import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from server_docker.content_quality import refusal_reason, valid_analysis
from server_docker.transcription_queue import transcribe_with_host, TranscriptionError
from server_docker.scripts.host_transcription_worker import run_once, resolve_video


class HybridVoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        (self.data/"downloads").mkdir()
        (self.data/"transcription_queue").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_classifies_known_yuanbao_refusals(self):
        for text in (
            "抱歉，未找到相关视频",
            "抱歉, 未找到相关视频",
            "这个视频我暂时还没办法理解哦",
            "暂时无法分析这个视频",
        ):
            self.assertEqual(refusal_reason(text), "YUANBAO_CONTENT_UNAVAILABLE")
        self.assertEqual(refusal_reason(""), "YUANBAO_EMPTY_RESPONSE")
        self.assertTrue(valid_analysis("第一部分：内容总结。视频介绍了人口结构的背景。"))
        self.assertTrue(valid_analysis(
            "作者提到元宝可能显示'未找到相关视频'，但后来他又详细介绍了"
            + "内容分析的七种方法。" * 25
        ))

    def test_sensevoice_launchd_config_is_explicit_and_opt_in(self):
        from server_docker.scripts.setup_host_transcriber import make_plist
        from server_docker.scripts.host_transcription_worker import default_transcriber
        fake_python = self.data / "python"
        fake_worker = self.data / "worker.py"
        model = self.data / "model"
        model.mkdir()
        (model / "model.int8.onnx").write_bytes(b"mock")
        (model / "tokens.txt").write_text("mock", encoding="utf8")
        fake_python.write_text("mock", encoding="utf8")
        with self.assertRaises(ValueError):
            make_plist(fake_python, fake_worker, self.data, model="sensevoice")
        config = make_plist(
            fake_python, fake_worker, self.data,
            model="sensevoice", sensevoice_python=fake_python,
            sensevoice_model_dir=model, fallback_base=True,
        )
        self.assertEqual(config["ProgramArguments"][-1], "sensevoice")
        env = config["EnvironmentVariables"]
        self.assertEqual(env["LOCAL_ASR_FALLBACK_BASE"], "false")
        self.assertEqual(env["LOCAL_ASR_SENSEVOICE_PYTHON"], str(fake_python))
        self.assertEqual(env["LOCAL_ASR_SENSEVOICE_MODEL_DIR"], str(model))
        self.assertNotIn("TOKEN", env)
        with patch.dict(os.environ, {"LOCAL_ASR_FALLBACK_BASE": "false"}):
            with patch(
                "server_docker.scripts.host_transcription_worker.sensevoice_transcriber",
                side_effect=RuntimeError("SENSEVOICE_TEST_FAILURE"),
            ):
                with self.assertRaisesRegex(RuntimeError, "SENSEVOICE_TEST_FAILURE"):
                    default_transcriber(self.data / "downloads" / "test.mp4", "sensevoice")

    def test_sensevoice_job_uses_isolated_staging_and_cleans_up(self):
        from server_docker.scripts.host_transcription_worker import sensevoice_transcriber
        from unittest.mock import Mock
        mp4 = self.data / "downloads" / "clip.mp4"
        mp4.write_bytes(b"immutable source")
        fakepython = self.data / "python"
        fakepython.write_bytes(b"fake python")
        fakepython.chmod(0o700)
        model = self.data / "model"
        model.mkdir()
        (model / "model.int8.onnx").write_bytes(b"model")
        (model / "tokens.txt").write_bytes(b"token symbols")
        before = mp4.read_bytes()

        def fake_ffmpeg(args, **_kwargs):
            dest = Path(args[-1])
            dest.write_bytes(b"a" * 4096)
            return Mock(returncode=0)

        def fake_popen(argv, **_kwargs):
            class FakeWorker:
                returncode = 0
                def communicate(self, data, timeout):
                    payload = json.loads(data)
                    out = Path(payload["output_dir"])
                    (out / "transcript.sensevoice.txt").write_text(
                        "[00:00:00.000 - 00:00:10.000] 这是一条用于验证离线语音识别的完整中文测试句子。",
                        encoding="utf8")
                    (out / "transcript.sensevoice.manifest.json").write_text(
                        json.dumps({"parts":1, "model":"sensevoice-int8-2024"}),encoding="utf8")
                    return json.dumps({"ok":True,"parts":1}), ""
            self.assertTrue(Path(argv[0]).is_file())
            self.assertTrue(Path(argv[1]).is_file())
            return FakeWorker()
        with patch.dict(os.environ, {
            "LOCAL_ASR_SENSEVOICE_PYTHON": str(fakepython),
            "LOCAL_ASR_SENSEVOICE_MODEL_DIR": str(model),
        }):
            with patch("server_docker.scripts.host_transcription_worker.subprocess.run", side_effect=fake_ffmpeg):
                with patch("server_docker.scripts.host_transcription_worker.subprocess.Popen", side_effect=fake_popen):
                    transcript, metrics = sensevoice_transcriber(mp4)
        self.assertIn("离线语音识别", transcript)
        self.assertEqual(metrics["segment_count"], 1)
        self.assertEqual(mp4.read_bytes(), before)
        self.assertEqual(list((self.data / "transcription_queue").iterdir()), [])

    def test_app_dispatches_configured_local_asr_model(self):
        from server_docker import app as appmod
        with patch.object(appmod, "LOCAL_TRANSCRIPTION_MODEL", "sensevoice"):
            with patch.object(appmod, "transcribe_with_host",
                              return_value=("test.transcript.txt", "中文原话")) as asr:
                name, text = appmod._local_transcribe("wxv_999999", self.data / "downloads" / "clip.mp4")
        self.assertEqual(name, "test.transcript.txt")
        self.assertEqual(text, "中文原话")
        self.assertEqual(asr.call_args.kwargs.get("model"), "sensevoice")

    def test_launchd_config_keeps_worker_local_and_private(self):
        from server_docker.scripts.setup_host_transcriber import make_plist
        fake_python = self.data / "python"
        fake_worker = self.data / "worker.py"
        model = self.data / "config-model"
        model.mkdir()
        (model / "model.int8.onnx").write_bytes(b"mock")
        (model / "tokens.txt").write_text("mock")
        fake_python.write_text("mock")
        plist = make_plist(fake_python, fake_worker, self.data,
                           sensevoice_python=fake_python, sensevoice_model_dir=model)
        self.assertTrue(plist["KeepAlive"])
        self.assertEqual(plist["ProgramArguments"][0], str(fake_python))
        self.assertNotIn("Sockets", plist)
        self.assertNotIn("NetworkState", plist)
        self.assertIn("/opt/homebrew/bin", plist["EnvironmentVariables"]["PATH"])

    def test_worker_transcribes_only_owned_media(self):
        mp4=(self.data/"downloads"/"clip.mp4")
        mp4.write_bytes(b"fake")
        request=self.data/"transcription_queue"/"wxv_000141.request.json"
        request.write_text(json.dumps({"job_id":"wxv_000141","video_filename":"clip.mp4","model":"sensevoice"}))
        calls=[]
        def fake_transcribe(path, model):
            calls.append((path.name,model))
            return "这是一段测试语音识别的中文逐字稿，详细记录了视频中的讲话内容。"
        self.assertTrue(run_once(self.data, fake_transcribe))
        self.assertEqual(calls,[("clip.mp4","sensevoice")])
        result=json.loads((self.data/"transcription_queue"/"wxv_000141.result.json").read_text())
        self.assertTrue(result["ok"])
        self.assertTrue((self.data/"downloads"/"wxv_000141.transcript.txt").is_file())
        self.assertFalse(request.exists())

    def test_path_traversal_and_symlink_rejected(self):
        for name in ("../outside.mp4","other.txt","/tmp/video.mp4"):
            with self.assertRaises(ValueError):
                resolve_video(self.data/"downloads",name)
        outside=self.data/"outside.mp4";outside.write_bytes(b"data")
        (self.data/"downloads"/"linked.mp4").symlink_to(outside)
        with self.assertRaises(ValueError):
            resolve_video(self.data/"downloads","linked.mp4")

    def test_queue_returns_transcript_and_uses_heartbeat(self):
        jobid="wxv_000142"
        mp4=self.data/"downloads"/"source.mp4";mp4.write_bytes(b"media")
        from server_docker.transcription_queue import _atomic_json
        queue=self.data/"transcription_queue"
        _atomic_json(queue/"worker.status.json",{"ready":True,"at":time.time(),"model":"base"})
        def runner():
            deadline=time.monotonic()+4
            req=queue/f"{jobid}.request.json"
            while not req.exists() and time.monotonic()<deadline: time.sleep(0.01)
            self.assertTrue(req.exists())
            (self.data/"downloads"/f"{jobid}.transcript.txt").write_text(
                "这段音频包含非常重要的几个观点和实际的详细说明。",encoding="utf-8"
            )
            _atomic_json(queue/f"{jobid}.result.json",{
                "ok":True,"job_id":jobid,"transcript_filename":f"{jobid}.transcript.txt"
            })
        thread=threading.Thread(target=runner);thread.start()
        try:
            filename,txt=transcribe_with_host(jobid,mp4,self.data,timeout_seconds=4,poll_seconds=.02)
            self.assertEqual(filename,f"{jobid}.transcript.txt")
            self.assertGreater(len(txt),20)
        finally:
            thread.join(timeout=5)

    def test_queue_exposes_real_worker_timing_and_accepts_legacy_missing(self):
        from server_docker.transcription_queue import _atomic_json
        from server_docker.transcription_queue import transcribe_with_host
        queue = self.data / "transcription_queue"
        video = self.data / "downloads/source.mp4"
        video.write_bytes(b"file")
        _atomic_json(queue/"worker.status.json", {"ready": True, "at": time.time(), "model": "sensevoice"})
        for ident, seconds, expected in [
            ("wxv_000144", 34.5, 34500),
            ("wxv_000145", None, None),
            ("wxv_000146", -12, None),
            ("wxv_000147", float("nan"), None),
        ]:
            def runner():
                req = queue / f"{ident}.request.json"
                deadline = time.monotonic()+3
                while not req.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(req.exists())
                name = f"{ident}.transcript.txt"
                (self.data / "downloads" / name).write_text(
                    "完整口播转写结果，真实中文视频内容能够顺利保存。", encoding="utf8")
                response = {"ok": True, "job_id": ident, "transcript_filename": name}
                if seconds is not None:
                    response["elapsed_seconds"] = seconds
                _atomic_json(queue / f"{ident}.result.json", response)
            thread = threading.Thread(target=runner)
            thread.start()
            try:
                name, contents, ms = transcribe_with_host(
                    ident, video, self.data, poll_seconds=.02,
                    timeout_seconds=4, include_elapsed_ms=True)
                self.assertEqual(ms, expected)
                self.assertEqual(name, f"{ident}.transcript.txt")
                self.assertTrue(contents)
            finally:
                thread.join(timeout=5)

    def test_worker_offline_fails_fast_without_writing_request(self):
        video=self.data/"downloads"/"clip.mp4";video.write_bytes(b"data")
        with self.assertRaises(TranscriptionError) as e:
            transcribe_with_host("wxv_000143",video,self.data,timeout_seconds=1)
        self.assertEqual(e.exception.code,"LOCAL_WORKER_OFFLINE")
        self.assertFalse((self.data/"transcription_queue"/"wxv_000143.request.json").exists())

class HybridRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.data=Path(self.temp.name)
        self.downloads=self.data/"downloads";self.downloads.mkdir()
        from server_docker.history_store import HistoryStore
        self.store=HistoryStore(self.data/"history.db")
        self.id="wxv_000151"
        self.store.create_job(id=self.id,input_url="https://weixin.qq.com/sph/test",
            prompt="提炼口播",preset_id=None,preset_name=None,output_format="md",
            download_requested=True,max_attempts=3)
        self.downloads_called=[]
    def tearDown(self):self.temp.cleanup()
    def analyzer(self,*args):
        return {"content":"抱歉，未找到相关视频",
          "previewUrl":"https://channels.weixin.qq.com/finder-preview/demo",
          "directUrl":"https://finder.video.qq.com/demo.mp4",
          "card":{"title":"长视频","coverUrl":""}}
    def downloader(self,stem,*args):
        self.downloads_called.append(stem)
        file=self.downloads/f"{stem}.mp4";file.write_bytes(b"valid-mp4")
        return file
    def runner(self,download=True,transcriber=None,text_analyzer=None,cached_video=None,cached_failed=None):
        from server_docker.job_runner import run_parse_job
        return run_parse_job(self.id,"https://weixin.qq.com/sph/test","提炼口播",
          None,"md",download,store=self.store,download_dir=self.downloads,
          analyzer=self.analyzer,downloader=self.downloader,transcriber=transcriber,
          text_analyzer=text_analyzer,cached_video_lookup=cached_video,
          cached_failed_analysis=cached_failed, sleeper=lambda _:None,thumbnailer=lambda *args:None)
    def test_partial_preserves_mp4_and_does_not_save_fake_md(self):
        row=self.runner()
        self.assertEqual(row["status"],"completed")
        self.assertEqual(row["analysis_status"],"unavailable")
        self.assertEqual(row["attempts_used"],1)
        self.assertIsNone(row["text_filename"])
        self.assertTrue((self.downloads/row["video_filename"]).exists())
    def test_md_only_success_uses_temp_video_and_removes_it(self):
        self.store.update_job(self.id,download_requested=False)
        def transcribe(jid,video):
            text="这是一条真实的中文口播内容，关于人口结构与青年婚育的影响。"
            filename=f"{jid}.transcript.txt"
            (self.downloads/filename).write_text(text,encoding="utf-8")
            return filename,text
        row=self.runner(download=False,transcriber=transcribe,
          text_analyzer=lambda text,prompt:"完整的AI分析：人口结构问题首先影响经济增长，随后影响社会信心。")
        self.assertEqual(row["status"],"completed")
        self.assertEqual(row["analysis_status"],"completed")
        self.assertEqual(row["analysis_source"],"local_transcript")
        self.assertIsNotNone(row["transcript_filename"])
        self.assertIsNotNone(row["text_filename"])
        self.assertIsNone(row["video_filename"])
        self.assertFalse(any(p.suffix==".mp4" for p in self.downloads.iterdir()))
    def test_known_failed_video_can_skip_second_yuanbao_video_request(self):
        cached=self.downloads/"old.mp4";cached.write_bytes(b"old-file")
        self.store.update_job(self.id,download_requested=False)
        def transcribe(job,video):
            name=f"{job}.transcript.txt"
            (self.downloads/name).write_text("完整中文口播内容，已经在本地转写完成，并保留为独立文件。",encoding="utf-8")
            return name,"完整中文口播内容，已经在本地转写完成，并保留为独立文件。"
        def analyzer_must_not_run(*args):
            raise AssertionError("Known unsupported video should reuse cached metadata")
        self.analyzer=analyzer_must_not_run
        old_analysis={
            "content":"抱歉，未找到相关视频",
            "previewUrl":"https://channels.weixin.qq.com/finder-preview/known",
            "directUrl":"https://finder.video.qq.com/known.mp4",
            "card":{"title":"旧长视频","coverUrl":""}
        }
        row=self.runner(download=False,transcriber=transcribe,
            text_analyzer=lambda *_:"经过全文梳理的AI分析完成，保留了用户要求的关键观点与证据。",
            cached_video=lambda url:cached,cached_failed=lambda url:old_analysis)
        self.assertEqual(row["status"],"completed")
        self.assertEqual(row["analysis_source"],"local_transcript")
        self.assertEqual(self.downloads_called,[])
        self.assertEqual(cached.read_bytes(),b"old-file")

    def test_existing_media_is_reused_without_redownload(self):
        cache=self.downloads/"old.mp4";cache.write_bytes(b"original")
        def transcribe(jid,video):
            name=f"{jid}.transcript.txt"
            (self.downloads/name).write_text("这是一段已经转写完成的中文口播，包含多个不同观点与证据。",encoding="utf-8")
            return name,"这是一段已经转写完成的中文口播，包含多个不同观点与证据。"
        row=self.runner(transcriber=transcribe,
          text_analyzer=lambda *args:"按照原 Prompt 提炼出成功的观点和完整的脚本结构。",
          cached_video=lambda url:cache)
        self.assertEqual(self.downloads_called,[])
        self.assertTrue((self.downloads/row["video_filename"]).exists())
        self.assertEqual(cache.read_bytes(),b"original")
    def test_transcription_time_persisted_separately_from_task_elapsed(self):
        def fake_asr(job_id, video_path):
            transcript_name = f"{job_id}.transcript.txt"
            (self.downloads / transcript_name).write_text(
                "这是一段未经人工润色的长视频口播文字，完整保留了识别文字。", encoding="utf8")
            return transcript_name, "真实口播全文，包含重要细节和视频观点。", 34500
        result = self.runner(
            transcriber=fake_asr,
            text_analyzer=lambda text, goal: "整理后的AI文字内容，保留主要事实和观点且具有完整结构。" * 3,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["transcription_elapsed_ms"], 34500)
        self.assertIsNotNone(result["elapsed_ms"])
        self.assertNotEqual(result["elapsed_ms"], 34500)

    def test_failed_transcription_preserves_video(self):
        def fail(jid,path):raise RuntimeError("failure")
        row=self.runner(transcriber=fail,text_analyzer=lambda *_:"unused")
        self.assertEqual(row["analysis_status"],"unavailable")
        self.assertIsNone(row["text_filename"])
        self.assertEqual(row["error_code"],"FALLBACK_FAILED")
        self.assertTrue((self.downloads/row["video_filename"]).exists())
    def test_no_video_and_unavailable_transcription_is_failed(self):
        self.store.update_job(self.id,download_requested=False)
        row=self.runner(download=False)
        self.assertEqual(row["status"],"failed")
        self.assertIsNone(row["text_filename"])

if __name__ == "__main__":
    unittest.main()
