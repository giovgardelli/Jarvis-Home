import os
import gspread
from datetime import date

# ── Cache ─────────────────────────────────────────────────────────────────────
_tasks_cache = None

def _load_cache():
    global _tasks_cache
    _tasks_cache = get_sheet().worksheet("Tasks").get_all_records()
    print(f"[Cache] Will Due: loaded {len(_tasks_cache)} tasks", flush=True)
    return _tasks_cache

def invalidate_cache():
    global _tasks_cache
    _tasks_cache = None
    print("[Cache] Will Due: cache cleared", flush=True)

def get_tasks():
    global _tasks_cache
    if _tasks_cache is None:
        return _load_cache()
    return _tasks_cache

# ── Auth ──────────────────────────────────────────────────────────────────────
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

# ── Reads (all use cache) ─────────────────────────────────────────────────────
def get_next_id():
    tasks = get_tasks()
    if not tasks:
        return 1
    return max(int(t.get("ID", 0)) for t in tasks) + 1

def get_filtered_tasks(owners=None):
    tasks = get_tasks()
    if owners:
        tasks = [t for t in tasks if t.get("Owner", "") in owners]
    tasks = [t for t in tasks if t.get("Status", "") != "Done"]
    tasks.sort(key=lambda x: int(x.get("Priority Score", 5)))
    return tasks

# ── Writes (update Sheet + reload cache) ──────────────────────────────────────
def add_task(task, owner, priority, priority_score, added_by):
    sheet = get_sheet().worksheet("Tasks")
    today = date.today().strftime("%m/%d/%Y")
    next_id = get_next_id()
    sheet.append_row([next_id, task, owner, priority, priority_score, added_by, today, "Pending"])
    _load_cache()
    return next_id

def complete_tasks_by_id(ids):
    sheet = get_sheet().worksheet("Tasks")
    records = sheet.get_all_records()
    headers = sheet.row_values(1)
    status_col = headers.index("Status") + 1
    completed = []
    for i, row in enumerate(records):
        if int(row.get("ID", 0)) in ids:
            sheet.update_cell(i + 2, status_col, "Done")
            completed.append(row["Task"])
    _load_cache()
    return completed

def reopen_task_by_id(task_id):
    sheet = get_sheet().worksheet("Tasks")
    records = sheet.get_all_records()
    headers = sheet.row_values(1)
    status_col = headers.index("Status") + 1
    for i, row in enumerate(records):
        if int(row.get("ID", 0)) == task_id:
            sheet.update_cell(i + 2, status_col, "Pending")
            _load_cache()
            return row["Task"]
    return None

def delete_task_by_id(task_id):
    sheet = get_sheet().worksheet("Tasks")
    records = sheet.get_all_records()
    for i, row in enumerate(records):
        if int(row.get("ID", 0)) == task_id:
            sheet.delete_rows(i + 2)
            _load_cache()
            return True
    return False
