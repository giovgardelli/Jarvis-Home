"""
model_watch.py — periodic check for AI model deprecations and upgrades.

Every CHECK_EVERY_DAYS (run daily at 6:40am, just before the 7am brief):
  1. Scan the repo for the model IDs actually in use (so this never drifts
     from the code).
  2. Ask each provider's API which models it currently offers.
  3. Have Gemini (with Google Search grounding) look for deprecation or
     shutdown notices for those exact models, and clearly better
     replacements for this use case.
  4. Store a short advisory; the morning brief appends it to Gio's
     Telegram message (never to the spoken brief).

"Nothing to report" produces no advisory, so the brief stays quiet.
"""
import os
from jarvis_core.config import TIMEZONE
import re
import glob
import requests
from datetime import datetime, date
import pytz

from jarvis_core import state

PT = pytz.timezone(TIMEZONE)
CHECK_EVERY_DAYS = 14
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# provider -> regex for model IDs as they appear in code
MODEL_PATTERNS = {
    "Anthropic (Claude)": re.compile(r"\bclaude-(?:opus|sonnet|haiku|fable)-[0-9][\w.-]*"),
    "Google (Gemini)": re.compile(r"\bgemini-[0-9][\w.-]*"),
    "Deepgram (speech-to-text)": re.compile(r"""["'](nova-[0-9][\w-]*|flux[\w-]*)["']"""),
    "ElevenLabs (text-to-speech)": re.compile(r"\beleven_[a-z0-9_]+"),
}

USE_CASE = (
    "a home voice assistant on a Raspberry Pi. Claude routes requests and writes "
    "short replies (latency matters most, then accuracy, then cost); Gemini answers "
    "web-search questions with Google Search grounding; Deepgram transcribes short "
    "voice commands; ElevenLabs speaks replies (lowest latency matters)."
)


def find_models_in_use():
    """{provider: sorted model IDs} from the repo's Python files."""
    files = glob.glob(os.path.join(REPO_ROOT, "*.py")) + \
        glob.glob(os.path.join(REPO_ROOT, "jarvis_core", "*.py")) + \
        glob.glob(os.path.join(REPO_ROOT, "agents", "*", "*.py"))
    found = {name: set() for name in MODEL_PATTERNS}
    for path in files:
        if os.path.abspath(path) == os.path.abspath(__file__):
            continue  # this file's patterns/prompts aren't real usage
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for name, pattern in MODEL_PATTERNS.items():
            for m in pattern.finditer(text):
                found[name].add((m.group(1) if m.groups() else m.group(0)).rstrip(".-"))
    return {name: sorted(ids) for name, ids in found.items() if ids}


def _anthropic_models():
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return [m.id for m in client.models.list(limit=100)]


def _gemini_models():
    from google import genai
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return [m.name.replace("models/", "") for m in client.models.list()
            if "gemini" in (m.name or "")]


def _elevenlabs_models():
    resp = requests.get("https://api.elevenlabs.io/v1/models",
                        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}, timeout=15)
    resp.raise_for_status()
    return [m["model_id"] for m in resp.json()]


def _deepgram_models():
    resp = requests.get("https://api.deepgram.com/v1/models",
                        headers={"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}"}, timeout=15)
    resp.raise_for_status()
    stt = resp.json().get("stt", [])
    return sorted({m.get("canonical_name") or m.get("name") for m in stt if m})


PROVIDER_LISTERS = {
    "Anthropic (Claude)": _anthropic_models,
    "Google (Gemini)": _gemini_models,
    "ElevenLabs (text-to-speech)": _elevenlabs_models,
    "Deepgram (speech-to-text)": _deepgram_models,
}


def list_available_models():
    """{provider: model IDs}, skipping any provider whose key/API is unavailable."""
    available = {}
    for name, lister in PROVIDER_LISTERS.items():
        try:
            available[name] = lister()
        except Exception as e:
            print(f"[ModelWatch] Could not list {name} models: {e}", flush=True)
    return available


def _build_prompt(in_use, available, previous):
    today = datetime.now(PT).strftime("%B %d, %Y")
    lines = [f"Today is {today}. You are reviewing the AI models used by {USE_CASE}", "",
             "Models currently in use (from the code):"]
    for provider, ids in in_use.items():
        lines.append(f"- {provider}: {', '.join(ids)}")
        offered = available.get(provider)
        if offered is not None:
            missing = [m for m in ids if not any(m == o or o.startswith(m) or m.startswith(o) for o in offered)]
            if missing:
                lines.append(f"  NOTE: not in the provider's current model list: {', '.join(missing)}")
    lines.append("")
    lines.append("Models each provider currently offers (from their APIs):")
    for provider, ids in available.items():
        lines.append(f"- {provider}: {', '.join(ids[:60])}")
    lines += [
        "",
        "Using Google Search, check for:",
        "1. Deprecation, retirement or shutdown announcements for the exact models in use, with dates.",
        "2. Newer models from the same provider that are clearly faster, cheaper or more accurate "
        "for this use case and are a near drop-in replacement.",
        "",
        "Rules:",
        "- Only report what you can confirm from official provider sources found via search. Never guess.",
        "- Ignore previews/experimental models unless the model in use is being shut down.",
        "- If nothing is actionable, reply with exactly: NONE",
        "- Otherwise reply with at most 3 lines, each starting with '• ', in plain text: "
        "what is affected, the recommendation (with the exact new model ID), the date if any, "
        "and the source domain in parentheses.",
    ]
    if previous:
        lines += ["", "Already reported last time (do not repeat unless a shutdown date is now "
                  "within 60 days or something changed):", previous]
    return "\n".join(lines)


def run_model_watch():
    """Run the check now. Returns the advisory text, or None if nothing to report."""
    from google import genai
    from google.genai import types

    in_use = find_models_in_use()
    available = list_available_models()
    record = state.get_model_watch()
    prompt = _build_prompt(in_use, available, record.get("last_reported"))

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        contents=prompt,
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
    )
    text = (response.text or "").strip()
    advisory = None if not text or text.upper().startswith("NONE") else text

    today = datetime.now(PT).date().isoformat()
    record["last_run"] = today
    record["advisory"] = {"date": today, "text": advisory}
    if advisory:
        record["last_reported"] = advisory
    state.set_model_watch(record)
    print(f"[ModelWatch] Done. In use: {in_use}. Advisory: {advisory or 'none'}", flush=True)
    return advisory


def maybe_run_model_watch():
    """Daily 6:40am job: run the check if the last one was CHECK_EVERY_DAYS ago
    or more (catches up after the Pi was off on the scheduled day)."""
    last = state.get_model_watch().get("last_run")
    today = datetime.now(PT).date()
    if last and (today - date.fromisoformat(last)).days < CHECK_EVERY_DAYS:
        return
    try:
        run_model_watch()
    except Exception as e:
        # last_run not updated, so it retries tomorrow
        print(f"[ModelWatch] Check failed, will retry tomorrow: {e}", flush=True)


def todays_advisory():
    """Advisory text produced today (for the morning brief), or None."""
    adv = state.get_model_watch().get("advisory") or {}
    if adv.get("date") == datetime.now(PT).date().isoformat():
        return adv.get("text")
    return None
