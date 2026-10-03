# Drew Parture — flight alerts

Reads flights from **your Google Calendar** (Gmail can add them from airline
confirmation emails automatically) and keeps you posted:

- **7:00 heads-up** for flights in the next day. Flights between 7 and 9am
  that same morning are skipped (you're already on your way); anything before
  9am *tomorrow* is announced today, so an early flight is never a surprise.
- **Live alerts** from 2 hours before departure: on time or delayed, delay
  updates, **departed**, **landed** — including red-eyes landing after
  midnight. Checked every 30 minutes; polling stops once the flight lands.

> "When is my next flight?" · "Any flights this week?" · "Book me an Uber to
> the airport" (sends an Uber deep link)

## Use it on its own (Telegram bot)

1. Bot and chat ids: [setup.md §4](../../docs/setup.md#4-telegram-bots).
   `RECIPIENT_CHAT_ID` receives the alerts.
2. Google access (Calendar, read-only): [setup.md §3](../../docs/setup.md#3-google-sheets-and-calendar-access-one-oauth-client-for-everything).
3. An [AviationStack](https://aviationstack.com) key for live status.
4. `cp .env.example .env`, fill it in, `python pi_run.py`.

Flight events are recognized by airline names or flight numbers in the event
title or description; Southwest events get a dedicated parser. Destination
comes from airport codes or common city names in the title.

## With Jarvis

Port **5002**. Voice: "when do I land" / "my flight" go straight here.
Sensitivity tier: **personal**. Feeds the morning brief.

## Customizing

Alert wording names the traveller ("Gio's flight has departed") — it reads
one person's calendar by design. Change it in `main.py`; the heads-up window
is in `morning_flight_check()`.

## Files

`calendar_reader.py` (find and parse flight events) · `flight_tracker.py`
(AviationStack) · `main.py` (alerts, Telegram, `/jarvis`) · `agent.py` (Q&A) ·
`uber.py` · `pi_run.py` (schedules)
