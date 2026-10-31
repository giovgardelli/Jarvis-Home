from flask import Flask, request, jsonify
from dotenv import load_dotenv
import os
import requests

load_dotenv()

from agent import process_message
from jarvis_core import household

app = Flask(__name__)

# Optional: Ray's own Telegram bot. Without it, Jarvis's bot and the speaker reach Ray.
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_IDS = [x.strip() for x in os.getenv("ALLOWED_CHAT_IDS", "").split(",") if x.strip()]
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


def get_speaker(chat_id):
    """Household member for a Telegram chat id (<ID>_CHAT_ID in .env)."""
    return next((m for m in household.member_ids()
                 if chat_id and os.getenv(f"{m.upper()}_CHAT_ID", "") == chat_id), None)


def send_telegram(chat_id, message):
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": message}, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)


@app.route("/")
def home():
    return "Ray Mote is running! 🌡️", 200


@app.route("/health")
def health():
    return "ok", 200


def handle_telegram_update(update):
    """Process one Telegram update. Called by the Telegram long-poller."""
    message = update.get("message", {})
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = (message.get("text") or "").strip()
    if not text:
        return
    speaker = get_speaker(chat_id)
    if chat_id not in ALLOWED_CHAT_IDS or not speaker:
        print(f"Rejected message from: {chat_id}", flush=True)
        return
    try:
        send_telegram(chat_id, process_message(text, speaker)["response_text"])
    except Exception as e:
        import traceback
        print(f"ERROR: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        send_telegram(chat_id, "Sorry, something went wrong. Try again!")


@app.route("/jarvis", methods=["POST"])
def jarvis_handler():
    secret = request.headers.get("X-Jarvis-Secret", "")
    if secret != os.environ.get("JARVIS_SECRET", ""):
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json() or {}
    speaker = data.get("speaker") or household.DEFAULT_SPEAKER
    message = data.get("params", {}).get("original_message", data.get("intent", ""))
    print(f"[Ray Mote] Received: {message[:80]}", flush=True)
    try:
        result = process_message(message, speaker)
    except Exception as e:
        import traceback
        print(f"[Ray Mote] Error: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        return jsonify({"response_text": "Something went wrong with the thermostat.", "status": "error"})
    print(f"[Ray Mote] status={result['status']} | {result['response_text'][:80]}", flush=True)
    return jsonify(result)
