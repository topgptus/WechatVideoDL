"""Admin-only shared credentials: upload, safe status, atomic replacement, CSRF."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_docker import app as module
from server_docker.yuanbao_credentials import (CredentialImportError, parse_netscape_cookies,
    parse_session, credential_status, MAX_COOKIES_BYTES)

COOKIE = (b'# Netscape HTTP Cookie File\n'
          b'.tencent.com\tTRUE\t/\tTRUE\t1893456000\ttoken_name\tPRIVATE_COOKIE_VALUE\n')
SESSION = json.dumps({'ua':'Browser UA', 'localStorage': {'key': 'PRIVATE_SESSION_VALUE'},
                     'sessionStorage': {'id':'session-value'}}).encode()


class SharedCredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cookie = Path(self.temp.name) / 'cookies.txt'
        self.session = Path(self.temp.name) / 'session.json'
        self.owner = {'id': 'owner', 'username': 'test-admin', 'role': 'admin','is_superadmin': True}
        self.member = {'id': 'member', 'username':'test-user','role':'member','is_superadmin': False}
        self.who = self.owner
        self.patches = [patch.object(module,'COOKIE_FILE',self.cookie),
                        patch.object(module,'SESSION_FILE',self.session),
                        patch.object(module,'current_user',side_effect=lambda:self.who),
                        patch.object(module,'get_user_store')]
        for p in self.patches: p.start()
        self.client=module.app.test_client()
        with self.client.session_transaction() as state:
            state['csrf']='test-csrf-value'

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def upload(self,kind,payload,filename,token='test-csrf-value'):
        return self.client.post(f'/admin/yuanbao/import/{kind}',base_url='https://localhost',
            content_type='multipart/form-data',data={
                'credential_file': (io.BytesIO(payload), filename),
                'csrf_token': token})

    def test_admin_imported_cookies_are_shared_with_worker_without_leaking_them(self):
        response=self.upload('cookies',COOKIE,'cookies.txt')
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.cookie.stat().st_mode & 0o777,0o600)
        page=response.get_data(as_text=True)
        self.assertIn('Cookie 已更新',page)
        self.assertNotIn('PRIVATE_COOKIE_VALUE',page)
        self.assertNotIn('token_name',page)
        self.assertEqual(credential_status(self.cookie,self.session)['cookies'],'ready')
        self.who=self.member
        self.assertEqual(module.load_cookies()[0]['value'],'PRIVATE_COOKIE_VALUE')
        self.assertNotIn('导入／更新 Cookie',self.client.get('/settings',base_url='https://localhost').get_data(as_text=True))

    def test_session_import_is_independent_and_shared(self):
        self.assertEqual(self.upload('session',SESSION,'session.json').status_code,200)
        self.assertFalse(self.cookie.exists())
        self.assertEqual(module.load_session_state()['localStorage']['key'],'PRIVATE_SESSION_VALUE')
        self.who=self.member
        self.assertIn('PRIVATE_SESSION_VALUE',module.load_session_state()['localStorage'].values())
        self.assertNotIn('PRIVATE_SESSION_VALUE',self.client.get('/settings',base_url='https://localhost').get_data(as_text=True))
        self.assertEqual(self.session.stat().st_mode & 0o777,0o600)

    def test_invalid_upload_does_not_replace_existing_credentials(self):
        self.upload('cookies',COOKIE,'cookies.txt')
        self.upload('session',SESSION,'session.json')
        before_cookie=self.cookie.read_bytes();before_session=self.session.read_bytes()
        for bad in (b'invalid non-netscape file',b'.evil.example\tTRUE\t/\tTRUE\t1893456000\tfoo\tbar',b''):
            self.assertEqual(self.upload('cookies',bad,'cookies.txt').status_code,400)
        for bad in (b'[1,2]',b'{"other":"data"}',b'{broken json}',b'{}'):
            self.assertEqual(self.upload('session',bad,'session.json').status_code,400)
        self.assertEqual(self.cookie.read_bytes(),before_cookie)
        self.assertEqual(self.session.read_bytes(),before_session)

    def test_oversized_rejected_before_overwriting_private_file(self):
        self.upload('cookies',COOKIE,'cookie.txt')
        response=self.upload('cookies',b'A'*(MAX_COOKIES_BYTES+1),'cookie.txt')
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.cookie.read_bytes(),COOKIE)

    def test_member_and_missing_csrf_cannot_upload(self):
        self.who=self.member
        denied=self.upload('cookies',COOKIE,'cookies.txt')
        self.assertEqual(denied.status_code,403)
        self.assertFalse(self.cookie.exists())
        self.who=self.owner
        no_csrf=self.upload('cookies',COOKIE,'cookies.txt',token='invalid')
        self.assertEqual(no_csrf.status_code,403)
        self.assertFalse(self.cookie.exists())

    def test_active_task_blocks_credentials_replacement(self):
        with patch.object(module,'PROCESS_LOCK') as lock:
            lock.acquire.return_value=False
            response=self.upload('cookies',COOKIE,'cookie.txt')
        self.assertEqual(response.status_code,409)
        self.assertFalse(self.cookie.exists())

    def test_diagnostic_and_status_never_echo_tokens(self):
        self.upload('cookies',COOKIE,'cookies.txt')
        self.upload('session',SESSION,'session.json')
        response=self.client.get('/settings',base_url='https://localhost')
        page=response.get_data(as_text=True)
        for secret in ('PRIVATE_COOKIE_VALUE','PRIVATE_SESSION_VALUE','token_name'):
            self.assertNotIn(secret,page)
        self.assertIn('已配置',page)
        self.assertIn('普通用户不能',page)

    def test_validators_accept_supported_cookie_and_browser_fields(self):
        self.assertEqual(len(parse_netscape_cookies(COOKIE)),1)
        self.assertEqual(parse_session(SESSION)['ua'],'Browser UA')
        with self.assertRaises(CredentialImportError):
            parse_session(b'[["not", "object"]]')
        with self.assertRaises(CredentialImportError):
            parse_session(b'{"localStorage":["bad"]}')


if __name__=='__main__':unittest.main()
