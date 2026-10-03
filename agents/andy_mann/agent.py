import os
from jarvis_core.config import TIMEZONE
import anthropic
from datetime import date, datetime
import pytz
from sheets import _cache, _load_cache, update_last_completed

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

def _get_tab(name):
    if _cache.get(name) is None:
        _load_cache(name)
    return _cache.get(name) or []
    
PT = pytz.timezone(TIMEZONE)

SYSTEM_PROMPT = """You are Andy Mann, a home maintenance assistant for Gio and Kenz.

CRITICAL — READ FIRST:
- NEVER ask follow-up questions when completing or adding tasks. No questions about frequency, reminders, categories, or systems.
- If a user says they completed a task that exists in the task library → emit COMPLETE_TASK command and confirm in one sentence.
- If the task does NOT exist in the task library → emit TASK_NOT_FOUND command and say briefly you couldn't find it. Do NOT offer to add it, do NOT ask about recurrence.

You manage home maintenance for their house: the task library (recurring
maintenance), contractors directory, warranties, and property details.

SCOPE:
- You ONLY handle home maintenance, property, contractors, warranties.
- If asked about personal tasks, shopping, flights, or anything not
  home-related → emit OUT_OF_SCOPE command and say briefly it's not
  something you track.

COMMANDS (include at END of response, never show to user):
COMPLETE_TASK|exact task name from library
TASK_NOT_FOUND|searched task name
OUT_OF_SCOPE|topic

COMPLETION:
- When a user says "I did X", "mark X as done", "X is done", "I fixed X",
  "I replaced X" → find the matching task in the library (match flexibly:
  ignore case, small wording differences) and emit COMPLETE_TASK with the
  EXACT task name as it appears in the library.
- Confirm naturally: "Great — marked [task] as completed today."

ANSWERING QUESTIONS:
- Use the data provided in context (task library, contractors, warranties,
  property profile) to answer questions about due dates, last completed,
  who to call, warranty coverage.
- Keep responses short and conversational.
- Always use today's date to calculate urgency. Tasks due within 3 days
  are urgent.
- Never invent tasks, contractors, or warranties not in the data.

GENERAL RULES:
- Keep responses short — this is a chat interface
- Never show command lines in your response
- Be friendly and practical"""

# Queries that ask to SEE maintenance tasks (sets status="list")
LIST_HINTS = ["maintenance task", "what's due", "whats due", "due this week",
              "due this month", "home tasks", "maintenance list", "what do i",
              "upcoming maintenance", "overdue"]


def _tasks_as_structured(tasks):
    """Convert task library rows into the contract's task shape."""
    out = []
    for t in tasks:
        out.append({
            "id": t.get("Task"),
            "task": t.get("Task"),
            "owner": "Home",
            "priority": f"{t.get('Frequency Value')} {t.get('Frequency Unit')}",
            "score": 3,
            "status": "Pending",
            "due": str(t.get("Next Due") or ""),
            "last_completed": str(t.get("Last Completed") or ""),
        })
    return out


def _build_context():
    """Assemble all four tabs into a context block for Claude."""
    today = datetime.now(PT)
    lines = [f"Today is {today.strftime('%A %B %-d, %Y')}.", ""]

    lines.append("=== TASK LIBRARY ===")
    tasks = _get_tab("task_library")
    for t in tasks:
        freq = f"{t.get('Frequency Value')} {t.get('Frequency Unit')}"
        lines.append(
            f"Task:{t.get('Task')} | System:{t.get('System')} | Frequency:{freq} | "
            f"Last Completed:{t.get('Last Completed')} | Next Due:{t.get('Next Due')}"
        )

    lines.append("")
    lines.append("=== CONTRACTORS ===")
    for c in _get_tab("contractors"):
        lines.append(
            f"{c.get('Trade')}: {c.get('Name')} | {c.get('Phone')} | "
            f"Rank:{c.get('Rank')} | Notes:{c.get('Notes')}"
        )

    lines.append("")
    lines.append("=== WARRANTIES ===")
    for w in _get_tab("warranties"):
        lines.append(
            f"{w.get('Item')} ({w.get('Category')}) | Model:{w.get('Model')} | "
            f"Vendor:{w.get('Vendor')} | Expires:{w.get('Expiry Date')} | Notes:{w.get('Notes')}"
        )

    lines.append("")
    lines.append("=== PROPERTY PROFILE ===")
    for p in _get_tab("property_profile"):
        lines.append(f"{p.get('Field')}: {p.get('Value')}")

    return "\n".join(lines)


from jarvis_core.voice import VOICE_INSTRUCTION, split_voice  # noqa: E402


def chat(text, history=None, speaker="gio", voice_mode=False):
    """
    Returns the v1 structured contract:
    {
      "response_text": str,
      "status": "completed" | "not_found" | "out_of_scope" | "list" | "chat",
      "tasks": [...],   # populated when status == "list"
    }
    Status is derived from the commands the code actually executed.
    """
    if history is None:
        history = []

    context = _build_context()

    messages = list(history)
    messages.append({
        "role": "user",
        "content": f"{context}\n\nMessage from {speaker}: {text}"
                   + (VOICE_INSTRUCTION if voice_mode else "")
    })

    response = client.messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=messages
    )
    reply, voice_text = split_voice(response.content[0].text)

    # Parse and execute commands — derive status from what actually ran
    status = "chat"
    lines = reply.split("\n")
    for line in lines:
        line = line.strip()
        if line.startswith("COMPLETE_TASK|"):
            task_name = line.split("|")[1].strip()
            ok = update_last_completed(task_name)
            status = "completed" if ok else "not_found"
        elif line.startswith("TASK_NOT_FOUND|"):
            status = "not_found"
        elif line.startswith("OUT_OF_SCOPE|"):
            status = "out_of_scope"

    clean_reply = "\n".join([
        l for l in lines
        if not l.strip().startswith("COMPLETE_TASK|")
        and not l.strip().startswith("TASK_NOT_FOUND|")
        and not l.strip().startswith("OUT_OF_SCOPE|")
    ]).strip()

    # List detection for read queries
    structured_tasks = []
    if status == "chat":
        t = text.lower()
        if any(h in t for h in LIST_HINTS):
            status = "list"
            structured_tasks = _tasks_as_structured(_get_tab("task_library"))

    return {"response_text": clean_reply, "voice_text": voice_text, "status": status, "tasks": structured_tasks}