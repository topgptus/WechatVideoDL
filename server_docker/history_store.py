from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


JOB_COLUMNS = {
    "created_at",
    "updated_at",
    "input_url",
    "prompt",
    "preset_id",
    "preset_name",
    "output_format",
    "download_requested",
    "status",
    "attempts_used",
    "max_attempts",
    "retry_errors_json",
    "error_code",
    "error_message",
    "yuanbao_content",
    "preview_url",
    "source_direct_url",
    "video_title",
    "cover_url",
    "thumbnail_filename",
    "bundle_filename",
    "text_filename",
    "video_filename",
    "text_bytes",
    "video_bytes",
    "elapsed_ms",
    "transcript_filename",
    "transcription_elapsed_ms",
    "analysis_source",
    "analysis_status",
    "stage",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class HistoryStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._reconcile_running_jobs()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    input_url TEXT NOT NULL,
                    prompt TEXT,
                    preset_id TEXT,
                    preset_name TEXT,
                    output_format TEXT NOT NULL CHECK (output_format IN ('txt', 'md')),
                    download_requested INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
                    attempts_used INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    retry_errors_json TEXT NOT NULL DEFAULT '[]',
                    error_code TEXT,
                    error_message TEXT,
                    yuanbao_content TEXT,
                    preview_url TEXT,
                    source_direct_url TEXT,
                    video_title TEXT,
                    cover_url TEXT,
                    thumbnail_filename TEXT,
                    bundle_filename TEXT,
                    text_filename TEXT,
                    video_filename TEXT,
                    text_bytes INTEGER,
                    video_bytes INTEGER,
                    elapsed_ms INTEGER,
                    transcript_filename TEXT,
                    transcription_elapsed_ms INTEGER,
                    analysis_source TEXT,
                    analysis_status TEXT,
                    stage TEXT
                )
                """
            )
            existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            for column in (
                "video_title", "cover_url", "thumbnail_filename", "bundle_filename",
                "transcript_filename", "analysis_source", "analysis_status", "stage",
            ):
                if column not in existing:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} TEXT")
            if "transcription_elapsed_ms" not in existing:
                conn.execute("ALTER TABLE jobs ADD COLUMN transcription_elapsed_ms INTEGER")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC)")

    def _reconcile_running_jobs(self) -> None:
        now = utc_now()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                UPDATE jobs
                   SET status='failed', updated_at=?, error_code='INTERRUPTED',
                       error_message='任务在服务重启前未完成，请重新提交。'
                 WHERE status='running'
                """,
                (now,),
            )

    def create_job(
        self,
        *,
        id: str,
        input_url: str,
        prompt: str | None,
        preset_id: str | None,
        preset_name: str | None,
        output_format: str,
        download_requested: bool,
        max_attempts: int,
    ) -> dict[str, Any]:
        now = utc_now()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    id, created_at, updated_at, input_url, prompt, preset_id, preset_name,
                    output_format, download_requested, status, attempts_used, max_attempts,
                    retry_errors_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', 0, ?, '[]')
                """,
                (
                    id,
                    now,
                    now,
                    input_url,
                    prompt,
                    preset_id,
                    preset_name,
                    output_format,
                    1 if download_requested else 0,
                    int(max_attempts),
                ),
            )
        result = self.get_job(id)
        assert result is not None
        return result

    def update_job(self, job_id: str, **fields: Any) -> dict[str, Any] | None:
        if not fields:
            return self.get_job(job_id)
        unknown = set(fields) - JOB_COLUMNS - {"retry_errors"}
        if unknown:
            raise ValueError(f"Unknown job fields: {', '.join(sorted(unknown))}")
        values = dict(fields)
        if "retry_errors" in values:
            values["retry_errors_json"] = json.dumps(values.pop("retry_errors"), ensure_ascii=False)
        values["updated_at"] = utc_now()
        keys = list(values)
        assignments = ", ".join(f"{key}=?" for key in keys)
        with closing(self._connect()) as conn, conn:
            conn.execute(
                f"UPDATE jobs SET {assignments} WHERE id=?",
                [self._encode_value(key, values[key]) for key in keys] + [job_id],
            )
        return self.get_job(job_id)

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def delete_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.get_job(job_id)
        if row is None:
            return None
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        return row

    @staticmethod
    def _encode_value(key: str, value: Any) -> Any:
        if key == "download_requested":
            return 1 if value else 0
        return value

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        raw_errors = data.pop("retry_errors_json", "[]") or "[]"
        try:
            data["retry_errors"] = json.loads(raw_errors)
        except (TypeError, json.JSONDecodeError):
            data["retry_errors"] = []
        data["download_requested"] = bool(data.get("download_requested"))
        return data
