# Waifu Market

Flask web marketplace for browsing, buying, and managing anime characters connected to a Telegram bot.

## Run & Operate

- `python app.py` — run the Flask web app on port 5000
- Open `/setup` on first run to configure MongoDB and the Telegram bot
- `/healthz` — health check endpoint
- Required setup values: `MONGO_URI`, `BOT_TOKEN`, and `OWNER_ID`
- `SESSION_SECRET` is available as a Replit secret and is used to sign Flask sessions

## Stack

- Python 3.12
- Flask
- MongoDB via PyMongo, with a local JSON store when MongoDB is not configured
- Telegram Mini App and Telegram login support

## Where things live

- `app.py` — Flask routes and application startup
- `templates/` — page templates, including the shared navigation in `templates/base.html`
- `static/css/style.css` — shared dark/purple visual theme and responsive layout
- `static/js/app.js` — Telegram Mini App initialization, navigation toggle, and shared UI helpers
- `db.py` — MongoDB/local JSON data access layer
- `config_store.py` — runtime configuration loaded from environment or `data/config.json`
- `attached_assets/` — uploaded visual references and project assets

## Architecture decisions

- The existing Flask/PyMongo structure is preserved; the project is not migrated to the generated pnpm artifacts.
- Configuration is entered through `/setup` or loaded from environment variables, so credentials are not hardcoded.
- The shared navigation keeps Market, Harem, Wallet, and owner-only Admin behind one hamburger menu.
- Telegram media is served through the same-origin `/media/<token>` route; the server streams bot files into `data/media_cache/` once and serves later requests from disk. The cache has a 1GB LRU budget and removes the least-recently-used media when full.

## Product

- Browse and filter character listings in the marketplace
- Manage a personal harem and list eligible characters for sale
- View wallet balance and transfer coins
- Configure the bot connection and manage marketplace settings as the owner

## User preferences

- Match the uploaded reference with a compact dark/purple Telegram Mini App style.
- Keep Market, Harem, Wallet, and Admin in the hamburger-opened main menu.

## Gotchas

- `/market` intentionally returns the maintenance page until `MONGO_URI` is configured.
- Admin is only shown to the configured owner.
- Do not put bot tokens, MongoDB URIs, or API keys in source control.
- Character records may use `img_url`, `image_url`, `photo`, `video_url`, `vd_url`, or Telegram file-id variants; media rendering normalizes these fields automatically.

## Pointers

- See `README.md` for Render deployment notes and Telegram bot connection details.
