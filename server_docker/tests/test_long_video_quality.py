"""Pure text analysis components; independent of optional ASR installation."""
import json
import tempfile
import unittest
from pathlib import Path
from server_docker.content_quality import refusal_reason
from server_docker.long_video_analysis import split_asr,collect_chunks,assemble_grounded_analysis

class LongVideoQualityTests(unittest.TestCase):
    def test_long_general_answer_cannot_disguise_refusal(self):
        response=('我这边尝试读取你给的链接时，视频解析失败，'
                  '公开检索也没有拿到这个视频的字幕内容。因此先做一份兜底模板。'
                  + '模板说明。'*70)
        self.assertEqual(refusal_reason(response),'YUANBAO_VIDEO_NOT_READ')
        self.assertIsNone(refusal_reason('这段演讲中引用了某平台视频解析失败的历史案例。'*30))

    def test_source_windows_preserved_after_partition(self):
        line='[00:00:12.000 - 00:00:31.000] 一段原始字幕。'
        source='\n'.join([line]*565)
        self.assertEqual('\n'.join(split_asr(source, max_chars=400)),source)

    def test_checkpoint_resume_and_full_edited_narration(self):
        source='\n'.join(f'[00:00:{i:02}.000 - 00:01:{i:02}.000] 第{i}段' for i in range(43))
        calls=[]
        def ask(text,instruction):
            calls.append(text)
            return ('【本段要点】实际录音提到相关信息。'*5+
                    '【本段口播整理】保留有依据的表达。'*6)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'check.json'
            result=collect_chunks(source,'请整理',ask,checkpoint=path)
            self.assertEqual(result,collect_chunks(source,'请整理',ask,checkpoint=path))
            self.assertEqual(len(calls),result['chunk_count'])
            analysis,narration=assemble_grounded_analysis(result,'请整理',lambda *_:'真实内容归纳总结。'*23)
            self.assertIn('SenseVoice',analysis)
            self.assertEqual(narration.count('部分：'),result['chunk_count'])
            self.assertIn('完整口播脚本',narration)
            self.assertNotIn('未经修改的逐字稿',narration)

    def test_changed_source_rejects_checkpoint_reuse(self):
        text='第一句话。第二句话。'
        with tempfile.TemporaryDirectory() as folder:
            cp=Path(folder)/'session.json'
            collect_chunks(text,'任务1',lambda *_:'【本段要点】真实语音。'*7+'【本段口播整理】文字表达。'*7,checkpoint=cp)
            with self.assertRaisesRegex(ValueError,'AI_CHECKPOINT_SOURCE_MISMATCH'):
                collect_chunks(text,'任务2',lambda *_:'',checkpoint=cp)

if __name__=='__main__':unittest.main()
