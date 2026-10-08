"""Smoke-test the Waifu Market Flask service without external credentials."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app


def main() -> None:
    app.config.update(TESTING=True)
    with app.test_client() as client:
        response = client.get("/healthz")
        assert response.status_code == 200, response.status_code
        assert response.get_json() == {"status": "ok"}, response.get_data(as_text=True)
        print("healthz: ok")


if __name__ == "__main__":
    main()
