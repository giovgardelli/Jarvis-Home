"""Household memory: commands, picking relevant facts, nightly learning, router use."""
import json
from datetime import date
import os
import tempfile
import types
import unittest

from helpers import fake_message
from router_under_test import get_router


def fact(text, topics="", subject="household", web_ok=False):
    return {"action": "add", "id": None, "fact": text, "subject": subject, "topics": topics.split(),
            "web_ok": web_ok, "stated": True, "expires": None}


class FakeClaude:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def create(self, **kwargs):
        self.prompts.append(kwargs["messages"][0]["content"])
        reply = self.reply(self.prompts[-1]) if callable(self.reply) else self.reply
        return fake_message(json.dumps(reply))


class MemoryBase(unittest.TestCase):
    def setUp(self):
        self.r = get_router()
        self.m = self.r.memory
        self._saved = (self.m.DB_PATH, self.m._conn, self.m.ENABLED, self.r.client)
        self.m.DB_PATH = os.path.join(tempfile.mkdtemp(), "memory.db")
        self.m._conn = None
        self.m.ENABLED = True

    def tearDown(self):
        self.m.DB_PATH, self.m._conn, self.m.ENABLED, self.r.client = self._saved

    def claude(self, reply):
        fake = FakeClaude(reply)
        self.r.client = types.SimpleNamespace(messages=fake)
        return fake

    def learn(self, ops, exchanges=(("I'm from Seattle", "Nice!"),)):
        for text, reply in exchanges:
            self.m.record_exchange(text, reply, "voice")
        self.claude({"operations": ops})
        return self.m.consolidate()

    def facts(self, status="active"):
        return [f["fact"] for f in self.m._rows("SELECT * FROM memories WHERE status=? ORDER BY id", (status,))]


class Commands(MemoryBase):
    def test_remember_saves_at_once(self):
        fake = self.claude({"facts": [{"fact": "The household's team is the Mariners", "subject": "household",
                            "topics": ["game", "baseball", "team"], "web_ok": True, "expires": None,
                            "reminder": None}]})
        reply = self.m.handle_command("Jarvis, remember that our team is the Mariners")
        self.assertEqual(reply, "Got it — I'll remember that the household's team is the Mariners.")
        self.assertIn("our team is the Mariners", fake.prompts[0])
        self.assertEqual(self.facts(), ["The household's team is the Mariners"])

    def test_secrets_are_refused_without_asking_claude(self):
        fake = self.claude({})
        reply = self.m.handle_command("remember that the garage code is 4471")
        self.assertIn("rather not keep passwords or codes", reply)
        self.assertEqual(fake.prompts, [])
        self.assertEqual(self.facts(), [])

    def test_forget_best_match_and_list(self):
        self.learn([fact("The household's team is the Mariners", "game"),
                    fact("Kenz's aunt is called Rosa", "aunt")])
        self.assertIn("Mariners", self.m.describe())
        self.assertEqual(self.m.handle_command("forget that our team is the Mariners"),
                         "Done — I've forgotten that the household's team is the Mariners")
        self.assertEqual(self.facts(), ["Kenz's aunt is called Rosa"])
        self.assertIn("couldn't find", self.m.forget("our dog's name"))
        self.assertEqual(self.m.handle_command("forget everything"), "Done — my household memory is now empty.")
        self.assertEqual(self.facts(), [])

    def test_commands_are_not_queued_for_learning(self):
        self.m.record_exchange("what do you remember", "…", "voice")
        self.m.record_exchange("what time is the game", "7pm", "voice", "gio")
        rows = self.m._rows("SELECT * FROM memory_inbox")
        self.assertEqual([(r["user_text"], r["speaker"]) for r in rows], [("what time is the game", "gio")])


class Relevance(MemoryBase):
    def setUp(self):
        super().setUp()
        self.learn([fact("The household's team is the Mariners", "game baseball score", web_ok=True),
                    fact("The household's home city is Seattle", "* weather city", web_ok=True),
                    fact("Kenz's aunt visits on Friday", "aunt visit"),
                    fact("Gio doesn't eat olives", "olives food restaurant", subject="gio")])

    def test_matching_words_and_always_facts(self):
        got = [f["fact"] for f in self.m.relevant("what time is the game tonight")]
        self.assertEqual(got, ["The household's team is the Mariners", "The household's home city is Seattle"])
        got = [f["fact"] for f in self.m.relevant("when does my aunt visit")]
        self.assertEqual(got[0], "Kenz's aunt visits on Friday")

    def test_web_only_and_personal_facts(self):
        self.assertNotIn("Kenz's aunt visits on Friday",
                         [f["fact"] for f in self.m.relevant("is the aunt's visit friday", web_only=True)])
        self.assertEqual([f["fact"] for f in self.m.relevant("book a restaurant")],
                         ["The household's home city is Seattle"], "Gio's facts only when Gio is known")
        self.assertIn("Gio doesn't eat olives",
                      [f["fact"] for f in self.m.relevant("book a restaurant", speakers=("gio",))])

    def test_off_means_nothing(self):
        self.m.ENABLED = False
        self.assertEqual(self.m.relevant("what time is the game"), [])


class NightlyLearning(MemoryBase):
    def test_stated_facts_are_used_patterns_need_a_second_day(self):
        new = self.learn([fact("Kenz's aunt visits on Friday", "aunt"),
                          dict(fact("The household follows the Mariners", "game"), stated=False)])
        self.assertEqual(new, ["Kenz's aunt visits on Friday"])
        self.assertEqual(self.facts("candidate"), ["The household follows the Mariners"])
        pattern_id = self.m._rows("SELECT id FROM memories WHERE status='candidate'")[0]["id"]
        new = self.learn([{"action": "reinforce", "id": pattern_id, "fact": None, "subject": "household",
                           "topics": [], "web_ok": True, "stated": False, "expires": None}],
                         exchanges=(("did the Mariners win", "Yes"),))
        self.assertEqual(new, ["The household follows the Mariners"])
        self.assertEqual(self.facts("candidate"), [])

    def test_inbox_is_emptied_and_prompt_has_the_day(self):
        self.m.record_exchange("my sister Nina is staying with us this week", "Lovely", "telegram", "kenz")
        fake = self.claude({"operations": []})
        self.m.consolidate()
        self.assertIn("kenz: my sister Nina is staying with us this week", fake.prompts[0])
        self.assertEqual(self.m._rows("SELECT * FROM memory_inbox"), [])

    def test_failure_keeps_the_inbox(self):
        self.m.record_exchange("we got a puppy called Biscuit", "Congrats", "voice")
        self.r.client = types.SimpleNamespace(messages=types.SimpleNamespace(
            create=lambda **k: (_ for _ in ()).throw(RuntimeError("down"))))
        with self.assertRaises(RuntimeError):
            self.m.consolidate()
        self.assertEqual(len(self.m._rows("SELECT * FROM memory_inbox")), 1)

    def test_secrets_forgotten_facts_and_expiry(self):
        self.learn([fact("The household's team is the Mariners", "game")])
        self.m.forget("team Mariners")
        self.learn([fact("The household's team is the Mariners", "game"),
                    fact("The wifi password is hunter22", "wifi"),
                    dict(fact("Nina is staying until October 1", "sister"), expires="2020-10-01"),
                    fact("Gio's favourite cuisine is Thai", "food", subject="gio")])
        self.assertEqual([f["fact"] for f in self.m.active_facts()], ["Gio's favourite cuisine is Thai"],
                         "secret dropped, forgotten not relearned, past-dated not used")
        self.m.consolidate()   # each night also retires facts past their date
        self.assertEqual(self.facts("expired"), ["Nina is staying until October 1"])

    def test_learned_since_for_the_brief(self):
        self.learn([fact("The household's home city is Seattle", "*"),
                    fact("Gio's favourite cuisine is Thai", "food", subject="gio")])
        self.assertEqual([f["fact"] for f in self.m.learned_since(speakers=("kenz",))],
                         ["The household's home city is Seattle"])
        self.assertEqual(len(self.m.learned_since(speakers=("gio",))), 2)


def told(text, subject="household", reminder=None):
    return {"fact": text, "subject": subject, "topics": ["date"], "web_ok": False, "expires": None,
            "reminder": reminder}


class Reminders(MemoryBase):
    """Dated facts: Claude plans the reminders once; the brief delivers them."""

    def tell(self, reply):
        fake = self.claude({"facts": [reply]})
        return self.m.handle_command("Jarvis remember " + reply["fact"]), fake

    def test_several_facts_in_one_message(self):
        bday = {"date": "2027-09-14", "yearly": True, "days_before": [7, 0], "remind": ["gio"], "action": None}
        aunt = dict(bday, date="2027-03-22", remind=["gio", "kenz"])
        fake = self.claude({"facts": [told("Kenz's birthday is September 14th", "kenz", bday),
                                      told("Gio's aunt's birthday is March 22", "household", aunt),
                                      told("The household's dog is called Biscuit"),
                                      told("The wifi password is hunter22")]})
        msg = self.m.handle_command("Jarvis remember:\n- Kenz's birthday September 14\n- Gio's aunt March 22\n"
                                    "- our dog is Biscuit")
        self.assertIn("one per person, date or piece of information", fake.prompts[0])
        self.assertIn("- our dog is Biscuit", fake.prompts[0], "multi-line Telegram messages are read whole")
        self.assertEqual(msg, "Got it — I'll remember 3 things: Kenz's birthday is September 14th; Gio's aunt's "
                              "birthday is March 22; and the household's dog is called Biscuit. I'll send "
                              "reminders for those 2 dates. Ask \"what do you remember?\" on Telegram to see when.")
        self.assertEqual(len(self.m.active_facts()), 3, "the password one was dropped")
        self.assertNotIn("Kenz's birthday", self.m.describe(), "unknown speaker: household facts only")
        listing = self.m.describe(("gio",))
        self.assertIn("• [Kenz] Kenz's birthday is September 14th 🔔 Gio: 1 week before and on the day", listing)
        self.assertIn("🔔 Gio and Kenz: 1 week before and on the day", listing)
        self.assertEqual(len(self.m.reminders_for("gio", date(2027, 3, 15))), 1)

    def test_birthday_plan_and_who_gets_reminded(self):
        msg, fake = self.tell(told("Kenz's birthday is September 14th", "kenz", {
            "date": "2027-09-14", "yearly": True, "days_before": [14, 7, 1, 0],
            "remind": ["gio"], "action": "order a gift"}))
        self.assertEqual(msg, "Got it — I'll remember that Kenz's birthday is September 14th. I'll remind Gio "
                              "2 weeks before, 1 week before, the day before and on the day.")
        self.assertIn("NEVER include that person", fake.prompts[0])
        on = lambda d, who="gio": self.m.reminders_for(who, date.fromisoformat(d))
        self.assertEqual(on("2027-08-31"), ["🎂 Kenz's birthday is September 14th — in 2 weeks. Time to order a gift."])
        self.assertEqual(on("2027-09-07"), ["🎂 Kenz's birthday is September 14th — in 1 week. Time to order a gift."])
        self.assertEqual(on("2027-09-13"), ["🎂 Kenz's birthday is September 14th — tomorrow. Time to order a gift."])
        self.assertEqual(on("2027-09-14"), ["🎂 Kenz's birthday is September 14th — today."])
        self.assertEqual(on("2027-09-11"), [], "only on the planned days")
        self.assertEqual(on("2027-09-13", "kenz"), [], "no spoiling the surprise")
        self.assertEqual(on("2028-09-07"), ["🎂 Kenz's birthday is September 14th — in 1 week. Time to order a gift."],
                         "every year")
        self.assertIn("🗓 *Coming up:*", self.m.reminder_note("gio", date(2027, 9, 13)))
        self.assertEqual(self.m.reminder_note("kenz", date(2027, 9, 13)), "")

    def test_gift_safety_when_claude_names_nobody(self):
        self.tell(told("Kenz's birthday is September 14th", "kenz", {
            "date": "2027-09-14", "yearly": True, "days_before": [7], "remind": [], "action": None}))
        self.assertEqual(len(self.m.reminders_for("gio", date(2027, 9, 14))), 1, "the day itself always included")
        self.assertEqual(self.m.reminders_for("kenz", date(2027, 9, 14)), [])

    def test_one_off_visit_expires_and_retelling_replaces(self):
        # Pin "today" before the visit: one-off facts retire once their date
        # passes on the real clock, so this test would expire with the date
        real_now = self.m._now
        self.m._now = lambda: real_now().replace(year=2026, month=10, day=12)
        self.addCleanup(setattr, self.m, "_now", real_now)
        self.tell(told("Kenz's aunt visits on October 20", "household", {
            "date": "2026-10-20", "yearly": False, "days_before": [1, 0], "remind": ["gio", "kenz"],
            "action": "get the guest room ready"}))
        self.assertEqual(self.m.reminders_for("kenz", date(2026, 10, 19)),
                         ["📅 Kenz's aunt visits on October 20 — tomorrow. Time to get the guest room ready."])
        self.assertEqual(self.m.reminders_for("kenz", date(2027, 10, 19)), [], "one-off: not next year")
        self.tell(told("Kenz's aunt visits on October 20", "household", {
            "date": "2026-10-20", "yearly": False, "days_before": [3, 0], "remind": ["gio", "kenz"],
            "action": None}))
        self.assertEqual(len([f for f in self.m.active_facts() if "aunt visits" in f["fact"]]), 1)
        self.assertEqual(self.m.reminders_for("kenz", date(2026, 10, 17)),
                         ["📅 Kenz's aunt visits on October 20 — in 3 days."])

    def test_leap_day_birthday(self):
        self.tell(told("Nina's birthday is February 29", "household", {
            "date": "2028-02-29", "yearly": True, "days_before": [0], "remind": ["gio"], "action": None}))
        self.assertEqual(len(self.m.reminders_for("gio", date(2027, 2, 28))), 1)

    def test_old_database_is_upgraded(self):
        import sqlite3
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        old = sqlite3.connect(path)
        old.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, fact TEXT NOT NULL, "
                    "subject TEXT NOT NULL DEFAULT 'household', topics TEXT NOT NULL DEFAULT '', "
                    "web_ok INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL, status TEXT NOT NULL, "
                    "evidence INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, last_seen TEXT NOT NULL, "
                    "activated TEXT, expires TEXT)")
        old.execute("INSERT INTO memories (fact, source, status, created, last_seen) "
                    "VALUES ('Old fact', 'told', 'active', 'x', 'x')")
        old.commit()
        old.close()
        self.m.DB_PATH, self.m._conn = path, None
        self.assertEqual([f["fact"] for f in self.m.active_facts()], ["Old fact"])
        self.assertEqual(self.m.reminders_for("gio"), [])


class RouterUsesMemory(MemoryBase):
    def setUp(self):
        super().setUp()
        self._saved_web = self.r._web_search
        self.web_calls = []
        self.r._web_search = lambda q, s, v=True, facts=None: self.web_calls.append(facts) or "WEB"
        self.learn([fact("The household's team is the Mariners", "game baseball", web_ok=True),
                    fact("Kenz's aunt visits on Friday", "aunt visit")])

    def tearDown(self):
        self.r._web_search = self._saved_web
        super().tearDown()

    def test_household_question_gets_facts_in_routing_prompt(self):
        fake = self.claude({"agent": "jarvis", "intent": "x", "direct_response": "On Friday."})
        out = self.r.route("when is Kenz's aunt visiting", "gio", channel="telegram", voice_mode=False)
        self.assertEqual(out["response_text"], "On Friday.")
        self.assertIn("- Kenz's aunt visits on Friday", fake.prompts[0])
        self.assertNotIn("Mariners", fake.prompts[0])

    def test_web_search_gets_only_web_ok_facts(self):
        self.claude({"agent": "gemini_search", "intent": "x", "direct_response": None})
        self.r.route("what time is the game", "gio", channel="telegram", voice_mode=False)
        self.assertEqual([f["fact"] for f in self.web_calls[0]], ["The household's team is the Mariners"])

    def test_memory_command_skips_routing(self):
        fake = self.claude({})
        out = self.r.route("what do you remember", None, channel="voice", voice_status="disabled")
        self.assertTrue(out["response_text"].startswith("Here's what I know: "))
        self.assertEqual(fake.prompts, [])

    def test_everything_by_voice_then_text_me_the_full_list(self):
        self.learn([fact(f"Fact number {i} about the family", f"topic{i}") for i in range(10)])
        out = self.r.route("Jarvis, tell me everything you know about my family", None,
                           channel="voice", voice_status="disabled")
        spoken = out["response_text"]
        self.assertTrue(spoken.startswith("Here are the latest 8 of 12 things I know:"), spoken)
        self.assertTrue(spoken.endswith('Say "text me that" for the full list.'))
        self.assertFalse(spoken.rstrip().endswith("?"), "no listening window after the list")
        self.assertIn("Fact number 0 about the family", out["full_response"])
        self.assertIn("Kenz's aunt visits on Friday", out["full_response"])
        again = self.r.route("text me that", None, last_full=out["full_response"],
                             channel="voice", voice_status="disabled")
        self.assertTrue(again["force_text"])
        self.assertEqual(again["response_text"], out["full_response"])

    def test_remember_without_that(self):
        fake = self.claude({"facts": [{"fact": "Kenz's birthday is September 14th", "subject": "kenz",
                            "topics": ["birthday"], "web_ok": False, "expires": None, "reminder": None}]})
        out = self.r.route("Jarvis remember Kenz's birthday is September 14th", None,
                           channel="voice", voice_status="disabled")
        self.assertEqual(out["response_text"], "Got it — I'll remember that Kenz's birthday is September 14th.")
        self.assertIn("Kenz's birthday is September 14th", fake.prompts[0])


if __name__ == "__main__":
    unittest.main()
