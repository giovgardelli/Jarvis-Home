# Andy Mann — home maintenance

Keeps track of recurring home maintenance, warranties and contractors, and
warns you about weather and air quality that affect the house.

> "When is the HVAC filter due?" · "I replaced the furnace filter today" ·
> "Who's our plumber?" · "Add a warranty for the dishwasher, 2 years, bought
> today" · "What's the weather looking like?"

Weather alerts (US: National Weather Service; air quality: AirNow) run at
7:00 and 19:00 and can suggest related tasks (e.g. check the filter when air
quality is poor).

## Use it on its own (Telegram bot)

1. Bot and chat ids: [setup.md §4](../../docs/setup.md#4-telegram-bots).
2. Google access: [setup.md §3](../../docs/setup.md#3-google-sheets-and-calendar-access-one-oauth-client-for-everything).
3. Create a sheet with these tabs and headers:

   **`Task_Library`** — recurring tasks
   | Task | System | Frequency Value | Frequency Unit | Last Completed | Reminder Notice (Days) | Next Due | Weather Triggers |
   |---|---|---|---|---|---|---|---|

   (`Frequency Unit`: days / weeks / months / years. `Next Due` is calculated
   when empty. `Weather Triggers` is optional.)

   **`Warranties`**
   | Item | Category | Purchase Date | Warranty Years | Expiry Date | Model | Serial | Cost | Vendor | Notes |
   |---|---|---|---|---|---|---|---|---|---|

   **`Contractors`**
   | Name | Trade | Rank | Phone | Email | Website | Rate | Notes |
   |---|---|---|---|---|---|---|---|

   **`Property_Profile`** — two columns, `Field` and `Value`; include a row
   `Zip Code` for weather alerts.

   **`Weather_Preferences`** — `Field` / `Value` rows to switch alert types
   on/off (optional).

4. `cp .env.example .env` (add `AIRNOW_API_KEY` for air quality), then `python pi_run.py`.

## With Jarvis

Port **5001**. Part of "what are my priorities" together with Will Due.
Sensitivity tier: **household**.

## Files

`agent.py` · `sheets.py` · `weather.py`, `weather_triggers.py` (alerts) ·
`scheduler.py` · `main.py` · `pi_run.py` · `get_token.py` (one-time Google
OAuth helper used by every Sheets agent — see setup.md)
