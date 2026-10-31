"""The morning brief numbers this week's tasks, and "1 is done" ticks them off
on the list each came from (Will Due or Andy Mann)."""
import os
import unittest
from datetime import datetime

from helpers import AGENTS, isolated_modules, load_file
from router_under_test import get_router

PARSES = {
    "1 is done": [1],
    "1 done": [1],
    "#1 done": [1],
    "mark 1 done": [1],
    "Mark #2 as done.": [2],
    "mark 2 and 3 done": [2, 3],
    "1, 3 and 4 are done": [1, 3, 4],
    "1 3": [1, 3],
    "3": [3],
    "number 2 is done": [2],
    "done 4": [4],
    "i did 2": [2],
    "tick off 1 please": [1],
    "Hey Jarvis, one is done": [1],
    "two and three are finished": [2, 3],
    # not brief numbers
    "set it to 70": None,
    "add 2 eggs to the list": None,
    "set a timer for 10 minutes": None,
    "is 1 done?x": None,
    "the one in the kitchen is done": None,
    "done": None,
}

TASKS = [
    {"n": 1, "task": "Call the electrician", "list": "will_due", "done": False},
    {"n": 2, "task": "Replace the HVAC filter", "list": "andy_mann", "done": False},
    {"n": 3, "task": "Book the vet", "list": "will_due", "done": False},
]


class Parsing(unittest.TestCase):
    def test_phrase_table(self):
        r = get_router()
        failures = [f"{t!r}: got {r._brief_numbers(t)}, want {want}"
                    for t, want in PARSES.items() if r._brief_numbers(t) != want]
        self.assertEqual(failures, [], "\n" + "\n".join(failures))


class TickOff(unittest.TestCase):
    def setUp(self):
        self.r = get_router()
        self.saved = (self.r.dispatch, self.r._route_with_haiku)
        self.sent = []

        def dispatch(agent_id, intent, params, speaker, voice_mode=True, chat_id=0):
            self.sent.append((agent_id, params["original_message"], speaker))
            found = "vet" not in params["original_message"]
            return {"response_text": "Done!" if found else "I couldn't find that task.",
                    "status": "completed" if found else "not_found", "tasks": []}
        self.r.dispatch = dispatch
        self.r._route_with_haiku = lambda *a, **k: ("jarvis", "chat", "Hello.", "haiku")
        self.brief(TASKS)

    def tearDown(self):
        self.r.dispatch, self.r._route_with_haiku = self.saved
        self.r._pending_owner.clear()

    def brief(self, items, date=None):
        date = date or datetime.now(self.r.PT).date().isoformat()
        self.r.state.set_brief_items("gio", {"date": date, "items": [dict(i) for i in items]})

    def say(self, text, speaker="gio"):
        return self.r.route(text, speaker, channel="telegram", voice_mode=False)["response_text"]

    def test_each_number_goes_to_its_own_list(self):
        out = self.say("mark 1 and 2 done")
        self.assertEqual(self.sent, [("will_due", "Mark Call the electrician as done", "gio"),
                                     ("andy_mann", "Mark Replace the HVAC filter as done", "gio")])
        self.assertEqual(out, "✅ Done: #1 Call the electrician, #2 Replace the HVAC filter.")

    def test_done_twice_is_not_sent_twice(self):
        self.say("1 done")
        self.assertEqual(self.say("1 is done"), "#1 Call the electrician was already ticked off.")
        self.assertEqual(len(self.sent), 1)

    def test_a_task_the_list_cant_find_says_so(self):
        out = self.say("3 done")
        self.assertEqual(out, "I couldn't tick off #3 Book the vet: I couldn't find that task.")
        self.assertFalse(self.r.state.get_brief_items("gio")["items"][2]["done"])

    def test_numbers_outside_the_brief_or_old_briefs_are_ordinary_requests(self):
        self.assertEqual(self.say("7 done"), "Hello.")
        self.brief(TASKS, date="2020-01-01")
        self.assertEqual(self.say("1 done"), "Hello.")
        self.assertEqual(self.sent, [])

    def test_unknown_voice_asks_whose_list_first(self):
        out = self.r.route("one is done", None, channel="voice", voice_mode=True)
        self.assertEqual(out["response_text"], self.r.OWNER_QUESTION)
        self.assertEqual(self.sent, [])
        self.r.route("Gio", None, channel="voice", voice_mode=True)
        self.assertEqual(self.sent, [("will_due", "Mark Call the electrician as done", "gio")])


class BriefLayout(unittest.TestCase):
    def setUp(self):
        get_router()   # first, so state uses the test database, never ~/jarvis_state.db
        for k in ("TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY", "JARVIS_SECRET"):
            os.environ.setdefault(k, "test")
        path = os.path.join(AGENTS, "jarvis")
        with isolated_modules(path=path, drop=("agent",)):
            self.agent = load_file(os.path.join(path, "agent.py"), "jarvis_agent_brief_test")

    def test_tasks_are_numbered_in_code(self):
        text, items = self.agent.render_brief({
            "greeting": "Good morning, Gio!",
            "tasks": [{"task": "Call the electrician", "list": "will_due", "when": "urgent"},
                      {"task": " ", "list": "will_due", "when": None},
                      {"task": "Replace the HVAC filter", "list": "andy_mann", "when": None}],
            "flights": None, "closing": "Have a great day."})
        self.assertIn("#1 Call the electrician — urgent\n#2 🏠 Replace the HVAC filter\n", text)
        self.assertEqual([(i["n"], i["list"]) for i in items], [(1, "will_due"), (2, "andy_mann")])
        self.assertNotIn("✈️", text)

    def test_at_most_ten_and_nothing_due(self):
        many = [{"task": f"Task {n}", "list": "will_due", "when": None} for n in range(15)]
        _, items = self.agent.render_brief({"greeting": "Hi", "tasks": many, "flights": None, "closing": ""})
        self.assertEqual(len(items), self.agent.MAX_BRIEF_TASKS)
        text, items = self.agent.render_brief({"greeting": "Hi", "tasks": [], "flights": "No flights.",
                                               "closing": ""})
        self.assertEqual((items, "Nothing due this week" in text, "✈️ No flights." in text), ([], True, True))


if __name__ == "__main__":
    unittest.main()
