# Setup — the whole thing on a Raspberry Pi

This sets up all agents plus the voice assistant. For a single agent, follow
its own README instead — the account steps below are the same, just fewer.

Plan for an evening. Most of the time goes into creating accounts and keys.

## 1. What you need

**Hardware:** a Raspberry Pi 4 or 5 (4 GB+), a USB microphone and a speaker
(a USB speakerphone does both), optionally a BlinkStick LED. See
[hardware-and-costs.md](hardware-and-costs.md).

**Accounts / keys** — only for the pieces you want:

| Service | Used by | Get it at |
|---|---|---|
| Anthropic API | Router, all agents except Miles Ahead | console.anthropic.com |
| Telegram bots | Every agent (one bot each) | @BotFather in Telegram |
| Google Cloud OAuth client | Sheets/Calendar agents | console.cloud.google.com |
| Deepgram | Voice assistant (speech-to-text) | deepgram.com |
| ElevenLabs | Voice assistant (speech) | elevenlabs.io |
| Gemini API | Web answers, model check | aistudio.google.com |
| AviationStack | Drew Parture (flight status) | aviationstack.com |
| SerpApi | Miles Ahead (fares) | serpapi.com |
| AirNow *(optional)* | Andy Mann (air quality, US) | docs.airnowapi.org |

## 2. Prepare the Pi

Raspberry Pi OS (64-bit), then:

```bash
sudo apt update && sudo apt install -y git python3-venv python3-dev ffmpeg portaudio19-dev alsa-utils
cd ~ && git clone https://github.com/<you>/jarvis-home.git
cd ~/jarvis-home
```

## 3. Google Sheets and Calendar access (one OAuth client for everything)

1. In Google Cloud Console, create a project and **enable** the Google Sheets,
   Google Drive and Google Calendar APIs.
2. Configure the **OAuth consent screen** (External; add yourself as a test
   user), then set its publishing status to **In production**. *Apps left in
   "Testing" get refresh tokens that expire after 7 days*, which silently breaks
   every Sheets agent a week later. (Unverified is fine for personal use; you'll
   click through a warning once.)
3. Create an **OAuth client ID** of type *Desktop app* and download it as
   `credentials.json`.
4. On a computer with a browser (not the headless Pi):
   ```bash
   pip install google-auth-oauthlib
   python agents/andy_mann/get_token.py     # with credentials.json in the current folder
   ```
   Sign in with the Google account that owns your sheets and calendar. This
   writes `token.json`.
5. Copy `client_id`, `client_secret` and `refresh_token` from `token.json`
   into `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`
   in step 5. Then delete `token.json` and `credentials.json` from that computer
   or store them somewhere safe.

The central service checks this token every morning (6:30) and messages you
on Telegram if it stops working, with the likely reason.

## 4. Telegram bots

1. In Telegram, talk to **@BotFather** → `/newbot`, once per agent you want
   (e.g. a "Jarvis" bot, a shopping-list bot…). Note each token.
2. Send any message to each bot, then find your **chat id**:
   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | grep -o '"chat":{"id":[0-9-]*'
   ```
   Do this for each household member.

Bots use long-polling, so there's nothing to expose to the internet.

## 5. Secrets and settings

```bash
cp deploy/jarvis_env.example ~/.jarvis_env && chmod 600 ~/.jarvis_env
nano ~/.jarvis_env                                  # shared keys

for a in andy_mann drew_parture shawn_ping will_due miles_ahead jarvis; do
  cp agents/$a/.env.example agents/$a/.env && chmod 600 agents/$a/.env
done
nano agents/shawn_ping/.env                         # …and each agent you use
```

If you edit secrets on another computer, `deploy/push_secrets_from_windows.sh`
copies the `.env` files to the Pi over ssh.

Then make it yours — names in `household.json`, timezone in `~/.jarvis_env`
(`JARVIS_TIMEZONE`). See [customizing.md](customizing.md).

## 6. Google Sheets

Each Sheets agent expects specific tabs and column headers — create one
spreadsheet per agent (or one with all tabs) and put its id (the long part of
the URL) in that agent's `GOOGLE_SHEET_ID`. The layouts are in each agent's
README: [Shawn Ping](../agents/shawn_ping/README.md),
[Will Due](../agents/will_due/README.md),
[Andy Mann](../agents/andy_mann/README.md),
[Miles Ahead](../agents/miles_ahead/README.md).

## 7. Start the agents

```bash
bash deploy/pi_deploy.sh
```

This creates a virtualenv (`agents/venv`), installs dependencies, and installs
one **user systemd service per agent** (`jarvis-<name>`), started now and at
every boot. At the end it waits for each agent to answer on its local port.

**Not using every agent?** Delete the folders of the ones you don't want
*before* running the script (it skips missing folders) and remove their
entries from `registry.json`, so the router never sends requests to them. An
agent folder without a filled-in `.env` would otherwise be installed and keep
failing.

## 8. The voice assistant

```bash
python3 -m venv ~/jarvis-env
~/jarvis-env/bin/pip install -r deploy/requirements-voice.txt

# find your devices: mic name for JARVIS_MIC_NAME, speaker for JARVIS_AUDIO_OUT
arecord -l && aplay -l

# record the offline replies (sorry / unauthorized / repeat)
set -a && source ~/.jarvis_env && set +a && ~/jarvis-env/bin/python generate_voice_responses.py

# install and start the service
REPO=~/jarvis-home
sed "s|@REPO@|$REPO|g" deploy/jarvis-voice.service.example > ~/.config/systemd/user/jarvis.service
systemctl --user daemon-reload && systemctl --user enable --now jarvis
loginctl enable-linger "$USER"     # keep user services running without a login
```

Generate the short "on it" chimes (played while Jarvis works) in your chosen
voice — they go to `~/acks/`:

```bash
set -a && source ~/.jarvis_env && set +a && ~/jarvis-env/bin/python agents/jarvis/generate_acks.py
```

Any short mp3s named `on_it.mp3`, `one_moment.mp3`, `let_me_check.mp3`,
`looking_into_that.mp3` and `right_away.mp3` in `~/acks/` work too.

## 9. Check it works

- Say **"Hey Jarvis, what time is it"** (no agents involved)
- **"Hey Jarvis, add eggs to the shopping list"** (Shawn Ping)
- Message your Jarvis bot on Telegram: **"what are my priorities"**
- Run the tests: `~/jarvis-env/bin/python -m unittest discover -s tests -b`

## Day to day

```bash
# update after pulling changes
cd ~/jarvis-home && git pull && systemctl --user restart jarvis 'jarvis-*'
# (re-run deploy/pi_deploy.sh when deploy/ or requirements changed)

# logs
journalctl --user -u jarvis -f            # voice loop
journalctl --user -u jarvis-will_due -f   # an agent
```

## Troubleshooting

| Symptom | Look at |
|---|---|
| An agent shows "no response" after deploy | `journalctl --user -u jarvis-<name> -n 40` — usually a missing key in its `.env` |
| Every request: "Sorry, I didn't manage to process that" | `journalctl --user -u jarvis -n 50` for `[Router] Haiku routing error` — check `ANTHROPIC_API_KEY` |
| Sheets agents stop working after a week | OAuth app still in "Testing" — see step 3 |
| No sound | `aplay -l`, then set `JARVIS_AUDIO_OUT` (e.g. `hw:1,0`) |
| "Audio device not found" | `arecord -l`, then set `JARVIS_MIC_NAME` to part of the mic's name |
| Jarvis wakes up to the TV | Raise the wake threshold in `jarvis-pi.py` (`score > 0.6`), or move the mic |
