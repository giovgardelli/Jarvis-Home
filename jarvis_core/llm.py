"""
llm.py — the Claude model Jarvis uses, and how its replies are read.

One setting for every Claude call (router, agents, brief):
  CLAUDE_MODEL in ~/.jarvis_env (default claude-haiku-5-5).
  To go back to the previous model, set it to the Haiku 4.5 ID given in
  docs/hardware-and-costs.md ("Models"). (The ID isn't written here because
  the model check scans the code for the models in use.)

Haiku 5.5 notes (they also work unchanged on Haiku 4.5):
  - It "thinks" by default, which adds delay. Spoken and interactive calls
    turn thinking off (fast()), matching how Haiku 4.5 always ran. A
    background job nobody waits on can leave it on for better answers.
  - A reply can start with a (hidden) thinking block, so the answer is read
    from the text blocks (text()), never from content[0].
  - It can occasionally decline a request (stop_reason "refusal"); text()
    turns that into a polite spoken line instead of an empty reply.
  - The same text counts as ~30% more tokens than on Haiku 4.5, so the
    max_tokens limits were raised to match.
"""
import os

MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5").strip()
REFUSAL_TEXT = "Sorry, I can't help with that one."


def fast(extra_body=None):
    """extra_body for a call someone is waiting on: thinking off.
    Sent via extra_body so older anthropic SDKs on the Pi accept it."""
    body = {"thinking": {"type": "disabled"}}
    body.update(extra_body or {})
    return body


def text(resp):
    """The reply's text (all text blocks, skipping thinking blocks)."""
    if getattr(resp, "stop_reason", None) == "refusal":
        details = getattr(resp, "stop_details", None)
        print(f"[Claude] Request declined ({getattr(details, 'category', None)})", flush=True)
        return REFUSAL_TEXT
    return "".join(getattr(b, "text", "") for b in resp.content
                   if getattr(b, "type", "text") == "text").strip()
