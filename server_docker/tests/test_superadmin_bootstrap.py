"""Portable super administrator bootstrap and legacy upgrade security tests."""

import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from werkzeug.security import generate_password_hash

from server_docker.user_store import UserError, UserStore


class SuperadminBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.directory.name) / "auth.db"

    def tearDown(self):
        self.directory.cleanup()

    def store(self, *, seed="initial_owner", selected=None):
        return UserStore(
            self.db_path,
            seed_username=seed,
            seed_password="sample-starting-password",
            bootstrap_superadmin_username=selected,
        )

    def make_legacy(self, accounts):
        """Create an auth DB from before the protected-owner migration."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('admin','member')),
                    active INTEGER NOT NULL DEFAULT 1,
                    session_version INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            for username, role, active in accounts:
                conn.execute(
                    """INSERT INTO users(
                       id,username,password_hash,role,active,
                       session_version,created_at,updated_at
                       ) VALUES(?,?,?,?,?,0,'2026-01-01','2026-01-01')""",
                    (
                        uuid.uuid4().hex, username,
                        generate_password_hash("test-password-for-" + username, method="scrypt"),
                        role, int(active),
                    ),
                )

    def snapshot(self):
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            return {
                row["username"]: dict(row)
                for row in conn.execute("SELECT * FROM users ORDER BY username")
            }

    def test_fresh_install_any_owner_name_gets_unique_protection(self):
        for name in ("initial_owner", "alice", "project-admin", "first.user"):
            with self.subTest(name=name):
                if self.db_path.exists():
                    self.db_path.unlink()
                store = self.store(seed=name)
                owner = store.get_user_by_name(name)
                self.assertTrue(owner["is_superadmin"])
                self.assertEqual(owner["role"], "admin")
                self.assertTrue(owner["active"])
                self.assertEqual(owner["session_version"], 0)
                store.create_user(
                    username="other_admin",password="different-pass-123",role="admin"
                )
                self.assertEqual(
                    sum(u["is_superadmin"] for u in store.list_users()),1
                )
                reloaded = self.store(seed="other_admin", selected="other_admin")
                self.assertEqual(reloaded.get_user_by_name(name)["id"], owner["id"])
                self.assertTrue(reloaded.get_user_by_name(name)["is_superadmin"])
                self.assertFalse(reloaded.get_user_by_name("other_admin")["is_superadmin"])

    def test_fresh_install_bootstrap_mismatch_is_fail_closed(self):
        with self.assertRaisesRegex(UserError, "SUPERADMIN_BOOTSTRAP_MISMATCH"):
            self.store(seed="alice", selected="bob")
        # After a mismatch we can still create a clean, correctly initialized DB.
        store = self.store(seed="alice", selected="alice")
        self.assertTrue(store.get_user_by_name("alice")["is_superadmin"])

    def test_legacy_two_admin_upgrade_selects_explicit_existing_owner(self):
        self.make_legacy([
            ("first_admin", "admin", True),
            ("designated_owner", "admin", True),
            ("ordinary_user", "member", True),
        ])
        before = self.snapshot()
        store = self.store(seed="first_admin", selected="designated_owner")
        owner = store.get_user_by_name("designated_owner")
        self.assertTrue(owner["is_superadmin"])
        self.assertEqual(owner["session_version"], before["designated_owner"]["session_version"] + 1)
        after = self.snapshot()
        for name in before:
            self.assertEqual(after[name]["id"], before[name]["id"])
            self.assertEqual(after[name]["password_hash"], before[name]["password_hash"])
            self.assertEqual(after[name]["role"], before[name]["role"])
            self.assertEqual(after[name]["active"], before[name]["active"])
        self.assertEqual(sum(row["is_superadmin"] for row in after.values()),1)
        # Once bound, a malicious config change cannot transfer ownership.
        for _ in range(2):
            new_store = self.store(seed="first_admin", selected="first_admin")
            self.assertEqual(
                new_store.get_user_by_name("designated_owner")["id"],owner["id"]
            )
            self.assertTrue(new_store.get_user_by_name("designated_owner")["is_superadmin"])
            self.assertFalse(new_store.get_user_by_name("first_admin")["is_superadmin"])
        self.assertEqual(
            self.snapshot()["designated_owner"]["session_version"],
            owner["session_version"],
        )

    def test_legacy_default_uses_original_configured_seed_account(self):
        self.make_legacy([
            ("first_admin", "admin", True),
            ("later_admin", "admin", True),
        ])
        store = self.store(seed="first_admin")
        self.assertTrue(store.get_user_by_name("first_admin")["is_superadmin"])
        self.assertFalse(store.get_user_by_name("later_admin")["is_superadmin"])

    def test_legacy_bad_candidate_refuses_upgrade_without_promotion(self):
        for candidate in ("missing", "reader", "disabled_admin"):
            with self.subTest(candidate=candidate):
                if self.db_path.exists():
                    self.db_path.unlink()
                self.make_legacy([
                    ("working_admin", "admin", True),
                    ("reader", "member", True),
                    ("disabled_admin", "admin", False),
                ])
                before = self.snapshot()
                with self.assertRaisesRegex(UserError, "SUPERADMIN_BOOTSTRAP_REQUIRED"):
                    self.store(seed="working_admin", selected=candidate)
                after = self.snapshot()
                self.assertEqual(set(after), set(before))
                self.assertEqual(
                    {key: (row["id"],row["password_hash"]) for key,row in after.items()},
                    {key: (row["id"],row["password_hash"]) for key,row in before.items()},
                )
                self.assertEqual(
                    sum(int(row.get("is_superadmin", 0)) for row in after.values()),0
                )
                # Explicit repair can choose an existing active administrator.
                repaired = self.store(seed="working_admin", selected="working_admin")
                self.assertTrue(repaired.get_user_by_name("working_admin")["is_superadmin"])

    def test_other_admin_cannot_seize_via_new_users_or_reconfiguration(self):
        store = self.store(seed="first_owner")
        owner_id = store.get_user_by_name("first_owner")["id"]
        other = store.create_user(
            username="candidate",password="candidate-password-1",role="admin"
        )
        # No username is a magic reserved superadmin name.
        new_member = store.create_user(
            username="project_root",password="project-root-pass",role="member"
        )
        self.assertFalse(new_member["is_superadmin"])
        changed = self.store(seed="candidate", selected="project_root")
        self.assertTrue(changed.get_user(owner_id)["is_superadmin"])
        self.assertFalse(changed.get_user(other["id"])["is_superadmin"])
        self.assertFalse(changed.get_user(new_member["id"])["is_superadmin"])


if __name__ == "__main__":
    unittest.main()
