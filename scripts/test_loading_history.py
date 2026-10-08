"""Loading, public-media cache and auction-history/data-retention regressions."""
import base64
import hashlib
import io
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import app as web
import db
from display_cache import display_state
from scripts import test_admin_branding as branding


class LoadingHistoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = branding.BrandingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.client = self.fixture.client
        self.store = db.get_db()
        for uid in (42, 43, 44):
            self.store.ensure_user(uid, f'User {uid}', f'user{uid}')
            self.store.add_coins(uid, 100000)

    def test_display_metadata_reused_and_invalidated_without_caching_balance(self):
        self.store._display_cache = None
        with patch.object(self.store, 'get_public_media', wraps=self.store.get_public_media) as read:
            for _ in range(10): display_state(self.store)
            self.assertEqual(read.call_count, 1)
            self.store.set_logo(base64.b64encode(b'synthetic image').decode(), 'image/png')
            self.assertIsNotNone(display_state(self.store)['logo']['version'])
            self.assertEqual(read.call_count, 2)
            with patch('display_cache.time.monotonic', return_value=time.monotonic() + 16):
                display_state(self.store)
            self.assertEqual(read.call_count, 3)
        self.store.add_coins(42, 100)
        self.assertEqual(self.client.get('/api/profile').json['balance'], '$1001.00')

    def test_legacy_media_fingerprints_are_read_only(self):
        mongo = object.__new__(db.MongoWebDB)
        mongo._settings = Mock()
        encoded = base64.b64encode(b'legacy').decode()
        mongo._settings.find.side_effect = [
            [{'_id':'site_logo','mime':'image/png'}],
            [{'_id':'site_logo','mime':'image/png','data':encoded}],
        ]
        info = mongo.get_public_media()['logo']
        self.assertEqual(info['version'], hashlib.sha256(encoded.encode()).hexdigest())
        self.assertNotIn('data', info)
        mongo._settings.update_one.assert_not_called()
        mongo._settings.delete_many.assert_not_called()

    def test_public_media_skips_presence_and_works_during_maintenance(self):
        output = io.BytesIO(); Image.new('RGB', (20,20), 'pink').save(output, format='PNG')
        data = base64.b64encode(output.getvalue()).decode()
        self.store.set_welcome_slides([{'data':data,'mime':'image/png'}])
        version = display_state(self.store)['slides'][0]['version']
        with patch('app._cfg', return_value='1'), patch.object(self.store, 'claim_presence', side_effect=AssertionError('media must not claim presence')):
            response = self.client.get('/welcome-media/0?v=' + version)
            self.assertEqual(response.status_code, 200)
            self.assertIn('immutable', response.headers['Cache-Control'])
            self.assertIn('s-maxage=2592000', response.headers['Vercel-CDN-Cache-Control'])
            self.assertNotIn('Set-Cookie', response.headers)
            response.close()
        self.store.set_welcome_slides([{'data':base64.b64encode(b'new').decode(),'mime':'image/png'}])
        self.assertEqual(self.client.get('/welcome-media/0?v=' + version).status_code, 404)

    def test_thumbnail_is_smaller_cached_and_conditional(self):
        raw = io.BytesIO(); Image.effect_noise((1400,1800), 80).convert('RGB').save(raw, format='PNG')
        original = raw.getvalue()
        upstream = Mock(status_code=200, headers={'Content-Type':'image/png'})
        upstream.iter_content.return_value = [original]
        url = '/media/' + web._media_token('synthetic-photo-id') + '?w=640'
        with tempfile.TemporaryDirectory() as cache, patch('app.IS_VERCEL', True), patch('app._MAX_MEDIA_BYTES', 4*1024*1024), patch('app._MEDIA_CACHE_DIR', cache), patch('app._tg_file_info', return_value={'url':'https://synthetic.invalid/photo','path':'photo.png'}), patch('app._req.get', return_value=upstream) as get:
            with self.client.session_transaction() as session: session.permanent = True
            first = self.client.get(url)
            self.assertEqual(first.mimetype, 'image/webp')
            self.assertLess(len(first.data), len(original) / 2)
            with Image.open(io.BytesIO(first.data)) as thumbnail:
                self.assertLessEqual(max(thumbnail.size), 640)
            self.assertNotIn('Set-Cookie', first.headers)
            second = self.client.get(url, headers={'If-None-Match':first.headers['ETag']})
            self.assertEqual(second.status_code, 304)
            get.assert_called_once(); upstream.close.assert_called_once()
            print(f'Thumbnail fixture: {len(original)} → {len(first.data)} bytes; cached repeat skipped upstream download.')
            first.close(); second.close()

    def test_failed_images_are_not_cached(self):
        with patch('app._tg_file_info', return_value=None):
            response = self.client.get('/media/' + web._media_token('missing'))
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            response.close()

    def test_reclaim_does_not_duplicate_presence_when_earlier_slot_is_free(self):
        self.store.release_presence('unused')
        self.assertTrue(self.store.claim_presence('first', 43))
        self.assertTrue(self.store.claim_presence('second', 44))
        self.store.release_presence('first')
        self.assertTrue(self.store.claim_presence('second', 44))
        self.assertEqual(self.store.active_presence_count(), 1)
        mongo = object.__new__(db.MongoWebDB)
        mongo.touch_presence = Mock(return_value=True)
        mongo._presence = Mock()
        self.assertTrue(mongo.claim_presence('existing', 43))
        mongo._presence.find_one_and_update.assert_not_called()

    def test_batched_view_tracking_is_deduplicated_and_csrf_protected(self):
        lid = self.store.add_listing(43, 'Seller', {'id':1,'name':'Card'}, 100)
        response = self.client.post('/api/views', json={'ids':[lid,lid]}, headers=self.fixture.headers)
        self.assertTrue(response.json['ok'])
        self.assertEqual(self.store.get_listing(lid)['views'], 1)
        self.assertEqual(self.client.post('/api/views', json={'ids':[lid]}).status_code, 403)
        self.assertEqual(self.client.post('/api/views', json={'ids':['x']*41}, headers=self.fixture.headers).status_code, 400)

    def test_auction_winners_retained_losers_leave_active_history(self):
        card = {'id':5, 'name':'History Card', 'anime':'Test', 'rarity':'Common'}
        lid = self.store.add_listing(44, 'Seller', card, 1000, 'auction', time.time()+3600)
        self.assertTrue(self.store.place_bid(lid, 42, 'First', 1500)['ok'])
        self.assertTrue(self.store.place_bid(lid, 43, 'Winner', 2000)['ok'])
        self.assertEqual(len(self.store.get_auctions(time.time(), bidder_id=42)), 1)
        self.assertIn(b'Outbid', self.client.get('/auction?tab=bids').data)
        self.store._load('market')[lid]['ends_at'] = time.time()-1
        before = len(self.store._load('transactions'))
        html = self.client.get('/auction?tab=bids').data
        self.assertNotIn(b'History Card', html)
        self.assertEqual(self.store.get_auction_wins(42), [])
        self.assertEqual(len(self.store.get_auction_wins(43)), 1)
        self.assertGreater(len(self.store._load('transactions')), before)
        for _ in range(15): self.store.log_transaction('transfer', 42, 43, 1)
        self.assertEqual(len(self.store.get_auction_wins(43)), 1)
        self.assertEqual(self.store.get_balance(42), 100000)
        self.assertEqual(self.store.get_balance(43), 98000)
        self.assertEqual(self.store.get_balance(44), 102000)
        self.fixture.identify(43)
        response = self.client.get('/auction?tab=wins')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'History Card', response.data)
        self.fixture.identify(42)
        self.assertNotIn(b'History Card', self.client.get('/auction?tab=wins').data)

    def test_wins_keep_last_ten_without_deleting_other_history(self):
        self.store.log_transaction('transfer', 42, 43, 100)
        self.store.log_transaction('auction_sale', 43, 42, 100)
        for i in range(12):
            self.store.log_transaction('auction_sale', 42, 44, 100, {'char': {'id': i, 'name': f'Win {i}'}})
        wins = self.store.get_auction_wins(42)
        self.assertEqual([row['details']['char']['id'] for row in wins], list(range(11, 1, -1)))
        self.assertEqual(len(self.store.get_auction_wins(43)), 1)
        self.assertEqual(len(self.store._load('transactions')), 12)
        self.assertEqual(self.store.get_auction_wins(42, skip=10), [])
        self.assertEqual(self.store.get_balance(42), 100000)

    def test_mongo_pruning_is_scoped_to_old_wins_for_one_user(self):
        mongo = object.__new__(db.MongoWebDB); mongo._tx = Mock()
        mongo._tx.find.return_value.sort.return_value.skip.return_value = [{'_id': 'old-win'}]
        mongo.log_transaction('auction_sale', 42, 43, 100)
        mongo._tx.delete_many.assert_called_once_with({
            'type': 'auction_sale', 'from_id': 42, '_id': {'$in': ['old-win']}})
        mongo._tx.reset_mock()
        mongo.log_transaction('transfer', 42, 43, 100)
        mongo._tx.find.assert_not_called()
        mongo._tx.delete_many.assert_not_called()

    def test_market_and_auction_are_separate_including_purchase_endpoints(self):
        fixed = self.store.add_listing(43, 'Seller', {'id': 21, 'name': 'Fixed Only'}, 100)
        legacy = self.store.add_listing(43, 'Seller', {'id': 22, 'name': 'Legacy Fixed'}, 100)
        self.store._load('market')[legacy].pop('listing_type')
        auction = self.store.add_listing(43, 'Seller', {'id': 23, 'name': 'Auction Only'}, 100, 'auction', time.time()+3600)
        market = self.client.get('/market').data
        self.assertIn(b'Fixed Only', market)
        self.assertIn(b'Legacy Fixed', market)
        self.assertNotIn(b'Auction Only', market)
        self.assertEqual(self.store.count_listings(listing_type='fixed'), 2)
        self.assertNotIn(b'Fixed Only', self.client.get('/auction').data)
        self.assertIn(b'Auction Only', self.client.get('/auction').data)
        self.assertFalse(self.store.buy_listing(auction, 42, 'Buyer')['ok'])
        self.assertFalse(self.store.lucky_buy_listing(auction, 42, 'Buyer', 44, 500)['ok'])
        self.assertEqual(self.store.get_balance(42), 100000)
        self.assertIsNotNone(self.store.get_listing(auction))
        mongo = object.__new__(db.MongoWebDB); mongo._market = Mock(); mongo._DESC = -1
        mongo._market.find.return_value.sort.return_value.skip.return_value.limit.return_value = []
        mongo.get_listings(search='Only', listing_type='fixed')
        self.assertEqual(mongo._market.find.call_args.args[0]['listing_type'], {'$in': ['fixed', None]})
        mongo.count_listings(listing_type='fixed')
        self.assertEqual(mongo._market.count_documents.call_args.args[0]['listing_type'], {'$in': ['fixed', None]})

    def test_games_live_below_home_actions_and_follow_owner_toggles(self):
        self.store.set_wheel_show(True)
        self.store.set_rocket_show(True)
        self.store.set_card_update_show(True)
        home = self.client.get('/').get_data(as_text=True)
        self.assertLess(home.index('aria-label="Main actions"'), home.index('aria-label="More activities"'))
        self.assertIn('id="openWheelBtn"', home)
        self.assertIn('id="wheelModal"', home)
        self.assertIn('js/wheel.js', home)
        market = self.client.get('/market').get_data(as_text=True)
        for marker in ('rocket-fab', 'card-update-fab', 'openWheelBtn', 'wheelModal', 'js/wheel.js'):
            self.assertNotIn(marker, market)
        self.store.set_wheel_show(False)
        self.store.set_rocket_show(False)
        self.store.set_card_update_show(False)
        home = self.client.get('/').get_data(as_text=True)
        self.assertNotIn('aria-label="More activities"', home)
        self.assertNotIn('id="wheelModal"', home)

    def test_auction_queries_are_filtered_and_bounded(self):
        mongo = object.__new__(db.MongoWebDB); mongo._market = Mock(); mongo._tx=Mock();mongo._DESC=-1
        mongo._market.find.return_value.sort.return_value.skip.return_value.limit.return_value=[]
        mongo.get_auctions(1234, skip=24, limit=25, bidder_id=42)
        query = mongo._market.find.call_args.args[0]
        self.assertEqual(query['listing_type'],'auction')
        self.assertEqual(query['ends_at'],{'$gt':1234})
        self.assertIn({'bidder_ids':42}, query['$or'])
        mongo._market.find.return_value.sort.return_value.skip.return_value.limit.assert_called_once_with(25)
        for i in range(27):
            self.store.add_listing(43,'Seller',{'id':i,'name':f'Card {i}'},100,'auction',time.time()+3600)
        response=self.client.get('/auction')
        self.assertEqual(response.data.count(b'class="char-card auction-card"'),24)
        self.assertIn(b'Next',response.data)
        self.assertEqual(self.client.get('/auction?page=2').data.count(b'class="char-card auction-card"'),3)

    def test_ad_markup_does_not_autoload_or_overlay(self):
        self.store.set_ad_banner(base64.b64encode(b'video').decode(), 'video/mp4')
        html = self.client.get('/market').text
        self.assertIn('Community spotlight', html)
        self.assertIn('data-src="/ad-banner-media?v=',html)
        self.assertNotIn('autoplay muted loop',html)
        self.assertLess(html.index('id="main-content"'), html.index('id="adBanner"'))


if __name__ == '__main__':
    unittest.main()
