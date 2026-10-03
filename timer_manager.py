"""
timer_manager.py — Timer and alarm management for Jarvis Pi
"""
import os
from jarvis_core.config import TIMEZONE
import json
import time
import threading
from datetime import datetime
import pytz

PT = pytz.timezone(TIMEZONE)

# Active timers are saved here so a restart doesn't lose them
from jarvis_core.config import home_path
TIMERS_FILE = home_path("timers.json")
# Timers that came due while Jarvis was restarting are still announced if
# they're at most this late; older ones are dropped
LATE_GRACE_SECONDS = 15 * 60

# Active timers: {name: {"thread": Thread, "end_time": float, "cancel_event": Event, "type": "timer"|"alarm"}}
_timers = {}
_lock = threading.Lock()

# Callback set by jarvis-pi.py for audio playback
_play_callback = None
_synthesize_callback = None


def init(play_fn, synthesize_fn):
    """Called at startup to wire in audio functions, then restore saved timers."""
    global _play_callback, _synthesize_callback
    _play_callback = play_fn
    _synthesize_callback = synthesize_fn
    _restore()


_save_lock = threading.Lock()


def _save():
    """Persist active timers (name → end time + type). Caller must NOT hold _lock.
    Saves are serialized (timer threads can fire while another save runs) and
    the snapshot is taken inside the save lock, so the newest state wins."""
    with _save_lock:
        with _lock:
            data = {n: {"end_time": e["end_time"], "type": e["type"]} for n, e in _timers.items()}
        try:
            tmp = TIMERS_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, TIMERS_FILE)
        except Exception as e:
            print(f"[Timer] Save error: {e}", flush=True)


def _restore():
    """Re-arm timers saved before a restart."""
    try:
        with open(TIMERS_FILE) as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return
    now = time.time()
    for name, entry in saved.items():
        end_time, kind = entry["end_time"], entry.get("type", "timer")
        if end_time > now:
            _schedule(name, end_time, kind, save=False)
            print(f"[Timer] Restored '{name}' ({int(end_time - now)}s left)", flush=True)
        elif now - end_time <= LATE_GRACE_SECONDS:
            _schedule(name, now, kind, late=True, save=False)
            print(f"[Timer] '{name}' came due during restart — announcing now", flush=True)
    _save()  # drops expired entries


def _fire(name, kind="timer", late=False):
    """Called when a timer expires. Plays audio notification."""
    print(f"[Timer] '{name}' fired!", flush=True)
    with _lock:
        _timers.pop(name, None)
    _save()
    if _synthesize_callback and _play_callback:
        try:
            if kind == "alarm":
                label = "your alarm" if name.lower() == "alarm" else f"your {name} alarm"
            else:
                label = "your timer" if name.lower() == "timer" else f"your {name} timer"
            if late:
                text = f"Sorry, {label} went off while I was restarting."
            else:
                text = f"Time's up — {label} is done." if kind != "alarm" else f"It's time — {label}."
            audio = _synthesize_callback(text)
            _play_callback(audio)  # waits its turn if Jarvis is mid-reply
        except Exception as e:
            print(f"[Timer] Audio error: {e}", flush=True)


def _run_until(name, end_time, kind, cancel_event, late):
    """Background thread: wait until end_time unless cancelled, then fire."""
    delay = end_time - time.time()
    if delay > 0 and cancel_event.wait(timeout=delay):
        return  # cancelled
    _fire(name, kind, late)


def _schedule(name, end_time, kind, late=False, save=True):
    """Start (or replace) a named timer/alarm ending at end_time."""
    cancel_existing(name, save=False)
    cancel_event = threading.Event()
    thread = threading.Thread(
        target=_run_until, args=(name, end_time, kind, cancel_event, late), daemon=True
    )
    with _lock:
        _timers[name] = {"thread": thread, "cancel_event": cancel_event,
                         "end_time": end_time, "type": kind}
    thread.start()
    if save:
        _save()


def set_timer(name: str, seconds: int) -> str:
    """Set a countdown timer. Returns confirmation string."""
    name = name.lower().strip()
    _schedule(name, time.time() + seconds, "timer")

    hours = seconds // 3600
    mins = (seconds % 3600) // 60
    secs = seconds % 60
    parts = []
    if hours > 0:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins > 0:
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    if secs > 0 and hours == 0:
        parts.append(f"{secs} second{'s' if secs != 1 else ''}")
    duration_str = " and ".join(parts)

    return f"Timer set — I'll let you know in {duration_str}." if name == "timer" else f"{name.title()} timer set for {duration_str}."


def set_alarm(name: str, target_timestamp: float) -> str:
    """Set an alarm for a specific time. Returns confirmation string."""
    name = name.lower().strip()
    _schedule(name, target_timestamp, "alarm")

    target_dt = datetime.fromtimestamp(target_timestamp, PT)
    time_str = target_dt.strftime("%-I:%M %p")
    return f"Alarm set for {time_str}." if name == "alarm" else f"{name.title()} alarm set for {time_str}."


def cancel_existing(name: str, save=True):
    """Cancel a timer by name if it exists."""
    with _lock:
        entry = _timers.pop(name, None)
    if entry:
        entry["cancel_event"].set()
        if save:
            _save()


def cancel_timer(name: str) -> str:
    """Cancel a named timer. Returns response string."""
    name = name.lower().strip()
    with _lock:
        entry = _timers.pop(name, None)
    if entry:
        entry["cancel_event"].set()
        _save()
        return f"{name.title()} timer cancelled."
    return f"I don't have a timer called '{name}' running."


def cancel_all() -> str:
    """Cancel all active timers."""
    with _lock:
        names = list(_timers.keys())
        for entry in _timers.values():
            entry["cancel_event"].set()
        _timers.clear()
    _save()
    if names:
        return f"Cancelled {len(names)} timer{'s' if len(names) != 1 else ''}: {', '.join(names)}."
    return "No active timers to cancel."


def list_timers() -> str:
    """List all active timers. Returns response string."""
    with _lock:
        if not _timers:
            return "No active timers."
        lines = []
        now = time.time()
        for name, entry in _timers.items():
            remaining = int(entry["end_time"] - now)
            if remaining <= 0:
                continue
            if entry["type"] == "alarm":
                target_dt = datetime.fromtimestamp(entry["end_time"], PT)
                lines.append(f"{name.title()}: alarm at {target_dt.strftime('%-I:%M %p')}")
            else:
                hours = remaining // 3600
                mins = (remaining % 3600) // 60
                secs = remaining % 60
                parts = []
                if hours > 0:
                    parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
                if mins > 0:
                    parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
                if secs > 0 and hours == 0:
                    parts.append(f"{secs} second{'s' if secs != 1 else ''}")
                lines.append(f"{name.title()}: {' and '.join(parts)} remaining")
    if not lines:
        return "No active timers."
    return "Active timers: " + ", ".join(lines) + "."


def get_current_time() -> str:
    """Return current time in PT as a spoken string."""
    now = datetime.now(PT)
    return now.strftime("%-I:%M %p")
