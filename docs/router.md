# The router (`jarvis_core/router.py`)

One routing brain for every way in: the voice loop and the Telegram bot both
call `route()`. If you're building a multi-agent assistant, this is the part
worth borrowing.

## The idea: cheap and certain first, AI second

```
request
  ├─ "text me that" / "tell me more"          → replay the last answer
  ├─ "1 is done" / "mark 2 and 3 done"        → tick off tasks numbered in today's brief
  ├─ timer / alarm / time                     → handled on the Pi (voice only)
  ├─ music: play / stop / louder / what's on  → the radio on the Pi (voice only)
  ├─ "what are my priorities"                 → ask several agents in parallel, merge
  ├─ explicit web search ("search for…")      → Gemini with Google Search
  ├─ unambiguous shortcut (shopping, flights) → that agent, no AI call
  └─ everything else                          → Claude Haiku picks the agent
```

- **Keyword shortcuts** are whole-word matches (`"land"` matches "when do I
  land", not "island") and deliberately few — only phrases that can't mean
  anything else. Everything ambiguous goes to the model.
- **Structured output:** Haiku must return JSON whose `agent` field is one of
  the agents in `registry.json` (plus `gemini_search` and `jarvis`), so
  routing can't produce an unparseable reply or a non-existent agent.
- **Parallel fan-out:** multi-agent questions ask agents at the same time. If
  every agent returns a structured list, they're combined in code (nothing can
  be invented); otherwise one merge call writes the answer.
- **Voice-ready replies:** in voice mode agents write a short spoken version
  in the same model call as the full answer, so there's no second
  "shorten this" call. The full answer is kept for "text me that".
- **Honest failures:** if routing fails, the reply says so — it never answers
  "done" for something that didn't happen.

## Who's asking, and are they allowed?

- **Sensitivity tiers** — each agent is `public`, `household` or `personal`
  in `registry.json`; with voice ID on, the router blocks requests a voice
  isn't recognized for. [Details](voice-id.md).
- **Unknown speaker** — with voice ID off, agents marked `needs_speaker` (the
  to-do list) take a name from the request or ask "Is that for Gio or Kenz?",
  then run the original request with the answer.
- **Numbered brief** — the morning brief numbers its tasks in code and
  remembers which list each came from, so "1 is done" (or just "1 3") is
  ticked off on the right list without a routing call. Only today's numbers
  count; a number that isn't in the brief is treated as a normal request.
- **Task hints** — "I cleaned the gutters": if that task appears in today's
  morning-brief list for one agent, Haiku is told so, and if the first agent
  can't find it, the router retries on the other.

## Using it elsewhere

```python
from jarvis_core.router import route

result = route("add oat milk to the list", speaker="gio",
               voice_mode=False, channel="telegram")
print(result["response_text"])
```

Inputs: the text, who's asking (`None` if unknown), recent `history`, the
previous full answer (`last_full`), `voice_mode`, `channel` (`"voice"` /
`"telegram"`), and optionally `voice_status` from voice ID. Output:
`response_text`, `full_response`, `force_text` (send as text), and `gate` when
a request was blocked.

The phrase table in `tests/test_router_rules.py` documents which path each
example request takes — add your phrasing there when you add shortcuts.
