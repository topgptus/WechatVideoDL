from __future__ import annotations

import re
from pathlib import Path
from typing import Any

try:
    from .artifact_names import artifact_filename
except ImportError:  # Docker executes app modules from /app, not as a package.
    from artifact_names import artifact_filename

DEFAULT_ALLOWED_EXTS = {"txt", "md", "mp4", "jpg", "zip"}


def safe_owned_path(
    download_dir: Path,
    filename: str | None,
    allowed_exts: set[str] | None = None,
) -> Path | None:
    if not filename:
        return None
    name = str(filename)
    if Path(name).name != name or name in {".", ".."}:
        return None
    if "/" in name or "\\" in name or "\x00" in name:
        return None
    ext = Path(name).suffix.lower().lstrip(".")
    allowed = DEFAULT_ALLOWED_EXTS if allowed_exts is None else {x.lower().lstrip(".") for x in allowed_exts}
    if ext not in allowed:
        return None
    root = Path(download_dir).resolve()
    candidate = (root / name).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def write_analysis_file(download_dir: Path, job: dict[str, Any], output_format: str) -> Path:
    if output_format not in {"txt", "md"}:
        raise ValueError("output_format must be 'txt' or 'md'")
    job_id = str(job.get("id") or "")
    if not re.fullmatch(r"wxv_\d{6}", job_id):
        raise ValueError("invalid job id")

    video_title = str(job.get("video_title") or "").strip()
    display_title = video_title or "微信视频号"
    root = Path(download_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / artifact_filename(video_title or None, job_id, output_format)
    input_url = str(job.get("input_url") or "")
    prompt = str(job.get("prompt") or "").strip()
    content = str(job.get("yuanbao_content") or "").strip()
    created_at = str(job.get("created_at") or "")
    preset_name = str(job.get("preset_name") or "").strip()

    if output_format == "txt":
        parts = [f"TITLE: {display_title}", f"ID: {job_id}", f"INPUT_URL: {input_url}"]
        if created_at:
            parts.append(f"CREATED_AT: {created_at}")
        if preset_name:
            parts.append(f"PRESET: {preset_name}")
        if prompt:
            parts += ["", "PROMPT:", prompt]
        parts += ["", "YUANBAO_CONTENT:", content, ""]
        text = "\n".join(parts)
    else:
        lines = [
            f"# {display_title}",
            "",
            f"- Task ID: `{job_id}`",
            f"- Source: {input_url}",
        ]
        if created_at:
            lines.append(f"- Created: {created_at}")
        if preset_name:
            lines.append(f"- Preset: {preset_name}")
        if prompt:
            lines += ["", "## Prompt", "", prompt]
        caption = ("## 基于 SenseVoice 真实逐字稿的 AI 整理" if
                   job.get("analysis_source") == "local_transcript"
                   else "## Yuanbao Response")
        lines += ["", caption, "", content, ""]
        text = "\n".join(lines)

    path.write_text(text, encoding="utf-8")
    return path
