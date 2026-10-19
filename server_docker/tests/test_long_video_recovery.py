import tempfile
import unittest
from pathlib import Path
from server_docker.content_quality import refusal_reason
from server_docker.long_video_analysis import split_asr, collect_chunks, assemble_grounded_analysis
from server_docker.scripts.sensevoice_engine import MAX_AUDIO_S, MAX_PARTS, CHUNK_S


class LongVideoRecoveryTests(unittest.TestCase):
    def test_long_failure_is_not_success(self):
        text=('我这边尝试读取你给的微信视频号链接时，视频解析失败，'
              '公开检索也没有拿到这条视频的标题、作者、字幕或口播内容。'
              '所以下面先按视频不可读做一份兜底知识笔记。'+'资料占位。'*80)
        self.assertEqual(refusal_reason(text),'YUANBAO_VIDEO_NOT_READ')
        self.assertIsNone(refusal_reason('这是视频里的嘉宾解释为什么曾经有人说过视频解析失败，但随后讨论了政策背景。'*10))

    def test_limits_cover_three_hour_audio(self):
        self.assertGreaterEqual(MAX_AUDIO_S,10800)
        self.assertGreaterEqual(MAX_PARTS,570)
        self.assertEqual(CHUNK_S,1800)

    def test_splitting_retains_every_line_and_timing(self):
        line='[00:00:12.000 - 00:00:31.000] 完整句子。'
        text='\n'.join(line for _ in range(565))
        chunks=split_asr(text, max_chars=400)
        self.assertGreater(len(chunks),1)
        self.assertEqual('\n'.join(chunks),text)

    def test_checkpoint_resumes_without_reasking_completed_parts(self):
        transcript='\n'.join(f'[00:00:{i:02}.000 - 00:01:{i:02}.000] 第{i}段语音信息' for i in range(42))
        seen=[]
        def ask(text, instruction):
            seen.append(text)
            return '【本段要点】本段是实际语音信息，关键内容来自原话。'*2+'【本段口播整理】根据原话梳理的中文语音信息。'*2
        with tempfile.TemporaryDirectory() as folder:
            cache=Path(folder)/'check.json'
            first=collect_chunks(transcript,'按段总结',ask,checkpoint=cache)
            expected=first['chunk_count']
            self.assertEqual(len(seen),expected)
            second=collect_chunks(transcript,'按段总结',ask,checkpoint=cache)
            self.assertEqual(len(seen),expected)
            self.assertEqual(first,second)
            analysis,narration=assemble_grounded_analysis(first,'按段总结',lambda *_:'综合归纳'*40)
            self.assertIn('基于本地语音识别',analysis)
            self.assertIn('完整口播脚本（整理版）',narration)
            self.assertNotIn('逐字稿（未修改）',narration)

if __name__=='__main__':unittest.main()
