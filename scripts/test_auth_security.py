"""Credential-free regressions for login, protected writes and error boundaries."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, Mock
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as web
import auth
import db
from waifu import webtoken

BOT_TOKEN = "synthetic-test-bot-token"


def mini_data(user=None, age=0, **extra):
    payload = {"auth_date": str(int(time.time()) - age),
               "user": json.dumps(user if user is not None else {"id": 42, "first_name": "Collector"}), **extra}
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    payload["hash"] = hmac.new(secret, "\n".join(f"{k}={v}" for k, v in sorted(payload.items())).encode(), hashlib.sha256).hexdigest()
    return urlencode(payload)


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for target, value in [("db.LocalWebDB._DIR", Path(self.temp.name)), ("db._db", None),
                              ("app.is_configured", lambda: True), ("app._bot_token", lambda: BOT_TOKEN),
                              ("app._cfg", lambda k, default="": default),
                              ("waifu.webtoken._TOKEN_FILE", Path(self.temp.name) / "tokens.json"),
                              ("waifu.webtoken._cfg", lambda k, default="": default)]:
            p = patch(target, value); p.start(); self.addCleanup(p.stop)
        web.app.config.update(TESTING=True)
        auth.set_bot_token(BOT_TOKEN)
        self.client = web.app.test_client()
        self.client.get("/")

    def headers(self):
        with self.client.session_transaction() as session:
            return {"X-CSRF-Token": session["csrf_token"]}

    def login(self):
        return self.client.post("/auth/webapp", json={"initData": mini_data(), "next": "/wallet"})

    def test_valid_login_rotates_session_and_logout_requires_csrf(self):
        old = self.headers()
        with self.client.session_transaction() as session:
            session["stale"] = True
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["redirect"], "/wallet")
        with self.client.session_transaction() as session:
            self.assertEqual(session["user_id"], 42)
            self.assertNotIn("stale", session)
        self.assertEqual(self.client.get("/wallet").status_code, 200)
        self.assertNotEqual(old, self.headers())
        self.assertEqual(self.client.post("/logout").status_code, 403)
        self.assertEqual(self.client.post("/logout", headers=old).status_code, 403)
        self.assertEqual(self.client.post("/logout", headers=self.headers()).status_code, 302)
        self.assertEqual(self.client.get("/api/balance").status_code, 401)

    def test_mini_app_rejects_invalid_expired_future_and_malformed(self):
        for data in [mini_data(age=86401), mini_data(age=-600), mini_data(user={"id": "oops"}),
                     mini_data(user={"id": True}), mini_data(user=[1]), mini_data(user={"id": 42, "first_name": []}),
                     mini_data() + "&auth_date=1", mini_data() + "x", {}, "", "hash=🌸"]:
            with self.subTest(data=str(data)[:35]):
                self.assertEqual(self.client.post("/auth/webapp", json={"initData": data}).status_code, 403)
        for data in [[], 3, "oops", None]:
            self.assertEqual(self.client.post("/auth/webapp", json=data).status_code, 400)

    def test_widget_signature_and_expiry(self):
        def signed(age=0, uid="42"):
            data = {"id": uid, "first_name": "Collector", "auth_date": str(int(time.time()) - age)}
            key = hashlib.sha256(BOT_TOKEN.encode()).digest()
            data["hash"] = hmac.new(key, "\n".join(f"{k}={v}" for k, v in sorted(data.items())).encode(), hashlib.sha256).hexdigest()
            return data
        self.client.get("/?next=/harem")
        self.assertEqual(self.client.get("/auth/telegram", query_string=signed()).location, "/harem")
        for data in [signed(age=86401), signed(age=-600), signed(uid="bad"), {**signed(), "hash": "🌸"}]:
            self.assertFalse(auth.verify_telegram_login(data))
        auth.set_bot_token("")
        self.assertFalse(auth.verify_telegram_login(signed()))

    def test_csrf_origin_and_malformed_json(self):
        self.login(); self.client.get("/")
        self.assertEqual(self.client.post("/api/transfer", json={}).status_code, 403)
        self.assertEqual(self.client.post("/api/transfer", json={}, headers={**self.headers(), "Origin": "https://evil.test"}).status_code, 403)
        self.assertEqual(self.client.post("/auth/webapp", json={"initData": mini_data()}, headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        self.assertEqual(self.client.post("/api/transfer", json={}, headers={**self.headers(), "Origin": "https://[broken"}).status_code, 403)
        self.assertEqual(self.client.post("/api/sell", json=[1], headers=self.headers()).status_code, 400)
        self.assertEqual(self.client.post("/api/transfer", json={"amount": "inf"}, headers=self.headers()).json["error"], "Invalid amount")

    def test_safe_redirects_and_page_errors(self):
        for value in ["https://evil.test", "//evil.test", "/\\evil.test", "/\nevil", [], 42]:
            self.assertEqual(web._safe_next_url(value, "/"), "/")
        for page in ["bad", "-2", "99999999999999999999999999", "1"]:
            response = self.client.get("/market?page=" + page)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/missing").status_code, 404)
        with patch("app.is_configured", return_value=False):
            response = self.client.get("/api/balance")
            self.assertEqual(response.status_code, 503)
            self.assertFalse(response.json["ok"])

    def test_pages_have_no_store_and_all_templates_compile(self):
        self.assertEqual(self.client.get("/").headers["Cache-Control"], "no-store")
        for template in web.app.jinja_env.list_templates():
            web.app.jinja_env.get_template(template)
        self.login()
        for page in ["/", "/market", "/harem", "/wallet", "/auction"]:
            self.assertEqual(self.client.get(page).status_code, 200, page)
        self.assertEqual(self.client.get("/admin").status_code, 403)

    def test_legacy_profile_values_do_not_crash_dashboard(self):
        self.login()
        store = db.get_db()
        store._load("users")["42"].update(level="Collector", xp={"old": "shape"})
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_setup_retains_secrets_and_first_setup_needs_key(self):
        config = {"MONGO_URI": "mongodb://synthetic", "BOT_TOKEN": BOT_TOKEN, "BOT_API_KEY": "synthetic-key"}
        with patch("app.is_owner", return_value=True), patch("app._cfg", side_effect=lambda k, default="": config.get(k, default)), patch("app._cfg_save") as save, patch("app.reset_db"):
            result = self.client.post("/setup", data={"OWNER_ID": "42", "BOT_TOKEN": "", "MONGO_URI": "set"}, headers=self.headers())
            self.assertEqual(result.status_code, 302)
            self.assertEqual(save.call_args.args[0]["MONGO_URI"], config["MONGO_URI"])
            self.assertEqual(save.call_args.args[0]["BOT_TOKEN"], BOT_TOKEN)
        with patch("app.is_configured", return_value=False), patch.dict("os.environ", {"SETUP_KEY": "synthetic-setup-key"}), patch("app._cfg_save") as save:
            self.client.post("/setup", data={"OWNER_ID": "42"}, headers=self.headers())
            save.assert_not_called()

    def test_bot_tokens_are_consumed_once_under_concurrency(self):
        token = asyncio.run(webtoken.create_token(42, "Collector", "collector"))
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(webtoken.consume_token, [token] * 16))
        self.assertEqual(sum(bool(r) for r in results), 1)
        self.assertIsNone(webtoken.consume_token(token))

    def test_mongo_token_consumption_is_atomic_and_fails_closed(self):
        token = asyncio.run(webtoken.create_token(42, "Collector", "collector"))
        with patch("waifu.webtoken._cfg", side_effect=lambda k, default="": "mongodb://synthetic" if k == "MONGO_URI" else default), patch("pymongo.MongoClient") as factory:
            collection = factory.return_value.__enter__.return_value.__getitem__.return_value.__getitem__.return_value
            collection.find_one_and_update.return_value = {"tokens": [{"token": token, "created_at": time.time(), "user_id": 42}]}
            self.assertEqual(webtoken.consume_token(token)["user_id"], 42)
            query, update = collection.find_one_and_update.call_args.args
            self.assertEqual(query["tokens"]["$elemMatch"]["token"], token)
            self.assertEqual(update, {"$pull": {"tokens": {"token": token}}})
            self.assertEqual(webtoken._file_read(), [])
            factory.side_effect = RuntimeError("offline")
            webtoken._file_add({"token": token, "created_at": time.time(), "user_id": 42})
            self.assertIsNone(webtoken.consume_token(token))

    def test_bot_login_uses_token_and_refuses_replay(self):
        token = asyncio.run(webtoken.create_token(42, "Collector", "collector"))
        self.assertEqual(self.client.get("/auth/bot", query_string={"token": token, "next": "/wallet"}).location, "/wallet")
        other = web.app.test_client()
        self.assertIn("expired", other.get("/auth/bot", query_string={"token": token}).location)

    def test_status_cache_reuses_upstream_result(self):
        web._bot_status_cache.clear()
        with patch("app._bot_api_url", return_value="https://synthetic.test"), patch("app._bot_api_key", return_value="synthetic"), patch("app._req.get", return_value=Mock(status_code=200)) as call:
            for _ in range(3):
                self.assertTrue(self.client.get("/bot-status").json["online"])
            call.assert_called_once()

    def test_mongo_queries_escape_search_and_exclude_media_data(self):
        store = object.__new__(db.MongoWebDB)
        store._market = Mock(); store._settings = Mock(); store._DESC = -1
        store.count_listings(search="[.*")
        query = store._market.count_documents.call_args.args[0]
        self.assertEqual(query["$or"][0]["char.name"]["$regex"], r"\[\.\*")
        store.get_logo(include_data=False)
        self.assertEqual(store._settings.find_one.call_args.args[1], {"mime": 1})
        store.get_welcome_slides(include_data=False)
        self.assertEqual(store._settings.find_one.call_args.args[1], {"slides.data": 0})


if __name__ == "__main__":
    unittest.main()
