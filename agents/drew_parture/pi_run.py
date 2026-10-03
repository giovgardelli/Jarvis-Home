"""
pi_run.py — single-process entrypoint for running Drew Parture on the Pi.

Three things in one process:
  1. Telegram long-poller (no webhook or open port needed)
  2. Flask /jarvis endpoint for the Pi voice router (localhost)
  3. A local scheduler: /daily-init each morning and /flight-check every 30
     minutes. The jobs hit the local endpoints over HTTP so the route logic is
     shared with manual triggers.
"""
import os
import sys

# Shared code lives in <repo>/jarvis_core — make the repo root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_core.config import TIMEZONE  # noqa: E402

import requests
import pytz
from apscheduler.schedulers.background import BackgroundScheduler

from main import app, handle_telegram_update, TELEGRAM_TOKEN

DEFAULT_PORT = 5002  # drew_parture
PT = pytz.timezone(TIMEZONE)


def _hit(path):
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    try:
        requests.get(f"http://127.0.0.1:{port}{path}", timeout=120)
    except Exception as e:
        print(f"[pi_run] scheduled {path} failed: {e}", flush=True)


def _start_scheduler():
    sched = BackgroundScheduler(timezone=PT)
    # Morning heads-up + prune old alert state
    sched.add_job(lambda: _hit("/daily-init"), "cron", hour=7, minute=0)
    # Pre-departure / delay / departure / arrival checks (was every 30 min)
    sched.add_job(lambda: _hit("/flight-check"), "interval", minutes=30)
    sched.start()
    print("[pi_run] Drew scheduler started (daily-init 7am, flight-check /30min).", flush=True)


if __name__ == "__main__":
    if TELEGRAM_TOKEN:
        from jarvis_core.telegram_poll import start_polling
        start_polling(TELEGRAM_TOKEN, handle_telegram_update)
    else:
        print("[pi_run] No TELEGRAM_BOT_TOKEN set — polling disabled.", flush=True)
    _start_scheduler()
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    app.run(host="127.0.0.1", port=port, threaded=True)
