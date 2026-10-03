from flask import Flask, request, jsonify
from dotenv import load_dotenv
import os
import requests

load_dotenv()

from agent import process_message

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


def get_sender_name(chat_id):
    if chat_id == GIO_CHAT_ID:
        return "Gio"
    elif chat_id == KENZ_CHAT_ID:
        return "Kenz"
    return "Unknown"


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
            print(f"[Telegram] Markdown failed: {resp.text}, retrying plain", flush=True)
            payload.pop("parse_mode")
            requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)


@app.route("/")
def home():
    return "Will Due is running! 📋", 200


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

    if chat_id not in ALLOWED_CHAT_IDS:
        print(f"Rejected message from: {chat_id}", flush=True)
        return

    speaker = get_speaker(chat_id)
    sender_name = get_sender_name(chat_id)
    if not speaker:
        return

    history = agent_histories.get(speaker, [])

    try:
        result = process_message(text, sender_name, chat_id, history)
        reply = result["response_text"]
        update_history(speaker, text, reply)
        send_telegram(chat_id, reply)
    except Exception as e:
        import traceback
        print(f"ERROR: {str(e)}", flush=True)
        print(traceback.format_exc(), flush=True)
        send_telegram(chat_id, "Sorry, something went wrong. Try again!")


@app.route("/jarvis", methods=["POST"])
def jarvis_handler():
    secret = request.headers.get("X-Jarvis-Secret", "")
    if secret != os.environ.get("JARVIS_SECRET", ""):
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json() or {}
    intent = data.get("intent", "")
    speaker = data.get("speaker", "gio")
    sender_name = "Gio" if speaker == "gio" else "Kenz"
    chat_id = GIO_CHAT_ID if speaker == "gio" else KENZ_CHAT_ID
    message = data.get("params", {}).get("original_message", intent)
    voice_mode = data.get("voice_mode", False)
    print(f"[Will Due] Received: {message[:80]}", flush=True)

    history = agent_histories.get(speaker, [])

    try:
        result = process_message(message, sender_name, chat_id, history, voice_mode=voice_mode)
        response_text = result["response_text"]
        update_history(speaker, message, response_text)
        print(f"[Will Due] status={result['status']} | {response_text[:80]}", flush=True)
    except Exception as e:
        import traceback
        print(f"[Will Due] Error: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        return jsonify({
            "response_text": "Something went wrong with the to-do list.",
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


# Pre-warm cache at startup
try:
    from sheets import _load_cache
    _load_cache()
except Exception as e:
    print(f"[Startup] Cache pre-warm failed: {e}", flush=True)
