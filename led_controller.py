"""
led_controller.py — BlinkStick LED state controller for Jarvis Pi
"""
import threading
import time
import math
from datetime import datetime
import pytz
from jarvis_core.config import TIMEZONE

# Lights stay dark from 10pm to 7am. Jarvis keeps listening; only the LEDs rest.
PT = pytz.timezone(TIMEZONE)
QUIET_START_HOUR = 22
QUIET_END_HOUR = 7


def is_quiet_hours():
    hour = datetime.now(PT).hour
    return hour >= QUIET_START_HOUR or hour < QUIET_END_HOUR


# Older name still used below (was referenced but never defined, which made
# uncertain()/unauthorized()/error() crash the voice loop)
_is_sleep_hours = is_quiet_hours

try:
    from blinkstick import blinkstick
    _stick = blinkstick.find_first()
    _available = _stick is not None
    if _available:
        print("[LED] BlinkStick found and ready", flush=True)
    else:
        print("[LED] BlinkStick not found — LED disabled", flush=True)
except ImportError:
    _stick = None
    _available = False
    print("[LED] blinkstick library not installed — LED disabled", flush=True)
except Exception as e:
    _stick = None
    _available = False
    print(f"[LED] BlinkStick init error: {e}", flush=True)

NUM_LEDS = 8

# Animation thread management
_anim_thread = None
_stop_event = threading.Event()
_lock = threading.Lock()


def _set_all(r, g, b):
    """Set all LEDs to the same color."""
    if not _available:
        return
    try:
        for i in range(NUM_LEDS):
            _stick.set_color(index=i, red=int(r), green=int(g), blue=int(b))
    except Exception as e:
        print(f"[LED] Set error: {e}", flush=True)


def _set_one(i, r, g, b):
    """Set a single LED."""
    if not _available:
        return
    try:
        _stick.set_color(index=i, red=int(r), green=int(g), blue=int(b))
    except Exception:
        pass


def _stop_animation():
    """Stop the current animation thread and wait for it to finish."""
    global _anim_thread
    _stop_event.set()
    if _anim_thread and _anim_thread.is_alive():
        _anim_thread.join(timeout=1.0)
    _stop_event.clear()


def _start_animation(fn, *args):
    """Start a new animation in a background thread (LEDs stay off in quiet hours)."""
    global _anim_thread
    with _lock:
        _stop_animation()
        if is_quiet_hours():
            _set_all(0, 0, 0)
            return
        _anim_thread = threading.Thread(target=fn, args=args, daemon=True)
        _anim_thread.start()


# ── Animation functions ───────────────────────────────────────────────────────

def _anim_breathe(r, g, b, min_bright=0.03, max_bright=0.12, period=4.0):
    """Gentle sine-wave breathe. Runs forever until stopped."""
    while not _stop_event.is_set():
        t = time.time()
        # Sine wave between min and max brightness
        factor = min_bright + (max_bright - min_bright) * (0.5 + 0.5 * math.sin(2 * math.pi * t / period))
        _set_all(r * factor, g * factor, b * factor)
        _stop_event.wait(timeout=0.05)


def _anim_wave(r, g, b, speed=0.06):
    """Wave with bright head and fading tail, travelling left to right."""
    pos = 0
    # First factor = head (brightest), rest = tail (progressively dimmer)
    tail_factors = [1.0, 0.2, 0.03, 0.02, 0.01, 0.01, 0.01, 0.01]

    while not _stop_event.is_set():
        for i in range(NUM_LEDS):
            _set_one(i, 0, 0, 0)
        for t, factor in enumerate(tail_factors):
            led_pos = (pos - t) % NUM_LEDS
            _set_one(led_pos, r * factor, g * factor, b * factor)
        pos = (pos + 1) % NUM_LEDS
        _stop_event.wait(timeout=speed)


def _anim_pulse_then_solid(r, g, b, pulses=2, pulse_speed=0.08):
    """Pulse N times then hold solid."""
    for _ in range(pulses):
        if _stop_event.is_set():
            return
        # Fade in
        for step in range(20):
            if _stop_event.is_set():
                return
            factor = step / 20
            _set_all(r * factor, g * factor, b * factor)
            _stop_event.wait(timeout=pulse_speed)
        # Fade out
        for step in range(20, 0, -1):
            if _stop_event.is_set():
                return
            factor = step / 20
            _set_all(r * factor, g * factor, b * factor)
            _stop_event.wait(timeout=pulse_speed)
    # Hold solid at 70% brightness
    _set_all(r * 0.7, g * 0.7, b * 0.7)
    while not _stop_event.is_set():
        _stop_event.wait(timeout=0.1)


def _anim_blink(r, g, b, on_s=1.0, off_s=0.5, brightness=0.4):
    """Slow blink — loops until stopped."""
    while not _stop_event.is_set():
        _set_all(r * brightness, g * brightness, b * brightness)
        _stop_event.wait(timeout=on_s)
        if _stop_event.is_set():
            return
        _set_all(0, 0, 0)
        _stop_event.wait(timeout=off_s)


def _anim_fast_blink_then_off(r, g, b, count=5, on_s=0.15, off_s=0.1):
    """Fast blink N times then turn off."""
    for _ in range(count):
        if _stop_event.is_set():
            return
        _set_all(r, g, b)
        _stop_event.wait(timeout=on_s)
        _set_all(0, 0, 0)
        _stop_event.wait(timeout=off_s)
    # Stay off


# ── Public state functions ────────────────────────────────────────────────────

def listening():
    """Gentle blue breathe — Jarvis is ready."""
    _start_animation(_anim_breathe, 0, 0, 255)


def wake_word():
    """Yellow wave — wake word detected."""
    _start_animation(_anim_wave, 255, 200, 0)


def identified(speaker: str):
    """Green pulse x2 then solid for Gio, purple for Kenz."""
    if speaker == "gio":
        _start_animation(_anim_pulse_then_solid, 0, 255, 0)
    elif speaker == "kenz":
        _start_animation(_anim_pulse_then_solid, 180, 0, 255)
    else:
        _start_animation(_anim_pulse_then_solid, 0, 255, 0)

def public_query():
    """White — public query, no voice ID required."""
    if is_quiet_hours():
        return
    _set_all(150, 150, 150)
    
def uncertain():
    """Orange slow blink — voice ID uncertain."""
    if _is_sleep_hours():
        return
    _start_animation(_anim_blink, 255, 80, 0)


def unauthorized():
    """Red fast blink x3 then off."""
    if _is_sleep_hours():
        return
    _start_animation(_anim_fast_blink_then_off, 255, 0, 0)


def error():
    """Red — system error. Off during sleep hours."""
    if _is_sleep_hours():
        off()
        return
    _set_all(200, 0, 0)


def off():
    """Turn off all LEDs."""
    _stop_animation()
    _set_all(0, 0, 0)
