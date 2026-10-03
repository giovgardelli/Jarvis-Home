# Shawn Ping — shared shopping list

A shopping list two people can add to by text or voice. Stored in a Google
Sheet you own, so you can also edit it from your phone.

> "Add oat milk and eggs to the list" · "What's on the shopping list?" ·
> "Remove item 3" · "1 4" (removes items 1 and 4) · "Clear the eggs"

It warns about near-duplicates ("similar item already on the list: *Oat
milk*. Same thing?") and, when the list reaches 7 items, offers to text it.

## Use it on its own (Telegram bot)

1. Create a bot with @BotFather and get your chat ids ([setup.md §4](../../docs/setup.md#4-telegram-bots)).
2. Google access: [setup.md §3](../../docs/setup.md#3-google-sheets-and-calendar-access-one-oauth-client-for-everything).
3. Create a sheet with a tab named **`Shopping_List`** and these headers in row 1:

   | # | Item | Added By | Date |
   |---|---|---|---|

4. `cp .env.example .env` and fill it in; shared keys go in `~/.jarvis_env`
   (`ANTHROPIC_API_KEY`, `JARVIS_SECRET`, `GOOGLE_*`).
5. Run it: `python pi_run.py` from this folder (inside the venv from
   `deploy/requirements-pi.txt`), or install everything with `deploy/pi_deploy.sh`.

## With Jarvis

Port **5003**. Voice: "Hey Jarvis, we need bread" goes straight here (a
keyword shortcut, no routing call). Sensitivity tier: **household**.

## Files

`agent.py` (Claude prompt and list commands) · `sheets.py` (Google Sheets,
cached) · `main.py` (Telegram + `/jarvis` endpoint) · `pi_run.py` (entrypoint)
