import os
from jarvis_core.config import TIMEZONE
import io
import random
import threading
import requests
from datetime import datetime
import pytz
from flask import Flask, request, jsonify
from speaker import identify_speaker
from agent import jarvis_respond
from jarvis_core import state

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

STT_ENABLED = os.environ.get("STT_ENABLED", "false").lower() == "true"
TTS_ENABLED = os.environ.get("TTS_ENABLED", "false").lower() == "true"

ACKS = ["On it.", "Looking into that.", "One moment.", "Let me check."]
ACK_FILES = {
    "On it.": "acks/on_it.mp3",
    "Looking into that.": "acks/looking_into_that.mp3",
    "One moment.": "acks/one_moment.mp3",
    "Let me check.": "acks/let_me_check.mp3",
    "Right away.": "acks/right_away.mp3",
}

# Morning briefing audio stays in memory — it's regenerated daily at 7am,
# so redeploy loss is acceptable (text brief is persisted via task_sources)
daily_summary = {
    "gio": {"text": None, "audio": None, "date": None},
    "kenz": {"text": None, "audio": None, "date": None},
}
first_audio_used = {"gio": False, "kenz": False}


def pt_today():
    return datetime.now(pytz.timezone(TIMEZONE)).date()


def send_message(chat_id, text):
    try:
        resp = requests.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }, timeout=10)
        if resp.status_code != 200:
            print(f"[Telegram] Markdown send failed: {resp.text}, retrying as plain text", flush=True)
            resp2 = requests.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": chat_id,
                "text": text
            }, timeout=10)
            if resp2.status_code != 200:
                print(f"[Telegram] Plain text send also failed: {resp2.text}", flush=True)
    except Exception as e:
        print(f"[Telegram] send_message error: {e}", flush=True)


def check_google_auth():
    """
    Validate the Google OAuth refresh token and alert ONLY on a genuine
    credential failure. Distinguishes root causes so the alert (and logs)
    tell you what actually broke instead of a generic 'expired' message.
    """
    print("[Auth Check] Running Google auth health check...", flush=True)
    import time

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN", "")

    # ── Pre-flight: are the credentials even present on THIS service? ──
    missing = []
    if not client_id:
        missing.append("GOOGLE_CLIENT_ID")
    if not client_secret:
        missing.append("GOOGLE_CLIENT_SECRET")
    if not refresh_token:
        missing.append("GOOGLE_REFRESH_TOKEN")
    if missing:
        msg = f"config error — these env vars are missing on the Jarvis service: {', '.join(missing)}"
        print(f"[Auth Check] {msg}", flush=True)
        gio_chat_id = os.environ.get("GIO_CHAT_ID")
        if gio_chat_id:
            send_message(gio_chat_id, f"⚠️ Google auth {msg}. This is a setup issue, not an expired token.")
        return

    # ── Live check with retries, classifying each failure ──
    last_reason = "unknown"
    for attempt in range(3):
        try:
            resp = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token"
                },
                timeout=10
            )
            if resp.status_code == 200:
                print("[Auth Check] Token is valid.", flush=True)
                return

            # Classify the 4xx/5xx so logs explain themselves
            try:
                body = resp.json()
                err = body.get("error", "")
                desc = body.get("error_description", "")
            except Exception:
                err, desc = "unparseable", resp.text[:150]

            if err == "invalid_grant":
                last_reason = "token is expired or revoked (invalid_grant) — a NEW token is needed"
            elif err == "invalid_request":
                last_reason = f"malformed request ({desc}) — usually a blank/garbled env var, NOT a real expiry"
            elif err == "invalid_client":
                last_reason = "client ID/secret mismatch (invalid_client) — check those two env vars"
            elif resp.status_code >= 500:
                last_reason = f"Google server error {resp.status_code} — transient, not our credentials"
            else:
                last_reason = f"{err}: {desc}"

            print(f"[Auth Check] Attempt {attempt+1} failed [{resp.status_code}]: {last_reason}", flush=True)

        except Exception as e:
            last_reason = f"network error contacting Google: {e}"
            print(f"[Auth Check] Attempt {attempt+1} {last_reason}", flush=True)

        if attempt < 2:
            time.sleep(3)

    # ── All 3 attempts failed — alert with the specific reason ──
    gio_chat_id = os.environ.get("GIO_CHAT_ID")
    if gio_chat_id:
        send_message(gio_chat_id, f"⚠️ Google auth check failed: {last_reason}")

def send_voice(chat_id, audio_bytes: bytes):
    requests.post(
        f"{TELEGRAM_API}/sendVoice",
        data={"chat_id": chat_id},
        files={"voice": ("reply.mp3", io.BytesIO(audio_bytes), "audio/mpeg")},
        timeout=30
    )


def get_file_bytes(file_id: str) -> bytes:
    file_info = requests.get(f"{TELEGRAM_API}/getFile?file_id={file_id}").json()
    file_path = file_info["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
    return requests.get(file_url, timeout=30).content


def play_ack(chat_id):
    ack_text = random.choice(ACKS)
    ack_file = ACK_FILES.get(ack_text)
    if TTS_ENABLED and ack_file and os.path.exists(ack_file):
        with open(ack_file, "rb") as f:
            send_voice(chat_id, f.read())
    else:
        send_message(chat_id, ack_text)


def make_speakable(text: str, speaker: str) -> str:
    import anthropic
    name = "Gio" if speaker == "gio" else "Kenz"
    prompt = (
        "Rewrite the following message so it sounds natural when spoken aloud. "
        "Remove all markdown, bullet points, asterisks, and headers. "
        "Convert lists into natural flowing sentences. "
        "Be concise. Do not add new information. "
        "Address the person as " + name + " if needed.\n\n"
        "Message:\n" + text
    )
    c = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = c.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.content[0].text.strip()


def generate_tts(text: str) -> bytes:
    from tts import synthesize
    return synthesize(text)


def deliver_response(chat_id, response_text, speaker, voice_mode, force_text=False):
    if force_text:
        send_message(chat_id, response_text)
        return
    if voice_mode and TTS_ENABLED:
        try:
            speakable = make_speakable(response_text, speaker)
            print(f"[TTS] Speakable: {speakable}")
            audio_bytes = generate_tts(speakable)
            print(f"[TTS] Got {len(audio_bytes)} bytes, sending voice...")
            send_voice(chat_id, audio_bytes)
            print(f"[TTS] Voice sent successfully")
        except Exception as e:
            import traceback
            print(f"[TTS] Error: {e}")
            print(traceback.format_exc())
            send_message(chat_id, response_text)
    else:
        send_message(chat_id, response_text)


def build_briefing_intro(user_input: str, speaker: str) -> str:
    """Generate a contextual 1-sentence intro before the morning briefing."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    name = "Gio" if speaker == "gio" else "Kenz"
    greeting = "morning" if datetime.now(pytz.timezone(TIMEZONE)).hour < 12 else "afternoon"
    prompt = (
        "Generate a single natural spoken sentence to introduce a morning briefing to " + name + ". "
        "The user just asked: \"" + user_input + "\". "
        "Acknowledge their request briefly and transition to the briefing. "
        "Examples: "
        "'I'll get to that in a moment, but first here's your morning briefing.' "
        "'On it — but let me start with your morning update.' "
        "'Good " + greeting + "! Before I answer that, here's what's ahead today.' "
        "Be natural and varied. One sentence only. No markdown."
    )
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=80,
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.content[0].text.strip()


def process_message(chat_id: int, text: str, speaker: str, voice_mode: bool):
    """Runs in background thread."""
    try:
        today = pt_today()
        # Persistent state — survives redeploys
        history = state.get_history(chat_id)
        last_full = state.get_last_full(chat_id)
        task_sources = state.get_task_sources(speaker)

        # Morning briefing — play on first audio interaction of the day
        summary = daily_summary.get(speaker, {})
        if (voice_mode and
                not first_audio_used.get(speaker, False) and
                summary.get("audio") and
                summary.get("date") == today):
            try:
                intro_text = build_briefing_intro(text, speaker)
                intro_audio = generate_tts(intro_text)
                send_voice(chat_id, intro_audio)
                send_voice(chat_id, summary["audio"])
                first_audio_used[speaker] = True
            except Exception as e:
                print(f"[Briefing] Error playing summary: {e}")

        print(f"[Jarvis] {speaker} said: {text[:100]}", flush=True)

        result = jarvis_respond(
            user_input=text,
            speaker=speaker,
            chat_id=chat_id,
            voice_mode=voice_mode,
            history=history,
            last_full_response=last_full,
            task_sources=task_sources
        )

        response_text = result.get("response_text", "")
        full_response = result.get("full_response", response_text)
        force_text = result.get("force_text", False)

        print(f"[Jarvis] Response: {response_text[:100]}", flush=True)

        if not response_text:
            return

        # Persist updated state
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": response_text})
        state.set_history(chat_id, history)
        state.set_last_full(chat_id, full_response)

        deliver_response(chat_id, response_text, speaker, voice_mode, force_text)

    except Exception as e:
        import traceback
        print(f"[process_message] Error: {e}")
        print(traceback.format_exc())
        send_message(chat_id, "Something went wrong. Please try again.")


def handle_telegram_update(update):
    """Process one Telegram update. Called by the Telegram long-poller."""
    message = update.get("message", {})
    chat_id = message.get("chat", {}).get("id")

    if not chat_id:
        return

    speaker = identify_speaker(str(chat_id))
    if not speaker:
        return

    text = (message.get("text") or "").strip()
    if text:
        threading.Thread(
            target=process_message,
            args=(chat_id, text, speaker, False)
        ).start()
        return

    voice = message.get("voice")
    if voice and STT_ENABLED:
        try:
            play_ack(chat_id)
            audio_bytes = get_file_bytes(voice["file_id"])
            from stt import transcribe
            transcript = transcribe(audio_bytes)
            print(f"[STT] Transcript: {transcript}")
            if transcript:
                threading.Thread(
                    target=process_message,
                    args=(chat_id, transcript, speaker, True)
                ).start()
        except Exception as e:
            print(f"[STT] Error: {e}")
            send_message(chat_id, "Sorry, I couldn't process that voice message.")
        return

    if voice and not STT_ENABLED:
        send_message(chat_id, "Voice messages aren't enabled yet. Send me text for now.")


@app.route("/morning-briefing", methods=["POST", "GET"])
def morning_briefing():
    """
    Manual trigger: rebuild and resend the morning brief (it also runs daily at
    7:00 from pi_run.py's scheduler).
    """
    threading.Thread(target=run_morning_briefing).start()
    return jsonify({"status": "briefing started"})


def run_morning_briefing():
    from agent import get_morning_data
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    today = pt_today()

    print("[Briefing] Starting morning briefing...")

    cache_agents = [
        "http://127.0.0.1:5004/refresh-cache",  # will_due
        "http://127.0.0.1:5003/refresh-cache",  # shawn_ping
        "http://127.0.0.1:5001/refresh-cache",  # andy_mann
    ]
    for url in cache_agents:
        try:
            requests.post(url, headers={"X-Jarvis-Secret": os.environ.get("JARVIS_SECRET", "")}, timeout=30)
            print(f"[Briefing] Cache refreshed: {url}", flush=True)
        except Exception as e:
            print(f"[Briefing] Cache refresh failed {url}: {e}", flush=True)

    data = get_morning_data()

    # Persist raw per-agent sources — survives redeploys now
    raw_sources = data.get("raw_sources", {})
    for speaker in ["gio", "kenz"]:
        state.set_task_sources(speaker, raw_sources.get(speaker, {}))
    print(f"[Briefing] Task sources persisted for same-day lookup", flush=True)

    for speaker in ["gio", "kenz"]:
        name = "Gio" if speaker == "gio" else "Kenz"
        chat_id = os.environ.get("GIO_CHAT_ID" if speaker == "gio" else "KENZ_CHAT_ID")

        summary_prompt = (
            "You are Jarvis. Generate a personalized morning briefing for " + name + ".\n\n"
            "Data from agents:\n" + data.get(speaker, "No data available") + "\n\n"
            "Rules:\n"
            "- Telegram text format with Markdown\n"
            "- Start with a warm good morning greeting\n"
            "- Cover: top 2-3 tasks due this week, any flights today, key home maintenance\n"
            "- Be concise — this is a morning summary, not a report\n"
            "- End with something encouraging\n"
        )

        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": summary_prompt}]
        )
        text_summary = resp.content[0].text.strip()

        if chat_id:
            message_text = text_summary
            # Model upgrade/deprecation advisory (Telegram only, Gio only, only
            # on days the check found something) — kept out of the spoken brief
            if speaker == "gio":
                from model_watch import todays_advisory
                advisory = todays_advisory()
                if advisory:
                    message_text += "\n\n🔧 *Model check:*\n" + advisory
            send_message(chat_id, message_text)
            print(f"[Briefing] Sent text summary to {name}")
            # Persist the brief into conversation history
            history = state.get_history(chat_id)
            history.append({
                "role": "assistant",
                "content": f"[Morning brief]: {text_summary}"
            })
            state.set_history(chat_id, history)

        audio_prompt = (
            "Rewrite this morning briefing as a very short spoken summary for " + name + ". "
            "Maximum 40 words. Natural speech, no markdown. "
            "Just the most important thing or two — flights first if any, then top task.\n\n"
            + text_summary
        )
        audio_resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content": audio_prompt}]
        )
        audio_text = audio_resp.content[0].text.strip()

        try:
            from tts import synthesize
            audio_bytes = synthesize(audio_text)
            daily_summary[speaker] = {
                "text": text_summary,
                "audio": audio_bytes,
                "audio_text": audio_text,
                "date": today
            }
            first_audio_used[speaker] = False
            print(f"[Briefing] Audio summary stored for {name}")
        except Exception as e:
            print(f"[Briefing] TTS error for {name}: {e}")

    print("[Briefing] Morning briefing complete.")


@app.route("/get-brief-text", methods=["POST"])
def get_brief_text():
    secret = request.headers.get("X-Jarvis-Secret", "")
    if secret != os.environ.get("JARVIS_SECRET", ""):
        return jsonify({"error": "unauthorized"}), 401

    speaker = (request.get_json() or {}).get("speaker", "gio")
    summary = daily_summary.get(speaker, {})

    if not summary.get("text"):
        return jsonify({"brief_text": None, "brief_audio_text": None, "date": None})

    # "date" lets the Pi reject a stale brief (e.g. yesterday's, if it asks
    # before today's 7am briefing has finished generating)
    return jsonify({
        "brief_text": summary["text"],
        "brief_audio_text": summary.get("audio_text", summary["text"]),
        "date": summary["date"].isoformat() if summary.get("date") else None
    })


@app.route("/send-text", methods=["POST"])
def send_text():
    secret = request.headers.get("X-Jarvis-Secret", "")
    if secret != os.environ.get("JARVIS_SECRET", ""):
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    text = data.get("text", "")
    speaker = data.get("speaker", "gio")
    chat_id = os.environ.get("GIO_CHAT_ID" if speaker == "gio" else "KENZ_CHAT_ID")
    if chat_id and text:
        send_message(chat_id, text)
    return jsonify({"status": "sent"})


@app.route("/model-check", methods=["POST"])
def model_check():
    """Run the model check now and send the result to Gio (for testing)."""
    secret = request.headers.get("X-Jarvis-Secret", "")
    if secret != os.environ.get("JARVIS_SECRET", ""):
        return jsonify({"error": "unauthorized"}), 401
    from model_watch import run_model_watch
    advisory = run_model_watch()
    gio_chat_id = os.environ.get("GIO_CHAT_ID")
    if gio_chat_id:
        send_message(gio_chat_id, "🔧 *Model check:*\n" + (advisory or "No model changes recommended right now."))
    return jsonify({"advisory": advisory})


@app.route("/health")
def health():
    return "ok"


@app.route("/auth-check", methods=["POST", "GET"])
def auth_check():
    check_google_auth()
    return jsonify({"status": "ok"})


@app.route("/")
def root():
    return "ok"
