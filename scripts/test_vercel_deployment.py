"""Serverless boundaries: cold starts, shared settings, sessions, and uploads.

Uses synthetic Mongo doubles; does not claim Atlas/Telegram integration coverage.
"""
import copy
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as web
import config_store
import db
from waifu import webtoken

ROOT = Path(__file__).resolve().parents[1]
VERCEL_ENV = {
    'VERCEL': '1', 'MONGO_URI': 'mongodb://synthetic.invalid',
    'DB_NAME': 'waifu_test', 'BOT_TOKEN': '123:synthetic',
    'BOT_USERNAME': 'SyntheticBot', 'OWNER_ID': '42',
    'SESSION_SECRET': 'synthetic-session-secret-for-tests-only-12345678',
}


class SettingsCollection:
    def __init__(self):
        self.values = {}
        self.reads = 0

    def find_one(self, query):
        assert query == {'_id': 'web_config'}
        self.reads += 1
        return {'values': copy.deepcopy(self.values)}

    def update_one(self, query, update, upsert=False):
        assert query == {'_id': 'web_config'} and upsert
        assert set(update) == {'$set'}  # Partial edits must not overwrite others.
        for key, value in update['$set'].items():
            self.values[key.removeprefix('values.')] = value


class VercelTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, VERCEL_ENV)
        env.start(); self.addCleanup(env.stop)

    def test_settings_persist_across_requests_and_keep_partial_edits(self):
        store = SettingsCollection()
        with patch('config_store._collection', return_value=store), patch.object(Path, 'write_text', side_effect=AssertionError('read-only filesystem')):
            with web.app.test_request_context('/'):
                config_store.save({'SITE_TITLE': 'Guess', 'SITE_SUBTITLE': 'Catch'})
                self.assertEqual(config_store.get('SITE_TITLE'), 'Guess')
                self.assertEqual(config_store.get('SITE_SUBTITLE'), 'Catch')
                self.assertEqual(store.reads, 1)
                store.values['SITE_TITLE'] = 'Edited by another instance'
                self.assertEqual(config_store.get('SITE_TITLE'), 'Guess')
            with web.app.test_request_context('/'):
                self.assertEqual(config_store.get('SITE_TITLE'), 'Edited by another instance')
                config_store.save({'SITE_SUBTITLE': ''})
                self.assertEqual(config_store.get('SITE_SUBTITLE'), '')
                self.assertEqual(config_store.get('SITE_TITLE'), 'Edited by another instance')
            with patch.dict(os.environ, {'SITE_TITLE': 'Env override'}):
                self.assertEqual(config_store.get('SITE_TITLE'), 'Env override')
            config_store.save({'MONGO_URI': 'ignored', 'DB_NAME': 'ignored'})
            self.assertNotIn('MONGO_URI', store.values)
            self.assertEqual(config_store.get('DB_NAME'), 'waifu_test')

    def test_settings_failure_never_writes_local_json(self):
        with patch('config_store._collection', side_effect=RuntimeError('offline')), patch.object(Path, 'write_text') as write:
            with self.assertRaises(RuntimeError):
                config_store.save({'SITE_TITLE': 'Guess'})
            write.assert_not_called()

    def test_database_failure_never_falls_back(self):
        with patch('db._db', None), patch('db.MongoWebDB', side_effect=RuntimeError('offline')), patch('db.LocalWebDB') as local:
            with self.assertRaises(RuntimeError):
                db.get_db()
            local.assert_not_called()
        with patch('db._db', None), patch.dict(os.environ, {'MONGO_URI': ''}), patch('db.LocalWebDB') as local:
            with self.assertRaises(RuntimeError):
                db.get_db()
            local.assert_not_called()

    def test_mongo_token_login_never_touches_local_disk(self):
        with patch('pymongo.MongoClient') as factory, patch('waifu.webtoken._file_lock', side_effect=AssertionError('read-only filesystem')):
            collection = factory.return_value.__enter__.return_value.__getitem__.return_value.__getitem__.return_value
            collection.find_one_and_update.return_value = {'tokens': [{'token': 'one-time', 'created_at': time.time(), 'user_id': 42}]}
            self.assertEqual(webtoken.consume_token('one-time')['user_id'], 42)
            collection.find_one_and_update.return_value = None
            self.assertIsNone(webtoken.consume_token('one-time'))

    def test_cold_start_and_cross_instance_session(self):
        # A new process must import without DB reads or repository writes and
        # must decode a cookie signed by a previous instance with the same key.
        code = r'''
import os
from unittest.mock import patch
from pathlib import Path
with patch.object(Path, 'write_text', side_effect=AssertionError('read-only')), patch('config_store.save', side_effect=AssertionError('cold-start mutation')), patch('config_store._collection', side_effect=AssertionError('cold-start DB access')):
    import app
assert app.IS_VERCEL
assert app.app.config['SESSION_COOKIE_SECURE']
assert app._MEDIA_CACHE_DIR.startswith('/tmp/')
assert app._MAX_MEDIA_BYTES == 4 * 1024 * 1024
assert app.app.test_client().get('/healthz').json == {'status':'ok'}
serializer = app.app.session_interface.get_signing_serializer(app.app)
if os.environ.get('TEST_SESSION_COOKIE'):
    assert serializer.loads(os.environ['TEST_SESSION_COOKIE'])['user_id'] == 42
    print('decoded')
else:
    print(serializer.dumps({'user_id':42}))
'''
        with tempfile.TemporaryDirectory() as cwd:
            env = {**os.environ, 'PYTHONPATH': str(ROOT), 'PYTHONDONTWRITEBYTECODE': '1'}
            result = subprocess.run([sys.executable, '-c', code], env=env, cwd=cwd, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            env['TEST_SESSION_COOKIE'] = result.stdout.strip()
            result = subprocess.run([sys.executable, '-c', code], env=env, cwd=cwd, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'decoded')
            self.assertEqual(list(Path(cwd).iterdir()), [])

    def test_missing_or_weak_deployment_config_fails_clearly(self):
        for changes, expected in [({'SESSION_SECRET': ''}, 'SESSION_SECRET'), ({'SESSION_SECRET': 'short'}, '32 random'), ({'OWNER_ID': 'bad'}, 'OWNER_ID')]:
            result = subprocess.run([sys.executable, '-c', 'import app'], cwd=ROOT,
                                    env={**os.environ, **changes}, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(expected, result.stderr)

    def test_oversized_media_is_bounded_and_connection_closed(self):
        response = Mock(status_code=200, headers={'Content-Type':'video/mp4'})
        response.iter_content.return_value = iter([b'x' * 1024, b'x' * 1024])
        with tempfile.TemporaryDirectory() as cache, patch('app._MEDIA_CACHE_DIR', cache), patch('app._MAX_MEDIA_BYTES', 1024), patch('app._req.get', return_value=response):
            with web.app.test_request_context('/'):
                result = web._serve_cached_media('https://synthetic.invalid/video.mp4')
                self.assertEqual(result.mimetype, 'image/svg+xml')
                result.close()
            self.assertEqual(list(Path(cache).iterdir()), [])
            response.close.assert_called_once()

    def test_vercel_upload_limits_and_owner_branding(self):
        # Exercise an actual Vercel-configured import, with isolated data only.
        code = r'''
import io
import tempfile
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import app
import db
from scripts.test_vercel_deployment import SettingsCollection
store = SettingsCollection()
with tempfile.TemporaryDirectory() as tmp, patch('config_store._collection', return_value=store), patch('db.LocalWebDB._DIR', Path(tmp)), patch('db._db', db.LocalWebDB()):
    client = app.app.test_client()
    with client.session_transaction() as s:
        s.update(user_id=42, first_name='Owner', csrf_token='synthetic-csrf')
    headers = {'X-CSRF-Token':'synthetic-csrf'}
    assert client.post('/api/admin/settings', json={'SITE_TITLE':'Vercel Guess'}, headers=headers).json['ok']
    assert b'Vercel Guess' in client.get('/').data
    image = io.BytesIO()
    Image.new('RGB', (20,20), 'pink').save(image, format='PNG'); image.seek(0)
    assert client.post('/admin/logo', data={'logo':(image,'logo.png')}, headers=headers).json['ok']
    logo = client.get('/site-logo'); assert logo.status_code == 200; logo.close()
    assert 'Maximum 4 MB' in client.get('/admin').text
    for path, field in [('/admin/logo','logo'),('/admin/welcome','welcome'),('/admin/ad-banner','ad_banner')]:
        with io.BytesIO(b'x'*(4*1024*1024+1)) as f:
            result = client.post(path, data={field:(f,'large.png')}, headers=headers)
            assert result.is_json and not result.json['ok'], (path,result.status_code)
    result = client.post('/admin/logo', data=b'x'*(4*1024*1024+65537), headers=headers)
    assert result.status_code == 413 and result.is_json
'''
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=os.environ, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
