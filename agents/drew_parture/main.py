from flask import Flask, request, jsonify
from jarvis_core.config import TIMEZONE
from dotenv import load_dotenv
import os
import json
import requests
from datetime import datetime, timedelta
import pytz
import time

load_dotenv()

from calendar_reader import get_todays_flights, get_upcoming_flights, get_flights_to_track, invalidate_cache
from flight_tracker import get_live_flight, parse_flight_status, format_time
from agent import chat
from jarvis_core.voice import split_voice
from uber import parse_uber_request, format_uber_message

app = Flask(__name__)

TELEGRAM_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN")
RECIPIENT_CHAT_ID = os.getenv("RECIPIENT_CHAT_ID")
ALLOWED_CHAT_IDS  = [x.strip() for x in os.getenv("ALLOWED_CHAT_IDS", "").split(",")]
GIO_CHAT_ID       = os.getenv("GIO_CHAT_ID", "")
KENZ_CHAT_ID      = os.getenv("KENZ_CHAT_ID", "")
TELEGRAM_API      = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

PT = pytz.timezone(TIMEZONE)

conversation_histories = {}
last_mentioned_flight  = {}

# ── Alert state (persisted to disk so restarts don't cause duplicates) ──────
# Lives next to the code, not in /tmp, which the Pi wipes on reboot.
ALERT_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alert_state.json")
ALERT_STATE_MAX_AGE_DAYS = 3

def load_alert_state():
    try:
        with open(ALERT_STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_alert_state(state):
    try:
        with open(ALERT_STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"[State] Failed to save alert state: {e}", flush=True)

def prune_alert_state():
    """Drop alert state for flights that departed more than a few days ago.
    Replaces the old daily wipe, which re-sent departed/landed alerts for a
    red-eye still in the air at 7am. Keys are "<flight>|<YYYY-MM-DD>"; any
    old-format key (flight number only) is dropped."""
    state = load_alert_state()
    cutoff = (datetime.now(PT) - timedelta(days=ALERT_STATE_MAX_AGE_DAYS)).date().isoformat()
    kept = {k: v for k, v in state.items() if "|" in k and k.split("|", 1)[1] >= cutoff}
    save_alert_state(kept)
    print(f"[State] Alert state pruned: {len(state)} → {len(kept)} entries.", flush=True)


# ── Telegram helpers ──────────────────────────────────────────────────────────

def send_telegram(chat_id, message):
    url     = f"{TELEGRAM_API}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
        print(f"Sent to {chat_id}: {message[:60]}...", flush=True)
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)

def send_to_recipient(message):
    send_telegram(RECIPIENT_CHAT_ID, message)


# ── Uber helpers ──────────────────────────────────────────────────────────────

def is_uber_request(text):
    return any(w in text.lower() for w in ["uber", "ride to", "car to", "take me to", "pick me up"])

def get_relevant_flight(text, chat_id):
    if chat_id in last_mentioned_flight:
        return last_mentioned_flight[chat_id]
    if any(w in text.lower() for w in ["airport", "lax", "sfo", "flight"]):
        flights = get_todays_flights()
        if flights:
            return flights[0]
        upcoming = get_upcoming_flights(days_ahead=7)
        if upcoming:
            return upcoming[0]
    return None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return "Drew Parture is running! ✈️", 200

@app.route("/health")
def health():
    return "ok", 200

def handle_telegram_update(update):
    """Process one Telegram update. Called by the Telegram long-poller."""
    message = update.get("message", {})
    chat_id = str(message.get("chat", {}).get("id", ""))
    text    = (message.get("text") or "").strip()

    if chat_id not in ALLOWED_CHAT_IDS:
        print(f"Rejected message from: {chat_id}", flush=True)
        return

    if not text:
        return

    if chat_id not in conversation_histories:
        conversation_histories[chat_id] = []
    history = conversation_histories[chat_id]

    try:
        if is_uber_request(text) and chat_id == GIO_CHAT_ID:
            flight       = get_relevant_flight(text, chat_id)
            request_data = parse_uber_request(text, flight_context=flight)
            if request_data.get("for_airport") and not request_data.get("destination_lat"):
                request_data.update({
                    "destination_name":  "LAX Airport",
                    "destination_lat":   33.9416,
                    "destination_lng":   -118.4085,
                    "destination_line1": "Los Angeles International Airport",
                    "destination_line2": "Los Angeles, CA 90045"
                })
            reply = format_uber_message(request_data, flight)
            send_telegram(chat_id, reply)

        elif is_uber_request(text) and chat_id == KENZ_CHAT_ID:
            send_telegram(chat_id, "Sorry, Uber booking is only available for Gio!")

        else:
            reply = chat(text, history)

            if any(w in reply.lower() for w in ["flight", "departs", "wn", "delta", "united", "alaska", "southwest"]):
                upcoming = get_upcoming_flights(days_ahead=60)
                if upcoming:
                    for flight in upcoming:
                        if (flight.get("flight_number", "").lower() in reply.lower() or
                                flight.get("date", "") in reply):
                            last_mentioned_flight[chat_id] = flight
                            break
                    else:
                        last_mentioned_flight[chat_id] = upcoming[0]

            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": reply})
            if len(history) > 10:
                conversation_histories[chat_id] = history[-10:]

            send_telegram(chat_id, reply)

    except Exception as e:
        print(f"ERROR: {str(e)}", flush=True)
        send_telegram(chat_id, "Sorry, I ran into an error. Please try again.")


@app.route("/jarvis", methods=["POST"])
def jarvis_handler():
    secret = request.headers.get("X-Jarvis-Secret", "")
    if secret != os.environ.get("JARVIS_SECRET", ""):
        return jsonify({"error": "unauthorized"}), 401

    data    = request.get_json() or {}
    intent  = data.get("intent", "")
    speaker = data.get("speaker", "gio")
    voice_mode = data.get("voice_mode", False)
    voice_text = None
    print(f"[Drew Parture] Received: {intent[:80]}", flush=True)

    try:
        if any(w in intent.lower() for w in ["refresh flights", "update flights", "reload flights"]):
            invalidate_cache()
            response_text = "Flight data refreshed, sir."
            voice_text = "Flight data refreshed."
        else:
            # Code-formatted quick answers carry airport codes and return no
            # VOICE line, so the router's shortening pass still handles them
            response_text, voice_text = split_voice(chat(intent, [], speaker=speaker, voice_mode=voice_mode))
        print(f"[Drew Parture] Response: {response_text[:80]}", flush=True)
    except Exception as e:
        import traceback
        print(f"[Drew Parture] Error: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        response_text = "Something went wrong checking your flights."

    return jsonify({"response_text": response_text, "voice_text": voice_text, "notify_target": None})


@app.route("/daily-init", methods=["POST", "GET"])
def daily_init():
    print("Daily init triggered — checking for flights...", flush=True)
    try:
        prune_alert_state()
        invalidate_cache()
        morning_flight_check()
        flights = get_todays_flights()
        if flights:
            return jsonify({"status": "ok", "flights_found": len(flights)})
        return jsonify({"status": "ok", "flights_found": 0, "message": "No flights today"})
    except Exception as e:
        print(f"Daily init error: {e}", flush=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/flight-check", methods=["POST", "GET"])
def flight_check():
    """Runs every 30 minutes. Handles pre-departure, delay, departure, and arrival alerts.

    Uses a rolling window (departed up to 12h ago → next 36h), NOT the 9AM
    window of the morning heads-up: a 7–9AM flight is skipped by the 7AM
    heads-up on purpose, but still gets its live alerts, and a red-eye still
    gets its landing alert after midnight."""
    print("Flight check triggered...", flush=True)
    try:
        flights = get_flights_to_track()
        if not flights:
            print("No flights to track, nothing to check.", flush=True)
            return jsonify({"status": "ok", "message": "No flights to track"})

        now          = datetime.now(PT)
        state        = load_alert_state()
        state_updated = False

        for flight in flights:
            flight_num = flight.get("flight_number", "")
            if not flight_num:
                print("Skipping flight with no flight number.", flush=True)
                continue

            dep_str = flight.get("departure_time", "")
            try:
                dep_dt = datetime.fromisoformat(dep_str).astimezone(PT)
            except Exception:
                print(f"Could not parse departure time for {flight_num}, skipping.", flush=True)
                continue

            # Keyed by flight + date: flight numbers repeat on other days
            state_key     = f"{flight_num}|{dep_dt.date().isoformat()}"
            flight_state  = state.setdefault(state_key, {})
            time_to_dep   = (dep_dt - now).total_seconds() / 60  # minutes until departure

            # Fully tracked — don't spend AviationStack calls on it
            if flight_state.get("arrived_alerted"):
                continue

            # First seen after it already left (e.g. service was down):
            # skip the stale "on time" message, but keep tracking departed/landed
            if time_to_dep <= 0 and not flight_state.get("predep_alerted"):
                flight_state["predep_alerted"] = True
                flight_state["last_delay_alerted"] = 0
                state_updated = True

            # ── Pre-departure check (2 hours before, once only) ───────────────
            if time_to_dep <= 120 and not flight_state.get("predep_alerted"):
                live   = get_live_flight(flight_num, dep_dt)
                status = parse_flight_status(live)

                if status:
                    delay_minutes = status.get("delay_minutes") or 0
                    if delay_minutes > 10:
                        new_dep = format_time(status.get("estimated_departure") or status.get("scheduled_departure"))
                        new_arr = format_time(status.get("estimated_arrival") or status.get("scheduled_arrival"))
                        msg  = f"⏰ *{flight_num} is delayed*\n\n"
                        msg += f"New departure: *{new_dep}*\n"
                        msg += f"New arrival: *{new_arr}*\n"
                        msg += f"Delay: {delay_minutes} min"
                    else:
                        dep_time = format_time(status.get("scheduled_departure"))
                        arr_time = format_time(status.get("scheduled_arrival"))
                        msg  = f"✅ *{flight_num} is on time*\n\n"
                        msg += f"Departs: *{dep_time}*\n"
                        msg += f"Arrives: *{arr_time}*"
                else:
                    # No live data yet — still send a basic on-time message from calendar
                    dep_time = format_time(dep_dt)
                    msg  = f"✅ *{flight_num} is on time*\n\n"
                    msg += f"Departs: *{dep_time}*"

                send_to_recipient(msg)
                flight_state["predep_alerted"] = True
                flight_state["last_delay_alerted"] = 0
                state_updated = True

            # ── Ongoing checks after pre-departure alert fired ────────────────
            elif flight_state.get("predep_alerted"):
                live   = get_live_flight(flight_num, dep_dt)
                status = parse_flight_status(live)

                if not status:
                    print(f"No live status for {flight_num}.", flush=True)
                    continue

                flight_status = status.get("flight_status", "")
                delay_minutes = status.get("delay_minutes") or 0
                actual_dep    = status.get("actual_departure")
                scheduled_dep = status.get("scheduled_departure")
                estimated_dep = status.get("estimated_departure")
                actual_arr    = status.get("actual_arrival")
                scheduled_arr = status.get("scheduled_arrival")
                estimated_arr = status.get("estimated_arrival")

                # Departed / landed are keyed off the actual wheels-up / wheels-down
                # timestamps, which AviationStack's free tier populates far earlier and
                # more reliably than the `flight_status` string (that can lag a day or
                # stay "scheduled" even after a real actual_departure appears). Status
                # is kept only as a fallback signal.
                has_departed = bool(actual_dep) or flight_status in ["active", "landed"]
                has_landed   = bool(actual_arr) or flight_status == "landed"

                # Departure alert
                if has_departed and not flight_state.get("departed_alerted"):
                    dep_time = format_time(actual_dep or scheduled_dep)
                    arr_time = format_time(estimated_arr or scheduled_arr)
                    msg  = f"🛫 *Gio's flight has departed!*\n\n"
                    msg += f"{flight_num} · {status['origin']} → {status['destination']}\n"
                    msg += f"Departed *{dep_time}* · Expected arrival *{arr_time}*"
                    send_to_recipient(msg)
                    flight_state["departed_alerted"] = True
                    state_updated = True

                # Delay update (only if not yet departed, and delay grew meaningfully)
                elif not has_departed and delay_minutes > 10:
                    last_delay = flight_state.get("last_delay_alerted", 0)
                    if delay_minutes > last_delay + 9:
                        new_dep = format_time(estimated_dep or scheduled_dep)
                        new_arr = format_time(estimated_arr or scheduled_arr)
                        msg  = f"⏰ *Delay update: {flight_num}*\n\n"
                        msg += f"New departure: *{new_dep}*\n"
                        msg += f"New arrival: *{new_arr}*\n"
                        msg += f"Delay: {delay_minutes} min"
                        send_to_recipient(msg)
                        flight_state["last_delay_alerted"] = delay_minutes
                        state_updated = True

                # Arrival alert
                if has_landed and not flight_state.get("arrived_alerted"):
                    arr_time = format_time(actual_arr or scheduled_arr)
                    msg  = f"🛬 *Gio has landed!*\n\n"
                    msg += f"{flight_num} · Landed *{arr_time}*\n"
                    msg += f"{status.get('destination_airport', '')} ({status['destination']})"
                    send_to_recipient(msg)
                    flight_state["arrived_alerted"] = True
                    state_updated = True

            if state_updated:
                save_alert_state(state)
                state_updated = False

        # Catch updates left unsaved by a `continue` above
        if state_updated:
            save_alert_state(state)

        return jsonify({"status": "ok"})

    except Exception as e:
        import traceback
        print(f"Flight check error: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/test-flight")
def test_flight():
    morning_flight_check()
    return "Flight check triggered!", 200


# ── Morning flight check ──────────────────────────────────────────────────────

def morning_flight_check():
    """Sends a simple heads-up message for each flight today. No status lookup."""
    print("Running morning flight check...", flush=True)
    flights = get_todays_flights()
    if not flights:
        print("No flights today.", flush=True)
        return

    now          = datetime.now(PT)
    window_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    window_end   = window_start + timedelta(hours=24)

    for flight in flights:
        flight_num = flight.get("flight_number", "")
        if not flight_num:
            print("Skipping flight with no flight number.", flush=True)
            continue

        dep_str = flight.get("departure_time", "")
        try:
            dep_dt = datetime.fromisoformat(dep_str).astimezone(PT)
        except Exception as e:
            print(f"Could not parse time for {flight_num}: {e} — skipping.", flush=True)
            continue

        # Intentional: flights 7–9AM today are skipped (already on the way at
        # 7AM); anything before 9AM tomorrow, incl. pre-7AM, is announced now
        # since tomorrow's heads-up would come too late.
        if not (window_start <= dep_dt < window_end):
            print(f"Skipping {flight_num} — outside 9AM window.", flush=True)
            continue

        destination = flight.get("destination", "")
        dep_time    = dep_dt.strftime("%-I:%M %p")
        day_word    = "today" if dep_dt.date() == now.date() else "tomorrow"

        msg = f"✈️ Gio has a flight to *{destination}* {day_word} at *{dep_time}*"
        send_to_recipient(msg)
        print(f"Morning brief sent for {flight_num}.", flush=True)
