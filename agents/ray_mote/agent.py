"""
agent.py — Ray Mote: the Nest thermostats by voice and Telegram.

Common requests ("set upstairs to 70", "make it warmer", "what's it set to")
are read with keyword rules, with no Claude call; anything else is parsed by
Claude into the same command. Code, never the model, decides what is sent to
Nest and writes the reply, so the temperature limits can't be talked around.

When Ray needs an answer ("Upstairs or downstairs?"), the reply has
status "question" and the words that answer it; the router sends the
answer back here.
"""
import json
import os
import re
import time

import anthropic

import nest
from jarvis_core import llm

MIN_F = int(os.environ.get("RAY_MOTE_MIN_F", "68"))
MAX_F = int(os.environ.get("RAY_MOTE_MAX_F", "75"))
STEP_F = 2              # "make it warmer" / "cooler"
RANGE_GAP_F = 3         # heat-and-cool mode keeps the two set points this far apart
PENDING_SECONDS = 120   # how long a question stays open

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

ALL_WORDS = ["both", "all", "everywhere", "whole house", "the house", "every floor",
             "both floors", "all of them", "each floor"]
YES_WORDS = ["yes", "yeah", "yep", "sure", "ok", "okay", "please", "do it", "go ahead", "fine"]
NO_WORDS = ["no", "nope", "don't", "cancel", "never mind", "leave it"]

HEAT_WORDS = ["heat", "heater", "heating", "furnace"]
COOL_WORDS = ["ac", "a/c", "air conditioning", "air conditioner", "air con", "cooling", "cool"]
ON_VERBS = ["turn on", "switch on", "start", "put on", "switch to", "set it to", "put it on", "change to"]
OFF_VERBS = ["turn off", "switch off", "shut off", "shut down", "stop"]
UP_WORDS = ["warmer", "warm it up", "warm up", "heat it up", "turn up", "turn it up", "raise",
            "increase", "bump up", "bump it up", "crank", "higher", "up", "i'm cold", "i'm freezing",
            "too cold", "it's cold", "it's freezing"]
DOWN_WORDS = ["cooler", "colder", "cool it down", "cool down", "turn down", "turn it down", "lower",
              "decrease", "drop", "down", "i'm hot", "too hot", "too warm", "it's hot"]
SMALL_WORDS = ["a bit", "a little", "a degree", "one degree", "slightly", "a touch"]
STATUS_WORDS = ["what", "what's", "how", "is it", "is the", "status", "check", "tell me"]
# Schedules aren't supported: say so rather than change it now
SCHEDULE_WORDS = ["o'clock", "tonight", "tomorrow", "every", "schedule", "in the morning", "at night",
                  "when i", "when we"]
CLOCK_TIME = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)")

UNSUPPORTED_TEXT = ("I can set the temperature, make it warmer or cooler, switch heat, cooling or "
                    "eco mode, and tell you what it's set to.")
NO_DEVICES_TEXT = "I can't see any thermostats on the Nest account."

_pending = {}   # speaker -> {"kind": "zone"|"confirm", "cmd": {...}, "expires": time}


# ── Words ──────────────────────────────────────────────────────────────────────

def _norm(text):
    return text.lower().replace("’", "'").strip()


def _has(text, words):
    return any(re.search(r"(?<![\w/])" + re.escape(w) + r"(?![\w/])", text) for w in words)


def _fmt(f):
    return str(int(round(f)))


def _clamp(f, low=None, high=None):
    return max(MIN_F if low is None else low, min(MAX_F if high is None else high, f))


def _join(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


# ── Zones (one per thermostat) ─────────────────────────────────────────────────

def _aliases():
    """NEST_ZONES="upstairs=Hallway, downstairs=Living Room" → {"hallway": "upstairs", ...}"""
    out = {}
    for pair in os.environ.get("NEST_ZONES", "").split(","):
        if "=" in pair:
            spoken, nest_name = pair.split("=", 1)
            out[nest_name.strip().lower()] = spoken.strip().lower()
    return out


def zone_name(device):
    """The name people say for this thermostat ("upstairs")."""
    aliases = _aliases()
    for key in (device["label"].lower(), device["room"].lower()):
        if key and key in aliases:
            return aliases[key]
    return re.sub(r"\s*thermostat$", "", device["label"].lower()).strip() or "thermostat"


def _find_zone(text, devices):
    """The thermostats a request names, "all", or None."""
    t = _norm(text)
    named = [d for d in devices if _has(t, [zone_name(d)])]
    if named:
        return named
    if _has(t, ALL_WORDS):
        return "all"
    return None


def _zone_question(devices):
    names = [zone_name(d).capitalize() if i == 0 else zone_name(d) for i, d in enumerate(devices)]
    return (", ".join(names[:-1]) + " or " + names[-1] + "?") if len(names) > 1 else names[0] + "?"


# ── Understanding the request ──────────────────────────────────────────────────

def _command(action=None, **kw):
    # "of": the set point the request names ("set the AC to 72" → COOL)
    cmd = {"action": action, "target": None, "delta": None, "mode": None, "eco": None, "zone": None, "of": None}
    cmd.update(kw)
    return cmd


def quick_parse(text):
    """The common requests, without Claude. None when unsure."""
    t = _norm(text)

    if _has(t, SCHEDULE_WORDS) or CLOCK_TIME.search(t):
        return _command("unsupported")
    if _has(t, ["eco", "eco mode"]):
        return _command("eco", eco=not _has(t, OFF_VERBS + ["off", "exit", "leave", "out of", "disable"]))

    # "turn on the heat" or "turn the heat on"
    turn = _has(t, ["turn", "switch", "shut"])
    on = _has(t, ON_VERBS) or (turn and _has(t, ["on"]))
    off = _has(t, OFF_VERBS) or (turn and _has(t, ["off"]))
    mode = None
    if _has(t, ["heat and cool", "heat cool", "heat-cool", "auto mode"]):
        mode = "HEATCOOL"
    elif off and _has(t, HEAT_WORDS + COOL_WORDS + ["thermostat", "hvac", "system"]):
        return _command("mode", mode="OFF")
    elif on and _has(t, HEAT_WORDS):
        mode = "HEAT"
    elif on and _has(t, COOL_WORDS):
        mode = "COOL"

    # "by 3", "3 degrees warmer", "up 2 degrees"
    rel = (re.search(r"\bby (\d{1,2})\b", t)
           or re.search(r"\b(\d{1,2}) degrees? (?:warmer|cooler|colder|up|down|higher|lower)\b", t)
           or re.search(r"\b(?:up|down) (\d{1,2}) degrees?\b", t))
    up, down = _has(t, UP_WORDS), _has(t, DOWN_WORDS)
    if rel and (up or down) and up != down:
        n = int(rel.group(1))
        return _command("adjust", delta=n if up else -n, mode=mode)

    absolute = (re.search(r"\b(?:to|at|make it|keep it)\s+(\d{2})\b", t)
                or re.search(r"\b(\d{2})\s*(?:degrees|°)", t))
    if absolute and 40 <= int(absolute.group(1)) <= 99:
        heat, cool = _has(t, HEAT_WORDS), _has(t, COOL_WORDS)
        return _command("set", target=int(absolute.group(1)), mode=mode,
                        of="HEAT" if heat and not cool else "COOL" if cool and not heat else None)

    if mode:
        return _command("mode", mode=mode)

    if up != down and not _has(t, ["what", "how", "?"]):
        step = 1 if _has(t, SMALL_WORDS) else STEP_F
        return _command("adjust", delta=step if up else -step)

    if _has(t, STATUS_WORDS) or t.endswith("?"):
        return _command("status")
    return None


PARSE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["set", "adjust", "mode", "eco", "status", "unsupported"]},
        "zone": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "target_f": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "delta_f": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "mode": {"anyOf": [{"type": "string", "enum": ["HEAT", "COOL", "HEATCOOL", "OFF"]}, {"type": "null"}]},
        "eco": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
    },
    "required": ["action", "zone", "target_f", "delta_f", "mode", "eco"],
    "additionalProperties": False,
}


def claude_parse(text, devices):
    zones = [zone_name(d) for d in devices]
    prompt = (
        "Turn this request to a home's Nest thermostats into JSON.\n"
        f"Thermostats: {', '.join(zones)}. Temperatures are Fahrenheit.\n"
        "- set: an exact temperature (target_f). adjust: warmer/cooler (delta_f, + is warmer; "
        f"{STEP_F} if no amount is given).\n"
        "- mode: switch to HEAT, COOL, HEATCOOL (heat and cool) or OFF. eco: eco mode on (true) or off.\n"
        "- status: asking what it's set to or how warm it is inside.\n"
        "- unsupported: anything else (schedules, fans, other devices).\n"
        f"- zone: one of {', '.join(zones)}, \"all\", or null if none is said.\n\n"
        f"Request: \"{text}\""
    )
    try:
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL") or llm.MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
            extra_body=llm.fast({"output_config": {"format": {"type": "json_schema", "schema": PARSE_SCHEMA}}}),
        )
        data = json.loads(llm.text(resp))
    except Exception as e:
        print(f"[Ray Mote] Parse error: {e}", flush=True)
        return _command("unsupported")
    zone = (data.get("zone") or "").lower()
    named = [d for d in devices if zone_name(d) == zone]
    return _command(data.get("action"), target=data.get("target_f"), delta=data.get("delta_f"),
                    mode=data.get("mode"), eco=data.get("eco"),
                    zone="all" if zone == "all" else (named or None))


# ── Doing it ───────────────────────────────────────────────────────────────────

def _range_around(target):
    """Heat-and-cool mode: a comfortable band around one temperature."""
    heat, cool = _clamp(target - 2), _clamp(target + 2)
    if cool - heat < RANGE_GAP_F:
        if cool >= MAX_F:
            heat = cool - RANGE_GAP_F
        else:
            cool = heat + RANGE_GAP_F
    return heat, cool


def _fresh(device):
    return next((d for d in nest.thermostats(fresh=True) if d["name"] == device["name"]), device)


def _apply(cmd, d):
    """Carry out cmd on one thermostat; returns what happened, e.g. "set to 70"."""
    if not d["online"]:
        return "is offline, so I couldn't change it"
    action = cmd["action"]

    if action == "eco":
        nest.set_eco(d, cmd["eco"])
        return "is in eco mode" if cmd["eco"] else "is out of eco mode"

    if cmd.get("mode"):
        if cmd["mode"] != "OFF" and cmd["mode"] not in d["modes"]:
            return {"HEAT": "has no heating", "COOL": "has no cooling",
                    "HEATCOOL": "can't do heat and cool"}[cmd["mode"]]
        nest.set_mode(d, cmd["mode"])
        if action == "mode":
            return {"HEAT": "is on heat", "COOL": "is on cooling", "HEATCOOL": "is on heat and cool",
                    "OFF": "is switched off"}[cmd["mode"]]
        d = {**d, "mode": cmd["mode"]}

    if d["eco"]:
        nest.set_eco(d, False)   # Nest ignores set points while in eco mode
        # "warmer" needs the set point eco mode was hiding
        d = _fresh(d) if action == "adjust" else {**d, "eco": False}
    mode = d["mode"]
    if mode == "OFF":
        return "is switched off, so I left it. Say \"turn on the heat\" first"

    if action == "set":
        if {cmd.get("of"), mode} == {"HEAT", "COOL"}:
            return ("is on heat, so I left it. Say \"turn on the AC\" first" if mode == "HEAT"
                    else "is on cooling, so I left it. Say \"turn on the heat\" first")
        target = int(_clamp(cmd["target"]))
        if mode == "HEAT":
            nest.set_heat(d, target)
        elif mode == "COOL":
            nest.set_cool(d, target)
        else:
            heat, cool = _range_around(target)
            nest.set_range(d, heat, cool)
            return f"is kept between {_fmt(heat)} and {_fmt(cool)}"
        return f"is set to {target}"

    # adjust
    delta = cmd["delta"] or STEP_F
    if None in ((d["heat_f"], d["cool_f"]) if mode == "HEATCOOL" else
                (d["heat_f"] if mode == "HEAT" else d["cool_f"],)):
        return "didn't report its set point. Try again in a moment"
    if mode == "HEATCOOL":
        heat = _clamp(round(d["heat_f"]) + delta, high=MAX_F - RANGE_GAP_F)
        cool = _clamp(round(d["cool_f"]) + delta, low=MIN_F + RANGE_GAP_F)
        if (heat, cool) == (round(d["heat_f"]), round(d["cool_f"])):
            return f"is already as {'warm' if delta > 0 else 'cool'} as I can set it"
        nest.set_range(d, heat, cool)
        return f"is kept between {_fmt(heat)} and {_fmt(cool)}"
    current = round(d["heat_f"] if mode == "HEAT" else d["cool_f"])
    new = int(_clamp(current + delta))
    if new == current:
        return f"is already at {current}, the {'most' if delta > 0 else 'least'} I can set"
    (nest.set_heat if mode == "HEAT" else nest.set_cool)(d, new)
    return f"is set to {new}"


def _describe(d):
    zone = zone_name(d).capitalize()
    if not d["online"]:
        return f"{zone} is offline."
    now = f"{zone} is {_fmt(d['ambient_f'])} degrees" if d["ambient_f"] is not None else zone
    if d["eco"]:
        return f"{now}, in eco mode."
    if d["mode"] == "OFF":
        return f"{now}, with the system off."
    if d["mode"] == "HEATCOOL":
        return f"{now}, kept between {_fmt(d['heat_f'])} and {_fmt(d['cool_f'])}."
    setpoint = _fmt(d["heat_f"] if d["mode"] == "HEAT" else d["cool_f"])
    if d["hvac"] in ("HEATING", "COOLING"):
        return f"{now}, {d['hvac'].lower()} to {setpoint}."
    return f"{now}, set to {setpoint}."


def _reply(text, status="done", answers=None):
    # Short and plain, so it's spoken as is (no shortening call in the router)
    return {"response_text": text, "voice_text": text, "status": status, "answers": answers or []}


def _ask(speaker, kind, cmd, question, answers):
    _pending[speaker] = {"kind": kind, "cmd": cmd, "expires": time.time() + PENDING_SECONDS}
    return _reply(question, status="question", answers=answers)


def run(cmd, devices, speaker):
    action = cmd["action"]
    zone = cmd.get("zone")
    if action not in ("set", "adjust", "mode", "eco", "status"):
        return _reply(UNSUPPORTED_TEXT, status="unsupported")

    if action == "status":
        chosen = zone if isinstance(zone, list) else devices
        return _reply(" ".join(_describe(d) for d in chosen), status="status")

    if action == "set" and cmd["target"] is not None and not MIN_F <= cmd["target"] <= MAX_F:
        allowed = int(_clamp(cmd["target"]))
        if isinstance(zone, list):
            where = _join([zone_name(d) for d in zone])
        elif zone == "all":
            where = "both" if len(devices) == 2 else "all of them"
        else:
            where = zone_name(devices[0]) if len(devices) == 1 else "it"
        return _ask(speaker, "confirm", {**cmd, "target": allowed},
                    f"I can only set between {MIN_F} and {MAX_F}. Set {where} to {allowed}?",
                    YES_WORDS + NO_WORDS)
    if action == "set" and cmd["target"] is None:
        return _reply("What temperature would you like?", status="unsupported")

    if zone == "all":
        targets = devices
    elif zone:
        targets = zone
    elif len(devices) == 1:
        targets = devices
    else:
        return _ask(speaker, "zone", cmd, _zone_question(devices),
                    [zone_name(d) for d in devices] + ALL_WORDS)

    results = []
    for d in targets:
        try:
            results.append((zone_name(d), _apply(cmd, d)))
        except nest.NestError as e:
            results.append((zone_name(d), None))
            print(f"[Ray Mote] {zone_name(d)}: {e}", flush=True)
            if len(targets) == 1:
                return _reply(str(e), status="error")
    # "Upstairs and downstairs are set to 70." when they all ended the same
    outcomes = {r for _, r in results}
    if len(results) > 1 and len(outcomes) == 1 and None not in outcomes:
        what = results[0][1]
        what = "are" + what[2:] if what.startswith("is ") else what
        text = f"{_join([z for z, _ in results]).capitalize()} {what}."
    else:
        text = " ".join(f"{z.capitalize()} {r}." if r else f"{z.capitalize()}: Nest didn't accept that."
                        for z, r in results)
    return _reply(text)


def _answer(pending, text, devices, speaker):
    """A reply to Ray's last question, or None if it's a new request
    ("what's it like upstairs?" names a floor but asks something else)."""
    if len(_norm(text).split()) > 5 or quick_parse(text):
        return None
    cmd = pending["cmd"]
    if pending["kind"] == "zone":
        zone = _find_zone(text, devices)
        return run({**cmd, "zone": zone}, devices, speaker) if zone else None
    t = _norm(text)
    if _has(t, NO_WORDS):
        return _reply("Okay, I'll leave it as it is.")
    if _has(t, YES_WORDS):
        return run(cmd, devices, speaker)
    return None


def process_message(text, speaker="gio"):
    """{response_text, voice_text, status, answers}. status: done / status /
    question (answers: the words that answer it) / unsupported / error."""
    try:
        devices = nest.thermostats()
        if not devices:
            return _reply(NO_DEVICES_TEXT, status="error")
        pending = _pending.pop(speaker, None)
        if pending and pending["expires"] > time.time():
            answered = _answer(pending, text, devices, speaker)
            if answered:
                return answered
        cmd = quick_parse(text) or claude_parse(text, devices)
        if cmd.get("zone") is None:
            cmd["zone"] = _find_zone(text, devices)
        print(f"[Ray Mote] {text[:60]!r} → {cmd['action']} target={cmd['target']} "
              f"delta={cmd['delta']} mode={cmd['mode']} eco={cmd['eco']}", flush=True)
        return run(cmd, devices, speaker)
    except nest.NestError as e:
        return _reply(str(e), status="error")
