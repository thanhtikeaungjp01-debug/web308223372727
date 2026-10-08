# Waifu Telegram Mini App

Python 3.12 / Flask mini app for the connected Telegram catch bot.

## Vercel မှာ တင်ရန်

1. Vercel → **Add New → Project** မှာ `thanhtikeaungjp01-debug/web` repo ကို import လုပ်ပါ။
2. **Root Directory** ကို repo root (`./`)၊ **Framework Preset** ကို **Flask** ထားပါ။ `artifacts/mockup-sandbox` ကို မရွေးပါနဲ့။
3. `vercel.json` က build command (`python scripts/build_vercel.py`) ကို သတ်မှတ်ထားပါတယ်။ Install Command / Output Directory overrides မထည့်ပါနဲ့။ Node / pnpm build မလိုပါ။
4. အောက်ပါ environment variables ကို **Production** မှာ ထည့်ပြီး Deploy နှိပ်ပါ။ Preview deployment စမ်းမယ်ဆိုရင် Preview environment မှာလည်း သီးသန့် test values ထည့်ပါ။

| Variable | ထည့်ရန် |
| --- | --- |
| `MONGO_URI` | Bot နဲ့အတူသုံးမယ့် MongoDB Atlas connection URI |
| `DB_NAME` | Bot သုံးတဲ့ database name (default `waifu_bot`) |
| `BOT_TOKEN` | BotFather ကရတဲ့ token |
| `BOT_USERNAME` | Bot username (`@` မပါ) |
| `OWNER_ID` | Owner ရဲ့ numeric Telegram user ID |
| `SESSION_SECRET` | အနည်းဆုံး 32 characters ရှိတဲ့ random secret; deploy တိုင်း မပြောင်းပါနဲ့ |

Secret ထုတ်ရန်: `python -c "import secrets; print(secrets.token_urlsafe(48))"`

MongoDB Atlas **Network Access** မှာ Vercel function က ချိတ်လို့ရအောင် သတ်မှတ်ပါ။ DB user ကို သက်ဆိုင်ရာ database အတွက် read/write ခွင့်ပေးပါ။ Bot HTTP API သုံးမယ်ဆိုရင် `BOT_API_URL` နဲ့ `BOT_API_KEY` ကိုပါ ထည့်ပါ။ Secrets ကို Git ထဲ မတင်ပါနဲ့။

5. Deploy ပြီးရင် `https://YOUR-PROJECT.vercel.app/healthz` မှာ `{"status":"ok"}` ရမရကြည့်ပါ။ ဒါက app liveness သာဖြစ်ပြီး MongoDB/Telegram connection ကို အတည်မပြုပါ။
6. BotFather `/setdomain` မှာ production hostname သတ်မှတ်ပါ။ `/setmenubutton` (သို့) Main Mini App URL မှာ HTTPS app URL ထည့်ပါ။ Bot ရဲ့ `MARKET_URL` ကိုလည်း ဒီ URL နဲ့ပြောင်းပါ။
7. Telegram ကနေ app ဖွင့်ပြီး login၊ balance၊ Admin → Appearance မှာ title/subtitle/logo save စမ်းပါ။ Incognito browser/နောက် device ကနေပြန်ဖွင့်ပြီး သိမ်းထားတာ ရှိနေကြောင်းစစ်ပါ။ Telegram ကနေဝင်တဲ့ production URL ကို သုံးပါ; Vercel Deployment Protection ဖွင့်ထားတဲ့ preview URL က visitors ကို ပိတ်ထားနိုင်ပါတယ်။

### Vercel ပေါ်မှာ သိမ်းဆည်းပုံ

- Owner title/subtitle/settings ကို MongoDB `bot_settings` collection ရဲ့ `web_config` document မှာ သိမ်းပါတယ်။ Logo၊ welcome images နဲ့ account data လည်း MongoDB မှာပဲ သိမ်းပါတယ်။ Database တူတဲ့ deployments တွေက settings ကို အတူသုံးပါတယ်။
- Environment variable က saved setting ထက် ဦးစားပေးပါတယ်။ Owner panel ကပြောင်းမယ့် `SITE_TITLE`, `SITE_SUBTITLE`, `MAINTENANCE_MODE` တွေကို Vercel env မှာ မသတ်မှတ်ပါနဲ့။ `MONGO_URI`, `DB_NAME`, `SESSION_SECRET` ကို Vercel Settings ကနေသာပြောင်းပြီး redeploy လုပ်ပါ။
- Existing server ရဲ့ `data/config.json` ကို Vercel ဆီ အလိုအလျောက် မကူးပါ။ Credentials ကို env ထဲထည့်ပြီး Appearance/settings ကို owner panel ကနေ ပြန်သိမ်းပါ။ Existing Mongo logo/user data ကို ဆက်သုံးပါတယ်။
- Cold start/redeploy က maintenance mode ကို မပြောင်းပါ။ MongoDB မရရင် local JSON ထဲ fallback မရေးပါ။
- Static CSS/JS/images ကို build က `public/static` ဆီကူးပြီး Vercel CDN ကပို့ပါတယ်။ Telegram media cache က `/tmp` မှာ 128 MiB အထိသာထားတဲ့ ယာယီ cache ဖြစ်ပါတယ်။
- Vercel ရဲ့ 4.5 MB request/response limit အတွက် **upload တစ်ဖိုင် 4 MiB အထိ** သာခွင့်ပြုပါတယ်။ Logo/welcome/ad upload UI က ကြိုစစ်ပေးပါတယ်။ Thumbnail ပြောင်းမည့် raster image ကို upstream မှ 12 MiB အထိဖတ်ပြီး ချုံ့ပါတယ်။ နောက်ဆုံး response နဲ့ video က 4 MiB ကျော်ရင် placeholder ပြပါတယ်။ ကြီးတဲ့ video တွေကို ဒီ proxy မှာ တိုက်ရိုက်မပို့နိုင်ပါ။
- Bot process ကို Vercel မှာ မ run ပါ။ အခု repo က mini app ဖြစ်ပြီး bot က လက်ရှိ host မှာ ဆက် run ရပါမယ်။

Official deployment references: [Flask on Vercel](https://vercel.com/docs/frameworks/backend/flask), [Python runtime](https://vercel.com/docs/functions/runtimes/python), [Function limits](https://vercel.com/docs/functions/limitations).

## Local development / persistent server

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python app.py
```

`python app.py` uses exported environment variables. To load a private `.env` explicitly, use `flask --app app run` (python-dotenv is included). For local HTTP, set `SESSION_COOKIE_SECURE=0`. Without MongoDB, local development uses JSON in `data/`. For browser-based first-time setup on a persistent server, set `SETUP_KEY` and use `/setup`; blank secret fields preserve current values. On Vercel, configure the required environment variables before deploying.

Render remains supported through `render.yaml` (`pip install -r requirements.txt`, `python app.py`, health path `/healthz`). Cloudflare Workers cannot run this Flask app directly.

## Local verification

```bash
PYTHONPATH=. python scripts/verify_deployment.py
PYTHONPATH=. python scripts/test_vercel_deployment.py
PYTHONPATH=. python scripts/test_auth_security.py
PYTHONPATH=. python scripts/test_admin_branding.py
python scripts/build_vercel.py
```

These checks use synthetic fixtures; real Telegram sign-in and Atlas connectivity must also be checked after deployment.

## Authentication and local development

- Set `BOT_USERNAME` and register your deployed domain with BotFather's `/setdomain` to enable the Telegram login widget. Users can also enter through the Telegram Mini App or a short-lived bot login link.
- Keep `SESSION_SECRET` stable and random. The Render Blueprint generates it automatically; set it yourself on Vercel. Set `SESSION_COOKIE_SECURE=1` on HTTPS; leave it `0` for local HTTP. Enable `TRUST_PROXY=1` only behind a single trusted reverse proxy; the Render Blueprint enables this so Telegram callbacks use HTTPS.
- Login signatures must be recent (within 24 hours, with 30 seconds of clock tolerance). Successful login rotates the session. Logout is a CSRF-protected POST.
- Browser writes include a session CSRF token. Bot integrations continue to use a bearer API key on `/api/internal/rarity-gate`.
- Bot and web must share the same MongoDB database for login tokens. Tokens are consumed atomically; a database failure does not fall back to a duplicate JSON token. With no MongoDB configured, a locked local JSON file supports development on one host.
- `SETUP_KEY` protects first-time browser setup; environment-based configuration does not require opening the wizard. Configuration secrets and local runtime JSON files are ignored by Git.

Install Python 3.12 dependencies with `python -m pip install -r requirements.txt`, then run `python app.py`. `/` and `/setup` can be previewed without credentials; `/market` requires a configured database as before.

Run the credential-free checks from the repository root:

```bash
PYTHONPATH=. python scripts/test_auth_security.py
for test in scripts/test_*.py; do PYTHONPATH=. python "$test" || exit 1; done
python scripts/verify_deployment.py
node --check static/js/app.js
```

The authentication tests use signed synthetic Telegram data and temporary local stores. They do not substitute for checking BotFather domain registration, a real Telegram callback, and a live MongoDB connection on your deployment.

## Mini app name and logo

As the configured owner, open **Admin → Appearance**. Under **Mini App Branding**, edit **App name** (up to 40 characters) and **Subtitle** (up to 64 characters), then select **Save Branding**. A blank subtitle hides the second line.

Under **Site Logo**, upload a PNG, JPEG, GIF or WebP photo (up to 4 MiB on Vercel, 5 MiB on a persistent server). It appears beside the name on Home; **Remove** restores the default icon. Logos are resized to 512 pixels or smaller. Content fingerprints in media URLs let browsers cache unchanged logos; replacing a logo produces a new URL. Text is stored in the existing runtime configuration and the logo in the existing database settings.

## Display preferences

Open the three-dot menu on Home to select **Small / Large** and **Dark / Light / Blue**. Both choices persist on that browser/device and apply across pages. Large also expands Telegram and requests fullscreen on supported Telegram 8.0+ clients; Small exits fullscreen and uses a compact layout. Older clients support expansion only: Telegram does not expose a collapse API, so reducing its window requires a swipe. Fullscreen is requested only after a button press; a reload restores the layout preference.


## Loading, images and advertisements

- Card photos use same-origin `/media/<token>?w=640` thumbnails: non-animated raster images become WebP, at most 640 pixels on either edge. Animated GIF/WebP and video remain animated; videos load on demand.
- Telegram file IDs use stable URLs with a 30-day browser/CDN cache. Logo, welcome and uploaded ad URLs include a content fingerprint, so changing an upload changes its URL. External mutable image URLs refresh after five minutes.
- Repeat visits can reuse downloaded image bytes. First visits still download; browser storage eviction, clearing cache, or private browsing can require another download. Vercel's bounded 128 MiB `/tmp` cache is temporary and may disappear on cold starts; browser/CDN caching reduces dependence on it.
- Only public display metadata is cached in memory for 15 seconds. Owner edits invalidate the local cache immediately; another instance can take up to 15 seconds to reflect them. Balances, bids, sessions and transaction history remain fresh.
- Market views are batched for visible cards. Page navigation uses native links, a progress indicator and supported-browser view transitions. Large auction lists are paginated, 24 entries per page.
- The Market advertisement is an inline **Community spotlight** card. It loads near the viewport, supports a destination link, and remembers dismissal for that ad during the browser session. Ad videos do not autoplay.
- If Market shows Maintenance, the owner must disable **Admin → Maintenance** (and remove a `MAINTENANCE_MODE=1` environment override if set). Public welcome/ad media remain available during maintenance.

For best hosted latency, place Vercel functions near the Atlas database. On large databases, have the database administrator inspect indexes for `market_listings` (`listing_type`, `ends_at`) and transaction history (`type`, `from_id`, `ts`) before adding them. This change does not create indexes or run a data migration.

## Auction history

Open **Auction → My bids** for current participation and leading/outbid status; **Won** keeps your latest 10 completed wins. At expiry, the displayed auction page refreshes once: ended bids leave My bids, and settled wins appear in Won. Up to 25 expired auctions settle per page visit, so a large backlog can take multiple visits.

Winning history uses existing `auction_sale` transaction records. When a new auction win is recorded, older `auction_sale` records beyond the winner’s latest ten are deleted. The deletion is restricted to that winner and those older records; other transaction types, users, owned cards and balances are untouched. Existing older wins are hidden immediately and pruned on the next win. Wallet displays its latest ten transactions. Already-deleted historical transactions cannot be recovered by this update. Participant tracking starts with bids placed after this update; legacy auctions still show the current highest bidder, but earlier outbid participants may have no stored record. Filtering losing bids from the UI does not delete users, balances or unrelated MongoDB records.

Focused synthetic checks: `PYTHONPATH=. python scripts/test_loading_history.py`.


Rocket, Update and Wheel appear beneath Home's four main action buttons when their owner visibility switches are enabled. Wheel opens its existing spin modal there. Market lists only fixed-price cards (including legacy listings without a type); Auction lists only auctions. Direct Market purchase/Lucky Buy requests also reject auction cards.


## Welcome slideshow

In **Admin → Appearance → Welcome Images**, select several photos and upload them (up to five stored photos). Home automatically slides every three seconds when there are at least two photos and loops back to the first. A single photo stays still. The dots indicate the current photo. There are no manual slide or pause controls. Rotation waits while the page is hidden or the banner is off screen, then resumes when visible. Reduced-motion devices use instant transitions. Existing uploaded photos are preserved; remove one before adding more if the list is full.
