"""Browser automation must acknowledge the submitted message and isolate chats."""
from pathlib import Path
import unittest


class YuanbaoSendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = (Path(__file__).resolve().parents[1] / 'browserless_function.js').read_text('utf8')

    def test_every_task_opens_its_own_conversation(self):
        self.assertIn('YUANBAO_NEW_CHAT_UNAVAILABLE',self.script)
        self.assertIn('await openFreshChat(page)',self.script)
        self.assertIn('[aria-label="新建对话"]',self.script)
        self.assertIn('[aria-label="New Chat"]',self.script)
        self.assertLess(self.script.index('await openFreshChat(page)'),
                        self.script.index('page.click("#yuanbao-send-btn")'))

    def test_text_analysis_also_uses_visible_localized_new_chat(self):
        source = (Path(__file__).resolve().parents[1] / 'browserless_text_function.js').read_text('utf8')
        self.assertIn('await openFreshChat(page)',source)
        self.assertIn('[aria-label="新建对话"]',source)
        self.assertIn("candidate.boundingBox()",source)

    def test_editor_text_must_match_full_request(self):
        self.assertIn('editorText !== requestText',self.script)
        self.assertIn('YUANBAO_EDITOR_MISMATCH',self.script)

    def test_message_send_has_delivery_confirmation(self):
        self.assertIn('YUANBAO_SEND_UNCONFIRMED',self.script)
        self.assertIn('messageAcknowledged',self.script)
        self.assertLess(self.script.index('page.click("#yuanbao-send-btn")'),
                        self.script.index('if (!messageAcknowledged)'))

    def test_no_logging_of_auth_or_video_text(self):
        self.assertNotIn('console.log(requestText)',self.script)
        self.assertNotIn('console.log(context.cookies)',self.script)
        self.assertNotIn('console.log(context.session)',self.script)


if __name__=='__main__':unittest.main()
