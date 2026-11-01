"""
state.py — persistent state for Jarvis, backed by local SQLite on the Pi.

A single local SQLite file (JARVIS_STATE_DB) with a small key-value/JSON design.

Design notes:
- Single key-value table with JSON text values. No migrations, no ORM.
- Every function degrades gracefully: if the DB is unreachable, Jarvis
  behaves exactly like the old in-memory version (empty state), never crashes.
- The stored data is only conversation history / last-response scratch /
  same-day task sources — nothing precious, so losing the file is harmless.
"""
import os
import json
import sqlite3
import threading

# Default lives with the other runtime files (JARVIS_HOME, default ~).
from jarvis_core.config import home_path

DB_PATH = os.environ.get("JARVIS_STATE_DB", home_path("jarvis_state.db"))

_conn = None
_lock = threading.Lock()


def _get_conn():
    global _conn
    try:
        if _conn is None:
            # check_same_thread=False: Flask + poller + scheduler threads share it,
            # guarded by _lock below.
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.execute("""
                CREATE TABLE IF NOT EXISTS jarvis_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            _conn.commit()
        return _conn
    except Exception as e:
        print(f"[State] DB connection error: {e}", flush=True)
        return None


def _get(key, default):
    conn = _get_conn()
    if conn is None:
        return default
    try:
        with _lock:
            cur = conn.execute("SELECT value FROM jarvis_state WHERE key = ?", (key,))
            row = cur.fetchone()
        return json.loads(row[0]) if row else default
    except Exception as e:
        print(f"[State] Read error for {key}: {e}", flush=True)
        return default


def _set(key, value):
    conn = _get_conn()
    if conn is None:
        return
    try:
        with _lock:
            conn.execute(
                """INSERT INTO jarvis_state (key, value, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(key) DO UPDATE
                   SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
                (key, json.dumps(value)),
            )
            conn.commit()
    except Exception as e:
        print(f"[State] Write error for {key}: {e}", flush=True)


# ── Public API — mirrors the old in-memory dicts ─────────────────────────────

def get_history(chat_id) -> list:
    return _get(f"history:{chat_id}", [])


def set_history(chat_id, history: list):
    _set(f"history:{chat_id}", history[-10:])


def get_last_full(chat_id):
    return _get(f"last_full:{chat_id}", None)


def set_last_full(chat_id, text):
    _set(f"last_full:{chat_id}", text)


def get_task_sources(speaker) -> dict:
    return _get(f"task_sources:{speaker}", {})


def set_task_sources(speaker, sources: dict):
    _set(f"task_sources:{speaker}", sources)


def get_brief_items(speaker) -> dict:
    """This morning's numbered tasks: {"date": "YYYY-MM-DD", "items": [{"n", "task", "list", "done"}]}"""
    return _get(f"brief_items:{speaker}", {})


def set_brief_items(speaker, brief: dict):
    _set(f"brief_items:{speaker}", brief)


def get_model_watch() -> dict:
    """Model-watch record: last_run date, today's advisory, last reported text."""
    return _get("model_watch", {})


def set_model_watch(record: dict):
    _set("model_watch", record)
