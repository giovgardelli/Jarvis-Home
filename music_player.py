"""
music_player.py — internet radio and AirPlay on the Jarvis speaker.

Radio plays through mpv, which can change its volume while it plays and read
the song title a station sends. Stations come from stations.json (presets)
or Radio Browser (radio-browser.info: a free, open directory, no account).

AirPlay is shairport-sync, a separate service (deploy/setup_music.sh). This
module only notices it — the service's hooks create AIRPLAY_FLAG while a
phone is playing — so the radio stops when AirPlay starts, and AirPlay is
turned down with the radio while Jarvis listens or talks (duck/unduck).

Everything shares the speaker through an ALSA mixing device ("jarvis"). A raw
hw: device plays one thing at a time, so on one the radio refuses to start
rather than leave Jarvis unable to speak.
"""
import json
import os
import re
import socket
import subprocess
import threading
import time
from contextlib import contextmanager

import requests

from jarvis_core.config import AIRPLAY_FLAG, AUDIO_OUT_DEVICE, PLAYBACK_DEVICE, home_path

STATIONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stations.json")
STATE_FILE = home_path("music.json")          # last station and volume, kept across restarts
MPV_SOCKET = home_path(".jarvis-mpv.sock")

RADIO_BROWSER_HOSTS = ["all.api.radio-browser.info", "de1.api.radio-browser.info",
                       "de2.api.radio-browser.info", "fi1.api.radio-browser.info"]
USER_AGENT = "JarvisHome/1.0"                 # Radio Browser asks clients to name themselves

DEFAULT_VOLUME = 50       # mpv's 0-100 scale is cubic: 50 is about a quarter of full loudness
VOLUME_STEP = 10
MIN_VOLUME, MAX_VOLUME = 10, 100
DUCK_RATIO = 0.5          # radio volume while Jarvis listens or talks (about -18 dB)

AIRPLAY_CONTROL = "AirPlay"   # the softvol control setup_music.sh puts in front of shairport-sync
AIRPLAY_DUCK_PERCENT = 65     # of a -51..0 dB range: about -18 dB, like the radio

CHECK_SECONDS = 1             # how often the watcher looks at AirPlay and the radio
RESTART_AFTER = 10            # a station that dropped is retried at most this often

# Words around the station people actually asked for: "play some jazz music"
FILLER_START = ["some", "a little", "a bit of", "the", "a", "me", "us", "us some", "me some"]
FILLER_END = ["music", "radio", "station", "please", "for me", "on the speaker"]
# What's left of "play the radio" / "play some music": no station named
GENERIC = {"", "music", "radio", "the radio", "something", "anything", "some music", "tunes"}

_lock = threading.Lock()
_mpv = None          # the running mpv process, or None
_station = None      # {"name", "url"} that is playing (or should be)
_last = {"station": None}   # what "play some music" resumes
_volume = DEFAULT_VOLUME
_ducks = 0           # how many things currently want the music lowered
_airplay_was_on = False
_last_start = 0.0
_watcher = None


# ── Stations ──────────────────────────────────────────────────────────────────

def presets():
    with open(STATIONS_PATH) as f:
        return json.load(f)["stations"]


def keyterms():
    """Preset names, for speech-to-text hints."""
    return [s["name"] for s in presets()]


def _strip_words(text, words, at_start):
    changed = True
    while changed:
        changed = False
        for w in sorted(words, key=len, reverse=True):
            if at_start and (text == w or text.startswith(w + " ")):
                text, changed = text[len(w):].strip(), True
            elif not at_start and (text == w or text.endswith(" " + w)):
                text, changed = text[:-len(w)].strip(), True
    return text


def clean_query(query):
    """'some jazz music please' → 'jazz'; 'the radio' → '' (no station named)."""
    q = re.sub(r"[^\w\s'&-]", " ", (query or "").lower().replace("’", "'"))
    q = " ".join(q.split())
    if q in GENERIC:
        return ""
    q = _strip_words(_strip_words(q, FILLER_START, True), FILLER_END, False)
    return "" if q in GENERIC else q


def find_preset(query):
    """The preset whose name or alias is in the query (whole words; the
    longest match wins, so "smooth jazz" beats "jazz")."""
    best, best_len = None, 0
    for station in presets():
        for term in [station["name"]] + station.get("aliases", []):
            term = term.lower()
            if re.search(r"\b" + re.escape(term) + r"\b", query) and len(term) > best_len:
                best, best_len = station, len(term)
    return best


def _radio_browser(**params):
    """Search Radio Browser, trying its mirrors in turn. Returns stations, most
    played first, or [] when nothing matched or no mirror answered."""
    params = {**params, "hidebroken": "true", "order": "clickcount", "reverse": "true", "limit": 5}
    for host in RADIO_BROWSER_HOSTS:
        try:
            resp = requests.get(f"https://{host}/json/stations/search", params=params,
                                headers={"User-Agent": USER_AGENT}, timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"[Music] Radio Browser {host}: {e}", flush=True)
    return []


def search(query):
    """A station for the query: a preset, else Radio Browser by name, then by
    genre tag. None if nothing was found."""
    preset = find_preset(query)
    if preset:
        return {"name": preset["name"], "url": preset["url"]}
    for field in ("name", "tag"):
        for found in _radio_browser(**{field: query}):
            url = found.get("url_resolved") or found.get("url")
            if url:
                return {"name": " ".join(found.get("name", query).split()), "url": url}
    return None


# ── State ─────────────────────────────────────────────────────────────────────

def _load_state():
    global _volume
    try:
        with open(STATE_FILE) as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return
    _volume = saved.get("volume", DEFAULT_VOLUME)
    if saved.get("last_station"):
        _last["station"] = saved["last_station"]


def _save_state():
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"volume": _volume, "last_station": _last["station"]}, f)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        print(f"[Music] Save error: {e}", flush=True)


# ── mpv ───────────────────────────────────────────────────────────────────────

def _shared_output():
    """True when Jarvis plays through a device that can mix several sounds."""
    return not PLAYBACK_DEVICE.startswith(("hw:", "plughw:"))


def _current_volume():
    return round(_volume * DUCK_RATIO) if _ducks else _volume


def _start_mpv(url, volume):
    try:
        os.remove(MPV_SOCKET)
    except OSError:
        pass
    return subprocess.Popen(
        ["mpv", "--no-video", "--no-terminal", "--ytdl=no", "--network-timeout=10",
         f"--audio-device=alsa/{PLAYBACK_DEVICE}", f"--volume={volume}",
         f"--input-ipc-server={MPV_SOCKET}", url],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _ipc(*command, wait=0.0):
    """Send one command to mpv over its socket; returns the reply's data.
    wait: seconds to keep retrying while a just-started mpv opens its socket."""
    deadline = time.time() + wait
    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect(MPV_SOCKET)
                s.sendall(json.dumps({"command": list(command)}).encode() + b"\n")
                buf = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        return None
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    for line in lines:
                        reply = json.loads(line)
                        if "error" in reply:            # skip mpv's event messages
                            return reply.get("data")
        except (OSError, ValueError):
            if time.time() >= deadline:
                return None
            time.sleep(0.1)


def _kill_mpv():
    global _mpv
    if _mpv is not None:
        _mpv.terminate()
        try:
            _mpv.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _mpv.kill()
        _mpv = None


def _set_radio_volume():
    # wait: an mpv started a moment ago may not have opened its socket yet
    if _mpv is not None and _mpv.poll() is None:
        _ipc("set_property", "volume", _current_volume(), wait=2)


# ── AirPlay ───────────────────────────────────────────────────────────────────

def airplay_active():
    return os.path.exists(AIRPLAY_FLAG)


def _speaker_card():
    """The ALSA card of the speaker: "2" from hw:2,0, "Speaker" from hw:CARD=Speaker,DEV=0."""
    override = os.environ.get("JARVIS_SPEAKER_CARD")
    if override:
        return override
    m = re.match(r"(?:plug)?hw:(?:CARD=)?([^,]+)", AUDIO_OUT_DEVICE)
    return m.group(1) if m else "0"


def _set_airplay_volume():
    """Turn AirPlay down while ducked, back to full otherwise. The control only
    exists once shairport-sync has played something, so only while it plays."""
    if not airplay_active():
        return
    percent = AIRPLAY_DUCK_PERCENT if _ducks else 100
    try:
        subprocess.run(["amixer", "-q", "-c", _speaker_card(), "sset", AIRPLAY_CONTROL, f"{percent}%"],
                       timeout=3, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[Music] AirPlay volume: {e}", flush=True)


# ── Ducking ───────────────────────────────────────────────────────────────────

def duck():
    """Lower the music: Jarvis heard its name, or is about to talk."""
    global _ducks
    with _lock:
        _ducks += 1
        if _ducks == 1:
            _set_radio_volume()
            _set_airplay_volume()


def unduck():
    global _ducks
    with _lock:
        _ducks = max(0, _ducks - 1)
        if _ducks == 0:
            _set_radio_volume()
            _set_airplay_volume()


@contextmanager
def ducked():
    duck()
    try:
        yield
    finally:
        unduck()


# ── Commands (each returns what Jarvis says) ─────────────────────────────────

def play(query):
    """Play the station asked for; "" (from "play some music") resumes the last one."""
    global _mpv, _station, _last_start
    if not _shared_output():
        return ("Music isn't set up on this speaker yet. "
                "Run the music setup script on the Pi, then ask me again.")
    if airplay_active():
        return "Something's playing over AirPlay right now. Pause it on your phone, then ask me again."
    query = clean_query(query)
    if query:
        station = search(query)
        if station is None:
            return (f"I couldn't find a radio station for {query}. "
                    "Try a genre like jazz or a station name — or play it from your phone over AirPlay.")
    else:
        station = _last["station"] or {k: presets()[0][k] for k in ("name", "url")}
    with _lock:
        _kill_mpv()
        _mpv = _start_mpv(station["url"], _current_volume())
        _station, _last_start = station, time.time()
        _last["station"] = station
        _save_state()
    print(f"[Music] Playing {station['name']} ({station['url']})", flush=True)
    return f"Playing {station['name']}."


def stop():
    """Stop the radio. Silent when nothing was playing ("stop" may have been
    meant for Jarvis itself)."""
    global _station
    with _lock:
        was_playing = _station is not None
        _kill_mpv()
        _station = None
    if was_playing:
        return "Stopped."
    if airplay_active():
        return "That's AirPlay — pause it on your phone."
    return ""


def change_volume(direction):
    """direction: "up" or "down"."""
    global _volume
    if _station is None:
        if airplay_active():
            return "AirPlay's volume is on your phone."
        return "Nothing's playing."
    with _lock:
        step = VOLUME_STEP if direction == "up" else -VOLUME_STEP
        new = min(MAX_VOLUME, max(MIN_VOLUME, _volume + step))
        if new == _volume:
            return "That's as loud as it goes." if direction == "up" else "That's as quiet as it goes."
        _volume = new
        _save_state()
        _set_radio_volume()
    return "Louder." if direction == "up" else "Quieter."


def now_playing():
    if _station is None:
        return "That's AirPlay — check your phone." if airplay_active() else "Nothing's playing."
    metadata = _ipc("get_property", "metadata") or {}
    title = next((v for k, v in metadata.items() if k.lower() == "icy-title" and v.strip()), None)
    if title:
        return f"This is {title.strip()}, on {_station['name']}."
    return f"This is {_station['name']}. It isn't saying what the song is."


def is_playing():
    return _station is not None


# ── Watcher ───────────────────────────────────────────────────────────────────

def check():
    """One look at AirPlay and the radio (the watcher runs it every second)."""
    global _airplay_was_on, _mpv, _station, _last_start
    on = airplay_active()
    if on and not _airplay_was_on:
        print("[Music] AirPlay started", flush=True)
        with _lock:
            if _station is not None:
                print("[Music] Stopping the radio for AirPlay", flush=True)
                _kill_mpv()
                _station = None
            _set_airplay_volume()   # a ducked level can survive a restart; reset it
    elif not on and _airplay_was_on:
        print("[Music] AirPlay stopped", flush=True)
    _airplay_was_on = on

    # A station that dropped (network blip) is restarted, at most every RESTART_AFTER s
    with _lock:
        if (_station is not None and _mpv is not None and _mpv.poll() is not None
                and time.time() - _last_start >= RESTART_AFTER):
            print(f"[Music] {_station['name']} stopped — restarting", flush=True)
            _mpv = _start_mpv(_station["url"], _current_volume())
            _last_start = time.time()


def _watch():
    while True:
        try:
            check()
        except Exception as e:
            print(f"[Music] Watcher error: {e}", flush=True)
        time.sleep(CHECK_SECONDS)


def init():
    """Called once at startup by the voice loop."""
    global _watcher, _airplay_was_on
    _load_state()
    _airplay_was_on = False
    if _watcher is None:
        _watcher = threading.Thread(target=_watch, daemon=True)
        _watcher.start()
