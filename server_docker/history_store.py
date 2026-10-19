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
    "transcription_chunk_count", "transcription_segment_count", "transcription_duration_seconds",
    "analysis_chunk_count",
    "analysis_source",
    "analysis_status",
    "stage",
    "owner_user_id",
    "task_mode", "analyze_requested", "narration_requested", "raw_requested", "timed_requested",
    "raw_transcript_filename", "timed_transcript_filename",
    "batch_id", "batch_index", "queue_state",
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
                    transcription_chunk_count INTEGER,
                    transcription_segment_count INTEGER,
                    transcription_duration_seconds REAL,
                    analysis_chunk_count INTEGER,
                    analysis_source TEXT,
                    analysis_status TEXT,
                    stage TEXT,
                    owner_user_id TEXT,
                    task_mode TEXT, analyze_requested INTEGER, narration_requested INTEGER,
                    raw_requested INTEGER, timed_requested INTEGER,
                    raw_transcript_filename TEXT, timed_transcript_filename TEXT,
                    batch_id TEXT, batch_index INTEGER, queue_state TEXT
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
            for name, field_type in (
                ("transcription_chunk_count", "INTEGER"),
                ("transcription_segment_count", "INTEGER"),
                ("transcription_duration_seconds", "REAL"),
                ("analysis_chunk_count", "INTEGER"),
            ):
                if name not in existing:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {field_type}")
            if "owner_user_id" not in existing:
                conn.execute("ALTER TABLE jobs ADD COLUMN owner_user_id TEXT")
            for column in ('task_mode', 'raw_transcript_filename', 'timed_transcript_filename'):
                if column not in existing:
                    conn.execute(f'ALTER TABLE jobs ADD COLUMN {column} TEXT')
            for column in ('analyze_requested', 'narration_requested', 'raw_requested', 'timed_requested'):
                if column not in existing:
                    conn.execute(f'ALTER TABLE jobs ADD COLUMN {column} INTEGER')
            for name, dtype in (("batch_id", "TEXT"), ("batch_index", "INTEGER"),
                                ("queue_state", "TEXT")):
                if name not in existing:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {dtype}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_batch ON jobs(batch_id,batch_index)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(queue_state,created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs(owner_user_id,created_at DESC)")

    def _reconcile_running_jobs(self) -> None:
        now = utc_now()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                UPDATE jobs
                   SET status='failed', updated_at=?, error_code='INTERRUPTED',
                       error_message='任务在服务重启前未完成，请重新提交。',
                       queue_state=CASE WHEN batch_id IS NOT NULL THEN 'finished' ELSE queue_state END
                 WHERE status='running' AND coalesce(queue_state, '') NOT IN ('waiting','paused')
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
        owner_user_id: str | None = None,
        task_mode: str | None = None, analyze_requested: bool = True,
        narration_requested: bool = False, raw_requested: bool = False,
        timed_requested: bool = False,
    ) -> dict[str, Any]:
        now = utc_now()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    id, created_at, updated_at, input_url, prompt, preset_id, preset_name,
                    output_format, download_requested, status, attempts_used, max_attempts,
                    retry_errors_json, owner_user_id, task_mode, analyze_requested,
                    narration_requested, raw_requested, timed_requested
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', 0, ?, '[]', ?, ?, ?, ?, ?, ?)
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
                    owner_user_id, task_mode, int(analyze_requested),
                    int(narration_requested), int(raw_requested), int(timed_requested),
                ),
            )
        result = self.get_job(id)
        assert result is not None
        return result

    def create_batch_jobs(self, *, batch_id: str, owner_user_id: str,
                          jobs: list[dict[str, Any]]) -> None:
        """Insert all task records in one transaction; never partially accept a batch."""
        if not jobs or len(jobs) > 30:
            raise ValueError('INVALID_BATCH_SIZE')
        now = utc_now()
        with closing(self._connect()) as conn, conn:
            for index, job in enumerate(jobs, 1):
                conn.execute(
                    """INSERT INTO jobs (id,created_at,updated_at,input_url,prompt,
                    preset_id,preset_name,output_format,download_requested,status,
                    attempts_used,max_attempts,retry_errors_json,owner_user_id,task_mode,
                    analyze_requested,narration_requested,raw_requested,timed_requested,
                    batch_id,batch_index,queue_state)
                    VALUES (?,?,?,?,?,?,?,?,?,'running',0,?,'[]',?,?,?,?,?,?,?,?,'waiting')""",
                    (job['id'],now,now,job['input_url'],job.get('prompt'),
                     job.get('preset_id'),job.get('preset_name'),job['output_format'],
                     int(job['download_requested']),int(job['max_attempts']),owner_user_id,
                     job['task_mode'],int(job['analyze_requested']),
                     int(job['narration_requested']),int(job['raw_requested']),
                     int(job['timed_requested']),batch_id,index),
                )

    def batch_jobs(self, batch_id: str, *, owner_user_id: str | None = None) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            if owner_user_id is None:
                rows = conn.execute('SELECT * FROM jobs WHERE batch_id=? ORDER BY batch_index',
                                    (batch_id,)).fetchall()
            else:
                rows = conn.execute('SELECT * FROM jobs WHERE batch_id=? AND owner_user_id=? ORDER BY batch_index',
                                    (batch_id,owner_user_id)).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def waiting_batch_jobs(self, limit: int) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute("""SELECT * FROM jobs WHERE queue_state='waiting'
                    AND status='running' ORDER BY created_at,id LIMIT ?""",
                    (max(1,int(limit)),)).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def recent_batches(self, *, owner_user_id: str | None = None, limit: int = 12) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            if owner_user_id is None:
                rows = conn.execute("""SELECT batch_id, MIN(created_at) AS created_at,
                    COUNT(*) AS total FROM jobs WHERE batch_id IS NOT NULL
                    GROUP BY batch_id ORDER BY created_at DESC LIMIT ?""", (limit,)).fetchall()
            else:
                rows = conn.execute("""SELECT batch_id, MIN(created_at) AS created_at,
                    COUNT(*) AS total FROM jobs WHERE batch_id IS NOT NULL AND owner_user_id=?
                    GROUP BY batch_id ORDER BY created_at DESC LIMIT ?""", (owner_user_id,limit)).fetchall()
        return [dict(row) for row in rows]

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

    def list_jobs(self, limit: int = 100, *, owner_user_id: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with closing(self._connect()) as conn:
            if owner_user_id is None:
                rows = conn.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE owner_user_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
                    (owner_user_id, limit),
                ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def page_jobs(self, *, page: int = 1, per_page: int = 20,
                  owner_user_id: str | None = None, query: str = '') -> dict[str, Any]:
        """Search all accessible history rows and paginate in SQLite."""
        per_page = per_page if per_page in (10, 20, 50, 100) else 20
        page = max(1, min(int(page), 1_000_000))
        query = str(query or '').strip()[:150]
        conditions = []
        params: list[Any] = []
        if owner_user_id is not None:
            conditions.append('owner_user_id=?')
            params.append(owner_user_id)
        if query:
            searchable = ('id', 'video_title', 'prompt', 'input_url', 'preset_name')
            conditions.append('(' + ' OR '.join(
                f"instr(lower(coalesce({column}, '')), lower(?)) > 0"
                for column in searchable
            ) + ')')
            params.extend([query] * len(searchable))
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        with closing(self._connect()) as conn:
            total = conn.execute('SELECT COUNT(*) FROM jobs' + where, params).fetchone()[0]
            pages = max(1, (total + per_page - 1) // per_page)
            page = min(page, pages)
            rows = conn.execute(
                'SELECT * FROM jobs' + where
                + ' ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?',
                [*params, per_page, (page - 1) * per_page],
            ).fetchall()
        return {'rows': [self._row_to_dict(row) for row in rows],
                'page': page, 'per_page': per_page, 'pages': pages, 'total': total}

    def assign_legacy_jobs(self, owner_user_id: str) -> int:
        """Idempotently claim pre-multi-user history for the first admin."""
        with closing(self._connect()) as conn, conn:
            result = conn.execute(
                "UPDATE jobs SET owner_user_id=? WHERE owner_user_id IS NULL",
                (owner_user_id,),
            )
        return result.rowcount

    def file_owners(self, filename: str) -> set[str | None]:
        """Identify every history record referencing a download basename."""
        if not filename:
            return set()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT owner_user_id FROM jobs WHERE
                   text_filename=? OR video_filename=? OR thumbnail_filename=?
                   OR transcript_filename=? OR raw_transcript_filename=? OR timed_transcript_filename=? OR bundle_filename=?""",
                (filename,) * 7,
            ).fetchall()
        return {row["owner_user_id"] for row in rows}

    def file_referenced_elsewhere(self, filename: str, job_id: str) -> bool:
        if not filename:
            return False
        with closing(self._connect()) as conn:
            row = conn.execute(
                """SELECT 1 FROM jobs WHERE id<>? AND
                   (text_filename=? OR video_filename=? OR thumbnail_filename=?
                    OR transcript_filename=? OR raw_transcript_filename=? OR timed_transcript_filename=? OR bundle_filename=?) LIMIT 1""",
                (job_id,) + (filename,) * 7,
            ).fetchone()
        return row is not None

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
