"""
jarvis_core/router.py — the one routing brain for Jarvis.

Used by both the voice loop (jarvis-pi.py, channel="voice") and the central
Telegram service (agents/jarvis, channel="telegram"). Order of checks:

  1. "text me that" / "hear more"  — replay the last full answer
  2. timers and alarms             — voice only (Telegram can't ring)
  3. multi-agent questions         — ask Will Due + Andy Mann in parallel, merge
  4. explicit web searches         — Gemini with Google Search
  5. unambiguous shortcuts         — shopping list, flights
  6. Haiku (structured output)     — picks an agent, Gemini, or answers directly;
                                     a completed task found in today's brief is
                                     hinted to the right list, with a retry on
                                     that list if the first agent can't find it
"""
import os
from jarvis_core.config import TIMEZONE
import re
import json
import time
import requests
import anthropic
from datetime import datetime, timedelta
import pytz
from concurrent.futures import ThreadPoolExecutor

from jarvis_core import household, llm, state
from jarvis_core.registry import access, agent as registry_agent, agent_ids, needs_speaker, routing_lines
from jarvis_core.voice import NO_COURTESY_QUESTION, split_voice as _split_voice

JARVIS_SECRET  = os.environ["JARVIS_SECRET"]
ANTHROPIC_KEY  = os.environ["ANTHROPIC_API_KEY"]
AGENT_TIMEOUT  = 70
ROUTER_MODEL   = llm.MODEL   # CLAUDE_MODEL in ~/.jarvis_env (jarvis_core/llm.py)
PT             = pytz.timezone(TIMEZONE)

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# Imported on first use: only the voice loop handles timers
timer_manager = None


def _timers():
    global timer_manager
    if timer_manager is None:
        import timer_manager as tm
        timer_manager = tm
    return timer_manager


def _address(speaker):
    return household.display_name(speaker)


# ── Sensitivity tiers ─────────────────────────────────────────────────────────
# The speaker can be reached by anyone in the room, so voice requests are
# gated by how sensitive the destination is (registry.json "access"):
#   public     timers, web questions, small talk        → anyone
#   household  shopping list, home maintenance          → a recognized voice,
#                                                         or "probably household"
#   personal   to-do list, calendar/flights, texting,   → a confirmed voice only
#              replaying the last answer
# voice_status comes from voice ID: confirmed / uncertain / unauthorized /
# error, or disabled / no_profiles (voice ID off or not set up: no gating),
# or None (identity known another way, e.g. Telegram account).
# Voice ID is a deterrent for casual access, not a security boundary.

TIER_RANK = {"public": 0, "household": 1, "personal": 2}
UNGATED_STATUSES = (None, "confirmed", "disabled", "no_profiles")
UNAUTHORIZED_TEXT = ("I'm afraid I'm only authorized to assist the residents of this household. "
                     "I'm not able to help with personal matters for anyone outside the family.")
REPEAT_TEXT = "I'm sorry, I didn't quite catch who's speaking. Could you try again?"


def _gate(tier, voice_status):
    """None if the request may go ahead, else the blocked reply. The reply's
    "gate" key ("unauthorized"/"repeat") lets the voice loop play its
    pre-recorded clip."""
    if voice_status in UNGATED_STATUSES or tier == "public":
        return None
    if tier == "household" and voice_status == "uncertain":
        return None
    print(f"[Router] Blocked {tier} request (voice: {voice_status})", flush=True)
    if voice_status in ("uncertain", "error"):
        return {"response_text": REPEAT_TEXT, "full_response": None, "force_text": False, "gate": "repeat"}
    return {"response_text": UNAUTHORIZED_TEXT, "full_response": None, "force_text": False, "gate": "unauthorized"}


def _strictest(tiers):
    return max(tiers, key=lambda t: TIER_RANK.get(t, 2))


# ── Who is asking? ────────────────────────────────────────────────────────────
# With voice ID off the voice loop passes speaker=None. Most agents don't care
# (they get DEFAULT_SPEAKER). Agents marked needs_speaker in registry.json
# (the to-do list) take a name said in the request, treat "our/us/both" as
# shared, and otherwise Jarvis asks "Is that for Gio or Kenz?" first.

DEFAULT_SPEAKER = household.DEFAULT_SPEAKER
SPEAKER_NAMES = household.spoken_names()          # from household.json
SHARED_WORDS = ["shared", "both", "both of us", "our", "ours", "us", "we", "everyone"]
OWNER_QUESTION = household.names_question()       # "Is that for Gio or Kenz?"
OWNER_ANSWER_MAX_WORDS = 5     # longer replies are new requests, not answers
OWNER_ANSWER_TIMEOUT = 120     # seconds a pending question stays open

_pending_owner = {}            # channel -> {"text": original request, "expires": time}


def _speaker_from_text(text):
    """'gio' or 'kenz' if the request names one of them (first mention wins)."""
    t = _normalize(text)
    found = []
    for speaker, names in SPEAKER_NAMES.items():
        for name in names:
            m = re.search(r"\b" + re.escape(name) + r"\b", t)
            if m:
                found.append((m.start(), speaker))
    return min(found)[1] if found else None


def _mentions_shared(text):
    return _contains(text, SHARED_WORDS)


def _ask_owner(text, channel):
    _pending_owner[channel] = {"text": text, "expires": time.time() + OWNER_ANSWER_TIMEOUT}
    print(f"[Router] Owner unknown for to-do request — asking", flush=True)
    return {"response_text": OWNER_QUESTION, "full_response": None, "force_text": False}


def _owner_answer(text, channel):
    """If this is the reply to "Gio or Kenz?", return (original request, speaker)."""
    pending = _pending_owner.pop(channel, None)
    if not pending or pending["expires"] < time.time():
        return None
    if len(_normalize(text).split()) > OWNER_ANSWER_MAX_WORDS:
        return None
    name = _speaker_from_text(text)
    if name:
        return pending["text"], name
    if _mentions_shared(text):
        return pending["text"] + f" (shared, for {household.everyone()})", DEFAULT_SPEAKER
    return None


def _structured(schema):
    """Structured outputs: Claude's reply is guaranteed to be JSON matching
    `schema`. Sent via extra_body so it works on older anthropic SDKs that
    don't know the output_config parameter yet."""
    return {"output_config": {"format": {"type": "json_schema", "schema": schema}}}


def _nullable(json_type):
    return {"anyOf": [{"type": json_type}, {"type": "null"}]}


def _routing_schema():
    return {
        "type": "object",
        "properties": {
            # Only real destinations can come back — no "agent not found"
            "agent": {"type": "string", "enum": agent_ids() + ["gemini_search", "jarvis"]},
            "intent": {"type": "string"},
            "direct_response": _nullable("string"),
        },
        "required": ["agent", "intent", "direct_response"],
        "additionalProperties": False,
    }


TIMER_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["set_timer", "set_alarm", "cancel", "cancel_all", "list", "time_query"]},
        "name": _nullable("string"),
        "seconds": _nullable("integer"),
        "alarm_time": _nullable("string"),   # 24h "HH:MM"; the date is worked out in code
    },
    "required": ["action", "name", "seconds", "alarm_time"],
    "additionalProperties": False,
}

# ── Trigger lists ─────────────────────────────────────────────────────────────

MULTI_AGENT_TRIGGERS = [
    "priorities", "priority", "to do", "todo", "to-do",
    "what's on my list", "what do i have", "what should i",
    "this week", "today", "what's due", "whats due",
    "catch me up", "overview", "summary", "rundown"
]

# Phrases that mean "hear more" anywhere in the utterance
HEAR_MORE_TRIGGERS = [
    "hear more", "keep going", "tell me more", "show more", "and the rest"
]
# Bare words that only mean "hear more" when they are the whole utterance —
# "more" inside "add more milk" must not trigger it.
HEAR_MORE_EXACT = [
    "more", "more please", "continue", "go on", "what else", "the rest"
]

TEXT_ME_TRIGGERS = [
    "text me", "send it", "text it", "message me", "send that",
    "send as text", "write it out", "send me the list", "send it to me"
]

TIME_QUERY_TRIGGERS = [
    "what time is it", "what's the time", "whats the time",
    "current time", "tell me the time"
]
 
TIMER_SET_TRIGGERS = [
    "set a timer", "timer for", "start a timer", "start timer",
    "set timer", "countdown for", "remind me in"
]
 
ALARM_SET_TRIGGERS = [
    "set an alarm", "alarm for", "alarm at", "wake me at",
    "wake me up at", "remind me at", "set alarm"
]
 
# A cancel request needs a cancel verb AND a timer/alarm noun, so
# "cancel the dentist appointment" is not treated as a timer command.
TIMER_CANCEL_VERBS = ["cancel", "stop", "delete", "clear", "turn off"]
TIMER_NOUNS = ["timer", "timers", "alarm", "alarms"]
 
TIMER_LIST_TRIGGERS = [
    "what timers", "active timers", "list timers",
    "do i have any timers", "timers running"
]

# "the list" on its own means the shopping list (house convention);
# to-do and maintenance lists are always named explicitly.
SHOPPING_WRITE_TRIGGERS = [
    "to the list", "on the list", "off the list", "from the list",
    "to the shopping", "to my shopping", "from the shopping",
    "to the grocery", "to my grocery", "from the grocery",
    "need to buy", "pick up some", "get some"
]
SHOPPING_READ_TRIGGERS = [
    "shopping list", "grocery list", "what's on the list",
    "whats on the list", "what do we need", "what do i need to buy"
]
TASK_WRITE_TRIGGERS = [
    "add to my to do", "add to my todo", "add to my task",
    "add to the to do", "add to the task list",
    "new task", "create a task", "remind me to",
    "mark as done", "mark complete", "complete the task",
    "i finished", "i completed", "i did ", "i bought",
    "i got ", "i fixed", "i installed", "remove from my to do",
    "delete the task", "remove the task"
]
TASK_READ_TRIGGERS = [
    "my tasks", "my to do", "my todo", "what's on my to do",
    "whats on my to do", "what do i have to do", "my priorities",
    "what should i work on", "pending tasks", "overdue tasks"
]
FLIGHT_TRIGGERS = [
    "my flight", "my next flight", "any flights", "do i have a flight",
    "when is my flight", "flight to", "flight on", "flights this week",
    "flights today", "departure", "land", "lands", "landing", "landed",
    "southwest", "confirmation number"
]
MAINTENANCE_TRIGGERS = [
    "hvac", "filter", "plumber", "contractor", "warranty",
    "appliance", "roof", "gutter", "furnace", "water heater",
    "electrical", "home maintenance", "maintenance task",
    "maintenance list", "home repair", "fix the", "repair the",
    "when did i last", "when was the last time", "property"
]
MESSAGING_TRIGGERS = [
    "text kenz", "text gio", "message kenz", "message gio",
    "tell kenz", "tell gio", "send kenz", "send gio",
    "text my wife", "text my husband", "message my wife",
    "message my husband", "tell my wife", "tell my husband"
]

# Only explicit search phrasing goes straight to Gemini (this path skips voice
# ID). Generic questions ("what's...", "when is...") go through the Haiku
# router, which can still pick gemini_search — so "what's on my to-do list"
# or "when is the furnace filter due" reach the right agent.
WEB_SEARCH_TRIGGERS = [
    "search for", "search the web", "look up", "google", "find out",
    "what do you know about", "news about", "latest news", "latest on",
    "score of", "score for", "did they win", "who won",
    "recipe for", "how to make",
    "weather in", "temperature in", "forecast for",
    "is it open", "hours for"
]

BRIEF_TRIGGERS = ["brief", "briefing"]

def _is_web_search(text: str) -> bool:
    t = text.lower().strip()
    # Don't catch things already handled by other agents
    if _is_timer_request(text):
        return False
    if _contains(t, FLIGHT_TRIGGERS):
        return False
    if _contains(t, SHOPPING_WRITE_TRIGGERS) or _contains(t, SHOPPING_READ_TRIGGERS):
        return False
    if _contains(t, TASK_WRITE_TRIGGERS) or _contains(t, TASK_READ_TRIGGERS):
        return False
    if _contains(t, MAINTENANCE_TRIGGERS):
        return False
    if _contains(t, MESSAGING_TRIGGERS) or _contains(t, BRIEF_TRIGGERS):
        return False
    return _contains(t, WEB_SEARCH_TRIGGERS)

# ── Detection helpers ─────────────────────────────────────────────────────────

def _normalize(text):
    # Lowercase and unify curly apostrophes so "what’s" matches "what's"
    return text.lower().replace("\u2019", "'").strip()

def _contains(text, triggers):
    """Whole-word/phrase match: "land" matches "when do I land" but not
    "island"; "add" does not match "address"."""
    t = _normalize(text)
    return any(re.search(r"\b" + re.escape(tr.strip()) + r"\b", t) for tr in triggers)

def _is_multi_agent(text):
    # Never trigger multi-agent for write operations
    write_words = ["add", "create", "remove", "delete", "mark", "complete", "update", "set", "cancel", "reschedule"]
    if _contains(text, write_words):
        return False
    return _contains(text, MULTI_AGENT_TRIGGERS)

def _is_hear_more(text, last_full=None):
    t = _normalize(text).rstrip(".!?")
    # Bare yes/ok only counts if last response offered more
    if t in ["yes", "yeah", "yep", "sure", "ok", "okay"]:
        if last_full and any(phrase in last_full.lower() for phrase in ["want to hear", "hear more", "few more", "more items"]):
            return True
        return False
    if t in HEAR_MORE_EXACT:
        return True
    return _contains(t, HEAR_MORE_TRIGGERS)

def _is_text_me(text, last_full=None):
    t = _normalize(text).rstrip(".!?")
    # "yes/sure" counts as text_me if last response offered to text
    if t in ["yes", "yeah", "yep", "sure", "ok", "okay", "yes please", "please"]:
        if last_full and any(phrase in last_full.lower() for phrase in ["text it to you", "want me to text", "send it to you"]):
            return True
        return False
    return _contains(text, TEXT_ME_TRIGGERS)

def _is_time_query(text):
    return _contains(text, TIME_QUERY_TRIGGERS)
 
def _is_timer_set(text):
    return _contains(text, TIMER_SET_TRIGGERS)
 
def _is_alarm_set(text):
    return _contains(text, ALARM_SET_TRIGGERS)
 
def _is_timer_cancel(text):
    return _contains(text, TIMER_CANCEL_VERBS) and _contains(text, TIMER_NOUNS)
 
def _is_timer_list(text):
    return _contains(text, TIMER_LIST_TRIGGERS)
 
def _is_timer_request(text):
    return any([
        _is_time_query(text),
        _is_timer_set(text),
        _is_alarm_set(text),
        _is_timer_cancel(text),
        _is_timer_list(text)
    ])


# ── Task completions: which list was it on? ───────────────────────────────────
# "I fixed the gutter" could be Will Due or Andy Mann. The morning brief stores
# each agent's list (task_sources); a word match there hints the right agent.

COMPLETION_HINTS = [
    "mark", "done", "complete", "completed", "finished", "i did",
    "i bought", "i got", "i fixed", "i installed", "i cleaned",
    "i repaired", "i paid", "i picked up", "took care of"
]


def _is_completion_request(text):
    return _contains(text, COMPLETION_HINTS)


def _extract_task_phrase(text):
    t = _normalize(text)
    for word in ["mark", "as done", "as complete", "completed", "complete",
                 "i did", "i bought", "i got", "i fixed", "i installed",
                 "i cleaned", "i repaired", "i paid", "i picked up",
                 "took care of", "finished", "done"]:
        t = t.replace(word, "")
    return t.strip()


def _find_task_source(text, task_sources):
    """Agent whose list (from today's brief) best matches the task words, or None."""
    if not task_sources:
        return None
    words = [w for w in _extract_task_phrase(text).split() if len(w) > 3]
    if not words:
        return None
    scores = {}
    for agent_id in ["will_due", "andy_mann"]:
        listing = (task_sources.get(agent_id) or "").lower()
        matches = sum(1 for w in words if w in listing)
        if matches:
            scores[agent_id] = matches
    return max(scores, key=scores.get) if scores else None


def _agent_not_found(result):
    """Did the agent say it couldn't find the task? Structured status first,
    phrase matching for agents that only return prose."""
    status = result.get("status", "unknown")
    if status != "unknown":
        return status == "not_found"
    text = (result.get("response_text") or "").lower()
    return any(p in text for p in ["don't see", "not in your task", "not in your list", "couldn't find that task"])


# ── Agent dispatch ────────────────────────────────────────────────────────────

def dispatch(agent_id, intent, params, speaker, voice_mode=True, chat_id=0):
    """POST to an agent's /jarvis endpoint. Returns
    {response_text, voice_text (spoken version or None), status, tasks}."""
    agent = registry_agent(agent_id)
    if not agent:
        return {"response_text": f"Agent {agent_id} not found.", "voice_text": None, "status": "error", "tasks": []}
    name = agent["display_name"]

    def error(text):
        return {"response_text": text, "voice_text": None, "status": "error", "tasks": []}

    try:
        resp = requests.post(
            agent["endpoint"],
            json={"intent": intent, "params": params, "speaker": speaker,
                  "voice_mode": voice_mode, "requester_chat_id": chat_id},
            headers={"X-Jarvis-Secret": JARVIS_SECRET},
            timeout=AGENT_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "response_text": data.get("response_text") or "Done.",
            # spoken version written by the agent in the same call (voice mode)
            "voice_text": data.get("voice_text"),
            "status": data.get("status", "unknown"),
            "tasks": data.get("tasks", []),
        }
    except requests.exceptions.Timeout:
        return error(f"{name} is taking too long — please try again in a moment.")
    except requests.exceptions.ConnectionError:
        return error(f"I couldn't reach {name}.")
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        if code == 401:
            return error(f"{name} rejected the request — check JARVIS_SECRET.")
        return error(f"{name} returned an error ({code}). Please try again.")
    except Exception as e:
        print(f"[Router] Dispatch error for {agent_id}: {e}", flush=True)
        return error(f"Something went wrong contacting {name}.")


# ── Voice rendering ───────────────────────────────────────────────────────────

def _voice_result(result, speaker):
    """Spoken + full text for a dispatch result: use the agent's own spoken
    version when it sent one, otherwise fall back to the shortening call."""
    full = result["response_text"]
    voice = result.get("voice_text")
    if voice:
        return {"response_text": voice, "full_response": full, "force_text": False}
    tr = _truncate_for_voice(full, speaker)
    return {"response_text": tr["truncated"], "full_response": full, "force_text": False}


def _render(result, speaker, voice_mode):
    if voice_mode:
        return _voice_result(result, speaker)
    return {"response_text": result["response_text"], "full_response": result["response_text"], "force_text": False}


def _truncate_for_voice(text, speaker):
    address = _address(speaker)
    word_count = len(text.split())

    # Only skip truncation for very short non-flight responses
    if word_count <= 40 and "WN" not in text and "LAX" not in text and "SFO" not in text:
        return {"truncated": text, "has_more": False}

    prompt = (
        "You are Jarvis. Rewrite the following for a voice response to " + address + ".\n"
        "Rules:\n"
        "- Maximum 3 items — pick the most urgent or soonest due\n"
        "- Under 60 words total\n"
        "- Natural spoken sentences, no markdown, no bullets\n"
        "- For flights: say airline name and destination city only. No flight numbers or airport codes.\n"
        "  For the date: calculate how many days away the flight is from today.\n"
        f"  Today is {datetime.now(pytz.timezone(TIMEZONE)).strftime('%A %B %d %Y')}.\n"
        "  If the flight is 7 days or fewer away: say the day of week (e.g. 'this Monday').\n"
        "  If the flight is more than 7 days away: say the full date (e.g. 'June 3rd') — NEVER say just the day of week.\n"
        "  Always include morning, afternoon, or evening based on departure time.\n"
        "- ONLY add 'There are a few more — want to hear them?' if you cut items out\n"
        "- If all items fit, do NOT add that offer\n\n"
        "Full response:\n" + text
    )
    resp = client.messages.create(
        model=ROUTER_MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
        extra_body=llm.fast(),
    )
    truncated = llm.text(resp)
    has_more = "want to hear" in truncated.lower()
    return {"truncated": truncated, "has_more": has_more}

# ── Routing prompt ────────────────────────────────────────────────────────────

def _build_routing_prompt(text, speaker, history, voice_mode=False, task_hint=None):
    agents_list = routing_lines() + (
        "\n- gemini_search: general knowledge, current events, news, sports, recipes, how-to, "
        "or any factual question not handled by the agents above")
    address = _address(speaker)

    history_text = ""
    if history:
        lines = [("User" if h["role"] == "user" else "Jarvis") + ": " + h["content"][:200] for h in history[-6:]]
        history_text = "Recent conversation:\n" + "\n".join(lines) + "\n\n"

    hint = (f"STRONG HINT: this task was found in today's {task_hint} list. Route there unless clearly wrong.\n\n"
            if task_hint else "")

    return (
        "You are Jarvis, an intelligent home orchestrator.\n"
        "You are calm, composed, and precise with a subtle British character.\n"
        f"Today is {datetime.now(PT).strftime('%A %B %d, %Y')}.\n\n"
        + history_text +
        "The speaker is: " + address + " (" + speaker + ")\n\n"
        "Available agents:\n" + agents_list + "\n\n"
        + hint +
        'The user said: "' + text + '"\n\n'
        "Reply with JSON: agent (an agent id, gemini_search, or jarvis), intent (what the user "
        "wants, in a short sentence), direct_response (your reply if agent is jarvis, else null).\n\n"
        "Route to 'jarvis' only for greetings, small talk, or questions about Jarvis himself.\n"
        "Route to 'gemini_search' for general knowledge or current events questions.\n"
        "For everything else pick the most specific agent.\n"
        + ("If agent is 'jarvis', write direct_response as one or two natural spoken sentences — no markdown. "
           + NO_COURTESY_QUESTION + "\n"
           if voice_mode else "") +
        "IMPORTANT: will_due is for ALL personal tasks and to-dos. andy_mann is ONLY for home maintenance tasks.\n"
        "If someone says 'add X to my to-do list' or 'add X to my task list' — always route to will_due.\n"
        "If someone says 'add X to my maintenance list' or X is clearly home-related — route to andy_mann.\n"
        "When marking tasks done, check the conversation history: a task mentioned under home/maintenance "
        "goes to andy_mann, one mentioned under personal tasks goes to will_due. If it sounds like home "
        "maintenance (water, plants, HVAC, repairs, irrigation) route to andy_mann; if unclear, will_due.\n"
    )


def _parse_timer_intent(text: str) -> dict:
    """Use Claude Haiku (structured output) to parse a timer/alarm request."""
    now_pt = datetime.now(pytz.timezone(TIMEZONE))
    prompt = (
        "Parse this timer/alarm request.\n"
        f"Current time: {now_pt.strftime('%I:%M %p on %A %B %d %Y')}\n\n"
        "Rules:\n"
        "- set_timer: for duration-based ('in 20 minutes', 'for 1 hour'); seconds = total seconds\n"
        "- set_alarm: for a specific clock time ('at 7pm', 'at 7:30'); alarm_time = 24-hour HH:MM\n"
        "- name: extract from request ('pizza timer' → 'pizza', 'pasta timer for 10 min' → 'pasta'), default 'timer'\n"
        "- 'half an hour' = 1800, 'quarter hour' = 900\n\n"
        f"Request: \"{text}\""
    )
    resp = client.messages.create(
        model=ROUTER_MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
        extra_body=llm.fast(_structured(TIMER_SCHEMA)),
    )
    return json.loads(llm.text(resp))


def _next_occurrence(hhmm: str) -> float:
    """Unix time of the next HH:MM in Pacific time (tomorrow if already past)."""
    pt = pytz.timezone(TIMEZONE)
    now = datetime.now(pt)
    hour, minute = (int(x) for x in hhmm.strip().split(":")[:2])
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target.timestamp()


def _handle_timer(text: str) -> str:
    """Handle all timer/alarm/time requests (voice only). Returns spoken response."""
    try:
        timer_manager = _timers()
        intent = _parse_timer_intent(text)
        action = intent.get("action")
 
        if action == "time_query":
            return f"It's {timer_manager.get_current_time()}."
 
        elif action == "set_timer":
            seconds = intent.get("seconds")
            name = intent.get("name") or "timer"
            if not seconds:
                return "I didn't catch how long to set the timer for. Could you try again?"
            return timer_manager.set_timer(name, int(seconds))
 
        elif action == "set_alarm":
            alarm_time = intent.get("alarm_time")
            name = intent.get("name") or "alarm"
            if not alarm_time:
                return "I didn't catch what time to set the alarm for. Could you try again?"
            return timer_manager.set_alarm(name, _next_occurrence(alarm_time))
 
        elif action == "cancel":
            name = intent.get("name") or "timer"
            return timer_manager.cancel_timer(name)
 
        elif action == "cancel_all":
            return timer_manager.cancel_all()
 
        elif action == "list":
            return timer_manager.list_timers()
 
        else:
            return "I didn't understand that timer request. Could you try again?"
 
    except Exception as e:
        print(f"[Router] Timer error: {e}", flush=True)
        return "Something went wrong with the timer. Please try again."
 
# ── Web search ────────────────────────────────────────────────────────────────
_gemini_client = None


def _web_search(query, speaker, voice_mode=True):
    """Answer with Gemini + Google Search grounding."""
    global _gemini_client
    try:
        from google import genai
        from google.genai import types

        if _gemini_client is None:
            _gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        style = ("2-3 short spoken sentences. No markdown, no bullets, just natural speech, "
                 "and don't end with a question"
                 if voice_mode else "2-3 short sentences. No markdown, no bullets, just natural conversational text")
        response = _gemini_client.models.generate_content(
            model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite"),
            contents=f"Answer this question for {_address(speaker)} in {style}: {query}",
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
        )
        return response.text.strip()
    except Exception as e:
        print(f"[WebSearch] Error: {e}", flush=True)
        return "I wasn't able to find an answer to that right now."


def _search(text, speaker, voice_mode):
    return _web_search(text, speaker) if voice_mode else _web_search(text, speaker, False)


# ── Merge multi-agent responses ───────────────────────────────────────────────

REJECTION_PHRASES = [
    "outside the scope", "not for me", "home maintenance only",
    "can't help with", "wrong assistant", "not my area",
    "not something i can", "that's not", "i don't handle"
]


def _render_task_list(task_lists):
    """Combine structured task lists from several agents in code (no LLM, so
    nothing can be invented). task_lists: [(agent_id, [task dicts])]"""
    tasks = [{**t, "_source": source} for source, items in task_lists for t in items
             if (t.get("status") or "Pending") != "Done"]
    if not tasks:
        return "Nothing pending — you're all caught up! 🎉"

    def score(t):
        try:
            return int(t.get("score") or 5)
        except (TypeError, ValueError):
            return 5
    tasks.sort(key=score)
    lines = ["📋 *Your combined list:*", ""]
    for t in tasks:
        prio = f" _{t['priority']}_" if t.get("priority") else ""
        home = " 🏠" if t["_source"] == "andy_mann" else ""
        lines.append(f"• {t.get('task')}{prio}{home}")
    return "\n".join(lines)


def _merge(text, speaker, results, voice_mode):
    """results: [(agent_id, dispatch result)] from the fan-out."""
    # Every agent returned a structured list: compose in code
    if results and all(r.get("status") == "list" for _, r in results):
        full = _render_task_list([(aid, r.get("tasks", [])) for aid, r in results])
        return _render({"response_text": full, "voice_text": None}, speaker, voice_mode)

    valid = [(aid, r) for aid, r in results
             if (r.get("response_text") or "").strip()
             and not any(p in r["response_text"].lower() for p in REJECTION_PHRASES)]
    if not valid:
        return {"response_text": "I wasn't able to get a response. Please try again.",
                "full_response": None, "force_text": False}
    if len(valid) == 1:
        return _render(valid[0][1], speaker, voice_mode)

    sections = "\n\n".join(
        "=== " + registry_agent(aid)["display_name"].upper() + " ===\n" + r["response_text"] for aid, r in valid
    )
    prompt = (
        "You are Jarvis. Merge the following agent responses into a single clean response for "
        + _address(speaker) + ".\n\n"
        f"Today is {datetime.now(PT).strftime('%A %B %d, %Y')}.\n\n"
        "Original request: \"" + text + "\"\n\n"
        "Agent responses:\n" + sections + "\n\n"
        "CRITICAL RULES:\n"
        "- ONLY report information explicitly present in the agent responses above\n"
        "- NEVER invent, assume, or extrapolate any tasks, appointments, flights, or events\n"
        "- NEVER mention that an agent rejected or couldn't handle something\n"
        "- Be concise — lead with the most urgent or time-sensitive items\n"
        "- If no time interval was specified, only include items due THIS WEEK\n"
        "- Do not repeat yourself or pad the response\n"
        "- Use Telegram Markdown\n"
        # Voice: write the spoken version in the same call (saves a round-trip)
        + ("\nAfter the merged response, add ONE final line starting with VOICE| followed by "
           "a spoken version on a single line: natural sentences, no markdown, at most 3 items "
           "(most urgent first), under 60 words, never flight numbers or airport codes. Only if "
           "you left items out, end it with \"There are a few more — want to hear them?\"\n"
           if voice_mode else "")
    )
    resp = client.messages.create(
        model=ROUTER_MODEL,
        max_tokens=950,
        messages=[{"role": "user", "content": prompt}],
        extra_body=llm.fast(),
    )
    full_text, voice_text = _split_voice(llm.text(resp))
    return _render({"response_text": full_text, "voice_text": voice_text}, speaker, voice_mode)


# ── Main route function ───────────────────────────────────────────────────────

ROUTING_LOG_MAX_BYTES = 5 * 1024 * 1024  # rotate to routing_log.csv.1 beyond this


def _log_routing_decision(text: str, agent: str, method: str):
    import csv
    pt = pytz.timezone(TIMEZONE)
    from jarvis_core.config import home_path
    log_path = home_path("routing_log.csv")
    try:
        if os.path.exists(log_path) and os.path.getsize(log_path) > ROUTING_LOG_MAX_BYTES:
            os.replace(log_path, log_path + ".1")  # keep one previous file
    except OSError:
        pass
    file_exists = os.path.exists(log_path)
    try:
        with open(log_path, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["timestamp", "text", "agent", "method"])
            writer.writerow([
                datetime.now(pt).isoformat(),
                text,
                agent,
                method
            ])
    except Exception as e:
        print(f"[Router] Log error: {e}", flush=True)

def _local_route(text: str) -> str | None:
    t = text.lower().strip()

    # Shopping — unambiguous, always Shawn Ping
    if _contains(t, SHOPPING_WRITE_TRIGGERS) or _contains(t, SHOPPING_READ_TRIGGERS):
        return "shawn_ping"

    # Flights — unambiguous, always Drew Parture
    if _contains(t, FLIGHT_TRIGGERS):
        return "drew_parture"

    # Tasks and maintenance — too ambiguous, let Haiku decide
    # "my tasks" could be Will Due or Andy Mann
    # "I fixed X" completion depends on what X is

    return None  # Fall through to Haiku


def _route_with_haiku(text, speaker, history, voice_mode, task_hint=None):
    """Returns (agent_id, intent, direct_response, method)."""
    local_agent = _local_route(text)
    if local_agent:
        print(f"[Router] Local route → {local_agent}", flush=True)
        _log_routing_decision(text, local_agent, "local")
        return local_agent, text, None, "local"

    print("[Router] Local route miss → calling Haiku", flush=True)
    t_haiku = time.time()
    try:
        resp = client.messages.create(
            model=ROUTER_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": _build_routing_prompt(
                text, speaker, history=history, voice_mode=voice_mode, task_hint=task_hint)}],
            extra_body=llm.fast(_structured(_routing_schema())),
        )
        routing = json.loads(llm.text(resp))
        agent_name = routing.get("agent") or "jarvis"
        intent = routing.get("intent") or text
        print(f"[Router] Haiku route → {agent_name} ({(time.time()-t_haiku)*1000:.0f}ms)", flush=True)
        _log_routing_decision(text, agent_name, "haiku")
        return agent_name, intent, routing.get("direct_response"), "haiku"
    except Exception as e:
        print(f"[Router] Haiku routing error: {e}", flush=True)
        _log_routing_decision(text, "error", "error")
        return "error", text, None, "error"


def route(text, speaker, history=None, last_full=None, voice_mode=True,
          channel="voice", task_sources=None, chat_id=0, voice_status=None):
    """
    Main entry point for both the voice loop and Telegram. Returns:
      {"response_text": str, "full_response": str|None, "force_text": bool}
    voice_mode: reply should be spoken (short, no markdown).
    channel:    "voice" (the Pi speaker) or "telegram".
    task_sources: today's per-agent lists from the morning brief; loaded from
                  shared state when not passed.
    speaker:    "gio" / "kenz", or None when unknown (voice ID off) — see
                "Who is asking?" above.
    voice_status: voice ID result for this request (see "Sensitivity tiers");
                None when identity is known otherwise (Telegram).
    """
    history = history or []
    print(f"[Router] Input: {text[:80]} speaker={speaker} channel={channel}", flush=True)

    # Speaker unknown (voice ID off): is this the answer to "Gio or Kenz?"
    if speaker is None:
        answer = _owner_answer(text, channel)
        if answer:
            original, who = answer
            print(f"[Router] Owner answer → {who}: re-running {original[:60]!r}", flush=True)
            return route(original, who, history, last_full, voice_mode, channel, task_sources, chat_id,
                         voice_status)

    # Who the request is for: the known speaker, else a name said in it
    named = _speaker_from_text(text) if speaker is None else None
    owner_known = speaker is not None or named is not None or _mentions_shared(text)
    speaker = speaker or named or DEFAULT_SPEAKER

    try:
        # Text me that
        if _is_text_me(text, last_full) and last_full:
            return _gate("personal", voice_status) or {
                "response_text": last_full, "full_response": last_full, "force_text": True}

        # Hear more (replays the last answer, which may have been personal)
        if _is_hear_more(text, last_full) and last_full:
            blocked = _gate("personal", voice_status)
            if blocked:
                return blocked
            if voice_mode:
                result = _truncate_for_voice(last_full, speaker)
                return {"response_text": result["truncated"], "full_response": None, "force_text": False}
            return {"response_text": last_full, "full_response": None, "force_text": False}

        # Timers and alarms ring on the Pi speaker only
        if _is_timer_request(text):
            if channel != "voice":
                return {"response_text": "Timers and alarms live on the speaker — just ask me out loud.",
                        "full_response": None, "force_text": False}
            response_text = _handle_timer(text)
            return {"response_text": response_text, "full_response": response_text, "force_text": False}

        # Multi-agent fan-out — both agents asked at the same time
        # (only agents present in registry.json — some installs skip one)
        fan_out = [a for a in ("will_due", "andy_mann") if registry_agent(a)]
        if fan_out and _is_multi_agent(text):
            agent_ids_ = fan_out
            blocked = _gate(_strictest([access(a) for a in agent_ids_]), voice_status)
            if blocked:
                return blocked
            if not owner_known and any(needs_speaker(a) for a in agent_ids_):
                return _ask_owner(text, channel)
            with ThreadPoolExecutor(max_workers=len(agent_ids_)) as pool:
                results = list(pool.map(
                    lambda a: dispatch(a, text, {"original_message": text}, speaker,
                                       voice_mode=voice_mode, chat_id=chat_id),
                    agent_ids_))
            return _merge(text, speaker, list(zip(agent_ids_, results)), voice_mode)

        # Explicit web search
        if _is_web_search(text):
            print("[Router] Web search → Gemini", flush=True)
            _log_routing_decision(text, "gemini_search", "local")
            response_text = _search(text, speaker, voice_mode)
            return {"response_text": response_text, "full_response": response_text, "force_text": False}

        # Completed task? Hint the list it was on in today's brief
        task_hint = None
        if _is_completion_request(text):
            sources = task_sources if task_sources is not None else state.get_task_sources(speaker)
            task_hint = _find_task_source(text, sources)
            if task_hint:
                print(f"[Router] Task found in today's {task_hint} list", flush=True)

        agent_id, intent, direct, method = _route_with_haiku(text, speaker, history, voice_mode, task_hint)
        params = {"original_message": text}

        # Never pretend a failed request succeeded
        if agent_id == "error":
            return {"response_text": "Sorry, I didn't manage to process that. Please try again.",
                    "full_response": None, "force_text": False}

        if agent_id == "gemini_search":
            response_text = _search(text, speaker, voice_mode)
            return {"response_text": response_text, "full_response": response_text, "force_text": False}

        if agent_id in ("none", "null", "", None, "jarvis"):
            return {"response_text": direct or "Understood.", "full_response": None, "force_text": False}

        blocked = _gate(access(agent_id), voice_status)
        if blocked:
            return blocked
        if not owner_known and needs_speaker(agent_id):
            return _ask_owner(text, channel)

        result = dispatch(agent_id, intent, params, speaker, voice_mode=voice_mode, chat_id=chat_id)

        # Agent couldn't find the task, but today's brief says it's on the other list
        if task_hint and agent_id != task_hint and _agent_not_found(result):
            blocked = _gate(access(task_hint), voice_status)
            if blocked:
                return blocked
            if not owner_known and needs_speaker(task_hint):
                return _ask_owner(text, channel)
            print(f"[Router] {agent_id} couldn't find it → retrying on {task_hint}", flush=True)
            result = dispatch(task_hint, intent, params, speaker, voice_mode=voice_mode, chat_id=chat_id)

        return _render(result, speaker, voice_mode)

    except Exception as e:
        import traceback
        print(f"[Router] Error: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        return {"response_text": "Something went wrong. Please try again.", "full_response": None, "force_text": False}


# Public names used by the voice loop for the guest web-search path
is_web_search = _is_web_search
web_search = _web_search
