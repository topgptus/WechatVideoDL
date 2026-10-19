"""Read-only health view of the private, file-backed SenseVoice worker."""
from __future__ import annotations

import json
import math
import time
from pathlib import Path


def sensevoice_status(data_dir: Path, enabled: bool, *, now: float | None = None) -> dict:
    """Never return host paths, process IDs, credentials, or raw heartbeat content."""
    status = {"enabled": bool(enabled), "model": "sensevoice",
              "state": "disabled", "ready": False, "message": "转写尚未启用"}
    if not enabled:
        return status
    queue = Path(data_dir) / "transcription_queue"
    marker = queue / "worker.status.json"
    try:
        if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 4096:
            raise ValueError("invalid heartbeat")
        heartbeat = json.loads(marker.read_text("utf-8"))
        age = (time.time() if now is None else now) - float(heartbeat["at"])
        if not math.isfinite(age) or age < -30 or age > 15:
            raise ValueError("stale heartbeat")
        if heartbeat.get("ready") is not True or heartbeat.get("model") != "sensevoice":
            raise ValueError("invalid worker")
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return {**status, "state": "offline",
                "message": "未发现就绪的 SenseVoice worker，请检查模型、启动状态和共享队列"}
    return {**status, "state": "ready", "ready": True,
            "message": "SenseVoice 已连接，可使用原始和时间轴逐字稿"}
