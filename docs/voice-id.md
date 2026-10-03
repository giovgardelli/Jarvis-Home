# Voice ID — who is speaking

Voice ID lets Jarvis tell household members apart by voice, so it can answer
"what's on **my** to-do list" for the right person and keep personal
information away from guests. It runs entirely on the Pi
([Resemblyzer](https://github.com/resemble-ai/Resemblyzer)); voice profiles
never leave the device.

**It's off by default.** Turn it on once your microphone is good enough —
see [Lessons learned](#lessons-learned) for why that matters.

## How it works

1. **Enrollment:** each person reads a short script (~25 seconds). Jarvis
   turns the recording into a voice profile — a small file in
   `~/voice_profiles/`.
2. **Every command:** while the command is being transcribed, Jarvis compares
   the same audio to each profile and gets a similarity score.
3. **The score becomes a status:**

| Score | Status | Meaning |
|---|---|---|
| ≥ `VOICE_ID_CONFIRMED` (0.78) | **confirmed** | It's this person |
| ≥ `VOICE_ID_UNCERTAIN` (0.73) | **uncertain** | Probably a household member, not sure who |
| below | **unauthorized** | Not a household voice |
| analysis failed | **error** | Treated like uncertain ("could you repeat that?") |

4. **The router decides** whether that status is enough for the request,
   using the destination's **sensitivity tier**:

| Tier | Allowed for | Set in |
|---|---|---|
| public | everyone | timers, web questions, small talk (built in) |
| household | confirmed or uncertain | `"access": "household"` in `registry.json` |
| personal | confirmed only | `"access": "personal"` in `registry.json` |

Blocked requests get a short, pre-recorded reply ("I'm only authorized to
assist the residents of this household…" or "I didn't quite catch who's
speaking — could you try again?") and never reach the agent. The reasoning
behind the tiers is in [privacy-and-security.md](privacy-and-security.md).

## When voice ID is off

- Nothing is gated — anyone in the room can use everything.
- Most agents don't care who's asking and use the default person
  (`default_speaker` in `household.json`).
- Agents that need to know (`"needs_speaker": true` — the to-do list) use a
  name said in the request ("add X to **Kenz's** list"), treat "our / us /
  both / shared" as shared, and otherwise **ask** "Is that for Gio or Kenz?"
  and run the request once you answer.
- The voice model isn't loaded at all (faster startup, less memory).

## Turning it on

```bash
cd ~/jarvis-home
# 1. Enroll each person (same mic and position you'll normally use)
~/jarvis-env/bin/python enroll.py --speaker gio
~/jarvis-env/bin/python enroll.py --speaker kenz

# 2. Enable it
echo "VOICE_ID_ENABLED=true" >> ~/.jarvis_env
systemctl --user restart jarvis

# 3. Check the scores (repeat as each person, and as a guest)
~/jarvis-env/bin/python enroll.py --test
```

Speaker ids (`gio`, `kenz`) must match the ids in `household.json`.

### Tuning the thresholds

Run `enroll.py --test` several times per person, from where you usually
stand, plus a few times with someone outside the household. Then set, in
`~/.jarvis_env`:

- `VOICE_ID_CONFIRMED` — just below the scores you see for the right person
- `VOICE_ID_UNCERTAIN` — a bit lower, but above what guests score

If genuine and guest scores overlap, the microphone is the bottleneck — no
threshold will fix it.

## Lessons learned

This project's own household turned voice ID **off**. Here's what we found,
so you don't have to:

- **The microphone matters more than the model.** A far-field USB
  conference mic across the room picks up a lot of room echo and little of
  what makes a voice distinctive. Scores for the right person and for guests
  ended up too close to separate reliably.
- **We needed a 3x software boost** (`prepare_audio()` in `jarvis-pi.py`)
  for transcription to work at a distance. Voice profiles must be enrolled
  through exactly the same audio processing they're compared against — if
  you change the boost or the mic, **re-enroll**.
- **Short phrases score lower.** "Yes" or "Kenz" carries much less voice than
  a full sentence, which is why follow-up answers accept "uncertain" for the
  same person.
- **What we'd try next:** a mic array with beamforming and noise suppression
  (e.g. a ReSpeaker-style board), placing the mic closer to where people
  talk, enrolling in the real room with normal background noise, and
  enrolling two or three samples per person.

## Files

| File | Role |
|---|---|
| `voice_id.py` | Profiles, embeddings, scoring, thresholds |
| `enroll.py` | Enrollment and `--test` scoring |
| `jarvis-pi.py` | Runs voice ID alongside transcription; plays the blocked-request clips |
| `jarvis_core/router.py` | Sensitivity tiers ("Sensitivity tiers" section) |
| `generate_voice_responses.py` | Records the pre-recorded replies (unauthorized / repeat / error) |
