# Ray Mote — the thermostats

Controls the Nest thermostats by voice (through Jarvis) and on Telegram.

> "Set upstairs to 70" · "Make it a bit warmer downstairs" · "Set both to 69"
> · "Turn on the AC" · "Put the house in eco mode" · "What's the thermostat
> set to?" · "I'm cold"

- **Limits:** Ray only sets temperatures inside a range you choose, by
  default **68–75°F** (`RAY_MOTE_MIN_F` / `RAY_MOTE_MAX_F` in `.env`). Ask for 80 and it answers "I can only
  set between 68 and 75. Set upstairs to 75?", and changes nothing unless you
  say yes. "Warmer" at 75 says it's already at the most. The limit applies to
  Ray only: a change in the Nest app is reported, not undone.
- **Which thermostat:** say its name as the Nest app shows it (the examples
  here use "upstairs" and "downstairs"), or "both" / "all". With more than one
  and none named, Ray asks ("Upstairs or downstairs?") and the next thing you
  say answers it, without "Hey Jarvis". Questions ("what's it set to?") cover
  every thermostat. One thermostat? Ray never asks.
- **Warmer / cooler** moves the set point by 2°F ("a bit": 1°F, "by 3": 3°F).
- **Eco mode** is switched off before setting a temperature, since Nest
  ignores set points in eco mode. A system switched **off** is left off: say
  "turn on the heat" first. "Set the AC to 72" while it's heating is left
  alone too, with a reply saying why.
- **Heat-and-cool mode:** "set to 72" keeps it between 70 and 74 (inside the
  limits, at least 3°F apart).
- **Speed:** common phrasings are understood by keyword rules, with no Claude
  call. Unusual ones ("make it toasty for movie night") are parsed by Claude
  into the same command. Either way, code decides what is sent to Nest and
  writes the reply, so the limits can't be talked around.
- Not supported (Ray says so): schedules ("at 7pm"), the fan, other devices.

## Set up Nest access (about an hour, once)

Works with thermostats on Google's Device Access program, which includes the
Nest Learning Thermostat (3rd gen) and later. The 1st and 2nd generation lost
Google's support for remote control in 2025.

1. **Device Access**: sign up at
   [console.nest.google.com/device-access](https://console.nest.google.com/device-access)
   (a one-time US$5 fee) with the Google account your Nest app uses.
2. **Google Cloud** (the project from [setup.md §3](../../docs/setup.md#3-google-sheets-and-calendar-access-one-oauth-client-for-everything)
   is fine):
   - enable the **Smart Device Management API**;
   - create an **OAuth client ID** of type *Web application* with the
     authorized redirect URI `https://www.google.com`;
   - the OAuth consent screen must be **In production**, or the token stops
     working after 7 days.
3. Back in Device Access, **create a project** and give it that OAuth client
   id (you can skip events / Pub/Sub). Note the **project id**.
4. **Name the thermostats** in the Nest app the way you say them ("Upstairs",
   "Downstairs"), or map them with `NEST_ZONES` in `.env`
   (`upstairs=Hallway, downstairs=Living Room`).
5. On a computer with a browser:
   ```bash
   python agents/ray_mote/get_token.py
   ```
   Sign in, **allow both thermostats**, and paste back the google.com address
   you land on. It prints the thermostats it can see and the lines for `.env`.
6. `cp .env.example .env`, paste those lines in, then `bash deploy/pi_deploy.sh`.

Check: "Hey Jarvis, what's the thermostat set to?"

## With Jarvis

Port **5007**. Thermostat phrases go straight here (a keyword shortcut, no
routing call); "service the HVAC" and "when is the furnace filter due" still
go to Andy Mann, and "temperature in Paris" to web search. Sensitivity tier:
**public**: anyone in the room, guests included, can change the temperature.

Ray's own Telegram bot is optional: Jarvis's Telegram bot reaches Ray too. To
add one, fill the Telegram lines in `.env` ([setup.md §4](../../docs/setup.md#4-telegram-bots)).

## Files

`agent.py` (understanding requests, limits, replies) · `nest.py` (Google
Smart Device Management API, Fahrenheit ↔ Celsius) · `get_token.py` (one-time
account link) · `main.py` (`/jarvis` endpoint + optional Telegram) · `pi_run.py`
