"""Private multi-user identity store.

Only the first installation seeds the environment's legacy administrator.
After seeding, authentication uses password hashes in /data/auth.db exclusively.
"""
from __future__ import annotations

import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
MIN_PASSWORD_LENGTH = 8


class UserError(ValueError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class UserStore:
    def __init__(self, db_path: Path, *, seed_username: str, seed_password: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize(seed_username, seed_password)

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self, username: str, password: str):
        # Do not re-seed if the administrator later changes the initial password.
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                        password_hash TEXT NOT NULL,
                        role TEXT NOT NULL CHECK (role IN ('admin','member')),
                        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
                        session_version INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                if conn.execute("SELECT count(*) FROM users").fetchone()[0] == 0:
                    if not username.strip() or not password:
                        raise UserError("ADMIN_BOOTSTRAP_CREDENTIALS_REQUIRED")
                    stamp = now_iso()
                    conn.execute(
                        """INSERT INTO users(id,username,password_hash,role,active,session_version,created_at,updated_at)
                           VALUES(?,?,?,'admin',1,0,?,?)""",
                        (uuid.uuid4().hex, username.strip(),
                         generate_password_hash(password, method="scrypt"), stamp, stamp),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        # Password hashes are not readable by other users on the Mac mini.
        if os.name != "nt":
            os.chmod(self.db_path, 0o600)

    def _public(self, row) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["active"] = bool(data.get("active"))
        data.pop("password_hash", None)
        return data

    def list_users(self):
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT id,username,role,active,session_version,created_at,updated_at
                     FROM users ORDER BY created_at,username"""
            ).fetchall()
        return [self._public(r) for r in rows]

    def get_user(self, uid: str):
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return self._public(row)

    def get_user_by_name(self, name: str):
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (name,)).fetchone()
        return self._public(row)

    def authenticate(self, username: str, password: str):
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
        if not row or not row["active"] or not password:
            return None
        if not check_password_hash(row["password_hash"], password):
            return None
        return self._public(row)

    def verify_password(self, uid: str, password: str) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT password_hash,active FROM users WHERE id=?", (uid,)).fetchone()
        return bool(row and row["active"] and check_password_hash(row["password_hash"], password))

    @staticmethod
    def validate_new_password(password: str):
        if not isinstance(password, str) or not MIN_PASSWORD_LENGTH <= len(password) <= 256:
            raise UserError("PASSWORD_LENGTH")

    def create_user(self, *, username: str, password: str, role: str = "member"):
        username = (username or "").strip()
        if not USERNAME_RE.fullmatch(username):
            raise UserError("USERNAME_INVALID")
        if role not in ("admin", "member"):
            raise UserError("ROLE_INVALID")
        self.validate_new_password(password)
        stamp = now_iso()
        uid = uuid.uuid4().hex
        with closing(self._connect()) as conn, conn:
            try:
                conn.execute(
                    """INSERT INTO users(id,username,password_hash,role,active,session_version,created_at,updated_at)
                       VALUES(?,?,?,?,1,0,?,?)""",
                    (uid, username, generate_password_hash(password, method="scrypt"), role, stamp, stamp),
                )
            except sqlite3.IntegrityError as exc:
                raise UserError("USERNAME_TAKEN") from exc
        return self.get_user(uid)

    def set_password(self, uid: str, password: str):
        self.validate_new_password(password)
        encoded = generate_password_hash(password, method="scrypt")
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                """UPDATE users SET password_hash=?,session_version=session_version+1,updated_at=?
                   WHERE id=?""",
                (encoded, now_iso(), uid),
            )
            if not cur.rowcount:
                raise UserError("USER_NOT_FOUND")

    def set_role(self, uid: str, role: str):
        if role not in ("admin", "member"):
            raise UserError("ROLE_INVALID")
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute("SELECT id,role,active FROM users WHERE id=?", (uid,)).fetchone()
                if not existing:
                    raise UserError("USER_NOT_FOUND")
                if existing["role"] == "admin" and role != "admin" and existing["active"]:
                    count = conn.execute(
                        "SELECT count(*) FROM users WHERE active=1 AND role='admin'"
                    ).fetchone()[0]
                    if count <= 1:
                        raise UserError("LAST_ADMIN")
                conn.execute(
                    """UPDATE users SET role=?,session_version=session_version+1,updated_at=? WHERE id=?""",
                    (role, now_iso(), uid),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def set_active(self, uid: str, active: bool):
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute("SELECT id,role,active FROM users WHERE id=?", (uid,)).fetchone()
                if not existing:
                    raise UserError("USER_NOT_FOUND")
                if existing["role"] == "admin" and existing["active"] and not active:
                    count = conn.execute(
                        "SELECT count(*) FROM users WHERE active=1 AND role='admin'"
                    ).fetchone()[0]
                    if count <= 1:
                        raise UserError("LAST_ADMIN")
                conn.execute(
                    """UPDATE users SET active=?,session_version=session_version+1,updated_at=? WHERE id=?""",
                    (1 if active else 0, now_iso(), uid),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
