from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    from .artifact_names import artifact_filename, artifact_stem
    from .content_quality import refusal_reason
    from .history_store import HistoryStore
    from .job_files import safe_owned_path, write_analysis_file
except ImportError:
    from artifact_names import artifact_filename, artifact_stem
    from content_quality import refusal_reason
    from history_store import HistoryStore
    from job_files import safe_owned_path, write_analysis_file


RETRYABLE_CODES = {
    "BROWSERLESS_UNREACHABLE", "BROWSERLESS_ERROR", "BROWSERLESS_BAD_JSON",
    "BROWSERLESS_SCRIPT_ERROR", "BROWSERLESS_BAD_RESULT", "YUANBAO_EMPTY_RESPONSE",
    "YUANBAO_PREVIEW_URL_MISSING", "VIDEO_URL_MISSING", "WECHAT_VIDEO_URL_MISSING",
    "DOWNLOAD_TIMEOUT", "DOWNLOAD_FAILED", "DOWNLOAD_VERIFY_FAILED",
    "YUANBAO_CARD_TIMEOUT", "YUANBAO_EDITOR_EMPTY",
}
NON_RETRYABLE_CODES = {
    "INVALID_URL", "INVALID_OUTPUT_FORMAT", "BAD_REQUEST", "UNAUTHORIZED",
    "YUANBAO_LOGIN_REQUIRED", "COOKIE_FILE_MISSING", "COOKIE_EMPTY",
    "SESSION_STATE_INVALID", "FUNCTION_FILE_MISSING",
}


def error_summary(exc: BaseException, attempt: int) -> dict[str, Any]:
    code = str(getattr(exc, "code", exc.__class__.__name__) or exc.__class__.__name__)
    message = str(getattr(exc, "message", str(exc)) or code)
    status = getattr(exc, "status", None)
    return {
        "attempt": attempt,
        "code": code[:120],
        "message": message[:500],
        "status": int(status) if isinstance(status, int) else None,
    }


def is_retryable_error(exc: BaseException) -> bool:
    code = str(getattr(exc, "code", "") or "")
    if code in NON_RETRYABLE_CODES:
        return False
    if code in RETRYABLE_CODES:
        return True
    status = getattr(exc, "status", None)
    return isinstance(status, int) and status >= 500


def cleanup_artifacts(download_dir: Path, filenames: Iterable[str | None]) -> None:
    for filename in dict.fromkeys(filenames):
        path = safe_owned_path(download_dir, filename)
        if path and path.exists():
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def _owned_copy(source: Path, dest: Path) -> Path:
    if source.resolve() == dest.resolve():
        return source
    # A hard link uses no extra disk space, and deleting either history record
    # will not invalidate the other one's media. Fall back for other file systems.
    try:
        os.link(source, dest)
    except (OSError, FileExistsError):
        shutil.copyfile(source, dest)
    return dest


def run_parse_job(
    job_id: str,
    input_url: str,
    prompt: str | None,
    preset: dict[str, Any] | None,
    output_format: str,
    download_requested: bool,
    *,
    store: HistoryStore,
    download_dir: Path,
    analyzer: Callable[[str, str | None], dict[str, Any]],
    downloader: Callable[[str, str, str], Path],
    max_attempts: int = 3,
    sleeper: Callable[[float], None] = time.sleep,
    thumbnailer: Callable[[Path, str, str | None, Path | None], Path | None] | None = None,
    transcriber: Callable[[str, Path], tuple[str, str] | tuple[str, str, int | None]] | None = None,
    text_analyzer: Callable[[str, str | None], str] | None = None,
    cached_video_lookup: Callable[[str], Path | None] | None = None,
    cached_failed_analysis: Callable[[str], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    if thumbnailer is None:
        try:
            from .thumbnail_service import create_thumbnail as thumbnailer_impl
        except ImportError:
            from thumbnail_service import create_thumbnail as thumbnailer_impl
        thumbnailer = thumbnailer_impl

    started = time.monotonic()
    retry_errors: list[dict[str, Any]] = []
    max_attempts = min(3, max(1, int(max_attempts)))
    download_dir = Path(download_dir)

    for attempt in range(1, max_attempts + 1):
        store.update_job(
            job_id, status="running", stage="yuanbao_video",
            attempts_used=attempt, max_attempts=max_attempts,
            retry_errors=retry_errors, error_code=None, error_message=None,
            analysis_status="pending",
        )
        provisional: list[str | None] = []
        temporary_video: Path | None = None
        try:
            # For a known unsupported video already saved locally, skip the
            # repeat Yuanbao video fetch and start directly with speech text.
            previous = cached_failed_analysis(input_url) if cached_failed_analysis and transcriber else None
            result = previous if previous is not None else analyzer(input_url, prompt)
            content = str(result.get("content") or "").strip()
            preview_url = str(result.get("previewUrl") or "").strip()
            direct_url = str(result.get("directUrl") or "").strip()
            card = result.get("card") if isinstance(result.get("card"), dict) else {}
            video_title = str(card.get("title") or "").strip() or "微信视频号"
            cover_url = str(card.get("coverUrl") or "").strip()
            store.update_job(
                job_id, yuanbao_content=content, preview_url=preview_url,
                source_direct_url=direct_url, video_title=video_title,
                cover_url=cover_url or None,
            )
            stem = artifact_stem(video_title, job_id)
            video_path = None
            transcript_filename = None
            failure_code = refusal_reason(content)
            analysis_source = "yuanbao_video" if not failure_code else None

            if download_requested or failure_code:
                # Reuse media only in fallback situations. Normal successes
                # remain compatible with existing URL/download behavior.
                cached = (cached_video_lookup(input_url) if cached_video_lookup and failure_code else None)
                if download_requested:
                    desired = download_dir / artifact_filename(video_title, job_id, "mp4")
                    provisional.append(desired.name)  # May be partially written before yt-dlp raises.
                    if cached and cached.is_file():
                        video_path = _owned_copy(cached, desired)
                    else:
                        video_path = downloader(stem, direct_url, preview_url)
                    provisional.append(video_path.name)
                    store.update_job(job_id, video_filename=video_path.name,
                                     video_bytes=video_path.stat().st_size)
                elif failure_code and transcriber is not None:
                    if cached and cached.is_file():
                        temporary_video = None
                        video_path = cached  # Existing file belongs to another task; never remove.
                    else:
                        # The user asked not to persist MP4; download temporarily
                        # for local speech recognition and then delete it.
                        provisional.append(stem + "-temporary.mp4")
                        temporary_video = downloader(stem+"-temporary", direct_url, preview_url)
                        video_path = temporary_video

            if failure_code:
                store.update_job(job_id, stage="local_transcription",
                                 analysis_status="unavailable", error_code=failure_code,
                                 error_message="元宝无法直接分析此视频，正在尝试本地语音转写")
                if transcriber is not None and video_path is not None:
                    try:
                        transcribed = transcriber(job_id, video_path)
                        transcript_filename, transcript = transcribed[:2]
                        elapsed = transcribed[2] if len(transcribed) > 2 else None
                        timing = (
                            {"transcription_elapsed_ms": elapsed}
                            if isinstance(elapsed, int) and not isinstance(elapsed, bool)
                            and 0 <= elapsed <= 86_400_000 else {}
                        )
                        store.update_job(job_id, transcript_filename=transcript_filename,
                                         stage="yuanbao_transcript", analysis_status="pending",
                                         **timing)
                        if text_analyzer is not None:
                            candidate = text_analyzer(transcript, prompt)
                            if not refusal_reason(candidate):
                                content = str(candidate).strip()
                                failure_code = None
                                analysis_source = "local_transcript"
                    except Exception as exc:
                        # A failed second stage must not erase a successfully
                        # downloaded MP4 or a successfully produced transcript.
                        failure_code = str(getattr(exc, "code", "FALLBACK_FAILED"))[:90]
                if failure_code:
                    # Never publish the original refusal as a successful Markdown.
                    # Preserve any successful download and transcript for recovery.
                    status = "completed" if ((video_path is not None and download_requested) or transcript_filename) else "failed"
                    thumbnail_path = None
                    if cover_url or (video_path is not None and download_requested):
                        try:
                            thumbnail_path = thumbnailer(
                                download_dir, artifact_filename(video_title, job_id, "jpg"),
                                cover_url or None,
                                video_path if download_requested else None,
                            )
                        except Exception:
                            pass
                    row = store.update_job(
                        job_id, status=status, stage="finished",
                        analysis_status="unavailable", analysis_source=None,
                        error_code=failure_code,
                        error_message="元宝未能完成分析；可下载的视频或本地逐字稿已保留" if status == "completed" else "元宝未能完成分析，本地转写尚不可用",
                        transcript_filename=transcript_filename,
                        thumbnail_filename=thumbnail_path.name if thumbnail_path else None,
                        elapsed_ms=int((time.monotonic()-started)*1000),
                    )
                    assert row is not None
                    return row

            # Only validated analysis text should be written into a success MD.
            store.update_job(job_id, yuanbao_content=content,
                             analysis_source=analysis_source, analysis_status="completed",
                             stage="saving_results", error_code=None, error_message=None)
            updated = store.get_job(job_id)
            assert updated is not None
            text_path = write_analysis_file(download_dir, updated, output_format)
            provisional.append(text_path.name)
            thumbnail_path = None
            if cover_url or (download_requested and video_path is not None):
                target = artifact_filename(video_title, job_id, "jpg")
                try:
                    thumbnail_path = thumbnailer(
                        download_dir, target, cover_url or None,
                        video_path if download_requested else None,
                    )
                except Exception:
                    thumbnail_path = None
            row = store.update_job(
                job_id, status="completed", stage="finished",
                attempts_used=attempt, retry_errors=retry_errors, error_code=None,
                error_message=None, text_filename=text_path.name,
                text_bytes=text_path.stat().st_size,
                video_filename=video_path.name if download_requested and video_path else None,
                video_bytes=video_path.stat().st_size if download_requested and video_path else None,
                thumbnail_filename=thumbnail_path.name if thumbnail_path else None,
                transcript_filename=transcript_filename,
                elapsed_ms=int((time.monotonic()-started)*1000),
            )
            assert row is not None
            return row

        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            summary = error_summary(exc, attempt)
            retry_errors.append(summary)
            current = store.get_job(job_id) or {}
            cleanup_artifacts(download_dir, [
                *provisional,
                current.get("text_filename"), current.get("video_filename"),
                current.get("thumbnail_filename"), current.get("bundle_filename"),
            ])
            final = (not is_retryable_error(exc)) or attempt >= max_attempts
            row = store.update_job(
                job_id, status="failed" if final else "running",
                stage="finished" if final else "yuanbao_video",
                attempts_used=attempt, retry_errors=retry_errors,
                error_code=summary["code"], error_message=summary["message"],
                text_filename=None, text_bytes=None, video_filename=None,
                video_bytes=None, thumbnail_filename=None, bundle_filename=None,
                analysis_status="unavailable" if final else "pending",
                elapsed_ms=int((time.monotonic()-started)*1000) if final else None,
            )
            if final:
                assert row is not None
                return row
            sleeper(attempt)
        finally:
            if temporary_video is not None:
                try:
                    temporary_video.unlink(missing_ok=True)
                except OSError:
                    pass
    raise RuntimeError("unreachable")
