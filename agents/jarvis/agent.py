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
