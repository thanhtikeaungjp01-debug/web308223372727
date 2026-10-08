"""
web/config_store.py — Runtime configuration store for the web app.
Priority: environment variables > MongoDB on Vercel, local JSON elsewhere.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from threading import Lock

from flask import g, has_request_context
from pymongo import MongoClient

_CONFIG_FILE = Path("data/config.json")
_cache: dict | None = None
_mongo_client = None
_mongo_lock = Lock()
_BOOTSTRAP_KEYS = {"MONGO_URI", "DB_NAME", "SESSION_SECRET"}


def is_serverless() -> bool:
    return os.environ.get("VERCEL") == "1"


def _collection():
    """Bootstrap directly from env, avoiding config/DB import recursion."""
    global _mongo_client
    uri = os.environ.get("MONGO_URI", "").strip()
    if not uri:
        raise RuntimeError("Set MONGO_URI in Vercel environment variables.")
    with _mongo_lock:
        if _mongo_client is None:
            _mongo_client = MongoClient(uri, serverSelectionTimeoutMS=5000,
                                        connectTimeoutMS=5000, socketTimeoutMS=10000,
                                        maxPoolSize=10)
    return _mongo_client[os.environ.get("DB_NAME", "").strip() or "waifu_bot"]["bot_settings"]


def _remote_load() -> dict:
    # One read per request; a different instance's edits appear next request.
    if has_request_context() and hasattr(g, "web_config"):
        return g.web_config
    doc = _collection().find_one({"_id": "web_config"}) or {}
    values = doc.get("values", {})
    if has_request_context():
        g.web_config = values
    return values


def _load() -> dict:
    global _cache
    if is_serverless():
        return _remote_load()
    if _cache is None:
        try:
            if _CONFIG_FILE.exists():
                _cache = json.loads(_CONFIG_FILE.read_text())
            else:
                _cache = {}
        except Exception:
            _cache = {}
    return _cache


def get(key: str, default: str = "") -> str:
    """Environment values override the deployment's persistent store."""
    val = os.environ.get(key, "").strip()
    if val:
        return val
    if is_serverless() and key in _BOOTSTRAP_KEYS:
        return default
    return str(_load().get(key, default)).strip()


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(get(key, str(default)))
    except (ValueError, TypeError):
        return default


def save(data: dict) -> None:
    """Persist partial edits atomically to MongoDB on Vercel or local JSON."""
    global _cache
    if is_serverless():
        updates = {f"values.{k}": v for k, v in data.items()
                   if v is not None and k not in _BOOTSTRAP_KEYS}
        if updates:
            _collection().update_one({"_id": "web_config"}, {"$set": updates}, upsert=True)
        reload()
        return
    _CONFIG_FILE.parent.mkdir(exist_ok=True)
    current = _load().copy()
    current.update({k: v for k, v in data.items() if v is not None})
    _CONFIG_FILE.write_text(json.dumps(current, indent=2))
    _cache = current


def reload() -> None:
    """Force reload from disk."""
    global _cache
    _cache = None
    if has_request_context():
        g.pop("web_config", None)


def is_configured() -> bool:
    """Return True if minimum required config is present.
    Only requires MONGO_URI — BOT_TOKEN/OWNER_ID come from env secrets.
    """
    return bool(get("MONGO_URI"))


def all_config() -> dict:
    """Return all config values for the admin settings page."""
    return {
        "SITE_TITLE":      get("SITE_TITLE", "WAIFU."),
        "SITE_SUBTITLE":   get("SITE_SUBTITLE", "TELEGRAM CATCH BOT"),
        "BOT_USERNAME":    get("BOT_USERNAME"),
        "OWNER_ID":        get("OWNER_ID"),
        "DB_NAME":         get("DB_NAME", "waifu_bot"),
        "BOT_API_URL":     get("BOT_API_URL"),
        "BOT_API_KEY":     "",           # never expose key in plaintext
        "BOT_API_KEY_SET": bool(get("BOT_API_KEY")),
        "MARKET_URL":      get("MARKET_URL"),
        "SUPPORT_CHAT":    get("SUPPORT_CHAT"),
        "UPDATE_CHAT":     get("UPDATE_CHAT"),
        "AD_BANNER_URL":   get("AD_BANNER_URL"),
        "AD_BANNER_LINK":  get("AD_BANNER_LINK"),
        "BRAND_ANIMATION": get("BRAND_ANIMATION", "random"),
    }
