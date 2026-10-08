from __future__ import annotations

import re

import app as web_app


old_configured = web_app.is_configured
old_cfg = web_app._cfg
try:
    web_app.is_configured = lambda: True
    web_app._cfg = lambda key, default="": default
    client = web_app.app.test_client()

    root = client.get("/")
    assert root.status_code == 200

    market = client.get("/market")
    assert market.status_code in (200, 503)

    api = client.get("/api/balance")
    assert api.status_code != 428

    verify = client.get("/verify")
    assert verify.status_code == 302 and verify.headers["Location"].endswith("/")
    print("direct access without verification gate: ok")
finally:
    web_app.is_configured = old_configured
    web_app._cfg = old_cfg
