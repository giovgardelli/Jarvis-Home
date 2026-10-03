"""
Voice-ready replies.

When the Pi voice router calls an agent with voice_mode, the agent's own
Claude call also writes the spoken version on a final "VOICE|..." line, so
the router can skip a separate "shorten for voice" call. The full reply
stays available for "text me that".
"""

VOICE_INSTRUCTION = (
    "\n\n(Voice request: after your normal reply, add ONE final line that starts with "
    "VOICE| followed by a spoken version for a smart speaker: natural sentences on a "
    "single line, no markdown, bullets or emoji, at most 3 items (most urgent or soonest "
    "first), under 60 words, never flight numbers or airport codes. Only if you left "
    "items out, end it with \"There are a few more — want to hear them?\")"
)


def split_voice(reply):
    """Return (reply without the VOICE| line, spoken text or None)."""
    voice = None
    kept = []
    for line in reply.split("\n"):
        if line.strip().startswith("VOICE|"):
            voice = line.strip()[len("VOICE|"):].strip() or None
        else:
            kept.append(line)
    return "\n".join(kept).strip(), voice
