"""
pi_run.py — single-process entrypoint for running Miles Ahead on the Pi.

Starts the Telegram long-poller (no webhook or open port needed) and serves the
/jarvis endpoint for the Pi voice router on a localhost port. The daily price
check (6am) starts when main is imported.

Note: playwright / browserbase in requirements.txt are unused — the price
source (scraper.py) is SerpApi over HTTP. Those deps can be removed.
"""
import os
import sys

# Shared code lives in <repo>/jarvis_core — make the repo root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from main import app, handle_telegram_update, TELEGRAM_TOKEN
from jarvis_core.telegram_poll import start_polling

DEFAULT_PORT = 5005  # miles_ahead

if __name__ == "__main__":
    if TELEGRAM_TOKEN:
        start_polling(TELEGRAM_TOKEN, handle_telegram_update)
    else:
        print("[pi_run] No TELEGRAM_BOT_TOKEN set — polling disabled.", flush=True)
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    app.run(host="127.0.0.1", port=port, threaded=True)
