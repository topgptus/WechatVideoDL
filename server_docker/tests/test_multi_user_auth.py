"""Multi-user integration tests: cookies, authorization and artifact isolation."""
from __future__ import annotations

import base64
import re
import tempfile
import unittest
from pathlib import Path

import server_docker.app as appmod
from server_docker.history_store import HistoryStore
from server_docker.user_store import UserError, UserStore


class MultiUserSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.downloads = self.root / "downloads"
        self.downloads.mkdir()
        self.keep = {}
        for key, val in {
            "DATA_DIR": self.root, "DOWNLOAD_DIR": self.downloads,
            "SEQUENCE_FILE": self.root / "sequence.txt",
            "WEBUI_USERNAME": "founder", "WEBUI_PASSWORD": "initial-secure-password",
            "PUBLIC_BASE_URL": "https://wvd.example.test",
            "HISTORY_STORE": HistoryStore(self.root / "history.db"),
            "USER_STORE": None,
        }.items():
            self.keep[key] = getattr(appmod, key)
            setattr(appmod, key, val)
        self.old_secure = appmod.app.config.get("SESSION_COOKIE_SECURE")
        self.old_testing = appmod.app.config.get("TESTING")
        appmod.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=True)
        appmod.LOGIN_FAILURES.clear()
        self.admin_client = appmod.app.test_client()
        self.member_client = appmod.app.test_client()
        self.other_client = appmod.app.test_client()
        self.store = appmod.get_history_store()

    def tearDown(self):
        appmod.app.config.update(TESTING=self.old_testing,
                                 SESSION_COOKIE_SECURE=self.old_secure)
        appmod.LOGIN_FAILURES.clear()
        for key, old in self.keep.items():
            setattr(appmod, key, old)
        self.tmp.cleanup()

    def request(self, client, method, uri, **kw):
        return client.open(uri, method=method, base_url="https://localhost", **kw)

    def csrf(self, client):
        html = self.request(client, "GET", "/login").get_data(as_text=True)
        m = re.search(r'name="csrf_token" value="([^"]+)"', html)
        if m is None:
            with client.session_transaction() as s:
                return s["csrf"]
        return m.group(1)

    def signed_in(self, client, username, password):
        self.request(client, "GET", "/login")
        with client.session_transaction() as s:
            token = s["csrf"]
        return self.request(client, "POST", "/login",
                            data={"username": username, "password": password,
                                  "csrf_token": token})

    def current_csrf(self, client):
        with client.session_transaction() as s:
            return s.get("csrf", "")

    def add_member(self, username="member1"):
        return appmod.get_user_store().create_user(
            username=username, password="user-password-12345", role="member")

    def create_job(self, user, job_id, *, text=None, video=None):
        self.store.create_job(
            id=job_id, owner_user_id=user["id"],
            input_url="https://weixin.qq.com/sph/example",
            prompt="important private prompt", preset_id=None,preset_name=None,
            output_format="md",download_requested=True,max_attempts=3)
        values={"status":"completed","yuanbao_content":"private result"}
        if text:
            (self.downloads / text).write_text("private content",encoding="utf8")
            values["text_filename"]=text
        if video:
            (self.downloads / video).write_bytes(b"private MP4")
            values["video_filename"]=video
        self.store.update_job(job_id, **values)

    def establish_owner_and_second_admin(self):
        # Simulate an arbitrary initial owner and a separate administrator.
        existing = self.store
        userdb = self.root / "auth.db"
        appmod.USER_STORE = None
        self.root.mkdir(exist_ok=True)
        # Seed the privileged account without exposing a production password.
        store = UserStore(userdb, seed_username="owner", seed_password="owner-test-pass-123")
        appmod.USER_STORE = store
        other = store.create_user(
            username="otheradmin", password="other-admin-pass-123",role="admin"
        )
        return store.get_user_by_name("owner"), other

    def test_unassigned_legacy_history_goes_to_protected_owner_not_first_admin(self):
        owner, other = self.establish_owner_and_second_admin()
        self.store.create_job(
            id="wxv_000199",input_url="https://weixin.qq.com/sph/legacy",
            prompt=None,preset_id=None,preset_name=None,output_format="md",
            download_requested=False,max_attempts=3,
        )
        self.assertIsNone(self.store.get_job("wxv_000199")["owner_user_id"])
        # 'otheradmin' sorts ahead of 'owner', but must never inherit
        # legacy downloads merely because of the list ordering.
        appmod.USER_STORE = None
        appmod.get_user_store()
        self.assertEqual(
            self.store.get_job("wxv_000199")["owner_user_id"],owner["id"]
        )
        self.assertNotEqual(
            self.store.get_job("wxv_000199")["owner_user_id"],other["id"]
        )

    def test_existing_two_admins_owner_is_promoted_without_password_loss(self):
        from werkzeug.security import generate_password_hash
        import sqlite3
        from uuid import uuid4
        owner_id=uuid4().hex
        db=self.root/"auth.db"
        with sqlite3.connect(str(db)) as conn:
            conn.execute("""
                CREATE TABLE users(
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('admin','member')),
                    active INTEGER NOT NULL DEFAULT 1,
                    session_version INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                INSERT INTO users(id,username,password_hash,role,active,session_version,created_at,updated_at)
                VALUES(?,?,?,'admin',1,0,?,?)
            """,(owner_id,"owner",generate_password_hash("prior-owner-pass-123",method="scrypt"),"2026-01-01","2026-01-01"))
            conn.execute("""
                INSERT INTO users(id,username,password_hash,role,active,session_version,created_at,updated_at)
                VALUES(?,?,?,'admin',1,0,?,?)
            """,(uuid4().hex,"founder",generate_password_hash("initial-secure-password",method="scrypt"),"2026-01-01","2026-01-01"))
            before = conn.execute("SELECT password_hash FROM users WHERE id=?", (owner_id,)).fetchone()[0]
        # Upgrade the old-style auth.db in-place without changing user IDs or hashes.
        migrated = UserStore(
            self.root / "auth.db",
            seed_username="founder", seed_password="initial-secure-password",
            bootstrap_superadmin_username="owner",
        )
        owner = migrated.get_user_by_name("owner")
        self.assertTrue(owner["is_superadmin"])
        self.assertTrue(owner["active"])
        self.assertEqual(owner["role"], "admin")
        self.assertEqual(owner["id"], owner_id)
        self.assertIsNotNone(migrated.authenticate("owner", "prior-owner-pass-123"))
        with sqlite3.connect(str(self.root / "auth.db")) as conn:
            after = conn.execute("SELECT password_hash FROM users WHERE id=?", (owner_id,)).fetchone()[0]
            self.assertEqual(after, before)
            self.assertEqual(conn.execute("SELECT count(*) FROM users WHERE is_superadmin=1").fetchone()[0],1)
        second = UserStore(self.root / "auth.db",
                           seed_username="founder", seed_password="initial-secure-password")
        self.assertEqual(second.get_user_by_name("owner")["session_version"],
                         owner["session_version"])

    def test_secondary_admin_cannot_modify_owner_by_any_management_endpoint(self):
        owner, other = self.establish_owner_and_second_admin()
        self.assertTrue(owner["is_superadmin"])
        self.assertFalse(other["is_superadmin"])
        self.assertEqual(self.signed_in(self.admin_client,"otheradmin","other-admin-pass-123").status_code,302)
        token=self.current_csrf(self.admin_client)
        base="/admin/users/"+owner["id"]
        for endpoint,data in (
            ("/password",{"password":"malicious-new-pass","confirm_password":"malicious-new-pass"}),
            ("/role",{"role":"member"}),
            ("/role",{"role":"admin"}),
            ("/active",{"active":"0"}),
            ("/active",{"active":"1"}),
        ):
            with self.subTest(endpoint=endpoint,data=data):
                result=self.request(self.admin_client,"POST",base+endpoint,
                                    data={"csrf_token":token,**data})
                self.assertEqual(result.status_code,400)
                self.assertIn("受保护",result.get_data(as_text=True))
                account=appmod.get_user_store().get_user_by_name("owner")
                self.assertTrue(account["is_superadmin"])
                self.assertEqual(account["role"],"admin")
                self.assertTrue(account["active"])
        self.assertTrue(appmod.get_user_store().verify_password(owner["id"],"owner-test-pass-123"))
        html=self.request(self.admin_client,"GET","/admin/users").get_data(as_text=True)
        self.assertIn("超级管理员 · 受保护",html)
        owner_card=html.split("<strong>owner</strong>", 1)[1].split("</article>",1)[0]
        self.assertNotIn("<form",owner_card)
        self.assertIn("受保护",owner_card)
        self.assertEqual(self.request(self.admin_client,"POST","/admin/users",data={
            "csrf_token":token,"username":"OWNER","password":"does-not-matter","role":"admin"
        }).status_code,400)

    def test_superadmin_protected_at_store_and_sqlite_layer(self):
        import sqlite3
        owner, other=self.establish_owner_and_second_admin()
        store=appmod.get_user_store()
        for action in (
            lambda:store.set_password(owner["id"],"bad-change-password",actor_user_id=other["id"]),
            lambda:store.set_role(owner["id"],"member",actor_user_id=other["id"]),
            lambda:store.set_role(owner["id"],"admin",actor_user_id=other["id"]),
            lambda:store.set_active(owner["id"],False,actor_user_id=other["id"]),
            lambda:store.set_active(owner["id"],True,actor_user_id=other["id"]),
        ):
            with self.assertRaisesRegex(UserError,"SUPERADMIN_PROTECTED"):
                action()
        # Username uniqueness protects a real existing account, not a
        # hard-coded special name.
        with self.assertRaisesRegex(UserError,"USERNAME_TAKEN"):
            store.create_user(username="OWNER",password="random-pass-4567",role="admin")
        with self.assertRaisesRegex(UserError,"ROLE_INVALID"):
            store.create_user(username="otherroot",password="random-pass-4567",role="superadmin")
        subordinate=store.create_user(
            username="team_member",password="member-pass-123",role="member"
        )
        # Ordinary admins retain authority over non-protected accounts.
        store.set_role(subordinate["id"],"admin",actor_user_id=other["id"])
        self.assertEqual(store.get_user(subordinate["id"])["role"],"admin")
        store.set_password(
            subordinate["id"],"new-member-pass-123",actor_user_id=other["id"]
        )
        self.assertIsNotNone(store.authenticate("team_member","new-member-pass-123"))
        store.set_active(subordinate["id"],False,actor_user_id=other["id"])
        self.assertFalse(store.get_user(subordinate["id"])["active"])
        with sqlite3.connect(str(self.root/"auth.db")) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE users SET is_superadmin=1 WHERE id=?",(other["id"],)
                )
            for sql in (
                "UPDATE users SET role='member' WHERE id=?",
                "UPDATE users SET active=0 WHERE id=?",
                "UPDATE users SET is_superadmin=0 WHERE id=?",
                "UPDATE users SET username='renamed_owner' WHERE id=?",
                "DELETE FROM users WHERE id=?",
            ):
                with self.subTest(sql=sql):
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute(sql,(owner["id"],))
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM users WHERE is_superadmin=1"
            ).fetchone()[0],1)

    def test_owner_can_self_change_password_with_current_password_only(self):
        owner,other=self.establish_owner_and_second_admin()
        self.assertEqual(self.signed_in(self.admin_client,"owner","owner-test-pass-123").status_code,302)
        csrf=self.current_csrf(self.admin_client)
        before=appmod.get_user_store().get_user_by_name("owner")["session_version"]
        wrong=self.request(self.admin_client,"POST","/account/password",data={
            "csrf_token":csrf,"current_password":"wrong-pass",
            "new_password":"changed-owner-pass-123","confirm_password":"changed-owner-pass-123"})
        self.assertEqual(wrong.status_code,400)
        with self.assertRaisesRegex(UserError,"CURRENT_PASSWORD_INVALID"):
            appmod.get_user_store().set_password(
                owner["id"],"changed-owner-pass-123",
                actor_user_id=owner["id"],current_password="wrong-pass")
        updated=self.request(self.admin_client,"POST","/account/password",data={
            "csrf_token":csrf,"current_password":"owner-test-pass-123",
            "new_password":"changed-owner-pass-123","confirm_password":"changed-owner-pass-123"})
        self.assertEqual(updated.status_code,302)
        owner_updated=appmod.get_user_store().get_user_by_name("owner")
        self.assertTrue(owner_updated["is_superadmin"])
        self.assertEqual(owner_updated["role"],"admin")
        self.assertTrue(owner_updated["active"])
        self.assertGreater(owner_updated["session_version"],before)
        self.assertIsNone(appmod.get_user_store().authenticate("owner","owner-test-pass-123"))
        self.assertEqual(self.signed_in(self.admin_client,"owner","changed-owner-pass-123").status_code,302)
        self.assertIn("超级管理员",self.request(self.admin_client,"GET","/account").get_data(as_text=True))
        # owner can still reset OTHER administrator's password and adjust role.
        owner_csrf=self.current_csrf(self.admin_client)
        changed=self.request(self.admin_client,"POST",f"/admin/users/{other['id']}/role",data={
            "csrf_token":owner_csrf,"role":"member"})
        self.assertEqual(changed.status_code,200)
        self.assertEqual(appmod.get_user_store().get_user_by_name("otheradmin")["role"],"member")

    def test_first_admin_bootstrap_and_legacy_task_owned(self):
        self.store.create_job(
            id="wxv_000101",input_url="https://weixin.qq.com/sph/old",
            prompt=None,preset_id=None,preset_name=None,output_format="md",
            download_requested=False,max_attempts=3)
        user_store=appmod.get_user_store()
        users=user_store.list_users()
        self.assertEqual(len(users),1)
        self.assertEqual(users[0]["role"],"admin")
        self.assertEqual(self.store.get_job("wxv_000101")["owner_user_id"],users[0]["id"])
        self.assertEqual(user_store.db_path.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.signed_in(self.admin_client,"founder","initial-secure-password").status_code,302)
        self.assertIn("Cookie", self.request(self.admin_client,"GET","/").headers.get("Vary",""))
        self.assertIn("Secure", " ".join(self.request(self.admin_client,"POST","/logout",
                                                    data={"csrf_token":self.current_csrf(self.admin_client)}).headers.getlist("Set-Cookie")))

    def test_login_csrf_and_no_secret_in_user_listing(self):
        self.assertEqual(self.request(self.admin_client,"POST","/login",data={
            "username":"founder","password":"initial-secure-password"}).status_code,403)
        self.assertEqual(self.signed_in(self.admin_client,"founder","incorrect").status_code,200)
        self.assertEqual(self.signed_in(self.admin_client,"founder","initial-secure-password").status_code,302)
        admin_html=self.request(self.admin_client,"GET","/admin/users").get_data(as_text=True)
        self.assertIn("用户管理",admin_html)
        self.assertNotIn("initial-secure-password",admin_html)
        users=appmod.get_user_store().list_users()
        self.assertNotIn("password_hash",users[0])

    def test_member_can_only_access_own_history_and_files(self):
        admin=appmod.get_user_store().list_users()[0]
        member=self.add_member()
        other=self.add_member("member2")
        self.create_job(admin,"wxv_000301",text="admin.md",video="admin.mp4")
        self.create_job(member,"wxv_000302",text="member.md",video="member.mp4")
        self.create_job(other,"wxv_000303",text="other.md")
        self.assertEqual(self.signed_in(self.member_client,"member1","user-password-12345").status_code,302)
        history=self.request(self.member_client,"GET","/api/history").get_json()["jobs"]
        self.assertEqual([j["id"] for j in history],["wxv_000302"])
        for job_id in ("wxv_000301","wxv_000303"):
            self.assertEqual(self.request(self.member_client,"GET","/api/history/"+job_id).status_code,404)
            self.assertEqual(self.request(self.member_client,"GET","/api/history/"+job_id+"/bundle").status_code,404)
            self.assertEqual(self.request(self.member_client,"DELETE","/api/history/"+job_id,
                                          headers={"X-CSRF-Token":self.current_csrf(self.member_client)}).status_code,404)
        for filename in ("admin.md","admin.mp4","other.md","unknown.txt","../history.db"):
            self.assertEqual(self.request(self.member_client,"GET","/files/"+filename).status_code,404)
        with self.request(self.member_client,"GET","/files/member.md") as result:
            self.assertEqual(result.status_code,200)
        with self.request(self.member_client,"GET","/api/history/wxv_000302/bundle") as result:
            self.assertEqual(result.status_code,200)
        self.assertTrue(self.store.get_job("wxv_000301"))
        self.assertEqual(self.request(self.member_client,"GET","/admin/users").status_code,403)
        self.assertEqual(self.request(self.member_client,"POST","/admin/users",
                 data={"csrf_token":self.current_csrf(self.member_client),"username":"hacker",
                       "password":"hunter2-secure-123"}).status_code,403)
        # Authenticated founder can manage all tasks.
        self.assertEqual(self.signed_in(self.admin_client,"founder","initial-secure-password").status_code,302)
        ids=[j["id"] for j in self.request(self.admin_client,"GET","/api/history").get_json()["jobs"]]
        self.assertEqual(set(ids),{"wxv_000301","wxv_000302","wxv_000303"})
        with self.request(self.admin_client,"GET","/files/member.md") as result:
            self.assertEqual(result.status_code,200)

    def test_shared_filename_does_not_cross_users(self):
        admin=appmod.get_user_store().list_users()[0]
        member=self.add_member()
        self.create_job(admin,"wxv_000401",text="shared.md")
        self.create_job(member,"wxv_000402",text="shared.md")
        self.assertEqual(self.signed_in(self.member_client,"member1","user-password-12345").status_code,302)
        self.assertEqual(self.request(self.member_client,"GET","/files/shared.md").status_code,404)
        self.assertEqual(self.request(self.member_client,"GET",
                                     "/api/history/wxv_000402/bundle").status_code,404)
        # Deleting the second reference may not destroy a file retained
        # by a different account.
        result=self.request(self.member_client,"DELETE",
                            "/api/history/wxv_000402",
                            headers={"X-CSRF-Token":self.current_csrf(self.member_client)})
        self.assertEqual(result.status_code,200)
        self.assertTrue((self.downloads/"shared.md").exists())

    def test_csrf_on_session_parse_delete_and_management(self):
        admin=appmod.get_user_store().list_users()[0]
        self.create_job(admin,"wxv_000501",text="private.md")
        self.assertEqual(self.signed_in(self.admin_client,"founder","initial-secure-password").status_code,302)
        self.assertEqual(self.request(self.admin_client,"DELETE","/api/history/wxv_000501").status_code,403)
        self.assertEqual(self.request(self.admin_client,"POST","/api/parse",
                           json={"url":"https://weixin.qq.com/sph/demo"}).status_code,403)
        self.assertEqual(self.request(self.admin_client,"POST","/admin/users",
                           data={"username":"newperson","password":"valid-pass-1234"}).status_code,403)
        self.assertIsNotNone(self.store.get_job("wxv_000501"))
        # Old-style Basic API client still proves credentials per request,
        # not merely by an environment variable.
        token=base64.b64encode(b"founder:initial-secure-password").decode()
        headers={"Authorization":"Basic "+token}
        self.assertEqual(self.request(self.admin_client,"GET","/api/history",headers=headers).status_code,200)
        self.assertEqual(self.request(self.admin_client,"DELETE","/api/history/wxv_000501",headers=headers).status_code,403)
        self.assertEqual(self.request(self.other_client,"DELETE","/api/history/wxv_000501",
                              headers={**headers,"Sec-Fetch-Site":"cross-site"}).status_code,403)
        self.assertEqual(self.request(self.other_client,"DELETE","/api/history/wxv_000501",
                              headers=headers).status_code,200)

    def test_create_user_change_role_disable_and_admin_reset_password(self):
        self.assertEqual(self.signed_in(self.admin_client,"founder","initial-secure-password").status_code,302)
        csrf=self.current_csrf(self.admin_client)
        response=self.request(self.admin_client,"POST","/admin/users",data={
            "csrf_token":csrf,"username":"designer","password":"new-strong-pass-123",
            "role":"member"})
        self.assertEqual(response.status_code,200)
        user=appmod.get_user_store().get_user_by_name("designer")
        self.assertEqual(user["role"],"member")
        self.assertEqual(self.signed_in(self.member_client,"designer","new-strong-pass-123").status_code,302)
        self.assertEqual(self.request(self.member_client,"GET","/admin/users").status_code,403)
        self.assertEqual(self.request(self.admin_client,"POST","/admin/users/"+user["id"]+"/role",
                  data={"csrf_token":csrf,"role":"admin"}).status_code,200)
        # Promotion requires a fresh session; outdated role/version invalidates it.
        self.assertEqual(self.request(self.member_client,"GET","/account").status_code,302)
        self.assertEqual(self.signed_in(self.member_client,"designer","new-strong-pass-123").status_code,302)
        self.assertEqual(self.request(self.member_client,"GET","/admin/users").status_code,200)
        self.assertEqual(self.request(self.admin_client,"POST","/admin/users/"+user["id"]+"/password",
                   data={"csrf_token":csrf,"password":"reset-pass-56789","confirm_password":"reset-pass-56789"}).status_code,200)
        self.assertEqual(self.request(self.member_client,"GET","/account").status_code,302)
        self.assertNotEqual(self.signed_in(self.member_client,"designer","new-strong-pass-123").status_code,302)
        self.assertEqual(self.signed_in(self.member_client,"designer","reset-pass-56789").status_code,302)
        self.assertEqual(self.request(self.admin_client,"POST","/admin/users/"+user["id"]+"/active",
                  data={"csrf_token":csrf,"active":"0"}).status_code,200)
        self.assertEqual(self.request(self.member_client,"GET","/account").status_code,302)
        self.assertNotEqual(self.signed_in(self.member_client,"designer","reset-pass-56789").status_code,302)

    def test_change_self_password_invalidates_cookie_and_old_basic(self):
        self.assertEqual(self.signed_in(self.admin_client,"founder","initial-secure-password").status_code,302)
        csrf=self.current_csrf(self.admin_client)
        response=self.request(self.admin_client,"POST","/account/password",data={
            "csrf_token":csrf,"current_password":"initial-secure-password",
            "new_password":"new-secure-admin-987","confirm_password":"new-secure-admin-987"})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.request(self.admin_client,"GET","/account").status_code,302)
        token=base64.b64encode(b"founder:initial-secure-password").decode()
        self.assertEqual(self.request(self.other_client,"GET","/api/history",
                                      headers={"Authorization":"Basic "+token}).status_code,401)
        self.assertNotEqual(self.signed_in(self.admin_client,"founder","initial-secure-password").status_code,302)
        self.assertEqual(self.signed_in(self.admin_client,"founder","new-secure-admin-987").status_code,302)
        # A fresh instance with the old env value cannot overwrite new DB hash.
        fresh=UserStore(self.root/"auth.db",seed_username="founder",seed_password="initial-secure-password")
        self.assertIsNone(fresh.authenticate("founder","initial-secure-password"))
        self.assertTrue(fresh.authenticate("founder","new-secure-admin-987"))

    def test_secure_cookie_attributes_and_admin_self_reset_rejected(self):
        self.assertEqual(self.signed_in(self.admin_client,"founder","initial-secure-password").status_code,302)
        response=self.request(self.admin_client,"GET","/account")
        self.assertEqual(response.status_code,200)
        admin=appmod.get_user_store().list_users()[0]
        result=self.request(self.admin_client,"POST","/admin/users/"+admin["id"]+"/password",data={
            "csrf_token":self.current_csrf(self.admin_client),
            "password":"bypass-self-password","confirm_password":"bypass-self-password"})
        self.assertEqual(result.status_code,400)
        self.assertTrue(appmod.get_user_store().authenticate("founder","initial-secure-password"))

    def test_last_admin_cannot_be_removed_and_duplicate_user_fails(self):
        store=appmod.get_user_store()
        admin=store.list_users()[0]
        with self.assertRaises(UserError):
            store.set_active(admin["id"],False,actor_user_id=admin["id"])
        with self.assertRaises(UserError):
            store.set_role(admin["id"],"member",actor_user_id=admin["id"])
        store.create_user(username="testuser",password="password-12345")
        with self.assertRaises(UserError):
            store.create_user(username="TESTUSER",password="password-12345")

    def test_password_policy_accepts_eight_characters_and_symbols(self):
        store=appmod.get_user_store()
        # Eight characters is the new minimum; symbol choice has no restriction.
        user=store.create_user(username="shortuser",password="abc#1234")
        self.assertIsNotNone(store.authenticate("shortuser","abc#1234"))
        store.set_password(user["id"],"example#1980",
                           actor_user_id=user["id"],current_password="abc#1234")
        self.assertIsNotNone(store.authenticate("shortuser","example#1980"))
        for password in ("", "a#12345", "x"*257):
            with self.subTest(length=len(password)):
                with self.assertRaises(UserError) as check:
                    store.validate_new_password(password)
                self.assertEqual(str(check.exception),"PASSWORD_LENGTH")
        # The former 12+ character passwords continue to work.
        other=store.create_user(username="longuser",password="previously-valid-password")
        self.assertIsNotNone(store.authenticate("longuser","previously-valid-password"))

    def test_eight_character_password_works_in_all_three_account_flows(self):
        self.assertEqual(self.signed_in(self.admin_client,"founder","initial-secure-password").status_code,302)
        csrf=self.current_csrf(self.admin_client)
        # 1. Administrator creates an account.
        response=self.request(self.admin_client,"POST","/admin/users",data={
            "csrf_token":csrf,"username":"shortuser","password":"example#1980","role":"member"})
        self.assertEqual(response.status_code,200)
        uid=appmod.get_user_store().get_user_by_name("shortuser")["id"]
        self.assertEqual(self.signed_in(self.member_client,"shortuser","example#1980").status_code,302)
        # 2. Administrator resets its password to the minimum length.
        reset=self.request(self.admin_client,"POST",f"/admin/users/{uid}/password",data={
            "csrf_token":csrf,"password":"ab#12345","confirm_password":"ab#12345"})
        self.assertEqual(reset.status_code,200)
        self.assertEqual(self.signed_in(self.member_client,"shortuser","ab#12345").status_code,302)
        # 3. User changes their own password to a new eight-character one.
        member_csrf=self.current_csrf(self.member_client)
        changed=self.request(self.member_client,"POST","/account/password",data={
            "csrf_token":member_csrf,"current_password":"ab#12345",
            "new_password":"xy#12345","confirm_password":"xy#12345"})
        self.assertEqual(changed.status_code,302)
        self.assertEqual(self.signed_in(self.member_client,"shortuser","xy#12345").status_code,302)
        # Seven-character input must still be rejected server-side.
        invalid=self.request(self.admin_client,"POST","/admin/users",data={
            "csrf_token":csrf,"username":"too_short","password":"x#12345","role":"member"})
        self.assertEqual(invalid.status_code,400)
        self.assertIn("至少需要 8 个字符",invalid.get_data(as_text=True))

    def test_password_input_attributes_match_backend_policy(self):
        self.assertEqual(self.signed_in(self.admin_client,"founder","initial-secure-password").status_code,302)
        for path in ("/admin/users","/account"):
            html=self.request(self.admin_client,"GET",path).get_data(as_text=True)
            self.assertIn('minlength="8"',html)
            self.assertIn("至少 8 个字符",html)
            self.assertNotIn('minlength="12"',html)

    def test_form_and_basic_failed_login_attempts_are_throttled(self):
        self.request(self.admin_client,"GET","/login")
        csrf=self.current_csrf(self.admin_client)
        for attempt in range(6):
            bad=self.request(self.admin_client,"POST","/login",data={
                "csrf_token":csrf,"username":"founder","password":"wrong-"+str(attempt)})
            self.assertEqual(bad.status_code,200)
        blocked=self.request(self.admin_client,"POST","/login",data={
            "csrf_token":csrf,"username":"founder","password":"initial-secure-password"})
        self.assertIn("尝试过多",blocked.get_data(as_text=True))
        basic="Basic "+base64.b64encode(b"founder:initial-secure-password").decode()
        self.assertEqual(self.request(self.other_client,"GET","/api/history",
                                      headers={"Authorization":basic}).status_code,401)
        appmod.LOGIN_FAILURES.clear()
        self.assertEqual(self.signed_in(self.admin_client,"founder","initial-secure-password").status_code,302)

    def test_old_login_true_cookie_no_longer_grants_access(self):
        with self.admin_client.session_transaction() as sess:
            sess["logged_in"]=True
        self.assertEqual(self.request(self.admin_client,"GET","/api/history").status_code,401)


if __name__=="__main__":
    unittest.main()
