"""
Generate the short "on it" chimes the voice assistant plays while it works.

Writes to ~/acks/ (where jarvis-pi.py looks). For Telegram voice-note replies
(TTS_ENABLED) also copy them to agents/jarvis/acks/. Usage, from the repo root:
  set -a && source ~/.jarvis_env && set +a && ~/jarvis-env/bin/python agents/jarvis/generate_acks.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from elevenlabs import ElevenLabs  # noqa: E402
from jarvis_core.config import home_path  # noqa: E402

client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "onwK4e9ZLuTAKqWW03F9")

ACKS = {
    "on_it": "On it.",
    "one_moment": "One moment.",
    "let_me_check": "Let me check.",
    "looking_into_that": "Looking into that.",
    "right_away": "Right away.",
}

OUT_DIR = home_path("acks")
os.makedirs(OUT_DIR, exist_ok=True)

for name, text in ACKS.items():
    print(f"Generating: {text}")
    audio = client.text_to_speech.convert(
        voice_id=VOICE_ID,
        text=text,
        model_id="eleven_flash_v2_5",
        output_format="mp3_44100_128"
    )
    audio_bytes = b"".join(audio)
    path = os.path.join(OUT_DIR, f"{name}.mp3")
    with open(path, "wb") as f:
        f.write(audio_bytes)
    print(f"  Saved {path} ({len(audio_bytes)} bytes)")

print(f"\nDone! Chimes are in {OUT_DIR}.")