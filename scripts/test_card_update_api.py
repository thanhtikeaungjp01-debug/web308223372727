from pathlib import Path
import json
import tempfile
import time

import db
import app as web_app


root = Path(tempfile.mkdtemp(prefix="waifu-card-update-api-"))
old_dir = db.LocalWebDB._DIR
old_db = db._db
db.LocalWebDB._DIR = root
try:
    store = db.LocalWebDB()
    db._db = store
    source = {"id": 101, "name": "Legend Source", "anime": "Test", "rarity": "🟡 Legend"}
    target = {"id": 909, "name": "Supreme Target", "anime": "Test", "rarity": "🪞 Supreme"}
    (root / "characters.json").write_text(json.dumps({"909": target}))
    store.ensure_user(7, "Tester", "tester")
    store.add_char(7, source)
    store.set_card_update_show(True)
    store.set_daily_rarity_limits({
        "⚜️ Divine": 0,
        "⚡️ CrossVerse": 1,
        "✨ Cataphract": 2,
        "🪞 Supreme": 3,
        "🌸 Special Edition": 4,
        "⛩️ Universal": 999,
    })

    web_app.is_configured = lambda: True
    web_app.is_owner = lambda: True
    client = web_app.app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = 7
        session["first_name"] = "Tester"
        session["robot_verified_at"] = time.time()

    page = client.get("/card-update")
    with client.session_transaction() as session:
        client.environ_base["HTTP_X_CSRF_TOKEN"] = session["csrf_token"]
    assert page.status_code == 200
    assert b"Card Update Roulette" in page.data

    admin_page = client.get("/admin")
    assert admin_page.status_code == 200
    assert b"Daily Rare Rarity Controls" in admin_page.data
    quota = client.get("/api/admin/daily-rarity")
    assert quota.status_code == 200
    assert quota.json["limits"]["⚜️ Divine"] == 0
    assert quota.json["limits"]["⛩️ Universal"] == 999
    assert store.claim_daily_rarity("⚜️ Divine") is False
    assert store.claim_daily_rarity("⛩️ Universal") is True
    assert store.claim_daily_rarity("⚡️ CrossVerse") is True
    assert store.claim_daily_rarity("⚡️ CrossVerse") is False
    store._u("rarity_quota", {"date": "2000-01-01", "limits": quota.json["limits"], "used": {"⚡️ CrossVerse": 1}})
    assert store.get_daily_rarity_quota()["used"]["⚡️ CrossVerse"] == 0
    saved = client.post("/api/admin/daily-rarity", json={"limits": quota.json["limits"]})
    assert saved.status_code == 200

    store._u("settings", {"web_api_keys": {"keys": [{"key": "gate-key"}]}})
    gate_locked = client.post("/api/internal/rarity-gate", json={"rarity": "⚜️ Divine"}, headers={"Authorization": "Bearer gate-key"})
    assert gate_locked.status_code == 200 and gate_locked.json["allowed"] is False
    gate_unlimited = client.post("/api/internal/rarity-gate", json={"rarity": "⛩️ Universal"}, headers={"Authorization": "Bearer gate-key"})
    assert gate_unlimited.status_code == 200 and gate_unlimited.json["allowed"] is True

    store.ensure_user(8, "Low Rarity", "low")
    store.add_char(8, {"id": 102, "name": "Common Source", "anime": "Test", "rarity": "⚪ Common"})
    with client.session_transaction() as session:
        session["user_id"] = 8
    blocked_source = client.post("/api/card-update/preview", json={"source_id": "102", "target_id": "909"})
    assert blocked_source.status_code == 400
    assert "Legend" in blocked_source.json["error"]
    with client.session_transaction() as session:
        session["user_id"] = 7

    preview = client.post("/api/card-update/preview", json={"source_id": "101", "target_id": "909"})
    assert preview.status_code == 200, preview.json
    assert preview.json["source"]["name"] == "Legend Source"
    assert preview.json["target"]["rarity"] == "🪞 Supreme"
    assert preview.json["chance"] == 0.02
    assert web_app._card_update_chance({"rarity": "🪞 Supreme"}, {"rarity": "🟡 Legend"}) == 0.02
    assert web_app._card_update_chance({"rarity": "⛩️ Universal"}, {"rarity": "💮 Mythical"}) == 0.00001
    assert web_app._card_update_chance({"rarity": "⚪ Common"}, {"rarity": "🟡 Legend"}) is None

    web_app.is_owner = lambda: False
    web_app.random.random = lambda: 0.01
    win = client.post("/api/card-update/spin", json={"source_id": "101", "target_id": "909"})
    assert win.status_code == 200, win.json
    assert win.json["won"] is True
    assert win.json["chance"] == 0.02
    assert win.json["usage"]["daily_used"] == 1
    assert [card["id"] for card in store.get_harem(7)] == [909]

    store.add_char(7, source)
    web_app.random.random = lambda: 0.99
    loss = client.post("/api/card-update/spin", json={"source_id": "101", "target_id": "909"})
    assert loss.status_code == 200, loss.json
    assert loss.json["won"] is False
    assert loss.json["chance"] == 0.02
    assert loss.json["usage"]["daily_used"] == 2
    assert [card["id"] for card in store.get_harem(7)] == [909]

    store.add_char(7, source)
    blocked_daily = client.post("/api/card-update/spin", json={"source_id": "101", "target_id": "909"})
    assert blocked_daily.status_code == 429
    assert "daily" in blocked_daily.json["error"].lower()
    assert [card["id"] for card in store.get_harem(7)] == [909, 101]

    store._u("card_update_usage", {"7": {"date": db._today_key(), "month": db._month_key(), "daily_used": 0, "monthly_used": 30}})
    blocked_monthly = client.post("/api/card-update/spin", json={"source_id": "101", "target_id": "909"})
    assert blocked_monthly.status_code == 429
    assert "monthly" in blocked_monthly.json["error"].lower()
    assert [card["id"] for card in store.get_harem(7)] == [909, 101]

    store.ensure_user(9, "Failure Case", "failure")
    store.add_char(9, source)
    original_replace = store.replace_char
    store.replace_char = lambda *args, **kwargs: None
    web_app.random.random = lambda: 0.01
    with client.session_transaction() as session:
        session["user_id"] = 9
    failed_mutation = client.post("/api/card-update/spin", json={"source_id": "101", "target_id": "909"})
    assert failed_mutation.status_code == 409
    assert [card["id"] for card in store.get_harem(9)] == [101]
    assert store.get_card_update_usage(9)["daily_used"] == 0
    store.replace_char = original_replace
    with client.session_transaction() as session:
        session["user_id"] = 7

    web_app.is_owner = lambda: True
    web_app.random.random = lambda: 0.10
    owner_bypass = client.post("/api/card-update/spin", json={"source_id": "101", "target_id": "909"})
    assert owner_bypass.status_code == 200, owner_bypass.json
    assert owner_bypass.json["owner_bypass"] is True
    assert owner_bypass.json["usage"]["monthly_used"] == 30

    store.set_card_update_show(False)
    hidden_page = client.get("/card-update")
    assert hidden_page.status_code == 404
    hidden = client.post("/api/card-update/preview", json={"source_id": "101", "target_id": "909"})
    assert hidden.status_code == 404
    print("card update API: ok")
finally:
    db._db = old_db
    db.LocalWebDB._DIR = old_dir
