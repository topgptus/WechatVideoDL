import tempfile
import unittest
from pathlib import Path

import server_docker.app as appmod


class HealthApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cookie = root / "yuanbao.tencent.com_cookies.txt"
        self.cookie.write_text("dummy", encoding="utf-8")
        self.old_cookie = appmod.COOKIE_FILE
        self.old_get = appmod.requests.get
        self.old_pending = appmod.MAX_PENDING_JOBS
        self.old_poll = getattr(appmod, "POLL_INTERVAL_SECONDS", None)
        appmod.COOKIE_FILE = self.cookie
        appmod.MAX_PENDING_JOBS = 8
        appmod.POLL_INTERVAL_SECONDS = 3

        class Response:
            status_code = 200
            text = "ok"

        appmod.requests.get = lambda *args, **kwargs: Response()
        appmod.app.config.update(TESTING=True)
        self.client = appmod.app.test_client()

    def tearDown(self):
        appmod.COOKIE_FILE = self.old_cookie
        appmod.requests.get = self.old_get
        appmod.MAX_PENDING_JOBS = self.old_pending
        if self.old_poll is None:
            delattr(appmod, "POLL_INTERVAL_SECONDS")
        else:
            appmod.POLL_INTERVAL_SECONDS = self.old_poll
        self.tmp.cleanup()

    def test_health_declares_async_queue_capabilities(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["async_jobs"])
        self.assertEqual(body["max_pending_jobs"], 8)
        self.assertEqual(body["worker_model"], "single-process")
        self.assertTrue(body["queue_persistent"])
        self.assertEqual(body["poll_interval_seconds"], 3)


if __name__ == "__main__":
    unittest.main()
