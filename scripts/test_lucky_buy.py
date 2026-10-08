from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db as db_module


def main():
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = db_module.LocalWebDB._DIR
        db_module.LocalWebDB._DIR = Path(tmp)
        try:
            store = db_module.LocalWebDB()
            store.ensure_user(1, "Buyer", "buyer")
            store.ensure_user(2, "Owner", "owner")
            store.ensure_user(3, "Seller", "seller")
            store.add_coins(1, 10000)
            store.add_coins(2, 3000)
            store.add_lucky_pool(3000)
            char = {"id": 77, "name": "Test Card", "anime": "Test", "rarity": "Common", "img_url": "https://example.com/card.jpg"}
            loss_id = store.add_listing(3, "Seller", char, 3000)
            original_random = db_module.random.random
            db_module.random.random = lambda: 0.99
            loss = store.lucky_buy_listing(loss_id, 1, "Buyer", 2, 500)
            assert loss["ok"] and not loss["won"]
            assert store.get_balance(1) == 9500 and store.get_balance(2) == 3000
            assert store.get_lucky_pool() == 3500
            assert store.get_listing(loss_id) is not None
            win_id = store.add_listing(3, "Seller", char, 3000)
            db_module.random.random = lambda: 0.0
            win = store.lucky_buy_listing(win_id, 1, "Buyer", 2, 2250)
            assert win["ok"] and win["won"] and win["chance"] == 0.75
            assert store.get_listing(win_id) is None
            assert store.get_balance(1) == 7250
            assert store.get_balance(2) == 3000
            assert store.get_balance(3) == 3000
            assert store.get_lucky_pool() == 2750
            assert any(c.get("id") == 77 for c in (store.get_user(1) or {}).get("characters", []))
            assert store.lucky_buy_listing(loss_id, 1, "Buyer", 2, 400)["ok"] is False
            poor_id = store.add_listing(3, "Seller", char, 5000)
            poor = store.lucky_buy_listing(poor_id, 1, "Buyer", 2, 500)
            assert poor["ok"] and not poor["won"] and poor["pool_ready"] is False
            assert store.get_listing(poor_id) is not None
            assert store.get_lucky_pool() == 3250
            db_module.random.random = original_random
        finally:
            db_module.LocalWebDB._DIR = old_dir
    print("Lucky Buy internal-currency tests: OK")


if __name__ == "__main__":
    main()
