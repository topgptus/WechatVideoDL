import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from server_docker.task_modes import resolve_options
from server_docker.raw_transcript_export import write_raw_artifacts
from server_docker.history_store import HistoryStore
from server_docker.job_runner import run_parse_job
from server_docker import app as appmod


class TaskModesAndBulkTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.output = self.root / 'downloads'
        self.output.mkdir()
        self.store = HistoryStore(self.root / 'history.db')

    def tearDown(self): self.folder.cleanup()

    def job(self, jid, opts, owner='a'):
        return self.store.create_job(
            id=jid, input_url='https://weixin.qq.com/sph/test', prompt='提炼口播脚本',
            preset_id=None,preset_name=None, output_format='md',
            download_requested=opts['download'],max_attempts=3, owner_user_id=owner,
            task_mode=opts['task_mode'],analyze_requested=opts['analyze'],
            narration_requested=opts['narration'],raw_requested=opts['raw'],
            timed_requested=opts['timed'],
        )

    @staticmethod
    def analyze(*args):
        return {'content':'一、背景\n开头\n二、完整口播脚本（整理版）\n原话信息。\n三、其他分析\n观点',
                'previewUrl':'https://channels.weixin.qq.com/finder-preview/test',
                'directUrl':'https://finder.video.qq.com/test.mp4',
                'card':{'title':'测试视频','coverUrl':''}}

    def download(self,stem,*_):
        p = self.output / (stem+'.mp4');p.write_bytes(b'video');return p

    def transcribe(self,jid,p):
        scratch=self.output / (jid+'.transcript.txt')
        txt='[00:00:00.000 - 00:00:19.000] 这是纯语音识别原始内容\n[00:00:19.000 - 00:00:38.000] 没有经过任何AI文案改写'
        scratch.write_text(txt)
        return scratch.name, txt, 1234

    def execute_task(self,jid,opts):
        return run_parse_job(jid,'https://weixin.qq.com/sph/test','提炼口播脚本',None,'md',
            opts['download'],store=self.store,download_dir=self.output,analyzer=self.analyze,
            downloader=self.download,thumbnailer=lambda *_: None,sleeper=lambda *_:None,
            transcriber=self.transcribe,options=opts)

    def test_only_download_has_no_analysis(self):
        options=resolve_options({'task_mode':'video_only'})
        self.job('wxv_000701',options)
        result=self.execute_task('wxv_000701',options)
        self.assertEqual(result['status'],'completed')
        self.assertTrue(result['video_filename'])
        self.assertIsNone(result['text_filename'])
        self.assertIsNone(result['raw_transcript_filename'])
        self.assertEqual(result['analysis_status'],'skipped')

    def test_only_raw_with_timing_without_saved_mp4(self):
        options=resolve_options({'task_mode':'narration_only','timed_transcript':True})
        self.job('wxv_000702',options)
        result=self.execute_task('wxv_000702',options)
        self.assertEqual(result['status'],'completed')
        self.assertIsNone(result['video_filename'])
        self.assertFalse(list(self.output.glob('*.mp4')))
        self.assertIsNone(result['raw_transcript_filename'])
        self.assertFalse((self.output/'wxv_000702.transcript-raw.md').exists())
        self.assertTrue((self.output/result['timed_transcript_filename']).is_file())
        self.assertIn('00:00:19', (self.output/result['timed_transcript_filename']).read_text())

    def test_full_mode_has_four_output_types(self):
        options=resolve_options({'task_mode':'full','timed_transcript':True})
        self.job('wxv_000703',options)
        result=self.execute_task('wxv_000703',options)
        self.assertTrue(result['video_filename'])
        self.assertTrue(result['text_filename'].endswith('.md'))
        self.assertTrue(result['transcript_filename'].endswith('.narration-ai.md'))
        self.assertIsNone(result['raw_transcript_filename'])
        self.assertFalse((self.output/'wxv_000703.transcript-raw.md').exists())
        self.assertTrue(result['timed_transcript_filename'].endswith('.transcript-timed.md'))

    def test_plain_asr_without_timing_produces_only_raw_md(self):
        options=resolve_options({'task_mode':'narration_only','timed_transcript':False})
        self.job('wxv_000705',options)
        result=self.execute_task('wxv_000705',options)
        self.assertEqual(result['status'],'completed')
        self.assertIsNone(result['timed_transcript_filename'])
        self.assertTrue((self.output/result['raw_transcript_filename']).is_file())
        self.assertFalse((self.output/'wxv_000705.transcript-timed.md').exists())

    def test_raw_export_exactly_one_file_in_txt_mode(self):
        payload=self.transcribe('wxv_000706',self.output/'video.mp4')[1]
        raw,timed=write_raw_artifacts(self.output,'wxv_000706',payload,'txt',True)
        self.assertIsNone(raw)
        self.assertTrue(timed.endswith('.txt'))
        self.assertEqual(list(self.output.glob('wxv_000706.transcript-raw.*')),[])
        raw,timed=write_raw_artifacts(self.output,'wxv_000707',payload,'txt',False)
        self.assertIsNone(timed)
        self.assertTrue(raw.endswith('.txt'))
        self.assertEqual(list(self.output.glob('wxv_000707.transcript-timed.*')),[])

    def test_invalid_child_option_without_parent(self):
        with self.assertRaisesRegex(ValueError, 'TIMELINE_REQUIRES_RAW_TRANSCRIPT'):
            resolve_options({'task_mode':'custom','download':False,'analyze':False,
                             'raw_transcript':False,'timed_transcript':True})

    def test_legacy_dual_asr_bundle_contains_only_timed_version(self):
        from server_docker.bundle_service import ensure_bundle
        import zipfile
        self.output.joinpath('wxv_000708.transcript-raw.md').write_text('plain')
        self.output.joinpath('wxv_000708.transcript-timed.md').write_text('timed')
        bundle=ensure_bundle(self.output, {
            'id':'wxv_000708', 'timed_requested':1,
            'raw_transcript_filename':'wxv_000708.transcript-raw.md',
            'timed_transcript_filename':'wxv_000708.transcript-timed.md',
        })
        with zipfile.ZipFile(bundle) as archive:
            names=archive.namelist()
            self.assertEqual(len(names), 1)
            self.assertIn('transcript-timed.md',names[0])

    def test_yuanbao_refusal_with_timed_asr_does_not_run_asr_twice(self):
        opts=resolve_options({'task_mode':'custom','download':False,'analyze':True,
                              'narration':True,'raw_transcript':True,
                              'timed_transcript':True})
        self.job('wxv_000709',opts)
        calls=[]
        def recognize(job_id, path):
            calls.append(job_id)
            return self.transcribe(job_id,path)
        def refused(*_):
            data=self.analyze()
            data['content']='抱歉，无法解析此视频，请提供完整逐字稿。'
            return data
        result=run_parse_job('wxv_000709','https://weixin.qq.com/sph/test',
            '整理完整口播脚本',None,'md',False,
            store=self.store,download_dir=self.output,analyzer=refused,
            downloader=self.download,thumbnailer=lambda *_:None,sleeper=lambda *_:None,
            transcriber=recognize,text_analyzer=lambda *_:
                '一、完整口播脚本（整理版）\n已根据原始音轨生成可靠逐字稿。\n'
                '二、分析\n依据原始识别结果给出内容整理。',
            options=opts)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(calls,['wxv_000709'])
        self.assertIsNone(result['raw_transcript_filename'])
        self.assertTrue((self.output/result['timed_transcript_filename']).is_file())
        self.assertTrue(result['text_filename'].endswith('.md'))
        self.assertTrue(result['transcript_filename'].endswith('.narration-ai.md'))
        self.assertFalse((self.output/'wxv_000709.transcript-raw.md').exists())

    def test_custom_invalid_modes(self):
        for data in ({'task_mode':'custom','download':False,'analyze':False},
                     {'task_mode':'unknown'},
                     {'task_mode':'custom','download':False,'analyze':False,'narration':True}):
            with self.assertRaises(ValueError): resolve_options(data)

    def test_timestamp_never_fabricated(self):
        with self.assertRaises(ValueError):
            write_raw_artifacts(self.output,'wxv_000704','没有时间信息','md',True)
        self.assertFalse((self.output/'wxv_000704.transcript-timed.md').exists())

    def test_bulk_delete_respects_owner_and_preserves_other_files(self):
        opts=resolve_options({'task_mode':'video_only'})
        self.job('wxv_000801',opts,owner='owner-a')
        self.job('wxv_000802',opts,owner='owner-b')
        shared=self.output/'shared.mp4';shared.write_bytes(b'video')
        self.store.update_job('wxv_000801',status='completed',video_filename=shared.name)
        self.store.update_job('wxv_000802',status='completed',video_filename=shared.name)
        client=appmod.app.test_client()
        with client.session_transaction() as sess:
            sess['csrf'] = 'batch-test-token'
        headers={'X-CSRF-Token':'batch-test-token'}
        member={'id':'owner-a','username':'a','role':'member'}
        with patch.object(appmod,'current_user',return_value=member),\
             patch.object(appmod,'get_history_store',return_value=self.store),\
             patch.object(appmod,'DOWNLOAD_DIR',self.output):
            forbidden=client.post('/api/history/bulk-delete',json={'ids':['wxv_000801','wxv_000802']},headers=headers)
            self.assertEqual(forbidden.status_code,403)
            self.assertIsNotNone(self.store.get_job('wxv_000801'))
            ok=client.post('/api/history/bulk-delete',json={'ids':['wxv_000801']},headers=headers)
            self.assertEqual(ok.status_code,200)
            self.assertTrue(shared.exists())
            self.assertIsNotNone(self.store.get_job('wxv_000802'))

if __name__ == '__main__': unittest.main()
