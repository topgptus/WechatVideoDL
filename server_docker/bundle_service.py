from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Any

try:
    from .artifact_names import artifact_filename, artifact_stem
    from .job_files import safe_owned_path
except ImportError:  # Docker executes modules from /app.
    from artifact_names import artifact_filename, artifact_stem
    from job_files import safe_owned_path

_COMPONENT_KEYS = ("text_filename", "video_filename", "thumbnail_filename", "transcript_filename")
_ALLOWED_EXTS = {"txt", "md", "mp4", "jpg"}


def _components(download_dir: Path, job: dict[str, Any]) -> list[tuple[Path, str]]:
    job_id = str(job.get("id") or "")
    title = str(job.get("video_title") or "").strip() or None
    stem = artifact_stem(title, job_id)
    items: list[tuple[Path, str]] = []
    for key in _COMPONENT_KEYS:
        filename = job.get(key)
        if not filename:
            continue
        path = safe_owned_path(download_dir, str(filename), _ALLOWED_EXTS)
        if path is None:
            raise FileNotFoundError(f"unsafe artifact filename: {filename}")
        if not path.exists() or not path.is_file():
            continue
        ext = path.suffix.lower().lstrip(".")
        member = f"{stem}.transcript.txt" if key == "transcript_filename" else f"{stem}.{ext}"
        items.append((path, member))
    if not items:
        raise FileNotFoundError("no bundle components are available")
    return items


def _cache_valid(bundle: Path, components: list[tuple[Path, str]]) -> bool:
    if not bundle.exists() or not bundle.is_file():
        return False
    try:
        newest_component = max(path.stat().st_mtime_ns for path, _ in components)
        if bundle.stat().st_mtime_ns < newest_component:
            return False
        expected = sorted(member for _, member in components)
        with zipfile.ZipFile(bundle, "r") as zf:
            return sorted(zf.namelist()) == expected
    except (OSError, zipfile.BadZipFile):
        return False


def ensure_bundle(download_dir: Path, job: dict[str, Any]) -> Path:
    root = Path(download_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    job_id = str(job.get("id") or "")
    title = str(job.get("video_title") or "").strip() or None
    filename = artifact_filename(title, job_id, "zip")
    bundle = safe_owned_path(root, filename, {"zip"})
    if bundle is None:
        raise ValueError("invalid bundle filename")
    components = _components(root, job)
    if _cache_valid(bundle, components):
        return bundle

    temp = bundle.with_name(f".{bundle.name}.tmp")
    temp.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for source, member in components:
                zf.write(source, arcname=member)
        os.replace(temp, bundle)
        return bundle
    finally:
        temp.unlink(missing_ok=True)
