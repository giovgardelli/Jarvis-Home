"""
local_stt.py — on-device speech-to-text with Moonshine (Track B).

Goal: keep your voice recordings in the house without making Jarvis slower or
less accurate. Controlled by LOCAL_STT in ~/.jarvis_env:

  off     (default) Deepgram only
  shadow  Deepgram is used as normal; after each answer, the same recording is
          also transcribed locally and both are logged to stt_shadow.csv for
          `python3 -m jarvis_core.shadow_report`. Runs after the reply, on its
          own thread, so it never slows Jarvis down.
  on      Moonshine transcribes; Deepgram is the automatic fallback if it
          fails or returns nothing.

LOCAL_STT_MODEL picks the model (default "moonshine/base"; "moonshine/tiny"
is faster and less accurate).

Install on the Pi (voice venv), then restart the voice loop:
  ~/jarvis-env/bin/pip install useful-moonshine-onnx
(If that package isn't found, see docs/going-local.md for alternatives.)
"""
import csv
import io
import os
import tempfile
import threading
import wave
from datetime import datetime

import pytz

from jarvis_core.config import TIMEZONE, home_path

MODE = os.environ.get("LOCAL_STT", "off").strip().lower()
MODEL = os.environ.get("LOCAL_STT_MODEL", "moonshine/base")
SHADOW_PATH = home_path("stt_shadow.csv")

_backend = None
_unavailable = None
_lock = threading.Lock()


def _load():
    """The Moonshine transcribe function, or None (reason in unavailable_reason())."""
    global _backend, _unavailable
    with _lock:
        if _backend is None and _unavailable is None:
            try:
                import moonshine_onnx
                _backend = lambda path: moonshine_onnx.transcribe(path, MODEL)
                print(f"[LocalSTT] Moonshine ready ({MODEL})", flush=True)
            except Exception as e:
                _unavailable = f"{type(e).__name__}: {e}"
                print(f"[LocalSTT] Not available — staying on Deepgram ({_unavailable})", flush=True)
        return _backend


def available():
    return _load() is not None


def unavailable_reason():
    _load()
    return _unavailable


def transcribe(wav_bytes):
    """Transcribe a WAV (bytes) on the Pi. Raises if Moonshine isn't available."""
    backend = _load()
    if backend is None:
        raise RuntimeError(f"local speech-to-text unavailable: {_unavailable}")
    fd, path = tempfile.mkstemp(suffix=".wav")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(wav_bytes)
        result = backend(path)
    finally:
        os.unlink(path)
    if isinstance(result, (list, tuple)):
        result = " ".join(str(r) for r in result)
    return (result or "").strip()


def warmup():
    """Load the model and run it once on silence, so the first real command
    (and the speed comparison) doesn't include the one-time load."""
    if not available():
        return
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 16000)
    try:
        transcribe(buf.getvalue())
    except Exception as e:
        print(f"[LocalSTT] Warm-up failed: {e}", flush=True)


def log_shadow(deepgram_text, local_text, deepgram_seconds, local_seconds,
               deepgram_decision, local_decision, error=""):
    try:
        new = not os.path.exists(SHADOW_PATH)
        with open(SHADOW_PATH, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["timestamp", "deepgram_text", "local_text", "deepgram_seconds",
                            "local_seconds", "deepgram_decision", "local_decision", "error"])
            w.writerow([datetime.now(pytz.timezone(TIMEZONE)).isoformat(), deepgram_text, local_text,
                        f"{deepgram_seconds:.3f}", f"{local_seconds:.3f}",
                        deepgram_decision, local_decision, error])
    except Exception as e:
        print(f"[LocalSTT] Shadow log error: {e}", flush=True)
