# Will Due — shared to-do list

A to-do list for a couple (or household): every task has an **owner** (a
person or "Shared") and a **priority**, inferred from how you say it.

> "Add call the electrician to my list" · "Add plan the trip to our list,
> no rush" · "What are Kenz's tasks?" · "I finished the taxes" ·
> "Mark 2 and 5 as done" · "Undo that"

Priority comes from your words: "asap / today" → urgent, "this week" → soon,
"whenever / no rush" → low. Saying "I bought the shoes" marks the matching
task done.

## Use it on its own (Telegram bot)

1. Bot and chat ids: [setup.md §4](../../docs/setup.md#4-telegram-bots). Each
   person messages the bot from their own Telegram account — that's how it
   knows whose "my list" is meant.
2. Google access: [setup.md §3](../../docs/setup.md#3-google-sheets-and-calendar-access-one-oauth-client-for-everything).
3. Create a sheet with a tab named **`Tasks`** and these headers:

   | ID | Task | Owner | Priority | Priority Score | Added By | Date Added | Status |
   |---|---|---|---|---|---|---|---|

4. `cp .env.example .env`, fill it in, and run `python pi_run.py`.

## With Jarvis

Port **5004**. Sensitivity tier: **personal**, and `needs_speaker` — by voice,
Jarvis needs to know whose list: a name in the request ("Kenz's list"),
"our / shared", a recognized voice, or it asks "Is that for Gio or Kenz?".

## Customizing

Owner names and pronoun rules ("his/her") are in the prompt in `agent.py`.

## Files

`agent.py` (prompt, commands) · `sheets.py` · `main.py` · `pi_run.py`
