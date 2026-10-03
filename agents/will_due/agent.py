import os
import re
import anthropic
from datetime import date
from sheets import (
    get_tasks, add_task, complete_tasks_by_id, delete_task_by_id,
    reopen_task_by_id, get_filtered_tasks
)

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM_PROMPT = """You are Will Due, a shared to-do list assistant for Gio and Kenz (a couple).

CRITICAL — READ FIRST:
- When adding tasks: ADD IMMEDIATELY. NEVER ask about categories, business/personal/administrative classifications, frequency, reminders, or any other fields. These do not exist.
- The ONLY fields are: task name, owner, priority. Infer all three and add.
- One confirmation sentence maximum. Nothing else.

You manage a shared task list. Each task has an owner (Gio, Kenz, or Shared) and a priority.

CONTEXT AWARENESS:
- You always know who is messaging you (told in every message)
- If the sender says "mine" or "my" → owner is the sender
- If the sender says "his" and sender is Kenz → owner is Gio
- If the sender says "her" or "hers" and sender is Gio → owner is Kenz
- If sender says "ours", "shared", "we", "us" → owner is Shared
- If sender says "[name]'s task" → use that name as owner

PRIORITY SCORING (always assign a numeric score):
- "asap", "urgent", "today", "right now" → score 1
- "this week", "soon", "by friday" → score 2
- "next couple weeks", "next few weeks", "soon-ish" → score 3
- "this month", "end of month" → score 4
- "whenever", "no rush", "eventually", "low priority" → score 5
- If no priority mentioned → score 5

COMMANDS (include at END of response, never show to user):
ADD_TASK|task name|owner|priority label|priority score|added_by
COMPLETE_TASKS|1,2,3
DELETE_TASK|id
REOPEN_TASK|task_id
TASK_NOT_FOUND|searched task name

IMPORTANT — TASK_NOT_FOUND:
- If the user asks to complete/delete/modify a task that does NOT
  appear in the task context provided, emit TASK_NOT_FOUND|<name>.
- Do NOT offer to add it, do NOT ask about frequency. Just say briefly
  that you couldn't find it and emit the command.

RETRIEVAL RULES:
- "my tasks" / "mine" → show sender's tasks only
- "his tasks" (from Kenz) / "her tasks" (from Gio) → show partner's tasks
- "shared" / "ours" → show shared tasks only
- "all" / "everything" → show all tasks merged by priority
- "mine and shared" → show sender's + shared
- Always sort by priority score
- When showing multiple owner types, label each item: [YOURS], [KENZ], [GIO], [SHARED]
- If user asks to undo, reopen, or unmark a task → use REOPEN_TASK with the task ID
- Always confirm what was reopened

COMPLETION:
- When user sends only numbers like "1 3" → complete those task IDs
- When a user says "I bought X", "I got X", "I did X", "I finished X", "I picked up X" → mark matching task done using COMPLETE_TASKS with its ID. Confirm naturally e.g. "Got it, marked Veja shoes as done."
- Match tasks flexibly: ignore case, small wording differences, plural/singular.
- Confirm what was completed

CONTEXT AND MEMORY:
- Use conversation history to resolve pronouns: "them", "it", "that one", "those"
- ONLY use history from THIS conversation. Ignore anything that seems unrelated to the current topic.
- If the user says "that's what I meant" or "yes" → confirm the most recent action, do not reference old tasks.

GENERAL RULES:
- Keep responses short and conversational
- Never show command lines in response
- Be friendly and encouraging
- Never ask clarifying questions when adding tasks

DISPLAY RULES:
- Always use today's date to calculate actual urgency
- Group tasks by days remaining, not by priority label
- 0-3 days remaining → Urgent (regardless of label)
- 4-7 days → This Week
- 8-14 days → Next Two Weeks
- No date or "whenever" → No Rush
- If a task says "end of month" or "this month" and there are fewer than 5 days left in the month, treat it as Urgent"""

from jarvis_core.voice import VOICE_INSTRUCTION, split_voice  # noqa: E402


# Queries that are asking to SEE the list (used to set status="list")
LIST_HINTS = ["my task", "my to do", "my todo", "to-do list", "to do list",
              "todo list", "all tasks", "everything", "what do i have",
              "what's on", "whats on", "show me", "list my", "pending"]


def parse_number_message(text):
    """If message is only numbers/separators, return list of ints."""
    cleaned = text.strip()
    if re.fullmatch(r"[\d\s,and]+", cleaned, flags=re.IGNORECASE):
        nums = re.findall(r"\d+", cleaned)
        return [int(n) for n in nums]
    return None


def _tasks_as_structured(tasks):
    """Convert sheet rows to a clean list of dicts for the contract."""
    out = []
    for t in tasks:
        out.append({
            "id": t.get("ID"),
            "task": t.get("Task"),
            "owner": t.get("Owner"),
            "priority": t.get("Priority"),
            "score": t.get("Priority Score"),
            "status": t.get("Status") or "Pending",
        })
    return out


def process_message(text, sender_name, sender_chat_id, history=None, voice_mode=False):
    """
    Returns a structured dict (the v1 contract):
    {
      "response_text": str,
      "status": "added"|"completed"|"reopened"|"deleted"|"list"|"not_found"|"chat",
      "tasks": [...],   # populated when status == "list"
    }
    Status is derived from the commands the code actually executed —
    never from LLM self-reporting.
    """
    if history is None:
        history = []

    # Fast path: bare numbers complete tasks by position
    numbers = parse_number_message(text)
    if numbers:
        sender_tasks = get_filtered_tasks(owners=[sender_name, "Shared"])
        actual_ids = []
        completed_names = []
        for num in numbers:
            if 1 <= num <= len(sender_tasks):
                actual_ids.append(int(sender_tasks[num - 1]["ID"]))
                completed_names.append(sender_tasks[num - 1]["Task"])
        if actual_ids:
            complete_tasks_by_id(actual_ids)
            if len(completed_names) == 1:
                msg = f"✅ Done! *{completed_names[0]}* marked as complete."
            else:
                names = "\n".join(f"• {n}" for n in completed_names)
                msg = f"✅ Marked as complete:\n{names}"
            return {"response_text": msg, "status": "completed", "tasks": []}
        return {
            "response_text": "I couldn't match those numbers to tasks. Try 'my tasks' to see your current list.",
            "status": "not_found", "tasks": []
        }

    # Build task context for Claude
    all_tasks = get_tasks()
    pending = [t for t in all_tasks if t.get("Status") != "Done"]
    done = [t for t in all_tasks if t.get("Status") == "Done"]
    pending.sort(key=lambda x: int(x.get("Priority Score", 5)))

    context_lines = []
    for t in pending:
        context_lines.append(
            f"ID:{t['ID']} | Task:{t['Task']} | Owner:{t['Owner']} | "
            f"Priority:{t['Priority']} | Score:{t['Priority Score']} | "
            f"Status:Pending"
        )
    for t in done:
        context_lines.append(
            f"ID:{t['ID']} | Task:{t['Task']} | Owner:{t['Owner']} | Status:Done"
        )
    context = "\n".join(context_lines) if context_lines else "No tasks."

    messages = list(history)
    messages.append({
        "role": "user",
        "content": f"Today is {date.today().strftime('%A %B %d, %Y')}.\nCurrent sender: {sender_name}\nTasks:\n{context}\n\nMessage: {text}"
                   + (VOICE_INSTRUCTION if voice_mode else "")
    })

    response = client.messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=messages
    )
    reply, voice_text = split_voice(response.content[0].text)

    # Parse and execute commands — status derived from what actually ran
    status = "chat"
    lines = reply.split("\n")
    for line in lines:
        line = line.strip()
        if line.startswith("ADD_TASK|"):
            parts = line.split("|")
            if len(parts) >= 6:
                add_task(parts[1], parts[2], parts[3], parts[4], parts[5])
                status = "added"
        elif line.startswith("COMPLETE_TASKS|"):
            nums = [int(n) for n in line.split("|")[1].split(",")]
            complete_tasks_by_id(nums)
            status = "completed"
        elif line.startswith("DELETE_TASK|"):
            delete_task_by_id(int(line.split("|")[1]))
            status = "deleted"
        elif line.startswith("REOPEN_TASK|"):
            reopen_task_by_id(int(line.split("|")[1]))
            status = "reopened"
        elif line.startswith("TASK_NOT_FOUND|"):
            status = "not_found"

    clean_reply = "\n".join([
        l for l in lines
        if not l.strip().startswith("ADD_TASK|")
        and not l.strip().startswith("COMPLETE_TASKS|")
        and not l.strip().startswith("DELETE_TASK|")
        and not l.strip().startswith("REOPEN_TASK|")
        and not l.strip().startswith("TASK_NOT_FOUND|")
    ]).strip()

    # If no write command ran and the query looks like a list request,
    # mark it as a list response and attach structured tasks
    structured_tasks = []
    if status == "chat":
        t = text.lower()
        if any(h in t for h in LIST_HINTS):
            status = "list"
            structured_tasks = _tasks_as_structured(pending)

    return {"response_text": clean_reply, "voice_text": voice_text, "status": status, "tasks": structured_tasks}