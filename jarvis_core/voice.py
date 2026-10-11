"""
Voice-ready replies.

When the Pi voice router calls an agent with voice_mode, the agent's own
Claude call also writes the spoken version on a final "VOICE|..." line, so
the router can skip a separate "shorten for voice" call. The full reply
stays available for "text me that".
"""

import re

# After a spoken reply that ends in a question, the Pi listens for 4 s without
# the wake word — so only end with a question when the answer is needed.
NO_COURTESY_QUESTION = (
    "Don't end with a question unless you need the answer to finish the request "
    "(no \"anything else?\")."
)

VOICE_INSTRUCTION = (
    "\n\n(Voice request: after your normal reply, add ONE final line that starts with "
    "VOICE| followed by a spoken version for a smart speaker: natural sentences on a "
    "single line, no markdown, bullets or emoji, at most 3 items (most urgent or soonest "
    "first), under 60 words, never flight numbers or airport codes. Only if you left "
    "items out, end it with \"There are a few more — want to hear them?\" "
    + NO_COURTESY_QUESTION + ")"
)

# Closing questions that don't need an answer: no listening window after these
COURTESY_QUESTIONS = (
    "anything else", "something else", "what else", "how else", "how can i help",
    "how may i help", "can i help", "may i help", "help you with", "assist you",
    "what can i do for you", "need anything", "let me know",
)


def expects_reply(text):
    """True when a spoken reply ends with a question Jarvis needs answered
    ("Is that for Gio or Kenz?", "Want to hear them?", "Want me to text it
    to you?") — the only time the Pi keeps listening without the wake word.
    Courtesy closers ("Anything else I can help with?") don't count."""
    t = (text or "").strip()
    if not t.endswith("?"):
        return False
    last = re.split(r"(?<=[.!?])\s+", t)[-1].lower().replace("’", "'")
    return not any(p in last for p in COURTESY_QUESTIONS)


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
