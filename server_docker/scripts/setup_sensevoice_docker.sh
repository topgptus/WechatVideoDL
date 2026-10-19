#!/usr/bin/env bash
# Administrator-initiated optional SenseVoice installation. Never invoked by WebUI.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
if [[ "$#" -gt 1 || ( "$#" -eq 1 && "$1" != "--enable-app" ) ]]; then
  echo "Usage: ./scripts/setup_sensevoice_docker.sh [--enable-app]" >&2
  exit 2
fi
ENABLE_APP=false
if [[ "${1:-}" == "--enable-app" ]]; then ENABLE_APP=true; fi
if [[ ! -f .env ]]; then
  echo "Missing private .env; create it from .env.example before setup." >&2
  exit 2
fi
command -v docker >/dev/null || { echo "Docker is required" >&2; exit 2; }
command -v python3 >/dev/null || { echo "Python 3 is required for safe model installation" >&2; exit 2; }
DOCKER=(docker compose -f docker-compose.yml -f docker-compose.sensevoice.yml --profile sensevoice)
"${DOCKER[@]}" config --quiet
mkdir -p data/downloads data/transcription_queue
# Prevent two workers claiming jobs from the same queue. Existing Docker
# worker (our own service) can be safely reconfigured using this script.
if [[ -f data/transcription_queue/worker.status.json ]]; then
  if python3 - <<'PY'
import json,time
from pathlib import Path
try:
    s=json.loads(Path("data/transcription_queue/worker.status.json").read_text())
    raise SystemExit(0 if s.get("ready") and abs(time.time()-float(s.get("at",0))) <= 90 else 1)
except (OSError,ValueError,TypeError):
    raise SystemExit(1)
PY
  then
    CID="$("${DOCKER[@]}" ps -q sensevoice)"
    if [[ -z "$CID" ]]; then
      echo "A different ASR worker is already active. Stop it before starting a Docker worker." >&2
      exit 3
    fi
  fi
fi
echo "Installing official SenseVoice INT8 model (only if not installed)..."
python3 scripts/install_sensevoice_model.py --target models/sensevoice
echo "Building optional isolated ASR worker..."
"${DOCKER[@]}" build sensevoice
echo "Starting isolated worker (no network, no host Docker socket, no credentials mount)..."
"${DOCKER[@]}" up -d --no-deps sensevoice
echo "Waiting for fresh, ready worker heartbeat..."
python3 - <<'PY'
import json,sys,time
from pathlib import Path
status=Path("data/transcription_queue/worker.status.json")
for _ in range(45):
    try:
        s=json.loads(status.read_text())
        if s.get("ready") is True and s.get("model") == "sensevoice" and abs(time.time()-float(s["at"])) <= 10:
            print("SenseVoice worker is ready")
            break
    except (FileNotFoundError,ValueError,TypeError,KeyError):
        pass
    time.sleep(2)
else:
    print("Worker did not become ready. Examine Docker container logs; WebUI was not changed.",file=sys.stderr)
    sys.exit(4)
PY
if ! "$ENABLE_APP"; then
  echo "Worker installed. Enable the app when ready by rerunning with --enable-app."
  exit 0
fi
echo "Enabling optional transcription in the private .env..."
python3 - <<'PY'
from pathlib import Path
import os,tempfile,shutil,time
source=Path(".env")
if source.is_symlink():
    raise SystemExit("Refusing symlink .env")
raw=source.read_text("utf-8")
lines=raw.splitlines(keepends=True)
updated=[]
seen=set()
for line in lines:
    if line.startswith("LOCAL_TRANSCRIPTION_ENABLED="):
        if "enabled" in seen: continue
        line="LOCAL_TRANSCRIPTION_ENABLED=true\n"
        seen.add("enabled")
    elif line.startswith("LOCAL_TRANSCRIPTION_MODEL="):
        if "model" in seen: continue
        line="LOCAL_TRANSCRIPTION_MODEL=sensevoice\n"
        seen.add("model")
    updated.append(line)
if "enabled" not in seen: updated.append("\nLOCAL_TRANSCRIPTION_ENABLED=true\n")
if "model" not in seen: updated.append("LOCAL_TRANSCRIPTION_MODEL=sensevoice\n")
target=Path("data")/".maintenance"
target.mkdir(parents=True,exist_ok=True,mode=0o700)
os.chmod(target,0o700)
# Never store a .env backup outside git-ignored private data.
backup=target/("sensevoice-env-before-"+time.strftime("%Y%m%d-%H%M%S")+".bak")
if backup.exists():
    raise SystemExit("Refusing to overwrite existing settings backup")
shutil.copy2(source, backup)
os.chmod(backup,0o600)
with tempfile.NamedTemporaryFile("w",dir=".",prefix=".sensevoice-env-",encoding="utf-8",delete=False) as f:
    tmp=Path(f.name);os.chmod(tmp,0o600);f.writelines(updated)
os.replace(tmp,source)
PY
echo "Updating application container to read the new opt-in settings..."
docker compose -f docker-compose.yml up -d --no-deps app
echo "SenseVoice enabled. Check 设置 → 语音识别 and try a short authorized test video."
