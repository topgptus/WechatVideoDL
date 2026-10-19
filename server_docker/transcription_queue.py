"""App-to-host transcription queue. No host network service or Docker socket."""
from __future__ import annotations

import json
import math
import os
import re
import time
from pathlib import Path


class TranscriptionError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _atomic_json(path: Path, obj: dict) -> None:
    tmp = path.with_name("." + path.name + f".{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as output:
        os.chmod(tmp, 0o600)
        json.dump(obj, output, ensure_ascii=False)
    os.replace(tmp, path)


def transcribe_with_host(
    job_id: str, video: Path, data_dir: Path, *, timeout_seconds: int = 3600,
    poll_seconds: float = 2, model: str = "sensevoice",
    include_elapsed_ms: bool = False, include_metrics: bool = False,
) -> tuple:
    if not re.fullmatch(r"wxv_\d{6}", job_id):
        raise TranscriptionError("INVALID_JOB_ID")
    data_dir = Path(data_dir).resolve()
    filename = Path(video).name
    if Path(video).resolve().parent != (data_dir / "downloads").resolve():
        raise TranscriptionError("INVALID_MEDIA_PATH")
    queue = data_dir / "transcription_queue"
    heartbeat = queue / "worker.status.json"
    try:
        state = json.loads(heartbeat.read_text("utf-8"))
        if not state.get("ready") or abs(time.time() - float(state["at"])) > 90:
            raise TranscriptionError("LOCAL_WORKER_OFFLINE")
    except (FileNotFoundError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise TranscriptionError("LOCAL_WORKER_OFFLINE") from None
    request = queue / f"{job_id}.request.json"
    result = queue / f"{job_id}.result.json"
    if result.exists():
        result.unlink()  # Old result must never be accepted for a new request.
    _atomic_json(request, {
        "job_id": job_id, "video_filename": filename, "model": model,
    })
    end = time.monotonic() + timeout_seconds
    while time.monotonic() < end:
        if result.is_file():
            try:
                data = json.loads(result.read_text("utf-8"))
            except (OSError, ValueError, TypeError):
                raise TranscriptionError("LOCAL_WORKER_INVALID_RESULT") from None
            if data.get("job_id") != job_id:
                raise TranscriptionError("LOCAL_WORKER_INVALID_RESULT")
            if not data.get("ok"):
                raise TranscriptionError(str(data.get("error_code") or "TRANSCRIPTION_FAILED"))
            metrics = {"transcription_chunk_count": data.get("chunk_count"),
                       "transcription_segment_count": data.get("segment_count"),
                       "transcription_duration_seconds": data.get("duration_seconds")}
            text_file = str(data.get("transcript_filename") or "")
            if text_file != f"{job_id}.transcript.txt":
                raise TranscriptionError("LOCAL_WORKER_INVALID_RESULT")
            path = data_dir / "downloads" / text_file
            if not path.is_file() or path.is_symlink():
                raise TranscriptionError("TRANSCRIPT_NOT_FOUND")
            text = path.read_text("utf-8").strip()
            if len(text) < 20:
                raise TranscriptionError("TRANSCRIPT_EMPTY")
            if include_elapsed_ms:
                # Existing workers may omit elapsed_seconds. Missing or
                # invalid timing never turns successful ASR into a failure.
                elapsed_seconds = data.get("elapsed_seconds")
                if (isinstance(elapsed_seconds, (float, int))
                        and not isinstance(elapsed_seconds, bool)
                        and math.isfinite(elapsed_seconds)
                        and 0 <= elapsed_seconds <= 86400):
                    elapsed_ms = round(elapsed_seconds * 1000)
                else:
                    elapsed_ms = None
                return ((text_file, text, elapsed_ms, metrics) if include_metrics
                        else (text_file, text, elapsed_ms))
            return (text_file, text, metrics) if include_metrics else (text_file, text)
        time.sleep(max(0.2, poll_seconds))
    raise TranscriptionError("LOCAL_TRANSCRIPTION_TIMEOUT")
