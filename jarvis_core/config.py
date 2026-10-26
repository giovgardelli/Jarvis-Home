"""
Pi-specific paths and devices, in one place.

Defaults match the current Pi; override any of them in ~/.jarvis_env
(e.g. JARVIS_AUDIO_OUT=hw:1,0 after plugging in a different speaker).
"""
import os

# Where runtime files live (voice profiles, brief audio, timers, state DB,
# logs). Defaults to the home directory of the user running Jarvis.
HOME = os.environ.get("JARVIS_HOME", os.path.expanduser("~"))

# Household timezone for schedules, briefs, alarms and flight times
TIMEZONE = os.environ.get("JARVIS_TIMEZONE", "America/Los_Angeles")

# Speaker for aplay, and a fragment of the microphone's device name
AUDIO_OUT_DEVICE = os.environ.get("JARVIS_AUDIO_OUT", "hw:2,0")
MIC_NAME = os.environ.get("JARVIS_MIC_NAME", "EMEET")

# What Jarvis's own sounds are played through. Defaults to the speaker itself;
# deploy/setup_music.sh sets it to "jarvis", a mixing device on that speaker,
# so speech, the radio and AirPlay can play at the same time (docs/music.md).
PLAYBACK_DEVICE = os.environ.get("JARVIS_PLAYBACK", AUDIO_OUT_DEVICE)

# Created by shairport-sync's hooks while an AirPlay stream is playing
AIRPLAY_FLAG = os.environ.get("JARVIS_AIRPLAY_FLAG", "/dev/shm/jarvis-airplay")

# Voice ID (who is speaking). Off for now: the current far-field mic isn't
# accurate enough. While off, every voice command is treated as
# JARVIS_SPEAKER (default "gio"). To turn it back on, see README.md →
# "Re-enabling voice ID".
VOICE_ID_ENABLED = os.environ.get("VOICE_ID_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def home_path(*parts):
    return os.path.join(HOME, *parts)


def wakeword_model_path():
    """The "hey jarvis" model bundled with openwakeword, found from the
    installed package (no hardcoded virtualenv or Python version)."""
    override = os.environ.get("JARVIS_WAKEWORD_MODEL")
    if override:
        return override
    import openwakeword
    return os.path.join(os.path.dirname(openwakeword.__file__),
                        "resources", "models", "hey_jarvis_v0.1.onnx")
