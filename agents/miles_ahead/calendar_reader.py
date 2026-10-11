import os
from jarvis_core.config import TIMEZONE
import re
from datetime import datetime, timedelta
import pytz
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

AIRLINE_CODES = {
    "delta": "DL", "united": "UA", "american": "AA",
    "southwest": "WN", "jetblue": "B6", "alaska": "AS",
    "spirit": "NK", "frontier": "F9", "hawaiian": "HA",
    "sun country": "SY", "breeze": "MX", "avelo": "XP"
}

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

KNOWN_AIRPORTS = {
    "LAX", "SFO", "JFK", "LGA", "EWR", "ORD", "MDW", "DFW", "DAL", "ATL", "MIA", "FLL",
    "DEN", "LAS", "PHX", "SEA", "PDX", "SLC", "BOS", "IAD", "DCA", "BWI", "SAN", "OAK",
    "SJC", "SMF", "BUR", "LGB", "SNA", "ONT", "PSP", "SBA", "SBP", "MRY", "FAT", "STS",
    "RNO", "BOI", "GEG", "ANC", "HNL", "OGG", "KOA", "LIH", "ITO", "AUS", "MSP", "MCO",
    "TPA", "BNA", "MSY", "SAT", "HOU", "IAH", "PHL", "CLT", "DTW", "STL", "MCI",
}

# Airlines whose fares are tracked: code -> (name as Google Flights shows it,
# where to rebook). Only airlines that let you rebook at a lower fare and keep
# the difference as credit on the fares you buy. (Alaska was left out on
# purpose: no credit on Saver fares. Drew Parture still tracks its delays.)
TRACKED_AIRLINES = {
    "WN": ("Southwest", "southwest.com"),
}


def tracked_airline(flight_number):
    """(name, rebook site) for a flight number like 'AS1234', or None."""
    return TRACKED_AIRLINES.get((flight_number or "")[:2].upper())


def _is_tracked_title(title):
    """Cheap pre-filter on the event title, before fetching the full event."""
    lower = title.lower()
    return any(re.search(rf'\b{code}\s*\d', title) or name.lower() in lower
               for code, (name, _) in TRACKED_AIRLINES.items())


def parse_tracked_flight(event):
    title = event.get("summary", "")
    description = event.get("description", "")
    full_text = f"{title} {description}"

    # Only tracked airlines: flight number from the title ("WN 1234",
    # "Flight to Denver (WN 1234)"); the airline name alone isn't enough.
    flight = dict()
    for code, (name, _) in TRACKED_AIRLINES.items():
        match = re.search(rf'\b{code}\s*(\d{{1,4}})\b', title)
        if match:
            flight["flight_number"] = f"{code}{match.group(1)}"
            flight["airline"] = name
            break
    else:
        return None

    # Try to extract airports from description first
    origin = None
    destination = None

    route_match = re.search(
        r'\b([A-Z]{3})\b\s+\d{2}:\d{2}.*?-.*?\b([A-Z]{3})\b\s+\d{2}:\d{2}',
        description
    )
    if route_match:
        origin = route_match.group(1)
        destination = route_match.group(2)
        print(f"Route from description: {origin}→{destination}", flush=True)
    else:
        codes = re.findall(r'\b([A-Z]{3})\b', f"{title} {description}")
        filtered = [c for c in codes if c in KNOWN_AIRPORTS]
        if len(filtered) >= 2:
            origin = filtered[0]
            destination = filtered[-1]
            print(f"Route from airport codes: {origin}→{destination}", flush=True)

    # Fallback to title-based routing
    if not origin or not destination:
        title_lower = title.lower()
        if "san francisco" in title_lower or "sfo" in title_lower:
            origin = "LAX"
            destination = "SFO"
        elif "los angeles" in title_lower or "lax" in title_lower:
            origin = "SFO"
            destination = "LAX"
        else:
            print(f"Could not determine route from title: {title}", flush=True)
            return None
        print(f"Route from title fallback: {origin}→{destination}", flush=True)

    flight["origin"] = origin
    flight["destination"] = destination

    # Extract confirmation
    conf_match = re.search(r'confirmation[^:]*:\s*([A-Z0-9]{5,8})', full_text, re.IGNORECASE)
    flight["confirmation"] = conf_match.group(1) if conf_match else ""

    # Extract date from event start time
    start = event.get("start", {})
    flight["departure_time"] = start.get("dateTime", start.get("date", ""))
    flight["date"] = flight["departure_time"][:10] if flight["departure_time"] else ""

    print(f"Parsed: {flight['flight_number']} on {flight['date']} {flight['origin']}→{flight['destination']}", flush=True)
    return flight

def get_tracked_flights(days_ahead=60):
    """Get all tracked-airline flights in the next N days with full event details."""
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
    print(f"Total calendar events found: {len(events)}", flush=True)

    flights = []
    for event in events:
        title = event.get("summary", "")

        # Quick pre-filter — only fetch full details for potential flight events
        if not _is_tracked_title(title):
            continue

        # Fetch full event to get description
        event_id = event.get("id")
        if event_id:
            full_event = get_full_event(service, event_id)
            if full_event:
                event = full_event
                desc = event.get("description", "")
                print(f"Full event fetched for '{title}' — description length: {len(desc)}", flush=True)

        flight = parse_tracked_flight(event)
        if flight:
            print(f"{flight['airline']} flight detected: {flight['flight_number']} {flight['origin']}→{flight['destination']}", flush=True)
            flights.append(flight)

    print(f"Found {len(flights)} tracked flights in calendar", flush=True)
    return flights