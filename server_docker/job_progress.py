"""Read-only, evidence-based per-task progress for the workspace.

Do not report guessed time/percent. Phase completion is distinct from wall time;
never expose raw ASR chunks, private paths, or unrelated workers' state.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_JOB = re.compile(r"wxv_\d{6}\Z")
_FINISHED = {"completed", "failed"}


def _live_checkpoints(data_dir: Path, job_id: str, stage: str) -> dict:
    activity: dict = {}
    if not _JOB.fullmatch(job_id):
        return activity
    queue = Path(data_dir) / "transcription_queue"
    if stage == "local_transcription":
        root = queue / (".sensevoice-" + job_id)
        parts = root / "output" / "sensevoice_parts"
        if (parts.is_dir() and not parts.is_symlink() and not root.is_symlink()
                and not queue.is_symlink()):
            # Cap directory scanning; never read sensitive transcription text.
            windows = sum(1 for i, p in enumerate(parts.glob("chunk_*/part_*.json"))
                          if i < 1800 and p.is_file() and not p.is_symlink())
            groups = sum(1 for i, p in enumerate(parts.glob("chunk_*/manifest.json"))
                         if i < 100 and p.is_file() and not p.is_symlink())
            if windows or groups:
                activity["recognized_windows"] = windows
                activity["finished_groups"] = groups
    if stage == "yuanbao_transcript":
        checkpoint = queue / ("." + job_id + "_ai_checkpoint.json")
        if checkpoint.is_file() and not checkpoint.is_symlink() and checkpoint.stat().st_size < 2_000_000:
            try:
                data = json.loads(checkpoint.read_text("utf-8"))
                done = len(data.get("notes", []))
                total = data.get("total")
                if isinstance(total, int) and 1 <= total <= 100 and 0 <= done <= total:
                    activity["ai_done"] = done
                    activity["ai_total"] = total
            except (ValueError, OSError, TypeError):
                pass
    return activity


def get_job_progress(job: dict, data_dir: Path) -> dict:
    """Return a mode-specific checklist with measured checkpoint counts only."""
    jid = str(job.get("id") or "")
    stage = str(job.get("stage") or "yuanbao_video")
    running = job.get("status") == "running"
    queued = running and not job.get("attempts_used")
    video = bool(job.get("download_requested"))
    raw = bool(job.get("raw_requested"))
    timed = bool(job.get("timed_requested"))
    analyze = job.get("analyze_requested") not in (False, 0)
    narration = bool(job.get("narration_requested"))
    fallback = stage in ("local_transcription", "yuanbao_transcript") and not raw

    stages = [{"key": "source", "label": "提取视频信息"}]
    if video or raw or fallback:
        stages.append({"key": "media", "label": "下载 MP4" if video else "准备音频素材"})
    if raw or fallback or job.get("transcription_elapsed_ms"):
        stages.append({"key": "asr", "label": "SenseVoice 语音识别"})
    if analyze:
        stages.append({"key": "analysis", "label": "AI 解析与旁白" if narration else "AI 内容解析"})
    stages.append({"key": "save", "label": "整理并保存结果"})

    keys = [s["key"] for s in stages]
    stage_key = {
        "yuanbao_video": "source",
        "downloading_video": "media",
        "preparing_media": "media",
        "local_transcription": "asr",
        "yuanbao_transcript": "analysis",
        "saving_results": "save",
    }.get(stage, "source")
    current = keys.index(stage_key) if stage_key in keys else 0
    final = job.get("status") in _FINISHED
    has_asr = bool(job.get("raw_transcript_filename") or job.get("timed_transcript_filename"))
    has_fallback_asr = bool(job.get("transcript_filename") and job.get("analysis_source") == "local_transcript")
    asr_done = has_asr or has_fallback_asr
    ai_done = analyze and job.get("analysis_status") == "completed"
    outputs: list[dict] = []
    for key, label, requested, filename in (
        ("video", "MP4 视频", video, job.get("video_filename")),
        ("analysis", "AI 解析", analyze, job.get("text_filename")),
        ("narration", "AI 整理旁白", narration and analyze,
         job.get("transcript_filename") if ".narration-ai." in str(job.get("transcript_filename") or "") else None),
        ("asr", "时间轴逐字稿" if timed else "原始逐字稿", raw,
         job.get("timed_transcript_filename") if timed else job.get("raw_transcript_filename")),
    ):
        if requested:
            outputs.append({"key": key, "label": label, "ready": bool(filename)})

    for position, step in enumerate(stages):
        key = step["key"]
        if queued:
            state = "waiting"
        elif key == "asr" and asr_done:
            state = "complete"
        elif key == "analysis" and ai_done:
            state = "complete"
        elif key == "media" and (bool(job.get("video_filename")) or (asr_done and not video)
                                 or (stage in ("local_transcription", "yuanbao_transcript", "saving_results") and not video)):
            state = "complete"
        elif key == "save" and final and job.get("status") == "completed" and not job.get("error_code"):
            state = "complete"
        elif key == "source" and (position < current or (final and
               (job.get("video_title") or any(o["ready"] for o in outputs)))):
            state = "complete"
        elif final:
            # A terminal task should identify missing requested artifacts as
            # failures, not retain perpetual "waiting" on the result screen.
            state = "failed" if key in ("analysis", "asr", "save") else "waiting"
        elif position < current:
            state = "complete"
        elif position == current:
            state = "active"
        else:
            state = "waiting"
        step["state"] = state

    # Recovered retries remain attached to the original failing step, not a
    # misleading final "saving" step. Only expose safe error-code metadata.
    stage_aliases = {"yuanbao_video":"source", "downloading_video":"media",
        "preparing_media":"media", "local_transcription":"asr",
        "yuanbao_transcript":"analysis", "saving_results":"save"}
    friendly = {"SENSEVOICE_FAILED":"语音识别未成功", "SENSEVOICE_TIMEOUT":"本地转写超时",
        "LOCAL_WORKER_OFFLINE":"本地转写服务未连接", "LOCAL_WORKER_DISABLED":"未启用 SenseVoice 转写",
        "YUANBAO_VIDEO_NOT_READ":"元宝未能读取视频内容", "BROWSERLESS_TIMEOUT":"元宝页面响应超时",
        "VIDEO_DOWNLOAD_FAILED":"视频下载失败", "DOWNLOAD_FAILED":"视频下载失败",
        "TEXT_AI_NO_ANSWER":"元宝没有返回可靠的语音解析", "LOCAL_TRANSCRIPTION_TIMEOUT":"本地语音识别超时"}
    issues: dict[str, list[str]] = {}
    for entry in job.get("retry_errors", []) or []:
        if not isinstance(entry, dict):
            continue
        target = stage_aliases.get(str(entry.get("stage") or ""))
        code = str(entry.get("code") or "").upper()
        if target in keys and re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", code):
            issues.setdefault(target, []).append(f"第 {entry.get('attempt') or '?'} 次尝试：{friendly.get(code, '处理发生异常')}（{code}）")
    error = str(job.get("error_code") or "").upper()
    if error and re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", error):
        if job.get("raw_requested") and not has_asr:
            target = "asr"
        elif analyze and job.get("analysis_status") == "unavailable":
            target = "analysis"
        else:
            target = stage_aliases.get(str((job.get("retry_errors") or [{}])[-1].get("stage") or ""), "save")
        if target not in keys:
            target = "save"
        issues.setdefault(target, []).append(f"错误代码：{error} · {friendly.get(error, '请查看任务详细错误')} ")
    for step in stages:
        messages = list(dict.fromkeys(issues.get(step["key"], [])))
        if messages:
            step["issues"] = messages[-3:]
            if job.get("status") in _FINISHED and any(m.startswith("错误代码：") for m in messages):
                step["state"] = "failed"
            elif step["state"] == "complete":
                step["recovered"] = True
    activity = _live_checkpoints(Path(data_dir), jid, stage) if running else {}
    return {
        "steps": stages,
        "total_steps": len(stages),
        "completed_steps": sum(s["state"] == "complete" for s in stages),
        "current_step": 0 if queued else min(current + 1, len(stages)),
        "queued": bool(queued),
        "activity": activity,
        "outputs": outputs,
        "attempt": int(job.get("attempts_used") or 0),
        "max_attempts": int(job.get("max_attempts") or 3),
    }
