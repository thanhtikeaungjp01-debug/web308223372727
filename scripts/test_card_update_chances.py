from pathlib import Path
import json
import random
import tempfile
import time

import app as web_app
import db


EXPECTED = {
    ("🟡 Legend", "💮 Mythical"): 0.45,
    ("🟡 Legend", "⚜️ Divine"): 0.35,
    ("🟡 Legend", "✨ Cataphract"): 0.10,
    ("🟡 Legend", "⚡️ CrossVerse"): 0.04,
    ("🟡 Legend", "🪞 Supreme"): 0.02,
    ("🟡 Legend", "🌸 Special Edition"): 0.01,
    ("🟡 Legend", "⛩️ Universal"): 0.00001,
    ("💮 Mythical", "⚜️ Divine"): 0.40,
    ("💮 Mythical", "✨ Cataphract"): 0.30,
    ("💮 Mythical", "⚡️ CrossVerse"): 0.15,
    ("💮 Mythical", "🪞 Supreme"): 0.08,
    ("💮 Mythical", "🌸 Special Edition"): 0.03,
    ("💮 Mythical", "⛩️ Universal"): 0.00001,
    ("⚜️ Divine", "✨ Cataphract"): 0.35,
    ("⚜️ Divine", "⚡️ CrossVerse"): 0.30,
    ("⚜️ Divine", "🪞 Supreme"): 0.20,
    ("⚜️ Divine", "🌸 Special Edition"): 0.10,
    ("⚜️ Divine", "⛩️ Universal"): 0.00001,
    ("✨ Cataphract", "⚡️ CrossVerse"): 0.40,
    ("✨ Cataphract", "🪞 Supreme"): 0.30,
    ("✨ Cataphract", "🌸 Special Edition"): 0.20,
    ("✨ Cataphract", "⛩️ Universal"): 0.00001,
    ("⚡️ CrossVerse", "🪞 Supreme"): 0.40,
    ("⚡️ CrossVerse", "🌸 Special Edition"): 0.30,
    ("⚡️ CrossVerse", "⛩️ Universal"): 0.0003,
    ("🪞 Supreme", "🌸 Special Edition"): 0.35,
    ("🪞 Supreme", "⛩️ Universal"): 0.001,
    ("🌸 Special Edition", "⛩️ Universal"): 0.015,
}

for (source_rarity, target_rarity), expected in EXPECTED.items():
    assert web_app._card_update_chance(
        {"rarity": target_rarity}, {"rarity": source_rarity}
    ) == expected, (source_rarity, target_rarity)

root = Path(tempfile.mkdtemp(prefix="waifu-card-update-chances-"))
old_dir = db.LocalWebDB._DIR
old_db = db._db
old_is_configured = web_app.is_configured
old_is_owner = web_app.is_owner
old_random = web_app.random.random
try:
    db.LocalWebDB._DIR = root
    store = db.LocalWebDB()
    db._db = store
    web_app.is_configured = lambda: True
    web_app.is_owner = lambda: False
    client = web_app.app.test_client()

    source = {"id": 501, "name": "Special Source", "anime": "Test", "rarity": "🌸 Special Edition"}
    target = {"id": 991, "name": "Universal Target", "anime": "Test", "rarity": "⛩️ Universal"}
    (root / "characters.json").write_text(json.dumps({"991": target}))
    store.ensure_user(77, "Tester", "tester")
    store.add_char(77, source)
    store.set_card_update_show(True)
    store.set_daily_rarity_limits({
        "⚜️ Divine": 999, "⚡️ CrossVerse": 999, "✨ Cataphract": 999,
        "🪞 Supreme": 999, "🌸 Special Edition": 999, "⛩️ Universal": 1,
    })
    with client.session_transaction() as session:
        session["user_id"] = 77
        session["first_name"] = "Tester"
        session["robot_verified_at"] = time.time()

    client.get("/")
    with client.session_transaction() as session:
        client.environ_base["HTTP_X_CSRF_TOKEN"] = session["csrf_token"]

    web_app.random.random = lambda: 0.01
    win = client.post("/api/card-update/spin", json={"source_id": "501", "target_id": "991"})
    assert win.status_code == 200, win.json
    assert win.json["won"] is True
    assert win.json["chance"] == 0.015
    assert win.json["rarity_quota"]["used"]["⛩️ Universal"] == 1
    assert [card["id"] for card in store.get_harem(77)] == [991]

    store._u("card_update_usage", {"77": {"date": db._today_key(), "month": db._month_key(), "daily_used": 0, "monthly_used": 0}})
    store._u("rarity_quota", {"date": db._today_key(), "limits": {"⛩️ Universal": 1}, "used": {"⛩️ Universal": 0}})
    store.add_char(77, source)
    web_app.random.random = lambda: 0.99
    loss = client.post("/api/card-update/spin", json={"source_id": "501", "target_id": "991"})
    assert loss.status_code == 200, loss.json
    assert loss.json["won"] is False
    assert loss.json["rarity_quota"]["used"]["⛩️ Universal"] == 0
    assert store.get_harem(77) == [{"id": 991, "name": "Universal Target", "anime": "Test", "rarity": "⛩️ Universal"}]

    store._u("card_update_usage", {"77": {"date": db._today_key(), "month": db._month_key(), "daily_used": 0, "monthly_used": 0}})
    store._u("rarity_quota", {"date": db._today_key(), "limits": {"⛩️ Universal": 0}, "used": {"⛩️ Universal": 0}})
    store.add_char(77, source)
    blocked = client.post("/api/card-update/spin", json={"source_id": "501", "target_id": "991"})
    assert blocked.status_code == 429, blocked.json
    assert store.get_card_update_usage(77)["daily_used"] == 0
    assert len(store.get_harem(77)) == 2

    print("card update chances and Universal quota: ok")
finally:
    db._db = old_db
    db.LocalWebDB._DIR = old_dir
    web_app.is_configured = old_is_configured
    web_app.is_owner = old_is_owner
    web_app.random.random = old_random


