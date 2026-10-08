from pathlib import Path
import tempfile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db as db_module
def main():
    with tempfile.TemporaryDirectory() as tmp:
        old = db_module.LocalWebDB._DIR; db_module.LocalWebDB._DIR = Path(tmp)
        try:
            store = db_module.LocalWebDB()
            for uid in (1, 2, 3): store.ensure_user(uid, f'U{uid}', f'u{uid}')
            store.add_coins(1, 10000); store.add_coins(2, 8000)
            char = {"id": 9, "name": "Auction Card", "anime": "Test", "rarity": "Common"}
            lid = store.add_listing(3, "Seller", char, 1000, "auction", 9999999999)
            assert store.place_bid(lid, 1, "U1", 1500)["ok"]
            assert store.get_balance(1) == 8500
            assert store.place_bid(lid, 2, "U2", 2000)["ok"]
            assert store.get_balance(1) == 10000 and store.get_balance(2) == 6000
            result = store.close_auction(lid, 3)
            assert result["ok"] and result["sold"] and store.get_balance(3) == 2000
            assert any(c.get("id") == 9 for c in store.get_harem(2))
        finally: db_module.LocalWebDB._DIR = old
    print("auction local storage: ok")
if __name__ == "__main__": main()
