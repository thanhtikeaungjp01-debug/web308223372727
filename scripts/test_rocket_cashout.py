"""Cashouts finalize only their own bet and survive concurrent round polling."""
import copy
from concurrent.futures import ThreadPoolExecutor
import time
import unittest
from unittest.mock import patch
import app as web
import db
from scripts import test_admin_branding as branding


class RocketCashoutTests(unittest.TestCase):
    def setUp(self):
        self.fixture=branding.BrandingTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store=db.get_db(); self.store.set_rocket_show(True)
        self.clients={}
        for uid in (42,43,44):
            self.store.ensure_user(uid,f'Player {uid}',''); self.store.add_coins(uid,900)
            client=web.app.test_client()
            with client.session_transaction() as session:
                session.update(user_id=uid,first_name=f'Player {uid}',csrf_token='synthetic-csrf')
            self.clients[uid]=client
        self.store.add_rocket_pool(100000)
        now=time.time()
        self.store.set_rocket_state({'round_id':'round-a','phase':'RUNNING','started_at':now-5,
            'crash_at':now+60,'crash_multiplier':15.3,'history':[],
            'bets':{str(uid):{'amount':100,'status':'ACTIVE'} for uid in self.clients}})
        # Match Mongo reads: each caller sees its own snapshot, not shared Python references.
        original=self.store.get_rocket_state
        mocked=patch.object(self.store,'get_rocket_state',side_effect=lambda:copy.deepcopy(original()))
        mocked.start(); self.addCleanup(mocked.stop)

    def cashout(self,uid=42,round_id='round-a'):
        return self.clients[uid].post('/api/rocket/cashout',json={'round_id':round_id},headers={'X-CSRF-Token':'synthetic-csrf'})

    def clear_cooldown(self,uid):
        key=f'action:{uid}:api_rocket_cashout'
        entry=self.store._load('web_controls')[key]
        self.store.finish_action(uid,'api_rocket_cashout',entry['token'])

    def test_cashout_freezes_one_bet_while_rocket_and_other_bets_continue(self):
        response=self.cashout(); self.assertTrue(response.json['ok'],response.json)
        payout=response.json['payout']; multiplier=response.json['multiplier']
        state=self.store.get_rocket_state()
        self.assertEqual(state['phase'],'RUNNING')
        self.assertEqual(state['bets']['42']['status'],'CASHED_OUT')
        self.assertEqual(state['bets']['43']['status'],'ACTIVE')
        self.assertEqual(self.store.get_balance(42),900+payout)
        self.assertEqual(self.store.get_rocket_pool(),100000-payout)
        self.clear_cooldown(42)
        replay=self.cashout();self.assertEqual(replay.json['payout'],payout)
        self.assertEqual(self.store.get_balance(42),900+payout)
        self.assertEqual(self.store.get_rocket_pool(),100000-payout)
        with patch('app.time.time',return_value=time.time()+70):
            view=self.clients[42].get('/api/rocket/state').json
        self.assertEqual(view['phase'],'SETTLED')
        self.assertEqual(view['bet']['status'],'CASHED_OUT')
        self.assertEqual(view['bet']['multiplier'],multiplier)
        self.assertEqual(self.store.get_rocket_state()['bets']['43']['status'],'LOST')

    def test_parallel_cashouts_and_polling_preserve_each_result(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures=[executor.submit(self.cashout,uid) for uid in (42,43)]
            futures.append(executor.submit(lambda:self.clients[44].get('/api/rocket/state')))
            responses=[future.result() for future in futures]
        self.assertTrue(all(r.json['ok'] for r in responses[:2]),[r.json for r in responses])
        state=self.store.get_rocket_state()
        self.assertEqual(state['bets']['42']['status'],'CASHED_OUT')
        self.assertEqual(state['bets']['43']['status'],'CASHED_OUT')
        paid=sum(r.json['payout'] for r in responses[:2])
        self.assertEqual(self.store.get_rocket_pool(),100000-paid)
        self.assertEqual(sum(self.store.get_balance(uid) for uid in (42,43)),1800+paid)

    def test_late_wrong_round_or_unfunded_cashout_never_credits(self):
        self.assertEqual(self.cashout(round_id='previous-round').status_code,409)
        self.store.reset_web_pool('rocket')
        failed=self.cashout();self.assertEqual(failed.status_code,409)
        self.assertIn('Pool',failed.json['error'])
        self.assertEqual(self.store.get_balance(42),900)
        self.assertEqual(self.store.get_rocket_state()['bets']['42']['status'],'ACTIVE')
        state=self.store.get_rocket_state();state['crash_at']=time.time()-1;self.store.set_rocket_state(state)
        self.assertEqual(self.cashout().status_code,409)
        self.assertEqual(self.store.get_rocket_state()['bets']['42']['status'],'LOST')
        self.assertEqual(self.store.get_balance(42),900)

    def test_round_lock_contention_and_release(self):
        token=self.store.begin_action(0,'rocket_round')
        response=self.clients[42].get('/api/rocket/state')
        self.assertEqual(response.status_code,503); self.assertTrue(response.json['transient'])
        self.store.finish_action(0,'rocket_round',token)
        with patch.object(self.store,'get_rocket_state',side_effect=ValueError('synthetic')), patch.dict(web.app.config,{'TESTING':True}):
            with self.assertRaises(ValueError): self.clients[42].get('/api/rocket/state')
        token=self.store.begin_action(0,'rocket_round');self.assertTrue(token)
        self.store.finish_action(0,'rocket_round',token)


if __name__=='__main__': unittest.main()
