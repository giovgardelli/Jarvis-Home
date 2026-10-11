import os
import anthropic
from sheets import get_shopping_list, add_item, remove_items, remove_item_by_name, get_similar_items

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """You are Shawn Ping, a friendly shopping list assistant.

You help users manage a shared shopping list. You can:
1. Add items to the list
2. Remove items from the list
3. Show the full list
4. Detect and resolve duplicates

COMMANDS (include at end of response, never show to user):
ADD_ITEM|item name
REMOVE_NUMBERS|1,2,3
REMOVE_ITEM|exact item name

RULES:
- Keep responses very short and casual — this is a quick texting interaction
- When adding multiple items: emit one ADD_ITEM command per item on separate lines, then confirm all additions in a single natural sentence e.g. "Added milk, eggs, and bread to the list."
- When adding one item: confirm clearly
- When showing list: read out all items naturally, like "You have milk, eggs, bread, and coffee on your list." Never say "Shopping list:" as a header.
- After showing the full list, just read the items naturally. Do not offer to text it.
- When user sends only numbers (e.g. "1 3" or "1,3"): remove those items
- Never show command lines in your response
- If asked to remove something not on the list, say so kindly
- You handle a SHARED list between a couple — be friendly to both"""

# Queries that mean "show me the list" (used to set status="list")
LIST_HINTS = ["list", "shopping", "what's on", "whats on", "what do we have",
              "what do i have", "show me", "groceries"]


from jarvis_core.voice import VOICE_INSTRUCTION, split_voice  # noqa: E402
from jarvis_core import llm


def format_list(items):
    if not items:
        return "🛒 Your shopping list is empty!"
    lines = ["🛒 *Shopping List:*\n"]
    for item in items:
        lines.append(f"{item['#']}. {item['Item']}")
    return "\n".join(lines)


def parse_number_message(text):
    """Check if message is just numbers — e.g. '1 3' or '1,2,3'."""
    cleaned = text.replace(",", " ").strip()
    if not cleaned:
        return None
    parts = cleaned.split()
    try:
        numbers = [int(p) for p in parts if p.strip()]
        if numbers and len(numbers) == len(parts):
            return numbers
        return None
    except ValueError:
        return None


def _items_as_structured(items):
    """Convert shopping list rows into the contract's item shape."""
    return [{"position": i.get("#"), "item": i.get("Item")} for i in items]


def _strip_commands(lines):
    return "\n".join([
        l for l in lines
        if not l.strip().startswith("ADD_ITEM|")
        and not l.strip().startswith("REMOVE_NUMBERS|")
        and not l.strip().startswith("REMOVE_ITEM|")
    ]).strip()


def process_message(text, sender_name, voice_mode=False, chat_id=None, history=None):
    """
    Returns the v1 structured contract:
    {
      "response_text": str,
      "status": "added" | "removed" | "list" | "chat",
      "items": [ {"position": int, "item": str} ]   # only for status="list"
    }
    Status is derived from the commands the code actually executed.
    """
    items = get_shopping_list()

    # Fast path: bare numbers remove by position
    numbers = parse_number_message(text)
    if numbers and all(1 <= n <= len(items) for n in numbers):
        removed_items = [i["Item"] for i in items if i["#"] in numbers]
        count = remove_items(numbers)
        if count == 1:
            msg = f"✅ Removed *{removed_items[0]}*!"
        else:
            names = ", ".join(f"*{n}*" for n in removed_items)
            msg = f"✅ Removed {names}!"
        return {"response_text": msg, "status": "removed", "items": []}

    list_context = format_list(items)

    response = client.messages.create(
        model=os.environ.get("ANTHROPIC_MODEL") or llm.MODEL,
        max_tokens=400,
        extra_body=llm.fast(),
        system=SYSTEM_PROMPT,
        messages=[
            *(history if history else []),
            {
                "role": "user",
                "content": f"Current shopping list:\n{list_context}\n\nMessage from {sender_name}: {text}"
                           + (VOICE_INSTRUCTION if voice_mode else "")
            }
        ]
    )

    reply, voice_text = split_voice(llm.text(response))
    lines = reply.split("\n")

    # Parse and execute commands — derive status from what actually ran
    status = "chat"
    duplicate_warning = ""
    for line in lines:
        line = line.strip()
        if line.startswith("ADD_ITEM|"):
            new_item = line.split("|")[1]
            similar = get_similar_items(new_item, items)
            add_item(new_item, sender_name)
            status = "added"

            # 7-item threshold (unchanged behavior)
            current_list = get_shopping_list()
            if len(current_list) == 7:
                item_names = [r["Item"] for r in current_list]
                if voice_mode:
                    offer = " That's 7 items now. Want me to text you the list?"
                    return {
                        "response_text": _strip_commands(lines) + offer,
                        "voice_text": (voice_text + offer) if voice_text else None,
                        "status": "added",
                        "items": []
                    }
                else:
                    if chat_id:
                        from main import send_telegram
                        formatted = "\n".join(f"{i+1}. {itm}" for i, itm in enumerate(item_names))
                        send_telegram(chat_id, f"📋 Your shopping list just hit 7 items:\n\n{formatted}")

            # Duplicate warning (deduped — was accidentally doubled in old code)
            if similar:
                similar_names = ", ".join(f"*{s['Item']}*" for s in similar)
                duplicate_warning = f"\n\n⚠️ Heads up — similar item(s) already on the list: {similar_names}. Same thing?"

        elif line.startswith("REMOVE_NUMBERS|"):
            nums = [int(n) for n in line.split("|")[1].split(",")]
            remove_items(nums)
            status = "removed"
        elif line.startswith("REMOVE_ITEM|"):
            remove_item_by_name(line.split("|")[1])
            status = "removed"

    result = _strip_commands(lines)
    if duplicate_warning:
        result += duplicate_warning

    # List detection + structured items
    structured_items = []
    if status == "chat":
        t = text.lower()
        if any(h in t for h in LIST_HINTS):
            status = "list"
            structured_items = _items_as_structured(get_shopping_list())
            # Voice-only offer to text when list is long (unchanged behavior)
            if voice_mode and len(structured_items) >= 5:
                result += " Want me to text it to you?"
                if voice_text:
                    voice_text += " Want me to text it to you?"

    return {"response_text": result, "voice_text": voice_text, "status": status, "items": structured_items}