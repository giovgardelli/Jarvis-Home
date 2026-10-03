import os
import gspread
from google.oauth2.credentials import Credentials
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta

_cache = {
    "property_profile": None,
    "task_library": None,
    "contractors": None,
    "warranties": None,
}
 
def _load_cache(tab=None):
    """Load one or all tabs into cache."""
    sheet = get_sheet()
    tabs = [tab] if tab else ["property_profile", "task_library", "contractors", "warranties"]
    tab_map = {
        "property_profile": "Property_Profile",
        "task_library": "Task_Library",
        "contractors": "Contractors",
        "warranties": "Warranties",
    }
    for t in tabs:
        try:
            _cache[t] = sheet.worksheet(tab_map[t]).get_all_records()
            print(f"[Cache] Andy Mann: loaded {len(_cache[t])} rows from {tab_map[t]}", flush=True)
        except Exception as e:
            print(f"[Cache] Andy Mann: error loading {t}: {e}", flush=True)
 
def invalidate_cache():
    for key in _cache:
        _cache[key] = None
    print("[Cache] Andy Mann: cache cleared", flush=True)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

TOKEN_URI = "https://oauth2.googleapis.com/token"

_spreadsheet = None

def get_sheet():
    """Open the spreadsheet once per process and reuse it (same approach as
    Miles Ahead). Re-authorizing and re-opening on every call added several
    Google round-trips per request and tripped the per-minute read quota."""
    global _spreadsheet
    if _spreadsheet is None:
        from jarvis_core.google_auth import get_credentials
        creds = get_credentials()
        client = gspread.authorize(creds)
        _spreadsheet = client.open_by_key(os.getenv("GOOGLE_SHEET_ID"))
    return _spreadsheet

def format_date_for_sheet(date_str):
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{d.month}/{d.day}/{d.year}"
    except:
        return date_str

def parse_date(date_str):
    if not date_str or str(date_str).strip() == "":
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(date_str).strip(), fmt).date()
        except:
            continue
    return None

def get_default_notice_days(frequency_value, frequency_unit):
    try:
        value = int(frequency_value)
        unit = str(frequency_unit).strip().lower()
        if unit == "days":
            total_days = value
        elif unit == "weeks":
            total_days = value * 7
        elif unit == "months":
            total_days = value * 30
        else:
            total_days = 30
        if total_days < 30:
            return 0
        elif total_days < 90:
            return 1
        else:
            return 7
    except:
        return 1

# ─── Property Profile ───────────────────────────────────────────────────────

def get_home_info():
    if _cache["property_profile"] is None:
        _load_cache("property_profile")
    rows = _cache["property_profile"]
    return "\n".join([f"{r['Field']}: {r['Value']}" for r in rows])

def add_or_update_property(field, value):
    sheet = get_sheet().worksheet("Property_Profile")
    records = sheet.get_all_records()
    for i, row in enumerate(records):
        if row["Field"].lower() == field.lower():
            sheet.update_cell(i + 2, 2, value)
            return "updated"
    sheet.append_row([field, value])
    _load_cache("property_profile")
    return "added"

def delete_property(field):
    sheet = get_sheet().worksheet("Property_Profile")
    records = sheet.get_all_records()
    for i, row in enumerate(records):
        if row["Field"].lower() == field.lower():
            sheet.delete_rows(i + 2)
            return True
    _load_cache("property_profile")
    return False

# ─── Task Library ────────────────────────────────────────────────────────────

def get_tasks():
    if _cache["task_library"] is None:
        _load_cache("task_library")
    return _cache["task_library"]

def get_tasks_with_due_dates():
    tasks = get_tasks()
    today = date.today()
    enriched = []
    for task in tasks:
        freq_value = task.get("Frequency Value", 1)
        freq_unit = task.get("Frequency Unit", "months")
        last_completed = task.get("Last Completed", "")
        next_due_raw = task.get("Next Due", "")
        next_due = parse_date(str(next_due_raw)) if next_due_raw else None
        if not next_due:
            continue
        days_until = (next_due - today).days
        notice_raw = task.get("Reminder Notice (Days)", "")
        if notice_raw == "" or notice_raw is None:
            notice_days = get_default_notice_days(freq_value, freq_unit)
        else:
            try:
                notice_days = int(notice_raw)
            except:
                notice_days = get_default_notice_days(freq_value, freq_unit)
        entry = dict()
        entry["Task"] = task["Task"]
        entry["System"] = task["System"]
        entry["Frequency Value"] = freq_value
        entry["Frequency Unit"] = freq_unit
        entry["Last Completed"] = last_completed
        entry["Next Due"] = next_due.isoformat()
        entry["Days Until Due"] = days_until
        entry["Reminder Notice (Days)"] = notice_days
        enriched.append(entry)
    return enriched

def update_last_completed(task_name, completed_date=None):
    sheet = get_sheet().worksheet("Task_Library")
    records = sheet.get_all_records()
    headers = sheet.row_values(1)
    if completed_date is None:
        completed_date = date.today().isoformat()
    completed_date = format_date_for_sheet(completed_date)
    for i, row in enumerate(records):
        if row["Task"].lower() == task_name.lower():
            col = headers.index("Last Completed") + 1
            sheet.update_cell(i + 2, col, completed_date)
            _load_cache("task_library")
            return True
    return False

def update_notice_days(task_name, notice_days):
    sheet = get_sheet().worksheet("Task_Library")
    records = sheet.get_all_records()
    headers = sheet.row_values(1)
    for i, row in enumerate(records):
        if row["Task"].lower() == task_name.lower():
            col = headers.index("Reminder Notice (Days)") + 1
            sheet.update_cell(i + 2, col, notice_days)
            return True
    _load_cache("task_library")
    return False

def delete_task(task_name):
    sheet = get_sheet().worksheet("Task_Library")
    records = sheet.get_all_records()
    for i, row in enumerate(records):
        if row["Task"].lower() == task_name.lower():
            sheet.delete_rows(i + 2)
            return True
    _load_cache("task_library")
    return False

def add_task(task, system, frequency_value, frequency_unit, last_completed="", notice_days=""):
    sheet = get_sheet().worksheet("Task_Library")
    if last_completed:
        last_completed = format_date_for_sheet(last_completed)
    if not notice_days:
        notice_days = get_default_notice_days(frequency_value, frequency_unit)
    sheet.append_row([task, system, frequency_value, frequency_unit, last_completed, notice_days])
    _load_cache("task_library")
    # ─── Contractors ─────────────────────────────────────────────────────────────

def get_contractors():
    if _cache["contractors"] is None:
        _load_cache("contractors")
    return _cache["contractors"]

def get_contractors_by_trade(trade):
    contractors = get_contractors()
    matches = []
    for c in contractors:
        if trade.lower() in str(c.get("Trade", "")).lower():
            matches.append(c)
    matches.sort(key=lambda x: int(x.get("Rank", 99) or 99))
    return matches

def add_contractor(name, trade, rank="", phone="", email="", website="", rate="", notes=""):
    sheet = get_sheet().worksheet("Contractors")
    sheet.append_row([name, trade, rank, phone, email, website, rate, notes])
    _load_cache("contractors")

def update_contractor(name, field, value):
    sheet = get_sheet().worksheet("Contractors")
    records = sheet.get_all_records()
    headers = sheet.row_values(1)
    for i, row in enumerate(records):
        if row["Name"].lower() == name.lower():
            # Case-insensitive column match
            headers_lower = [h.lower() for h in headers]
            field_lower = field.lower()
            if field_lower not in headers_lower:
                print(f"Column '{field}' not found in headers: {headers}", flush=True)
                return False
            col = headers_lower.index(field_lower) + 1
            sheet.update_cell(i + 2, col, value)
            return True
    _load_cache("contractors")
    return False

def delete_contractor(name):
    sheet = get_sheet().worksheet("Contractors")
    records = sheet.get_all_records()
    for i, row in enumerate(records):
        if row["Name"].lower() == name.lower():
            sheet.delete_rows(i + 2)
            return True
    _load_cache("contractors")
    return False

    # ─── Warranties ──────────────────────────────────────────────────────────────

def get_warranties():
    if _cache["warranties"] is None:
        _load_cache("warranties")
    return _cache["warranties"]

def get_expiring_warranties(days_ahead=90):
    warranties = get_warranties()
    today = date.today()
    expiring = []
    for w in warranties:
        expiry = w.get("Expiry Date", "")
        if not expiry or str(expiry).lower() == "lifetime":
            continue
        expiry_date = parse_date(str(expiry))
        if expiry_date:
            days_until = (expiry_date - today).days
            if days_until <= days_ahead:
                w["Days Until Expiry"] = days_until
                expiring.append(w)
    return expiring

def add_warranty(item, category, purchase_date, warranty_years, model="", serial="", cost="", vendor="", notes=""):
    sheet = get_sheet().worksheet("Warranties")
    if purchase_date:
        purchase_date = format_date_for_sheet(purchase_date)
    sheet.append_row([item, category, purchase_date, warranty_years, "", model, serial, cost, vendor, notes])
    _load_cache("warranties")

def update_warranty(item, field, value):
    sheet = get_sheet().worksheet("Warranties")
    records = sheet.get_all_records()
    headers = sheet.row_values(1)
    for i, row in enumerate(records):
        if row["Item"].lower() == item.lower():
            col = headers.index(field) + 1
            sheet.update_cell(i + 2, col, value)
            return True
    _load_cache("warranties")
    return False

def delete_warranty(item):
    sheet = get_sheet().worksheet("Warranties")
    records = sheet.get_all_records()
    for i, row in enumerate(records):
        if row["Item"].lower() == item.lower():
            sheet.delete_rows(i + 2)
            return True
    _load_cache("warranties")
    return False

def get_property_items():
    """Get list of appliance/system items from Property Profile for consistency."""
    known_systems = [
        "hvac upstairs", "hvac downstairs", "water filtration",
        "roof", "water shutoff system", "alarm system",
        "water heater", "dryer", "washer", "refrigerator"
    ]
    sheet = get_sheet().worksheet("Property_Profile")
    rows = sheet.get_all_records()
    items = []
    for row in rows:
        if row["Field"].lower() in known_systems:
            items.append(row["Field"])
    return items