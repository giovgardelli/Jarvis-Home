import os
from jarvis_core.config import TIMEZONE
import anthropic
from datetime import datetime
import pytz
from calendar_reader import get_upcoming_flights

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT_GIO = """You are Drew Parture, a friendly flight tracking assistant for Gio.

You track Gio's flights from his Google Calendar.
When Gio asks about "my flight", "my next flight", etc — he is asking about his own flights.
CRITICAL: You ONLY report flights. Never mention appointments, meetings, car service, dentist, or any non-flight calendar events.
If the calendar data contains non-flight events, ignore them completely.

Rules:
- Keep responses short and conversational
- Always refer to flights as "your flight" when talking to Gio
- Always convert times to Pacific Time (PT)
- If no flights are found, say so clearly
- Format dates clearly e.g. "Monday May 25th"
- Say morning/afternoon/evening instead of exact times unless asked
- Never mention flight numbers or airport codes unless specifically asked"""

SYSTEM_PROMPT_KENZ = """You are Drew Parture, a friendly flight tracking assistant for Kenz.

You track flights for Kenz's husband Gio from their shared Google Calendar.
When Kenz asks about "my husband's flight", "Gio's flight", "his flight" — she is asking about Gio's flights.
CRITICAL: You ONLY report flights. Never mention appointments, meetings, car service, dentist, or any non-flight calendar events.
If the calendar data contains non-flight events, ignore them completely.

Rules:
- Keep responses short and conversational
- Always refer to flights as "Gio's flight" or "your husband's flight" when talking to Kenz
- Always convert times to Pacific Time (PT)
- If no flights are found, say so clearly
- Format dates clearly e.g. "Monday May 25th"
- Say morning/afternoon/evening instead of exact times unless asked
- Never mention flight numbers or airport codes unless specifically asked"""


def format_flight(flight):
    """Format a flight for display."""
    pt = pytz.timezone(TIMEZONE)

    try:
        dep = datetime.fromisoformat(flight["departure_time"])
        dep_pt = dep.astimezone(pt)
        dep_str = dep_pt.strftime("%A %B %-d · %I:%M %p PT")
    except:
        dep_str = flight["departure_time"]

    try:
        arr = datetime.fromisoformat(flight["arrival_time"])
        arr_pt = arr.astimezone(pt)
        arr_str = arr_pt.strftime("%I:%M %p PT")
    except:
        arr_str = flight["arrival_time"]

    line = f"✈️ {flight.get('airline_name', '')} {flight.get('flight_number', '')}"
    if flight.get('origin') and flight.get('destination'):
        line += f" · {flight['origin']} → {flight['destination']}"
    line += f"\n📅 {dep_str}"
    if arr_str:
        line += f" → {arr_str}"
    if flight.get('confirmation'):
        line += f"\n🎫 Confirmation: {flight['confirmation']}"
    return line


def build_context():
    flights = get_upcoming_flights(days_ahead=90)
    today = datetime.now(pytz.timezone(TIMEZONE))

    if not flights:
        return f"Today is {today.strftime('%A %B %-d, %Y')}. No upcoming flights found in the next 90 days. Do not mention any appointments, meetings, or events — there are none in the system."

    lines = [
        f"Today is {today.strftime('%A %B %-d, %Y')}.",
        f"The following is the COMPLETE and EXHAUSTIVE list of upcoming flights. There are exactly {len(flights)} flight(s). Do not mention any other events, appointments, or activities.",
        ""
    ]
    for f in flights:
        lines.append(format_flight(f))
        lines.append("")

    return "\n".join(lines)


def format_flight_spoken(flight, speaker="gio"):
    """Format a single flight for voice — no hallucination possible."""
    pt = pytz.timezone(TIMEZONE)

    airline = flight.get("airline_name", "")
    destination = flight.get("destination", "")
    dep_raw = flight.get("departure_time", "")

    try:
        dep = datetime.fromisoformat(dep_raw).astimezone(pt)
        days_away = (dep.date() - datetime.now(pt).date()).days
        if days_away == 0:
            date_str = "today"
        elif days_away == 1:
            date_str = "tomorrow"
        elif days_away <= 7:
            date_str = dep.strftime("%A")
        else:
            date_str = dep.strftime("%B %-d")
        hour = dep.hour
        if hour < 12:
            time_of_day = "morning"
        elif hour < 17:
            time_of_day = "afternoon"
        else:
            time_of_day = "evening"
        date_str = f"{date_str} {time_of_day}"
    except Exception:
        date_str = dep_raw

    pronoun = "Your" if speaker == "gio" else "Gio's"
    return f"{pronoun} next flight is {airline} to {destination} {date_str}."


def _select_flight_for_timeframe(flights, user_message):
    """
    Pick the flight that matches the timeframe in the question.
    - "today" / "tonight" → a flight departing today (PT)
    - "tomorrow" → a flight departing tomorrow (PT)
    - otherwise → the next upcoming flight
    Returns (flight, found_in_timeframe_bool).
    """
    pt = pytz.timezone(TIMEZONE)
    today = datetime.now(pt).date()
    lower = user_message.lower()

    target_date = None
    if "tomorrow" in lower:
        from datetime import timedelta
        target_date = today + timedelta(days=1)
    elif "today" in lower or "tonight" in lower:
        target_date = today

    if target_date is not None:
        for f in flights:
            try:
                dep = datetime.fromisoformat(f.get("departure_time", "")).astimezone(pt)
                if dep.date() == target_date:
                    return f, True
            except Exception:
                continue
        # Asked about a specific day but nothing matched
        return None, False

    # No specific timeframe — return the next upcoming flight
    return flights[0], True


from jarvis_core.voice import VOICE_INSTRUCTION, split_voice  # noqa: E402


# Simple next-flight queries are answered in code with an already-spoken sentence
SIMPLE_FLIGHT_QUERIES = ["next flight", "upcoming flight", "when is my flight", "do i have a flight", "my flight tonight", "flight tonight", "flight today", "flight this", "what time is my flight", "time does my flight"]


def is_simple_flight_query(user_message):
    lower = user_message.lower()
    return any(q in lower for q in SIMPLE_FLIGHT_QUERIES)


def chat(user_message, history=[], speaker="gio", voice_mode=False):
    """Returns the reply text. With voice_mode, a Claude-written reply may end
    with a VOICE| line — callers split it off with split_voice()."""
    context = build_context()
    lower = user_message.lower()

    # For simple next-flight queries, bypass Claude and format directly
    if is_simple_flight_query(user_message):
        flights = get_upcoming_flights()
        if not flights:
            return "You have no upcoming flights."

        flight, found = _select_flight_for_timeframe(flights, user_message)

        if not found:
            # They asked about a specific day with no flight that day
            if "tomorrow" in lower:
                return "No flight tomorrow." if speaker == "gio" else "Gio has no flight tomorrow."
            return "No flight today." if speaker == "gio" else "Gio has no flight today."

        return format_flight_spoken(flight, speaker)

    system = SYSTEM_PROMPT_GIO if speaker == "gio" else SYSTEM_PROMPT_KENZ
    response = client.messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=300,
        system=system,
        messages=[
            *history,
            {"role": "user", "content": f"{context}\n\nQuestion: {user_message}" + (VOICE_INSTRUCTION if voice_mode else "")}
        ]
    )
    return response.content[0].text.strip()