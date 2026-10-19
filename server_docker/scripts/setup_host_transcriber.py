#!/usr/bin/env python3
"""Create a private launchd agent for the Mac-host transcription worker.

Only intended for macOS installations with a cached OpenAI Whisper model.
This helper does not manage Docker, Cloudflare, NPM, or unrelated agents.
"""
from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "org.wechatvideodl.local-transcriber"


def make_plist(
    python: Path, worker: Path, data_dir: Path, *,
    model: str = "base",
    sensevoice_python: Path | None = None,
    sensevoice_model_dir: Path | None = None,
    fallback_base: bool = False,
) -> dict:
    if model not in {"base", "sensevoice", "small", "medium", "turbo"}:
        raise ValueError("INVALID_ASR_MODEL")
    if model == "sensevoice":
        if sensevoice_python is None or sensevoice_model_dir is None:
            raise ValueError("SENSEVOICE_PATHS_REQUIRED")
        if (not sensevoice_python.is_file() or
                not (sensevoice_model_dir / "model.int8.onnx").is_file() or
                not (sensevoice_model_dir / "tokens.txt").is_file() or
                sensevoice_model_dir.is_symlink()):
            raise ValueError("SENSEVOICE_PATHS_INVALID")
    environment = {
        "PYTHONUNBUFFERED": "1", "OMP_NUM_THREADS": "4",
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    }
    if model == "sensevoice":
        environment.update({
            "LOCAL_ASR_SENSEVOICE_PYTHON": str(sensevoice_python),
            "LOCAL_ASR_SENSEVOICE_MODEL_DIR": str(sensevoice_model_dir),
            "LOCAL_ASR_FALLBACK_BASE": "true" if fallback_base else "false",
        })
    queue = data_dir / "transcription_queue"
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(python), str(worker), "--data-dir", str(data_dir), "--model", model,
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 20,
        # launchd does not inherit an interactive Homebrew shell PATH.
        # Whisper invokes FFmpeg as an external command during audio decoding.
        "EnvironmentVariables": environment,
        "StandardOutPath": str(queue / "worker.stdout.log"),
        "StandardErrorPath": str(queue / "worker.stderr.log"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--python", required=True, help="Python interpreter where whisper and torch are installed")
    parser.add_argument("--model", choices=["base", "small", "medium", "turbo", "sensevoice"], default="base")
    parser.add_argument("--sensevoice-python", help="Isolated Python with sherpa-onnx and numpy")
    parser.add_argument("--sensevoice-model-dir", help="Offline SenseVoice model/tokens directory")
    parser.add_argument("--fallback-base", action="store_true",
                        help="Allow Whisper base retry if SenseVoice fails")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise SystemExit("launchd setup is only supported on macOS")
    # Preserve the venv interpreter symlink: resolving it can remove the
    # virtual-environment sys.path that contains whisper and torch.
    python = Path(args.python).expanduser().absolute()
    if not python.is_file():
        raise SystemExit("Whisper Python executable does not exist")
    worker = Path(__file__).with_name("host_transcription_worker.py").resolve(strict=True)
    data = Path(args.data_dir).expanduser().resolve(strict=True)
    if not (data/"downloads").is_dir():
        raise SystemExit("The specified directory is not a valid WechatVideoDL data folder")
    plist = Path.home()/"Library"/"LaunchAgents"/f"{LABEL}.plist"
    uid = str(os.getuid())
    domain = f"gui/{uid}"
    if args.uninstall:
        if plist.exists():
            subprocess.run(["launchctl","bootout",domain,str(plist)],capture_output=True)
            plist.unlink()
        print("transcription_service_uninstalled")
        return
    doc = make_plist(
        python, worker, data, model=args.model,
        sensevoice_python=Path(args.sensevoice_python).expanduser().absolute()
        if args.sensevoice_python else None,
        sensevoice_model_dir=Path(args.sensevoice_model_dir).expanduser().absolute()
        if args.sensevoice_model_dir else None,
        fallback_base=args.fallback_base,
    )
    if not args.install:
        print("dry_run_only", "label", LABEL, "worker_exists", worker.is_file(),
              "model_cache_exists", (Path.home()/".cache"/"whisper"/"base.pt").is_file())
        return
    queue = data/"transcription_queue"
    queue.mkdir(mode=0o700, exist_ok=True)
    os.chmod(queue,0o700)
    for logname in ("worker.stdout.log","worker.stderr.log"):
        log = queue/logname
        if not log.exists():
            with log.open("w") as stream: pass
        os.chmod(log,0o600)
    plist.parent.mkdir(parents=True,exist_ok=True)
    if plist.exists():
        subprocess.run(["launchctl","bootout",domain,str(plist)],capture_output=True)
    with plist.open("wb") as output:
        plistlib.dump(doc,output)
    os.chmod(plist,0o600)
    p = subprocess.run(["launchctl","bootstrap",domain,str(plist)],capture_output=True,text=True,timeout=20)
    if p.returncode:
        raise SystemExit("transcription_service_bootstrap_failed")
    print("transcription_service_installed",LABEL)


if __name__ == "__main__":
    main()
