"""Consolidated settings UI and legacy route compatibility."""
import unittest
from pathlib import Path
from unittest.mock import patch
from server_docker import app as appmod

ROOT = Path(__file__).resolve().parents[1]

class SettingsUiTests(unittest.TestCase):
    def test_header_account_name_and_settings_entry(self):
        markup = (ROOT / 'templates/base.html').read_text()
        self.assertIn("url_for('settings_page')", markup)
        self.assertIn('current_user().username', markup)
        self.assertIn('header-logout', markup)
        self.assertNotIn("url_for('admin_users')", markup)

    def test_settings_contains_sections_and_diagnostic(self):
        markup = (ROOT / 'templates/settings.html').read_text()
        for name in ('account', 'appearance', 'users', 'yuanbao'):
            self.assertIn('data-settings-panel="' + name + '"', markup)
        self.assertIn('yuanbao-diagnostic-button', markup)
        self.assertIn("current_user().role == 'admin'", markup)

    def test_workspace_no_admin_diagnostic(self):
        self.assertNotIn('yuanbao-diagnostic-button', (ROOT / 'templates/index.html').read_text())

    def test_panel_navigation_and_mobile_targets(self):
        js = (ROOT / 'static/app.js').read_text()
        css = (ROOT / 'static/app.css').read_text()
        self.assertIn('if (page === "settings") initSettings()', js)
        self.assertIn('hashchange', js)
        self.assertIn('grid-template-columns:repeat(3,minmax(0,1fr))', css)
        self.assertIn('prefers-reduced-motion:reduce', css)

    def test_legacy_account_and_admin_users_paths_share_settings(self):
        client = appmod.app.test_client()
        member = {"username": "member", "role": "member", "id": "m", "is_superadmin": False}
        admin = {"username": "admin", "role": "admin", "id": "a", "is_superadmin": True}
        with patch.object(appmod, 'current_user', return_value=member):
            self.assertIn('data-page="settings"', client.get('/account').get_data(as_text=True))
            self.assertEqual(client.get('/admin/users').status_code, 403)
        with patch.object(appmod, 'current_user', return_value=admin), patch.object(appmod, 'get_user_store') as store:
            store.return_value.list_users.return_value = []
            self.assertIn('data-initial-section="users"', client.get('/admin/users').get_data(as_text=True))

    def test_settings_admin_and_member_visibility(self):
        client = appmod.app.test_client()
        with patch.object(appmod, 'current_user', return_value={"username": "member", "role": "member", "id": "m", "is_superadmin": False}):
            reply = client.get('/settings')
            self.assertEqual(reply.status_code, 200)
            html = reply.get_data(as_text=True)
            self.assertIn('账号与安全', html)
            self.assertNotIn('id="yuanbao-diagnostic-button"', html)
            self.assertNotIn('data-settings-panel="users"', html)
        with patch.object(appmod, 'current_user', return_value={"username": "admin", "role": "admin", "id": "a", "is_superadmin": True}), \
             patch.object(appmod, 'get_user_store') as store:
            store.return_value.list_users.return_value = []
            reply = client.get('/settings')
            self.assertEqual(reply.status_code, 200)
            self.assertIn('id="yuanbao-diagnostic-button"', reply.get_data(as_text=True))

if __name__ == '__main__':
    unittest.main()
