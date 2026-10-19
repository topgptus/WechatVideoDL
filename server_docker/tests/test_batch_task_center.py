"""Batch queue: persistent intake, owner isolation and safe pending-only controls."""
import tempfile
import threading
import time
import io
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from server_docker import app as a
from server_docker.history_store import HistoryStore
from server_docker.batch_input import collect_video_urls


class PendingFuture:
    def __init__(self):
        self.cancelled = False
    def cancel(self):
        if self.cancelled:return False
        self.cancelled = True
        return True
    def done(self):
        return self.cancelled


class HoldingExecutor:
    def __init__(self):
        self.submitted = []
    def submit(self, fn, *args):
        result=PendingFuture()
        self.submitted.append((fn,args,result))
        return result


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = HistoryStore(self.root/'history.db')
        self.executor = HoldingExecutor()
        self.slots=threading.BoundedSemaphore(2)
        self.user={'id':'owner-01','role':'member','username':'user'}
        self.patches = [
            patch.object(a,'get_history_store',return_value=self.store),
            patch.object(a,'JOB_EXECUTOR',self.executor),
            patch.object(a,'JOB_SLOTS',self.slots),
            patch.object(a,'DATA_DIR',self.root),
            patch.object(a,'DOWNLOAD_DIR',self.root/'downloads'),
            patch.object(a,'SEQUENCE_FILE',self.root/'sequence.txt'),
            patch.object(a,'BATCH_FUTURES',{}),
            patch.object(a,'current_user',return_value=self.user),
            patch.object(a,'csrf_valid',return_value=True),
            patch.object(a,'LOCAL_TRANSCRIPTION_ENABLED',True),
        ]
        for patcher in self.patches:patcher.start()
        self.client=a.app.test_client()

    def tearDown(self):
        for patcher in reversed(self.patches):patcher.stop()
        self.tmp.cleanup()

    def url(self,n):return f'https://weixin.qq.com/sph/TEST{n:04d}'
    def create(self,n=5):
        return self.client.post('/api/batches',json={'urls':[self.url(i) for i in range(n)],
            'task_mode':'video_only','output_format':'md'})

    def test_pasted_share_text_removes_duplicates_without_losing_order(self):
        links, duplicates=collect_video_urls('转发朋友：\n'+self.url(1)+'，\n'+self.url(2)+'\n'+self.url(1))
        self.assertEqual(links,[self.url(1),self.url(2)])
        self.assertEqual(duplicates,1)
        with self.assertRaisesRegex(ValueError,'INVALID_BATCH_URL'):
            collect_video_urls([self.url(1),'https://attacker.example/video'])
        with self.assertRaisesRegex(ValueError,'BATCH_TOO_LARGE'):
            collect_video_urls([self.url(i) for i in range(31)])

    def test_batch_queue_limits_executor_reservations_but_stores_all_jobs(self):
        response=self.create(5)
        self.assertEqual(response.status_code,202,response.get_json())
        batch=response.get_json()['batch']
        self.assertEqual(batch['total'],5)
        self.assertEqual(batch['counts']['queued'],5)
        self.assertEqual(len(self.executor.submitted),2)
        rows=self.store.batch_jobs(batch['id'])
        self.assertEqual([r['queue_state'] for r in rows],['scheduled','scheduled','waiting','waiting','waiting'])
        self.assertEqual(len({r['id'] for r in rows}),5)

    def test_pause_pending_then_resume_only_two_slots(self):
        batch=self.create(5).get_json()['batch']
        base='/api/batches/'+batch['id']
        r=self.client.post(base+'/action',json={'action':'pause'})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.get_json()['changed'],5)
        self.assertTrue(all(x['queue_state']=='paused' for x in self.store.batch_jobs(batch['id'])))
        self.assertEqual(a._dispatch_batch_waiting(),0)
        r=self.client.post(base+'/action',json={'action':'resume'})
        self.assertEqual(r.get_json()['changed'],5)
        self.assertEqual(len(self.executor.submitted),4)
        self.assertEqual([x['queue_state'] for x in self.store.batch_jobs(batch['id'])].count('scheduled'),2)

    def test_cancel_pending_keeps_no_queued_submissions_and_can_not_resume(self):
        batch=self.create(3).get_json()['batch']
        base='/api/batches/'+batch['id']
        self.client.post(base+'/action',json={'action':'pause'})
        r=self.client.post(base+'/action',json={'action':'cancel'})
        self.assertEqual(r.get_json()['changed'],3)
        rows=self.store.batch_jobs(batch['id'])
        self.assertTrue(all(x['error_code']=='BATCH_CANCELLED' for x in rows))
        self.assertEqual(self.client.post(base+'/action',json={'action':'resume'}).get_json()['changed'],0)
        self.assertEqual(a._dispatch_batch_waiting(),0)

    def test_cross_user_visibility_and_actions_rejected(self):
        batch=self.create(3).get_json()['batch']
        with patch.object(a,'current_user',return_value={'id':'outsider','role':'member'}):
            self.assertEqual(self.client.get('/api/batches/'+batch['id']).status_code,404)
            self.assertEqual(self.client.post('/api/batches/'+batch['id']+'/action',json={'action':'pause'}).status_code,404)
            self.assertEqual(self.client.get('/api/batches').get_json()['batches'],[])
        self.assertEqual(self.store.batch_jobs(batch['id'])[0]['queue_state'],'scheduled')

    def test_running_task_is_not_interrupted_by_batch_pause(self):
        batch=self.create(3).get_json()['batch']
        rows=self.store.batch_jobs(batch['id'])
        self.store.update_job(rows[0]['id'],queue_state='active',attempts_used=1)
        response=self.client.post('/api/batches/'+batch['id']+'/action',
                                  json={'action':'pause'}).get_json()
        self.assertEqual(response['active_uninterrupted'],1)
        self.assertEqual(response['changed'],2)
        self.assertEqual(self.store.get_job(rows[0]['id'])['queue_state'],'active')

    def test_real_worker_completion_dispatches_next_waiting_task(self):
        # No network, browser session, media file or Yuanbao login is used.
        completed=[]
        def fake_processing(jid,*args,**kwargs):
            completed.append(jid)
            self.store.update_job(jid,status='completed',stage='finished',
                                  attempts_used=1)
        with ThreadPoolExecutor(max_workers=1) as executor, \
             patch.object(a,'JOB_EXECUTOR',executor), \
             patch.object(a,'JOB_SLOTS',threading.BoundedSemaphore(1)), \
             patch.object(a,'run_parse_job',side_effect=fake_processing), \
             patch.object(a,'BATCH_FUTURES',{}):
            batch=self.create(4).get_json()['batch']
            end=time.monotonic()+8
            while time.monotonic()<end:
                rows=self.store.batch_jobs(batch['id'])
                if all(row['status']=='completed' and row['queue_state']=='finished'
                       for row in rows):
                    break
                time.sleep(.04)
            self.assertEqual(len(completed),4)
            self.assertEqual(len(set(completed)),4)
            self.assertTrue(all(row['status']=='completed' for row in rows))

    def test_retries_only_failed_without_existing_artifacts(self):
        batch=self.create(3).get_json()['batch']
        self.client.post('/api/batches/'+batch['id']+'/action',json={'action':'pause'})
        rows=self.store.batch_jobs(batch['id'])
        self.store.update_job(rows[0]['id'],status='failed',queue_state='finished',error_code='YUANBAO_TIMEOUT')
        self.store.update_job(rows[1]['id'],status='failed',queue_state='finished',video_filename='existing.mp4')
        result=self.client.post('/api/batches/'+batch['id']+'/action',json={'action':'retry'}).get_json()
        self.assertEqual(result['changed'],1)
        self.assertEqual(self.store.get_job(rows[0]['id'])['status'],'running')
        self.assertEqual(self.store.get_job(rows[1]['id'])['status'],'failed')

    def test_restart_reconciles_active_but_preserves_paused_and_waiting(self):
        batch=self.create(5).get_json()['batch']
        rows=self.store.batch_jobs(batch['id'])
        self.store.update_job(rows[0]['id'],queue_state='active')
        self.store.update_job(rows[1]['id'],queue_state='paused')
        HistoryStore(self.root/'history.db')
        states={r['id']:(r['status'],r['queue_state']) for r in self.store.batch_jobs(batch['id'])}
        self.assertEqual(states[rows[0]['id']],('failed','finished'))
        self.assertEqual(states[rows[1]['id']],('running','paused'))
        self.assertEqual(states[rows[3]['id']],('running','waiting'))

    def test_invalid_options_never_create_partial_records(self):
        resp=self.client.post('/api/batches',json={'urls':[self.url(1)],'task_mode':'custom',
            'download':False,'analyze':False,'raw_transcript':False})
        self.assertEqual(resp.status_code,400)
        self.assertEqual(self.store.list_jobs(),[])

    def test_text_zip_exports_owned_results_without_video_or_private_files(self):
        batch=self.create(2).get_json()['batch']
        rows=self.store.batch_jobs(batch['id'])
        downloads=self.root/'downloads'
        downloads.mkdir(exist_ok=True)
        transcript=rows[0]['id']+'.transcript-raw.md'
        (downloads/transcript).write_text('用于测试的语音识别结果',encoding='utf8')
        (downloads/(rows[0]['id']+'.mp4')).write_bytes(b'not to be bundled')
        self.store.update_job(rows[0]['id'],status='completed',queue_state='finished',
                              raw_transcript_filename=transcript,
                              video_filename=rows[0]['id']+'.mp4')
        response=self.client.get('/api/batches/'+batch['id']+'/texts.zip')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.mimetype,'application/zip')
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            self.assertEqual(archive.namelist(),[f"{rows[0]['id']}/{transcript}"])
            self.assertIn('语音识别',archive.read(archive.namelist()[0]).decode('utf8'))
        with patch.object(a,'current_user',return_value={'id':'other','role':'member'}):
            self.assertEqual(self.client.get('/api/batches/'+batch['id']+'/texts.zip').status_code,404)

    def test_all_zip_includes_video_analysis_transcripts_thumbnail_by_job(self):
        batch=self.create(2).get_json()['batch']
        rows=self.store.batch_jobs(batch['id'])
        folder=self.root/'downloads'
        folder.mkdir(exist_ok=True)
        first,second=rows
        media=f"{first['id']}.mp4"
        analysis=f"{first['id']}.md"
        raw=f"{first['id']}.transcript-raw.md"
        thumb=f"{first['id']}.jpg"
        other=f"{second['id']}.mp4"
        for filename,content in ((media,b'video bytes'),(analysis,b'analysis'),
                                 (raw,b'raw text'),(thumb,b'jpg'),
                                 (other,b'second video')):
            (folder/filename).write_bytes(content)
        self.store.update_job(first['id'],status='completed',queue_state='finished',
                              video_filename=media,text_filename=analysis,
                              raw_transcript_filename=raw,thumbnail_filename=thumb)
        self.store.update_job(second['id'],status='running',queue_state='waiting',
                              video_filename=other)
        response=self.client.get('/api/batches/'+batch['id']+'/all.zip')
        self.assertEqual(response.status_code,200,response.get_json(silent=True))
        self.assertEqual(response.mimetype,'application/zip')
        archive_bytes=response.get_data()
        response.close()
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            expected={f"{first['id']}/{x}" for x in (media,analysis,raw,thumb)}
            expected.add(f"{second['id']}/{other}")
            self.assertEqual(set(archive.namelist()),expected)
            self.assertEqual(archive.read(f"{first['id']}/{media}"),b'video bytes')
            self.assertEqual(archive.getinfo(f"{first['id']}/{media}").compress_type,zipfile.ZIP_STORED)
        with patch.object(a,'current_user',return_value={'id':'other','role':'member'}):
            self.assertEqual(self.client.get('/api/batches/'+batch['id']+'/all.zip').status_code,404)

    def test_all_zip_rejects_other_batch_and_private_or_symlink_files(self):
        batch=self.create(1).get_json()['batch']
        other=self.create(1).get_json()['batch']
        first=self.store.batch_jobs(batch['id'])[0]
        second=self.store.batch_jobs(other['id'])[0]
        folder=self.root/'downloads'
        folder.mkdir(exist_ok=True)
        public=first['id']+'.mp4'
        secret=second['id']+'.md'
        (folder/public).write_bytes(b'public')
        (folder/secret).write_bytes(b'other-batch-private')
        (folder/'secret-link.md').symlink_to(folder/secret)
        self.store.update_job(first['id'],video_filename=public,
                              text_filename='secret-link.md',
                              transcript_filename='../secrets.txt')
        self.store.update_job(second['id'],text_filename=secret)
        response=self.client.get('/api/batches/'+batch['id']+'/all.zip')
        self.assertEqual(response.status_code,200)
        archive_bytes=response.get_data()
        response.close()
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            self.assertEqual(archive.namelist(),[f"{first['id']}/{public}"])
            self.assertNotIn(b'other-batch-private',archive.read(archive.namelist()[0]))

    def test_all_zip_empty_and_size_guard(self):
        batch=self.create(1).get_json()['batch']
        url='/api/batches/'+batch['id']+'/all.zip'
        self.assertEqual(self.client.get(url).status_code,404)
        row=self.store.batch_jobs(batch['id'])[0]
        folder=self.root/'downloads'
        folder.mkdir(exist_ok=True)
        filename=row['id']+'.mp4'
        (folder/filename).write_bytes(b'123456789')
        self.store.update_job(row['id'],video_filename=filename)
        with patch.object(a,'BATCH_BUNDLE_MAX_BYTES',8):
            response=self.client.get(url)
        self.assertEqual(response.status_code,413)
        self.assertEqual(response.get_json()['error']['code'],'BUNDLE_TOO_LARGE')

    def test_large_text_zip_is_rejected_before_read(self):
        batch=self.create(1).get_json()['batch']
        row=self.store.batch_jobs(batch['id'])[0]
        folder=self.root/'downloads'
        folder.mkdir(exist_ok=True)
        name=row['id']+'.txt'
        with (folder/name).open('wb') as handle:
            handle.truncate(51*1024*1024)
        self.store.update_job(row['id'],text_filename=name)
        response=self.client.get('/api/batches/'+batch['id']+'/texts.zip')
        self.assertEqual(response.status_code,413)
        self.assertEqual(response.get_json()['error']['code'],'BUNDLE_TOO_LARGE')

    def test_health_check_restarts_durable_waiting_jobs(self):
        batch=self.create(4).get_json()['batch']
        self.client.post('/api/batches/'+batch['id']+'/action',json={'action':'pause'})
        rows=self.store.batch_jobs(batch['id'])
        self.store.update_job(rows[0]['id'],queue_state='waiting')
        with patch.object(a.requests,'get') as remote:
            remote.return_value.status_code=200
            remote.return_value.text='ok'
            self.client.get('/health')
        latest=self.store.get_job(rows[0]['id'])
        self.assertEqual(latest['queue_state'],'scheduled')
        self.assertEqual(len(self.executor.submitted),3)

    def test_nav_and_workspace_are_not_modified(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/'templates/batches.html').read_text('utf8')
        js=(root/'static/app.js').read_text('utf8')
        for control in ['batch-urls','batch-submit','batch-job-list','batch-pause','batch-resume','batch-cancel','batch-retry','batch-export']:
            self.assertIn('id="'+control+'"',html)
        self.assertIn('initBatches()',js)
        self.assertNotIn('navigator.share(',js.split('function initBatches()',1)[1])

    def test_batch_ui_has_consistent_card_spacing_and_optional_ai_settings(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/'templates/batches.html').read_text('utf8')
        css=(root/'static/app.css').read_text('utf8')
        js=(root/'static/app.js').read_text('utf8')
        self.assertIn('class="batch-panel-content"',html)
        self.assertIn('class="batch-empty-state"',html)
        self.assertIn('class="task-modes batch-task-modes"',html)
        self.assertIn('class="task-mode-grid" id="batch-task-mode-grid"',html)
        self.assertIn('id="batch-analysis-options"',html)
        self.assertIn('id="batch-format-options"',html)
        self.assertIn('id="batch-task-output-hint"',html)
        self.assertIn('name="batch-task-mode" value="full"',html)
        self.assertIn('本批次统一设置',html)
        self.assertNotIn('batch-advanced-panel',html)
        self.assertIn('padding:24px 26px',css)
        self.assertIn('flex-direction:column',css)
        self.assertIn('monitorPanel.dataset.empty = "false"',js)
        self.assertIn('monitorPanel.dataset.empty="true"',js)

    def test_authenticated_batch_page_renders(self):
        response=self.client.get('/batches')
        self.assertEqual(response.status_code,200)
        self.assertIn('创建批次',response.get_data(as_text=True))
        self.assertIn('id="batch-urls"',response.get_data(as_text=True))

if __name__=='__main__':unittest.main()
