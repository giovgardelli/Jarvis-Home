# Privacy and security

Commercial smart speakers send your voice to someone else's servers, keep
your history in someone else's account, and can change what they do with it
at any time. Running your own assistant changes that — but only if you're
clear about which parts are actually local. This page is that honest map.

## The short version

Jarvis is **local-first for control, cloud-assisted for intelligence**: the
parts that decide *when* something is recorded, *who* is speaking, and *who
can reach your home* run on the Pi; understanding speech and writing replies
use cloud AI because a Raspberry Pi can't do them fast enough yet.

## What stays on the Pi, and what doesn't

| Stays on the Pi | Leaves the house (and to whom) |
|---|---|
| **Wake word detection** (openWakeWord) — nothing is sent until "Hey Jarvis" | **Your spoken command** → Deepgram (speech-to-text) |
| **Voice prints and voice ID** (Resemblyzer) — never uploaded | **The request text** → Anthropic (routing and replies) |
| Timers, alarms, LEDs, keyword shortcuts | **Spoken replies** → ElevenLabs (text-to-speech) |
| Conversation history and state (SQLite file) | **Web questions** → Google Gemini; **radio searches** → Radio Browser (and Anthropic for the closest genre) |
| All API keys and secrets | **Lists and calendar** → your own Google Sheets / Calendar |
| The agents themselves (localhost only) | **Telegram messages** → Telegram |

Each provider's own data-retention policy applies to what you send it. Review
them, and prefer providers and account settings that don't train on your
data or keep it longer than needed.

## Design choices that protect you

**1. No doors into your home.** The Telegram bots *poll* Telegram for new
messages instead of receiving webhooks, so you never open a port or expose a
URL. Each agent's web server listens on `127.0.0.1` only — reachable from the
Pi itself, not from your network, let alone the internet. The one exception is
optional: [AirPlay](music.md) makes the speaker visible on your home Wi-Fi
(still not the internet), so anyone on it can play music to it. Set an AirPlay
password if that matters.

**2. Agents only trust Jarvis.** Requests between Jarvis and the agents carry
a shared secret (`JARVIS_SECRET`); anything without it is refused.

**3. Bots only answer your household.** Each Telegram bot has an allow-list
(`ALLOWED_CHAT_IDS`); messages from anyone else are ignored.

**4. Recording starts at the wake word.** The microphone is always listening
*locally* for "Hey Jarvis", but audio is only sent for transcription after
it's heard, and only until you stop speaking.

**5. Biometrics stay home.** If you enable voice ID, the voice profiles are
small files on the Pi and the comparison happens on the Pi.

**6. Secrets never touch git.** Keys live in `~/.jarvis_env` and
`agents/<name>/.env` (both gitignored). `deploy/push_secrets_from_windows.sh`
copies them to the Pi over ssh so they never pass through GitHub.

**7. Failures fail safe.** If voice ID can't analyze a voice, Jarvis asks you
to repeat rather than assuming it's you. If routing fails, Jarvis says so
rather than pretending the request went through.

## Why gate personal information by voice

A kitchen speaker is a shared device in a shared room. Guests, a visiting
contractor, kids' friends, or a TV ad saying "Hey Jarvis" can all reach it.
Phones solve this with a lock screen; a speaker has nothing like that unless
it can tell *who* is talking.

That's what **voice ID** is for. Jarvis sorts every request into a
**sensitivity tier** and only answers the sensitive ones for a recognized
voice:

| Tier | Examples | Who can use it by voice |
|---|---|---|
| **Public** | Timers, alarms, web questions, small talk | Anyone — guests welcome |
| **Household** | Shopping list, home maintenance | A recognized voice, or "probably someone who lives here" |
| **Personal** | To-do list, calendar and flights, fare tracking, "text me that", the morning brief | A confirmed voice only |

Each agent's tier is one line in `registry.json` (`"access"`), so you decide
what counts as personal in your home.

**Be realistic about what this protects.** Voice ID on a hobbyist microphone
is a **deterrent against casual access, not a security boundary**. A
recording of your voice can fool it, and a poor far-field mic will
sometimes fail to recognize *you* (the project's own household turned voice
ID off for that reason until they upgrade the mic — see
[voice-id.md](voice-id.md)). The tiers keep the cost of mistakes low: a
misfire only matters for personal requests, and public ones always work.

Telegram doesn't need voice ID: identity comes from the Telegram account,
already limited by the allow-list.

## Going more local

Everything that goes to the cloud has a local alternative. The trade-off is
speed and quality on a Pi; a small mini-PC or a Pi 5 with an accelerator
narrows it.

| Cloud piece today | Local alternative | Trade-off |
|---|---|---|
| Deepgram speech-to-text | [whisper.cpp](https://github.com/ggerganov/whisper.cpp) or faster-whisper | Slower on a Pi; tiny/base models are fast but less accurate |
| ElevenLabs text-to-speech | [Piper](https://github.com/rhasspy/piper) | Very fast and fully offline; voices are less natural |
| Anthropic routing and replies | A local model via [Ollama](https://ollama.com) | Needs more hardware; smaller models route less reliably |
| Gemini web answers | — | Inherently online; you can leave web questions out |
| Google Sheets / Calendar | A local SQLite store or CalDAV server | You lose the easy phone/desktop editing |

The router already decides where many common requests go with keyword
shortcuts instead of an AI call (shopping list, flights, timers, "what are my
priorities", explicit web searches), so a local routing model would only see
what's left.

## A checklist for your own install

- [ ] `~/.jarvis_env` and every `.env` are `chmod 600` and not in git
- [ ] `JARVIS_SECRET` is long and random
- [ ] `ALLOWED_CHAT_IDS` is set for every bot
- [ ] No port forwarding to the Pi on your router; agents bind to `127.0.0.1`
- [ ] Decide which agents are `personal` in `registry.json`
- [ ] If others use the room, consider enabling voice ID (with a decent mic)
- [ ] Review the retention settings of each AI provider you use
- [ ] Keep the Pi updated (`sudo apt update && sudo apt full-upgrade`)
