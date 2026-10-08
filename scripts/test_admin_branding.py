"""Owner branding persistence, permissions and logo replacement regressions."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import app as web
import config_store
import db


class BrandingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for target, value in [('config_store._CONFIG_FILE', self.root/'config.json'),
                              ('config_store._cache', None), ('db.LocalWebDB._DIR', self.root),
                              ('db._db', None), ('app.is_configured', lambda: True),
                              ('app._owner_id', lambda: 42)]:
            p = patch(target, value); p.start(); self.addCleanup(p.stop)
        env = patch.dict('os.environ', {'SITE_TITLE':'', 'SITE_SUBTITLE':'', 'MONGO_URI':'', 'MAINTENANCE_MODE':'0'})
        env.start(); self.addCleanup(env.stop)
        self.client = web.app.test_client()
        self.identify(42)

    def identify(self, uid):
        with self.client.session_transaction() as session:
            session.clear()
            session.update(user_id=uid, first_name='Owner')
        self.client.get('/')
        with self.client.session_transaction() as session:
            self.headers = {'X-CSRF-Token': session['csrf_token']}

    def save(self, **data):
        return self.client.post('/api/admin/settings', json=data, headers=self.headers)

    def upload(self, color='purple'):
        image = io.BytesIO()
        Image.new('RGB', (640, 320), color).save(image, format='PNG')
        image.seek(0)
        return self.client.post('/admin/logo', data={'logo':(image,'logo.png')}, headers=self.headers)

    def test_five_welcome_photos_upload_render_and_reject_extra_without_changes(self):
        def upload(color):
            image = io.BytesIO()
            Image.new('RGB', (40, 20), color).save(image, format='PNG')
            image.seek(0)
            return self.client.post('/admin/welcome', data={'welcome': (image, 'welcome.png')}, headers=self.headers)
        for i, color in enumerate(('red', 'green', 'blue', 'pink', 'purple')):
            response = upload(color)
            self.assertTrue(response.json['ok'])
            self.assertEqual(response.json['count'], i + 1)
        slides = db.get_db().get_welcome_slides()
        self.assertEqual(len(slides), 5)
        before = [slide['data'] for slide in slides]
        response = upload('yellow')
        self.assertEqual(response.status_code, 400)
        self.assertEqual([slide['data'] for slide in db.get_db().get_welcome_slides()], before)
        home = self.client.get('/').get_data(as_text=True)
        self.assertEqual(home.count('data-slide='), 5)
        self.assertNotIn('miniBannerPause', home)
        self.assertNotIn('aria-label="Show highlight', home)
        self.assertIn('3 seconds', self.client.get('/admin').get_data(as_text=True))
        self.assertTrue(self.client.post('/admin/welcome/2/delete', headers=self.headers).json['ok'])
        self.assertTrue(upload('yellow').json['ok'])
        self.identify(43)
        self.assertEqual(upload('yellow').status_code, 403)

    def test_defaults_save_escape_and_persist(self):
        self.assertIn(b'WAIFU.', self.client.get('/').data)
        response = self.save(SITE_TITLE='<b>Guess</b>', SITE_SUBTITLE='Catch with us')
        self.assertEqual(response.status_code, 200)
        config_store.reload()
        self.assertEqual(config_store.all_config()['SITE_TITLE'], '<b>Guess</b>')
        home = self.client.get('/').get_data(as_text=True)
        self.assertIn('&lt;b&gt;Guess&lt;/b&gt;', home)
        self.assertNotIn('<b>Guess</b>', home)
        self.assertIn('Catch with us', home)
        admin = self.client.get('/admin').get_data(as_text=True)
        self.assertIn('Mini App Branding', admin)
        self.assertIn('id="cfg-SITE_TITLE"', admin)
        self.assertIn('Catch with us', admin)

    def test_validation_and_clear_subtitle(self):
        for data in [{'SITE_TITLE':''}, {'SITE_TITLE':'a'*41}, {'SITE_SUBTITLE':'a'*65},
                     {'SITE_TITLE':['bad']}, {'SITE_SUBTITLE':'a\nb'}]:
            self.assertEqual(self.save(**data).status_code, 400)
        self.assertEqual(self.save(SITE_SUBTITLE='').status_code, 200)
        self.assertEqual(config_store.all_config()['SITE_SUBTITLE'], '')
        self.assertNotIn('TELEGRAM CATCH BOT', self.client.get('/').get_data(as_text=True))

    def test_only_owner_can_change_branding_or_logo(self):
        self.identify(43)
        self.assertEqual(self.save(SITE_TITLE='Changed').status_code, 401)
        self.assertEqual(self.upload().status_code, 403)
        self.assertEqual(self.client.post('/admin/logo/delete', headers=self.headers).status_code, 403)
        self.identify(42)
        self.assertEqual(self.client.post('/api/admin/settings', json={'SITE_TITLE':'Changed'}).status_code, 403)
        self.assertEqual(config_store.all_config()['SITE_TITLE'], 'WAIFU.')

    def test_logo_upload_revalidate_replace_delete(self):
        self.assertTrue(self.upload().json['ok'])
        response = self.client.get('/site-logo')
        self.assertEqual(response.mimetype, 'image/webp')
        self.assertEqual(Image.open(io.BytesIO(response.data)).size, (512,256))
        self.assertEqual(response.headers['Cache-Control'], 'public, no-cache')
        old_etag = response.headers['ETag']
        self.assertEqual(self.client.get('/site-logo', headers={'If-None-Match':old_etag}).status_code, 304)
        self.assertIn(b'class="mini-brand-logo"', self.client.get('/').data)
        self.assertTrue(self.upload('blue').json['ok'])
        replaced = self.client.get('/site-logo', headers={'If-None-Match':old_etag})
        self.assertEqual(replaced.status_code, 200)
        self.assertNotEqual(replaced.headers['ETag'], old_etag)
        self.assertTrue(self.client.post('/admin/logo/delete', headers=self.headers).json['ok'])
        self.assertNotIn(b'class="mini-brand-logo"', self.client.get('/').data)
        self.assertEqual(self.client.get('/site-logo').status_code, 404)

    def test_invalid_and_oversized_images_leave_logo_unchanged(self):
        self.upload()
        original = self.client.get('/site-logo').data
        for raw in [b'not a PNG', b'x'*(5*1024*1024+1)]:
            response = self.client.post('/admin/logo', data={'logo':(io.BytesIO(raw),'bad.png')}, headers=self.headers)
            self.assertEqual(response.status_code, 400)
            response.request.environ['wsgi.input'].close()
            response.close()
            self.assertEqual(self.client.get('/site-logo').data, original)


if __name__ == '__main__':
    unittest.main()
