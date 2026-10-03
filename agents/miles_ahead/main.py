from flask import Flask, request
from jarvis_core.config import TIMEZONE
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
import os
import requests

load_dotenv()

from checker import run_daily_check
from sheets import get_flights, set_flight_status

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
RECIPIENT_CHAT_ID = os.getenv("RECIPIENT_CHAT_ID")
ALLOWED_CHAT_IDS = [x.strip() for x in os.getenv("ALLOWED_CHAT_IDS", RECIPIENT_CHAT_ID or "").split(",")]
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

def send_telegram(chat_id, message):
    url = f"{TELEGRAM_API}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
        print(f"Sent: {message[:60]}...", flush=True)
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)

def send_to_recipient(message):
    send_telegram(RECIPIENT_CHAT_ID, message)

def heartbeat_message(summary):
    """Short 'still working' ping for a clean run — flights were checked and
    none were cheaper. Returns None when nothing was actually checked."""
    n = summary.get("checked", 0)
    if n <= 0:
        return None
    return f"✅ Price check ran — {n} flight{'s' if n != 1 else ''} checked, no cheaper fares found."

def format_flight_list(flights):
    if not flights:
        return "No flights currently being tracked."
    lines = ["✈️ *Tracked Flights:*\n"]
    for f in flights:
        status = f.get("Status", "Pending")
        emoji = "⏸️" if status == "Paused" else "🟢"
        lines.append(
            f"{emoji} *{f['Flight']}* · {f['Route']} · {f['Date']}\n"
            f"   💰 Paid: ${f.get('Paid Price', '?')} | {emoji} {status}"
        )
    return "\n\n".join(lines)

def handle_command(text, chat_id):
    text_lower = text.lower().strip()

    # List flights
    if any(w in text_lower for w in ["list", "flights", "tracking", "show"]):
        flights = get_flights()
        reply = format_flight_list(flights)
        send_telegram(chat_id, reply)
        return

    # Pause a flight
    if any(w in text_lower for w in ["pause", "stop", "skip", "ignore"]):
        flights = get_flights()
        # Find flight mentioned
        for f in flights:
            flight_num = f.get("Flight", "")
            date_str = f.get("Date", "")
            if flight_num.lower() in text_lower or date_str in text_lower:
                set_flight_status(flight_num, date_str, "Paused")
                send_telegram(chat_id, f"⏸️ Paused scanning for *{flight_num}* on {date_str}.")
                return
        send_telegram(chat_id, "I couldn't find that flight. Try 'list' to see tracked flights.")
        return

    # Resume a flight
    if any(w in text_lower for w in ["resume", "restart", "unstopped", "unpause", "reactivate"]):
        flights = get_flights()
        for f in flights:
            flight_num = f.get("Flight", "")
            date_str = f.get("Date", "")
            if flight_num.lower() in text_lower or date_str in text_lower:
                set_flight_status(flight_num, date_str, "Pending")
                send_telegram(chat_id, f"▶️ Resumed scanning for *{flight_num}* on {date_str}.")
                return
        send_telegram(chat_id, "I couldn't find that flight. Try 'list' to see tracked flights.")
        return

    # Manual check
    if any(w in text_lower for w in ["check", "scan", "run"]):
        send_telegram(chat_id, "🔍 Running price check now...")
        alerts, summary = run_daily_check()
        for alert in alerts:
            send_telegram(chat_id, alert)
        if summary["deals"] == 0:
            ping = heartbeat_message(summary)
            send_telegram(chat_id, ping or "✅ No deals found right now.")
        return

    # Help
    send_telegram(chat_id,
        "✈️ *Miles Ahead Commands:*\n\n"
        "• *list* — show all tracked flights\n"
        "• *check* — run a price check now\n"
        "• *pause WN1234* — stop scanning a flight\n"
        "• *resume WN1234* — resume scanning a flight"
    )

@app.route("/")
def home():
    return "Miles Ahead is running! ✈️💰", 200

def handle_telegram_update(update):
    """Process one Telegram update. Called by the Telegram long-poller."""
    message = update.get("message", {})
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = (message.get("text") or "").strip()

    if not text:
        return

    if chat_id not in ALLOWED_CHAT_IDS:
        print(f"Rejected message from: {chat_id}", flush=True)
        return

    try:
        handle_command(text, chat_id)
    except Exception as e:
        print(f"ERROR: {str(e)}", flush=True)
        send_telegram(chat_id, "Sorry, something went wrong. Try again!")


@app.route("/jarvis", methods=["POST"])
def jarvis_handler():
    secret = request.headers.get("X-Jarvis-Secret", "")
    if secret != os.environ.get("JARVIS_SECRET", ""):
        return {"error": "unauthorized"}, 401

    data = request.get_json() or {}
    intent = data.get("intent", "")
    speaker = data.get("speaker", "gio")
    print(f"[Miles Ahead] Received: {intent[:80]}", flush=True)

    responses = []
    original_send = globals().get("send_telegram")

    def capture_send(chat_id, message):
        responses.append(message)

    import builtins
    global send_telegram
    _real_send = send_telegram
    send_telegram = capture_send

    try:
        handle_command(intent, "jarvis_internal")
    except Exception as e:
        import traceback
        print(f"[Miles Ahead] Error: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
    finally:
        send_telegram = _real_send

    response_text = "\n\n".join(responses) if responses else "Done."
    print(f"[Miles Ahead] Response: {response_text[:80]}", flush=True)
    return {"response_text": response_text, "notify_target": None}
    
@app.route("/test")
def test():
    alerts, summary = run_daily_check()
    for alert in alerts:
        send_to_recipient(alert)
    if summary["deals"] == 0:
        ping = heartbeat_message(summary)
        if ping:
            send_to_recipient(ping)
    if alerts:
        return f"Sent {len(alerts)} alerts!", 200
    return "No deals found or no flights tracked yet.", 200

def daily_check():
    print("Running Miles Ahead daily check...", flush=True)
    alerts, summary = run_daily_check()
    for alert in alerts:
        send_to_recipient(alert)
    # Heartbeat on a clean run so a silent day reads as "working", not "broken".
    if summary["deals"] == 0:
        ping = heartbeat_message(summary)
        if ping:
            send_to_recipient(ping)
    if not alerts:
        print("No alerts to send.", flush=True)

scheduler = BackgroundScheduler(timezone=TIMEZONE)
scheduler.add_job(daily_check, "cron", hour=6, minute=0)
scheduler.start()
