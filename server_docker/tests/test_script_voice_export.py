import tempfile
import unittest
from pathlib import Path

from server_docker.script_voice_export import (extract_spoken_script,
                                               wants_script_export, write_script_voice)
from server_docker.history_store import HistoryStore
from server_docker.job_runner import run_parse_job


class ScriptVoiceExportTests(unittest.TestCase):
    def test_extract_numbered_spoken_section(self):
        text = ('一、背景信息\n无关内容\n二、完整口播脚本（整理版）\n'
                '开场：\n第一句。\n第二句。\n三、字幕脉络（去重后）\n不是旁白内容。')
        script, found = extract_spoken_script(text)
        self.assertTrue(found)
        self.assertIn('第一句', script)
        self.assertNotIn('字幕脉络', script)
        self.assertNotIn('无关内容', script)

    def test_ai_narration_preserves_internal_numbered_lists(self):
        text = ("一、分析\n摘要\n二、完整口播脚本（整理版）\n"
                "第 1/10 部分：\n1. 首个观点\n2. 第二观点\n"
                "第 2/10 部分：\n3. 另一观点\n三、结论\n非旁白结论")
        script, identified = extract_spoken_script(text)
        self.assertTrue(identified)
        self.assertIn('3. 另一观点', script)
        self.assertNotIn('非旁白结论', script)

    def test_prompt_without_preset_still_triggers_export(self):
        self.assertTrue(wants_script_export(None, '提炼视频完整口播脚本、字幕脉络'))
        self.assertTrue(wants_script_export({'id': 'script_copy'}, 'hello'))
        self.assertFalse(wants_script_export({'id': 'knowledge_tags'}, '总结要点'))

    def test_no_section_is_labelled_not_verbatim(self):
        with tempfile.TemporaryDirectory() as d:
            name = write_script_voice(Path(d), 'wxv_000101', '演示', '这里没有独立段落。')
            content = (Path(d) / name).read_text('utf-8')
            self.assertIn('完整性未经验证', content)
            self.assertIn('非逐字稿', content)
            with self.assertRaises(ValueError):
                write_script_voice(Path(d), '../secret', '演示', '内容')

    def test_successful_yuanbao_script_creates_download_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            store = HistoryStore(root / 'history.db')
            job_id = 'wxv_000101'
            prompt = '提炼视频的完整口播脚本、字幕脉络'
            store.create_job(id=job_id, input_url='https://weixin.qq.com/sph/demo',
                             prompt=prompt, preset_id='script_copy',
                             preset_name='脚本 / 字幕 / 文案提炼', output_format='md',
                             download_requested=False, max_attempts=3)
            result = run_parse_job(
                job_id, 'https://weixin.qq.com/sph/demo', prompt, {'id': 'script_copy'},
                'md', False, store=store, download_dir=root / 'downloads',
                analyzer=lambda *_: {
                    'content': '一、背景\n说明\n二、完整口播脚本（整理版）\n甲说的话。\n三、核心文案\n后续信息',
                    'previewUrl': 'https://channels.weixin.qq.com/finder-preview/demo',
                    'directUrl': 'https://finder.video.qq.com/demo.mp4',
                    'card': {'title': '演示'},
                },
                downloader=lambda *_: self.fail('MD-only should not download'),
                thumbnailer=lambda *_: None, sleeper=lambda _: None,
            )
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['transcript_filename'], job_id + '.narration-ai.md')
            text = (root / 'downloads' / result['transcript_filename']).read_text('utf-8')
            self.assertIn('AI 整理版', text)
            self.assertIn('甲说的话。', text)
            self.assertNotIn('后续信息', text)
            self.assertTrue((root / 'downloads' / result['text_filename']).exists())


if __name__ == '__main__': unittest.main()
