"""
web/config_store.py — Runtime configuration store for the web app.
Priority: environment variables > data/config.json
"""
from __future__ import annotations
import json
import os
from pathlib import Path

_CONFIG_FILE = Path("data/config.json")
_cache: dict | None = None


def _load() -> dict:
    global _cache
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
    """Read from env first, fall back to config.json."""
    val = os.environ.get(key, "").strip()
    if val:
        return val
    return str(_load().get(key, default)).strip()


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(get(key, str(default)))
    except (ValueError, TypeError):
        return default


def save(data: dict) -> None:
    """Persist config values to data/config.json."""
    global _cache
    _CONFIG_FILE.parent.mkdir(exist_ok=True)
    current = _load().copy()
    current.update({k: v for k, v in data.items() if v is not None})
    _CONFIG_FILE.write_text(json.dumps(current, indent=2))
    _cache = current


def reload() -> None:
    """Force reload from disk."""
    global _cache
    _cache = None


def is_configured() -> bool:
    """Return True if minimum required config is present.
    Only requires MONGO_URI — BOT_TOKEN/OWNER_ID come from env secrets.
    """
    return bool(get("MONGO_URI"))


def all_config() -> dict:
    """Return all config values for the admin settings page."""
    return {
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
