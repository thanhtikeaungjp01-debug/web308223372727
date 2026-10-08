"""
web/auth.py — Telegram Login Widget verification.
https://core.telegram.org/widgets/login
"""
from __future__ import annotations
import hashlib
import hmac
import json
import re
from urllib.parse import parse_qsl
import time
from functools import wraps

from flask import session, redirect, url_for, jsonify, request

_BOT_TOKEN_HASH: bytes | None = None


def set_bot_token(token: str) -> None:
    global _BOT_TOKEN_HASH
    _BOT_TOKEN_HASH = hashlib.sha256(token.encode()).digest() if token else None


def verify_telegram_login(data: dict) -> bool:
    """Verify Telegram Login Widget callback data and return True if valid."""
    if _BOT_TOKEN_HASH is None:
        return False
    if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
        return False
    data = dict(data)
    received = data.pop("hash", "")
    if not re.fullmatch(r"[a-f0-9]{64}", received):
        return False
    if not fresh_auth_date(data.get("auth_date")) or not valid_user_id(data.get("id")):
        return False
    check_str = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    expected = hmac.new(_BOT_TOKEN_HASH, check_str.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


def fresh_auth_date(value) -> bool:
    try:
        age = time.time() - int(value)
        return -30 <= age <= 86_400
    except (ValueError, TypeError, OverflowError):
        return False


def valid_user_id(value) -> bool:
    value = str(value)
    return len(value) <= 19 and value.isascii() and value.isdigit() and 0 < int(value) < 2**63


def verify_webapp_data(init_data, token: str) -> dict | None:
    """Verify signed, recent Mini App data before reading the user's identity."""
    if not isinstance(init_data, str) or not token or len(init_data) > 16384:
        return None
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
        params = dict(pairs)
        if len(params) != len(pairs) or not fresh_auth_date(params.get("auth_date")):
            return None
        received = params.pop("hash", "")
        if not re.fullmatch(r"[a-f0-9]{64}", received):
            return None
        check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received):
            return None
        user = json.loads(params.get("user", "{}"))
        if not isinstance(user, dict) or not valid_user_id(user.get("id")):
            return None
        if any(not isinstance(user.get(k, ""), str) for k in ("first_name", "username", "photo_url")):
            return None
        return user
    except (ValueError, TypeError, OverflowError):
        return None


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
