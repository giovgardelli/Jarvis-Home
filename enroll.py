"""
enroll.py — Voice enrollment script
Run this on the Pi to enroll a new speaker.
Usage: python3 enroll.py --speaker gio
       python3 enroll.py --speaker kenz
       python3 enroll.py --test          # say anything; prints match scores
"""
import os
import sys
import wave
import io
import time
import pyaudio
import numpy as np
import argparse

from jarvis_core.config import MIC_NAME

SAMPLE_RATE = 16000
CHANNELS    = 2
CHUNK       = 1024
FORMAT      = pyaudio.paInt16
RECORD_SECONDS = 25

SCRIPT = """Please read the following text naturally at a normal pace:

"Good morning. I'd like to add some items to the shopping list today.
Can you also check what tasks I have due this week? I'm thinking about
the home maintenance schedule and whether the HVAC service is coming up.
Also remind me about any upcoming flights — I think there's one to
San Francisco next month. It's good to stay on top of these things."

Speak clearly, at the same distance from the microphone you normally use."""


def find_device(name_fragment: str) -> int:
    p = pyaudio.PyAudio()
    for i in range(p.get_device_count()):
        d = p.get_device_info_by_index(i)
        if name_fragment.lower() in d["name"].lower() and d["maxInputChannels"] > 0:
            p.terminate()
            return i
    p.terminate()
    raise RuntimeError(f"Device '{name_fragment}' not found")


def record(device: int, seconds: int) -> bytes:
    p = pyaudio.PyAudio()
    stream = p.open(format=FORMAT, channels=CHANNELS, rate=SAMPLE_RATE,
                    input=True, input_device_index=device,
                    frames_per_buffer=CHUNK)
    frames = []
    print(f"\nRecording for {seconds} seconds...")
    for i in range(int(SAMPLE_RATE / CHUNK * seconds)):
        frames.append(stream.read(CHUNK))
        if i % int(SAMPLE_RATE / CHUNK) == 0:
            elapsed = i // int(SAMPLE_RATE / CHUNK)
            remaining = seconds - elapsed
            print(f"  {remaining}s remaining...", end="\r")
    stream.stop_stream()
    stream.close()
    p.terminate()
    print("\nDone recording.")

    buf = io.BytesIO()
    wf = wave.open(buf, "wb")
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(p.get_sample_size(FORMAT))
    wf.setframerate(SAMPLE_RATE)
    wf.writeframes(b"".join(frames))
    wf.close()
    buf.seek(0)
    return buf.read()


TEST_SECONDS = 5


def run_test():
    """Record a short phrase and show how it scores against each profile —
    use it to pick VOICE_ID_CONFIRMED / VOICE_ID_UNCERTAIN."""
    from voice_id import identify, SIMILARITY_CONFIRMED, SIMILARITY_UNCERTAIN
    device = find_device(MIC_NAME)
    input(f"\nPress Enter, then say a normal command (e.g. 'what's on the shopping list')...")
    result = identify(record(device, TEST_SECONDS), SAMPLE_RATE)
    print(f"\nResult: {result}")
    print(f"Current thresholds: confirmed >= {SIMILARITY_CONFIRMED}, uncertain >= {SIMILARITY_UNCERTAIN}")
    print("Per-speaker scores are printed above as [VoiceID] lines.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--speaker", help="Speaker name (gio or kenz)")
    parser.add_argument("--test", action="store_true", help="Score a short phrase against saved profiles")
    args = parser.parse_args()
    if args.test:
        run_test()
        return
    if not args.speaker:
        parser.error("--speaker is required (or use --test)")

    speaker = args.speaker.lower().strip()
    print(f"\n=== Voice Enrollment for {speaker.title()} ===\n")
    print(SCRIPT)
    input("\nPress Enter when ready to start recording...")

    device = find_device(MIC_NAME)

    # Countdown
    for i in range(3, 0, -1):
        print(f"Starting in {i}...")
        time.sleep(1)

    audio_bytes = record(device, RECORD_SECONDS)

    print("\nProcessing voice profile...")
    # Same decoding as live recognition (see voice_id.wav_to_float)
    from voice_id import save_profile, wav_to_float
    save_profile(speaker, wav_to_float(audio_bytes), SAMPLE_RATE)
    print(f"\n✅ Profile saved for {speaker.title()}!")
    from voice_id import PROFILES_DIR
    print(f"   File: {os.path.join(PROFILES_DIR, speaker + '.npy')}")


if __name__ == "__main__":
    main()
