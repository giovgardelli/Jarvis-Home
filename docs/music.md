# Music: radio and AirPlay

Two free ways to play music on the Jarvis speaker, with no Spotify or Apple
Music account linked to Jarvis:

| | What you get | How you control it |
|---|---|---|
| **Radio** | Thousands of free internet stations: SomaFM presets plus anything in [Radio Browser](https://www.radio-browser.info) | Your voice: "Hey Jarvis, play some jazz" |
| **AirPlay** | Whatever your iPhone or Mac plays: your Apple Music library, podcasts, YouTube | Your phone. The Pi shows up as a speaker called "Jarvis" |

Either way, **when you say "Hey Jarvis" the music drops to about a quarter of
its loudness** so Jarvis can hear you. It stays down while Jarvis answers and
comes back up when the exchange is over. Timers, alarms and the morning brief
turn it down the same way.

## Set it up (once)

```bash
bash ~/jarvis-home/deploy/setup_music.sh            # AirPlay name "Jarvis"
bash ~/jarvis-home/deploy/setup_music.sh "Kitchen"  # or name it yourself
```

The script installs `mpv` (radio) and `shairport-sync` (AirPlay) and sets up
a mixing device so Jarvis's voice, the radio and AirPlay can share the one USB
speaker. It sets `JARVIS_PLAYBACK=jarvis` in `~/.jarvis_env` and restarts
Jarvis. It reads your speaker from `JARVIS_AUDIO_OUT`, so set that first.

Then check:

1. `speaker-test -D jarvis -c 2 -t sine -l 1` plays a tone.
2. "Hey Jarvis, play some jazz" plays music, and "Hey Jarvis, stop" stops it.
3. On an iPhone: Control Center → the AirPlay icon → **Jarvis**.

## What you can say

| Say | Does |
|---|---|
| "play some jazz", "play the 80s station", "put on some chill music" | A preset from `stations.json` |
| "play KEXP", "play BBC Radio 1", "play reggae" | Searched on Radio Browser: by station name, then by genre |
| "play the radio", "play some music", "resume" | The last station (the first preset the very first time) |
| "stop", "pause", "turn off the radio" | Stops the radio |
| "louder", "turn it up", "quieter", "turn the volume down" | Radio volume, in steps. Kept across restarts |
| "what's playing", "what song is this" | The title the station sends, if it sends one |

These are keyword rules in the router, so they're instant and don't need a
Claude call. Anyone in the room can use them, like timers. They work by voice
only: over Telegram, Jarvis says to ask out loud.

What radio *can't* do: play a specific song, album or artist ("play Taylor
Swift" finds a fan station at best). Use AirPlay for that.

## Your stations

`stations.json` (at the repo root) holds the presets: a name, other words
for it (`aliases`, such as genres), and a stream or `.pls`/`.m3u` URL. The
defaults are [SomaFM](https://somafm.com) channels: free, ad-free and funded
by listeners. Add your local station and any genres you ask for often.
Anything not in the file is looked up on Radio Browser, so you only need
entries for favourites, or for a word that should always mean one station.

## How it fits together

```
"Hey Jarvis" ──► voice loop ── duck() ──► radio volume ×0.5, AirPlay −18 dB
                                 │
router ── music keyword rules ──► music_player.py ──► mpv ──┐
                                                           ├──► "jarvis" ALSA mixer ──► speaker
iPhone ── AirPlay ──► shairport-sync ──► "AirPlay" volume ─┤
Jarvis's voice, timers, brief ──► ffmpeg | aplay ──────────┘
```

- **Sharing the speaker.** A raw `hw:` device plays one sound at a time.
  `/etc/asound.conf` adds an ALSA `dmix` mixer on it, so Jarvis's voice is
  heard *over* the lowered music rather than fighting it for the device. If
  `JARVIS_PLAYBACK` is still a `hw:` device, the radio refuses to start.
  Otherwise it would take the speaker and Jarvis couldn't talk.
- **Turning things down.** The radio's volume is changed live through mpv's
  control socket. AirPlay goes through its own volume control (`AirPlay`,
  added by the script), which Jarvis turns down with `amixer`. Your phone's
  volume slider is separate and keeps working.
- **Radio vs AirPlay.** shairport-sync creates `/dev/shm/jarvis-airplay`
  while a phone is playing. When that appears, the radio stops. While it's
  there, Jarvis asks you to pause AirPlay on your phone before playing radio.
- **Dropouts.** If a station's stream drops, it's restarted (at most every
  10 seconds).

## Tuning

| Want | Change |
|---|---|
| Music lower or higher while Jarvis listens | `DUCK_RATIO` (radio) and `AIRPLAY_DUCK_PERCENT` (AirPlay) in `music_player.py` |
| Default or step volume | `DEFAULT_VOLUME`, `VOLUME_STEP` |
| A password on AirPlay | Add `password = "...";` to `general` in `/etc/shairport-sync.conf` and restart `shairport-sync` (re-running the script overwrites it) |

## Things to know

- **Loud music makes "Hey Jarvis" harder to hear.** The mic hears the
  speaker, and nothing can lower the music before the wake word is heard. If
  it misses you, speak up, or keep the music volume moderate. A mic farther
  from the speaker helps most.
- **AirPlay is AirPlay 1** with the Raspberry Pi OS package. iPhones and Macs
  play to it fine. It doesn't appear in the Home app or join multi-room
  groups.
- **Pausing on the phone** may keep AirPlay "active" for a little while.
  Jarvis keeps the radio off until the session ends.
- **Privacy:** what you ask the radio to play is sent to Radio Browser as a
  search when it isn't a preset. AirPlay is visible to everyone on your Wi-Fi
  (not the internet), so anyone on it can play to the speaker. Add a password
  if that matters ([privacy-and-security.md](privacy-and-security.md)).

## Troubleshooting

| Symptom | Look at |
|---|---|
| "Music isn't set up on this speaker yet" | Run `deploy/setup_music.sh`. Check `JARVIS_PLAYBACK=jarvis` in `~/.jarvis_env` |
| Jarvis goes silent after the setup | `speaker-test -D jarvis …`. If that fails, check the card in `/etc/asound.conf` against `aplay -l` |
| Radio says "Playing" but nothing plays | `mpv --audio-device=alsa/jarvis <the url>` shows the error. `journalctl --user -u jarvis -f` shows `[Music]` lines |
| "Jarvis" not in the AirPlay list | `systemctl status shairport-sync`. The phone and Pi must be on the same network |
| AirPlay isn't turned down on "Hey Jarvis" | `ls /dev/shm/jarvis-airplay` while playing (missing: the hooks didn't run). `amixer -c <card> sget AirPlay` |
| Jarvis says AirPlay is playing when it isn't | `sudo systemctl restart shairport-sync` clears the flag |
