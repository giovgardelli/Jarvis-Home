import os
from jarvis_core.config import TIMEZONE
import requests
from datetime import datetime
import pytz

AVIATIONSTACK_KEY = os.getenv("AVIATIONSTACK_API_KEY")
PT = pytz.timezone(TIMEZONE)


def get_live_flight(flight_number, target_departure=None):
    """
    Get live flight status from AviationStack.
    If target_departure (a timezone-aware datetime) is provided, pick the rotation
    whose scheduled departure is closest to it — flight numbers repeat within a day.
    """
    try:
        url = "http://api.aviationstack.com/v1/flights"
        params = {
            "access_key": AVIATIONSTACK_KEY,
            "flight_iata": flight_number
        }
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        flights = data.get("data", [])
        print(f"[AviationStack] {flight_number} → status:{resp.status_code} results:{len(flights)}", flush=True)

        if not flights:
            return None

        # No target given — fall back to first result (old behavior)
        if target_departure is None:
            return flights[0]

        # Pick the rotation whose scheduled departure is closest to the calendar time
        best_flight = None
        best_gap    = None
        for f in flights:
            sched_str = f.get("departure", {}).get("scheduled")
            if not sched_str:
                continue
            try:
                sched_dt = datetime.fromisoformat(sched_str.replace("Z", "+00:00")).astimezone(PT)
            except Exception:
                continue
            gap = abs((sched_dt - target_departure).total_seconds())
            if best_gap is None or gap < best_gap:
                best_gap     = gap
                best_flight  = f

        # If the closest match is still more than 6 hours off, it's not our flight
        if best_flight is None or best_gap > 6 * 3600:
            print(f"[AviationStack] No rotation within 6h of target {target_departure}. Best gap: {best_gap}", flush=True)
            return None

        print(f"[AviationStack] Matched rotation {best_gap/60:.0f} min from calendar time.", flush=True)
        return best_flight

    except Exception as e:
        print(f"[AviationStack] Error for {flight_number}: {e}", flush=True)
        return None


def parse_flight_status(live_data):
    """Parse live flight data into a clean status dict."""
    if not live_data:
        return None
    pt = pytz.timezone(TIMEZONE)

    def to_pt(dt_str):
        if not dt_str:
            return None
        try:
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            return dt.astimezone(pt)
        except:
            return None

    departure = live_data.get("departure", {})
    arrival   = live_data.get("arrival", {})
    flight    = live_data.get("flight", {})
    airline   = live_data.get("airline", {})

    status = dict()
    status["flight_number"]       = flight.get("iata", "")
    status["airline"]             = airline.get("name", "")
    status["flight_status"]       = live_data.get("flight_status", "")
    status["origin"]              = departure.get("iata", "")
    status["destination"]         = arrival.get("iata", "")
    status["origin_airport"]      = departure.get("airport", "")
    status["destination_airport"] = arrival.get("airport", "")
    status["terminal"]            = departure.get("terminal", "")
    status["gate"]                = departure.get("gate", "")
    status["scheduled_departure"] = to_pt(departure.get("scheduled"))
    status["actual_departure"]    = to_pt(departure.get("actual"))
    status["estimated_departure"] = to_pt(departure.get("estimated"))
    status["scheduled_arrival"]   = to_pt(arrival.get("scheduled"))
    status["actual_arrival"]      = to_pt(arrival.get("actual"))
    status["estimated_arrival"]   = to_pt(arrival.get("estimated"))
    status["delay_minutes"]       = departure.get("delay", 0) or 0
    return status


def format_time(dt):
    if not dt:
        return "N/A"
    return dt.strftime("%I:%M %p PT")