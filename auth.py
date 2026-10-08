"""
web/auth.py — Telegram Login Widget verification.
https://core.telegram.org/widgets/login
"""
from __future__ import annotations
import hashlib
import hmac
import time
from functools import wraps

from flask import session, redirect, url_for, jsonify, request

_BOT_TOKEN_HASH: bytes | None = None


def set_bot_token(token: str) -> None:
    global _BOT_TOKEN_HASH
    if token:
        _BOT_TOKEN_HASH = hashlib.sha256(token.encode()).digest()


def verify_telegram_login(data: dict) -> bool:
    """Verify Telegram Login Widget callback data and return True if valid."""
    if _BOT_TOKEN_HASH is None:
        return False
    data     = {k: v for k, v in data.items() if v}
    received = data.pop("hash", "")
    # reject stale logins (> 24 h)
    try:
        if time.time() - int(data.get("auth_date", 0)) > 86_400:
            return False
    except (ValueError, TypeError):
        return False
    check_str = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    expected  = hmac.new(_BOT_TOKEN_HASH, check_str.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


def login_required(f):
    """Page decorator — redirects to index if not logged in."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("index", next=request.path))
        return f(*args, **kwargs)
    return decorated


def api_login_required(f):
    """API decorator — returns 401 JSON if not logged in."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"ok": False, "error": "Not logged in"}), 401
        return f(*args, **kwargs)
    return decorated
