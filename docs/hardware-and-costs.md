# Hardware and running costs

## What this project runs on

| Part | Used here | Notes |
|---|---|---|
| Computer | Raspberry Pi | A Pi 4 (4 GB) or Pi 5 is comfortable; everything heavy happens in the cloud |
| Microphone | EMEET USB conference microphone | Far-field; found by name (`JARVIS_MIC_NAME`) |
| Speaker | USB speaker | Set with `JARVIS_AUDIO_OUT` (an ALSA device such as `hw:2,0`) |
| Status light *(optional)* | BlinkStick (8 LEDs) | Shows listening / heard you / who's speaking / errors; dark 10pm–7am |
| Storage | microSD | Logs go to the system journal (rotated); wake-word samples are capped |

The code finds devices by name and settings, so other hardware works — set
`JARVIS_MIC_NAME` to part of your mic's name (`arecord -l`) and
`JARVIS_AUDIO_OUT` to your speaker (`aplay -l`).

## If we were buying again

- **A better microphone first.** A far-field conference mic works for speech
  recognition at a distance (with a software boost), but not well enough to
  tell people apart by voice. For voice ID, use a mic array with beamforming
  and noise suppression, placed close to where people talk. See
  [voice-id.md](voice-id.md#lessons-learned).
- **Separate speaker and mic** if you can: playback and echo are easier to
  manage, and you can pick a speaker that actually sounds good.
- **A Pi 5 or a small mini-PC** if you want to move speech-to-text or speech
  locally ([going more local](privacy-and-security.md#going-more-local)).

## Running costs

Jarvis has no subscription of its own — you pay the AI and data providers
you choose, directly, for what you use. For a household's level of use, the
bill is usually modest, and several providers offer free tiers that cover
light use. Prices change, so check each provider's pricing page.

What drives cost, roughly in order:

| Provider | What you pay for | Ways to spend less |
|---|---|---|
| ElevenLabs | Characters of speech generated | Short replies (agents already write a ≤60-word spoken version) |
| Deepgram | Minutes of audio transcribed | Recording stops ~0.8 s after you stop talking |
| Anthropic (Claude Haiku) | Tokens per request | Keyword shortcuts skip the routing call for common requests |
| SerpApi | Searches (Miles Ahead fare checks) | Flights under $55 aren't checked; checks are spaced out |
| AviationStack | API calls (Drew Parture) | Polling starts 2 h before departure and stops after landing |
| Gemini | Web answers + a model check every 14 days | — |
| Google Sheets / Calendar, Telegram | Free | — |

The voice assistant is the main cost; the Telegram-only agents are cheap.

## Models

Every Claude call uses one model, set by `CLAUDE_MODEL` in `~/.jarvis_env`
(default `claude-haiku-5-5`; code in `jarvis_core/llm.py`). Haiku 5.5 is
about a tenth of Haiku 4.5's per-token price, though the same text counts as
roughly 30% more tokens. Every call runs with the model's "thinking"
switched off, for speed.

To go back to Haiku 4.5, set `CLAUDE_MODEL=claude-haiku-4-5-20251001` and
restart the services. A single agent can use a different model with
`ANTHROPIC_MODEL` in its own `.env`.
