import os
import gspread
from datetime import date

# ── Cache ─────────────────────────────────────────────────────────────────────
_list_cache = None

def _load_cache():
    global _list_cache
    _list_cache = get_sheet().worksheet("Shopping_List").get_all_records()
    print(f"[Cache] Shawn Ping: loaded {len(_list_cache)} items", flush=True)
    return _list_cache

def invalidate_cache():
    global _list_cache
    _list_cache = None
    print("[Cache] Shawn Ping: cache cleared", flush=True)

def get_shopping_list():
    global _list_cache
    if _list_cache is None:
        return _load_cache()
    return _list_cache

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

# ── Writes (update Sheet + reload cache) ──────────────────────────────────────
def add_item(item, added_by=""):
    sheet = get_sheet().worksheet("Shopping_List")
    records = sheet.get_all_records()
    next_num = len(records) + 1
    today = date.today().strftime("%m/%d/%Y")
    sheet.append_row([next_num, item, added_by, today])
    _load_cache()

def remove_items(numbers):
    sheet = get_sheet().worksheet("Shopping_List")
    records = sheet.get_all_records()
    rows_to_delete = []
    for i, row in enumerate(records):
        if int(row.get("#", 0)) in numbers:
            rows_to_delete.append(i + 2)
    for row_idx in sorted(rows_to_delete, reverse=True):
        sheet.delete_rows(row_idx)
    renumber_list()
    _load_cache()
    return len(rows_to_delete)

def remove_item_by_name(item_name):
    sheet = get_sheet().worksheet("Shopping_List")
    records = sheet.get_all_records()
    for i, row in enumerate(records):
        if row["Item"].lower() == item_name.lower():
            sheet.delete_rows(i + 2)
            renumber_list()
            _load_cache()
            return True
    return False

def renumber_list():
    # One batch write for the whole "#" column instead of one API call per row
    sheet = get_sheet().worksheet("Shopping_List")
    records = sheet.get_all_records()
    if not records:
        return
    # Keyword args: gspread 5.x and 6.x disagree on positional order
    sheet.update(range_name=f"A2:A{len(records) + 1}", values=[[i + 1] for i in range(len(records))])

def get_similar_items(new_item, existing_items):
    new_lower = new_item.lower()
    similar = []
    for item in existing_items:
        existing_lower = item["Item"].lower()
        new_words = set(new_lower.split())
        existing_words = set(existing_lower.split())
        common_words = new_words & existing_words
        ignore = {"the", "a", "an", "of", "for", "and", "or"}
        meaningful_common = common_words - ignore
        if meaningful_common or new_lower in existing_lower or existing_lower in new_lower:
            similar.append(item)
    return similar
