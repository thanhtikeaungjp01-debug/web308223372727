# Waifu Market — Web App

> **Important:** This application is a Python Flask service backed by MongoDB and Telegram. It is not the `artifacts/mockup-sandbox` frontend workspace and must not be deployed with `wrangler deploy` as a Cloudflare Worker.

## Correct deployment: Render Web Service

1. Create a **Web Service** on [Render](https://render.com).
2. Connect this repository, `osamudav1/Waifuweb112373`.
3. Use the included `render.yaml` Blueprint, or set:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `python app.py`
   - **Health check path:** `/healthz`
4. Set `MONGO_URI`, `BOT_TOKEN`, and `OWNER_ID`; optionally set the remaining values from `.env.example`.
5. Open the Render URL. If the app is not configured yet, it will redirect to `/setup`.

The repository includes `scripts/verify_deployment.py` for a credential-free local smoke test:

```bash
python scripts/verify_deployment.py
```

## Cloudflare setup

Cloudflare Pages/Workers cannot run this Python Flask application directly. If a `workers.dev` URL shows **“Component Preview Server”**, it is the unrelated `artifacts/mockup-sandbox` demo and means the wrong project was deployed.

Use Cloudflare only after the Render service is working:

1. Copy the Render hostname, for example `waifu-web-market.onrender.com`.
2. In Cloudflare DNS, create a CNAME record for your desired hostname pointing to that Render hostname.
3. Enable the orange-cloud proxy if desired and set SSL/TLS mode to **Full**.
4. Open the custom domain, not the unrelated `*.workers.dev` preview URL.

Do **not** select `artifacts/mockup-sandbox` as the Cloudflare Pages build directory, and do not use `wrangler deploy` for this repository. `render.yaml` is the source of truth for production deployment.

## First-time setup

1. Deploy on Render.
2. Open the web URL and complete `/setup` with `MONGO_URI`, `BOT_TOKEN`, and `OWNER_ID`.
3. For the Bot API, run `/api` in the Telegram bot to generate a key.
4. Paste the bot's Render URL and that key in the setup form.

## Health check

- URL: `https://your-web.onrender.com/healthz`
- Response: `{"status":"ok"}`

## Connecting Bot ↔ Web

- Set `MARKET_URL` in the bot to this web app's URL.
- Set `BOT_API_URL` and `BOT_API_KEY` in the web app to the bot's URL and API key.
