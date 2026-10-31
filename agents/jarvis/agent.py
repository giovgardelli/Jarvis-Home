"""
agent.py — the central Jarvis service's link to the shared router.

Routing itself lives in jarvis_core/router.py (shared with the voice loop);
this module adapts it for Telegram and gathers the morning-brief data.
"""
from concurrent.futures import ThreadPoolExecutor

from jarvis_core import router
from jarvis_core.registry import agent as registry_agent


def jarvis_respond(user_input, speaker, chat_id, voice_mode=False, history=None,
                   last_full_response=None, task_sources=None):
    """Answer a Telegram message (voice_mode=True for Telegram voice notes)."""
    return router.route(
        user_input, speaker,
        history=history,
        last_full=last_full_response,
        voice_mode=voice_mode,
        channel="telegram",
        task_sources=task_sources,
        chat_id=chat_id,
    )


MORNING_QUERIES = [
    # (result key, agent, speaker, question)
    ("will_due_gio", "will_due", "gio", "What are my tasks due this week?"),
    ("will_due_kenz", "will_due", "kenz", "What are my tasks due this week?"),
    ("andy_mann", "andy_mann", "gio", "What home maintenance tasks are due this week?"),
    ("drew_parture", "drew_parture", "gio", "Do we have any flights today or this week?"),
]


def get_morning_data() -> dict:
    """Ask the agents for this week's tasks/maintenance/flights (in parallel)."""
    def ask(query):
        key, agent_id, speaker, question = query
        r = router.dispatch(agent_id, question, {"original_message": question}, speaker,
                            voice_mode=False, chat_id=0)
        return key, r["response_text"]

    # Skip agents this install doesn't run (not in registry.json)
    queries = [q for q in MORNING_QUERIES if registry_agent(q[1])]
    with ThreadPoolExecutor(max_workers=max(len(queries), 1)) as pool:
        results = dict(pool.map(ask, queries))

    def context(name, tasks_key):
        return (
            f"{name}'s tasks this week:\n" + results.get(tasks_key, "No tasks found") + "\n\n"
            "Home maintenance:\n" + results.get("andy_mann", "No tasks found") + "\n\n"
            "Flights:\n" + results.get("drew_parture", "No flights found")
        )

    raw_sources = {
        "gio": {"will_due": results.get("will_due_gio", ""), "andy_mann": results.get("andy_mann", "")},
        "kenz": {"will_due": results.get("will_due_kenz", ""), "andy_mann": results.get("andy_mann", "")},
    }
    return {"gio": context("Gio", "will_due_gio"), "kenz": context("Kenz", "will_due_kenz"), "raw_sources": raw_sources}


# ── The written brief: tasks numbered in code, so "1 is done" works ───────────
# Claude picks and orders this week's tasks (structured output); the numbers,
# and the record of which list each came from, are added here. The router's
# "1 is done" reads that record (state.get_brief_items).

MAX_BRIEF_TASKS = 10   # a safety limit only; the prompt asks for the top few

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "greeting": {"type": "string"},
        "tasks": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "list": {"type": "string", "enum": ["will_due", "andy_mann"]},
                "when": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "required": ["task", "list", "when"],
            "additionalProperties": False,
        }},
        "flights": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "closing": {"type": "string"},
    },
    "required": ["greeting", "tasks", "flights", "closing"],
    "additionalProperties": False,
}


def brief_prompt(name, data):
    return (
        "You are Jarvis. Write " + name + "'s morning briefing as JSON.\n\n"
        "Data from agents:\n" + data + "\n\n"
        "- greeting: one warm good-morning sentence.\n"
        # Which tasks, and how many, is the brief's original rule — only the
        # numbering is new
        "- tasks: the top 2-3 tasks due this week, plus key home maintenance — be concise, "
        "this is a morning summary, not a report. Most urgent first. task: its name exactly as "
        "written in the data. list: \"will_due\" for to-do items, \"andy_mann\" for home "
        "maintenance. when: a short note such as \"due Friday\" or \"urgent\", or null.\n"
        "- flights: one sentence about flights today or this week, or null if none.\n"
        "- closing: one short encouraging sentence.\n"
        "Only use what is in the data — never invent tasks or flights."
    )


def render_brief(brief):
    """(Telegram text, numbered items) from the structured brief."""
    tasks = [t for t in brief.get("tasks", []) if (t.get("task") or "").strip()][:MAX_BRIEF_TASKS]
    items = [{"n": n, "task": t["task"].strip(), "list": t["list"], "done": False}
             for n, t in enumerate(tasks, start=1)]
    lines = [brief.get("greeting", "Good morning!").strip(), ""]
    if items:
        lines.append("📋 *This week*")
        for item, t in zip(items, tasks):
            home = "🏠 " if item["list"] == "andy_mann" else ""
            when = (t.get("when") or "").strip()
            lines.append(f"#{item['n']} {home}{item['task']}" + (f" — {when}" if when else ""))
        lines.append("_Reply \"1 done\" (or \"mark 2 and 3 done\") to tick them off._")
    else:
        lines.append("📋 Nothing due this week.")
    if brief.get("flights"):
        lines += ["", "✈️ " + brief["flights"].strip()]
    if brief.get("closing"):
        lines += ["", brief["closing"].strip()]
    return "\n".join(lines), items
