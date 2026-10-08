from pathlib import Path
import tempfile

import db


root = Path(tempfile.mkdtemp(prefix="waifu-card-update-"))
old_dir = db.LocalWebDB._DIR
db.LocalWebDB._DIR = root
try:
    store = db.LocalWebDB()
    source = {"id": 101, "name": "Common Source", "anime": "Test", "rarity": "⚪ Common"}
    target = {"id": 909, "name": "Supreme Target", "anime": "Test", "rarity": "🪞 Supreme"}
    store.ensure_user(7, "Tester", "tester")
    store.add_char(7, source)
    store.add_char(7, source)

    characters = {"909": target}
    (root / "characters.json").write_text(__import__("json").dumps(characters))
    assert store.get_char_by_id("909")["name"] == "Supreme Target"

    replaced = store.replace_char(7, "101", target)
    assert replaced["id"] == 101
    assert [card["id"] for card in store.get_harem(7)] == [909, 101]

    removed = store.remove_char(7, "101")
    assert removed["id"] == 101
    assert [card["id"] for card in store.get_harem(7)] == [909]

    assert store.get_card_update_show() is False
    store.set_card_update_show(True)
    assert store.get_card_update_show() is True
    print("card update local storage: ok")
finally:
    db.LocalWebDB._DIR = old_dir
