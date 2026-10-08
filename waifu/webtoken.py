"""
waifu/webtoken.py — One-time web login tokens.

When a user clicks /market, the bot generates a short-lived token (5 min)
tied to that user's Telegram ID. The token is embedded in the Web Market URL.
The website consumes the token and logs the user in automatically.

Works with MongoDB (primary) + data/web_tokens.json fallback so both
the bot and the web app can share tokens even without a live DB.
"""
from __future__ import annotations
import asyncio
import json
import os
import time
import uuid
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
    try:
        _TOKEN_FILE.parent.mkdir(exist_ok=True)
        _TOKEN_FILE.write_text(json.dumps(tokens, default=str))
    except Exception:
        pass


def _file_add(token_doc: dict) -> None:
    """Append a token to the JSON file, pruning expired ones."""
    now = time.time()
    tokens = [t for t in _file_read() if now - t.get("created_at", 0) < _TOKEN_TTL]
    tokens.append(token_doc)
    _file_write(tokens)


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

    # Always write to the shared JSON file so the web app can find it
    # even when MongoDB is not available.
    await asyncio.get_event_loop().run_in_executor(None, _file_add, doc)

    # Also write to MongoDB if available
    try:
        from waifu import bot_settings_collection
        await bot_settings_collection.update_one(
            {"_id": "web_tokens"},
            {"$push": {"tokens": doc}},
            upsert=True,
        )
    except Exception:
        pass

    return token


# ── Sync API (called from the web app) ────────────────────────────────────────

def consume_token(token: str) -> dict | None:
    """
    Find and delete a valid (non-expired) token.
    Returns the token document (with user_id, first_name, username) or None.
    Tries MongoDB first, falls back to JSON file.
    """
    now = time.time()

    # ── Try MongoDB ────────────────────────────────────────────────────────────
    mongo_uri = os.environ.get("MONGO_URI", "").strip()
    if mongo_uri:
        try:
            from pymongo import MongoClient
            client = MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
            db_name = os.environ.get("DB_NAME", "waifu_bot")
            coll = client[db_name]["bot_settings"]
            doc  = coll.find_one({"_id": "web_tokens"})
            tokens = (doc or {}).get("tokens", [])
            found = next(
                (t for t in tokens
                 if t["token"] == token and now - t.get("created_at", 0) < _TOKEN_TTL),
                None,
            )
            if found:
                new = [t for t in tokens if t["token"] != token]
                coll.update_one({"_id": "web_tokens"}, {"$set": {"tokens": new}})
                return found
        except Exception:
            pass

    # ── Fall back to JSON file ─────────────────────────────────────────────────
    tokens = _file_read()
    found  = next(
        (t for t in tokens
         if t["token"] == token and now - t.get("created_at", 0) < _TOKEN_TTL),
        None,
    )
    if found:
        new = [t for t in tokens if t["token"] != token]
        _file_write(new)
    return found
