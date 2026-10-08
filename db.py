"""
web/db.py — Sync database layer for the web marketplace.
Uses pymongo if MONGO_URI is set, otherwise a JSON-file store.
Config is read dynamically from config_store (env vars > data/config.json).
"""
from __future__ import annotations
import json
import os
import random
import re
import time
import uuid
from pathlib import Path
from threading import RLock

# ── rarity constants ──────────────────────────────────────────────────────────

RARITY_VALUE: dict[str, int] = {
    "⛩️ Universal":       11,
    "🌸 Special Edition": 10,
    "🪞 Supreme":          9,
    "✨ Cataphract":       8,
    "⚡️ CrossVerse":       7,
    "⚜️ Divine":           6,
    "💮 Mythical":         5,
    "🟡 Legend":           4,
    "🟤 Medium":           3,
    "🔵 Rare":             2,
    "⚪ Common":           1,
}

RARITY_SELL_PRICE: dict[str, int] = {
    "⛩️ Universal":       300010,
    "🌸 Special Edition":  70040,
    "🪞 Supreme":          20060,
    "✨ Cataphract":       10030,
    "⚡️ CrossVerse":        2750,
    "⚜️ Divine":            1390,
    "💮 Mythical":           760,
    "🟡 Legend":             273,
    "🟤 Medium":             110,
    "🔵 Rare":                30,
    "⚪ Common":              15,
}

MAX_SELLABLE_VALUE = 7
LIST_FEE           = 10

# Daily rarity controls are admin-managed. A limit of 0 locks a rarity;
# 999 means unlimited and therefore uses the bot's normal rarity percentage.
DAILY_RARITY_KEYS: tuple[str, ...] = (
    "⚜️ Divine",
    "⚡️ CrossVerse",
    "✨ Cataphract",
    "🪞 Supreme",
    "🌸 Special Edition",
    "⛩️ Universal",
)
DAILY_RARITY_UNLIMITED = 999
DEFAULT_DAILY_RARITY_LIMITS = {rarity: DAILY_RARITY_UNLIMITED for rarity in DAILY_RARITY_KEYS}

CARD_UPDATE_DAILY_LIMIT = 2
CARD_UPDATE_MONTHLY_LIMIT = 30


def _clean_daily_rarity_limits(raw: dict | None) -> dict[str, int]:
    raw = raw or {}
    limits = {}
    for rarity in DAILY_RARITY_KEYS:
        try:
            value = int(raw.get(rarity, DAILY_RARITY_UNLIMITED))
        except (TypeError, ValueError):
            value = DAILY_RARITY_UNLIMITED
        limits[rarity] = max(0, min(DAILY_RARITY_UNLIMITED, value))
    return limits


def _today_key() -> str:
    import datetime
    return datetime.date.today().isoformat()


def _month_key() -> str:
    import datetime
    return datetime.date.today().strftime("%Y-%m")

_PRESENCE_LOCK = RLock()


def is_sellable(rarity: str) -> bool:
    return RARITY_VALUE.get(rarity, 0) <= MAX_SELLABLE_VALUE


def usd(cents: int) -> str:
    return f"${cents / 100:.2f}"


def _ago(ts: float) -> str:
    delta = int(time.time() - ts)
    if delta < 60:    return "just now"
    if delta < 3600:  return f"{delta // 60}m ago"
    if delta < 86400: return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


# ── MongoDB backend ────────────────────────────────────────────────────────────

class MongoWebDB:
    def __init__(self, mongo_uri: str, db_name: str):
        from pymongo import MongoClient, DESCENDING
        self._DESC = DESCENDING
        self._client = MongoClient(mongo_uri, serverSelectionTimeoutMS=6000)
        mdb = self._client[db_name]
        self._users    = mdb["users"]
        self._market   = mdb["market_listings"]
        self._chars    = mdb["anime_characters"]
        self._settings = mdb["bot_settings"]
        self._tx       = mdb["transactions"]
        self._stats    = mdb["site_stats"]
        self._wheel    = mdb["wheel_config"]
        self._rocket   = mdb["rocket_game"]
        self._codes    = mdb["redeem_codes"]
        self._presence = mdb["web_presence"]
        self._card_update = mdb["card_update_config"]
        self._rarity_quota = mdb["rarity_quota"]
        self._card_update_usage = mdb["card_update_usage"]
        self._client.admin.command("ping")

    def get_user(self, user_id: int):
        return self._users.find_one({"id": user_id})

    def ensure_user(self, user_id: int, first_name: str, username: str):
        self._users.update_one(
            {"id": user_id},
            {"$setOnInsert": {
                "id": user_id, "coins": 0, "characters": [],
                "favorites": [], "xp": 0, "wins": 0,
            }},
            upsert=True,
        )
        self._users.update_one(
            {"id": user_id},
            {"$set": {"first_name": first_name, "username": username}},
        )
        return self._users.find_one({"id": user_id})

    def get_balance(self, user_id: int) -> int:
        doc = self._users.find_one({"id": user_id}, {"coins": 1})
        return (doc or {}).get("coins", 0)

    def add_coins(self, user_id: int, delta: int) -> int:
        doc = self._users.find_one_and_update(
            {"id": user_id},
            {"$inc": {"coins": delta}},
            return_document=True,
        )
        return (doc or {}).get("coins", 0)

    def get_harem(self, user_id: int) -> list:
        doc = self._users.find_one({"id": user_id}, {"characters": 1})
        return (doc or {}).get("characters", [])

    def remove_char(self, user_id: int, char_id: str):
        doc = self._users.find_one({"id": user_id}, {"characters": 1})
        if not doc:
            return None
        chars = doc.get("characters", [])
        found = next((c for c in chars if str(c.get("id")) == str(char_id)), None)
        if not found:
            return None
        idx = next(i for i, c in enumerate(chars) if str(c.get("id")) == str(char_id))
        chars.pop(idx)
        self._users.update_one({"id": user_id}, {"$set": {"characters": chars}})
        return found

    def add_char(self, user_id: int, char: dict):
        self._users.update_one({"id": user_id}, {"$push": {"characters": char}})

    def replace_char(self, user_id: int, source_id: str, target_char: dict):
        """Replace one owned card instance while preserving duplicate-card counts."""
        doc = self._users.find_one({"id": user_id}, {"characters": 1})
        if not doc:
            return None
        chars = list(doc.get("characters", []))
        index = next((i for i, c in enumerate(chars)
                      if str(c.get("id")) == str(source_id)), None)
        if index is None:
            return None
        source = chars[index]
        chars[index] = dict(target_char)
        result = self._users.update_one(
            {"id": user_id, "characters": {"$elemMatch": {"id": source.get("id")}}},
            {"$set": {"characters": chars}},
        )
        return source if result.modified_count else None

    def find_user_by_username(self, username: str):
        username = username.lstrip("@")
        return self._users.find_one({"username": {"$regex": f"^{re.escape(username)}$", "$options": "i"}})

    def get_listings(self, skip=0, limit=20, rarity=None, search=None) -> list:
        filt: dict = {}
        if rarity:
            filt["char.rarity"] = rarity
        if search:
            filt["$or"] = [
                {"char.name":  {"$regex": re.escape(search), "$options": "i"}},
                {"char.anime": {"$regex": re.escape(search), "$options": "i"}},
            ]
        return list(self._market.find(filt).sort("listed_at", self._DESC).skip(skip).limit(limit))

    def count_listings(self, rarity=None, search=None) -> int:
        filt: dict = {}
        if rarity:
            filt["char.rarity"] = rarity
        if search:
            filt["$or"] = [
                {"char.name":  {"$regex": re.escape(search), "$options": "i"}},
                {"char.anime": {"$regex": re.escape(search), "$options": "i"}},
            ]
        return self._market.count_documents(filt)

    def get_listing(self, listing_id: str):
        from bson import ObjectId
        try:
            return self._market.find_one({"_id": ObjectId(listing_id)})
        except Exception:
            return self._market.find_one({"_id": listing_id})

    def add_listing(self, seller_id, seller_name, char, price, listing_type="fixed", ends_at=None) -> str:
        result = self._market.insert_one({
            "seller_id":   seller_id,
            "seller_name": seller_name,
            "char_id":     str(char["id"]),
            "char":        char,
            "price":       price,
            "listing_type": listing_type,
            "ends_at": ends_at,
            "highest_bid": None,
            "listed_at":   time.time(),
        })
        return str(result.inserted_id)

    def _delete_listing_raw(self, listing_id: str):
        from bson import ObjectId
        try:
            return self._market.find_one_and_delete({"_id": ObjectId(listing_id)})
        except Exception:
            return self._market.find_one_and_delete({"_id": listing_id})

    def get_user_listings(self, user_id: int) -> list:
        return list(self._market.find({"seller_id": user_id}).sort("listed_at", self._DESC))

    def delist_listing(self, listing_id: str, by_admin=False):
        listing = self._delete_listing_raw(listing_id)
        if listing:
            bid = listing.get("highest_bid") or {}
            if bid.get("bidder_id"):
                self.add_coins(int(bid["bidder_id"]), int(bid.get("amount", 0)))
            self.add_char(listing["seller_id"], listing["char"])
            self.log_transaction("delist", listing["seller_id"], listing["seller_id"], 0,
                                 {"char": listing["char"], "by_admin": by_admin, "listing_id": listing_id})
        return listing

    def buy_listing(self, listing_id: str, buyer_id: int, buyer_name: str) -> dict:
        from bson import ObjectId
        try:
            listing = self._market.find_one({"_id": ObjectId(listing_id)})
        except Exception:
            return {"ok": False, "error": "Invalid listing ID"}
        if not listing:
            return {"ok": False, "error": "Listing not found"}
        if listing["seller_id"] == buyer_id:
            return {"ok": False, "error": "Cannot buy your own listing"}
        price = listing["price"]
        buyer = self._users.find_one({"id": buyer_id})
        if not buyer or buyer.get("coins", 0) < price:
            return {"ok": False, "error": f"Insufficient balance. Need {usd(price)}"}
        self._users.update_one({"id": buyer_id},             {"$inc": {"coins": -price}})
        self._users.update_one({"id": listing["seller_id"]}, {"$inc": {"coins":  price}})
        self.add_char(buyer_id, listing["char"])
        self._market.delete_one({"_id": ObjectId(listing_id)})
        self.log_transaction("buy", buyer_id, listing["seller_id"], price, {
            "char": listing["char"], "listing_id": listing_id,
            "buyer_name": buyer_name, "seller_name": listing["seller_name"],
        })
        return {"ok": True, "char": listing["char"], "price": price}

    def _update_listing_auction(self, listing_id: str, bid: dict):
        from bson import ObjectId
        try: self._market.update_one({"_id": ObjectId(listing_id)}, {"$set": {"highest_bid": bid}})
        except Exception: self._market.update_one({"_id": listing_id}, {"$set": {"highest_bid": bid}})
    def place_bid(self, listing_id: str, bidder_id: int, bidder_name: str, amount: int) -> dict:
        listing = self.get_listing(listing_id)
        if not listing or listing.get("listing_type", "fixed") != "auction":
            return {"ok": False, "error": "Auction not found"}
        if listing.get("seller_id") == bidder_id:
            return {"ok": False, "error": "Cannot bid on your own auction"}
        if listing.get("ends_at") and time.time() >= float(listing["ends_at"]):
            return {"ok": False, "error": "Auction has ended"}
        previous = listing.get("highest_bid") or {}
        minimum = max(int(listing.get("price", 0)), int(previous.get("amount", 0)) + 1)
        if amount < minimum:
            return {"ok": False, "error": f"Bid must be at least {usd(minimum)}"}
        previous_reserved = int(previous.get("amount", 0)) if previous.get("bidder_id") != bidder_id else 0
        extra = amount - int(previous.get("amount", 0)) if previous.get("bidder_id") == bidder_id else amount
        if self.get_balance(bidder_id) < extra:
            return {"ok": False, "error": f"Insufficient balance. Need {usd(extra)}"}
        self.add_coins(bidder_id, -extra)
        if previous.get("bidder_id") and previous.get("bidder_id") != bidder_id:
            self.add_coins(previous["bidder_id"], int(previous.get("amount", 0)))
        bid = {"bidder_id": bidder_id, "bidder_name": bidder_name, "amount": amount, "at": time.time()}
        self._update_listing_auction(listing_id, bid)
        self.log_transaction("auction_bid", bidder_id, listing["seller_id"], amount, {"listing_id": listing_id, "char": listing["char"]})
        return {"ok": True, "bid": bid}

    def close_auction(self, listing_id: str, seller_id: int, by_admin=False) -> dict:
        listing = self.get_listing(listing_id)
        if not listing or listing.get("listing_type", "fixed") != "auction":
            return {"ok": False, "error": "Auction not found"}
        if listing.get("seller_id") != seller_id and not by_admin:
            return {"ok": False, "error": "Only the seller can close this auction"}
        bid = listing.get("highest_bid") or {}
        if not bid:
            removed = self._pop_listing(listing_id)
            if removed:
                self.add_char(removed["seller_id"], removed["char"])
            return {"ok": bool(removed), "sold": False}
        removed = self._pop_listing(listing_id)
        if not removed:
            return {"ok": False, "error": "Auction changed; try again"}
        self.add_coins(removed["seller_id"], int(bid["amount"]))
        self.add_char(int(bid["bidder_id"]), removed["char"])
        self.log_transaction("auction_sale", int(bid["bidder_id"]), removed["seller_id"], int(bid["amount"]), {"listing_id": listing_id, "char": removed["char"]})
        return {"ok": True, "sold": True, "price": int(bid["amount"]), "char": removed["char"]}

    def lucky_buy_listing(self, listing_id: str, buyer_id: int, buyer_name: str,
                          owner_id: int, stake: int) -> dict:
        from bson import ObjectId
        try:
            oid = ObjectId(listing_id)
        except Exception:
            return {"ok": False, "error": "Invalid listing ID"}
        listing = self._market.find_one({"_id": oid})
        if not listing:
            return {"ok": False, "error": "Listing not found"}
        if listing.get("seller_id") == buyer_id:
            return {"ok": False, "error": "Cannot lucky-buy your own listing"}
        price = int(listing.get("price", 0))
        if price <= 0 or stake < 500:
            return {"ok": False, "error": "Lucky Buy requires at least $5.00"}
        pool_before = self.get_lucky_pool()
        debited = self._users.update_one({"id": buyer_id, "coins": {"$gte": stake}}, {"$inc": {"coins": -stake}})
        if not debited.modified_count:
            return {"ok": False, "error": "Insufficient balance for this attempt"}
        pool_after = self.add_lucky_pool(stake)
        if pool_before < price:
            self.log_transaction("lucky_stake", buyer_id, owner_id, stake, {"listing_id": listing_id, "chance": 0, "won": False, "pool": True, "pool_balance": pool_after})
            return {"ok": True, "won": False, "chance": 0, "stake": stake, "price": price, "pool_balance": pool_after, "pool_ready": False}
        boost = 0.05 if pool_before >= price else 0
        chance = min(0.75, stake / price + boost)
        won = random.random() < chance
        if not won:
            self.log_transaction("lucky_stake", buyer_id, owner_id, stake, {"listing_id": listing_id, "chance": chance, "won": False, "pool": True, "pool_balance": pool_after})
            return {"ok": True, "won": False, "chance": chance, "stake": stake, "price": price, "pool_balance": pool_after, "pool_ready": True}
        removed = self._market.delete_one({"_id": oid})
        if not removed.deleted_count:
            self.withdraw_lucky_pool(stake)
            self._users.update_one({"id": buyer_id}, {"$inc": {"coins": stake}})
            return {"ok": False, "error": "Listing changed; stake refunded"}
        if not self.withdraw_lucky_pool(price):
            self._market.insert_one(dict(listing))
            return {"ok": True, "won": False, "chance": 0, "stake": stake, "price": price, "pool_balance": self.get_lucky_pool(), "pool_ready": False}
        self._users.update_one({"id": listing["seller_id"]}, {"$inc": {"coins": price}})
        self.add_char(buyer_id, listing["char"])
        pool_final = self.get_lucky_pool()
        self.log_transaction("lucky_stake", buyer_id, owner_id, stake, {"listing_id": listing_id, "chance": chance, "won": True, "pool": True, "pool_balance": pool_final})
        self.log_transaction("lucky_payout", owner_id, listing["seller_id"], price, {"listing_id": listing_id, "buyer_id": buyer_id, "char": listing["char"], "pool": True})
        return {"ok": True, "won": True, "chance": chance, "stake": stake, "price": price, "pool_balance": pool_final, "pool_ready": True, "char": listing["char"]}

    def claim_presence(self, presence_id: str, user_id: int, max_users: int = 8, ttl: int = 45) -> bool:
        from pymongo import ReturnDocument
        now = time.time()
        for index in range(max_users):
            slot_id = f"slot-{index}"
            try:
                doc = self._presence.find_one_and_update(
                    {"_id": slot_id, "$or": [
                        {"active": {"$ne": True}}, {"expires_at": {"$lt": now}},
                        {"presence_id": presence_id},
                    ]},
                    {"$set": {"active": True, "presence_id": presence_id,
                              "user_id": int(user_id), "expires_at": now + ttl}},
                    upsert=True, return_document=ReturnDocument.AFTER,
                )
                if doc and doc.get("presence_id") == presence_id:
                    return True
            except Exception:
                continue
        return False

    def touch_presence(self, presence_id: str, ttl: int = 45) -> bool:
        now = time.time()
        result = self._presence.update_many(
            {"presence_id": presence_id, "active": True, "expires_at": {"$gte": now}},
            {"$set": {"expires_at": now + ttl}},
        )
        return bool(result.modified_count)

    def release_presence(self, presence_id: str) -> None:
        self._presence.update_many({"presence_id": presence_id}, {"$set": {"active": False, "expires_at": 0}})

    def active_presence_count(self) -> int:
        return self._presence.count_documents({"active": True, "expires_at": {"$gte": time.time()}})

    def transfer_coins(self, from_id, to_id, amount, from_name="", to_name="") -> dict:
        if amount <= 0:
            return {"ok": False, "error": "Amount must be positive"}
        if from_id == to_id:
            return {"ok": False, "error": "Cannot transfer to yourself"}
        if self.get_balance(from_id) < amount:
            return {"ok": False, "error": "Insufficient balance"}
        if not self._users.find_one({"id": to_id}):
            return {"ok": False, "error": "Recipient not found"}
        self._users.update_one({"id": from_id}, {"$inc": {"coins": -amount}})
        self._users.update_one({"id": to_id},   {"$inc": {"coins":  amount}})
        self.log_transaction("transfer", from_id, to_id, amount,
                             {"from_name": from_name, "to_name": to_name})
        return {"ok": True, "new_balance": self.get_balance(from_id)}

    def log_transaction(self, tx_type, from_id, to_id, amount, details=None):
        self._tx.insert_one({
            "type":    tx_type,
            "from_id": from_id,
            "to_id":   to_id,
            "amount":  amount,
            "details": details or {},
            "ts":      time.time(),
        })
        # keep only last 10 per user — prune older ones
        for uid in set(filter(None, [from_id, to_id])):
            ids_to_keep = [
                d["_id"] for d in
                self._tx.find({"$or": [{"from_id": uid}, {"to_id": uid}]},
                              {"_id": 1}).sort("ts", self._DESC).limit(10)
            ]
            if ids_to_keep:
                self._tx.delete_many({
                    "$or": [{"from_id": uid}, {"to_id": uid}],
                    "_id": {"$nin": ids_to_keep},
                })

    def get_transactions(self, user_id: int, limit=10) -> list:
        return list(
            self._tx.find({"$or": [{"from_id": user_id}, {"to_id": user_id}]})
            .sort("ts", self._DESC).limit(limit)
        )

    def increment_listing_views(self, listing_id: str):
        from bson import ObjectId
        try:
            self._market.update_one({"_id": ObjectId(listing_id)}, {"$inc": {"views": 1}})
        except Exception:
            self._market.update_one({"_id": listing_id}, {"$inc": {"views": 1}})

    def record_visit(self, ip: str):
        today = time.strftime("%Y-%m-%d")
        self._stats.update_one(
            {"_id": f"visit:{today}:{ip}"},
            {"$setOnInsert": {"date": today, "ip": ip, "ts": time.time()}},
            upsert=True,
        )

    def get_daily_visits(self, days: int = 7) -> list:
        import datetime
        result = []
        for i in range(days - 1, -1, -1):
            d = (datetime.date.today() - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
            count = self._stats.count_documents({"date": d})
            result.append({"date": d, "count": count})
        return result

    def verify_api_key(self, key: str) -> bool:
        doc  = self._settings.find_one({"_id": "web_api_keys"})
        keys = (doc or {}).get("keys", [])
        return any(k["key"] == key for k in keys)

    def get_logo(self, include_data=True) -> dict | None:
        if not include_data:
            return self._settings.find_one({"_id": "site_logo", "data": {"$exists": True, "$ne": ""}}, {"mime": 1})
        doc = self._settings.find_one({"_id": "site_logo"})
        if doc and doc.get("data"):
            return {"data": doc["data"], "mime": doc.get("mime", "image/jpeg")}
        return None

    def set_logo(self, data_b64: str, mime: str) -> None:
        self._settings.update_one(
            {"_id": "site_logo"},
            {"$set": {"data": data_b64, "mime": mime}},
            upsert=True,
        )

    def delete_logo(self) -> None:
        self._settings.delete_one({"_id": "site_logo"})

    def get_welcome_slides(self, include_data=True) -> list:
        doc = self._settings.find_one({"_id": "welcome_slides"}, None if include_data else {"slides.data": 0})
        return (doc or {}).get("slides", [])

    def set_welcome_slides(self, slides: list) -> None:
        self._settings.update_one(
            {"_id": "welcome_slides"}, {"$set": {"slides": slides}}, upsert=True
        )

    def delete_welcome_slide(self, index: int) -> None:
        slides = self.get_welcome_slides()
        if 0 <= index < len(slides):
            slides.pop(index)
            self.set_welcome_slides(slides)

    def get_lucky_pool(self) -> int:
        doc = self._settings.find_one({"_id": "lucky_pool"})
        return int((doc or {}).get("coins", 0))
    def add_lucky_pool(self, amount: int) -> int:
        self._settings.update_one({"_id": "lucky_pool"}, {"$inc": {"coins": int(amount)}}, upsert=True)
        return self.get_lucky_pool()
    def withdraw_lucky_pool(self, amount: int) -> bool:
        result = self._settings.update_one(
            {"_id": "lucky_pool", "coins": {"$gte": int(amount)}},
            {"$inc": {"coins": -int(amount)}},
        )
        return bool(result.modified_count)
    def get_ad_banner(self, include_data=True) -> dict | None:
        if not include_data:
            return self._settings.find_one({"_id": "ad_banner", "data": {"$exists": True, "$ne": ""}}, {"mime": 1})
        doc = self._settings.find_one({"_id": "ad_banner"})
        if doc and doc.get("data"):
            return {"data": doc["data"], "mime": doc.get("mime", "image/jpeg")}
        return None

    def set_ad_banner(self, data_b64: str, mime: str) -> None:
        self._settings.update_one(
            {"_id": "ad_banner"},
            {"$set": {"data": data_b64, "mime": mime}},
            upsert=True,
        )

    def delete_ad_banner(self) -> None:
        self._settings.delete_one({"_id": "ad_banner"})

    def all_listings(self) -> list:
        return list(self._market.find().sort("listed_at", self._DESC))

    def get_wheel_prizes(self) -> list:
        doc = self._wheel.find_one({"_id": "prizes"})
        return (doc or {}).get("prizes", [])

    def save_wheel_prizes(self, prizes: list) -> None:
        self._wheel.update_one({"_id": "prizes"}, {"$set": {"prizes": prizes}}, upsert=True)

    def get_wheel_show(self) -> bool:
        doc = self._wheel.find_one({"_id": "config"})
        return bool((doc or {}).get("show", False))

    def set_wheel_show(self, show: bool) -> None:
        self._wheel.update_one({"_id": "config"}, {"$set": {"show": bool(show)}}, upsert=True)

    def get_card_update_show(self) -> bool:
        return bool((self._card_update.find_one({"_id": "config"}) or {}).get("show", False))

    def set_card_update_show(self, show: bool) -> None:
        self._card_update.update_one({"_id": "config"}, {"$set": {"show": bool(show)}}, upsert=True)

    def get_card_update_usage(self, user_id: int) -> dict:
        today = _today_key()
        month = _month_key()
        key = str(user_id)
        doc = self._card_update_usage.find_one({"_id": key}) or {}
        if doc.get("date") != today or doc.get("month") != month:
            self._card_update_usage.update_one(
                {"_id": key},
                {"$set": {"date": today, "month": month, "daily_used": 0, "monthly_used": 0}},
                upsert=True,
            )
            doc = {"date": today, "month": month, "daily_used": 0, "monthly_used": 0}
        return {"date": today, "month": month,
                "daily_used": int(doc.get("daily_used", 0)),
                "monthly_used": int(doc.get("monthly_used", 0)),
                "daily_limit": CARD_UPDATE_DAILY_LIMIT,
                "monthly_limit": CARD_UPDATE_MONTHLY_LIMIT}

    def consume_card_update(self, user_id: int) -> tuple[bool, dict]:
        status = self.get_card_update_usage(user_id)
        if status["daily_used"] >= CARD_UPDATE_DAILY_LIMIT or status["monthly_used"] >= CARD_UPDATE_MONTHLY_LIMIT:
            return False, status
        result = self._card_update_usage.update_one(
            {"_id": str(user_id), "date": status["date"], "month": status["month"],
             "daily_used": {"$lt": CARD_UPDATE_DAILY_LIMIT},
             "monthly_used": {"$lt": CARD_UPDATE_MONTHLY_LIMIT}},
            {"$inc": {"daily_used": 1, "monthly_used": 1}},
            upsert=True,
        )
        if not result.modified_count:
            return False, self.get_card_update_usage(user_id)
        return True, self.get_card_update_usage(user_id)

    def refund_card_update(self, user_id: int) -> dict:
        status = self.get_card_update_usage(user_id)
        self._card_update_usage.update_one(
            {"_id": str(user_id), "date": status["date"], "month": status["month"],
             "daily_used": {"$gt": 0}, "monthly_used": {"$gt": 0}},
            {"$inc": {"daily_used": -1, "monthly_used": -1}},
        )
        return self.get_card_update_usage(user_id)

    def get_daily_rarity_quota(self) -> dict:
        today = _today_key()
        doc = self._rarity_quota.find_one({"_id": "daily"}) or {}
        limits = _clean_daily_rarity_limits(doc.get("limits"))
        used = {rarity: max(0, int((doc.get("used") or {}).get(rarity, 0))) for rarity in DAILY_RARITY_KEYS}
        if doc.get("date") != today:
            used = {rarity: 0 for rarity in DAILY_RARITY_KEYS}
            self._rarity_quota.update_one(
                {"_id": "daily"},
                {"$set": {"date": today, "limits": limits, "used": used}},
                upsert=True,
            )
        return {"date": today, "limits": limits, "used": used}

    def set_daily_rarity_limits(self, limits: dict) -> dict:
        clean = _clean_daily_rarity_limits(limits)
        today = _today_key()
        doc = self._rarity_quota.find_one({"_id": "daily"}) or {}
        used = {rarity: max(0, int((doc.get("used") or {}).get(rarity, 0))) for rarity in DAILY_RARITY_KEYS}
        if doc.get("date") != today:
            used = {rarity: 0 for rarity in DAILY_RARITY_KEYS}
        self._rarity_quota.update_one(
            {"_id": "daily"},
            {"$set": {"date": today, "limits": clean, "used": used}},
            upsert=True,
        )
        return {"date": today, "limits": clean, "used": used}

    def claim_daily_rarity(self, rarity: str) -> bool:
        if rarity not in DAILY_RARITY_KEYS:
            return False
        status = self.get_daily_rarity_quota()
        limit = status["limits"].get(rarity, 0)
        if limit == DAILY_RARITY_UNLIMITED:
            return True
        result = self._rarity_quota.update_one(
            {"_id": "daily", "date": status["date"], f"limits.{rarity}": limit, f"used.{rarity}": {"$lt": limit}},
            {"$inc": {f"used.{rarity}": 1}},
        )
        return bool(result.modified_count)

    def release_daily_rarity(self, rarity: str) -> None:
        """Refund one previously claimed finite rarity quota slot."""
        if rarity not in DAILY_RARITY_KEYS:
            return
        status = self.get_daily_rarity_quota()
        if status["limits"].get(rarity, 0) == DAILY_RARITY_UNLIMITED:
            return
        self._rarity_quota.update_one(
            {"_id": "daily", "date": status["date"], f"used.{rarity}": {"$gt": 0}},
            {"$inc": {f"used.{rarity}": -1}},
        )

    def get_rocket_show(self) -> bool:
        return bool((self._rocket.find_one({"_id": "config"}) or {}).get("show", False))
    def set_rocket_show(self, show: bool) -> None:
        self._rocket.update_one({"_id": "config"}, {"$set": {"show": bool(show)}}, upsert=True)
    def get_rocket_pool(self) -> int:
        return int((self._rocket.find_one({"_id": "pool"}) or {}).get("coins", 0))
    def add_rocket_pool(self, amount: int) -> int:
        self._rocket.update_one({"_id": "pool"}, {"$inc": {"coins": int(amount)}}, upsert=True)
        return self.get_rocket_pool()
    def withdraw_rocket_pool(self, amount: int) -> bool:
        r = self._rocket.update_one({"_id": "pool", "coins": {"$gte": int(amount)}}, {"$inc": {"coins": -int(amount)}})
        return bool(r.modified_count)
    def get_rocket_state(self) -> dict:
        return self._rocket.find_one({"_id": "round"}) or {}
    def set_rocket_state(self, state: dict) -> None:
        self._rocket.update_one({"_id": "round"}, {"$set": {**state, "_id": "round"}}, upsert=True)
    def get_redeem_codes(self) -> list:
        docs = list(self._codes.find({}, {"code": 1, "limit": 1, "uses": 1, "created_at": 1}))
        for d in docs:
            d["id"] = str(d.pop("_id", ""))
        return docs

    def add_redeem_code(self, code: str, limit: int) -> str:
        r = self._codes.insert_one({
            "code": code.upper().strip(), "limit": int(limit),
            "uses": 0, "created_at": time.time(),
        })
        return str(r.inserted_id)

    def delete_redeem_code(self, code_id: str) -> None:
        from bson import ObjectId
        try:
            self._codes.delete_one({"_id": ObjectId(code_id)})
        except Exception:
            pass

    def use_redeem_code(self, code_text: str) -> tuple:
        doc = self._codes.find_one({"code": code_text.upper().strip()})
        if not doc:
            return None, "Invalid secret code"
        lim, uses = doc.get("limit", 0), doc.get("uses", 0)
        if lim > 0 and uses >= lim:
            return None, "This code has reached its usage limit"
        self._codes.update_one({"_id": doc["_id"]}, {"$inc": {"uses": 1}})
        return doc, None

    def get_char_by_id(self, char_id: str):
        try:
            c = self._chars.find_one({"id": int(char_id)})
            if not c:
                c = self._chars.find_one({"id": str(char_id)})
            return c
        except Exception:
            return None

    def top_users(self, limit=50) -> list:
        return list(
            self._users.find(
                {},
                {"id": 1, "first_name": 1, "username": 1, "coins": 1, "characters": 1},
            ).sort("coins", self._DESC).limit(limit)
        )


# ── JSON-file fallback ────────────────────────────────────────────────────────

class LocalWebDB:
    _DIR = Path("data")

    def __init__(self):
        self._DIR.mkdir(exist_ok=True)
        self._cache: dict[str, dict] = {}

    def _load(self, name: str) -> dict:
        if name not in self._cache:
            p = self._DIR / f"{name}.json"
            self._cache[name] = json.loads(p.read_text()) if p.exists() else {}
        return self._cache[name]

    def _save(self, name: str):
        p = self._DIR / f"{name}.json"
        p.write_text(json.dumps(self._cache[name], indent=2, default=str))

    def _u(self, name: str, data: dict):
        self._cache[name] = data
        self._save(name)

    def get_user(self, user_id: int):
        return self._load("users").get(str(user_id))

    def ensure_user(self, user_id: int, first_name: str, username: str):
        users = self._load("users")
        k = str(user_id)
        if k not in users:
            users[k] = {"id": user_id, "coins": 0, "characters": [], "favorites": [], "xp": 0, "wins": 0}
        users[k].update({"first_name": first_name, "username": username})
        self._u("users", users)
        return users[k]

    def get_balance(self, user_id: int) -> int:
        return (self.get_user(user_id) or {}).get("coins", 0)

    def add_coins(self, user_id: int, delta: int) -> int:
        users = self._load("users")
        k = str(user_id)
        if k not in users:
            users[k] = {"id": user_id, "coins": 0, "characters": []}
        users[k]["coins"] = users[k].get("coins", 0) + delta
        self._u("users", users)
        return users[k]["coins"]

    def get_harem(self, user_id: int) -> list:
        return (self.get_user(user_id) or {}).get("characters", [])

    def remove_char(self, user_id: int, char_id: str):
        users = self._load("users")
        k = str(user_id)
        if k not in users:
            return None
        chars = users[k].get("characters", [])
        found = next((c for c in chars if str(c.get("id")) == str(char_id)), None)
        if not found:
            return None
        idx = next(i for i, c in enumerate(chars) if str(c.get("id")) == str(char_id))
        chars.pop(idx)
        users[k]["characters"] = chars
        self._u("users", users)
        return found

    def add_char(self, user_id: int, char: dict):
        users = self._load("users")
        k = str(user_id)
        if k not in users:
            users[k] = {"id": user_id, "coins": 0, "characters": []}
        users[k].setdefault("characters", []).append(char)
        self._u("users", users)

    def replace_char(self, user_id: int, source_id: str, target_char: dict):
        """Replace one owned card instance while preserving duplicate-card counts."""
        users = self._load("users")
        k = str(user_id)
        user = users.get(k)
        if not user:
            return None
        chars = user.get("characters", [])
        index = next((i for i, c in enumerate(chars)
                      if str(c.get("id")) == str(source_id)), None)
        if index is None:
            return None
        source = chars[index]
        chars[index] = dict(target_char)
        user["characters"] = chars
        self._u("users", users)
        return source

    def find_user_by_username(self, username: str):
        username = username.lstrip("@").lower()
        for u in self._load("users").values():
            if (u.get("username") or "").lower() == username:
                return u
        return None

    def _all_listings(self) -> list:
        items = list(self._load("market").values())
        items.sort(key=lambda x: x.get("listed_at", 0), reverse=True)
        return items

    def get_listings(self, skip=0, limit=20, rarity=None, search=None) -> list:
        items = self._all_listings()
        if rarity:
            items = [i for i in items if i.get("char", {}).get("rarity") == rarity]
        if search:
            s = search.lower()
            items = [i for i in items if
                     s in i.get("char", {}).get("name",  "").lower() or
                     s in i.get("char", {}).get("anime", "").lower()]
        return items[skip:skip + limit]

    def count_listings(self, rarity=None, search=None) -> int:
        return len(self.get_listings(0, 999999, rarity, search))

    def get_listing(self, listing_id: str):
        return self._load("market").get(listing_id)

    def add_listing(self, seller_id, seller_name, char, price, listing_type="fixed", ends_at=None) -> str:
        market = self._load("market")
        lid    = uuid.uuid4().hex
        market[lid] = {
            "_id": lid, "seller_id": seller_id, "seller_name": seller_name,
            "char_id": str(char["id"]), "char": char,
            "price": price, "listing_type": listing_type, "ends_at": ends_at,
            "highest_bid": None, "listed_at": time.time(),
        }
        self._u("market", market)
        return lid

    def _pop_listing(self, listing_id: str):
        market = self._load("market")
        item   = market.pop(listing_id, None)
        if item:
            self._u("market", market)
        return item

    def get_user_listings(self, user_id: int) -> list:
        return sorted(
            [v for v in self._load("market").values() if v.get("seller_id") == user_id],
            key=lambda x: x.get("listed_at", 0), reverse=True,
        )

    def delist_listing(self, listing_id: str, by_admin=False):
        listing = self._pop_listing(listing_id)
        if listing:
            bid = listing.get("highest_bid") or {}
            if bid.get("bidder_id"):
                self.add_coins(int(bid["bidder_id"]), int(bid.get("amount", 0)))
            self.add_char(listing["seller_id"], listing["char"])
            self.log_transaction("delist", listing["seller_id"], listing["seller_id"], 0,
                                 {"char": listing["char"], "by_admin": by_admin})
        return listing

    def buy_listing(self, listing_id: str, buyer_id: int, buyer_name: str) -> dict:
        listing = self.get_listing(listing_id)
        if not listing:
            return {"ok": False, "error": "Listing not found"}
        if listing["seller_id"] == buyer_id:
            return {"ok": False, "error": "Cannot buy your own listing"}
        price = listing["price"]
        if self.get_balance(buyer_id) < price:
            return {"ok": False, "error": f"Insufficient balance. Need {usd(price)}"}
        self.add_coins(buyer_id, -price)
        self.add_coins(listing["seller_id"], price)
        self.add_char(buyer_id, listing["char"])
        self._pop_listing(listing_id)
        self.log_transaction("buy", buyer_id, listing["seller_id"], price, {
            "char": listing["char"], "listing_id": listing_id,
            "buyer_name": buyer_name, "seller_name": listing["seller_name"],
        })
        return {"ok": True, "char": listing["char"], "price": price}

    def _update_listing_auction(self, listing_id: str, bid: dict):
        market = self._load("market")
        if listing_id in market:
            market[listing_id]["highest_bid"] = bid
            self._u("market", market)
    def place_bid(self, listing_id: str, bidder_id: int, bidder_name: str, amount: int) -> dict:
        listing = self.get_listing(listing_id)
        if not listing or listing.get("listing_type", "fixed") != "auction":
            return {"ok": False, "error": "Auction not found"}
        if listing.get("seller_id") == bidder_id:
            return {"ok": False, "error": "Cannot bid on your own auction"}
        if listing.get("ends_at") and time.time() >= float(listing["ends_at"]):
            return {"ok": False, "error": "Auction has ended"}
        previous = listing.get("highest_bid") or {}
        minimum = max(int(listing.get("price", 0)), int(previous.get("amount", 0)) + 1)
        if amount < minimum:
            return {"ok": False, "error": f"Bid must be at least {usd(minimum)}"}
        previous_reserved = int(previous.get("amount", 0)) if previous.get("bidder_id") != bidder_id else 0
        extra = amount - int(previous.get("amount", 0)) if previous.get("bidder_id") == bidder_id else amount
        if self.get_balance(bidder_id) < extra:
            return {"ok": False, "error": f"Insufficient balance. Need {usd(extra)}"}
        self.add_coins(bidder_id, -extra)
        if previous.get("bidder_id") and previous.get("bidder_id") != bidder_id:
            self.add_coins(previous["bidder_id"], int(previous.get("amount", 0)))
        bid = {"bidder_id": bidder_id, "bidder_name": bidder_name, "amount": amount, "at": time.time()}
        self._update_listing_auction(listing_id, bid)
        self.log_transaction("auction_bid", bidder_id, listing["seller_id"], amount, {"listing_id": listing_id, "char": listing["char"]})
        return {"ok": True, "bid": bid}

    def close_auction(self, listing_id: str, seller_id: int, by_admin=False) -> dict:
        listing = self.get_listing(listing_id)
        if not listing or listing.get("listing_type", "fixed") != "auction":
            return {"ok": False, "error": "Auction not found"}
        if listing.get("seller_id") != seller_id and not by_admin:
            return {"ok": False, "error": "Only the seller can close this auction"}
        bid = listing.get("highest_bid") or {}
        if not bid:
            removed = self._pop_listing(listing_id)
            if removed:
                self.add_char(removed["seller_id"], removed["char"])
            return {"ok": bool(removed), "sold": False}
        removed = self._pop_listing(listing_id)
        if not removed:
            return {"ok": False, "error": "Auction changed; try again"}
        self.add_coins(removed["seller_id"], int(bid["amount"]))
        self.add_char(int(bid["bidder_id"]), removed["char"])
        self.log_transaction("auction_sale", int(bid["bidder_id"]), removed["seller_id"], int(bid["amount"]), {"listing_id": listing_id, "char": removed["char"]})
        return {"ok": True, "sold": True, "price": int(bid["amount"]), "char": removed["char"]}

    def lucky_buy_listing(self, listing_id: str, buyer_id: int, buyer_name: str,
                          owner_id: int, stake: int) -> dict:
        listing = self.get_listing(listing_id)
        if not listing:
            return {"ok": False, "error": "Listing not found"}
        if listing.get("seller_id") == buyer_id:
            return {"ok": False, "error": "Cannot lucky-buy your own listing"}
        price = int(listing.get("price", 0))
        if price <= 0 or stake < 500:
            return {"ok": False, "error": "Lucky Buy requires at least $5.00"}
        pool_before = self.get_lucky_pool()
        if self.get_balance(buyer_id) < stake:
            return {"ok": False, "error": "Insufficient balance for this attempt"}
        self.add_coins(buyer_id, -stake)
        pool_after = self.add_lucky_pool(stake)
        if pool_before < price:
            self.log_transaction("lucky_stake", buyer_id, owner_id, stake, {"listing_id": listing_id, "chance": 0, "won": False, "pool": True, "pool_balance": pool_after})
            return {"ok": True, "won": False, "chance": 0, "stake": stake, "price": price, "pool_balance": pool_after, "pool_ready": False}
        boost = 0.05 if pool_before >= price else 0
        chance = min(0.75, stake / price + boost)
        won = random.random() < chance
        if not won:
            self.log_transaction("lucky_stake", buyer_id, owner_id, stake, {"listing_id": listing_id, "chance": chance, "won": False, "pool": True, "pool_balance": pool_after})
            return {"ok": True, "won": False, "chance": chance, "stake": stake, "price": price, "pool_balance": pool_after, "pool_ready": True}
        removed = self._pop_listing(listing_id)
        if not removed:
            self.withdraw_lucky_pool(stake)
            self.add_coins(buyer_id, stake)
            return {"ok": False, "error": "Listing changed; stake refunded"}
        if not self.withdraw_lucky_pool(price):
            market = self._load("market")
            market[listing_id] = listing
            self._u("market", market)
            return {"ok": True, "won": False, "chance": 0, "stake": stake, "price": price, "pool_balance": self.get_lucky_pool(), "pool_ready": False}
        self.add_coins(listing["seller_id"], price)
        self.add_char(buyer_id, listing["char"])
        pool_final = self.get_lucky_pool()
        self.log_transaction("lucky_stake", buyer_id, owner_id, stake, {"listing_id": listing_id, "chance": chance, "won": True, "pool": True, "pool_balance": pool_final})
        self.log_transaction("lucky_payout", owner_id, listing["seller_id"], price, {"listing_id": listing_id, "buyer_id": buyer_id, "char": listing["char"], "pool": True})
        return {"ok": True, "won": True, "chance": chance, "stake": stake, "price": price, "pool_balance": pool_final, "pool_ready": True, "char": listing["char"]}

    def claim_presence(self, presence_id: str, user_id: int, max_users: int = 8, ttl: int = 45) -> bool:
        now = time.time()
        with _PRESENCE_LOCK:
            slots = self._load("web_presence")
            for index in range(max_users):
                key = f"slot-{index}"
                slot = slots.get(key, {})
                if (slot.get("presence_id") == presence_id or
                        not slot.get("active") or float(slot.get("expires_at", 0)) < now):
                    slots[key] = {"active": True, "presence_id": presence_id,
                                  "user_id": int(user_id), "expires_at": now + ttl}
                    self._u("web_presence", slots)
                    return True
            return False

    def touch_presence(self, presence_id: str, ttl: int = 45) -> bool:
        with _PRESENCE_LOCK:
            slots = self._load("web_presence")
            for slot in slots.values():
                if slot.get("presence_id") == presence_id and slot.get("active") and float(slot.get("expires_at", 0)) >= time.time():
                    slot["expires_at"] = time.time() + ttl
                    self._u("web_presence", slots)
                    return True
        return False

    def release_presence(self, presence_id: str) -> None:
        with _PRESENCE_LOCK:
            slots = self._load("web_presence")
            for slot in slots.values():
                if slot.get("presence_id") == presence_id:
                    slot.update({"active": False, "expires_at": 0})
            self._u("web_presence", slots)

    def active_presence_count(self) -> int:
        now = time.time()
        with _PRESENCE_LOCK:
            return sum(1 for slot in self._load("web_presence").values()
                       if slot.get("active") and float(slot.get("expires_at", 0)) >= now)

    def transfer_coins(self, from_id, to_id, amount, from_name="", to_name="") -> dict:
        if amount <= 0:
            return {"ok": False, "error": "Amount must be positive"}
        if from_id == to_id:
            return {"ok": False, "error": "Cannot transfer to yourself"}
        if self.get_balance(from_id) < amount:
            return {"ok": False, "error": "Insufficient balance"}
        if not self.get_user(to_id):
            return {"ok": False, "error": "Recipient not found"}
        self.add_coins(from_id, -amount)
        self.add_coins(to_id,    amount)
        self.log_transaction("transfer", from_id, to_id, amount,
                             {"from_name": from_name, "to_name": to_name})
        return {"ok": True, "new_balance": self.get_balance(from_id)}

    def log_transaction(self, tx_type, from_id, to_id, amount, details=None):
        txs = self._load("transactions")
        tid = uuid.uuid4().hex
        txs[tid] = {"id": tid, "type": tx_type, "from_id": from_id, "to_id": to_id,
                    "amount": amount, "details": details or {}, "ts": time.time()}
        # prune: keep only last 10 per user
        for uid in set(filter(None, [from_id, to_id])):
            user_txs = sorted(
                [(k, v) for k, v in txs.items()
                 if v.get("from_id") == uid or v.get("to_id") == uid],
                key=lambda x: x[1].get("ts", 0), reverse=True,
            )
            for k, _ in user_txs[10:]:
                txs.pop(k, None)
        self._u("transactions", txs)

    def get_transactions(self, user_id: int, limit=10) -> list:
        txs = self._load("transactions")
        items = [v for v in txs.values()
                 if v.get("from_id") == user_id or v.get("to_id") == user_id]
        items.sort(key=lambda x: x.get("ts", 0), reverse=True)
        return items[:limit]

    def increment_listing_views(self, listing_id: str):
        market = self._load("market")
        if listing_id in market:
            market[listing_id]["views"] = market[listing_id].get("views", 0) + 1
            self._u("market", market)

    def record_visit(self, ip: str):
        import datetime
        stats = self._load("stats")
        today = datetime.date.today().strftime("%Y-%m-%d")
        day   = stats.setdefault(today, {})
        if ip not in day:
            day[ip] = time.time()
            self._u("stats", stats)

    def get_daily_visits(self, days: int = 7) -> list:
        import datetime
        stats  = self._load("stats")
        result = []
        for i in range(days - 1, -1, -1):
            d = (datetime.date.today() - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
            result.append({"date": d, "count": len(stats.get(d, {}))})
        return result

    def verify_api_key(self, key: str) -> bool:
        settings = self._load("settings")
        keys     = settings.get("web_api_keys", {}).get("keys", [])
        return any(k["key"] == key for k in keys)

    def get_logo(self, include_data=True) -> dict | None:
        logo = self._load("settings").get("site_logo")
        return logo if logo and logo.get("data") else None

    def set_logo(self, data_b64: str, mime: str) -> None:
        s = self._load("settings")
        s["site_logo"] = {"data": data_b64, "mime": mime}
        self._u("settings", s)

    def delete_logo(self) -> None:
        s = self._load("settings")
        s.pop("site_logo", None)
        self._u("settings", s)

    def get_welcome_slides(self, include_data=True) -> list:
        return self._load("settings").get("welcome_slides", [])

    def set_welcome_slides(self, slides: list) -> None:
        s = self._load("settings")
        s["welcome_slides"] = slides
        self._u("settings", s)

    def delete_welcome_slide(self, index: int) -> None:
        slides = self.get_welcome_slides()
        if 0 <= index < len(slides):
            slides.pop(index)
            self.set_welcome_slides(slides)

    def get_lucky_pool(self) -> int:
        return int(self._load("settings").get("lucky_pool", {}).get("coins", 0))
    def add_lucky_pool(self, amount: int) -> int:
        settings = self._load("settings")
        pool = settings.setdefault("lucky_pool", {"coins": 0})
        pool["coins"] = int(pool.get("coins", 0)) + int(amount)
        self._u("settings", settings)
        return int(pool["coins"])
    def withdraw_lucky_pool(self, amount: int) -> bool:
        settings = self._load("settings")
        pool = settings.setdefault("lucky_pool", {"coins": 0})
        if int(pool.get("coins", 0)) < int(amount):
            return False
        pool["coins"] = int(pool.get("coins", 0)) - int(amount)
        self._u("settings", settings)
        return True

    def get_ad_banner(self, include_data=True) -> dict | None:
        ad = self._load("settings").get("ad_banner")
        return ad if ad and ad.get("data") else None

    def set_ad_banner(self, data_b64: str, mime: str) -> None:
        s = self._load("settings")
        s["ad_banner"] = {"data": data_b64, "mime": mime}
        self._u("settings", s)

    def delete_ad_banner(self) -> None:
        s = self._load("settings")
        s.pop("ad_banner", None)
        self._u("settings", s)

    def all_listings(self) -> list:
        return self._all_listings()

    def get_wheel_prizes(self) -> list:
        return self._load("wheel_config").get("prizes", [])

    def save_wheel_prizes(self, prizes: list) -> None:
        cfg = self._load("wheel_config")
        cfg["prizes"] = prizes
        self._u("wheel_config", cfg)

    def get_wheel_show(self) -> bool:
        return bool(self._load("wheel_config").get("show", False))

    def set_wheel_show(self, show: bool) -> None:
        cfg = self._load("wheel_config")
        cfg["show"] = bool(show)
        self._u("wheel_config", cfg)

    def get_card_update_show(self) -> bool:
        return bool(self._load("card_update_config").get("show", False))

    def set_card_update_show(self, show: bool) -> None:
        cfg = self._load("card_update_config")
        cfg["show"] = bool(show)
        self._u("card_update_config", cfg)

    def get_card_update_usage(self, user_id: int) -> dict:
        with _PRESENCE_LOCK:
            today = _today_key()
            month = _month_key()
            users = self._load("card_update_usage")
            key = str(user_id)
            entry = users.get(key) or {}
            if entry.get("date") != today or entry.get("month") != month:
                entry = {"date": today, "month": month, "daily_used": 0, "monthly_used": 0}
                users[key] = entry
                self._u("card_update_usage", users)
            return {"date": today, "month": month,
                    "daily_used": int(entry.get("daily_used", 0)),
                    "monthly_used": int(entry.get("monthly_used", 0)),
                    "daily_limit": CARD_UPDATE_DAILY_LIMIT,
                    "monthly_limit": CARD_UPDATE_MONTHLY_LIMIT}

    def consume_card_update(self, user_id: int) -> tuple[bool, dict]:
        with _PRESENCE_LOCK:
            status = self.get_card_update_usage(user_id)
            if status["daily_used"] >= CARD_UPDATE_DAILY_LIMIT or status["monthly_used"] >= CARD_UPDATE_MONTHLY_LIMIT:
                return False, status
            users = self._load("card_update_usage")
            entry = users.setdefault(str(user_id), {"date": status["date"], "month": status["month"], "daily_used": 0, "monthly_used": 0})
            entry["daily_used"] = int(entry.get("daily_used", 0)) + 1
            entry["monthly_used"] = int(entry.get("monthly_used", 0)) + 1
            self._u("card_update_usage", users)
            return True, self.get_card_update_usage(user_id)

    def refund_card_update(self, user_id: int) -> dict:
        with _PRESENCE_LOCK:
            status = self.get_card_update_usage(user_id)
            users = self._load("card_update_usage")
            entry = users.get(str(user_id))
            if entry and entry.get("date") == status["date"] and entry.get("month") == status["month"]:
                entry["daily_used"] = max(0, int(entry.get("daily_used", 0)) - 1)
                entry["monthly_used"] = max(0, int(entry.get("monthly_used", 0)) - 1)
                self._u("card_update_usage", users)
            return self.get_card_update_usage(user_id)

    def get_daily_rarity_quota(self) -> dict:
        with _PRESENCE_LOCK:
            today = _today_key()
            cfg = self._load("rarity_quota")
            limits = _clean_daily_rarity_limits(cfg.get("limits"))
            used = {rarity: max(0, int((cfg.get("used") or {}).get(rarity, 0))) for rarity in DAILY_RARITY_KEYS}
            if cfg.get("date") != today:
                used = {rarity: 0 for rarity in DAILY_RARITY_KEYS}
                cfg = {"date": today, "limits": limits, "used": used}
                self._u("rarity_quota", cfg)
            return {"date": today, "limits": limits, "used": used}

    def set_daily_rarity_limits(self, limits: dict) -> dict:
        with _PRESENCE_LOCK:
            today = _today_key()
            cfg = self._load("rarity_quota")
            used = {rarity: max(0, int((cfg.get("used") or {}).get(rarity, 0))) for rarity in DAILY_RARITY_KEYS}
            if cfg.get("date") != today:
                used = {rarity: 0 for rarity in DAILY_RARITY_KEYS}
            clean = _clean_daily_rarity_limits(limits)
            self._u("rarity_quota", {"date": today, "limits": clean, "used": used})
            return {"date": today, "limits": clean, "used": used}

    def claim_daily_rarity(self, rarity: str) -> bool:
        if rarity not in DAILY_RARITY_KEYS:
            return False
        with _PRESENCE_LOCK:
            status = self.get_daily_rarity_quota()
            limit = status["limits"].get(rarity, 0)
            if limit == DAILY_RARITY_UNLIMITED:
                return True
            if limit <= 0 or status["used"].get(rarity, 0) >= limit:
                return False
            cfg = self._load("rarity_quota")
            used = cfg.setdefault("used", {})
            used[rarity] = int(used.get(rarity, 0)) + 1
            self._u("rarity_quota", cfg)
            return True

    def release_daily_rarity(self, rarity: str) -> None:
        """Refund one previously claimed finite rarity quota slot."""
        if rarity not in DAILY_RARITY_KEYS:
            return
        with _PRESENCE_LOCK:
            status = self.get_daily_rarity_quota()
            if status["limits"].get(rarity, 0) == DAILY_RARITY_UNLIMITED:
                return
            cfg = self._load("rarity_quota")
            used = cfg.setdefault("used", {})
            if int(used.get(rarity, 0)) <= 0:
                return
            used[rarity] = int(used[rarity]) - 1
            self._u("rarity_quota", cfg)

    def get_rocket_show(self) -> bool:
        return bool(self._load("rocket_config").get("show", False))
    def set_rocket_show(self, show: bool) -> None:
        cfg = self._load("rocket_config"); cfg["show"] = bool(show); self._u("rocket_config", cfg)
    def get_rocket_pool(self) -> int:
        return int(self._load("rocket_config").get("pool", 0))
    def add_rocket_pool(self, amount: int) -> int:
        cfg = self._load("rocket_config"); cfg["pool"] = int(cfg.get("pool", 0)) + int(amount); self._u("rocket_config", cfg); return cfg["pool"]
    def withdraw_rocket_pool(self, amount: int) -> bool:
        cfg = self._load("rocket_config")
        if int(cfg.get("pool", 0)) < int(amount): return False
        cfg["pool"] -= int(amount); self._u("rocket_config", cfg); return True
    def get_rocket_state(self) -> dict:
        return self._load("rocket_round")
    def set_rocket_state(self, state: dict) -> None:
        self._u("rocket_round", state)
    def get_redeem_codes(self) -> list:
        return list(self._load("wheel_codes").values())

    def add_redeem_code(self, code: str, limit: int) -> str:
        codes = self._load("wheel_codes")
        cid   = uuid.uuid4().hex
        codes[cid] = {
            "id": cid, "code": code.upper().strip(),
            "limit": int(limit), "uses": 0, "created_at": time.time(),
        }
        self._u("wheel_codes", codes)
        return cid

    def delete_redeem_code(self, code_id: str) -> None:
        codes = self._load("wheel_codes")
        codes.pop(code_id, None)
        self._u("wheel_codes", codes)

    def use_redeem_code(self, code_text: str) -> tuple:
        codes = self._load("wheel_codes")
        doc = next((v for v in codes.values()
                    if v.get("code") == code_text.upper().strip()), None)
        if not doc:
            return None, "Invalid secret code"
        lim, uses = doc.get("limit", 0), doc.get("uses", 0)
        if lim > 0 and uses >= lim:
            return None, "This code has reached its usage limit"
        codes[doc["id"]]["uses"] = uses + 1
        self._u("wheel_codes", codes)
        return doc, None

    def get_char_by_id(self, char_id: str):
        """Resolve a card from local master data or any cached user harem."""
        target = str(char_id).strip()
        chars = self._load("characters")
        values = chars.values() if isinstance(chars, dict) else chars
        for value in values:
            if str(value.get("id")) == target:
                return dict(value)
        for user in self._load("users").values():
            for value in user.get("characters", []):
                if str(value.get("id")) == target:
                    return dict(value)
        return None

    def top_users(self, limit=50) -> list:
        items = list(self._load("users").values())
        items.sort(key=lambda x: x.get("coins", 0), reverse=True)
        return items[:limit]


# ── factory ───────────────────────────────────────────────────────────────────

_db: MongoWebDB | LocalWebDB | None = None


def reset_db() -> None:
    """Force the next get_db() call to create a fresh connection."""
    global _db
    _db = None


def get_db() -> MongoWebDB | LocalWebDB:
    global _db
    if _db is None:
        try:
            from web.config_store import get as _cfg
        except ModuleNotFoundError:
            from config_store import get as _cfg  # type: ignore
        mongo_uri = _cfg("MONGO_URI")
        db_name   = _cfg("DB_NAME", "waifu_bot")
        if mongo_uri:
            # A configured database is authoritative; never lose writes into a
            # disposable JSON fallback when MongoDB is unavailable.
            _db = MongoWebDB(mongo_uri, db_name)
        else:
            if os.environ.get("VERCEL") == "1":
                raise RuntimeError("MONGO_URI is required on Vercel.")
            print("[WebDB] No MONGO_URI — using local JSON store")
            _db = LocalWebDB()
    return _db
