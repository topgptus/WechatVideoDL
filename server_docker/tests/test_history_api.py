import base64
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

import server_docker.app as appmod
from server_docker.history_store import HistoryStore


class HistoryApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.downloads = root / "downloads"
        self.downloads.mkdir()
        self.old = {}
        for name, value in {
            "DATA_DIR": root,
            "DOWNLOAD_DIR": self.downloads,
            "PUBLIC_BASE_URL": "",
            "TRUST_PROXY_HEADERS": False,
            "WEBUI_USERNAME": "u",
            "WEBUI_PASSWORD": "p",
        }.items():
            self.old[name] = getattr(appmod, name, None)
            setattr(appmod, name, value)
        self.old_store = getattr(appmod, "HISTORY_STORE", None)
        self.store = HistoryStore(root / "history.db")
        appmod.HISTORY_STORE = self.store
        appmod.app.config.update(TESTING=True)
        self.client = appmod.app.test_client()
        token = base64.b64encode(b"u:p").decode()
        self.headers = {"Authorization": f"Basic {token}"}

    def tearDown(self):
        appmod.HISTORY_STORE = self.old_store
        for name, value in self.old.items():
            setattr(appmod, name, value)
        self.tmp.cleanup()

    def create(self, job_id, *, fmt="md", status="completed", title=None):
        self.store.create_job(
            id=job_id,
            input_url=f"https://weixin.qq.com/sph/{job_id}",
            prompt="prompt",
            preset_id=None,
            preset_name=None,
            output_format=fmt,
            download_requested=True,
            max_attempts=3,
        )
        self.store.update_job(
            job_id,
            status=status,
            yuanbao_content=f"content {job_id}",
            video_title=title,
        )

    def test_history_requires_auth(self):
        self.assertEqual(self.client.get("/api/history").status_code, 401)

    def test_list_is_newest_first_and_detail_has_content(self):
        self.create("wxv_000201")
        self.create("wxv_000202")
        r = self.client.get("/api/history", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual([j["id"] for j in body["jobs"]], ["wxv_000202", "wxv_000201"])
        detail = self.client.get("/api/history/wxv_000202", headers=self.headers)
        self.assertEqual(detail.status_code, 200)
        job = detail.get_json()["job"]
        self.assertEqual(job["content"], "content wxv_000202")
        self.assertEqual(job["title"], "wxv_000202")  # legacy record fallback

    def test_serializes_title_source_thumbnail_and_bundle_links(self):
        self.create("wxv_000206", title="拿破仑密信")
        thumb = self.downloads / "拿破仑密信__000206.jpg"
        thumb.write_bytes(b"jpg")
        text = self.downloads / "拿破仑密信__000206.md"
        text.write_text("md", "utf-8")
        self.store.update_job(
            "wxv_000206",
            thumbnail_filename=thumb.name,
            text_filename=text.name,
            text_bytes=text.stat().st_size,
        )
        r = self.client.get("/api/history/wxv_000206", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        job = r.get_json()["job"]
        self.assertEqual(job["title"], "拿破仑密信")
        self.assertEqual(job["source_url"], "https://weixin.qq.com/sph/wxv_000206")
        self.assertEqual(job["thumbnail"]["filename"], thumb.name)
        self.assertTrue(job["thumbnail"]["download_path"].endswith(".jpg"))
        self.assertEqual(job["bundle"]["download_path"], "/api/history/wxv_000206/bundle")

    def test_detail_missing_returns_404(self):
        r = self.client.get("/api/history/wxv_999999", headers=self.headers)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()["error"]["code"], "NOT_FOUND")

    def test_bundle_requires_auth_and_missing_job_is_404(self):
        self.assertEqual(self.client.get("/api/history/wxv_000201/bundle").status_code, 401)
        r = self.client.get("/api/history/wxv_999999/bundle", headers=self.headers)
        self.assertEqual(r.status_code, 404)

    def test_bundle_download_persists_filename_and_contains_titled_members(self):
        self.create("wxv_000207", title="标题 A")
        text = self.downloads / "标题 A__000207.md"
        video = self.downloads / "标题 A__000207.mp4"
        thumb = self.downloads / "标题 A__000207.jpg"
        text.write_text("md", "utf-8")
        video.write_bytes(b"video")
        thumb.write_bytes(b"jpg")
        self.store.update_job(
            "wxv_000207",
            text_filename=text.name,
            video_filename=video.name,
            thumbnail_filename=thumb.name,
        )
        r = self.client.get("/api/history/wxv_000207/bundle", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers.get("Content-Disposition", ""))
        with zipfile.ZipFile(io.BytesIO(r.data)) as zf:
            self.assertEqual(
                sorted(zf.namelist()),
                sorted(["标题 A__000207.md", "标题 A__000207.mp4", "标题 A__000207.jpg"]),
            )
        row = self.store.get_job("wxv_000207")
        self.assertEqual(row["bundle_filename"], "标题 A__000207.zip")
        r.close()

    def test_bundle_legacy_record_uses_safe_fallback_name(self):
        self.create("wxv_000208")
        text = self.downloads / "wxv_000208.md"
        text.write_text("legacy", "utf-8")
        self.store.update_job("wxv_000208", text_filename=text.name)
        r = self.client.get("/api/history/wxv_000208/bundle", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.store.get_job("wxv_000208")["bundle_filename"], "微信视频号__000208.zip")
        r.close()

    def test_delete_removes_all_db_owned_files_and_same_title_neighbor_survives(self):
        self.create("wxv_000203", title="同名")
        self.create("wxv_000204", title="同名")
        owned = []
        for ext, key in (("md", "text_filename"), ("mp4", "video_filename"), ("jpg", "thumbnail_filename"), ("zip", "bundle_filename")):
            p = self.downloads / f"同名__000203.{ext}"
            p.write_bytes(b"x")
            owned.append(p)
            self.store.update_job("wxv_000203", **{key: p.name})
        neighbor = self.downloads / "同名__000204.md"
        neighbor.write_text("neighbor", "utf-8")
        self.store.update_job("wxv_000204", text_filename=neighbor.name)

        r = self.client.delete("/api/history/wxv_000203", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        self.assertTrue(all(not p.exists() for p in owned))
        self.assertIsNone(self.store.get_job("wxv_000203"))
        self.assertIsNotNone(self.store.get_job("wxv_000204"))
        self.assertTrue(neighbor.exists())

    def test_running_job_cannot_be_deleted(self):
        self.create("wxv_000212", status="running")
        response = self.client.delete("/api/history/wxv_000212", headers=self.headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"]["code"], "JOB_ACTIVE")
        self.assertIsNotNone(self.store.get_job("wxv_000212"))

    def test_legacy_invalid_analysis_has_accurate_partial_or_failed_status(self):
        self.create("wxv_000217", title="长视频")
        self.store.update_job("wxv_000217", yuanbao_content="抱歉，未找到相关视频",
                              video_filename="long_video.mp4")
        partial = self.client.get("/api/history/wxv_000217", headers=self.headers)
        self.assertEqual(partial.status_code, 200)
        self.assertEqual(partial.get_json()["job"]["status"], "partial")
        self.assertEqual(partial.get_json()["job"]["analysis_status"], "unavailable")

        self.create("wxv_000218", title="没有视频")
        self.store.update_job("wxv_000218", yuanbao_content="抱歉，未找到相关视频")
        failed = self.client.get("/api/history/wxv_000218", headers=self.headers)
        self.assertEqual(failed.get_json()["job"]["status"], "failed")

    def test_transcript_download_link_is_serialized_when_present(self):
        self.create("wxv_000219", title="完整口播")
        transcript = self.downloads / "wxv_000219.transcript.txt"
        transcript.write_text("人工校对的完整口播", encoding="utf-8")
        self.store.update_job("wxv_000219", transcript_filename=transcript.name,
                              analysis_source="local_transcript", analysis_status="completed")
        r = self.client.get("/api/history/wxv_000219", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        job = r.get_json()["job"]
        self.assertEqual(job["analysis_source"], "local_transcript")
        self.assertTrue(job["transcript"]["download_path"].endswith(".transcript.txt"))

    def test_delete_tolerates_missing_optional_file(self):
        self.create("wxv_000209", title="缺文件")
        text = self.downloads / "缺文件__000209.md"
        text.write_text("hello", "utf-8")
        self.store.update_job(
            "wxv_000209", text_filename=text.name,
            video_filename="缺文件__000209.mp4", video_bytes=123,
        )
        r = self.client.delete("/api/history/wxv_000209", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(text.exists())
        self.assertIsNone(self.store.get_job("wxv_000209"))

    def test_delete_rejects_malformed_id_and_cannot_delete_arbitrary_path(self):
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("safe", "utf-8")
        self.create("wxv_000205")
        self.store.update_job("wxv_000205", text_filename="../../outside.txt")

        bad = self.client.delete("/api/history/not-valid", headers=self.headers)
        self.assertEqual(bad.status_code, 400)
        ok = self.client.delete("/api/history/wxv_000205", headers=self.headers)
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
