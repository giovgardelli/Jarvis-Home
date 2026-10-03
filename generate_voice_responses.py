"""
generate_voice_responses.py — Generate pre-recorded voice responses
Run once on the Pi to create the audio clips (needs the voice loop's venv,
which has elevenlabs, and ELEVENLABS_API_KEY from ~/.jarvis_env):
  cd ~/jarvis-home && set -a && source ~/.jarvis_env && set +a && \
    jarvis-env/bin/python generate_voice_responses.py
"""
import os
from elevenlabs import ElevenLabs

VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "onwK4e9ZLuTAKqWW03F9")
from jarvis_core.config import home_path

OUTPUT_DIR = home_path("voice_responses")

RESPONSES = {
    "unauthorized": (
        "I'm afraid I'm only authorized to assist the residents of this household. "
        "I'm not able to help with personal matters for anyone outside the family."
    ),
    "repeat": (
        "I'm sorry, I didn't quite catch who's speaking. Could you try again?"
    ),
    # Played when a request fails — pre-recorded because the failure may be
    # ElevenLabs itself
    "error": (
        "I'm sorry, something went wrong on my end. Please try again in a moment."
    ),
}

def main():
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        print("Set ELEVENLABS_API_KEY first")
        return

    client = ElevenLabs(api_key=api_key)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for name, text in RESPONSES.items():
        print(f"Generating: {name}...")
        audio = client.text_to_speech.convert(
            voice_id=VOICE_ID,
            text=text,
            model_id="eleven_flash_v2_5",
            output_format="mp3_44100_128"
        )
        audio_bytes = b"".join(audio)
        path = os.path.join(OUTPUT_DIR, f"{name}.mp3")
        with open(path, "wb") as f:
            f.write(audio_bytes)
        print(f"  Saved {path} ({len(audio_bytes)} bytes)")

    print("\nDone!")

if __name__ == "__main__":
    main()
