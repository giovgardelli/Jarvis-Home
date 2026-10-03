from flask import Flask, request, jsonify
from jarvis_core.config import TIMEZONE
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
import os
import requests
 
load_dotenv()
 
from agent import chat
from scheduler import build_daily_message, build_weather_messages
 
app = Flask(__name__)
 
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_IDS = [x.strip() for x in os.getenv("ALLOWED_CHAT_IDS", "").split(",")]
GIO_CHAT_ID = os.getenv("GIO_CHAT_ID", "")
KENZ_CHAT_ID = os.getenv("KENZ_CHAT_ID", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
 
MAX_HISTORY = 6  # 3 exchanges
 
# Shared conversation history keyed by speaker ("gio" or "kenz")
agent_histories = {"gio": [], "kenz": []}
 
 
def get_speaker(chat_id):
    if chat_id == GIO_CHAT_ID:
        return "gio"
    elif chat_id == KENZ_CHAT_ID:
        return "kenz"
    return None
 
 
def update_history(speaker, user_message, assistant_reply):
    history = agent_histories.get(speaker, [])
    history.append({"role": "user", "content": user_message})
    history.append({"role": "assistant", "content": assistant_reply})
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    agent_histories[speaker] = history
 
 
def send_telegram(chat_id, message):
    url = f"{TELEGRAM_API}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            print(f"Markdown failed: {resp.text}, retrying as plain text", flush=True)
            payload.pop("parse_mode")
            requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)
 
 
def send_to_all(message):
    for chat_id in ALLOWED_CHAT_IDS:
        if chat_id:
            send_telegram(chat_id, message)
 
 
@app.route("/")
def home():
    return "Andy Mann Home Agent is running!", 200
 
 
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

    speaker = get_speaker(chat_id)
    if not speaker:
        # Unknown chat_id — fall back to chat_id keyed history
        speaker = chat_id

    history = agent_histories.get(speaker, [])

    try:
        result = chat(text, history, speaker=speaker)
        reply = result["response_text"]
        update_history(speaker, text, reply)
        send_telegram(chat_id, reply)
    except Exception as e:
        import traceback
        print(f"ERROR in chat: {str(e)}", flush=True)
        print(traceback.format_exc(), flush=True)
        send_telegram(chat_id, "Sorry, I ran into an error. Please try again.")


 
 
@app.route("/jarvis", methods=["POST"])
def jarvis_handler():
    secret = request.headers.get("X-Jarvis-Secret", "")
    if secret != os.environ.get("JARVIS_SECRET", ""):
        return jsonify({"error": "unauthorized"}), 401
 
    data = request.get_json() or {}
    intent = data.get("intent", "")
    speaker = data.get("speaker", "gio")
    message = data.get("params", {}).get("original_message", intent)
    voice_mode = data.get("voice_mode", False)
    print(f"[Andy Mann] Received: {message[:80]}", flush=True)
 
    history = agent_histories.get(speaker, [])
 
    try:
        result = chat(message, history, speaker=speaker, voice_mode=voice_mode)
        response_text = result["response_text"]
        update_history(speaker, message, response_text)
        print(f"[Andy Mann] status={result['status']} | {response_text[:80]}", flush=True)
    except Exception as e:
        import traceback
        print(f"[Andy Mann] Error: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        return jsonify({
            "response_text": "Something went wrong with home maintenance.",
            "status": "error",
            "tasks": [],
            "notify_target": None
        })
 
    return jsonify({
        "response_text": response_text,
        "voice_text": result.get("voice_text"),
        "status": result["status"],
        "tasks": result.get("tasks", []),
        "notify_target": None
    })
 
 
@app.route("/refresh-cache", methods=["POST"])
def refresh_cache():
    from sheets import _load_cache, invalidate_cache
    invalidate_cache()
    _load_cache()
    return jsonify({"status": "ok", "message": "Cache refreshed"})
 
 
def daily_check():
    print("Running daily maintenance check...", flush=True)
    message = build_daily_message()
    if message:
        send_to_all(message)
        print("Daily reminder sent!", flush=True)
    else:
        print("No tasks due soon.", flush=True)
 
 
def weather_check():
    print("Running weather check...", flush=True)
    messages = build_weather_messages()
    for msg in messages:
        send_to_all(msg)
        print(f"Weather alert sent: {msg[:50]}...", flush=True)
    if not messages:
        print("No weather alerts to send.", flush=True)
 
 
scheduler = BackgroundScheduler(timezone=TIMEZONE)
# Daily maintenance list is NOT sent directly anymore — it now reaches Gio/Kenz
# through Jarvis's morning brief (get_morning_data pulls andy_mann's tasks).
# scheduler.add_job(daily_check, "cron", hour=8, minute=0)
scheduler.add_job(weather_check, "cron", hour=7, minute=0)
scheduler.add_job(weather_check, "cron", hour=19, minute=0)
scheduler.start()
 
# Pre-warm cache at startup
try:
    from sheets import _load_cache
    _load_cache()
except Exception as e:
    print(f"[Startup] Cache pre-warm failed: {e}", flush=True)
 
