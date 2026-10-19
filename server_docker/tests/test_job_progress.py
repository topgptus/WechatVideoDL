import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_docker.job_progress import get_job_progress
from server_docker import app as appmod


class JobProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def sample(self, **kwargs):
        row = {'id':'wxv_000019','status':'running','stage':'yuanbao_video',
               'attempts_used':1,'max_attempts':3,'task_mode':'custom',
               'analyze_requested':0,'download_requested':0,
               'raw_requested':1,'timed_requested':1,
               'narration_requested':0,'transcript_filename':None,
               'timed_transcript_filename':None}
        row.update(kwargs)
        return row

    def test_only_timed_transcript_mode_has_no_video_or_ai_steps(self):
        p = get_job_progress(self.sample(stage='local_transcription'),self.root)
        self.assertEqual([s['key'] for s in p['steps']], ['source','media','asr','save'])
        self.assertEqual([s['state'] for s in p['steps']], ['complete','complete','active','waiting'])
        self.assertEqual(p['completed_steps'],2)
        self.assertEqual(p['current_step'],3)
        self.assertEqual(p['outputs'], [{'key':'asr','label':'时间轴逐字稿','ready':False}])
        self.assertNotIn('percent',p)

    def test_save_video_without_ai(self):
        p = get_job_progress(self.sample(stage='downloading_video',
            download_requested=1,raw_requested=0,timed_requested=0),self.root)
        self.assertEqual([s['key'] for s in p['steps']], ['source','media','save'])
        self.assertEqual(p['steps'][1]['state'],'active')
        self.assertEqual(p['outputs'][0]['label'],'MP4 视频')

    def test_queued_status_waits_without_fake_completion(self):
        p = get_job_progress(self.sample(attempts_used=0),self.root)
        self.assertEqual(p['completed_steps'],0)
        self.assertTrue(p['queued'])
        self.assertTrue(all(s['state']=='waiting' for s in p['steps']))

    def test_completed_timed_transcript_marks_asr_and_saved_steps(self):
        p = get_job_progress(self.sample(status='completed',stage='finished',
            timed_transcript_filename='wxv_000019.transcript-timed.md',
            video_title='Test Video'),self.root)
        self.assertEqual(p['completed_steps'],4)
        self.assertTrue(p['outputs'][0]['ready'])

    def test_unavailable_ai_does_not_show_complete(self):
        p=get_job_progress(self.sample(status='completed',stage='finished',
           analyze_requested=1,raw_requested=0,timed_requested=0,
           analysis_status='unavailable',video_title='Video',error_code='YUANBAO_VIDEO_NOT_READ'),self.root)
        self.assertEqual(next(s for s in p['steps'] if s['key']=='analysis')['state'],'failed')

    def test_checkpoint_reports_measured_windows_without_content(self):
        parts=self.root/'transcription_queue'/'.sensevoice-wxv_000019'/'output'/'sensevoice_parts'/'chunk_001'
        parts.mkdir(parents=True)
        (parts/'part_0001.json').write_text('{"text":"PRIVATE CONTENT"}')
        (parts/'part_0002.json').write_text('{"text":"PRIVATE CONTENT"}')
        (parts/'manifest.json').write_text('{"end_s":1800}')
        p=get_job_progress(self.sample(stage='local_transcription'),self.root)
        self.assertEqual(p['activity'],{'recognized_windows':2,'finished_groups':1})
        self.assertNotIn('PRIVATE',str(p))

    def test_ai_checkpoint_reports_counts_only(self):
        q=self.root/'transcription_queue'
        q.mkdir()
        (q/'.wxv_000019_ai_checkpoint.json').write_text(json.dumps({
            'notes':['SECRET PART ONE','SECRET PART TWO'],'total':10}))
        p=get_job_progress(self.sample(stage='yuanbao_transcript',
                            analyze_requested=1),self.root)
        self.assertEqual(p['activity'],{'ai_done':2,'ai_total':10})
        self.assertNotIn('SECRET',str(p))

    def test_detail_api_exposes_progress_only_for_authorized_users(self):
        from server_docker.history_store import HistoryStore
        store=HistoryStore(self.root/'history.db')
        store.create_job(id='wxv_000019',input_url='https://weixin.qq.com/sph/a',
            prompt=None,preset_id=None,preset_name=None,output_format='md',
            download_requested=False,max_attempts=3,owner_user_id='owner-a',
            raw_requested=True,timed_requested=True,analyze_requested=False)
        store.update_job('wxv_000019',stage='local_transcription',attempts_used=1)
        client=appmod.app.test_client()
        with patch.object(appmod,'current_user',return_value={'id':'owner-a','role':'member'}), \
             patch.object(appmod,'get_history_store',return_value=store), \
             patch.object(appmod,'DATA_DIR',self.root):
            response=client.get('/api/history/wxv_000019')
            self.assertEqual(response.status_code,200)
            progress=response.get_json()['job']['progress']
            self.assertEqual(progress['current_step'],3)
            self.assertEqual(progress['outputs'][0]['label'],'时间轴逐字稿')
        with patch.object(appmod,'current_user',return_value={'id':'owner-b','role':'member'}), \
             patch.object(appmod,'get_history_store',return_value=store):
            self.assertIn(client.get('/api/history/wxv_000019').status_code,(403,404))

    def test_progress_ui_contract(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/'templates'/'index.html').read_text('utf8')
        js=(root/'static'/'app.js').read_text('utf8')
        for control in ('progress-steps','progress-summary','progress-measured','progress-outputs',
                        'result-workflow','result-workflow-steps'):
            self.assertIn(f'id="{control}"',html)
        self.assertIn('renderJobProgress(data);',js)
        self.assertIn('data.progress',js) if 'data.progress' in js else self.assertIn('data?.progress',js)
        self.assertIn('当前：',js)

if __name__=='__main__':unittest.main()
