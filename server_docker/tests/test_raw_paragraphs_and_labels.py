"""ASR window reflow must not rewrite recognized speech or confuse artifact labels."""
import unittest
from unittest.mock import patch
from server_docker.raw_transcript_export import STAMP, readable_paragraphs
from server_docker import app as appmod


class ReadableRawTranscriptTests(unittest.TestCase):
    def test_reflow_joins_windows_without_changing_words(self):
        parts = ['领导面对困难时，我认', '为应该先解决实际问题。',
                 '接下来我们说明研究方法，', '包括原始材料的核对和归档。'] * 40
        result=readable_paragraphs(parts)
        self.assertEqual(result.replace('\n', ''), ''.join(parts))
        self.assertNotIn('我认\n为', result)
        self.assertGreater(result.count('\n\n'), 0)

    def test_history_ui_uses_windows_not_semantic_paragraphs(self):
        from pathlib import Path
        ui=(Path(__file__).resolve().parents[1]/'static/app.js').read_text('utf8')
        self.assertIn('识别 ${job.transcription_segment_count} 个窗口',ui)
        self.assertIn('AI整理旁白',ui)
        self.assertIn('时间轴逐字稿',ui)

    def test_timing_option_is_child_and_one_output_only(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        html=(root/'templates/index.html').read_text('utf-8')
        js=(root/'static/app.js').read_text('utf-8')
        self.assertIn('class="transcript-output-group"',html)
        self.assertIn('id="timed-option"',html)
        self.assertIn('timedWrap.classList.toggle("hidden", !flags[3].checked)',js)
        self.assertIn('timedWrap:$("timed-option")',js)
        self.assertIn('只输出一份「时间轴逐字稿」',js)
        self.assertNotIn('if (timedToggle.checked) rawToggle.checked = true',js)

    def test_timecode_accepts_exported_en_dash(self):
        self.assertTrue(STAMP.fullmatch('[02:58:24.000–02:58:43.200] 最后一段原话'))
        self.assertTrue(STAMP.fullmatch('[02:58:24.000 - 02:58:43.200] 最后一段原话'))

    def test_ai_narration_does_not_inherit_asr_label(self):
        job = {'id':'wxv_000018', 'status':'completed','video_title':'Test',
               'output_format':'md','transcription_elapsed_ms':320000,
               'analysis_source':'local_transcript','analysis_status':'completed',
               'transcript_filename':'wxv_000018.narration-ai.md',
               'raw_transcript_filename':'wxv_000018.transcript-raw.md',
               'timed_transcript_filename':'wxv_000018.transcript-timed.md'}
        with appmod.app.test_request_context('/history'):
            data=appmod.serialize_job(job)
        self.assertEqual(data['transcript_kind'],'ai_edited')

    def test_old_timed_task_hides_duplicate_raw_link(self):
        old={'id':'wxv_000018','status':'completed','timed_requested':1,
             'transcript_filename':'wxv_000018.narration-ai.md',
             'raw_transcript_filename':'wxv_000018.transcript-raw.md',
             'timed_transcript_filename':'wxv_000018.transcript-timed.md'}
        with appmod.app.test_request_context('/history'):
            data=appmod.serialize_job(old)
        self.assertIsNone(data['raw_transcript']['download_path'])
        self.assertIsNotNone(data['timed_transcript']['download_path'])
        self.assertIsNotNone(data['transcript']['download_path'])

    def test_legacy_transcript_recognized_by_filename(self):
        job = {'id':'wxv_000027', 'status':'completed', 'output_format':'txt',
               'transcript_filename':'wxv_000027.transcript.txt'}
        with appmod.app.test_request_context('/history'):
            data=appmod.serialize_job(job)
        self.assertEqual(data['transcript_kind'],'raw_asr')

if __name__=='__main__': unittest.main()
