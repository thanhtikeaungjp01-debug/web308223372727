# Standalone entry point for the web app
# Run: python app.py
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

try:
    from web.app import app
except ModuleNotFoundError:
    from app import app  # type: ignore

if __name__ == "__main__":
    port = int(os.environ.get("PORT", os.environ.get("WEB_PORT", "5000")))
    print(f"\U0001F338 Waifu Market running -> http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
