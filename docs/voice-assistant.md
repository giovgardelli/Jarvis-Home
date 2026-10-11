# The voice assistant (`jarvis-pi.py`)

A "Hey Jarvis" speaker on a Raspberry Pi. It handles timers, alarms, the time
and web questions on its own, and everything else through the
[router](router.md) and the agents.

## What happens when you talk to it

1. **Wake word** — [openWakeWord](https://github.com/dscripka/openWakeWord)
   listens on the Pi for "Hey Jarvis". Nothing leaves the Pi before this.
2. **Recording** — until you stop talking (~0.8 s of silence), max 6 s.
3. **In parallel:** speech-to-text (Deepgram) and, if enabled,
   [voice ID](voice-id.md) on the Pi.
4. **In parallel again:** the router works on your request **while** a short
   "on it" chime plays.
5. **Reply** — streamed from ElevenLabs straight to the speaker: it starts
   playing while the rest is still being generated.
6. **Follow-up** — only when Jarvis asks something it needs answered ("want to
   hear the rest?", "is that for Gio or Kenz?", "want me to text it to you?")
   does the mic reopen for 4 s without the wake word. After statements and
   courtesy questions ("anything else?") it goes straight back to waiting for
   "Hey Jarvis", so it doesn't pick up conversations in the room
   (`expects_reply` in `jarvis_core/voice.py`).

Also built in:

- **Timers and alarms** — "set a pasta timer for 10 minutes", "wake me up at
  7:30", "cancel my alarm". They survive restarts; one that went off during a
  restart is announced late instead of lost.
- **Morning brief** — the central service builds it at 7:00; the speaker
  fetches it and plays it when you say "good morning" or "what's my brief".
- **"Text me that"** — sends the full version of the last answer to Telegram.
- **"Tell me more"** — reads more of a long answer.
- **LEDs** (BlinkStick) — listening, heard you, who's speaking, errors; dark
  10pm–7am. Jarvis keeps answering at night.
- **Resilience** — a failed request plays an offline "sorry" clip and goes
  back to listening; a disconnected mic is found and reopened.

## Run just the voice assistant

The voice loop needs the router, which needs at least Anthropic. Agents are
optional: without them, timers, the time, web questions and small talk still
work, and requests for missing agents get a "couldn't reach…" reply.
Remove agents you don't run from `registry.json`.

Setup: [setup.md](setup.md) steps 2, 5 and 8. Keys: `ANTHROPIC_API_KEY`,
`DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, `GEMINI_API_KEY` (web answers),
`JARVIS_SECRET`.

## Tuning

| Want | Change |
|---|---|
| Fewer false wakes (TV) | Raise `score > 0.6` in `jarvis-pi.py` |
| It cuts you off mid-sentence | Raise `SILENCE_DURATION` in `record_command()` |
| Quiet mic / far away | The 3x boost in `prepare_audio()` (re-enroll voice ID if you change it) |
| Better recognition of your words | Add to the `keyterm` list in `transcribe()`; names come from `household.json` |
| A different voice | `ELEVENLABS_VOICE_ID` |
| Different chimes | Replace the mp3s in `~/acks/` |

The loop saves one short audio sample per near-miss wake word to
`~/wakeword_samples/` (newest 500 kept), handy if you ever train a custom
wake word.
