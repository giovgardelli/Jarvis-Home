import os
import gspread
from google.oauth2.credentials import Credentials
from datetime import date, datetime

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

_spreadsheet = None
_flights_ws = None

def get_sheet():
    """Open the spreadsheet once per process and reuse it. Re-opening on every
    call fired a Sheets metadata read each time and tripped Google's per-minute
    read quota (429) now that all agents share one account."""
    global _spreadsheet
    if _spreadsheet is None:
        from jarvis_core.google_auth import get_credentials
        creds = get_credentials()
        client = gspread.authorize(creds)
        _spreadsheet = client.open_by_key(os.getenv("GOOGLE_SHEET_ID"))
    return _spreadsheet

def _flights():
    """Cached 'Flights' worksheet handle (opened once)."""
    global _flights_ws
    if _flights_ws is None:
        _flights_ws = get_sheet().worksheet("Flights")
    return _flights_ws

def get_flights():
    return _flights().get_all_records()

def get_flight(flight_number, date_str):
    flights = get_flights()
    for f in flights:
        if str(f.get("Flight", "")) == flight_number and str(f.get("Date", "")) == date_str:
            return f
    return None

def add_flight(flight_number, date_str, route, departure, confirmation, paid_price):
    sheet = _flights()
    today = date.today().strftime("%m/%d/%Y")
    sheet.append_row([
        flight_number, date_str, route, departure,
        confirmation, paid_price, today, "Pending"
    ])

def update_flight_field(flight_number, date_str, field, value):
    sheet = _flights()
    records = sheet.get_all_records()
    headers = sheet.row_values(1)
    for i, row in enumerate(records):
        if str(row.get("Flight")) == flight_number and str(row.get("Date")) == date_str:
            col = headers.index(field) + 1
            sheet.update_cell(i + 2, col, value)
            return True
    return False

def set_flight_status(flight_number, date_str, status):
    return update_flight_field(flight_number, date_str, "Status", status)

def get_active_flights():
    """Get flights that should be scanned — Pending only, not Paused or Done."""
    flights = get_flights()
    today = date.today()
    active = []
    for f in flights:
        status = f.get("Status", "Pending")
        if status in ["Paused", "Done"]:
            continue
        # Skip flights that have already departed
        flight_date = f.get("Date", "")
        try:
            fd = datetime.strptime(flight_date, "%Y-%m-%d").date()
            if fd < today:
                continue
        except:
            pass
        active.append(f)
    return active

def should_check_today(flight):
    """Determine if a flight should be checked today based on smart scheduling."""
    today = date.today()
    flight_date_str = flight.get("Date", "")
    last_checked_str = flight.get("Last Checked", "")

    try:
        flight_date = datetime.strptime(flight_date_str, "%Y-%m-%d").date()
    except:
        return True

    days_until = (flight_date - today).days

    # Always check if within 24 hours of departure
    if days_until <= 1:
        return True

    # Check every 3 days otherwise
    if not last_checked_str:
        return True

    try:
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                last_checked = datetime.strptime(last_checked_str.strip(), fmt).date()
                days_since = (today - last_checked).days
                return days_since >= 3
            except:
                continue
    except:
        return True

    return True