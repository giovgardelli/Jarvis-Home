# Making it yours

Jarvis was built for one household — Gio and Kenz — and their names, habits
and timezone show up as examples. Here's what to change, from "settings only"
to "edit a prompt".

## 1. Settings (no code)

| What | Where |
|---|---|
| Who lives here, how to say their names, spellings speech-to-text gets wrong | `household.json` |
| Timezone (briefs, alarms, flight times) | `JARVIS_TIMEZONE` in `~/.jarvis_env` (e.g. `Europe/Rome`) |
| Microphone and speaker | `JARVIS_MIC_NAME`, `JARVIS_AUDIO_OUT` |
| Where runtime files live | `JARVIS_HOME` (default: your home folder) |
| Web-answer model | `GEMINI_MODEL` |
| Radio presets ("play some jazz") | `stations.json` — see [music.md](music.md) |
| Voice ID on/off and thresholds | `VOICE_ID_ENABLED`, `VOICE_ID_CONFIRMED`, `VOICE_ID_UNCERTAIN` — see [voice-id.md](voice-id.md) |

`household.json` example:

```json
{
  "default_speaker": "gio",
  "members": [
    {"id": "gio",  "name": "Gio",  "aliases": ["geo"]},
    {"id": "kenz", "name": "Kenz", "aliases": ["kenzie"], "keyterms": ["Makenzie"]}
  ]
}
```

- `id` is used in code and in env names (`GIO_CHAT_ID`) and voice profiles.
- `aliases` are words speech-to-text produces instead of the name — they're
  *matched* ("add milk to Geo's list" → Gio) but not boosted.
- `keyterms` are extra words to boost in speech-to-text (a full name).

## 2. Which agents need a recognized voice

Each agent in `registry.json` has:

- `"access"`: `"public"`, `"household"` or `"personal"` — who may use it by
  voice when voice ID is on ([why](privacy-and-security.md#why-gate-personal-information-by-voice)).
- `"needs_speaker": true` *(optional)* — the agent's answer depends on who's
  asking ("**my** to-do list"). With voice ID off, Jarvis asks "Is that for …?"
  unless a name or "our/shared" is in the request.

## 3. Adding your own agent

1. Copy an agent folder (Shawn Ping is the simplest) and give it a free port
   in `deploy/pi_deploy.sh` (`PORT` map).
2. Its `/jarvis` endpoint receives `{"intent", "params": {"original_message"},
   "speaker", "voice_mode"}` with the `X-Jarvis-Secret` header and returns
   `{"response_text", "voice_text"?, "status"?}`.
   - `voice_text` (optional): a short spoken version. Use
     `jarvis_core.voice.VOICE_INSTRUCTION` / `split_voice` to get it from the
     same Claude call; otherwise the router shortens long replies for you.
   - `status` (optional): `"list"` with a `tasks` array lets multi-agent
     answers be combined in code; `"not_found"` enables the "try the other list" retry.
3. Add it to `registry.json` with a one-line `routing_hint` — the router's
   prompt and its list of allowed destinations are built from the registry.
4. Add phrases to `tests/test_router_rules.py` if you add keyword shortcuts.

## 4. Names and habits inside agents

Some agents' prompts and messages mention the household directly. Find them
with:

```bash
grep -rn "Gio\|Kenz\|GIO_\|KENZ_" agents/ jarvis-pi.py
```

The main places:

| File | What's there |
|---|---|
| `agents/will_due/agent.py` | To-do prompt: owners ("Gio, Kenz or Shared") and pronoun rules ("his/her") |
| `agents/*/main.py` | `GIO_CHAT_ID` / `KENZ_CHAT_ID` → person mapping for each bot |
| `agents/jarvis/speaker.py` | Telegram chat id → person for the central bot |
| `agents/jarvis/main.py` | Morning brief: who gets one |
| `agents/drew_parture/*.py` | Alert wording ("Gio's flight has departed") — flights come from one person's calendar |
| `jarvis_core/router.py` | Keyword shortcuts (`*_TRIGGERS` lists) — add your own phrasing |
| `jarvis-pi.py` | Speech-to-text hint words (`keyterm` list) — add words your household uses |

For a household of one, set a single member in `household.json` and point
both chat-id settings at yourself, or simplify the prompts.

## 5. Other behaviours you may want to change

| Behaviour | Where |
|---|---|
| LEDs dark 10pm–7am | `QUIET_START_HOUR` / `QUIET_END_HOUR` in `led_controller.py` |
| Morning brief at 7:00, fetched by the speaker 7:05–7:45 | `agents/jarvis/pi_run.py`, `jarvis-pi.py` |
| Flight heads-up window (skips 7–9am flights on the day) | `morning_flight_check()` in `agents/drew_parture/main.py` |
| Fare-drop threshold (alerts only if savings > $5) | `MIN_SAVINGS_TO_ALERT` in `agents/miles_ahead/checker.py` |
| Model check every 14 days | `CHECK_EVERY_DAYS` in `agents/jarvis/model_watch.py` |
| Wake word sensitivity | `score > 0.6` in `jarvis-pi.py` |
