import os
from jarvis_core.config import TIMEZONE
import re
from datetime import datetime, timedelta
import pytz
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import time

# ── Flight cache ──────────────────────────────────────────────────────────────
_flights_cache = None
_flights_cache_time = 0
_todays_flights_cache = None
_todays_flights_cache_time = 0
CACHE_TTL = 2 * 60 * 60  # 2 hours in seconds


def invalidate_cache():
    """Force cache refresh on next call."""
    global _flights_cache, _flights_cache_time
    global _todays_flights_cache, _todays_flights_cache_time
    global _tracking_cache, _tracking_cache_time
    _tracking_cache = None
    _tracking_cache_time = 0
    _flights_cache = None
    _flights_cache_time = 0
    _todays_flights_cache = None
    _todays_flights_cache_time = 0
    print("[Cache] Flight cache cleared", flush=True)

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

AIRLINE_CODES = {
    "delta": "DL", "united": "UA", "american": "AA",
    "southwest": "WN", "jetblue": "B6", "alaska": "AS",
    "spirit": "NK", "frontier": "F9", "hawaiian": "HA",
    "sun country": "SY", "breeze": "MX", "avelo": "XP"
}

KNOWN_AIRPORTS = {
    "LAX","SFO","JFK","LGA","EWR","ORD","MDW","DFW","DAL",
    "ATL","MIA","FLL","DEN","LAS","PHX","SEA","PDX","SLC",
    "BOS","IAD","DCA","BWI","SAN","OAK","SJC","SMF","BUR",
    "LGB","HNL","OGG","KOA","ITO","LIH","ANC","FAI","SNA",
    "ONT","PSP","SBA","SBP","FAT","RNO","TUS","ABQ"
}

# Common city names in airline event titles → airport code (Southwest-heavy;
# ambiguous metro areas like New York or Chicago are left out on purpose)
CITY_CODES = {
    "san francisco": "SFO", "los angeles": "LAX", "oakland": "OAK", "san jose": "SJC",
    "san diego": "SAN", "burbank": "BUR", "long beach": "LGB", "orange county": "SNA",
    "ontario": "ONT", "palm springs": "PSP", "sacramento": "SMF", "las vegas": "LAS",
    "phoenix": "PHX", "denver": "DEN", "seattle": "SEA", "portland": "PDX",
    "salt lake": "SLC", "honolulu": "HNL", "boston": "BOS", "atlanta": "ATL",
    "miami": "MIA", "dallas": "DAL",
}


def _destination_from_title(title):
    """Airport code for the destination named in an event title, or ''."""
    codes = [c for c in re.findall(r"\b([A-Z]{3})\b", title) if c in KNOWN_AIRPORTS]
    if codes:
        return codes[-1]
    lower = title.lower()
    hits = [(lower.find(city), code) for city, code in CITY_CODES.items() if city in lower]
    return max(hits)[1] if hits else ""


def get_calendar_service():
    from jarvis_core.google_auth import get_credentials
    creds = get_credentials()
    return build("calendar", "v3", credentials=creds)

def get_full_event(service, event_id):
    """Fetch complete event details including description."""
    try:
        return service.events().get(
            calendarId="primary",
            eventId=event_id
        ).execute()
    except Exception as e:
        print(f"Error fetching full event {event_id}: {e}", flush=True)
        return None

def parse_southwest_flight(event):
    """Parse a Southwest flight event."""
    title = event.get("summary", "")
    description = event.get("description", "")
    full_text = f"{title} {description}"

    if "southwest" not in full_text.lower() and "WN" not in title:
        return None

    flight = dict()
    flight["title"] = title
    flight["airline_name"] = "Southwest"
    flight["airline_code"] = "WN"

    # Extract flight number
    match = re.search(r'WN\s*(\d{1,4})', title)
    if match:
        flight["flight_number"] = f"WN{match.group(1)}"
    else:
        return None

    # Try description for airports first
    origin = None
    destination = None
    if description:
        route_match = re.search(
            r'\b([A-Z]{3})\b\s+\d{2}:\d{2}.*?-.*?\b([A-Z]{3})\b\s+\d{2}:\d{2}',
            description
        )
        if route_match:
            origin = route_match.group(1)
            destination = route_match.group(2)
        else:
            codes = re.findall(r'\b([A-Z]{3})\b', description)
            filtered = [c for c in codes if c in KNOWN_AIRPORTS]
            if len(filtered) >= 2:
                origin = filtered[0]
                destination = filtered[-1]

    # Fallback to the title: destination from an airport code or city name
    # ("Flight to San Francisco"). The origin is left blank if not stated.
    if not origin or not destination:
        origin = ""
        destination = _destination_from_title(title)

    flight["origin"] = origin or ""
    flight["destination"] = destination or ""

    # Confirmation
    conf_match = re.search(r'confirmation[^:]*:\s*([A-Z0-9]{5,8})', full_text, re.IGNORECASE)
    flight["confirmation"] = conf_match.group(1) if conf_match else ""

    # Times
    start = event.get("start", {})
    end = event.get("end", {})
    flight["departure_time"] = start.get("dateTime", start.get("date", ""))
    flight["arrival_time"] = end.get("dateTime", end.get("date", ""))
    flight["date"] = flight["departure_time"][:10] if flight["departure_time"] else ""

    return flight

def parse_flight_from_event(event):
    """Extract flight details from any airline calendar event."""
    title = event.get("summary", "")
    description = event.get("description", "")
    full_text = f"{title} {description}".lower()

    flight_keywords = ["flight", "airlines", "air lines", "airways"] + list(AIRLINE_CODES.keys())
    if not any(kw in full_text for kw in flight_keywords):
        return None

    flight = dict()
    flight["title"] = title

    # Flight number
    flight_num_pattern = r'\b([A-Z]{2}|[A-Z][A-Z0-9])\s*(\d{1,4})\b'
    match = re.search(flight_num_pattern, f"{title} {description}")
    if match:
        flight["flight_number"] = f"{match.group(1)}{match.group(2)}"
        flight["airline_code"] = match.group(1)
    else:
        flight["flight_number"] = ""
        flight["airline_code"] = ""

    # Airports
    airports = re.findall(r'\b([A-Z]{3})\b', f"{title} {description}")
    filtered = [a for a in airports if a in KNOWN_AIRPORTS]
    if len(filtered) >= 2:
        flight["origin"] = filtered[0]
        flight["destination"] = filtered[-1]
    elif len(filtered) == 1:
        flight["origin"] = filtered[0]
        flight["destination"] = ""
    else:
        flight["origin"] = ""
        flight["destination"] = ""

    # Confirmation
    conf_match = re.search(r'confirmation[^:]*:\s*([A-Z0-9]{5,8})', f"{title} {description}", re.IGNORECASE)
    flight["confirmation"] = conf_match.group(1) if conf_match else ""

    # Airline name
    for name, code in AIRLINE_CODES.items():
        if name in full_text:
            flight["airline_name"] = name.title()
            break
    else:
        flight["airline_name"] = flight.get("airline_code", "")

    # Times
    start = event.get("start", {})
    end = event.get("end", {})
    flight["departure_time"] = start.get("dateTime", start.get("date", ""))
    flight["arrival_time"] = end.get("dateTime", end.get("date", ""))
    flight["date"] = flight["departure_time"][:10] if flight["departure_time"] else ""

    return flight

_tracking_cache = None
_tracking_cache_time = 0
TRACKING_CACHE_TTL = 30 * 60  # matches the flight-check cadence


def get_flights_to_track():
    """Flights for live alerts: departed in the last 12h (so a red-eye still
    gets its landing alert after midnight) through the next 36h. Rolling
    window, independent of the 9AM morning heads-up window."""
    global _tracking_cache, _tracking_cache_time
    if _tracking_cache is not None and time.time() - _tracking_cache_time < TRACKING_CACHE_TTL:
        return _tracking_cache

    service = get_calendar_service()
    now = datetime.now(pytz.timezone(TIMEZONE))
    events_result = service.events().list(
        calendarId="primary",
        timeMin=(now - timedelta(hours=12)).isoformat(),
        timeMax=(now + timedelta(hours=36)).isoformat(),
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    flights = []
    for event in events_result.get("items", []):
        title = event.get("summary", "")
        if "WN" in title or "southwest" in title.lower():
            event_id = event.get("id")
            if event_id:
                full_event = get_full_event(service, event_id)
                if full_event:
                    event = full_event
            flight = parse_southwest_flight(event)
        else:
            flight = parse_flight_from_event(event)
        if flight:
            flights.append(flight)

    _tracking_cache = flights
    _tracking_cache_time = time.time()
    print(f"[Cache] Flights to track cached: {len(flights)} flights", flush=True)
    return flights


def get_todays_flights():
    """Get all flights departing from now through 9 AM the following day — cached for 2 hours."""
    global _todays_flights_cache, _todays_flights_cache_time
    if _todays_flights_cache is not None and time.time() - _todays_flights_cache_time < CACHE_TTL:
        print("[Cache] Returning cached today's flights", flush=True)
        return _todays_flights_cache

    service = get_calendar_service()
    pt = pytz.timezone(TIMEZONE)
    now = datetime.now(pt)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    next_day_9am = (start_of_day + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)

    events_result = service.events().list(
        calendarId="primary",
        timeMin=start_of_day.isoformat(),
        timeMax=next_day_9am.isoformat(),
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    events = events_result.get("items", [])
    flights = []
    for event in events:
        title = event.get("summary", "")
        # Southwest events need the full event fetched (for the description)
        # and a dedicated parser — same handling as get_upcoming_flights.
        if "WN" in title or "southwest" in title.lower():
            event_id = event.get("id")
            if event_id:
                full_event = get_full_event(service, event_id)
                if full_event:
                    event = full_event
            flight = parse_southwest_flight(event)
            if flight:
                flights.append(flight)
            continue
        flight = parse_flight_from_event(event)
        if flight:
            flights.append(flight)

    _todays_flights_cache = flights
    _todays_flights_cache_time = time.time()
    print(f"[Cache] Today's flights cached: {len(flights)} flights", flush=True)
    return flights

def get_upcoming_flights(days_ahead=90):
    """Get all flights in the next N days — cached for 2 hours."""
    global _flights_cache, _flights_cache_time
    if _flights_cache is not None and time.time() - _flights_cache_time < CACHE_TTL:
        print("[Cache] Returning cached upcoming flights", flush=True)
        return _flights_cache

    service = get_calendar_service()
    pt = pytz.timezone(TIMEZONE)
    now = datetime.now(pt)
    end = now + timedelta(days=days_ahead)

    events_result = service.events().list(
        calendarId="primary",
        timeMin=now.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    events = events_result.get("items", [])
    flights = []
    for event in events:
        title = event.get("summary", "")

        if "WN" in title or "southwest" in title.lower():
            event_id = event.get("id")
            if event_id:
                full_event = get_full_event(service, event_id)
                if full_event:
                    event = full_event
            flight = parse_southwest_flight(event)
            if flight:
                flights.append(flight)
            continue

        flight = parse_flight_from_event(event)
        if flight:
            flights.append(flight)

    _flights_cache = flights
    _flights_cache_time = time.time()
    print(f"[Cache] Upcoming flights cached: {len(flights)} flights", flush=True)
    return flights