from pathlib import Path
import tempfile
import time
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db as db_module


def main():
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = db_module.LocalWebDB._DIR
        db_module.LocalWebDB._DIR = Path(tmp)
        try:
            store = db_module.LocalWebDB()
            for index in range(8):
                assert store.claim_presence(f"presence-{index}", index + 1, 8, 45)
            assert store.active_presence_count() == 8
            assert not store.claim_presence("presence-9", 9, 8, 45)
            assert store.touch_presence("presence-0", 45)
            store.release_presence("presence-0")
            assert store.active_presence_count() == 7
            assert store.claim_presence("presence-9", 9, 8, 45)
            assert store.active_presence_count() == 8
            store.release_presence("presence-9")
            assert store.active_presence_count() == 7
        finally:
            db_module.LocalWebDB._DIR = old_dir
    print("Capacity gate tests: OK")


if __name__ == "__main__":
    main()
