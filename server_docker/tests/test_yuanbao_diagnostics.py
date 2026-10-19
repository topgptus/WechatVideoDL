import unittest
from pathlib import Path
from unittest.mock import patch
from server_docker import app as appmod

ROOT = Path(__file__).resolve().parents[1]

class YuanbaoDiagnosticTests(unittest.TestCase):
    def test_read_only_browser_script(self):
        source = (ROOT / 'browserless_diagnostic.js').read_text()
        self.assertNotIn('page.click(', source)
        self.assertNotIn('page.keyboard.', source)
        self.assertIn('type: \'application/json\'', source)

    def test_new_chat_before_sending(self):
        source = (ROOT / 'browserless_function.js').read_text()
        self.assertLess(source.index("page.click('[aria-label=\"New Chat\"]')"), source.index('page.click("#yuanbao-send-btn")'))

    def test_send_acknowledgment_before_card_wait(self):
        source = (ROOT / 'browserless_function.js').read_text()
        self.assertLess(source.index('YUANBAO_SEND_UNCONFIRMED'), source.index('page.waitForSelector(".hyc-link-card"'))
        self.assertIn('occurrences > previous', source)

    def test_admin_only_diagnostic_and_allowlisted_response(self):
        client = appmod.app.test_client()
        with patch.object(appmod, 'current_user', return_value={'id': 'member', 'role': 'member'}):
            response = client.get('/api/admin/browserless-diagnostic')
            self.assertEqual(response.status_code, 403)
        with patch.object(appmod, 'current_user', return_value={'id': 'admin', 'role': 'admin'}), \
             patch.object(appmod, 'load_cookies', return_value=[]), \
             patch.object(appmod, 'load_session_state', return_value={}), \
             patch.object(appmod, 'browserless_function', return_value={'editorVisible': True, 'secret': 'NEVER_PRINT'}):
            response = client.get('/api/admin/browserless-diagnostic')
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('secret', response.get_data(as_text=True))
            self.assertTrue(response.json['diagnostic']['editorVisible'])

if __name__ == '__main__': unittest.main()
