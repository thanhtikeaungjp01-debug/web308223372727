"""
web/app.py — Waifu Market standalone web application.
Run: python web/app.py  (or python -m web.app)
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import io
import json
import os
import time
import random
import secrets
import tempfile
import subprocess
import urllib.parse
from PIL import Image, ImageOps
from functools import lru_cache
from threading import Lock
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

import requests as _req
from flask import (Flask, render_template, request, session, redirect,
                   url_for, jsonify, send_file, abort, g)

try:
    from web.db          import get_db, reset_db, usd, is_sellable, RARITY_VALUE, DAILY_RARITY_KEYS, DAILY_RARITY_UNLIMITED, _ago, LIST_FEE
    from web.auth        import verify_telegram_login, verify_webapp_data, valid_user_id, set_bot_token, login_required, api_login_required
    from web.config_store import get as _cfg, get_int as _cfg_int, save as _cfg_save, \
                                  reload as _cfg_reload, is_configured, all_config
except ModuleNotFoundError:
    from db          import get_db, reset_db, usd, is_sellable, RARITY_VALUE, DAILY_RARITY_KEYS, DAILY_RARITY_UNLIMITED, _ago, LIST_FEE  # type: ignore
    from auth        import verify_telegram_login, verify_webapp_data, valid_user_id, set_bot_token, login_required, api_login_required  # type: ignore
    from config_store import get as _cfg, get_int as _cfg_int, save as _cfg_save, \
                                  reload as _cfg_reload, is_configured, all_config  # type: ignore

IS_VERCEL = os.environ.get("VERCEL") == "1"
if IS_VERCEL:
    required = ("MONGO_URI", "BOT_TOKEN", "BOT_USERNAME", "OWNER_ID", "SESSION_SECRET")
    missing = [key for key in required if not os.environ.get(key, "").strip()]
    if missing:
        raise RuntimeError("Set Vercel environment variables: " + ", ".join(missing))
    if len(os.environ["SESSION_SECRET"].strip()) < 32:
        raise RuntimeError("SESSION_SECRET must contain at least 32 random characters.")
    if not valid_user_id(os.environ["OWNER_ID"]):
        raise RuntimeError("OWNER_ID must be a positive Telegram user ID.")

try:
    from web.display_cache import display_state
except ModuleNotFoundError:
    from display_cache import display_state

try:
    from web.video_media import transcode_480p, MAX_VIDEO_INPUT
except ModuleNotFoundError:
    from video_media import transcode_480p, MAX_VIDEO_INPUT

app = Flask(__name__, template_folder="templates", static_folder="static")
if IS_VERCEL or os.environ.get("TRUST_PROXY") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Keep CSS/JS/images in the browser cache so repeated page switches do not
# redownload the same assets. The moderate TTL avoids stale deploys.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600
app.secret_key = os.environ.get("SESSION_SECRET", os.urandom(32))
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_REFRESH_EACH_REQUEST=False,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_VERCEL or os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
    MAX_CONTENT_LENGTH=(4 * 1024 * 1024 + 64 * 1024) if IS_VERCEL else 32 * 1024 * 1024,
)
app.permanent_session_lifetime = 60 * 60 * 24 * 30
MAX_ACTIVE_USERS = 8
PRESENCE_TTL_SECONDS = 45
ROBOT_VERIFICATION_TTL_SECONDS = 24 * 60 * 60
# Card Update odds are stored as probabilities because the spin endpoint
# compares them directly with random.random(). The labels below use the
# canonical rarity names used by the database and bot.
UNIVERSAL_RARITY = "⛩️ Universal"
CARD_UPDATE_CHANCES = {
    "🟡 Legend": {
        "💮 Mythical": 0.45,
        "⚜️ Divine": 0.35,
        "✨ Cataphract": 0.10,
        "⚡️ CrossVerse": 0.04,
        "🪞 Supreme": 0.02,
        "🌸 Special Edition": 0.01,
        UNIVERSAL_RARITY: 0.00001,  # 0.001%
    },
    "💮 Mythical": {
        "⚜️ Divine": 0.40,
        "✨ Cataphract": 0.30,
        "⚡️ CrossVerse": 0.15,
        "🪞 Supreme": 0.08,
        "🌸 Special Edition": 0.03,
        UNIVERSAL_RARITY: 0.00001,  # 0.001%
    },
    "⚜️ Divine": {
        "✨ Cataphract": 0.35,
        "⚡️ CrossVerse": 0.30,
        "🪞 Supreme": 0.20,
        "🌸 Special Edition": 0.10,
        UNIVERSAL_RARITY: 0.00001,  # 0.001%
    },
    "✨ Cataphract": {
        "⚡️ CrossVerse": 0.40,
        "🪞 Supreme": 0.30,
        "🌸 Special Edition": 0.20,
        UNIVERSAL_RARITY: 0.00001,  # 0.001%
    },
    "⚡️ CrossVerse": {
        "🪞 Supreme": 0.40,
        "🌸 Special Edition": 0.30,
        UNIVERSAL_RARITY: 0.0003,  # 0.03%
    },
    "🪞 Supreme": {
        "🌸 Special Edition": 0.35,
        UNIVERSAL_RARITY: 0.001,  # 0.1%
    },
    "🌸 Special Edition": {
        UNIVERSAL_RARITY: 0.015,  # 1.5%
    },
}

# Auction starting prices are half of the requested reference card prices.
AUCTION_MIN_PRICE = {
    "⛩️ Universal": 1500000,
    "🌸 Special Edition": 600000,
    "🪞 Supreme": 200000,
    "✨ Cataphract": 105000,
    "⚡️ CrossVerse": 60000,
    "⚜️ Divine": 15000,
    "💮 Mythical": 5000,
    "🟡 Legend": 3500,
    "🟤 Medium": 2500,
    "🔵 Rare": 1250,
    "⚪ Common": 750,
}


@lru_cache(maxsize=128)
def _asset_version(path, modified, size):
    with open(path, "rb") as asset:
        return hashlib.sha256(asset.read()).hexdigest()[:16]


@app.url_defaults
def version_static_assets(endpoint, values):
    if endpoint == "static" and "filename" in values:
        path = os.path.join(app.static_folder, values["filename"])
        try:
            stat = os.stat(path)
            values.setdefault("v", _asset_version(path, stat.st_mtime_ns, stat.st_size))
        except OSError:
            pass


# ── dynamic config helpers (read fresh on every call) ─────────────────────────

def _bot_token()    -> str:  return _cfg("BOT_TOKEN")
def _bot_username() -> str:  return _cfg("BOT_USERNAME").lstrip("@")
def _owner_id()     -> int:  return _cfg_int("OWNER_ID")
def _bot_api_url()  -> str:  return _cfg("BOT_API_URL").rstrip("/")
def _bot_api_key()  -> str:  return _cfg("BOT_API_KEY")

def _refresh_auth():
    """Re-initialise the Telegram login verifier whenever BOT_TOKEN changes."""
    set_bot_token(_bot_token())

_refresh_auth()

# Maintenance is an explicit owner setting; cold starts never change it.


# ── helpers ───────────────────────────────────────────────────────────────────

def _nonnegative_int(value, default=0):
    try:
        return max(0, int(value))
    except (ValueError, TypeError, OverflowError):
        return default


def current_user() -> dict | None:
    if "user_id" not in session:
        return None
    return {
        "id":         session["user_id"],
        "first_name": session.get("first_name", ""),
        "username":   session.get("username",   ""),
        "photo_url":  session.get("photo_url",  ""),
        "level":      session.get("level", 1),
        "xp":         session.get("xp", 0),
    }


def _safe_next_url(value: str | None, fallback: str = "/market") -> str:
    if not isinstance(value, (str, type(None))):
        return fallback
    value = (value or "").strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value or any(ord(c) < 32 for c in value):
        return fallback
    return value


def _robot_verified() -> bool:
    try:
        verified_at = float(session.get("robot_verified_at", 0))
    except (TypeError, ValueError):
        return False
    return verified_at > 0 and (time.time() - verified_at) < ROBOT_VERIFICATION_TTL_SECONDS


def is_owner() -> bool:
    u  = current_user()
    oid = _owner_id()
    if not u or not oid:
        return False
    # Telegram IDs are numeric, but older signed session cookies can contain
    # the same ID as a string. Normalize both sides before comparing so the
    # owner is not incorrectly denied access to /admin.
    try:
        return int(u.get("id", 0)) == int(oid)
    except (TypeError, ValueError):
        return str(u.get("id", "")).strip() == str(oid).strip()


def _pick_prize(prizes: list) -> tuple:
    if not prizes:
        return None, -1
    total = sum(float(p.get("percent", 0)) for p in prizes)
    if total <= 0:
        idx = random.randrange(len(prizes))
        return prizes[idx], idx
    r = random.uniform(0, total)
    cumulative = 0.0
    for i, p in enumerate(prizes):
        cumulative += float(p.get("percent", 0))
        if r <= cumulative:
            return p, i
    return prizes[-1], len(prizes) - 1


def _check_api_key() -> bool:
    auth = request.headers.get("Authorization", "")
    key  = auth[7:].strip() if auth.startswith("Bearer ") else request.args.get("api_key", "")
    return bool(key and get_db().verify_api_key(key))


_tg_file_info_cache: dict[str, dict] = {}

def _tg_file_info(file_id: str) -> dict | None:
    """Resolve a Telegram file_id once and keep the result in process memory."""
    token = _bot_token()
    if not token:
        return None
    key = (hashlib.sha256(token.encode()).hexdigest(), file_id)
    cached = _tg_file_info_cache.get(key)
    if cached and cached["expires_at"] > time.monotonic():
        return cached
    try:
        r = _req.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": file_id}, timeout=6,
        )
        d = r.json()
        if d.get("ok"):
            result = d["result"]
            info = {
                "url": f"https://api.telegram.org/file/bot{token}/{result['file_path']}",
                "path": result.get("file_path", ""),
                "expires_at": time.monotonic() + 3000,
            }
            if len(_tg_file_info_cache) >= 512:
                _tg_file_info_cache.pop(next(iter(_tg_file_info_cache)))
            _tg_file_info_cache[key] = info
            return info
    except Exception:
        pass
    return None


def _media_ref(value) -> str:
    """Accept the common bot media shapes and return one remote reference."""
    if isinstance(value, (list, tuple)):
        # Telegram photo sizes are normally ordered smallest → largest.
        return _media_ref(value[-1]) if value else ""
    if isinstance(value, dict):
        value = (
            value.get("file_id") or value.get("fileId") or value.get("url")
            or value.get("file_url") or value.get("path") or ""
        )
    return str(value or "").strip()


def _media_kind(value: str, hint: str = "") -> str:
    """Determine whether a bot media reference should render as image or video."""
    hint = str(hint or "").lower()
    if "video" in hint or "animation" in hint:
        return "video"
    ref = _media_ref(value).lower().split("?", 1)[0]
    # Do not call Telegram getFile while rendering a page. Explicit video
    # fields/media_type and normal file extensions are enough to classify most
    # records; the proxy resolves the file_id only when the media is requested.
    return "video" if ref.endswith((
        ".mp4", ".webm", ".mov", ".m4v", ".ogv", ".avi", ".mkv"
    )) else "image"


def _media_token(value: str) -> str:
    raw = _media_ref(value).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _media_from_token(token: str) -> str:
    return base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode("utf-8")


def media_url(value, fallback: str | None = None, hint: str = "") -> str | None:
    """Return a same-origin cached URL; the browser never fetches bot media directly."""
    ref = _media_ref(value)
    if not ref:
        return fallback
    return url_for("media_proxy", token=_media_token(ref), **({"w": 640} if _media_kind(ref, hint) == "image" else {"q": "480"}))


def char_img_url(img_url: str) -> str:
    return media_url(img_url, url_for("static", filename="img/card-placeholder.svg")) or url_for(
        "static", filename="img/card-placeholder.svg"
    )


def _format_char_media(char: dict) -> dict:
    """Normalize image/video fields from bot character records for every page."""
    image_ref = next(
        (_media_ref(char.get(k)) for k in
         ("img_url", "image_url", "photo_url", "photo", "poto", "image",
          "photo_file_id", "file_id")
         if _media_ref(char.get(k))),
        "",
    )
    video_ref = next(
        (_media_ref(char.get(k)) for k in
         ("video_url", "vd_url", "vd", "video", "video_file_id", "animation",
          "animation_file_id")
         if _media_ref(char.get(k))),
        "",
    )
    image_is_video = image_ref and _media_kind(image_ref, str(char.get("media_type", ""))) == "video"
    if video_ref or image_is_video:
        video_ref = video_ref or image_ref
        poster_ref = "" if image_is_video and not char.get("poster_url") else (
            char.get("poster_url") or image_ref
        )
        return {
            "char_img": media_url(poster_ref, None) if poster_ref and poster_ref != video_ref else "",
            "char_video": media_url(video_ref, None, hint="video"),
        }
    return {
        "char_img": media_url(image_ref, url_for("static", filename="img/card-placeholder.svg")),
        "char_video": "",
    }


def _resolve_user_name(db, user_id) -> str:
    if not user_id:
        return ""
    try:
        doc = db.get_user(int(user_id))
        if doc:
            name  = doc.get("first_name") or doc.get("username") or ""
            uname = doc.get("username", "")
            return f"{name} (@{uname})" if uname and name and name != uname else (name or uname or str(user_id))
    except Exception:
        pass
    return str(user_id)


@app.context_processor
def inject_site_logo():
    config = all_config()
    context = {
        "site_logo_url": None,
        "config": config,
        "ad_banner_url": config.get("AD_BANNER_URL", ""),
        "ad_banner_is_video": False,
        "is_owner": is_owner(),
        "rocket_show": False,
        "card_update_show": False,
        "lucky_buy_enabled": _cfg("LUCKY_BUY_ENABLED", "1") == "1",
    }
    try:
        state = display_state(get_db())
        logo = state["logo"]
        if logo:
            context["site_logo_url"] = url_for("site_logo", v=logo.get("version"))
        ad_banner = state["ad"]
        if ad_banner:
            context["ad_banner_url"] = url_for("ad_banner_media", v=ad_banner.get("version"))
            context["ad_banner_is_video"] = str(ad_banner.get("mime", "")).lower().startswith("video/")
            context["config"]["AD_BANNER_URL"] = context["ad_banner_url"]
        context.update({key: state[key] for key in ("rocket_show", "card_update_show", "wheel_show")})
    except Exception:
        pass
    return context


def _fmt_listing(lst: dict) -> dict:
    lst = dict(lst)
    lst["_id_str"]   = str(lst.get("_id", lst.get("id", "")))
    lst["price_str"] = usd(lst.get("price", 0))
    lst["is_auction"] = lst.get("listing_type", "fixed") == "auction"
    bid = lst.get("highest_bid") or {}
    lst["bid_str"] = usd(bid.get("amount", 0)) if bid else "No bids"
    lst["ago"]       = _ago(lst.get("listed_at", 0))
    lst["views"]     = lst.get("views", 0)
    char = lst.get("char", {})
    lst.update(_format_char_media(char))
    return lst


# ── before_request: show maintenance page if not configured ──────────────────

def _csrf_token():
    return session.setdefault("csrf_token", secrets.token_urlsafe(32))


app.jinja_env.globals["csrf_token"] = _csrf_token


def _banned_response():
    g.web_banned = True
    if request.path.startswith(('/api/', '/auth/')) and (request.is_json or request.path.startswith('/api/')):
        return jsonify(ok=False, banned=True, error="User Is Banned"), 403
    return render_template("banned.html"), 403


@app.before_request
def check_web_ban():
    if request.endpoint in {"static", "healthz", "media_proxy", "proxy_image", "site_logo", "welcome_media", "ad_banner_media"}:
        return
    uid = session.get("user_id")
    if uid and get_db().is_web_banned(uid):
        return _banned_response()


@app.before_request
def protect_requests():
    if request.is_json and request.method not in {"GET", "HEAD", "OPTIONS"}:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(ok=False, error="Expected a JSON object"), 400
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if request.endpoint == "api_internal_rarity_gate":
        return  # This endpoint validates its own bot API key.
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        return jsonify(ok=False, error="Cross-site request denied"), 403
    origin = request.headers.get("Origin")
    if origin:
        try:
            allowed_origin = urllib.parse.urlsplit(origin).netloc == request.host
        except ValueError:
            allowed_origin = False
        if not allowed_origin:
            return jsonify(ok=False, error="Cross-site request denied"), 403
    if request.endpoint == "webapp_auth":
        return  # Telegram HMAC authenticates the login payload.
    expected = session.get("csrf_token", "")
    submitted = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    if not expected or not hmac.compare_digest(expected.encode(), submitted.encode()):
        return jsonify(ok=False, error="Your session changed. Refresh the page and try again."), 403


@app.before_request
def prevent_duplicate_actions():
    protected = {'api_sell', 'api_buy', 'api_lucky_buy', 'api_auction_bid',
                 'api_auction_close', 'api_delist', 'api_rocket_bet', 'api_rocket_cashout',
                 'api_transfer', 'api_card_update_spin', 'api_wheel_spin',
                 'api_admin_delist', 'admin_reset_pool'}
    uid = session.get('user_id')
    if request.method in {'POST', 'DELETE'} and uid and request.endpoint in protected:
        token = get_db().begin_action(uid, request.endpoint)
        if not token:
            return jsonify(ok=False, error='Please wait before trying again.'), 429
        g.action_guard = (uid, request.endpoint, token)


@app.after_request
def release_action_guard(response):
    guard = getattr(g, 'action_guard', None)
    if guard:
        payload = response.get_json(silent=True) or {}
        cooldown = 0.8 if payload.get('ok') and request.endpoint not in {'api_card_update_spin', 'api_wheel_spin'} else 0
        get_db().finish_action(*guard, cooldown=cooldown)
    return response


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if getattr(g, "web_banned", False) or request.endpoint not in {"static", "media_proxy", "proxy_image", "site_logo", "welcome_media", "ad_banner_media", "healthz"}:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.before_request
def _check_setup():
    exempt = {"setup", "static", "maintenance", "healthz", "bot_status",
              "proxy_image", "media_proxy", "site_logo", "welcome_media", "ad_banner_media", "capacity_status",
              "capacity_release", "bot_auth", "telegram_auth", "webapp_auth",
              "logout", "index", "verify_gate"}
    if request.endpoint is None or request.endpoint in exempt:
        return
    # The owner must still be able to enter the Mini App and open Admin while
    # the public site is being configured or temporarily maintained. This is
    # especially important for Telegram WebApp sessions, which authenticate
    # immediately before redirecting to /market.
    owner_session = is_owner()
    if not is_configured() and not owner_session:
        if request.path.startswith("/api/"):
            return jsonify(ok=False, error="The market is being configured. Please try again later."), 503
        return render_template("maintenance.html"), 503
    if _cfg("MAINTENANCE_MODE", "0") == "1" and not owner_session:
        if request.path.startswith("/api/"):
            return jsonify(ok=False, error="The market is under maintenance. Please try again later."), 503
        return render_template("maintenance.html", maintenance_mode=True), 503
    if "user_id" in session and request.endpoint not in {"index", "bot_auth", "telegram_auth", "webapp_auth", "logout", "verify_gate"}:
        db = get_db()
        presence_id = session.setdefault("presence_id", secrets.token_urlsafe(18))
        admitted = db.claim_presence(presence_id, int(session["user_id"]), MAX_ACTIVE_USERS, PRESENCE_TTL_SECONDS)
        if not admitted:
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Web is full", "wait": True,
                                "active": db.active_presence_count(), "limit": MAX_ACTIVE_USERS}), 429
            return render_template("capacity_wait.html", active=db.active_presence_count(), limit=MAX_ACTIVE_USERS), 429
    # track unique daily visitors (skip bots/API calls)
    if request.method == "GET" and not request.path.startswith("/api/") and request.endpoint not in {"proxy_image", "site_logo", "bot_status", "api_bot_status"}:
        try:
            ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
            if ip:
                get_db().record_visit(ip)
        except Exception:
            pass


# ── image proxy (with disk cache) ─────────────────────────────────────────────

_MEDIA_CACHE_DIR = os.path.join(tempfile.gettempdir(), "waifu-media-cache") if IS_VERCEL else os.path.join(os.path.dirname(__file__), "data", "media_cache")
os.makedirs(_MEDIA_CACHE_DIR, exist_ok=True)
_MAX_MEDIA_BYTES = (4 if IS_VERCEL else 256) * 1024 * 1024
_MAX_MEDIA_CACHE_BYTES = (128 if IS_VERCEL else 1024) * 1024 * 1024

_NO_IMAGE_PATH = os.path.join(os.path.dirname(__file__), "static", "img", "card-placeholder.svg")

def _serve_no_image():
    resp = send_file(_NO_IMAGE_PATH, mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Vercel-CDN-Cache-Control"] = "no-store"
    return resp


def _remove_media_cache_entry(cache_file: str) -> None:
    for path in (cache_file, cache_file + ".ct"):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _trim_media_cache(incoming_bytes: int = 0, exclude: str = "") -> None:
    """Evict least-recently-used media until the cache fits its 1GB budget."""
    entries = []
    total = 0
    try:
        names = os.listdir(_MEDIA_CACHE_DIR)
    except OSError:
        return

    for name in names:
        if name.endswith(".ct") or ".tmp." in name:
            continue
        path = os.path.join(_MEDIA_CACHE_DIR, name)
        if not os.path.isfile(path):
            continue
        try:
            size = os.path.getsize(path)
            accessed = os.path.getatime(path)
        except OSError:
            continue
        entries.append((accessed, path, size))
        total += size

    # The incoming file is not part of entries yet. Remove oldest files first.
    for accessed, path, size in sorted(entries, key=lambda item: item[0]):
        if total + incoming_bytes <= _MAX_MEDIA_CACHE_BYTES:
            break
        if path == exclude:
            continue
        _remove_media_cache_entry(path)
        total -= size


_MEDIA_LOCKS = [Lock() for _ in range(32)]


def _media_response(path, content_type, stable):
    response = send_file(path, mimetype=content_type, conditional=True)
    response.headers["Cache-Control"] = ("public, max-age=2592000, immutable" if stable
                                         else "public, max-age=300, stale-while-revalidate=60")
    response.headers["Vercel-CDN-Cache-Control"] = "public, s-maxage=" + ("2592000" if stable else "300")
    return response


def _serve_cached_media(ref: str):
    """Bounded per-instance disk cache, plus browser/CDN caching across visits."""
    ref = _media_ref(ref)
    thumbnail = request.args.get("w") == "640"
    video = request.args.get("q") == "480"
    key = hashlib.sha256((ref + ("|video480-v1:" + ("webm" if request.args.get("format") == "webm" else "mp4") if video else "|thumb640-v1" if thumbnail else "")).encode()).hexdigest()
    with _MEDIA_LOCKS[int(key[:2], 16) % len(_MEDIA_LOCKS)]:
        return _fetch_cached_media(ref, key, thumbnail, video)


def _fetch_cached_media(ref, key, thumbnail, video=False):
    stable = not ref.startswith(("http://", "https://"))
    cache_file = os.path.join(_MEDIA_CACHE_DIR, key)
    meta_file = cache_file + ".ct"
    if os.path.exists(cache_file) and os.path.exists(meta_file):
        try:
            # External image URLs may be edited in place; refresh them after 5m.
            if not stable and time.time() - os.path.getmtime(cache_file) > 300:
                raise ValueError("external media expired")
            with open(meta_file) as metadata:
                content_type = metadata.read().strip()
            stat = os.stat(cache_file)
            os.utime(cache_file, (time.time(), stat.st_mtime))
            return _media_response(cache_file, content_type, stable)
        except (OSError, ValueError):
            _remove_media_cache_entry(cache_file)

    info = _tg_file_info(ref) if stable else None
    url = (info or {}).get("url") if stable else ref
    if not url:
        if video: return jsonify(ok=False, error="Video unavailable"), 422
        return _serve_no_image()
    temporary = f"{cache_file}.tmp.{secrets.token_hex(12)}"
    response = None
    try:
        response = _req.get(url, stream=True, timeout=(4, 8) if video else (6, 20))
        if response.status_code != 200:
            raise ValueError("upstream media unavailable")
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if not content_type:
            content_type = "video/mp4" if _media_kind(ref, (info or {}).get("path", "")) == "video" else "image/jpeg"
        image_variant = thumbnail and content_type.startswith("image/")
        download_limit = MAX_VIDEO_INPUT if video else (12 * 1024 * 1024 if IS_VERCEL and image_variant else _MAX_MEDIA_BYTES)
        total = 0
        download_started = time.monotonic()
        with open(temporary, "wb") as output:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if video and time.monotonic() - download_started > 10:
                    raise ValueError("video download timeout")
                if total > download_limit:
                    raise ValueError("media download limit exceeded")
                output.write(chunk)
        if video:
            output_format = "webm" if request.args.get("format") == "webm" else "mp4"
            converted_video = temporary + "." + output_format
            transcode_480p(temporary, converted_video, output_format)
            os.replace(converted_video, temporary)
            total = os.path.getsize(temporary)
            content_type = "video/" + output_format
        elif image_variant:
            try:
                with Image.open(temporary) as original:
                    if original.width * original.height > 25_000_000:
                        raise ValueError("image dimensions too large")
                    # Preserve animated GIF/WebP media instead of freezing frame 1.
                    if not getattr(original, "is_animated", False):
                        picture = ImageOps.exif_transpose(original)
                        picture.thumbnail((640, 640), Image.Resampling.LANCZOS)
                        converted = io.BytesIO()
                        picture.convert("RGBA" if "A" in picture.getbands() else "RGB").save(converted, format="WEBP", quality=82)
                        optimized = converted.getvalue()
                        with open(temporary, "wb") as output:
                            output.write(optimized)
                        total = len(optimized)
                        content_type = "image/webp"
            except (OSError, Image.DecompressionBombError) as error:
                raise ValueError("invalid thumbnail image") from error
        if total > _MAX_MEDIA_BYTES:
            raise ValueError("media response limit exceeded")
        _trim_media_cache(incoming_bytes=total, exclude=cache_file)
        os.replace(temporary, cache_file)
        with open(meta_file, "w") as metadata:
            metadata.write(content_type)
        return _media_response(cache_file, content_type, stable)
    except (OSError, ValueError, _req.RequestException, subprocess.SubprocessError):
        if video:
            response_error = jsonify(ok=False, error="Unable to prepare this video. Try again or upload an MP4 under 5 minutes / 32 MB.")
            response_error.status_code = 422
            response_error.headers["Cache-Control"] = "no-store"
            response_error.headers["Vercel-CDN-Cache-Control"] = "no-store"
            return response_error
        return _serve_no_image()
    finally:
        if response is not None:
            response.close()
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass


@app.route("/media/<token>")
def media_proxy(token):
    try:
        ref = _media_from_token(token)
    except (ValueError, UnicodeDecodeError):
        return _serve_no_image()
    return _serve_cached_media(ref)


@app.route("/img/<path:file_id>")
def proxy_image(file_id):
    """Backward-compatible URL for older saved listings."""
    return _serve_cached_media(file_id)


# ── setup wizard ──────────────────────────────────────────────────────────────

@app.route("/setup", methods=["GET", "POST"])
def setup():
    configured = is_configured()
    if configured and not is_owner():
        return redirect(url_for("index"))

    error   = None
    success = None

    if request.method == "POST":
        fields = ["MONGO_URI", "BOT_TOKEN", "BOT_USERNAME",
                  "OWNER_ID", "DB_NAME", "BOT_API_URL", "BOT_API_KEY"]
        data = {k: request.form.get(k, "").strip() for k in fields}

        for key in ("MONGO_URI", "BOT_TOKEN", "BOT_API_KEY"):
            if not data[key] or data[key] == "set":
                data[key] = _cfg(key)
        setup_key = os.environ.get("SETUP_KEY", "")
        if not configured and (not setup_key or not secrets.compare_digest(request.form.get("setup_key", "").encode(), setup_key.encode())):
            error = "Enter the setup key configured by the site operator."
        elif not valid_user_id(data["OWNER_ID"]):
            error = "Owner ID must be a positive numeric Telegram ID."
        elif not data["MONGO_URI"] or not data["BOT_TOKEN"] or not data["OWNER_ID"]:
            error = "MONGO_URI, BOT_TOKEN and OWNER_ID are required."
        else:
            _cfg_save({k: v for k, v in data.items() if v})
            _cfg_reload()
            reset_db()
            _refresh_auth()
            return redirect(url_for("index"))

    return render_template(
        "setup.html",
        user=current_user(),
        config=all_config(),
        bot_token_set=bool(_cfg("BOT_TOKEN")),
        mongo_set=bool(_cfg("MONGO_URI")),
        configured=configured,
        error=error,
        success=success,
    )


# ── auth ──────────────────────────────────────────────────────────────────────

@app.route("/auth/bot")
def bot_auth():
    token    = request.args.get("token", "").strip()
    next_url = _safe_next_url(request.args.get("next"), "/")
    if not token:
        return redirect(url_for("index") + "?error=1")

    try:
        from waifu.webtoken import consume_token
    except ImportError:
        import sys, os as _os
        sys.path.insert(0, _os.path.dirname(_os.path.dirname(__file__)))
        try:
            from waifu.webtoken import consume_token  # type: ignore
        except ImportError:
            from webtoken import consume_token  # type: ignore

    doc = consume_token(token)
    if not doc:
        return redirect(url_for("index") + "?error=expired")

    if not valid_user_id(doc.get("user_id")):
        return redirect(url_for("index", error="expired"))
    uid   = int(doc["user_id"])
    fname = doc.get("first_name", "")
    uname = doc.get("username",   "")
    if get_db().is_web_banned(uid):
        session.clear()
        session["user_id"] = uid
        return _banned_response()
    get_db().ensure_user(uid, fname, uname)
    session.clear()
    session.permanent     = True
    session["user_id"]    = uid
    session["first_name"] = fname
    session["username"]   = uname
    session["photo_url"]  = doc.get("photo_url", "")
    return redirect(next_url)


@app.route("/auth/telegram")
def telegram_auth():
    data = dict(request.args)
    next_url = _safe_next_url(session.pop("login_next", None), "/")
    if not verify_telegram_login(data):
        return redirect(url_for("index") + "?error=1")
    uid   = int(data["id"])
    fname = data.get("first_name", "")
    uname = data.get("username",   "")
    if get_db().is_web_banned(uid):
        session.clear()
        session["user_id"] = uid
        return _banned_response()
    get_db().ensure_user(uid, fname, uname)
    session.clear()
    session.permanent     = True
    session["user_id"]    = uid
    session["first_name"] = fname
    session["username"]   = uname
    session["photo_url"]  = data.get("photo_url", "")
    return redirect(next_url)


@app.route("/auth/webapp", methods=["POST"])
def webapp_auth():
    """Validate Telegram Mini App initData and create a session."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(ok=False, error="Invalid login request"), 400
    token = _bot_token()
    if not token:
        return jsonify(ok=False, error="Telegram login is not configured yet"), 503
    tg_user = verify_webapp_data(data.get("initData"), token)
    if not tg_user:
        return jsonify(ok=False, error="Login expired or invalid. Reopen the app from Telegram."), 403
    uid = int(tg_user["id"])
    if get_db().is_web_banned(uid):
        session.clear()
        session["user_id"] = uid
        return _banned_response()
    get_db().ensure_user(uid, tg_user.get("first_name", ""), tg_user.get("username", ""))
    session.clear()
    session.permanent = True
    session.update(user_id=uid, first_name=tg_user.get("first_name", ""),
                   username=tg_user.get("username", ""), photo_url=tg_user.get("photo_url", ""))
    return jsonify(ok=True, redirect=_safe_next_url(data.get("next"), "/"))


@app.route("/logout", methods=["POST"])
def logout():
    presence_id = session.get("presence_id")
    if presence_id:
        try:
            get_db().release_presence(presence_id)
        except Exception:
            pass
    session.clear()
    return redirect(url_for("index"))


@app.route("/capacity-status")
def capacity_status():
    if "user_id" not in session:
        return jsonify({"admitted": False, "active": 0, "limit": MAX_ACTIVE_USERS})
    presence_id = session.setdefault("presence_id", secrets.token_urlsafe(18))
    db = get_db()
    admitted = db.claim_presence(presence_id, int(session["user_id"]), MAX_ACTIVE_USERS, PRESENCE_TTL_SECONDS)
    return jsonify({"admitted": bool(admitted), "active": db.active_presence_count(), "limit": MAX_ACTIVE_USERS})


@app.route("/capacity-release", methods=["POST"])
def capacity_release():
    presence_id = session.get("presence_id")
    if presence_id:
        try:
            get_db().release_presence(presence_id)
        except Exception:
            pass
    return ("", 204)


# ── robot verification ─────────────────────────────────────────────────────────
@app.route("/verify", methods=["GET", "POST"])
def verify_gate():
    return redirect(url_for("index"))


# ── pages ─────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    if request.args.get("next"):
        session["login_next"] = _safe_next_url(request.args.get("next"), "/")
    user = current_user()
    balance_str = "—"
    return render_template(
        "index.html", bot_username=_bot_username(), user=user,
        balance_str=balance_str,
        welcome_slides=[url_for("welcome_media", index=i, v=slide.get("version")) for i, slide in enumerate(display_state(get_db())["slides"])],
        is_owner=is_owner(),
    )


@app.route("/api/profile")
@api_login_required
def api_profile():
    user = current_user()
    stored = get_db().get_profile(int(user["id"])) or {}
    photo = stored.get("photo_url") or stored.get("avatar") or user.get("photo_url")
    return jsonify(ok=True, balance=usd(stored.get("coins", 0)),
                   photo_url=media_url(photo) if photo else "",
                   first_name=stored.get("first_name") or user.get("first_name", ""))


@app.route("/market")
def market():
    db       = get_db()
    page     = max(1, request.args.get("page", 1, type=int))
    per_page = 20
    rarity   = request.args.get("rarity", "").strip() or None
    search   = request.args.get("q",      "").strip()[:100] or None
    total    = db.count_listings(rarity, search, listing_type="fixed")
    pages    = max(1, (total + per_page - 1) // per_page)
    page     = min(page, pages)
    skip     = (page - 1) * per_page
    listings = [_fmt_listing(l) for l in db.get_listings(skip, per_page, rarity, search, listing_type="fixed")]
    rarities = list(RARITY_VALUE.keys())
    return render_template(
        "market.html",
        user=current_user(), listings=listings,
        page=page, pages=pages, total=total,
        rarity=rarity or "", search=search or "",
        rarities=rarities, is_owner=is_owner(),
        welcome_slides=[url_for("welcome_media", index=i, v=slide.get("version")) for i, slide in enumerate(display_state(db)["slides"])],
    )

def _settle_expired_auctions(db):
    # Bound settlement work per visit; never scan the entire marketplace.
    for listing in db.get_auctions(time.time(), limit=25, expired=True):
        db.close_auction(str(listing.get("_id", "")), listing.get("seller_id"), by_admin=True)

@app.route("/auction")
@login_required
def auction():
    db = get_db()
    _settle_expired_auctions(db)
    tab = request.args.get("tab", "active")
    if tab not in {"active", "bids", "wins"}:
        tab = "active"
    page = max(1, min(request.args.get("page", 1, type=int), 1000))
    per_page = 24
    uid = session["user_id"]
    rows = (db.get_auction_wins(uid, (page - 1) * per_page, per_page + 1) if tab == "wins" else
            db.get_auctions(time.time(), (page - 1) * per_page, per_page + 1,
                            bidder_id=uid if tab == "bids" else None))
    has_next = len(rows) > per_page
    rows = rows[:per_page]
    wins = []
    if tab == "wins":
        for row in rows:
            char = row.get("details", {}).get("char") or {}
            wins.append({"char": char, "price_str": usd(row.get("amount", 0)),
                         "ago": _ago(row.get("ts", 0)), **_format_char_media(char)})
    return render_template("auction.html", user=current_user(),
                           auctions=[] if tab == "wins" else [_fmt_listing(row) for row in rows],
                           wins=wins, tab=tab, page=page, has_next=has_next,
                           auction_floors=AUCTION_MIN_PRICE, is_owner=is_owner())


@app.route("/card-update")
def card_update_page():
    if not _card_update_allowed():
        abort(404)
    return render_template("card_update.html", user=current_user(), is_owner=is_owner())


@app.route("/harem")
@login_required
def harem():
    db  = get_db()
    uid = session["user_id"]
    raw = db.get_harem(uid)
    seen: dict[str, dict] = {}
    for c in raw:
        cid = str(c.get("id"))
        if cid not in seen:
            seen[cid] = dict(c)
            seen[cid]["count"] = 0
            seen[cid].update(_format_char_media(c))
        seen[cid]["count"] += 1
    for c in seen.values():
        c["sellable"]   = is_sellable(c.get("rarity", ""))
        c["rarity_val"] = RARITY_VALUE.get(c.get("rarity", ""), 0)
    char_list   = sorted(seen.values(), key=lambda x: x["rarity_val"], reverse=True)
    my_listings = [_fmt_listing(l) for l in db.get_user_listings(uid)]
    return render_template("harem.html",
                           user=current_user(), chars=char_list,
                           my_listings=my_listings, usd=usd)


@app.route("/wallet")
@login_required
def wallet():
    db  = get_db()
    uid = session["user_id"]
    bal = db.get_balance(uid)
    txs = db.get_transactions(uid, 10)
    names = db.get_user_names({party for tx in txs for party in (tx.get("from_id"), tx.get("to_id")) if party})
    def party_name(party):
        doc = names.get(party) or {}
        first, username = doc.get("first_name", ""), doc.get("username", "")
        return f"{first} (@{username})" if first and username and first != username else (first or username or str(party or ""))
    for tx in txs:
        tx["_id_str"]    = str(tx.get("_id", tx.get("id", "")))
        tx["ago"]        = _ago(tx.get("ts", 0))
        tx["amount_str"] = usd(tx.get("amount", 0))
        tx["direction"]  = "out" if tx.get("from_id") == uid else "in"
        tx_type = tx.get("type", "")
        det = tx.get("details", {})
        if tx_type == "buy":
            tx["label"] = "🛒 Bought" if tx["direction"] == "out" else "💰 Sold"
        elif tx_type == "transfer":
            tx["label"] = "📤 Sent"   if tx["direction"] == "out" else "📥 Received"
        elif tx_type == "sell_list":
            tx["label"] = "🏷️ Listing fee"
        elif tx_type == "delist":
            tx["label"] = "↩️ Delisted"
        else:
            tx["label"] = tx_type.replace("_", " ").title()
        from_id   = tx.get("from_id")
        to_id     = tx.get("to_id")
        from_name = (det.get("from_name") or det.get("buyer_name")  or
                     party_name(from_id) if from_id else "")
        to_name   = (det.get("to_name")   or det.get("seller_name") or
                     party_name(to_id)   if to_id   else "")
        if tx["direction"] == "out":
            tx["party_label"] = "To";   tx["party_name"] = to_name;   tx["party_id"] = to_id
        else:
            tx["party_label"] = "From"; tx["party_name"] = from_name; tx["party_id"] = from_id
        char = det.get("char")
        tx["char_name"]   = char.get("name",   "") if char else ""
        tx["char_rarity"] = char.get("rarity", "") if char else ""
    return render_template("wallet.html",
                           user=current_user(), uid=uid,
                           balance=bal, balance_str=usd(bal), txs=txs)


@app.route("/admin")
def admin():
    if not is_owner():
        abort(403)
    db       = get_db()
    # Keep the functional Top 10 listing/delist control, but do not load
    # dashboard overview statistics on every Admin page visit.
    listings = [_fmt_listing(l) for l in db.get_listings(0, 10)]
    wheel_prizes = db.get_wheel_prizes()
    wheel_show   = db.get_wheel_show()
    rocket_show  = db.get_rocket_show()
    card_update_show = db.get_card_update_show()
    daily_quota = db.get_daily_rarity_quota()
    daily_rarity_rows = [
        {"key": rarity, "limit": daily_quota["limits"].get(rarity, DAILY_RARITY_UNLIMITED),
         "used": daily_quota["used"].get(rarity, 0)}
        for rarity in DAILY_RARITY_KEYS
    ]
    rocket_pool  = db.get_rocket_pool()
    wheel_codes  = db.get_redeem_codes()
    for c in wheel_codes:
        c.pop("_id", None)
    page_config = all_config()
    if db.get_ad_banner():
        page_config["AD_BANNER_URL"] = url_for("ad_banner_media")
    return render_template("admin.html",
                           user=current_user(), listings=listings,
                           config=page_config,
                           welcome_slides=db.get_welcome_slides(),
                           bot_username=_bot_username(),
                           wheel_prizes=wheel_prizes,
                           wheel_show=wheel_show,
                            wheel_codes=wheel_codes,
                           lucky_pool_balance=usd(db.get_lucky_pool()),
                           auction_quota=db.get_auction_quota(),
                           maintenance_mode=_cfg("MAINTENANCE_MODE", "0") == "1",
                           rocket_show=rocket_show,
                           card_update_show=card_update_show,
                           daily_rarity_rows=daily_rarity_rows,
                           daily_quota_date=daily_quota["date"],
                           rocket_pool_balance=usd(rocket_pool))


# ── API: bot status proxy ─────────────────────────────────────────────────────

@app.route("/api/bot-status")
def api_bot_status():
    if not is_owner():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    api_url = _bot_api_url()
    api_key = _bot_api_key()
    if not api_url:
        return jsonify({"ok": False, "error": "BOT_API_URL not configured"})
    try:
        r = _req.get(
            f"{api_url}/api/stats",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5,
        )
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ── API: increment listing views ─────────────────────────────────────────────

@app.route("/api/view/<listing_id>", methods=["POST"])
def api_view(listing_id):
    try:
        get_db().increment_listing_views(listing_id)
    except Exception:
        pass
    return jsonify({"ok": True})


@app.route("/api/views", methods=["POST"])
def api_views():
    ids = (request.get_json(silent=True) or {}).get("ids")
    if not isinstance(ids, list) or len(ids) > 40 or any(not isinstance(value, str) or not 1 <= len(value) <= 64 for value in ids):
        return jsonify(ok=False, error="Invalid listing IDs"), 400
    if ids:
        get_db().increment_listing_views_many(ids)
    return jsonify(ok=True)


# ── API: save settings (admin) ────────────────────────────────────────────────

@app.route("/api/admin/settings", methods=["POST"])
def api_admin_settings():
    if not is_owner():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    data   = request.get_json(force=True) or {}
    fields = ["BOT_USERNAME", "OWNER_ID", "DB_NAME",
              "BOT_API_URL", "BOT_API_KEY", "MARKET_URL",
              "SUPPORT_CHAT", "UPDATE_CHAT", "AD_BANNER_URL",
              "AD_BANNER_LINK", "BRAND_ANIMATION"]
    to_save = {k: str(data[k]).strip() for k in fields if k in data and str(data[k]).strip()}
    for key, limit in (("SITE_TITLE", 40), ("SITE_SUBTITLE", 64)):
        if key not in data:
            continue
        value = data[key]
        if not isinstance(value, str):
            return jsonify(ok=False, error="Brand text must be plain text."), 400
        value = value.strip()
        if len(value) > limit or any(ord(char) < 32 for char in value):
            return jsonify(ok=False, error=f"{key} must be at most {limit} characters on one line."), 400
        if key == "SITE_TITLE" and not value:
            return jsonify(ok=False, error="App name is required."), 400
        to_save[key] = value

    if not to_save:
        return jsonify({"ok": False, "error": "Nothing to save"})
    _cfg_save(to_save)
    _cfg_reload()
    return jsonify({"ok": True})

@app.route("/api/admin/maintenance", methods=["POST"])
def api_admin_maintenance():
    if not is_owner():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", False))
    _cfg_save({"MAINTENANCE_MODE": "1" if enabled else "0"})
    _cfg_reload()
    return jsonify({"ok": True, "enabled": enabled})
@app.route("/api/admin/lucky-buy", methods=["POST"])
def admin_lucky_buy_switch():
    if not is_owner(): return jsonify(ok=False, error="Unauthorized"), 403
    enabled = (request.get_json(silent=True) or {}).get("enabled")
    if not isinstance(enabled, bool): return jsonify(ok=False, error="Expected enabled boolean"), 400
    _cfg_save({"LUCKY_BUY_ENABLED": "1" if enabled else "0"})
    return jsonify(ok=True, enabled=enabled)


@app.route('/api/admin/pool/reset', methods=['POST'])
def admin_reset_pool():
    if not is_owner(): return jsonify(ok=False, error='Unauthorized'), 403
    pool = (request.get_json(silent=True) or {}).get('pool')
    if pool not in {'lucky', 'rocket'}: return jsonify(ok=False, error='Choose a pool'), 400
    get_db().reset_web_pool(pool)
    return jsonify(ok=True, pool=pool, balance='$0.00')


@app.route("/api/admin/web-ban", methods=["POST"])
def admin_web_ban():
    if not is_owner(): return jsonify(ok=False, error="Unauthorized"), 403
    data = request.get_json(silent=True) or {}
    if not valid_user_id(data.get("user_id")) or not isinstance(data.get("banned"), bool):
        return jsonify(ok=False, error="Enter a valid Telegram user ID"), 400
    uid = int(data["user_id"])
    if uid == int(_owner_id()): return jsonify(ok=False, error="The owner cannot be banned"), 400
    get_db().set_web_ban(uid, data["banned"])
    return jsonify(ok=True, user_id=uid, banned=data["banned"])


# ── API: sell ─────────────────────────────────────────────────────────────────

@app.route("/api/sell", methods=["POST"])
@api_login_required
def api_sell():
    db      = get_db()
    uid     = session["user_id"]
    data    = request.get_json(force=True) or {}
    char_id = str(data.get("char_id", "")).strip()
    try:
        price = int(data.get("price", 0))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Invalid price"})
    if price < 1:
        return jsonify({"ok": False, "error": "Price must be at least $0.01 (1 cent)"})
    chars = db.get_harem(uid)
    char  = next((c for c in chars if str(c.get("id")) == char_id), None)
    if not char:
        return jsonify({"ok": False, "error": "Character not found in your harem"})
    listing_type = "auction" if str(data.get("listing_type", "fixed")).lower() == "auction" else "fixed"
    if listing_type != "auction" and not is_sellable(char.get("rarity", "")):
        return jsonify({"ok": False, "error": "Only CrossVerse rarity and below can be listed in the fixed market"})
    if not is_owner():
        active = len(db.get_user_listings(uid))
        if active >= 5:
            return jsonify({"ok": False, "error": f"Market limit reached — max 5 active listings ({active}/5). Delist one first."})
    if listing_type == "auction":
        auction_floor = int(AUCTION_MIN_PRICE.get(char.get("rarity", ""), 1))
        if price < auction_floor:
            return jsonify({"ok": False, "error": f"Auction for {char.get('rarity', 'this rarity')} must start at least {usd(auction_floor)} (half reference price)."})
    if db.get_balance(uid) < LIST_FEE:
        return jsonify({"ok": False, "error": f"Need {usd(LIST_FEE)} listing fee"})
    quota_month = db.reserve_auction_slot(uid) if listing_type == "auction" else None
    if listing_type == "auction" and not quota_month:
        return jsonify(ok=False, error="Monthly auction limit reached (15/15). Resets on the first day of next month (UTC)."), 429
    try:
        removed = db.remove_char(uid, char_id)
    except Exception:
        if quota_month: db.refund_auction_slot(uid, quota_month)
        raise
    if not removed:
        if quota_month: db.refund_auction_slot(uid, quota_month)
        return jsonify({"ok": False, "error": "Could not remove character — try again"})
    db.add_coins(uid, -LIST_FEE)
    seller_name = session.get("first_name", str(uid))
    try:
        duration_hours = max(1, min(168, int(data.get("duration_hours", 24)))) if listing_type == "auction" else None
    except (TypeError, ValueError):
        duration_hours = 24 if listing_type == "auction" else None
    ends_at = time.time() + duration_hours * 3600 if duration_hours else None
    try:
        lid = db.add_listing(uid, seller_name, char, price, listing_type, ends_at)
    except Exception:
        db.add_char(uid, removed)
        db.add_coins(uid, LIST_FEE)
        if quota_month: db.refund_auction_slot(uid, quota_month)
        raise
    db.log_transaction("sell_list", uid, uid, LIST_FEE,
                       {"char": char, "listing_id": lid, "price": price})
    return jsonify({"ok": True, "listing_id": lid,
                    "message": f"Listed {char.get('name')} for {usd(price)}" + (f" as a {duration_hours}h auction" if listing_type == "auction" else "")})


@app.route("/api/auction/bid/<listing_id>", methods=["POST"])
@api_login_required
def api_auction_bid(listing_id):
    data = request.get_json(silent=True) or {}
    try: amount = int(round(float(data.get("amount", 0)) * 100))
    except (TypeError, ValueError): return jsonify({"ok": False, "error": "Enter a valid bid"}), 400
    if amount < 1: return jsonify({"ok": False, "error": "Bid must be at least $0.01"}), 400
    uid = session["user_id"]
    return jsonify(get_db().place_bid(listing_id, uid, session.get("first_name", str(uid)), amount))

@app.route("/api/auction/close/<listing_id>", methods=["POST"])
@api_login_required
def api_auction_close(listing_id):
    return jsonify(get_db().close_auction(listing_id, session["user_id"], by_admin=is_owner()))

# ── API: buy ──────────────────────────────────────────────────────────────────

@app.route("/api/buy/<listing_id>", methods=["POST"])
@api_login_required
def api_buy(listing_id):
    uid    = session["user_id"]
    name   = session.get("first_name", str(uid))
    result = get_db().buy_listing(listing_id, uid, name)
    return jsonify(result)


@app.route("/api/lucky-buy/<listing_id>", methods=["POST"])
@api_login_required
def api_lucky_buy(listing_id):
    if _cfg("LUCKY_BUY_ENABLED", "1") != "1":
        return jsonify(ok=False, error="Lucky Buy is disabled"), 403
    data = request.get_json(silent=True) or {}
    try:
        stake = int(round(float(data.get("stake", 0)) * 100))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Enter a valid lucky amount"}), 400
    if stake < 500:
        return jsonify({"ok": False, "error": "Lucky Buy requires at least $5.00"}), 400
    uid = session["user_id"]
    name = session.get("first_name", str(uid))
    owner_id = _owner_id()
    if not owner_id:
        return jsonify({"ok": False, "error": "Owner wallet is not configured"}), 503
    result = get_db().lucky_buy_listing(listing_id, uid, name, owner_id, stake)
    return jsonify(result)


# ── API: delist ───────────────────────────────────────────────────────────────

@app.route("/api/delist/<listing_id>", methods=["POST"])
@api_login_required
def api_delist(listing_id):
    db      = get_db()
    uid     = session["user_id"]
    listing = db.get_listing(listing_id)
    if not listing:
        return jsonify({"ok": False, "error": "Listing not found"})
    if listing.get("seller_id") != uid and not is_owner():
        return jsonify({"ok": False, "error": "Not your listing"})
    result = db.delist_listing(listing_id, by_admin=is_owner())
    return jsonify({"ok": bool(result)})


# ── API: user lookup ──────────────────────────────────────────────────────────

@app.route("/api/user-lookup")
@api_login_required
def api_user_lookup():
    q  = request.args.get("q", "").strip()
    db = get_db()
    if not q:
        return jsonify({"ok": False, "error": "Enter a Wallet ID or @username"})
    if q.lstrip("@").isdigit():
        uid = int(q.lstrip("@"))
        doc = db.get_user(uid)
    else:
        doc = db.find_user_by_username(q.lstrip("@"))
        uid = doc["id"] if doc else 0
    if not doc:
        return jsonify({"ok": False, "error": "User not found."})
    if doc.get("id") == session["user_id"] or uid == session["user_id"]:
        return jsonify({"ok": False, "error": "You can't send coins to yourself"})
    name  = doc.get("first_name", "") or doc.get("username", "") or str(uid)
    uname = doc.get("username", "")
    display = f"{name} (@{uname})" if uname and name and name != uname else name or str(uid)
    return jsonify({"ok": True, "user_id": uid, "display_name": display})


# ── API: transfer ─────────────────────────────────────────────────────────────

@app.route("/api/transfer", methods=["POST"])
@api_login_required
def api_transfer():
    db     = get_db()
    uid    = session["user_id"]
    data   = request.get_json(force=True) or {}
    to_val = str(data.get("to", "")).strip()
    try:
        amount = int(float(data.get("amount", 0)) * 100)
    except (ValueError, TypeError, OverflowError):
        return jsonify({"ok": False, "error": "Invalid amount"})
    if to_val.lstrip("@").isdigit():
        to_id  = int(to_val.lstrip("@"))
        to_doc = db.get_user(to_id)
    else:
        to_doc = db.find_user_by_username(to_val)
        to_id  = to_doc["id"] if to_doc else 0
    if not to_doc:
        return jsonify({"ok": False, "error": "User not found"})
    fname  = session.get("first_name", str(uid))
    result = db.transfer_coins(uid, to_id, amount,
                               from_name=fname,
                               to_name=to_doc.get("first_name", str(to_id)))
    if result.get("ok"):
        result["new_balance_str"] = usd(result.get("new_balance", 0))
    return jsonify(result)


# ── API: balance ──────────────────────────────────────────────────────────────

@app.route("/api/balance")
@api_login_required
def api_balance():
    bal = get_db().get_balance(session["user_id"])
    return jsonify({"ok": True, "balance": bal, "balance_str": usd(bal)})


# ── API: admin delist ─────────────────────────────────────────────────────────

@app.route("/api/admin/delist/<listing_id>", methods=["DELETE", "POST"])
def api_admin_delist(listing_id):
    if not (_check_api_key() or is_owner()):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    result = get_db().delist_listing(listing_id, by_admin=True)
    return jsonify({"ok": bool(result)})


@app.route("/api/admin/listings")
def api_admin_listings():
    if not (_check_api_key() or is_owner()):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    listings = [_fmt_listing(l) for l in get_db().all_listings()]
    return jsonify({"ok": True, "count": len(listings), "listings": listings})


# ── logo, welcome slides, and popup ad media (database-backed) ─────────────────

ALLOWED_IMG = {"image/png", "image/jpeg", "image/gif", "image/webp"}
ALLOWED_AD_MEDIA = ALLOWED_IMG | {"video/mp4", "video/webm", "video/quicktime", "video/ogg"}
MAX_AD_MEDIA_BYTES = (4 if IS_VERCEL else 25) * 1024 * 1024
MAX_LOGO_UPLOAD_BYTES = (4 if IS_VERCEL else 5) * 1024 * 1024
MAX_WELCOME_UPLOAD_BYTES = (4 if IS_VERCEL else 8) * 1024 * 1024
MAX_WELCOME_STORED_BYTES = 180 * 1024
MAX_WELCOME_SLIDES = 5

def _stored_media_response(item):
    fingerprint = hashlib.sha256(item["data"].encode()).hexdigest()
    version = request.args.get("v")
    if version and version != fingerprint:
        abort(404)
    data = base64.b64decode(item["data"])
    if IS_VERCEL and len(data) > _MAX_MEDIA_BYTES:
        return _serve_no_image()
    response = send_file(io.BytesIO(data), mimetype=item.get("mime", "image/webp"),
                         etag=hashlib.sha256(data).hexdigest(), conditional=True)
    response.headers["Cache-Control"] = "public, max-age=2592000, immutable" if version else "public, no-cache"
    response.headers["Vercel-CDN-Cache-Control"] = "public, s-maxage=2592000" if version else "no-store"
    return response


@app.route("/site-logo")
def site_logo():
    try:
        logo = get_db().get_logo()
        if logo:
            return _stored_media_response(logo)
    except Exception:
        pass
    abort(404)


@app.route("/admin/logo", methods=["POST"])
def admin_logo_upload():
    if not is_owner():
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
    f = request.files.get("logo")
    if not f or not f.content_type or f.content_type not in ALLOWED_IMG:
        return jsonify({"ok": False, "error": "Invalid file"})
    raw = f.read(MAX_LOGO_UPLOAD_BYTES + 1)
    if len(raw) > MAX_LOGO_UPLOAD_BYTES:
        return jsonify(ok=False, error=f"Logo must be {MAX_LOGO_UPLOAD_BYTES // (1024 * 1024)} MB or smaller."), 400
    try:
        with Image.open(io.BytesIO(raw)) as logo:
            if logo.width * logo.height > 25_000_000:
                return jsonify(ok=False, error="Logo dimensions are too large."), 400
            logo.thumbnail((512, 512))
            output = io.BytesIO()
            logo.convert("RGBA").save(output, format="WEBP", quality=88)
    except (OSError, ValueError, Image.DecompressionBombError):
        return jsonify(ok=False, error="Choose a valid PNG, JPEG, GIF or WebP image."), 400
    data_b64 = base64.b64encode(output.getvalue()).decode()
    get_db().set_logo(data_b64, "image/webp")
    return jsonify({"ok": True, "url": url_for("site_logo")})


@app.route("/admin/logo/delete", methods=["POST"])
def admin_logo_delete():
    if not is_owner():
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
    get_db().delete_logo()
    return jsonify({"ok": True})


@app.route("/welcome-media/<int:index>")
def welcome_media(index):
    try:
        slide = get_db().get_welcome_slide(index)
        return _stored_media_response(slide)
    except (IndexError, KeyError, ValueError, TypeError):
        abort(404)


@app.route("/admin/welcome", methods=["POST"])
def admin_welcome_upload():
    if not is_owner():
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
    f = request.files.get("welcome")
    if not f or f.content_type not in ALLOWED_IMG:
        return jsonify({"ok": False, "error": "Please upload a PNG, JPG, GIF, or WebP image."})
    slides = get_db().get_welcome_slides()
    if len(slides) >= MAX_WELCOME_SLIDES:
        return jsonify({"ok": False, "error": "Keep up to 5 welcome images. Remove one before adding another."}), 400
    raw = f.read(MAX_WELCOME_UPLOAD_BYTES + 1)
    if not raw or len(raw) > MAX_WELCOME_UPLOAD_BYTES:
        return jsonify({"ok": False, "error": f"Image is empty or larger than {MAX_WELCOME_UPLOAD_BYTES // (1024 * 1024)} MB."})
    try:
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        image.thumbnail((1400, 700), Image.Resampling.LANCZOS)
        quality = 82
        out = io.BytesIO()
        image.save(out, format="WEBP", quality=quality, method=6, optimize=True)
        while out.tell() > MAX_WELCOME_STORED_BYTES and quality > 45:
            quality -= 7
            out = io.BytesIO()
            image.save(out, format="WEBP", quality=quality, method=6, optimize=True)
        slides.append({"data": base64.b64encode(out.getvalue()).decode(), "mime": "image/webp"})
        get_db().set_welcome_slides(slides)
        return jsonify({"ok": True, "url": url_for("welcome_media", index=len(slides) - 1), "count": len(slides)})
    except Exception:
        return jsonify({"ok": False, "error": "Could not process this image."}), 400


@app.route("/admin/welcome/<int:index>/delete", methods=["POST"])
def admin_welcome_delete(index):
    if not is_owner():
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
    slides = get_db().get_welcome_slides()
    if index < 0 or index >= len(slides):
        return jsonify({"ok": False, "error": "Welcome image not found."}), 404
    get_db().delete_welcome_slide(index)
    return jsonify({"ok": True})


@app.route("/ad-banner-media")
def ad_banner_media():
    try:
        ad = get_db().get_ad_banner()
        if ad:
            return _stored_media_response(ad)
    except Exception:
        pass
    abort(404)


@app.route("/admin/ad-banner", methods=["POST"])
def admin_ad_banner_upload():
    if not is_owner():
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
    f = request.files.get("ad_banner")
    if not f or not f.content_type or f.content_type not in ALLOWED_AD_MEDIA:
        return jsonify({"ok": False, "error": "Please upload a valid image or video file."})
    data = f.read(MAX_AD_MEDIA_BYTES + 1)
    if not data:
        return jsonify({"ok": False, "error": "The uploaded file is empty."})
    if len(data) > MAX_AD_MEDIA_BYTES:
        return jsonify({"ok": False, "error": f"File is too large. Maximum size is {MAX_AD_MEDIA_BYTES // (1024 * 1024)} MB."})
    data_b64 = base64.b64encode(data).decode()
    get_db().set_ad_banner(data_b64, f.content_type)
    return jsonify({"ok": True, "url": url_for("ad_banner_media"), "mime": f.content_type})


@app.route("/admin/ad-banner/delete", methods=["POST"])
def admin_ad_banner_delete():
    if not is_owner():
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
    get_db().delete_ad_banner()
    _cfg_save({"AD_BANNER_URL": ""})
    _cfg_reload()
    return jsonify({"ok": True})


# ── bot status ────────────────────────────────────────────────────────────────

_bot_status_cache = {}
_bot_status_lock = Lock()


@app.route("/bot-status")
def bot_status():
    api_url, api_key = _bot_api_url(), _bot_api_key()
    if not api_url or not api_key:
        return jsonify(online=False, reason="not_configured")
    cache_key = (api_url, api_key)
    with _bot_status_lock:
        cached = _bot_status_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < 30:
            return jsonify(cached[1])
        result = {"online": False, "reason": "unreachable"}
        try:
            response = _req.get(f"{api_url}/api/ping",
                               headers={"Authorization": f"Bearer {api_key}"}, timeout=5)
            if response.status_code == 200:
                result = {"online": True}
        except _req.RequestException:
            pass
        _bot_status_cache.clear()
        _bot_status_cache[cache_key] = (time.monotonic(), result)
    return jsonify(result)


# ── Rocket game ───────────────────────────────────────────────────────────────

def _rocket_allowed():
    return bool(get_db().get_rocket_show())

def _rocket_crash_profile(pool_cents: int) -> dict:
    """Published virtual-coin crash distribution; probabilities sum to 1.0."""
    pool = max(0, int(pool_cents)) / 100
    if pool < 2000:
        return {"label": "Pool under $2,000", "max_x": 2.4,
                "ranges": [(1.0, 2.4, 0.95), (2.4, 2.4, 0.05)]}
    return {"label": "Pool $2,000+", "max_x": 120.0,
            "ranges": [(1.0, 5.0, 0.95), (5.0, 13.0, 0.04), (13.0, 20.0, 0.009), (20.0, 50.0, 0.000999), (50.0, 120.0, 0.000001)]}

def _rocket_choose_crash(pool_cents: int) -> tuple[float, dict]:
    profile = _rocket_crash_profile(pool_cents)
    pick = random.random(); cursor = 0.0
    for low, high, weight in profile["ranges"]:
        cursor += weight
        if pick <= cursor:
            return round(random.uniform(low, high), 2), profile
    low, high, _ = profile["ranges"][-1]
    return round(random.uniform(low, high), 2), profile

def _rocket_settle(state, now):
    crash_multiplier = float(state.get("crash_multiplier") or state.get("multiplier") or 1.0)
    state["phase"] = "SETTLED"
    state["settled_at"] = now
    # Keep the settled result visible long enough for every client poll to
    # render the crash point and each player's final result.
    state["settled_until"] = now + 3.0
    state["multiplier"] = round(crash_multiplier, 2)
    for bet in (state.get("bets") or {}).values():
        if bet.get("status") == "ACTIVE":
            bet.update({"status": "LOST", "multiplier": round(crash_multiplier, 2), "payout": 0})
    return state

def _rocket_phase(db):
    state = db.get_rocket_state()
    now = time.time()
    if not state or state.get("phase") is None or (
        state.get("phase") == "SETTLED" and now >= float(state.get("settled_until", 0))
    ):
        history = list((state or {}).get("history") or [])
        if state and state.get("phase") == "SETTLED" and state.get("crash_multiplier") is not None:
            history.insert(0, {"round_id": state.get("round_id"), "multiplier": float(state.get("crash_multiplier", 1.0)), "settled_at": state.get("settled_at", now)})
        history = history[:10]
        round_id = secrets.token_urlsafe(10)
        state = {"round_id": round_id, "phase": "WAITING", "started_at": 0,
                 "ends_at": 0, "multiplier": 1.0, "crash_at": 0.0, "bets": {}, "history": history}
        db.set_rocket_state(state)
    elif state.get("phase") == "BETTING_OPEN" and now >= float(state.get("ends_at", 0)):
        state["phase"] = "RUNNING"; state["started_at"] = now
        crash_multiplier, profile = _rocket_choose_crash(db.get_rocket_pool())
        state["crash_multiplier"] = crash_multiplier; state["crash_profile"] = profile
        state["crash_at"] = now + max(0.2, (crash_multiplier - 1.0) / 0.22)
        db.set_rocket_state(state)
    elif state.get("phase") == "RUNNING" and now >= float(state.get("crash_at", 0)):
        _rocket_settle(state, now); db.set_rocket_state(state)
    return state

@app.route("/rocket")
@login_required
def rocket():
    if not _rocket_allowed(): abort(404)
    return render_template("rocket.html", user=current_user(), balance=get_db().get_balance(session["user_id"]), rocket_pool=get_db().get_rocket_pool())

@app.route("/api/rocket/state")
@api_login_required
def api_rocket_state():
    db = get_db()
    if not _rocket_allowed(): return jsonify({"ok": False, "error": "Rocket is disabled"}), 404
    state = _rocket_phase(db); uid = str(session["user_id"])
    bet = state.get("bets", {}).get(uid)
    if state.get("phase") == "RUNNING":
        elapsed = max(0.0, time.time() - float(state.get("started_at", time.time())))
        state["multiplier"] = round(1.0 + elapsed * 0.22, 2)
        if time.time() >= float(state.get("crash_at", 0)):
            _rocket_settle(state, time.time()); db.set_rocket_state(state)
    multiplier = float(state.get("multiplier", 1.0))
    participants = []
    for user_key, item in (state.get("bets") or {}).items():
        profile = db.get_user(int(user_key)) or {}
        amount = int(item.get("amount", 0)); payout = int(item.get("payout", round(amount * multiplier)))
        participants.append({"user_id": int(user_key), "name": profile.get("first_name") or profile.get("username") or "Player", "amount": amount, "status": item.get("status", "ACTIVE"), "payout": payout, "multiplier": item.get("multiplier", multiplier)})
    participants.sort(key=lambda x: x["amount"], reverse=True)
    return jsonify({"ok": True, "phase": state.get("phase"), "round_id": state.get("round_id"),
                    "ends_at": state.get("ends_at"), "started_at": state.get("started_at"),
                    "multiplier": multiplier, "crash_multiplier": state.get("crash_multiplier"),
                    "crash_profile": state.get("crash_profile"), "history": (state.get("history") or [])[:10], "bet": bet, "participants": participants,
                    "balance": db.get_balance(session["user_id"]), "pool": db.get_rocket_pool(), "server_now": time.time()})

@app.route("/api/rocket/bet", methods=["POST"])
@api_login_required
def api_rocket_bet():
    db = get_db()
    if not _rocket_allowed(): return jsonify({"ok": False, "error": "Rocket is disabled"}), 404
    state = _rocket_phase(db)
    if state.get("phase") not in ("WAITING", "BETTING_OPEN"):
        return jsonify({"ok": False, "error": "Betting is closed"}), 409
    if state.get("phase") == "WAITING":
        state["phase"] = "BETTING_OPEN"; state["started_at"] = time.time(); state["ends_at"] = time.time() + 8
        db.set_rocket_state(state)
    data = request.get_json(silent=True) or {}
    try: amount = int(round(float(data.get("amount", 0)) * 100))
    except (TypeError, ValueError): amount = 0
    if amount <= 0: return jsonify({"ok": False, "error": "Enter a valid bet amount"}), 400
    uid = session["user_id"]; key = str(uid)
    if state.get("bets", {}).get(key): return jsonify({"ok": False, "error": "You already placed a bet"}), 409
    if db.get_balance(uid) < amount: return jsonify({"ok": False, "error": "Insufficient balance"}), 400
    db.add_coins(uid, -amount); db.add_rocket_pool(amount)
    state.setdefault("bets", {})[key] = {"amount": amount, "status": "ACTIVE"}; db.set_rocket_state(state)
    return jsonify({"ok": True, "bet": state["bets"][key], "balance": db.get_balance(uid), "pool": db.get_rocket_pool()})

@app.route("/api/rocket/cashout", methods=["POST"])
@api_login_required
def api_rocket_cashout():
    db = get_db()
    if not _rocket_allowed(): return jsonify({"ok": False, "error": "Rocket is disabled"}), 404
    state = _rocket_phase(db); uid = session["user_id"]; key = str(uid)
    if state.get("phase") != "RUNNING": return jsonify({"ok": False, "error": "Cashout is not available"}), 409
    bet = state.get("bets", {}).get(key)
    if not bet or bet.get("status") != "ACTIVE": return jsonify({"ok": False, "error": "No active bet"}), 409
    multiplier = round(1.0 + max(0.0, time.time() - float(state.get("started_at", time.time()))) * 0.22, 2)
    payout = int(round(int(bet["amount"]) * multiplier))
    if not db.withdraw_rocket_pool(payout): return jsonify({"ok": False, "error": "Pool cannot cover this payout"}), 409
    db.add_coins(uid, payout); bet.update({"status": "CASHED_OUT", "multiplier": multiplier, "payout": payout}); db.set_rocket_state(state)
    return jsonify({"ok": True, "multiplier": multiplier, "payout": payout, "balance": db.get_balance(uid), "pool": db.get_rocket_pool()})

@app.route("/api/admin/rocket/show", methods=["POST"])
def api_admin_rocket_show():
    if not is_owner(): return jsonify({"ok": False, "error": "Unauthorized"}), 403
    show = bool((request.get_json(silent=True) or {}).get("show", False)); get_db().set_rocket_show(show)
    return jsonify({"ok": True, "show": show})

# ── Card Update: owned card → target card roulette ────────────────────────────


def _card_update_allowed():
    return bool(get_db().get_card_update_show())


def _card_update_chance(target: dict, source: dict | None = None) -> float | None:
    """Return the configured chance for one exact source-to-target transition."""
    target_rarity = str((target or {}).get("rarity", ""))
    source_rarity = str((source or {}).get("rarity", ""))
    return CARD_UPDATE_CHANCES.get(source_rarity, {}).get(target_rarity)


def _clean_card(char: dict) -> dict:
    card = dict(char or {})
    card.pop("_id", None)
    return card


def _card_update_limit_error(usage: dict) -> str | None:
    if usage.get("daily_used", 0) >= usage.get("daily_limit", 2):
        return f"Daily Card Update limit reached ({usage.get('daily_limit', 2)}/day). Try again tomorrow."
    if usage.get("monthly_used", 0) >= usage.get("monthly_limit", 30):
        return f"Monthly Card Update limit reached ({usage.get('monthly_limit', 30)}/month)."
    return None


def _card_update_payload(char: dict) -> dict:
    card = _clean_card(char)
    card.update(_format_char_media(card))
    return {
        "id": str(card.get("id", "")),
        "name": str(card.get("name", "Unknown card")),
        "anime": str(card.get("anime", "")),
        "rarity": str(card.get("rarity", "")),
        "char_img": card.get("char_img", ""),
        "char_video": card.get("char_video", ""),
    }


def _resolve_card_update(db, uid, source_id: str, target_id: str):
    source = next((c for c in db.get_harem(uid)
                   if str(c.get("id")) == str(source_id)), None)
    if not source:
        return None, None, "The source card is not in your harem"
    if RARITY_VALUE.get(str(source.get("rarity", "")), 0) < RARITY_VALUE.get("🟡 Legend", 4):
        return None, None, "Card Update requires a Legend or higher source card"
    target = db.get_char_by_id(target_id)
    if not target:
        return None, None, f"Target card ID #{target_id} was not found"
    if str(source.get("id")) == str(target.get("id")):
        return None, None, "Source and target card IDs must be different"
    source_rarity = str(source.get("rarity", ""))
    target_rarity = str(target.get("rarity", ""))
    if target_rarity not in CARD_UPDATE_CHANCES.get(source_rarity, {}):
        return None, None, f"{source_rarity} → {target_rarity} is not an available Card Update transition"
    return source, _clean_card(target), None


@app.route("/api/card-update/preview", methods=["POST"])
@api_login_required
def api_card_update_preview():
    if not _card_update_allowed():
        return jsonify({"ok": False, "error": "Card Update is disabled"}), 404
    db = get_db()
    data = request.get_json(silent=True) or {}
    source_id = str(data.get("source_id", "")).strip()
    target_id = str(data.get("target_id", "")).strip()
    if not source_id or not target_id:
        return jsonify({"ok": False, "error": "Enter both source and target card IDs"}), 400
    uid = session["user_id"]
    source, target, error = _resolve_card_update(db, uid, source_id, target_id)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    usage = db.get_card_update_usage(uid)
    chance = _card_update_chance(target, source)
    return jsonify({"ok": True, "chance": chance,
                    "source": _card_update_payload(source),
                    "target": _card_update_payload(target),
                    "usage": usage, "owner_bypass": is_owner()})


@app.route("/api/card-update/spin", methods=["POST"])
@api_login_required
def api_card_update_spin():
    if not _card_update_allowed():
        return jsonify({"ok": False, "error": "Card Update is disabled"}), 404
    db = get_db()
    data = request.get_json(silent=True) or {}
    source_id = str(data.get("source_id", "")).strip()
    target_id = str(data.get("target_id", "")).strip()
    if not source_id or not target_id:
        return jsonify({"ok": False, "error": "Enter both source and target card IDs"}), 400
    uid = session["user_id"]
    source, target, error = _resolve_card_update(db, uid, source_id, target_id)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    owner_bypass = is_owner()
    usage = db.get_card_update_usage(uid)
    reserved = False
    universal_quota_claimed = False
    if not owner_bypass:
        reserved, usage = db.consume_card_update(uid)
        if not reserved:
            return jsonify({"ok": False, "error": _card_update_limit_error(usage),
                            "usage": usage}), 429

    # Universal is also subject to the admin-managed daily rarity allowance.
    # Reserve a finite slot before the random roll; failed/lost attempts return
    # the slot so only an actual Universal result increments Used.
    if target.get("rarity") == UNIVERSAL_RARITY and not owner_bypass:
        quota = db.get_daily_rarity_quota()
        universal_limit = quota["limits"].get(UNIVERSAL_RARITY, 0)
        if not db.claim_daily_rarity(UNIVERSAL_RARITY):
            if reserved:
                usage = db.refund_card_update(uid)
            return jsonify({"ok": False,
                            "error": "Universal is locked or today’s Universal limit has been reached.",
                            "usage": usage,
                            "rarity_quota": db.get_daily_rarity_quota()}), 429
        universal_quota_claimed = universal_limit != DAILY_RARITY_UNLIMITED

    chance = _card_update_chance(target, source)
    won = random.random() < chance
    try:
        if won:
            changed = db.replace_char(uid, source_id, target)
            if not changed:
                if universal_quota_claimed:
                    db.release_daily_rarity(UNIVERSAL_RARITY)
                if reserved:
                    usage = db.refund_card_update(uid)
                return jsonify({"ok": False, "error": "The source card changed — no card was removed. Refresh and try again.",
                                "usage": usage,
                                "rarity_quota": db.get_daily_rarity_quota()}), 409
            message = f"🎉 Update success! {target.get('name', 'Target card')} was added to your harem."
        else:
            removed = db.remove_char(uid, source_id)
            if universal_quota_claimed:
                db.release_daily_rarity(UNIVERSAL_RARITY)
            if not removed:
                if reserved:
                    usage = db.refund_card_update(uid)
                return jsonify({"ok": False, "error": "The source card changed — no card was removed. Refresh and try again.",
                                "usage": usage,
                                "rarity_quota": db.get_daily_rarity_quota()}), 409
            message = f"💨 Update failed. {source.get('name', 'The source card')} disappeared."
    except Exception:
        app.logger.exception("Card Update mutation failed for user %s", uid)
        if universal_quota_claimed:
            try:
                db.release_daily_rarity(UNIVERSAL_RARITY)
            except Exception:
                app.logger.exception("Universal rarity quota refund failed for user %s", uid)
        if reserved:
            try:
                usage = db.refund_card_update(uid)
            except Exception:
                app.logger.exception("Card Update usage refund failed for user %s", uid)
        return jsonify({"ok": False, "error": "Update could not be completed — no card was intentionally changed. Please try again.",
                        "usage": usage,
                        "rarity_quota": db.get_daily_rarity_quota()}), 500

    try:
        db.log_transaction("card_update", uid, uid, 0, {
            "source_card": _clean_card(source),
            "target_card": target,
            "won": won,
            "chance": chance,
        })
    except Exception:
        pass
    return jsonify({"ok": True, "won": won,
                    "winner_idx": 0 if won else 1,
                    "chance": chance,
                    "source": _card_update_payload(source),
                    "target": _card_update_payload(target),
                    "message": message,
                    "usage": db.get_card_update_usage(uid),
                    "rarity_quota": db.get_daily_rarity_quota(),
                    "owner_bypass": owner_bypass})


@app.route("/api/admin/card-update/show", methods=["POST"])
def api_admin_card_update_show():
    if not is_owner():
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
    show = bool((request.get_json(silent=True) or {}).get("show", False))
    get_db().set_card_update_show(show)
    return jsonify({"ok": True, "show": show})


@app.route("/api/admin/daily-rarity", methods=["GET", "POST"])
def api_admin_daily_rarity():
    if not is_owner():
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
    db = get_db()
    if request.method == "GET":
        return jsonify({"ok": True, **db.get_daily_rarity_quota()})
    data = request.get_json(silent=True) or {}
    limits = data.get("limits")
    if not isinstance(limits, dict):
        return jsonify({"ok": False, "error": "Daily rarity limits are required"}), 400
    for rarity in DAILY_RARITY_KEYS:
        try:
            value = int(limits.get(rarity, DAILY_RARITY_UNLIMITED))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": f"Invalid limit for {rarity}"}), 400
        if value < 0 or value > DAILY_RARITY_UNLIMITED:
            return jsonify({"ok": False, "error": f"Limit for {rarity} must be between 0 and 999"}), 400
    return jsonify({"ok": True, **db.set_daily_rarity_limits(limits)})


@app.route("/api/internal/rarity-gate", methods=["POST"])
def api_internal_rarity_gate():
    """Atomically approve one rare-card spawn for a trusted bot/background caller."""
    if not _check_api_key():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    rarity = str((request.get_json(silent=True) or {}).get("rarity", "")).strip()
    if rarity not in DAILY_RARITY_KEYS:
        return jsonify({"ok": False, "error": "Unsupported rare rarity"}), 400
    db = get_db()
    allowed = db.claim_daily_rarity(rarity)
    status = db.get_daily_rarity_quota()
    return jsonify({"ok": True, "allowed": allowed, "rarity": rarity,
                    "limit": status["limits"].get(rarity, 0),
                    "used": status["used"].get(rarity, 0),
                    "date": status["date"]})


# ── Spin Wheel: public ────────────────────────────────────────────────────────

@app.route("/api/wheel/prizes")
def api_wheel_prizes():
    db = get_db()
    return jsonify({"ok": True, "prizes": db.get_wheel_prizes(), "show": db.get_wheel_show()})


@app.route("/api/wheel/spin", methods=["POST"])
@api_login_required
def api_wheel_spin():
    db = get_db()
    if not db.get_wheel_show():
        return jsonify({"ok": False, "error": "Wheel is not available"})
    data      = request.get_json(force=True) or {}
    code_text = str(data.get("code", "")).strip()
    if not code_text:
        return jsonify({"ok": False, "error": "Enter a secret code"})
    code, err = db.use_redeem_code(code_text)
    if err:
        return jsonify({"ok": False, "error": err})
    prizes = db.get_wheel_prizes()
    if not prizes:
        return jsonify({"ok": False, "error": "No prizes configured yet"})
    prize, winner_idx = _pick_prize(prizes)
    uid     = session["user_id"]
    message = ""
    ptype   = prize.get("type", "")
    if ptype == "coins":
        try:
            amount = int(round(float(prize.get("value", 0)) * 100))
        except Exception:
            amount = 0
        if amount > 0:
            db.add_coins(uid, amount)
        message = f"🎉 You won {prize.get('label', usd(amount))}! Added to your wallet."
    elif ptype == "char":
        char_id = str(prize.get("value", "")).strip()
        char    = db.get_char_by_id(char_id) if char_id else None
        if char:
            db.add_char(uid, char)
            message = f"🎉 You won {char.get('name', 'a character')}! Added to your harem."
        else:
            message = f"🎉 You won character ID #{char_id}! Contact admin to claim."
    elif ptype == "thanks":
        msg_val = str(prize.get("value", "")).strip()
        message = f"🙏 {msg_val}" if msg_val else "🙏 Thank You for spinning!"
    else:
        message = f"🎉 You won: {prize.get('label', '?')}!"
    return jsonify({"ok": True, "prize": prize, "winner_idx": winner_idx, "message": message})


# ── Spin Wheel: admin ─────────────────────────────────────────────────────────

@app.route("/api/admin/wheel/prizes", methods=["GET"])
def api_admin_get_prizes():
    if not is_owner():
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
    db = get_db()
    return jsonify({"ok": True, "prizes": db.get_wheel_prizes(), "show": db.get_wheel_show()})


@app.route("/api/admin/wheel/prizes", methods=["POST"])
def api_admin_save_prizes():
    if not is_owner():
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
    data   = request.get_json(force=True) or {}
    prizes = data.get("prizes", [])
    get_db().save_wheel_prizes(prizes)
    return jsonify({"ok": True})


@app.route("/api/admin/wheel/show", methods=["POST"])
def api_admin_wheel_show():
    if not is_owner():
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
    data = request.get_json(force=True) or {}
    show = bool(data.get("show", False))
    get_db().set_wheel_show(show)
    return jsonify({"ok": True, "show": show})


@app.route("/api/admin/wheel/codes")
def api_admin_get_codes():
    if not is_owner():
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
    codes = get_db().get_redeem_codes()
    for c in codes:
        c.pop("_id", None)
    return jsonify({"ok": True, "codes": codes})


@app.route("/api/admin/wheel/codes", methods=["POST"])
def api_admin_add_code():
    if not is_owner():
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
    data  = request.get_json(force=True) or {}
    code  = str(data.get("code", "")).strip().upper()
    try:
        limit = int(data.get("limit", 0))
    except Exception:
        limit = 0
    if not code:
        return jsonify({"ok": False, "error": "Code text is required"})
    cid = get_db().add_redeem_code(code, limit)
    return jsonify({"ok": True, "id": cid, "code": code, "limit": limit, "uses": 0})


@app.route("/api/admin/wheel/codes/<code_id>/delete", methods=["POST"])
def api_admin_delete_code(code_id):
    if not is_owner():
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
    get_db().delete_redeem_code(code_id)
    return jsonify({"ok": True})


@app.errorhandler(HTTPException)
def http_error(error):
    if request.path.startswith(("/api/", "/auth/", "/admin/")):
        return jsonify(ok=False, error=error.description), error.code
    return render_template("error.html", user=current_user(), status=error.code,
                           message=error.description), error.code


@app.context_processor
def upload_limits():
    return {"logo_upload_mb": MAX_LOGO_UPLOAD_BYTES // (1024 * 1024),
            "welcome_upload_mb": MAX_WELCOME_UPLOAD_BYTES // (1024 * 1024),
            "welcome_max_slides": MAX_WELCOME_SLIDES,
            "ad_upload_mb": MAX_AD_MEDIA_BYTES // (1024 * 1024)}


# ── deployment health check ───────────────────────────────────────────────────
@app.route("/healthz")
def healthz():
    """Return a credential-free liveness response for Render and monitors."""
    return jsonify({"status": "ok"})


# ── run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port  = int(os.environ.get("WEB_PORT", os.environ.get("PORT", "5000")))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    print(f"🌸 Waifu Market running → http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=False)
