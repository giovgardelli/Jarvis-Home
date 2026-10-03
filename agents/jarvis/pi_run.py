"""
pi_run.py — single-process entrypoint for the central Jarvis service on the Pi.

Runs:
  1. Telegram long-poller for the main Jarvis bot (no webhook or open port needed)
  2. Flask app on a localhost port, serving the endpoints the Pi voice loop
     still calls: /get-brief-text, /send-text
  3. A local scheduler:
       - 7:00am  morning briefing (was POST /morning-briefing)
       - 6:30am  Google auth health check (was POST /auth-check)
       - 6:40am  AI model deprecation/upgrade check, every 14 days (model_watch.py)

State is persisted to local SQLite (see state.py). Its routing brain
(jarvis_core/router.py, via agent.py) dispatches to the other agents at 127.0.0.1:500x
per registry.json.
"""
import os
import sys

# Shared code lives in <repo>/jarvis_core — make the repo root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_core.config import TIMEZONE  # noqa: E402

import pytz
from apscheduler.schedulers.background import BackgroundScheduler

from main import app, handle_telegram_update, run_morning_briefing, check_google_auth, TELEGRAM_TOKEN
from model_watch import maybe_run_model_watch

DEFAULT_PORT = 5006  # jarvis (central)
PT = pytz.timezone(TIMEZONE)


def _start_scheduler():
    sched = BackgroundScheduler(timezone=PT)
    sched.add_job(run_morning_briefing, "cron", hour=7, minute=0)
    sched.add_job(check_google_auth, "cron", hour=6, minute=30)
    # Runs the model check when 14+ days have passed; result goes in the 7am brief
    sched.add_job(maybe_run_model_watch, "cron", hour=6, minute=40)
    sched.start()
    print("[pi_run] Jarvis scheduler started (briefing 7am, auth-check 6:30am, model-watch 6:40am).", flush=True)


if __name__ == "__main__":
    if TELEGRAM_TOKEN:
        from jarvis_core.telegram_poll import start_polling
        start_polling(TELEGRAM_TOKEN, handle_telegram_update)
    else:
        print("[pi_run] No TELEGRAM_BOT_TOKEN set — polling disabled.", flush=True)
    _start_scheduler()
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    app.run(host="127.0.0.1", port=port, threaded=True)
