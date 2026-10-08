import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db

root = Path('/tmp/waifu-rocket-test')
root.mkdir(exist_ok=True)
old_dir = db.LocalWebDB._DIR
db.LocalWebDB._DIR = root
try:
    store = db.LocalWebDB()
    store.ensure_user(101, 'Tester', 'tester')
    store.add_coins(101, 1000)
    assert store.get_balance(101) == 1000
    assert store.get_rocket_show() is False
    store.set_rocket_show(True)
    assert store.get_rocket_show() is True
    store.add_rocket_pool(250)
    assert store.get_rocket_pool() == 250
    assert store.withdraw_rocket_pool(100) is True
    assert store.get_rocket_pool() == 150
    state = {'round_id': 'test-round', 'phase': 'BETTING_OPEN', 'ends_at': time.time() + 8, 'bets': {}}
    store.set_rocket_state(state)
    assert store.get_rocket_state()['phase'] == 'BETTING_OPEN'
    print('rocket local storage: ok')
finally:
    db.LocalWebDB._DIR = old_dir
