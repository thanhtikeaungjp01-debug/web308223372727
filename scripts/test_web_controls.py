"""Synthetic-only regressions for owner controls, calendar quotas and media playback."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import app as web
import db
from video_media import transcode_480p
from imageio_ffmpeg import get_ffmpeg_exe, read_frames
from scripts.test_admin_branding import BrandingTests


class ControlsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = BrandingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.client, self.headers = self.fixture.client, self.fixture.headers
        self.store = db.get_db()
        for uid in (42, 43, 44):
            self.store.ensure_user(uid, 'Player', '')
            self.store.add_coins(uid, 100000)

    def post(self, path, data):
        return self.client.post(path, json=data, headers=self.fixture.headers)

    def test_shared_calendar_quota_concurrency_and_refund(self):
        with patch('web_controls.month_key', return_value='2026-10'):
            with ThreadPoolExecutor(max_workers=20) as pool:
                results = list(pool.map(lambda _: self.store.reserve_auction_slot(43), range(35)))
            self.assertEqual(sum(bool(x) for x in results), 15)
            self.assertEqual(self.store.get_auction_quota(43)['sandbox'], ['43'])
            for _ in range(15): self.store.reserve_auction_slot(44)
            self.store.refund_auction_slot(43, '2026-10')
            self.assertEqual(self.store.get_auction_quota(43)['used'], 14)
            self.assertEqual(self.store.get_auction_quota()['sandbox'], ['44'])
        with patch('web_controls.month_key', return_value='2026-11'):
            self.assertEqual(self.store.get_auction_quota()['sandbox'], [])
            for uid in (43,44): self.assertEqual(self.store.get_auction_quota(uid)['used'], 0)
            self.assertTrue(self.store.reserve_auction_slot(43))
        self.assertEqual(self.store.get_balance(43), 100000)

    def test_auction_endpoint_quota_and_fixed_market_block(self):
        card={'id':9, 'name':'Quota card', 'rarity':'⚜️ Divine'}
        for _ in range(15): self.store.reserve_auction_slot(42)
        self.store.add_char(42, card)
        response=self.post('/api/sell', {'char_id':'9','price':100000,'listing_type':'auction'})
        self.assertEqual(response.status_code,429)
        self.assertEqual(len(self.store.get_harem(42)),1)
        self.assertEqual(self.store.get_balance(42),100000)
        response=self.post('/api/sell', {'char_id':'9','price':100000,'listing_type':'fixed'})
        self.assertEqual(response.status_code,403)
        self.assertFalse(response.json['ok'])
        self.assertEqual(len(self.store.get_harem(42)),1)
        self.assertEqual(self.store.get_balance(42),100000)
        self.assertEqual(self.store.get_auction_quota(42)['used'],15)

    def test_fixed_and_implicit_market_listing_blocked_for_owner_and_user(self):
        for uid in (42,43):
            self.fixture.identify(uid)
            self.store.add_char(uid,{'id':99,'name':'Blocked market card','rarity':'⚪ Common'})
            before=self.store.get_balance(uid)
            for listing_type in ('fixed', '', None, 'unknown'):
                payload={'char_id':'99','price':100}
                if listing_type is not None: payload['listing_type']=listing_type
                response=self.post('/api/sell',payload)
                self.assertEqual(response.status_code,403)
                self.assertEqual(self.store.get_balance(uid),before)
                self.assertEqual(len(self.store.get_harem(uid)),1)
                self.assertEqual(self.store.get_auction_quota(uid)['used'],0)
                self.assertEqual(self.store.get_user_listings(uid),[])

    def test_auction_rarity_rules_and_harem_buttons(self):
        blocked = ('⚪ Common','🔵 Rare','🟤 Medium','🟡 Legend')
        for uid in (42,43):
            self.fixture.identify(uid)
            for index, rarity in enumerate(blocked + ('Unknown',)):
                card_id=str(uid*100+index)
                self.store.add_char(uid,{'id':card_id,'name':rarity,'rarity':rarity})
                response=self.post('/api/sell',{'char_id':card_id,'price':2000000,'listing_type':'auction'})
                self.assertEqual(response.status_code,403,rarity)
            self.assertEqual(self.store.get_balance(uid),100000)
            self.assertEqual(self.store.get_auction_quota(uid)['used'],0)
            self.assertEqual(self.store.get_user_listings(uid),[])
            self.assertEqual(len(self.store.get_harem(uid)),5)
        allowed=('💮 Mythical','⚜️ Divine','⚡️ CrossVerse','✨ Cataphract','🪞 Supreme','🌸 Special Edition','⛩️ Universal')
        for index,rarity in enumerate(allowed):
            self.post('/capacity-release',{})
            uid=100+index
            self.store.ensure_user(uid,'Seller',''); self.store.add_coins(uid,1000)
            self.store.add_char(uid,{'id':str(uid),'name':rarity,'rarity':rarity})
            self.fixture.identify(uid)
            page=self.client.get('/harem').get_data(as_text=True)
            self.assertIn('data-auctionable="true"',page)
            self.assertIn('btn-sm sell-btn',page)
            self.assertNotIn('Unsellable',page)
            self.assertNotIn('sellTypeInput',page)
            price=web.AUCTION_MIN_PRICE[rarity]
            self.assertIn(f'data-min-price="{price}"',page)
            response=self.post('/api/sell',{'char_id':str(uid),'price':price-1,'listing_type':'auction'})
            self.assertFalse(response.json['ok'])
            self.assertEqual(self.store.get_auction_quota(uid)['used'],0)
            response=self.post('/api/sell',{'char_id':str(uid),'price':price,'listing_type':'auction'})
            self.assertTrue(response.json['ok'],response.json)
            self.assertEqual(self.store.get_listing(response.json['listing_id'])['listing_type'],'auction')
            self.assertEqual(self.store.get_balance(uid),1000-web.LIST_FEE)
            self.assertEqual(self.store.get_auction_quota(uid)['used'],1)
        self.fixture.identify(43)
        page=self.client.get('/harem').get_data(as_text=True)
        self.assertNotIn('btn-sm sell-btn',page)
        self.assertIn('Collection only',page)
        self.assertNotIn('Unsellable',page)

    def test_failed_removal_refunds_quota(self):
        self.store.add_char(42, {'id':9,'name':'Card','rarity':'⚜️ Divine'})
        with patch.object(self.store,'remove_char', return_value=None):
            response=self.post('/api/sell',{'char_id':'9','price':100000,'listing_type':'auction'})
        self.assertFalse(response.json['ok'])
        self.assertEqual(self.store.get_auction_quota(42)['used'],0)

    def test_cancelled_auctions_count_and_failed_creation_refunds(self):
        card={'id':9,'name':'Card','rarity':'⚜️ Divine'}
        self.store.add_char(42,card)
        response=self.post('/api/sell',{'char_id':'9','price':100000,'listing_type':'auction'})
        self.assertTrue(response.json['ok'])
        self.assertTrue(self.post('/api/delist/'+response.json['listing_id'],{}).json['ok'])
        self.assertEqual(self.store.get_auction_quota(42)['used'],1)
        before=self.store.get_balance(42)
        with patch('web_controls.time.time',return_value=time.time()+2), patch.dict(web.app.config,{'TESTING':True}), patch.object(self.store,'add_listing',side_effect=ValueError('synthetic failure')):
            with self.assertRaises(ValueError):
                self.post('/api/sell',{'char_id':'9','price':100000,'listing_type':'auction'})
        self.assertEqual(self.store.get_auction_quota(42)['used'],1)
        self.assertEqual(self.store.get_balance(42),before)
        self.assertEqual(len(self.store.get_harem(42)),1)

    def test_profile_retains_stored_avatar(self):
        self.store._load('users')['42']['avatar']='https://example.com/avatar.png'
        profile=self.client.get('/api/profile').json
        self.assertTrue(profile['photo_url'].startswith('/media/'))
        self.assertEqual(profile['balance'],'$1000.00')

    def test_toggle_only_disables_lucky_buy(self):
        lid=self.store.add_listing(44,'Seller',{'id':8,'name':'Card','rarity':'Common'},100)
        self.assertTrue(self.post('/api/admin/lucky-buy',{'enabled':False}).json['ok'])
        page=self.client.get('/market').data
        self.assertNotIn(b'btn-lucky btn-sm lucky-btn',page)
        self.assertIn(b'btn-primary btn-sm buy-btn',page)
        before=self.store.get_balance(42)
        self.assertEqual(self.post('/api/lucky-buy/'+lid,{'stake':5}).status_code,403)
        self.assertEqual(self.store.get_balance(42),before)
        self.assertTrue(self.post('/api/buy/'+lid,{}).json['ok'])
        self.assertTrue(self.post('/api/admin/lucky-buy',{'enabled':True}).json['enabled'])
        self.fixture.identify(43)
        self.assertEqual(self.post('/api/admin/lucky-buy',{'enabled':False}).status_code,403)

    def test_ban_all_private_routes_and_unban_without_data_loss(self):
        self.assertTrue(self.post('/api/admin/web-ban',{'user_id':43,'banned':True}).json['ok'])
        # Set a verified session without visiting Home (which now correctly blocks this user).
        with self.client.session_transaction() as session: session['user_id']=43
        for path in ('/','/market','/wallet','/admin','/rocket'):
            response=self.client.get(path)
            self.assertEqual(response.status_code,403,path)
            self.assertIn(b'User Is Banned',response.data)
            self.assertNotIn(b'<button',response.data)
            self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertTrue(self.client.get('/api/profile').json['banned'])
        self.assertTrue(self.post('/api/buy/missing',{}).json['banned'])
        self.fixture.identify(42)
        self.assertEqual(self.post('/api/admin/web-ban',{'user_id':42,'banned':True}).status_code,400)
        self.assertTrue(self.post('/api/admin/web-ban',{'user_id':43,'banned':False}).json['ok'])
        self.fixture.identify(43)
        self.assertEqual(self.client.get('/').status_code,200)
        self.assertEqual(self.client.get('/api/profile').json['balance'],'$1000.00')
        self.assertEqual(self.store.get_balance(44),100000)
        self.assertEqual(self.post('/api/admin/web-ban',{'user_id':44,'banned':True}).status_code,403)

    def test_banned_verified_telegram_login_is_blocked_before_user_mutation(self):
        from scripts.test_auth_security import mini_data, BOT_TOKEN
        import auth
        auth.set_bot_token(BOT_TOKEN)
        self.addCleanup(auth.set_bot_token, '')
        self.store.set_web_ban(43, True)
        with self.client.session_transaction() as session: session.clear()
        with patch('app._bot_token', return_value=BOT_TOKEN), patch.object(self.store,'ensure_user', wraps=self.store.ensure_user) as ensure:
            response=self.client.post('/auth/webapp', json={'initData':mini_data({'id':43,'first_name':'Banned'})})
            self.assertEqual(response.status_code,403)
            self.assertTrue(response.json['banned'])
            ensure.assert_not_called()
        self.assertEqual(self.client.get('/').status_code,403)

    def test_pool_reset_scope_csrf_and_permissions(self):
        self.store.add_lucky_pool(5000); self.store.add_rocket_pool(6000)
        self.assertEqual(self.client.post('/api/admin/pool/reset',json={'pool':'lucky'}).status_code,403)
        self.assertTrue(self.post('/api/admin/pool/reset',{'pool':'lucky'}).json['ok'])
        self.assertEqual(self.store.get_lucky_pool(),0)
        self.assertEqual(self.store.get_rocket_pool(),6000)
        with patch('web_controls.time.time',return_value=time.time()+2):
            self.assertTrue(self.post('/api/admin/pool/reset',{'pool':'rocket'}).json['ok'])
        self.assertEqual(self.store.get_rocket_pool(),0)
        self.assertEqual(self.store.get_balance(42),100000)
        self.fixture.identify(43)
        self.assertEqual(self.post('/api/admin/pool/reset',{'pool':'lucky'}).status_code,403)

    def test_concurrent_request_guard_and_token_ownership(self):
        with ThreadPoolExecutor(max_workers=10) as pool:
            tokens=list(pool.map(lambda _:self.store.begin_action(43,'buy'), range(10)))
        winner=next(t for t in tokens if t)
        self.assertEqual(sum(bool(t) for t in tokens),1)
        self.store.finish_action(43,'buy','wrong-token')
        self.assertIsNone(self.store.begin_action(43,'buy'))
        self.store.finish_action(43,'buy',winner)
        self.assertTrue(self.store.begin_action(43,'buy'))
        self.assertTrue(self.store.begin_action(44,'buy'))
        token=self.store.begin_action(42,'api_sell')
        self.assertEqual(self.post('/api/sell',{}).status_code,429)
        self.store.finish_action(42,'api_sell',token)

    def test_mongo_operations_are_narrow_and_quota_update_atomic(self):
        mongo=object.__new__(db.MongoWebDB)
        mongo._web_controls=Mock(); mongo._settings=Mock(); mongo._rocket=Mock()
        mongo._web_controls.update_one.return_value.modified_count=1
        self.assertTrue(mongo.reserve_auction_slot(43))
        query,update=mongo._web_controls.update_one.call_args.args
        self.assertIn('$expr',query)
        self.assertEqual(update[0]['$set']['counts.43']['$add'][1],1)
        mongo.reset_web_pool('lucky')
        mongo._settings.update_one.assert_called_once_with({'_id':'lucky_pool'},{'$set':{'coins':0}},upsert=True)
        mongo._rocket.update_one.assert_not_called()
        mongo.set_web_ban(43,False)
        mongo._web_controls.delete_one.assert_called_once_with({'_id':'ban:43'})


class VideoTests(unittest.TestCase):
    def test_bundled_transcode_dimensions_format_cache_ranges_and_invalid_input(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source.webm'; output=Path(directory)/'converted.mp4'
            subprocess.run([get_ffmpeg_exe(),'-v','error','-f','lavfi','-i','testsrc2=size=1280x720:rate=15',
                            '-t','1','-c:v','libvpx','-threads','2',str(source)],check=True)
            transcode_480p(str(source),str(output))
            frames=read_frames(str(output))
            metadata=next(frames)
            self.assertEqual(metadata['codec'],'h264')
            self.assertLessEqual(metadata['size'][1],480)
            self.assertLessEqual(metadata['size'][0],854)
            self.assertIn('yuv420p',metadata['pix_fmt'])
            self.assertTrue(next(frames))  # Decode a real output frame with the bundled binary.
            frames.close()
            self.assertLessEqual(output.stat().st_size,4*1024*1024)
            webm=Path(directory)/'converted.webm'
            transcode_480p(str(source),str(webm),'webm')
            frames=read_frames(str(webm)); metadata=next(frames)
            self.assertEqual(metadata['codec'],'vp9'); self.assertTrue(next(frames)); frames.close()
            raw=output.read_bytes(); self.assertLess(raw.index(b'moov'),raw.index(b'mdat'))
            response=Mock(status_code=200,headers={'Content-Type':'video/webm'})
            response.iter_content.return_value=[source.read_bytes()]
            client=web.app.test_client()
            url='/media/'+web._media_token('https://example.com/synthetic.webm')+'?q=480'
            with patch('app._MEDIA_CACHE_DIR',directory), patch('app._req.get',return_value=response) as get:
                first=client.get(url); self.assertEqual(first.status_code,200)
                self.assertEqual(first.mimetype,'video/mp4')
                second=client.get(url,headers={'Range':'bytes=0-99'})
                self.assertEqual(second.status_code,206); self.assertEqual(len(second.data),100)
                self.assertEqual(get.call_count,1)
                conditional=client.get(url,headers={'If-None-Match':first.headers['ETag']})
                self.assertEqual(conditional.status_code,304)
                conditional.close(); second.close(); first.close()
            bad=Path(directory)/'bad'; bad.write_text('invalid video')
            with self.assertRaises(ValueError): transcode_480p(str(bad),str(output))


if __name__=='__main__': unittest.main()
