"""Web-only restrictions and shared UTC calendar-month auction buckets."""
from datetime import datetime, timezone
from threading import RLock
import time
import secrets

MONTHLY_AUCTION_LIMIT = 15
_LOCK = RLock()


def month_key():
    return datetime.now(timezone.utc).strftime('%Y-%m')


class WebControls:
    def is_web_banned(self, user_id):
        key = f'ban:{int(user_id)}'
        if hasattr(self, '_web_controls'):
            return self._web_controls.find_one({'_id': key}, {'_id': 1}) is not None
        return bool(self._load('web_controls').get(key))

    def set_web_ban(self, user_id, banned):
        key = f'ban:{int(user_id)}'
        if hasattr(self, '_web_controls'):
            if banned:
                self._web_controls.update_one({'_id': key}, {'$set': {'user_id': int(user_id)}}, upsert=True)
            else:
                self._web_controls.delete_one({'_id': key})
        else:
            with _LOCK:
                data = self._load('web_controls')
                if banned: data[key] = {'user_id': int(user_id)}
                else: data.pop(key, None)
                self._u('web_controls', data)

    def get_auction_quota(self, user_id=None):
        month = month_key()
        key = f'auction:{month}'
        if hasattr(self, '_web_controls'):
            bucket = self._web_controls.find_one({'_id': key}) or {}
        else:
            bucket = self._load('web_controls').get(key, {})
        return {'month': month, 'limit': MONTHLY_AUCTION_LIMIT,
                'used': bucket.get('counts', {}).get(str(user_id), 0),
                'sandbox': bucket.get('sandbox', [])}

    def reserve_auction_slot(self, user_id):
        month, uid = month_key(), str(int(user_id))
        key = f'auction:{month}'
        if hasattr(self, '_web_controls'):
            from pymongo.errors import DuplicateKeyError
            try:
                self._web_controls.update_one({'_id': key}, {'$setOnInsert': {'counts': {}, 'sandbox': []}}, upsert=True)
            except DuplicateKeyError:
                pass  # Another instance created this month's bucket.
            field = f'counts.{uid}'
            result = self._web_controls.update_one(
                {'_id': key, '$expr': {'$lt': [{'$ifNull': [f'${field}', 0]}, MONTHLY_AUCTION_LIMIT]}},
                [{'$set': {field: {'$add': [{'$ifNull': [f'${field}', 0]}, 1]}}},
                 {'$set': {'sandbox': {'$cond': [{'$eq': [f'${field}', MONTHLY_AUCTION_LIMIT]},
                     {'$setUnion': [{'$ifNull': ['$sandbox', []]}, [uid]]}, {'$ifNull': ['$sandbox', []]}]}}}])
            return month if result.modified_count else None
        with _LOCK:
            data = self._load('web_controls')
            bucket = data.setdefault(key, {'counts': {}, 'sandbox': []})
            used = bucket['counts'].get(uid, 0)
            if used >= MONTHLY_AUCTION_LIMIT: return None
            bucket['counts'][uid] = used + 1
            if used + 1 == MONTHLY_AUCTION_LIMIT and uid not in bucket['sandbox']:
                bucket['sandbox'].append(uid)
            self._u('web_controls', data)
            return month

    def refund_auction_slot(self, user_id, month):
        # Refund the reservation's month even if the calendar changed meanwhile.
        uid, key = str(int(user_id)), f'auction:{month}'
        if hasattr(self, '_web_controls'):
            field = f'counts.{uid}'
            self._web_controls.update_one({'_id': key, field: {'$gt': 0}},
                {'$inc': {field: -1}, '$pull': {'sandbox': uid}})
        else:
            with _LOCK:
                data = self._load('web_controls')
                bucket = data.get(key, {})
                if bucket.get('counts', {}).get(uid, 0) > 0:
                    bucket['counts'][uid] -= 1
                    bucket['sandbox'] = [value for value in bucket.get('sandbox', []) if value != uid]
                    self._u('web_controls', data)

    def begin_action(self, user_id, action):
        """One in-flight request per identity/action across serverless instances."""
        key, token, now = f'action:{int(user_id)}:{action}', secrets.token_hex(16), time.time()
        if hasattr(self, '_web_controls'):
            from pymongo.errors import DuplicateKeyError
            try:
                result = self._web_controls.update_one(
                    {'_id': key, 'until': {'$lte': now}},
                    {'$set': {'token': token, 'until': now + 90}}, upsert=True)
                return token if result.modified_count or result.upserted_id else None
            except DuplicateKeyError:
                return None
        with _LOCK:
            data = self._load('web_controls')
            if data.get(key, {}).get('until', 0) > now: return None
            data[key] = {'token': token, 'until': now + 90}
            self._u('web_controls', data)
            return token

    def finish_action(self, user_id, action, token, cooldown=0):
        key = f'action:{int(user_id)}:{action}'
        until = time.time() + cooldown
        if hasattr(self, '_web_controls'):
            self._web_controls.update_one({'_id': key, 'token': token}, {'$set': {'until': until}})
        else:
            with _LOCK:
                data = self._load('web_controls')
                if data.get(key, {}).get('token') == token:
                    data[key]['until'] = until
                    self._u('web_controls', data)

    def reset_web_pool(self, pool):
        if pool not in {'lucky', 'rocket'}: raise ValueError('Unknown pool')
        if hasattr(self, '_web_controls'):
            collection = self._settings if pool == 'lucky' else self._rocket
            collection.update_one({'_id': 'lucky_pool' if pool == 'lucky' else 'pool'},
                                  {'$set': {'coins': 0}}, upsert=True)
        else:
            with _LOCK:
                name = 'settings' if pool == 'lucky' else 'rocket_config'
                data = self._load(name)
                if pool == 'lucky': data.setdefault('lucky_pool', {})['coins'] = 0
                else: data['pool'] = 0
                self._u(name, data)
