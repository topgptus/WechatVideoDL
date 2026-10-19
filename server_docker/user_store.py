"""Private multi-user identity and role store.

The initial owner is the unique protected super administrator.
Identity is permanently bound to the database user ID (is_superadmin=1),
never to a hard-coded username or the current environment setting.
All account mutations require an authenticated actor at the store layer.
"""
from __future__ import annotations

import os
import re
import sqlite3
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
    def __init__(
        self, db_path: Path, *, seed_username: str, seed_password: str,
        bootstrap_superadmin_username: str | None = None,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Used only for first initialization or one-time migration of legacy
        # databases with no protected owner. After that DB identity prevails.
        self.bootstrap_superadmin_username = (
            bootstrap_superadmin_username.strip()
            if bootstrap_superadmin_username is not None
            else seed_username.strip()
        )
        self._initialize(seed_username, seed_password)

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self, username: str, password: str):
        # Never reset passwords when restarting the service.
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
                        is_superadmin INTEGER NOT NULL DEFAULT 0 CHECK (is_superadmin IN (0,1)),
                        session_version INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                columns = {item["name"] for item in conn.execute("PRAGMA table_info(users)")}
                if "is_superadmin" not in columns:
                    conn.execute(
                        "ALTER TABLE users ADD COLUMN is_superadmin INTEGER NOT NULL DEFAULT 0 "
                        "CHECK (is_superadmin IN (0,1))"
                    )
                if conn.execute("SELECT count(*) FROM users").fetchone()[0] == 0:
                    if not username.strip() or not password:
                        raise UserError("ADMIN_BOOTSTRAP_CREDENTIALS_REQUIRED")
                    if (self.bootstrap_superadmin_username
                            and self.bootstrap_superadmin_username.casefold()
                            != username.strip().casefold()):
                        raise UserError("SUPERADMIN_BOOTSTRAP_MISMATCH")
                    stamp = now_iso()
                    conn.execute(
                        """INSERT INTO users(
                           id,username,password_hash,role,active,is_superadmin,
                           session_version,created_at,updated_at)
                           VALUES(?,?,?,'admin',1,1,0,?,?)""",
                        (uuid.uuid4().hex, username.strip(),
                         generate_password_hash(password, method="scrypt"), stamp, stamp),
                    )

                marked = conn.execute(
                    "SELECT id,role,active FROM users WHERE is_superadmin=1"
                ).fetchall()
                if len(marked) > 1:
                    raise UserError("SUPERADMIN_CONFLICT")
                if marked:
                    # Once locked, changing the bootstrap env variable cannot
                    # reassign, replace, or create another protected account.
                    if marked[0]["role"] != "admin" or not marked[0]["active"]:
                        raise UserError("SUPERADMIN_CONFLICT")
                else:
                    # Upgrade from an older multi-user database only once.
                    # Never silently choose the newest/oldest administrator:
                    # an explicit existing enabled admin must match the
                    # bootstrap identity, otherwise refuse to start.
                    owner = conn.execute(
                        "SELECT id,role,active FROM users "
                        "WHERE username=? COLLATE NOCASE",
                        (self.bootstrap_superadmin_username,),
                    ).fetchone()
                    if not owner or owner["role"] != "admin" or not owner["active"]:
                        raise UserError("SUPERADMIN_BOOTSTRAP_REQUIRED")
                    conn.execute(
                        """UPDATE users SET is_superadmin=1,
                           session_version=session_version+1,updated_at=? WHERE id=?""",
                        (now_iso(), owner["id"]),
                    )
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_single_superadmin "
                    "ON users(is_superadmin) WHERE is_superadmin=1"
                )
                # Defense in depth: even accidental direct UPDATE/DELETE of
                # a superadmin row must not demote, disable or erase it.
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS protect_superadmin_fields
                    BEFORE UPDATE OF username,role,active,is_superadmin ON users
                    WHEN OLD.is_superadmin=1 AND (
                        NEW.username<>OLD.username OR NEW.role<>'admin'
                        OR NEW.active<>1 OR NEW.is_superadmin<>1
                    )
                    BEGIN SELECT RAISE(ABORT, 'SUPERADMIN_PROTECTED'); END
                """)
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS protect_superadmin_delete
                    BEFORE DELETE ON users WHEN OLD.is_superadmin=1
                    BEGIN SELECT RAISE(ABORT, 'SUPERADMIN_PROTECTED'); END
                """)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        if os.name != "nt":
            os.chmod(self.db_path, 0o600)

    @staticmethod
    def _public(row) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["active"] = bool(data.get("active"))
        data["is_superadmin"] = bool(data.get("is_superadmin"))
        data.pop("password_hash", None)
        return data

    def list_users(self):
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT id,username,role,active,is_superadmin,
                          session_version,created_at,updated_at
                   FROM users ORDER BY created_at,username"""
            ).fetchall()
        return [self._public(r) for r in rows]

    def get_user(self, uid: str):
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return self._public(row)

    def get_user_by_name(self, name: str):
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username=? COLLATE NOCASE", (name,)
            ).fetchone()
        return self._public(row)

    def authenticate(self, username: str, password: str):
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)
            ).fetchone()
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
                    """INSERT INTO users(
                       id,username,password_hash,role,active,session_version,created_at,updated_at)
                       VALUES(?,?,?,?,1,0,?,?)""",
                    (uid, username, generate_password_hash(password, method="scrypt"), role, stamp, stamp),
                )
            except sqlite3.IntegrityError as exc:
                raise UserError("USERNAME_TAKEN") from exc
        return self.get_user(uid)

    @staticmethod
    def _actor(conn, actor_user_id: str):
        actor = conn.execute(
            "SELECT id,role,active FROM users WHERE id=?", (actor_user_id,)
        ).fetchone()
        if not actor or not actor["active"]:
            raise UserError("PERMISSION_DENIED")
        return actor

    def set_password(
        self, uid: str, password: str, *, actor_user_id: str,
        current_password: str | None = None
    ):
        self.validate_new_password(password)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                actor = self._actor(conn, actor_user_id)
                target = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
                if not target:
                    raise UserError("USER_NOT_FOUND")
                if target["is_superadmin"] and actor["id"] != uid:
                    raise UserError("SUPERADMIN_PROTECTED")
                if actor["id"] == uid:
                    if not current_password or not check_password_hash(
                        target["password_hash"], current_password
                    ):
                        raise UserError("CURRENT_PASSWORD_INVALID")
                elif actor["role"] != "admin":
                    raise UserError("PERMISSION_DENIED")
                encoded = generate_password_hash(password, method="scrypt")
                conn.execute(
                    """UPDATE users SET password_hash=?,
                       session_version=session_version+1,updated_at=? WHERE id=?""",
                    (encoded, now_iso(), uid),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def set_role(self, uid: str, role: str, *, actor_user_id: str):
        if role not in ("admin", "member"):
            raise UserError("ROLE_INVALID")
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                actor = self._actor(conn, actor_user_id)
                if actor["role"] != "admin":
                    raise UserError("PERMISSION_DENIED")
                target = conn.execute(
                    "SELECT id,role,active,is_superadmin FROM users WHERE id=?", (uid,)
                ).fetchone()
                if not target:
                    raise UserError("USER_NOT_FOUND")
                if target["is_superadmin"]:
                    raise UserError("SUPERADMIN_PROTECTED")
                if uid == actor_user_id:
                    raise UserError("CANNOT_EDIT_SELF")
                if target["role"] == "admin" and role != "admin" and target["active"]:
                    count = conn.execute(
                        "SELECT count(*) FROM users WHERE active=1 AND role='admin'"
                    ).fetchone()[0]
                    if count <= 1:
                        raise UserError("LAST_ADMIN")
                conn.execute(
                    """UPDATE users SET role=?,session_version=session_version+1,updated_at=?
                       WHERE id=?""",
                    (role, now_iso(), uid),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def set_active(self, uid: str, active: bool, *, actor_user_id: str):
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                actor = self._actor(conn, actor_user_id)
                if actor["role"] != "admin":
                    raise UserError("PERMISSION_DENIED")
                target = conn.execute(
                    "SELECT id,role,active,is_superadmin FROM users WHERE id=?", (uid,)
                ).fetchone()
                if not target:
                    raise UserError("USER_NOT_FOUND")
                if target["is_superadmin"]:
                    raise UserError("SUPERADMIN_PROTECTED")
                if uid == actor_user_id:
                    raise UserError("CANNOT_EDIT_SELF")
                if target["role"] == "admin" and target["active"] and not active:
                    count = conn.execute(
                        "SELECT count(*) FROM users WHERE active=1 AND role='admin'"
                    ).fetchone()[0]
                    if count <= 1:
                        raise UserError("LAST_ADMIN")
                conn.execute(
                    """UPDATE users SET active=?,session_version=session_version+1,updated_at=?
                       WHERE id=?""",
                    (1 if active else 0, now_iso(), uid),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
