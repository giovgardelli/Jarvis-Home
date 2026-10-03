"""
telegram_poll.py — Telegram long-polling shim for Pi-hosted agents.

Each bot PULLS its updates with getUpdates instead of receiving webhooks, so
the Pi needs no public URL or open port (it stays behind your router's NAT).
Shared by all agents.

IMPORTANT: run exactly ONE poller per bot token. Telegram returns HTTP 409
Conflict if two long-polls share the same token, so never start this in more
than one process (e.g. don't run it under multi-worker gunicorn).
"""
import threading
import time
import requests


def start_polling(token, handler, drop_pending=True):
    """Start a daemon thread that long-polls Telegram and calls handler(update).

    handler: callable taking a single Telegram `update` dict.
    Returns the thread (already started).
    """
    def _loop():
        api = f"https://api.telegram.org/bot{token}"

        # Flip the bot from webhook mode to polling mode. drop_pending clears
        # any backlog that queued while the bot was offline or on a webhook.
        try:
            requests.get(
                f"{api}/deleteWebhook",
                params={"drop_pending_updates": str(drop_pending).lower()},
                timeout=10,
            )
        except Exception as e:
            print(f"[Poll] deleteWebhook failed: {e}", flush=True)

        offset = None
        while True:
            try:
                resp = requests.get(
                    f"{api}/getUpdates",
                    params={"offset": offset, "timeout": 30},
                    timeout=40,
                ).json()
                if not resp.get("ok"):
                    print(f"[Poll] getUpdates not ok: {resp}", flush=True)
                    time.sleep(3)
                    continue
                for upd in resp.get("result", []):
                    offset = upd["update_id"] + 1
                    try:
                        handler(upd)
                    except Exception as e:
                        import traceback
                        print(f"[Poll] handler error: {e}", flush=True)
                        print(traceback.format_exc(), flush=True)
            except requests.exceptions.RequestException as e:
                print(f"[Poll] network error: {e}", flush=True)
                time.sleep(3)
            except Exception as e:
                print(f"[Poll] loop error: {e}", flush=True)
                time.sleep(3)

    t = threading.Thread(target=_loop, daemon=True, name="telegram-poll")
    t.start()
    return t
