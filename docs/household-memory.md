# Household memory

Jarvis gets to know your home over time: who lives there, your city, your
team, the visit coming up on Friday. It uses that to answer household
questions and to make vague questions specific. "What time's the game?"
becomes "what time is the Mariners game?"

Code: `jarvis_core/memory.py`.

## How it learns

| Way | Example | When it's used |
|---|---|---|
| **You tell it** | "Jarvis, remember that our team is the Mariners" | Immediately |
| **It picks it up** from everyday requests (voice and Telegram) | "Kenz's aunt is visiting on Friday, add sheets to the list" | From the next morning, if it was **stated** by someone in the house |
| **It notices a pattern** | You ask about the Mariners on several days | Once the pattern shows up on **two different days** |

Learning happens once a night, at **3:30am**:
1. Each exchange goes into a short-lived inbox.
2. Claude reads that day's inbox together with the current memory and
   proposes new facts, reinforcements, corrections and expiries.
3. The inbox is then **deleted**. Only the distilled facts stay; no
   transcripts are kept.
4. Anything new is listed in the 7am Telegram brief, so a wrong guess can
   be fixed with "forget …".

Nothing is added in real time, so it never slows a reply down.

**Never stored:** passwords, PINs, door, garage or alarm codes, and card or
account numbers. This is checked in code, not only asked of the AI.
Time-bound facts ("staying until Sunday") expire on their own. A pattern
that isn't seen again within 45 days is dropped.

## Commands (voice or Telegram)

- "Jarvis, remember Kenz's birthday is September 14th" (with or without "that"):
  saved immediately. Several at once work too, each saved separately with its
  own reminders:
  - by voice: "…Kenz's birthday is September 14th and Theo's is March 22"
  - on Telegram: "Jarvis remember:" followed by a list, one per line (up to
    25)
- "What do you remember?" / "Tell me everything you know about my family":
  - by voice: reads the latest 8, then "Say 'text me that' for the full
    list". "Text me that" sends everything to your Telegram.
  - on Telegram: the full list, with each reminder plan (🔔 who and when).
    Dated facts also show for whoever gets their reminders, so Kenz's
    birthday shows in Gio's list.
- "Forget that …": removes the closest matching fact. It also won't be
  learned again.
- "Forget everything": wipes the memory.

Not memory commands:
- "Remember to …" goes to the to-do list.
- "Remember when we…" is just conversation.
- "Forget it" on its own means "never mind".

## Reminders for dates (birthdays, anniversaries, visits)

When you tell Jarvis something with a date ("Jarvis, remember Kenz's birthday
is September 14th"), Claude plans the reminders once, at that moment. After that,
plain code delivers them in the 7am brief, as a "🗓 Coming up" section on
Telegram and at the start of the spoken brief.

- **When to remind** depends on how much preparation the event needs, not on
  how close the person is:
  - partner's birthday or anniversary (a gift that ships, a reservation):
    2 weeks, 1 week, the day before and on the day
  - other close family birthdays: 1 week, the day before, on the day
  - a friend's birthday: on the day
  - a visit or appointment: the day before and on the day
- **Who gets reminded:** never the person a gift or surprise is for. Kenz's
  birthday reminds Gio only, in Gio's brief.
- **The reply says the plan**, so you can check it: "I'll remind Gio 2 weeks
  before, 1 week before, the day before and on the day."
- **To change the timing, say it again:** "Jarvis, remember Kenz's birthday is
  September 14th, remind me 3 weeks before". It replaces the old one.
- **Repeats:** birthdays and anniversaries every year. One-off events (a
  visit) are dropped once the day has passed.
- **What gets reminders:** only dates you tell Jarvis directly. Facts learned
  overnight never get reminders. Facts with reminders show a 🔔 in "what do
  you remember?".

Why it isn't a separate agent: the dates already live in memory and the brief
already reaches everyone each morning, so a separate bot would add a second
place to tell things. That changes if it grows into gift ideas or budgets.

## Who a fact is about

Most facts belong to the **household**. A fact is filed under a person only
when Jarvis knows who said it:
- **Telegram:** always, from the account.
- **Voice:** voice ID is switched off for *answering* (see
  [voice-id.md](voice-id.md)). It still runs **in the background after the
  reply** to make a best guess. The guess is used only when the match is
  confident and clearly beats the other voice; otherwise the fact goes to
  the household. A wrong guess only misfiles a fact; it never blocks or
  changes an answer. `MEMORY_VOICE_GUESS=off` turns the guess off.

Personal facts are only used for that person, or when the request names
them.

## Where facts go (privacy)

The memory is stored only on the Pi (`jarvis_state.db`). For each request,
only the few facts that match it are sent, not the whole memory:
- **Claude** (routing) sees the matching facts, so it can answer "when is
  Kenz's aunt visiting?" or route with context.
- **Gemini** (web answers) only sees facts marked safe for search: city,
  team, favourite store or cuisine. It never sees health, schedules,
  travel, relationships or anything about guests.
- The nightly learning sends the day's exchanges to Claude once more. They
  were already sent once when they were routed.

## Settings and tools

In `~/.jarvis_env`:
- `MEMORY=off` switches the whole feature off.
- `MEMORY_VOICE_GUESS=off` stops the background voice guess.

On the Pi, from the repo root:

```
python3 -m jarvis_core.memory          # list stored facts, candidates and forgotten ones
python3 -m jarvis_core.memory learn    # run tonight's learning now
```
