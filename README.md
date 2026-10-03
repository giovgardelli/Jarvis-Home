# Jarvis Home

A home assistant you run yourself on a Raspberry Pi: a voice speaker ("Hey
Jarvis") plus Telegram bots, backed by small single-purpose agents for a
shared shopping list, to-do list, home maintenance, flight alerts and
fare-drop tracking.

It was built for one household (Gio and Kenz — you'll see their names in the
examples) and is shared so you can **clone it and make it yours**. Take the
whole thing, or just the piece you want.

```
"Hey Jarvis" ─► voice loop (jarvis-pi.py) ─┐
                                           ├─► router (jarvis_core/router.py) ─► agents on localhost
Telegram ─────► central service ───────────┘                │
                                                            └─► web answers / direct replies
```

It's built to feel quick: requests are routed while a short "on it" chime
plays, several agents are asked in parallel, and spoken replies start playing
while they're still being generated.

## Pick your piece

Every piece has its own guide. Most agents also work **on their own as a
Telegram bot**, without the voice assistant.

| Piece | What it does | Needs | Guide |
|---|---|---|---|
| **Voice assistant** | Wake word, speech-to-text, routing, streamed speech, LEDs, timers & alarms, morning brief | Pi + USB mic/speaker, Deepgram, ElevenLabs, Anthropic | [docs/voice-assistant.md](docs/voice-assistant.md) |
| **Router** | Sends each request to the right agent: instant keyword shortcuts, then Claude with structured output; parallel multi-agent answers | Anthropic (+ Gemini for web answers) | [docs/router.md](docs/router.md) |
| **Shawn Ping** | Shared shopping list | Telegram, Google Sheets, Anthropic | [agents/shawn_ping](agents/shawn_ping/README.md) |
| **Will Due** | Shared to-do list with owners and priorities | Telegram, Google Sheets, Anthropic | [agents/will_due](agents/will_due/README.md) |
| **Andy Mann** | Home maintenance schedule, warranties, contractors, weather & air-quality alerts | Telegram, Google Sheets, Anthropic (+ AirNow) | [agents/andy_mann](agents/andy_mann/README.md) |
| **Drew Parture** | Flights from your Google Calendar: morning heads-up, delay, departure and landing alerts | Telegram, Google Calendar, AviationStack, Anthropic | [agents/drew_parture](agents/drew_parture/README.md) |
| **Miles Ahead** | Watches your booked Southwest fares and alerts you when the price drops | Telegram, Google Calendar + Sheets, SerpApi | [agents/miles_ahead](agents/miles_ahead/README.md) |
| **Jarvis (central)** | Telegram front door to all agents, 7am morning brief, model-update check | Telegram, Anthropic, Gemini | [agents/jarvis](agents/jarvis/README.md) |
| **Voice ID** *(optional)* | Recognizes who's speaking and gates personal information | Voice assistant + a decent mic | [docs/voice-id.md](docs/voice-id.md) |

## Privacy and security, in short

Jarvis is **local-first for control** and uses cloud AI where a Pi can't keep
up. Specifically:

- **Nothing in your home is reachable from the internet.** Telegram bots poll
  for messages (no webhooks, no open ports); agents listen on localhost only
  and require a shared secret.
- **Audio only leaves the house after the wake word.** "Hey Jarvis" is
  detected on the Pi; nothing is sent before it.
- **Voice prints never leave the device.** Voice ID runs entirely on the Pi.
- **Your keys and data stay yours.** Secrets live in files on the Pi (never
  in git); lists live in your own Google Sheets; history in a local SQLite file.
- **Personal answers can be gated by voice.** A speaker in a shared room can be
  reached by guests; Voice ID lets anyone set a timer but keeps your to-do list
  and travel plans to recognized voices.

What *does* go to the cloud — your spoken command, the request text, spoken
replies, web questions — and how to keep more of it local is covered in
**[docs/privacy-and-security.md](docs/privacy-and-security.md)**.

## Getting started

- **Whole thing on a Pi:** [docs/setup.md](docs/setup.md) — accounts, Google
  and Telegram setup, secrets, services, first run.
- **Just one agent:** open its guide from the table above.
- **Make it yours** (names, timezone, new agents, which agents need a
  recognized voice): [docs/customizing.md](docs/customizing.md).
- **Hardware and running costs:** [docs/hardware-and-costs.md](docs/hardware-and-costs.md).

## Repository layout

| Path | What it is |
|---|---|
| `jarvis-pi.py` | Voice loop |
| `jarvis_core/` | Shared code: router, agent registry, household, Telegram poller, Google auth, state, config |
| `registry.json` | The agents: endpoints, routing hints, sensitivity tier |
| `household.json` | Who lives here: names and spellings speech-to-text might produce |
| `agents/<name>/` | One agent per folder, each a small Flask app + Telegram bot (`pi_run.py`) |
| `timer_manager.py`, `led_controller.py`, `voice_id.py`, `enroll.py` | Voice-loop helpers |
| `deploy/` | Pi setup script, requirements, settings template, voice service file |
| `tests/` | Test suite — standard library only: `python3 -m unittest discover -s tests -b` |

## Status

A personal project shared as-is. It runs daily in one home; issues and pull
requests are welcome, but support is best-effort. Licensed under the
Apache License 2.0 — see [LICENSE](LICENSE).
