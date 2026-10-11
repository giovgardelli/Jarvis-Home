# Notes for Claude (and contributors)

See README.md for the architecture and docs/ for guides.

- **Run the tests before every commit**: `python3 -m unittest discover -s tests -b`.
  Add or update tests with each change.
- The suite is standard-library only; `tests/helpers.py` stubs third-party
  libraries that aren't installed. Keep it that way.
- Routing changes go in `jarvis_core/router.py` — it serves both voice and
  Telegram. Keep the phrase table in `tests/test_router_rules.py` current.
- Who lives here comes from `household.json`; each agent's voice sensitivity
  tier (`access`) and `needs_speaker` from `registry.json`.
- Voice ID is optional and off by default (`VOICE_ID_ENABLED`); keep its code.
- Changing the voice-ID audio path (`prepare_audio` / `voice_id.py` /
  `enroll.py`) requires users to re-enroll — call it out in the change.
- Claude calls go through `jarvis_core/llm.py`: model from `CLAUDE_MODEL`,
  `llm.fast()` (thinking off) on anything a person waits for, and `llm.text()`
  to read replies (never `content[0]` — Haiku 5.5 can return thinking blocks
  and refusals). Don't write model IDs in `.py` files other than `llm.py`:
  the model check scans the code for them.
- Agents start via `agents/<name>/pi_run.py`, which puts the repo root on
  `sys.path`; shared code is imported as `jarvis_core.*`.
- Runtime paths come from `jarvis_core/config.py` (`JARVIS_HOME`, default ~).
- Docs live in `README.md`, `docs/` and each `agents/<name>/README.md` — keep
  them in step with behaviour changes.
