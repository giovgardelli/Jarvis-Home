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

def parse_southwest_flight(event):
    title = event.get("summary", "")
    description = event.get("description", "")
    full_text = f"{title} {description}"

    # Only process Southwest flights
    if "southwest" not in full_text.lower() and "WN" not in title:
        return None

    flight = dict()

    # Extract flight number from title
    match = re.search(r'WN\s*(\d{1,4})', title)
    if match:
        flight["flight_number"] = f"WN{match.group(1)}"
    else:
        return None

    # Try to extract airports from description first
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
            print(f"Route from description: {origin}→{destination}", flush=True)
        else:
            known = {"LAX","SFO","JFK","LGA","EWR","ORD","MDW","DFW","DAL",
                     "ATL","MIA","FLL","DEN","LAS","PHX","SEA","PDX","SLC",
                     "BOS","IAD","DCA","BWI","SAN","OAK","SJC","SMF","BUR","LGB"}
            codes = re.findall(r'\b([A-Z]{3})\b', description)
            filtered = [c for c in codes if c in known]
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

def get_southwest_flights(days_ahead=60):
    """Get all Southwest flights in the next N days with full event details."""
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
        if "WN" not in title and "southwest" not in title.lower():
            continue

        # Fetch full event to get description
        event_id = event.get("id")
        if event_id:
            full_event = get_full_event(service, event_id)
            if full_event:
                event = full_event
                desc = event.get("description", "")
                print(f"Full event fetched for '{title}' — description length: {len(desc)}", flush=True)

        flight = parse_southwest_flight(event)
        if flight:
            print(f"Southwest flight detected: {flight['flight_number']} {flight['origin']}→{flight['destination']}", flush=True)
            flights.append(flight)

    print(f"Found {len(flights)} Southwest flights in calendar", flush=True)
    return flights