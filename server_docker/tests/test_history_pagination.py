import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_docker.history_store import HistoryStore
from server_docker import app as appmod


class HistoryPaginationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = HistoryStore(Path(self.folder.name) / 'history.db')
        for number in range(1, 27):
            self.store.create_job(
                id=f'wxv_{number:06d}', input_url=f'https://weixin.qq.com/sph/{number}',
                prompt='口播测试' if number == 26 else '普通任务',
                preset_id=None, preset_name=None, output_format='md',
                download_requested=False, max_attempts=3,
                owner_user_id='owner-a' if number % 2 == 0 else 'owner-b',
            )

    def tearDown(self):self.folder.cleanup()

    def test_pages_and_total(self):
        one = self.store.page_jobs(page=1, per_page=10)
        two = self.store.page_jobs(page=2, per_page=10)
        end = self.store.page_jobs(page=9999, per_page=10)
        self.assertEqual((one['total'], one['pages'], one['page'], len(one['rows'])), (26, 3, 1, 10))
        self.assertEqual([r['id'] for r in one['rows']][-1], 'wxv_000017')
        self.assertEqual(two['rows'][0]['id'], 'wxv_000016')
        self.assertEqual((end['page'], len(end['rows'])), (3, 6))
        self.assertEqual(self.store.page_jobs(page=1, per_page=999)['per_page'], 20)

    def test_query_across_all_pages_and_user_isolation(self):
        search = self.store.page_jobs(page=1, per_page=10, query='口播测试', owner_user_id='owner-a')
        self.assertEqual([r['id'] for r in search['rows']], ['wxv_000026'])
        other = self.store.page_jobs(page=1, per_page=10, query='口播测试', owner_user_id='owner-b')
        self.assertEqual(other['total'], 0)
        wildcards = self.store.page_jobs(page=1, per_page=10, query='%')
        self.assertEqual(wildcards['total'], 0)

    def test_paginated_api_respects_role_and_old_clients(self):
        client = appmod.app.test_client()
        member = {'id': 'owner-b', 'username': 'member', 'role': 'member'}
        with patch.object(appmod, 'current_user', return_value=member), \
             patch.object(appmod, 'get_history_store', return_value=self.store):
            reply = client.get('/api/history?page=1&per_page=10')
            self.assertEqual(reply.status_code, 200)
            data = reply.get_json()
            self.assertEqual(data['pagination']['total'], 13)
            self.assertEqual(len(data['jobs']), 10)
            self.assertNotIn('owner_username', data['jobs'][0])
            self.assertEqual(len(client.get('/api/history').get_json()['jobs']), 13)

    def test_frontend_stores_page_size(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / 'static/app.js').read_text('utf-8')
        self.assertIn('wvd-history-page-size', text)
        self.assertIn('URLSearchParams', text)
        html = (root / 'templates/history.html').read_text('utf-8')
        for value in ('10', '20', '50', '100'):
            self.assertIn(f'value="{value}"', html)

if __name__ == '__main__': unittest.main()
