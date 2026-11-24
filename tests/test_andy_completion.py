"""Ticking off a home task: "1 done" from the morning brief marks the exact
task without asking Claude, Claude can't claim a completion the sheet didn't
get, and yesterday's conversation can't block today's request.

The bug this pins: "Mark #1 done" got "already marked done two days ago"
while the sheet said otherwise — an earlier "marked as done" reply that never
reached the sheet was still in Andy Mann's undated history, and Claude
believed it over the data."""
import os
import types
import unittest
from datetime import date, datetime
from unittest import mock

from helpers import AGENTS, fake_message, install_stubs, isolated_modules, load_file
from router_under_test import get_router

ANDY = os.path.join(AGENTS, "andy_mann")
LIBRARY = [{"Task": "Water the plants", "System": "Garden", "Frequency Value": 2, "Frequency Unit": "weeks",
            "Last Completed": "3/2/2026", "Next Due": "3/16/2026"}]


def fake_sheets(marked):
    def update_last_completed(name, completed_date=None):
        if any(t["Task"].lower() == name.lower() for t in LIBRARY):
            marked.append(name)
            return True
        return False
    return types.SimpleNamespace(_cache={"task_library": LIBRARY, "contractors": [], "warranties": [],
                                         "property_profile": []},
                                 _load_cache=lambda tab=None: None,
                                 update_last_completed=update_last_completed)


def load_andy(marked):
    install_stubs()
    for k in ("ANTHROPIC_API_KEY", "JARVIS_SECRET", "TELEGRAM_BOT_TOKEN"):
        os.environ.setdefault(k, "test")
    with isolated_modules(fakes={"sheets": fake_sheets(marked)}, path=ANDY, drop=("agent",)):
        return load_file(os.path.join(ANDY, "agent.py"), "andy_agent_completion_test")


class ExactCompletion(unittest.TestCase):
    def test_brief_items_are_marked_without_claude(self):
        marked = []
        agent = load_andy(marked)
        agent.client = types.SimpleNamespace(messages=types.SimpleNamespace(
            create=lambda **k: self.fail("no Claude call for an exact name")))
        out = agent.complete_tasks(["Water the plants"])
        self.assertEqual((out["status"], marked), ("completed", ["Water the plants"]))
        self.assertEqual(out["response_text"], "Marked Water the plants as completed today.")
        out = agent.complete_tasks(["Water the plants", "Paint the moon"])
        self.assertEqual(out["status"], "not_found")
        self.assertIn("couldn't find Paint the moon", out["response_text"])

    def test_claude_cant_claim_a_completion_the_sheet_didnt_get(self):
        marked = []
        agent = load_andy(marked)
        reply = "Great — marked Water planters as completed today.\nCOMPLETE_TASK|Water planters"
        agent.client = types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **k: fake_message(reply)))
        out = agent.chat("I watered the plants")
        self.assertEqual(out["status"], "not_found")
        self.assertEqual(out["response_text"],
                         "I couldn't find Water planters in the task library, so nothing was marked done.")
        self.assertEqual(marked, [])

    def test_prompt_puts_the_sheet_above_earlier_messages(self):
        agent = load_andy([])
        self.assertIn("only truth about what has been done", agent.SYSTEM_PROMPT)
        self.assertIn("never refuse to complete", agent.SYSTEM_PROMPT)


class RouterSendsExactNames(unittest.TestCase):
    def test_numbered_item_carries_the_task_name(self):
        r = get_router()
        sent = []
        saved = r.dispatch
        r.dispatch = lambda agent_id, intent, params, *a, **k: (
            sent.append((agent_id, params)) or {"response_text": "ok", "status": "completed", "tasks": []})
        try:
            r.state.set_brief_items("gio", {"date": datetime.now(r.PT).date().isoformat(), "items": [
                {"n": 1, "task": "Water the plants", "list": "andy_mann", "done": False}]})
            out = r.route("Mark #1 done", "gio", channel="telegram", voice_mode=False)
        finally:
            r.dispatch = saved
        self.assertEqual(sent, [("andy_mann", {"original_message": "Mark Water the plants as done",
                                               "complete_tasks": ["Water the plants"]})])
        self.assertEqual(out["response_text"], "✅ Done: #1 Water the plants.")


class HistoryIsTodayOnly(unittest.TestCase):
    def load_main(self, agent):
        install_stubs()
        for k in ("ANTHROPIC_API_KEY", "JARVIS_SECRET", "TELEGRAM_BOT_TOKEN"):
            os.environ.setdefault(k, "test")
        path = os.path.join(AGENTS, agent)
        fakes = {
            "agent": types.SimpleNamespace(chat=None, complete_tasks=None, process_message=None),
            "scheduler": types.SimpleNamespace(build_weather_messages=None),
            "sheets": types.SimpleNamespace(_load_cache=lambda tab=None: None),
        }
        with isolated_modules(fakes=fakes, path=path, drop=("main",)):
            return load_file(os.path.join(path, "main.py"), f"{agent}_main_history_test")

    def test_yesterdays_exchanges_are_dropped(self):
        for agent in ("andy_mann", "will_due"):
            with self.subTest(agent=agent):
                main = self.load_main(agent)
                days = [date(2026, 3, 14)]

                class Clock(datetime):
                    @classmethod
                    def now(cls, tz=None):
                        return datetime(days[0].year, days[0].month, days[0].day, 9, tzinfo=tz)
                with mock.patch.object(main, "datetime", Clock):
                    main.update_history("gio", "Mark Water the plants as done", "Great — marked it.")
                    self.assertEqual(len(main.get_history("gio")), 2, "same day: kept")
                    days[0] = date(2026, 3, 16)
                    self.assertEqual(main.get_history("gio"), [], "a new day starts clean")


if __name__ == "__main__":
    unittest.main()
