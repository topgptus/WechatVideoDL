"""Task-aware progress independent of UI, HTTP routes and private files."""
import json
import tempfile
import unittest
from pathlib import Path
from server_docker.job_progress import get_job_progress

class JobProgressModelTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.data=Path(self.temp.name)
        self.job={'id':'wxv_000019','status':'running','stage':'local_transcription',
                  'attempts_used':1,'max_attempts':3,'download_requested':False,
                  'raw_requested':True,'timed_requested':True,
                  'analyze_requested':False,'narration_requested':False}
    def tearDown(self):self.temp.cleanup()

    def test_timed_only_has_true_steps_and_no_fake_percentage(self):
        p=get_job_progress(self.job,self.data)
        self.assertEqual([v['key'] for v in p['steps']],['source','media','asr','save'])
        self.assertEqual([v['state'] for v in p['steps']],['complete','complete','active','waiting'])
        self.assertEqual(p['current_step'],3)
        self.assertNotIn('percent',p)
        self.assertEqual([x['label'] for x in p['outputs']],['时间轴逐字稿'])

    def test_queued_jobs_not_prematurely_complete(self):
        p=get_job_progress({**self.job,'attempts_used':0},self.data)
        self.assertTrue(p['queued'])
        self.assertEqual(p['completed_steps'],0)

    def test_progress_checkpoint_reports_counts_only(self):
        root=self.data/'transcription_queue'/'.sensevoice-wxv_000019'/'output'/'sensevoice_parts'/'chunk_001'
        root.mkdir(parents=True)
        (root/'part_0001.json').write_text('{"text":"SECRET"}')
        (root/'manifest.json').write_text('{"duration":1800}')
        progress=get_job_progress(self.job,self.data)
        self.assertEqual(progress['activity'],{'recognized_windows':1,'finished_groups':1})
        self.assertNotIn('SECRET',str(progress))

    def test_completed_timed_export_is_4_of_4(self):
        p=get_job_progress({**self.job,'status':'completed','stage':'finished',
            'timed_transcript_filename':'wxv_000019.transcript-timed.md','video_title':'任务'},self.data)
        self.assertEqual(p['completed_steps'],4)

    def test_error_attached_to_actual_stage(self):
        j={**self.job,'status':'failed','stage':'finished','error_code':'SENSEVOICE_TIMEOUT',
           'retry_errors':[{'stage':'local_transcription','attempt':1,'code':'SENSEVOICE_TIMEOUT'}]}
        p=get_job_progress(j,self.data)
        asr=next(x for x in p['steps'] if x['key']=='asr')
        self.assertEqual(asr['state'],'failed')
        self.assertIn('SENSEVOICE_TIMEOUT',' '.join(asr['issues']))

if __name__=='__main__':unittest.main()
