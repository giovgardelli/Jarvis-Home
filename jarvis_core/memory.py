"""
memory.py — the household memory: what Jarvis learns about your home over time.

Stored only on the Pi, in the same SQLite file as the rest of Jarvis's state.

How facts get in
  - Told:    "Jarvis, remember that our team is the Mariners" → saved at once.
  - Learned: every exchange (voice and Telegram) goes into a short-lived inbox.
             Each night (3:30am) Claude reads the day's inbox with the current
             memory and proposes new facts, reinforcements, corrections and
             expiries. The raw inbox is then deleted — only distilled facts stay.
             A learned fact is used once it was clearly *stated* by someone in
             the household ("my aunt visits on Friday"), or once the same
             pattern shows up on two different days ("asks about the Mariners").
  - Never stored: passwords, PINs, door/garage/alarm codes, card or account
    numbers (checked in code as well as asked of Claude).

How facts get used — only the ones relevant to the request, so the least
information leaves the house:
  - Routing prompt: lets Jarvis answer household questions ("when is Kenz's aunt
    visiting?") and route with context.
  - Web answers: facts marked web_ok ("home city is Seattle", "team is the
    Mariners") help make vague questions specific ("what time's the game?").
    Gemini only sees those facts, never the rest of the memory.

Who a fact is about: "household", or a person when Jarvis knows who said it
(Telegram account, or a confident voice match — see MEMORY_VOICE_GUESS).

Commands (voice or Telegram): "remember that…", "forget that…",
"what do you remember?". New facts are listed in the morning brief so you can
see what was learned and say "forget …" if it's wrong.

Settings (~/.jarvis_env): MEMORY=on|off (default on).
"""
import json
import os
import re
import sqlite3
import threading
from datetime import date, datetime, timedelta

import pytz

from jarvis_core.config import TIMEZONE, home_path

ENABLED = os.environ.get("MEMORY", "on").strip().lower() not in ("0", "off", "false", "no")
DB_PATH = os.environ.get("JARVIS_STATE_DB", home_path("jarvis_state.db"))
PT = pytz.timezone(TIMEZONE)

MAX_RELEVANT = 8            # facts added to one request, at most
VOICE_MAX_FACTS = 8         # facts read out by voice; the rest via "text me that"
INBOX_KEEP_DAYS = 14        # unprocessed exchanges older than this are dropped
CANDIDATE_KEEP_DAYS = 45    # an unconfirmed pattern not seen again is dropped
ALWAYS = "*"                # topic meaning "relevant to every request" (e.g. home city)

# Never store these, whatever Claude proposes
SECRET = re.compile(
    r"\b(password|passcode|passwd|pin|pin code|door code|garage code|alarm code|gate code|"
    r"lock code|security code|social security|ssn|credit card|card number|account number|"
    r"routing number|cvv)\b|\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b", re.I)

STOPWORDS = set("""a an the and or but if of to in on at for from with by about as is are was were be
been being do does did have has had i me my we our us you your he she it its they them their
this that these those what whats what's when where who whom which why how can could would should
will shall may might must please jarvis hey tell give get got any some there here today tonight
tomorrow now just really very much more most also than then so not no yes ok okay""".split())

_conn = None
_lock = threading.Lock()

# Dated facts ("Kenz's birthday is September 14th") carry a reminder plan, decided
# once by Claude when the fact is saved; delivery is plain code (reminders_for)
REMINDER_COLUMNS = (
    ("event_date", "TEXT"),      # YYYY-MM-DD (any year for yearly events)
    ("yearly", "INTEGER"),       # 1 = birthdays, anniversaries
    ("remind_days", "TEXT"),     # "14,7,1,0" — days before the date
    ("remind_who", "TEXT"),      # "gio" — never the person a gift is for
    ("action", "TEXT"),          # "order a gift"
)


def _now():
    return datetime.now(PT)


def _conn_():
    global _conn
    if _conn is None:
        # The voice loop and the Telegram service share this file
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        _conn.row_factory = sqlite3.Row
        _conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fact TEXT NOT NULL,
                subject TEXT NOT NULL DEFAULT 'household',
                topics TEXT NOT NULL DEFAULT '',
                web_ok INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL,              -- told / learned
                status TEXT NOT NULL,              -- active / candidate / forgotten / expired
                evidence INTEGER NOT NULL DEFAULT 1,
                created TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                activated TEXT,
                expires TEXT                       -- YYYY-MM-DD, or NULL
            );
            CREATE TABLE IF NOT EXISTS memory_inbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                channel TEXT NOT NULL,
                speaker TEXT,
                user_text TEXT NOT NULL,
                reply TEXT NOT NULL
            );
        """)
        # Reminder columns (added later — upgrade older databases in place)
        have = {r[1] for r in _conn.execute("PRAGMA table_info(memories)")}
        for col, kind in REMINDER_COLUMNS:
            if col not in have:
                _conn.execute(f"ALTER TABLE memories ADD COLUMN {col} {kind}")
        _conn.commit()
    return _conn


def _run(sql, args=(), many=False):
    with _lock:
        conn = _conn_()
        cur = conn.executemany(sql, args) if many else conn.execute(sql, args)
        conn.commit()
        return cur


def _rows(sql, args=()):
    with _lock:
        return [dict(r) for r in _conn_().execute(sql, args).fetchall()]


def _words(text):
    return {w for w in re.findall(r"[a-z0-9']+", (text or "").lower().replace("’", "'"))
            if w not in STOPWORDS and len(w) > 1}


def _stem(w):
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


def is_secret(text):
    return bool(SECRET.search(text or ""))


# ── Recording exchanges (the inbox) ───────────────────────────────────────────

def record_exchange(user_text, reply, channel, speaker=None):
    """Queue one exchange for tonight's learning. Never raises."""
    if not ENABLED or not (user_text or "").strip() or is_command(user_text):
        return
    try:
        _run("INSERT INTO memory_inbox (ts, channel, speaker, user_text, reply) VALUES (?,?,?,?,?)",
             (_now().isoformat(), channel, speaker, user_text[:500], (reply or "")[:400]))
    except Exception as e:
        print(f"[Memory] Record error: {e}", flush=True)


# ── Reading facts ─────────────────────────────────────────────────────────────

def active_facts():
    today = _now().date().isoformat()
    return _rows("SELECT * FROM memories WHERE status='active' AND (expires IS NULL OR expires >= ?) "
                 "ORDER BY id", (today,))


def relevant(text, speakers=(), web_only=False, limit=MAX_RELEVANT):
    """Active facts that matter for this request: ones sharing a word with it
    (fact text or its topics), plus ALWAYS facts. Personal facts only for the
    people in `speakers` (who's asking, or who the request names)."""
    if not ENABLED:
        return []
    try:
        facts = active_facts()
    except Exception as e:
        print(f"[Memory] Read error: {e}", flush=True)
        return []
    query = {_stem(w) for w in _words(text)}
    allowed = {"household"} | {s for s in speakers if s}
    scored = []
    for f in facts:
        if f["subject"] not in allowed or (web_only and not f["web_ok"]):
            continue
        topics = f["topics"].split()
        keys = {_stem(w) for w in _words(f["fact"]) | set(topics)}
        score = len(query & keys) + (0.5 if ALWAYS in topics else 0)
        if score:
            scored.append((score, f["id"], f))
    scored.sort(key=lambda s: (-s[0], -s[1]))
    return [f for _, _, f in scored[:limit]]


def prompt_block(facts, heading="What you know about this household (use only if relevant)"):
    if not facts:
        return ""
    return heading + ":\n" + "\n".join(f"- {_label(f)}" for f in facts) + "\n\n"


def _label(f):
    who = "" if f["subject"] == "household" else f"[{f['subject'].title()}] "
    until = f" (until {f['expires']})" if f.get("expires") else ""
    return f"{who}{f['fact']}{until}"


# ── Commands: remember / forget / list ────────────────────────────────────────

# "Jarvis, remember (that) Kenz's birthday is September 14th" — but not "remember to
# call mom" (a to-do), "remember when we…" (reminiscing) or a bare "remember this"
REMEMBER = re.compile(r"^(?:hey\s+)?(?:jarvis[,:]?\s+)?(?:please\s+|can you\s+|could you\s+)?"
                      r"(?:remember(?:\s+that)?|note\s+that|keep in mind(?:\s+that)?)[,:]?\s+"
                      r"(?!to\s|when\s|how\s|the time\s|me\b)(.{3,})$", re.I | re.S)
VAGUE = {"this", "that", "it", "this one", "that one"}
FORGET = re.compile(r"^(?:hey\s+)?(?:jarvis[,]?\s+)?(?:please\s+)?forget\s+(?:that\s+|about\s+)?(.+)$", re.I)
LIST = ("what do you remember", "what do you know", "what have you learned", "what have you learnt",
        "show me your memory", "list your memories", "tell me everything you know",
        "tell me what you know", "tell me what you remember", "everything you know",
        "what's in your memory", "what is in your memory")
FORGET_ALL = ("forget everything", "erase your memory", "wipe your memory", "clear your memory")


def _clean(text):
    return (text or "").strip().rstrip(".!?").replace("’", "'")


def command(text):
    """('remember', fact) / ('forget', what) / ('forget_all', None) / ('list', None) / None."""
    t = _clean(text)
    low = t.lower()
    # Only the bare question lists the memory; "what do you remember about
    # the plumber?" is a question for the router (with the facts in its prompt)
    bare = re.sub(r"^(?:hey\s+)?(?:jarvis[,:]?\s+)?(?:please\s+)?|\s+about (?:us|me|the household|our home"
                  r"|the house|my family|our family|the family)$", "", low)
    if bare in LIST:
        return ("list", None)
    if any(p in low for p in FORGET_ALL):
        return ("forget_all", None)
    m = REMEMBER.match(t)
    if m and m.group(1).strip().lower() not in VAGUE:
        return ("remember", m.group(1).strip())
    m = FORGET.match(t)
    # "forget it" / "forget that" / "never mind, forget about it" mean "cancel"
    if m and len(_words(m.group(1))) >= 2:
        return ("forget", m.group(1).strip())
    return None


def is_command(text):
    return command(text) is not None


FACT_SCHEMA = {
    "type": "object",
    "properties": {
        "fact": {"type": "string"},
        "subject": {"type": "string"},
        "topics": {"type": "array", "items": {"type": "string"}},
        "web_ok": {"type": "boolean"},
        "expires": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "reminder": {"anyOf": [{"type": "null"}, {
            "type": "object",
            "properties": {
                "date": {"type": "string"},
                "yearly": {"type": "boolean"},
                "days_before": {"type": "array", "items": {"type": "integer"}},
                "remind": {"type": "array", "items": {"type": "string"}},
                "action": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "required": ["date", "yearly", "days_before", "remind", "action"],
            "additionalProperties": False,
        }]},
    },
    "required": ["fact", "subject", "topics", "web_ok", "expires", "reminder"],
    "additionalProperties": False,
}

# One message can hold several facts ("…Kenz's birthday is September 14th and Theo's
# is March 22", or a pasted list on Telegram) — one entry each
TOLD_SCHEMA = {
    "type": "object",
    "properties": {"facts": {"type": "array", "items": FACT_SCHEMA}},
    "required": ["facts"],
    "additionalProperties": False,
}
MAX_TOLD = 25

REMINDER_RULES = (
    "reminder: only when the fact has a specific date worth acting on (birthday, anniversary, "
    "visit, appointment, deadline); otherwise null.\n"
    "- date: YYYY-MM-DD of the next occurrence (today counts).\n"
    "- yearly: true for birthdays and anniversaries.\n"
    "- days_before: when to remind, chosen by how much preparation it needs, not by how close "
    "the person is. A partner's birthday or an anniversary (a gift that may need shipping, a "
    "reservation): [14, 7, 1, 0]. Another close family member's birthday (a gift): [7, 1, 0]. "
    "A friend's birthday (a message): [0]. A visit or appointment: [1, 0]. Always include 0 "
    "(the day itself). If the person said when they want reminding, use exactly that.\n"
    "- remind: ids from {people} to remind. If the event involves a gift or surprise for a "
    "household member, NEVER include that person (Kenz's birthday → remind Gio only). Shared "
    "household events: everyone. Someone's own appointment: that person.\n"
    "- action: a few words for what to do ahead of time (\"order a gift\", \"book a table\", "
    "\"get the guest room ready\"), or null.\n"
)

FACT_RULES = (
    "Write each fact as one short, self-contained third-person sentence using names, not "
    "pronouns (\"Gio's favourite team is the Mariners\", \"The household's home city is "
    "Seattle\"). subject: 'household', or a person's id from {people} when the fact is only "
    "about them. topics: 3-8 lowercase words someone might use in a question this fact "
    "helps with (synonyms included, e.g. team → game, match, score); add \"*\" only for "
    "facts that matter to almost any question (home city / neighbourhood). web_ok: true "
    "only if it's harmless to include in a web search (city, favourite team, cuisine, store, "
    "car model) — false for health, schedules, travel, relationships, money, anything about "
    "guests. expires: YYYY-MM-DD when the fact is time-bound (a visit, a trip), else null. "
    "Never record passwords, PINs, codes, or card/account numbers.\n"
)


def _people():
    from jarvis_core import household
    return household.member_ids()


def _claude(prompt, schema, max_tokens, think=False):
    """think=False for commands someone is waiting on; the nightly learning
    lets the model think (better facts, and nobody is waiting)."""
    from jarvis_core import llm, router
    extra = router._structured(schema)
    resp = router.client.messages.create(
        model=router.ROUTER_MODEL, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
        extra_body=extra if think else llm.fast(extra))
    return json.loads(llm.text(resp))


def _insert(fact, subject, topics, web_ok, source, status, expires, plan=None):
    now = _now().isoformat()
    plan = plan or {}
    _run("INSERT INTO memories (fact, subject, topics, web_ok, source, status, evidence, created, "
         "last_seen, activated, expires, event_date, yearly, remind_days, remind_who, action) "
         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
         (fact, subject, " ".join(t.lower() for t in topics), int(bool(web_ok)), source, status,
          1, now, now, now if status == "active" else None, expires,
          plan.get("event_date"), int(bool(plan.get("yearly"))) if plan else None,
          ",".join(str(d) for d in plan.get("remind_days", [])) or None,
          ",".join(plan.get("remind_who", [])) or None, plan.get("action")))


def _subject(value):
    value = (value or "household").strip().lower()
    return value if value in _people() else "household"


def _date_or_none(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat() if value else None
    except (TypeError, ValueError):
        return None


def remember(text, speaker=None):
    """Save what the user explicitly asked Jarvis to remember (one or more facts)."""
    if is_secret(text):
        return "I'd rather not keep passwords or codes — those are safer somewhere else."
    people = _people()
    prompt = (
        f"Today is {_now().strftime('%A %B %d, %Y')}. Someone in the household"
        + (f" ({speaker})" if speaker else "")
        + f" asked their home assistant to remember this:\n\"\"\"\n{text}\n\"\"\"\n"
        "Split it into separate facts — one per person, date or piece of information (a list "
        "of five birthdays is five facts). " + FACT_RULES.format(people=", ".join(people))
        + REMINDER_RULES.format(people=", ".join(people))
        + ("If they say 'I'/'my' the fact is about " + speaker + ".\n" if speaker else
           "The speaker is unknown: if they say 'I'/'my', keep the subject 'household'.\n"))
    try:
        facts = _claude(prompt, TOLD_SCHEMA, 4000)["facts"][:MAX_TOLD]
    except Exception as e:
        print(f"[Memory] Remember error: {e}", flush=True)
        return "Sorry, I couldn't save that just now."
    saved = [s for s in (_save_told(f) for f in facts if not is_secret(f["fact"])) if s]
    if not saved:
        return "Sorry, I couldn't find anything to remember in that."
    if len(saved) == 1:
        fact, plan = saved[0]
        reply = f"Got it — I'll remember that {_inline(fact)}."
        if plan:
            reply += f" I'll remind {_names(plan['remind_who'])} {_when_list(plan['remind_days'])}."
        return reply
    listed = [_inline(fact) for fact, _ in saved]
    reply = f"Got it — I'll remember {len(saved)} things: " + "; ".join(listed[:-1]) + "; and " + listed[-1] + "."
    dated = [fact for fact, plan in saved if plan]
    if dated:
        reply += (" I'll send reminders for that date." if len(dated) == 1 else
                  f" I'll send reminders for those {len(dated)} dates.")
        reply += " Ask \"what do you remember?\" on Telegram to see when."
    return reply


def _save_told(f):
    """Store one told fact; returns (fact, reminder plan or None)."""
    fact = (f.get("fact") or "").strip()
    if not fact:
        return None
    subject = _subject(f["subject"])
    plan = _reminder_plan(f.get("reminder"), subject)
    expires = _date_or_none(f["expires"])
    if plan and not plan["yearly"]:
        expires = expires or plan["event_date"]       # one-off events retire after the day
    if plan:
        _replace_same_event(fact, subject, plan["event_date"])
    _insert(fact, subject, f["topics"], f["web_ok"], "told", "active", expires, plan)
    print(f"[Memory] Told: {fact} (reminder: {plan})", flush=True)
    return fact, plan


def _inline(fact):
    """A fact to follow "…that": "The household's team…" → "the household's
    team…", but names stay capitalised ("Kenz's birthday…")."""
    first, _, rest = fact.partition(" ")
    small = {"the", "our", "their", "a", "an", "my", "his", "her", "its", "your"}
    return (first.lower() + " " + rest if first.lower() in small else fact).rstrip(".")


def forget(what):
    """Forget the active fact that best matches `what`. Forgotten facts are
    never used again, and tonight's learning is told not to relearn them."""
    target = {_stem(w) for w in _words(what)}
    best, best_score = None, 0.0
    for f in active_facts():
        keys = {_stem(w) for w in _words(f["fact"])}
        score = len(target & keys) / max(len(target), 1)
        if score > best_score:
            best, best_score = f, score
    if not best or best_score < 0.5:
        return "I couldn't find that in my memory. Ask \"what do you remember?\" to see the list."
    _run("UPDATE memories SET status='forgotten' WHERE id=?", (best["id"],))
    return f"Done — I've forgotten that {_inline(best['fact'])}"


def forget_all():
    _run("DELETE FROM memories")
    _run("DELETE FROM memory_inbox")
    return "Done — my household memory is now empty."


def _plan_label(f):
    """' 🔔 Gio: 1 week before, the day before and on the day' for dated facts."""
    if not f.get("remind_days") or not f.get("remind_who"):
        return ""
    days = [int(d) for d in f["remind_days"].split(",")]
    return f" 🔔 {_names(f['remind_who'].split(','))}: {_when_list(days)}"


def describe(speakers=(), voice_mode=False):
    allowed = {"household"} | {s for s in speakers if s}
    # Also dates someone gets reminded of (Kenz's birthday shows for Gio)
    facts = [f for f in active_facts() if f["subject"] in allowed
             or allowed & set((f.get("remind_who") or "").split(","))]
    if not facts:
        return "I haven't learned anything about the household yet."
    if voice_mode:
        spoken = " ".join(f["fact"].rstrip(".") + "." for f in facts[-VOICE_MAX_FACTS:])
        if len(facts) <= VOICE_MAX_FACTS:
            return "Here's what I know: " + spoken
        # No question at the end, so the speaker doesn't keep listening
        return (f"Here are the latest {VOICE_MAX_FACTS} of {len(facts)} things I know: {spoken} "
                "Say \"text me that\" for the full list.")
    return "🧠 *What I know:*\n" + "\n".join(
        f"• {_label(f)}" + _plan_label(f) for f in facts) + \
        "\n\nSay \"forget …\" to remove anything."


def handle_command(text, speaker=None, speakers=(), voice_mode=False):
    kind, arg = command(text)
    if kind == "remember":
        return remember(arg, speaker)
    if kind == "forget":
        return forget(arg)
    if kind == "forget_all":
        return forget_all()
    return describe(speakers, voice_mode)


# ── Nightly learning ──────────────────────────────────────────────────────────

LEARN_SCHEMA = {
    "type": "object",
    "properties": {
        "operations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "reinforce", "update", "expire"]},
                    "id": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                    "fact": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "subject": {"type": "string"},
                    "topics": {"type": "array", "items": {"type": "string"}},
                    "web_ok": {"type": "boolean"},
                    "stated": {"type": "boolean"},
                    "expires": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": ["action", "id", "fact", "subject", "topics", "web_ok", "stated", "expires"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["operations"],
    "additionalProperties": False,
}


def _learn_prompt(inbox, known, forgotten):
    people = _people()
    lines = []
    for r in inbox:
        who = r["speaker"] or "someone (unknown)"
        lines.append(f"[{r['ts'][:16]} {r['channel']}] {who}: {r['user_text']}\n    Jarvis: {r['reply']}")
    known_lines = "\n".join(f"#{f['id']} ({f['status']}, seen {f['evidence']}x) {_label(f)}"
                            for f in known) or "(nothing yet)"
    forgotten_lines = "\n".join(f"- {f['fact']}" for f in forgotten) or "(none)"
    return (
        f"Today is {_now().strftime('%A %B %d, %Y')}. You maintain the long-term memory of a home "
        f"assistant for one household (people: {', '.join(people)}). Below are today's exchanges "
        "and what is already known. Propose memory operations.\n\n"
        "Already known:\n" + known_lines + "\n\n"
        "Forgotten on request — never add these again:\n" + forgotten_lines + "\n\n"
        "Today's exchanges:\n" + "\n".join(lines) + "\n\n"
        "Operations:\n"
        "- add: a new durable fact or preference useful for future requests — who lives here and "
        "their relationships, home city, favourite teams/stores/restaurants/cuisines, pets, cars, "
        "routines, upcoming visits or trips. stated=true only if a household member said it "
        "directly about themselves or the home; stated=false for a pattern you infer (e.g. "
        "they keep asking about the Mariners).\n"
        "- reinforce: today's exchanges show an existing fact (#id) again.\n"
        "- update: an existing fact (#id) changed — give the new full fact.\n"
        "- expire: an existing fact (#id) is no longer true.\n"
        "Skip one-off questions, general knowledge, and anything said by guests, the TV or "
        "unknown visitors that isn't clearly about this household. Don't add what agents already "
        "store (shopping items, to-dos, flights, maintenance tasks). Prefer few, high-quality "
        "operations; none is fine.\n"
        + FACT_RULES.format(people=", ".join(people))
        + "When the speaker is unknown, facts about 'me/my' go to subject 'household'.\n"
        "For reinforce/expire, fact may be null; always fill the other fields (copy them).\n"
    )


def consolidate():
    """Nightly: learn from the inbox, then empty it. Returns the facts that
    became active (for the morning brief). Safe to call any time."""
    if not ENABLED:
        return []
    cutoff = (_now() - timedelta(days=INBOX_KEEP_DAYS)).isoformat()
    _run("DELETE FROM memory_inbox WHERE ts < ?", (cutoff,))
    inbox = _rows("SELECT * FROM memory_inbox ORDER BY id")
    _expire_old()
    if not inbox:
        return []
    known = _rows("SELECT * FROM memories WHERE status IN ('active','candidate') ORDER BY id")
    forgotten = _rows("SELECT * FROM memories WHERE status='forgotten' ORDER BY id")
    ops = _claude(_learn_prompt(inbox, known, forgotten), LEARN_SCHEMA, 16000, think=True)["operations"]
    activated = apply_operations(ops, {f["id"]: f for f in known})
    _run("DELETE FROM memory_inbox WHERE id <= ?", (inbox[-1]["id"],))
    print(f"[Memory] Learned from {len(inbox)} exchanges: {len(ops)} operations, "
          f"{len(activated)} new facts in use", flush=True)
    return activated


def apply_operations(ops, known):
    now = _now().isoformat()
    activated = []
    forgotten = {f["fact"].lower() for f in _rows("SELECT fact FROM memories WHERE status='forgotten'")}
    for op in ops:
        fact = (op.get("fact") or "").strip()
        if fact and (is_secret(fact) or fact.lower() in forgotten):
            continue
        target = known.get(op.get("id"))
        action = op["action"]
        if action == "add" and fact:
            status = "active" if op["stated"] else "candidate"
            _insert(fact, _subject(op["subject"]), op["topics"], op["web_ok"], "learned", status,
                    _date_or_none(op["expires"]))
            if status == "active":
                activated.append(fact)
        elif action == "reinforce" and target:
            evidence = target["evidence"] + 1
            promote = target["status"] == "candidate" and evidence >= 2
            _run("UPDATE memories SET evidence=?, last_seen=?, status=?, activated=COALESCE(activated, ?) "
                 "WHERE id=?", (evidence, now, "active" if promote else target["status"],
                                now if promote else None, target["id"]))
            if promote:
                activated.append(target["fact"])
        elif action == "update" and target and fact:
            _run("UPDATE memories SET fact=?, topics=?, web_ok=?, expires=?, last_seen=? WHERE id=?",
                 (fact, " ".join(t.lower() for t in op["topics"]), int(bool(op["web_ok"])),
                  _date_or_none(op["expires"]), now, target["id"]))
            if target["status"] == "active":
                activated.append(fact)
        elif action == "expire" and target:
            _run("UPDATE memories SET status='expired' WHERE id=?", (target["id"],))
    return activated


def _expire_old():
    today = _now().date().isoformat()
    _run("UPDATE memories SET status='expired' WHERE status IN ('active','candidate') "
         "AND expires IS NOT NULL AND expires < ?", (today,))
    stale = (_now() - timedelta(days=CANDIDATE_KEEP_DAYS)).isoformat()
    _run("DELETE FROM memories WHERE status='candidate' AND last_seen < ?", (stale,))


# ── Reminders (birthdays, anniversaries, visits) ──────────────────────────────

def _reminder_plan(raw, subject):
    """Clean up Claude's reminder plan, or None. Gift/surprise safety: if
    Claude names nobody, everyone except the person the event is about."""
    if not raw:
        return None
    event = _date_or_none(raw.get("date"))
    if not event:
        return None
    people = _people()
    days = sorted({d for d in raw.get("days_before") or [] if isinstance(d, int) and 0 <= d <= 60} | {0},
                  reverse=True)
    who = [p for p in (raw.get("remind") or []) if p in people]
    if not who:
        who = [p for p in people if p != subject] or people
    action = (raw.get("action") or "").strip().rstrip(".") or None
    return {"event_date": event, "yearly": bool(raw.get("yearly")), "remind_days": days,
            "remind_who": who, "action": action}


def _replace_same_event(fact, subject, event_date):
    """Saying a dated fact again (e.g. with different timing) replaces it."""
    month_day = event_date[5:]
    target = {_stem(w) for w in _words(fact)}
    for f in active_facts():
        if f["subject"] != subject or not f.get("event_date") or f["event_date"][5:] != month_day:
            continue
        keys = {_stem(w) for w in _words(f["fact"])}
        if len(target & keys) / max(len(target), 1) >= 0.5:
            _run("UPDATE memories SET status='expired' WHERE id=?", (f["id"],))


def _names(ids):
    from jarvis_core import household
    names = [household.display_name(i) for i in ids]
    return " and ".join(names) if len(names) < 3 else ", ".join(names[:-1]) + " and " + names[-1]


def _before(days):
    if days == 0:
        return "on the day"
    if days == 1:
        return "the day before"
    if days % 7 == 0:
        weeks = days // 7
        return f"{weeks} week{'s' if weeks > 1 else ''} before"
    return f"{days} days before"


def _when_list(days):
    parts = [_before(d) for d in days]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _next_occurrence(event_date, yearly, today):
    d = date.fromisoformat(event_date)
    if not yearly:
        return d
    for year in (today.year, today.year + 1):
        try:
            when = d.replace(year=year)
        except ValueError:                      # Feb 29 in a non-leap year
            when = date(year, 2, 28)
        if when >= today:
            return when


def _in_days(n):
    if n == 0:
        return "today"
    if n == 1:
        return "tomorrow"
    if n % 7 == 0:
        return f"in {n // 7} week{'s' if n > 7 else ''}"
    return f"in {n} days"


def reminders_for(person, today=None):
    """Reminder lines due today for one person (for the morning brief)."""
    if not ENABLED:
        return []
    today = today or _now().date()
    lines = []
    for f in active_facts():
        if not f.get("event_date") or not f.get("remind_days"):
            continue
        if person not in (f.get("remind_who") or "").split(","):
            continue
        when = _next_occurrence(f["event_date"], f.get("yearly"), today)
        if when is None:
            continue
        n = (when - today).days
        if str(n) not in f["remind_days"].split(","):
            continue
        low = f["fact"].lower()
        icon = "🎂" if "birthday" in low else "💍" if "anniversary" in low else "📅"
        line = f"{icon} {f['fact'].rstrip('.')} — {_in_days(n)}."
        if n > 0 and f.get("action"):
            line += f" Time to {f['action']}."
        lines.append((n, line))
    return [line for _, line in sorted(lines)]


def reminder_note(person, today=None):
    """The morning-brief section, or "" when nothing is due."""
    try:
        lines = reminders_for(person, today)
    except Exception as e:
        print(f"[Memory] Reminder error: {e}", flush=True)
        return ""
    return ("\n\n🗓 *Coming up:*\n" + "\n".join(lines)) if lines else ""


def learned_since(hours=20, speakers=()):
    """Facts that became active recently (for the morning brief)."""
    since = (_now() - timedelta(hours=hours)).isoformat()
    allowed = {"household"} | {s for s in speakers if s}
    return [f for f in active_facts()
            if f["source"] == "learned" and (f["activated"] or "") >= since and f["subject"] in allowed]


if __name__ == "__main__":
    # python3 -m jarvis_core.memory          → show what's stored
    # python3 -m jarvis_core.memory learn    → run tonight's learning now
    import sys
    if sys.argv[1:] == ["learn"]:
        print("New facts:", consolidate())
    for f in _rows("SELECT * FROM memories ORDER BY status, id"):
        print(f"#{f['id']:<4} {f['status']:<10} {f['subject']:<10} web={f['web_ok']} "
              f"x{f['evidence']}  {f['fact']}  [{f['topics']}]")
    print(f"Inbox: {len(_rows('SELECT id FROM memory_inbox'))} exchanges waiting")
