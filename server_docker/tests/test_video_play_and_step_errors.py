"""Owner-only playback and persisted per-stage task errors."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from server_docker import app as appmod
from server_docker.history_store import HistoryStore
from server_docker.job_progress import get_job_progress

class OwnerPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.work=tempfile.TemporaryDirectory()
        self.root=Path(self.work.name)
        (self.root/'downloads').mkdir()
        self.store=HistoryStore(self.root/'history.db')
        self.store.create_job(id='wxv_000019',input_url='https://weixin.qq.com/sph/example',prompt=None,
             preset_id=None,preset_name=None,output_format='md',download_requested=False,max_attempts=3,
             owner_user_id='owner-a',raw_requested=True,timed_requested=True,analyze_requested=False)
        self.store.update_job('wxv_000019',source_direct_url='https://finder.video.qq.com/251/20304/stodownload?token=private',
                              status='completed',stage='finished')
        self.appctx=patch.object(appmod,'get_history_store',return_value=self.store)
        self.appctx.start()
        self.pathctx=patch.object(appmod,'DOWNLOAD_DIR',self.root/'downloads')
        self.pathctx.start()
        self.client=appmod.app.test_client()
        self.user=patch.object(appmod,'current_user',return_value={'id':'owner-a','username':'member','role':'member'})
        self.user.start()

    def tearDown(self):
        self.user.stop();self.pathctx.stop();self.appctx.stop();self.work.cleanup()

    def test_direct_url_is_not_exposed_to_frontend(self):
        with appmod.app.test_request_context('/api/history/wxv_000019'):
            result=appmod.serialize_job(self.store.get_job('wxv_000019'))
        self.assertEqual(result['video']['play_path'],'/api/history/wxv_000019/play')
        self.assertNotIn('token=private',str(result['video']))

    def test_remote_playback_range_auth_and_private_headers(self):
        upstream=Mock(status_code=206,headers={'Content-Type':'video/mp4',
              'Content-Length':'4','Content-Range':'bytes 0-3/100','Accept-Ranges':'bytes'})
        upstream.iter_content.return_value=[b'abcd']
        with patch.object(appmod.requests,'get',return_value=upstream) as get:
            r=self.client.get('/api/history/wxv_000019/play',headers={'Range':'bytes=0-3'})
            self.assertEqual(r.status_code,206)
            self.assertEqual(r.data,b'abcd')
            self.assertIn('no-store',r.headers['Cache-Control'])
            self.assertEqual(r.headers['Content-Range'],'bytes 0-3/100')
            self.assertNotIn('token=private',str(r.headers))
            get.assert_called_once()
            self.assertEqual(get.call_args.kwargs['headers'],{'Range':'bytes=0-3'})
            self.assertFalse(get.call_args.kwargs['allow_redirects'])
            upstream.close.assert_called()

    def test_foreign_user_cannot_get_play_path(self):
        with patch.object(appmod,'current_user',return_value={'id':'owner-b','role':'member'}):
            r=self.client.get('/api/history/wxv_000019/play')
            self.assertEqual(r.status_code,404)

    def test_strict_host_blocks_server_side_request_forgery(self):
        self.store.update_job('wxv_000019',source_direct_url='http://127.0.0.1:81/secrets')
        with patch.object(appmod.requests,'get') as get:
            self.assertEqual(self.client.get('/api/history/wxv_000019/play').status_code,404)
            get.assert_not_called()

    def test_bad_remote_response_is_not_forwarded(self):
        upstream=Mock(status_code=302,headers={'Location':'https://evil.example/'})
        with patch.object(appmod.requests,'get',return_value=upstream):
            r=self.client.get('/api/history/wxv_000019/play')
            self.assertEqual(r.status_code,502)
            upstream.close.assert_called()

    def test_locally_owned_mp4_streams_with_byte_ranges(self):
        media=self.root/'downloads'/'wxv_000019.mp4'
        media.write_bytes(b'0123456789')
        self.store.update_job('wxv_000019',video_filename=media.name)
        r=self.client.get('/api/history/wxv_000019/play',headers={'Range':'bytes=1-3'})
        self.assertEqual(r.status_code,206)
        self.assertEqual(r.data,b'123')
        self.assertIn('no-store',r.headers['Cache-Control'])
        self.assertIn('inline',r.headers.get('Content-Disposition','inline'))

    def test_code_based_step_error_is_attached_to_failed_stage(self):
        job=self.store.get_job('wxv_000019')
        job.update({'status':'failed','stage':'finished','attempts_used':2,
                    'error_code':'SENSEVOICE_TIMEOUT',
                    'retry_errors':[{'attempt':1,'code':'HTTP_TIMEOUT','stage':'yuanbao_video'},
                                    {'attempt':2,'code':'SENSEVOICE_TIMEOUT','stage':'local_transcription'}]})
        p=get_job_progress(job,self.root)
        asr=next(step for step in p['steps'] if step['key']=='asr')
        self.assertEqual(asr['state'],'failed')
        self.assertIn('SENSEVOICE_TIMEOUT',' '.join(asr['issues']))
        source=next(step for step in p['steps'] if step['key']=='source')
        self.assertIn('HTTP_TIMEOUT',' '.join(source['issues']))
        self.assertNotIn('secret',str(p).lower())

    def test_ui_contains_playback_and_history_stage_review(self):
        static=(Path(__file__).resolve().parents[1]/'static/app.js').read_text('utf8')
        self.assertIn('videoPlayButton',static)
        self.assertIn('history-step-review',static)
        self.assertIn('step.issues',static)
        self.assertNotIn('sourceLink(url, "浏览器打开")',static)

if __name__=='__main__':unittest.main()
