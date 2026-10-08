"""
waifu/webtoken.py — One-time web login tokens.

When a user clicks /market, the bot generates a short-lived token (5 min)
tied to that user's Telegram ID. The token is embedded in the Web Market URL.
The website consumes the token and logs the user in automatically.

Uses MongoDB when configured, otherwise a locked local JSON store.
Bot and web must use the same database/configuration; tokens are never mirrored.
"""
from __future__ import annotations
import asyncio
import json
import os
import time
import uuid
import fcntl
from contextlib import contextmanager
from threading import RLock

try:
    from web.config_store import get as _cfg
except ModuleNotFoundError:
    from config_store import get as _cfg
from pathlib import Path

_TOKEN_TTL  = 300          # 5 minutes
_TOKEN_FILE = Path("data/web_tokens.json")


# ── File helpers (sync, safe for async call via run_in_executor) ───────────────

def _file_read() -> list:
    try:
        if _TOKEN_FILE.exists():
            return json.loads(_TOKEN_FILE.read_text())
    except Exception:
        pass
    return []


def _file_write(tokens: list) -> None:
    _TOKEN_FILE.parent.mkdir(exist_ok=True)
    temporary = _TOKEN_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(tokens, default=str))
    temporary.chmod(0o600)
    temporary.replace(_TOKEN_FILE)


_TOKEN_LOCK = RLock()


@contextmanager
def _file_lock():
    if os.environ.get("VERCEL") == "1":
        raise RuntimeError("Local token storage is unavailable on Vercel; set MONGO_URI.")
    _TOKEN_FILE.parent.mkdir(exist_ok=True)
    with _TOKEN_LOCK, _TOKEN_FILE.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _file_add(token_doc: dict) -> None:
    with _file_lock():
        now = time.time()
        tokens = [t for t in _file_read() if 0 <= now - t.get("created_at", 0) < _TOKEN_TTL]
        tokens.append(token_doc)
        _file_write(tokens)


def _mongo_add(doc: dict) -> None:
    from pymongo import MongoClient
    with MongoClient(_cfg("MONGO_URI"), serverSelectionTimeoutMS=3000) as client:
        client[_cfg("DB_NAME", "waifu_bot")]["bot_settings"].update_one(
            {"_id": "web_tokens"}, {"$push": {"tokens": doc}}, upsert=True,
        )


# ── Async API (called from the bot) ───────────────────────────────────────────

async def create_token(user_id: int, first_name: str, username: str) -> str:
    """Generate a one-time login token and persist it. Returns the token string."""
    token = uuid.uuid4().hex
    doc = {
        "token":      token,
        "user_id":    user_id,
        "first_name": first_name,
        "username":   username or "",
        "created_at": time.time(),
    }

    # Use one authoritative store. Mirroring one-time tokens permits replay.
    if _cfg("MONGO_URI"):
        await asyncio.get_running_loop().run_in_executor(None, _mongo_add, doc)
    else:
        await asyncio.get_running_loop().run_in_executor(None, _file_add, doc)

    return token


# ── Sync API (called from the web app) ────────────────────────────────────────

def consume_token(token: str) -> dict | None:
    """Consume once atomically. Never fall back to a duplicate after a DB failure."""
    if not token or len(token) > 256:
        return None
    now = time.time()
    mongo_uri = _cfg("MONGO_URI")
    if mongo_uri:
        try:
            from pymongo import MongoClient
            with MongoClient(mongo_uri, serverSelectionTimeoutMS=3000) as client:
                coll = client[_cfg("DB_NAME", "waifu_bot")]["bot_settings"]
                doc = coll.find_one_and_update(
                    {"_id": "web_tokens", "tokens": {"$elemMatch": {
                        "token": token, "created_at": {"$gt": now - _TOKEN_TTL, "$lte": now}
                    }}},
                    {"$pull": {"tokens": {"token": token}}},
                )
            if not doc:
                return None
            found = next((t for t in doc.get("tokens", []) if t.get("token") == token
                          and 0 <= now - t.get("created_at", 0) < _TOKEN_TTL), None)
            # Remove legacy mirrored copies too, before any later config change.
            if os.environ.get("VERCEL") != "1":
                with _file_lock():
                    _file_write([t for t in _file_read() if t.get("token") != token])
            return found
        except Exception:
            return None
    with _file_lock():
        tokens = _file_read()
        found = next((t for t in tokens if t.get("token") == token
                      and 0 <= now - t.get("created_at", 0) < _TOKEN_TTL), None)
        _file_write([t for t in tokens if t.get("token") != token
                     and 0 <= now - t.get("created_at", 0) < _TOKEN_TTL])
        return found
