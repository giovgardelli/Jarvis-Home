# Jarvis — central service (port 5006)

The Telegram front door to every agent, and the household's morning brief.
Runs as the user service `jarvis-jarvis` (`pi_run.py`). It:

- answers **@JarvisHomeBot** on Telegram (long-polling) through the shared
  router in `jarvis_core/router.py` (`agent.py` is the thin adapter);
- builds the **7am morning brief**: asks the agents for this week's tasks,
  maintenance and flights (in parallel), sends each person a Telegram
  summary, and keeps a short spoken version for the Pi to fetch;
- runs the **Google auth check** (6:30am) and the **model check**
  (6:40am, every 14 days — `model_watch.py`).

## Endpoints (localhost only)

| Route | Used by |
|---|---|
| `POST /get-brief-text` | Voice loop, 7:05–7:45am, to fetch today's brief |
| `POST /send-text` | Voice loop, for "text me that" |
| `POST /model-check` | Manual: run the model check now, result to Gio on Telegram |
| `GET/POST /morning-briefing` | Manual: rebuild and resend the brief |
| `GET/POST /auth-check` | Manual: run the Google auth check |
| `GET /health` | Health check |

`/get-brief-text`, `/send-text` and `/model-check` require the `X-Jarvis-Secret` header.

## Files

| File | |
|---|---|
| `main.py` | Flask app, Telegram handling, morning brief |
| `agent.py` | Telegram → shared router; morning-brief data gathering |
| `model_watch.py` | Model deprecation/upgrade check |
| `speaker.py` | Telegram chat id → gio / kenz |
| `stt.py`, `tts.py`, `acks/` | Optional Telegram voice notes (`STT_ENABLED`, `TTS_ENABLED`) |
| `pi_run.py` | Entrypoint: poller + scheduler + Flask |

## Setup

`cp .env.example .env` (your main bot's token and each person's chat id);
shared keys (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `JARVIS_SECRET`,
`GOOGLE_*`) come from `~/.jarvis_env`. Who gets a brief is set in `main.py`
and `speaker.py` (chat id → person) — see [customizing](../../docs/customizing.md).

To add an agent, add it to `registry.json` at the repo root — the router
builds its prompt and allowed agents from it.
