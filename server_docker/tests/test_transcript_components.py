import tempfile
import unittest
from pathlib import Path
from server_docker.raw_transcript_export import write_raw_artifacts,readable_paragraphs
from server_docker.script_voice_export import write_script_voice,extract_spoken_script
from server_docker.task_modes import resolve_options

class TranscriptExportComponentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.words='\n'.join(f'[00:00:{n:02d}.000 - 00:00:{n+1:02d}.000] 第{n}句。' for n in range(10))
    def tearDown(self):self.tmp.cleanup()

    def test_only_timed_md_when_timing_selected(self):
        plain,timed=write_raw_artifacts(self.root,'wxv_000111',self.words,'md',True)
        self.assertIsNone(plain)
        self.assertEqual(timed,'wxv_000111.transcript-timed.md')
        self.assertEqual(list(self.root.glob('wxv_000111.transcript-raw.*')),[])
        self.assertIn('[00:00:01.000', (self.root/timed).read_text())

    def test_only_untimed_txt_when_timing_not_selected(self):
        plain,timed=write_raw_artifacts(self.root,'wxv_000112',self.words,'txt',False)
        self.assertIsNone(timed)
        self.assertTrue((self.root/plain).exists())
        self.assertEqual(list(self.root.glob('wxv_000112.transcript-timed.*')),[])
        self.assertNotIn('[00:00:00', (self.root/plain).read_text())

    def test_raw_paragraphs_preserve_all_recognized_characters(self):
        words=['我在第一个识别窗口认为','应该接下去持续听完这个句子。']*80
        got=readable_paragraphs(words)
        self.assertEqual(got.replace('\n',''),''.join(words))
        self.assertNotIn('认为\n应该',got)

    def test_timing_requires_real_stamps(self):
        with self.assertRaisesRegex(ValueError,'TIMESTAMPS_UNAVAILABLE'):
            write_raw_artifacts(self.root,'wxv_000113','没有时间窗口的内容','md',True)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_mode_choices_and_invalid_combinations(self):
        self.assertEqual(resolve_options({'task_mode':'video_only'})['raw'],False)
        self.assertTrue(resolve_options({'task_mode':'narration_only'})['raw'])
        with self.assertRaisesRegex(ValueError,'TIMELINE_REQUIRES_RAW_TRANSCRIPT'):
            resolve_options({'task_mode':'custom','download':False,'analyze':False,
                             'timed_transcript':True,'raw_transcript':False})

    def test_ai_script_keeps_numbered_sections_and_is_not_verbatim(self):
        source=('一、背景\n资料说明\n二、完整口播脚本（整理版）\n'
                '1. 演讲的第一段\n2. 演讲的第二段\n三、参考资料\n无关材料')
        excerpt,found=extract_spoken_script(source)
        self.assertTrue(found)
        self.assertIn('2. 演讲的第二段',excerpt)
        self.assertNotIn('无关材料',excerpt)
        name=write_script_voice(self.root,'wxv_000114','测试',source,output_format='md')
        self.assertIn('非音频逐字转写',(self.root/name).read_text())

if __name__=='__main__':unittest.main()
