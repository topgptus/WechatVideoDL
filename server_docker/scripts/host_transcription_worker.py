#!/usr/bin/env python3
"""Local-only SenseVoice worker for WechatVideoDL.

No listening TCP port or Docker socket. Communicates through a private,
shared-volume queue. The script itself never receives Yuanbao credentials.
"""
from __future__ import annotations

import argparse
import json
import hashlib
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import sys
import threading
import time
from pathlib import Path
from typing import Callable

JOB_ID = re.compile(r"^wxv_\d{6}$")
MODEL_CACHE = {}


def atomic_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + ".tmp")
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    with temp.open("w", encoding="utf-8") as stream:
        os.chmod(temp, 0o600)
        stream.write(data)
    os.replace(temp, path)


def resolve_video(download_dir: Path, filename: str) -> Path:
    """Permit only existing, regular .mp4 files within this data folder."""
    if not filename or Path(filename).name != filename or not filename.lower().endswith(".mp4"):
        raise ValueError("INVALID_VIDEO_FILENAME")
    target = download_dir / filename
    if target.is_symlink() or not target.is_file():
        raise ValueError("VIDEO_NOT_FOUND")
    resolved = target.resolve()
    if resolved.parent != download_dir.resolve():
        raise ValueError("UNSAFE_VIDEO_PATH")
    if not stat.S_ISREG(resolved.stat().st_mode):
        raise ValueError("INVALID_MEDIA")
    return resolved


def sensevoice_transcriber(video: Path) -> str:
    """Use offline SenseVoice in an isolated interpreter; never mutate the video.

    Source files, session state, and credentials are never read by the engine.
    The staging folder (including extracted audio and checkpoints) disappears
    whether the process succeeds, times out, or fails.
    """
    python_path = os.environ.get("LOCAL_ASR_SENSEVOICE_PYTHON", "").strip()
    model_root = os.environ.get("LOCAL_ASR_SENSEVOICE_MODEL_DIR", "").strip()
    python = Path(python_path).expanduser() if python_path else None
    model = Path(model_root).expanduser() if model_root else None
    if (python is None or not python.is_file() or not os.access(python, os.X_OK)
            or model is None or model.is_symlink() or not model.is_dir()
            or not (model / "model.int8.onnx").is_file()
            or not (model / "tokens.txt").is_file()):
        raise RuntimeError("SENSEVOICE_NOT_CONFIGURED")
    engine = Path(__file__).with_name("sensevoice_engine.py")
    if not engine.is_file() or engine.is_symlink():
        raise RuntimeError("SENSEVOICE_ENGINE_MISSING")
    staging_root = video.parent.parent / "transcription_queue"
    if staging_root.is_symlink() or not staging_root.is_dir():
        raise RuntimeError("SENSEVOICE_STAGING_UNAVAILABLE")
    # Persistent, per-job private staging allows a multi-hour video to resume
    # at the last validated 30-minute group after a worker restart.
    match = re.search(r'(?:wxv_|__)(\d{6})$', video.stem)
    # Legacy test fixtures and older artifact names may not include a job ID.
    # Bind their private staging to a deterministic media pathname digest.
    staging_key = ("wxv_" + match.group(1)) if match else (
        "file_" + hashlib.sha256(str(video.resolve()).encode()).hexdigest()[:16])
    staging = staging_root / (".sensevoice-" + staging_key)
    if staging.is_symlink():
        raise RuntimeError("SENSEVOICE_STAGING_UNAVAILABLE")
    staging.mkdir(mode=0o700, exist_ok=True)
    audio = staging / "source.flac"
    source_signature = {"size": video.stat().st_size, "mtime_ns": video.stat().st_mtime_ns}
    source_marker = staging / "source.json"
    if source_marker.exists():
        if json.loads(source_marker.read_text("utf-8")) != source_signature:
            raise RuntimeError("SENSEVOICE_SOURCE_CHANGED")
    else:
        if audio.exists():
            raise RuntimeError("SENSEVOICE_SOURCE_CHANGED")
        pending = staging / ".source.flac.pending"
        pending.unlink(missing_ok=True)
        converted = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-n",
             "-i", str(video), "-vn", "-ar", "16000", "-ac", "1",
             "-c:a", "flac", "-f", "flac", str(pending)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300,
        )
        if converted.returncode or not pending.is_file() or pending.stat().st_size < 4096:
            raise RuntimeError("SENSEVOICE_AUDIO_EXTRACT_FAILED")
        os.chmod(pending, 0o600)
        os.replace(pending, audio)
        source_marker.write_text(json.dumps(source_signature), encoding="utf-8")
        os.chmod(source_marker, 0o600)
    result_dir = staging / "output"
    result_dir.mkdir(mode=0o700, exist_ok=True)
    if result_dir.is_symlink():
        raise RuntimeError("SENSEVOICE_STAGING_UNAVAILABLE")
    runner = subprocess.Popen(
        [str(python), str(engine)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, start_new_session=True,
    )
    try:
        stdout, _ = runner.communicate(
            json.dumps({
                "input": str(audio),
                "output_dir": str(result_dir),
                "model_dir": str(model),
            }), timeout=3600,
        )
    except subprocess.TimeoutExpired:
        try:
            os.killpg(runner.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        runner.communicate()
        raise RuntimeError("SENSEVOICE_TIMEOUT") from None
    if runner.returncode:
        try:
            reason = json.loads(stdout.strip()).get("code", "SENSEVOICE_FAILED")
        except (ValueError, AttributeError):
            reason = "SENSEVOICE_FAILED"
        raise RuntimeError(reason)
    try:
        response = json.loads(stdout.strip())
        manifest = json.loads(
            (result_dir / "transcript.sensevoice.manifest.json").read_text("utf8"))
    except (OSError, TypeError, ValueError, KeyError):
        raise RuntimeError("SENSEVOICE_RESULT_INVALID") from None
    if (not response.get("ok") or response.get("parts", 0) < 1
            or manifest.get("parts") != response["parts"]
            or manifest.get("model") != "sensevoice-int8-2024"):
        raise RuntimeError("SENSEVOICE_RESULT_INVALID")
    transcript = result_dir / "transcript.sensevoice.txt"
    if not transcript.is_file() or transcript.is_symlink():
        raise RuntimeError("SENSEVOICE_RESULT_INVALID")
    text = transcript.read_text("utf8").strip()
    if len(text) < 20:
        raise RuntimeError("SENSEVOICE_EMPTY")
    # Preserve actual group/window counts for UI and future diagnostics.
    metrics = {"chunk_count": int(manifest.get("chunk_count", 0)),
               "segment_count": int(manifest["parts"]),
               "duration_seconds": float(manifest.get("duration_seconds", 0))}
    shutil.rmtree(staging)
    return text, metrics


def default_transcriber(video: Path, model_name: str) -> str:
    # This project only routes new ASR work to SenseVoice. Any locally
    # installed Whisper remains untouched and available to other projects.
    if model_name != "sensevoice":
        raise ValueError("MODEL_NOT_ALLOWED")
    return sensevoice_transcriber(video)


def process_request(data_dir: Path, payload: dict, transcribe: Callable[[Path, str], str]) -> dict:
    job_id = str(payload.get("job_id") or "")
    if not JOB_ID.fullmatch(job_id):
        raise ValueError("INVALID_JOB_ID")
    video = resolve_video(data_dir / "downloads", str(payload.get("video_filename") or ""))
    model = str(payload.get("model") or "sensevoice")
    if model != "sensevoice":
        raise ValueError("MODEL_NOT_ALLOWED")
    start = time.monotonic()
    transcribed = transcribe(video, model)
    text, metrics = transcribed if isinstance(transcribed, tuple) else (transcribed, {})
    if len(text) < 20:
        raise ValueError("TRANSCRIPT_EMPTY")
    output = data_dir / "downloads" / f"{job_id}.transcript.txt"
    temp = output.with_name("." + output.name + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        os.chmod(temp, 0o600)
        stream.write(text + "\n")
    os.replace(temp, output)
    return {
        "ok": True, "job_id": job_id, "transcript_filename": output.name,
        "characters": len(text), "elapsed_seconds": round(time.monotonic() - start, 1),
        "chunk_count": metrics.get("chunk_count"),
        "segment_count": metrics.get("segment_count"),
        "duration_seconds": metrics.get("duration_seconds"),
    }


def run_once(data_dir: Path, transcribe: Callable[[Path, str], str] = default_transcriber) -> bool:
    queue = data_dir / "transcription_queue"
    queue.mkdir(parents=True, exist_ok=True)
    os.chmod(queue, 0o700)
    items = sorted(queue.glob("wxv_*.processing.json")) + sorted(queue.glob("wxv_*.request.json"))
    for item in items:
        match = re.fullmatch(r"(wxv_\d{6})\.(?:request|processing)\.json", item.name)
        if not match or item.is_symlink():
            continue
        jid = match[1]
        claimed = queue / f"{jid}.processing.json"
        result_file = queue / f"{jid}.result.json"
        if result_file.is_file():
            item.unlink(missing_ok=True)
            continue
        if item != claimed:
            try:
                os.replace(item, claimed)
            except FileNotFoundError:
                continue
        try:
            data = json.loads(claimed.read_text("utf-8"))
            if data.get("job_id") != jid:
                raise ValueError("JOB_ID_MISMATCH")
            result = process_request(data_dir, data, transcribe)
        except Exception as exc:
            reason = str(exc)
            code = reason if reason in {
                "INVALID_VIDEO_FILENAME", "VIDEO_NOT_FOUND", "UNSAFE_VIDEO_PATH",
                "INVALID_MEDIA", "INVALID_JOB_ID", "JOB_ID_MISMATCH",
                "MODEL_NOT_ALLOWED", "UNKNOWN_WHISPER_MODEL",
                "WHISPER_MODEL_NOT_CACHED", "TRANSCRIPT_EMPTY",
                "SENSEVOICE_NOT_CONFIGURED", "SENSEVOICE_ENGINE_MISSING",
                "SENSEVOICE_AUDIO_EXTRACT_FAILED", "SENSEVOICE_STAGING_UNAVAILABLE",
                "SENSEVOICE_FAILED", "SENSEVOICE_TIMEOUT",
                "AUDIO_TOO_LONG_OR_INVALID", "TOO_MANY_PARTS", "ENGINE_FAILED",
                "SENSEVOICE_SOURCE_CHANGED",
                "SENSEVOICE_RESULT_INVALID", "SENSEVOICE_EMPTY",
            } else "TRANSCRIPTION_FAILED"
            result = {"ok": False, "job_id": jid, "error_code": code}
        atomic_json(result_file, result)
        claimed.unlink(missing_ok=True)
        return True
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--model", choices=["sensevoice"], default="sensevoice")
    args = parser.parse_args()
    data_dir = Path(args.data_dir).expanduser().resolve(strict=True)
    if not (data_dir / "downloads").is_dir():
        raise SystemExit("Invalid WechatVideoDL data directory")
    queue = data_dir / "transcription_queue"
    queue.mkdir(mode=0o700, exist_ok=True)
    os.chmod(queue, 0o700)
    if args.once:
        run_once(data_dir)
        return
    stop = threading.Event()
    def heartbeat():
        while not stop.is_set():
            atomic_json(queue / "worker.status.json", {
                "ready": True, "at": time.time(), "model": args.model, "pid": os.getpid(),
            })
            stop.wait(3)
    threading.Thread(target=heartbeat, daemon=True, name="transcriber-heartbeat").start()
    try:
        while True:
            if not run_once(data_dir):
                time.sleep(3)
    finally:
        stop.set()


if __name__ == "__main__":
    main()
