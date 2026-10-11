# Miles Ahead — Southwest fare-drop alerts

Southwest typically lets you rebook at a lower fare and keep the difference
as travel credit (check their current policy).
Miles Ahead finds your booked Southwest flights in Google Calendar, tracks
what you paid, checks current fares every morning (6:00), and messages you
when the same flight — or another Southwest flight within an hour of it —
gets cheaper. Other airlines' flights are ignored here (Drew Parture still
sends their delay and departure alerts).

A flight is picked up when the calendar event's title has its flight number
(`WN 1234`) and the route can be read from the event: two airport codes
(`LAX … SFO`) in the title or description. To add an airline with the same
kind of policy, add it to `TRACKED_AIRLINES` in `calendar_reader.py`.

- Only alerts when you'd save **more than $5**
- Skips flights bought for under $55
- Stops tracking flights you cancel (removed from the calendar)
- Sends a short "checked, nothing cheaper" note so you know it ran

> "List my tracked flights" · "Pause WN1234" · "Resume monitoring the 14th"

## Use it on its own (Telegram bot)

1. Bot and chat ids: [setup.md §4](../../docs/setup.md#4-telegram-bots).
   `RECIPIENT_CHAT_ID` receives the alerts.
2. Google access (Calendar + Sheets): [setup.md §3](../../docs/setup.md#3-google-sheets-and-calendar-access-one-oauth-client-for-everything).
3. A [SerpApi](https://serpapi.com) key (fares come from Google Flights via
   SerpApi; you're bound by its terms).
4. Create a sheet with a tab named **`Flights`**:

   | Flight | Date | Route | Departure | Confirmation | Paid Price | Last Checked | Status |
   |---|---|---|---|---|---|---|---|

   New calendar flights are added automatically; fill in **Paid Price** if it
   isn't detected.
5. `cp .env.example .env`, fill it in, `python pi_run.py`.

## With Jarvis

Port **5005**. Sensitivity tier: **personal**.

## Files

`checker.py` (daily check, alert rules) · `scraper.py` (SerpApi) ·
`calendar_reader.py` · `sheets.py` · `main.py` · `pi_run.py`
