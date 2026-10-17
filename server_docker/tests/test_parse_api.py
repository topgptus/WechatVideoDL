import base64
import tempfile
import threading
import time
import unittest
from pathlib import Path

import server_docker.app as appmod
from server_docker.history_store import HistoryStore


class ParseApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.downloads = root / "downloads"
        self.downloads.mkdir()
        self.old = {}
        for name, value in {
            "DATA_DIR": root,
            "DOWNLOAD_DIR": self.downloads,
            "SEQUENCE_FILE": root / "sequence.txt",
            "PUBLIC_BASE_URL": "",
            "TRUST_PROXY_HEADERS": False,
            "WEBUI_USERNAME": "tester",
            "WEBUI_PASSWORD": "secret",
            "MAX_PARSE_ATTEMPTS": 3,
        }.items():
            self.old[name] = getattr(appmod, name, None)
            setattr(appmod, name, value)
        self.old_store = getattr(appmod, "HISTORY_STORE", None)
        appmod.HISTORY_STORE = HistoryStore(root / "history.db")
        self.old_analyzer = appmod.analyze_with_yuanbao
        self.old_downloader = appmod.download_video
        appmod.analyze_with_yuanbao = lambda *_: {
            "content": "analysis body",
            "previewUrl": "https://channels.weixin.qq.com/finder-preview/demo",
            "directUrl": "https://finder.video.qq.com/demo.mp4",
            "card": {"title": "Demo"},
        }
        def downloader(job_id, direct_url, referer):
            path = self.downloads / f"{job_id}.mp4"
            path.write_bytes(b"video")
            return path
        appmod.download_video = downloader
        appmod.app.config.update(TESTING=True)
        self.client = appmod.app.test_client()

    def tearDown(self):
        appmod.analyze_with_yuanbao = self.old_analyzer
        appmod.download_video = self.old_downloader
        appmod.HISTORY_STORE = self.old_store
        for name, value in self.old.items():
            setattr(appmod, name, value)
        self.tmp.cleanup()

    def auth(self):
        token = base64.b64encode(b"tester:secret").decode()
        return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    def post(self, payload):
        return self.client.post("/api/parse", json=payload, headers=self.auth())

    def await_job(self, job_id, timeout=4.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/history/{job_id}", headers=self.auth())
            self.assertEqual(response.status_code, 200)
            job = response.get_json()["job"]
            if job["status"] in ("completed", "failed"):
                return job
            time.sleep(0.02)
        self.fail(f"Job {job_id} did not finish in {timeout}s")

    def test_omitted_output_format_defaults_to_md(self):
        r = self.post({"url": "https://weixin.qq.com/sph/demo", "download": False})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.headers["Location"], f"/api/history/{r.get_json()['id']}")
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertTrue(r.get_json()["ok"])
        body = self.await_job(r.get_json()["id"])
        self.assertTrue(body["text"]["filename"].endswith(".md"))
        self.assertEqual(body["output_format"], "md")

    def test_markdown_output_and_retry_metadata_and_proxy_safe_links(self):
        r = self.post({
            "url": "https://weixin.qq.com/sph/demo",
            "prompt": "总结",
            "output_format": "md",
            "download": True,
        })
        self.assertEqual(r.status_code, 202)
        body = self.await_job(r.get_json()["id"])
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["attempts_used"], 1)
        self.assertEqual(body["max_attempts"], 3)
        self.assertEqual(body["retry_errors"], [])
        self.assertEqual(body["title"], "Demo")
        self.assertRegex(body["id"], r"^wxv_\d{6}$")
        self.assertRegex(body["text"]["filename"], r"^Demo__\d{6}\.md$")
        self.assertRegex(body["text"]["download_path"], r"^/files/Demo__\d{6}\.md$")
        self.assertTrue(body["text"]["download_url"].startswith("http://localhost/files/"))
        self.assertRegex(body["video"]["download_path"], r"^/files/Demo__\d{6}\.mp4$")

    def test_history_api_includes_optional_transcription_duration(self):
        r = self.post({"url": "https://weixin.qq.com/sph/demo", "download": False})
        body = self.await_job(r.get_json()["id"])
        self.assertIn("transcription_elapsed_ms", body)
        self.assertIsNone(body["transcription_elapsed_ms"])
        appmod.HISTORY_STORE.update_job(body["id"], transcription_elapsed_ms=187500)
        updated = self.client.get(f"/api/history/{body['id']}", headers=self.auth()).get_json()["job"]
        self.assertEqual(updated["transcription_elapsed_ms"], 187500)
        history = self.client.get("/api/history", headers=self.auth()).get_json()["jobs"]
        self.assertEqual(history[0]["transcription_elapsed_ms"], 187500)

    def test_invalid_output_format_returns_json_400_without_analyzer(self):
        calls = []
        appmod.analyze_with_yuanbao = lambda *_: calls.append(1)
        r = self.post({
            "url": "https://weixin.qq.com/sph/demo",
            "output_format": "html",
        })
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.content_type, "application/json")
        body = r.get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"]["code"], "INVALID_OUTPUT_FORMAT")
        self.assertEqual(calls, [])

    def test_malformed_json_returns_json_400(self):
        r = self.client.post("/api/parse", data="{bad", headers=self.auth())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.content_type, "application/json")
        body = r.get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"]["code"], "BAD_REQUEST")

    def test_next_id_recovers_from_text_only_artifacts(self):
        (self.downloads / "wxv_000042.md").write_text("old", encoding="utf-8")
        if appmod.SEQUENCE_FILE.exists():
            appmod.SEQUENCE_FILE.unlink()
        self.assertEqual(appmod.next_id(), "wxv_000043")

    def test_failed_job_returns_json_with_attempt_metadata(self):
        class UpstreamError(RuntimeError):
            code = "BROWSERLESS_UNREACHABLE"
            status = 503
            message = "temporarily unavailable"
        appmod.analyze_with_yuanbao = lambda *_: (_ for _ in ()).throw(UpstreamError())
        old_sleep = getattr(appmod, "RETRY_SLEEPER", None)
        appmod.RETRY_SLEEPER = lambda _: None
        try:
            r = self.post({"url": "https://weixin.qq.com/sph/demo", "download": False})
        finally:
            appmod.RETRY_SLEEPER = old_sleep
        self.assertEqual(r.status_code, 202)
        body = self.await_job(r.get_json()["id"])
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["attempts_used"], 3)
        self.assertEqual(len(body["retry_errors"]), 3)
        self.assertEqual(body["error_code"], "BROWSERLESS_UNREACHABLE")

    def test_returns_immediately_and_polls_until_completion(self):
        started, release = threading.Event(), threading.Event()

        def slow_analyzer(*_):
            started.set()
            release.wait(4)
            return {
                "content": "slow result",
                "previewUrl": "https://channels.weixin.qq.com/finder-preview/demo",
                "directUrl": "https://finder.video.qq.com/demo.mp4",
                "card": {"title": "Slow"},
            }
        appmod.analyze_with_yuanbao = slow_analyzer
        started_at = time.monotonic()
        r = self.post({"url": "https://weixin.qq.com/sph/slow", "download": False})
        self.assertEqual(r.status_code, 202)
        self.assertLess(time.monotonic() - started_at, 1)
        job_id = r.get_json()["id"]
        self.assertTrue(started.wait(2))
        pending = self.client.get(f"/api/history/{job_id}", headers=self.auth()).get_json()["job"]
        self.assertEqual(pending["status"], "running")
        blocked = self.client.delete(f"/api/history/{job_id}", headers=self.auth())
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.get_json()["error"]["code"], "JOB_ACTIVE")
        release.set()
        result = self.await_job(job_id)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["content"], "slow result")

    def test_bounded_queue_returns_429_without_creating_another_job(self):
        original_slots = appmod.JOB_SLOTS
        blocker = threading.Event()
        appmod.JOB_SLOTS = threading.BoundedSemaphore(1)
        try:
            original_analyzer = appmod.analyze_with_yuanbao
            def waiting(*args):
                blocker.wait(4)
                return original_analyzer(*args)
            appmod.analyze_with_yuanbao = waiting
            first = self.post({"url": "https://weixin.qq.com/sph/first", "download": False})
            self.assertEqual(first.status_code, 202)
            second = self.post({"url": "https://weixin.qq.com/sph/second", "download": False})
            self.assertEqual(second.status_code, 429)
            self.assertEqual(second.get_json()["error"]["code"], "QUEUE_FULL")
            blocker.set()
            self.assertEqual(self.await_job(first.get_json()["id"])["status"], "completed")
            self.assertEqual(len(appmod.HISTORY_STORE.list_jobs()), 1)
        finally:
            blocker.set()
            appmod.JOB_SLOTS = original_slots

    def test_empty_card_title_uses_human_fallback_filename(self):
        appmod.analyze_with_yuanbao = lambda *_: {
            "content": "analysis body",
            "previewUrl": "https://channels.weixin.qq.com/finder-preview/demo",
            "directUrl": "https://finder.video.qq.com/demo.mp4",
            "card": {"title": "", "coverUrl": ""},
        }
        r = self.post({"url": "https://weixin.qq.com/sph/demo", "download": False, "output_format": "md"})
        self.assertEqual(r.status_code, 202)
        body = self.await_job(r.get_json()["id"])
        self.assertEqual(body["title"], "微信视频号")
        self.assertRegex(body["text"]["filename"], r"^微信视频号__\d{6}\.md$")


if __name__ == "__main__":
    unittest.main()
