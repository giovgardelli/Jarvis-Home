"""The local routing classifier (Track A): learning, labels, shadow and on modes."""
import csv
import os
import tempfile
import types
import unittest

from helpers import fake_message, install_stubs
from router_under_test import get_router

install_stubs()
from jarvis_core.intent_classifier import IntentClassifier, training_examples  # noqa: E402

TRAIN = [
    ("remind me to call the dentist", "will_due"), ("add pay the water bill to my list", "will_due"),
    ("what's on my to do list", "will_due"), ("mark the taxes as done", "will_due"),
    ("when is the hvac filter due", "andy_mann"), ("who's our plumber", "andy_mann"),
    ("add a warranty for the fridge", "andy_mann"), ("when did we last clean the gutters", "andy_mann"),
    ("what's the capital of france", "gemini_search"), ("how tall is the eiffel tower", "gemini_search"),
    ("good morning jarvis", "jarvis"), ("how are you doing", "jarvis"),
]


def write_log(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "text", "agent", "method"])
        for text, agent, method in rows:
            w.writerow(["2026-10-03T10:00:00-07:00", text, agent, method])


class Model(unittest.TestCase):
    def test_learns_household_phrasing(self):
        m = IntentClassifier(TRAIN)
        label, confidence, similarity = m.predict("remind me to call the electrician")
        self.assertEqual(label, "will_due")
        self.assertTrue(0 < confidence <= 1 and 0 < similarity <= 1)
        self.assertEqual(m.predict("when is the furnace filter due")[0], "andy_mann")

    def test_conflicting_history_means_low_confidence(self):
        m = IntentClassifier([("fix the leaking sink", "andy_mann"), ("fix the leaking sink today", "will_due")])
        _, confidence, _ = m.predict("fix the leaking sink")
        self.assertLess(confidence, 0.9, "ambiguous phrasing must not be trusted")

    def test_no_overlap_means_no_guess(self):
        self.assertEqual(IntentClassifier(TRAIN).predict("xylophone zebra"), (None, 0.0, 0.0))
        self.assertEqual(IntentClassifier([]).predict("anything"), (None, 0.0, 0.0))


class TrainingData(unittest.TestCase):
    def test_labels_come_from_claude_and_todays_rules(self):
        path = os.path.join(tempfile.mkdtemp(), "routing_log.csv")
        write_log(path, [
            ("remind me to call mom", "will_due", "haiku"),          # trusted
            ("add bread to the list", "shawn_ping", "local"),        # relabelled by today's rules → same
            ("add call the plumber to my to do", "shawn_ping", "local"),  # old buggy rule → dropped today
            ("something broke", "error", "error"),                    # skipped
            ("remind me to call mom", "andy_mann", "haiku"),          # latest label wins
            ("let me think", "classifier_guess", "classifier"),       # never train on own guesses
        ])
        rules = {"add bread to the list": "shawn_ping"}
        agents = [{"id": "will_due", "example_phrases": ["what are my tasks"]}]
        examples = training_examples(path, agents, {"will_due", "andy_mann", "shawn_ping"}, rules.get)
        self.assertEqual(sorted(examples), sorted([
            ("remind me to call mom", "andy_mann"), ("add bread to the list", "shawn_ping"),
            ("what are my tasks", "will_due")]))


class FakeClaude:
    def __init__(self, agent):
        self.agent, self.calls = agent, 0

    def create(self, **kwargs):
        self.calls += 1
        import json
        return fake_message(json.dumps({"agent": self.agent, "intent": "x", "direct_response": "Hello."}))


class RouterModes(unittest.TestCase):
    def setUp(self):
        self.r = get_router()
        self.saved = (self.r.client, self.r.dispatch, self.r.CLASSIFIER_MODE)
        write_log(self.r.ROUTING_LOG_PATH, [(t, a, "haiku") for t, a in TRAIN])
        self.r.classifier._model = None          # retrain on the log above
        self.dispatched = []
        self.r.dispatch = lambda a, *args, **k: self.dispatched.append(a) or {"response_text": "ok", "voice_text": "ok"}
        if os.path.exists(self.r.ROUTER_SHADOW_PATH):
            os.remove(self.r.ROUTER_SHADOW_PATH)

    def tearDown(self):
        self.r.client, self.r.dispatch, self.r.CLASSIFIER_MODE = self.saved
        self.r.classifier._model = None

    def shadow_rows(self):
        with open(self.r.ROUTER_SHADOW_PATH, newline="") as f:
            return list(csv.DictReader(f))

    def test_shadow_logs_a_real_prediction_and_claude_still_decides(self):
        self.r.CLASSIFIER_MODE = "shadow"
        fake = FakeClaude("will_due")
        self.r.client = types.SimpleNamespace(messages=fake)
        self.r.route("remind me to call the electrician", "gio")
        self.assertEqual(fake.calls, 1, "Claude still routes in shadow mode")
        row = self.shadow_rows()[0]
        self.assertEqual((row["predicted"], row["claude_agent"]), ("will_due", "will_due"))
        self.assertGreater(float(row["confidence"]), 0)

    def test_on_mode_skips_claude_only_when_confident(self):
        self.r.CLASSIFIER_MODE = "on"
        fake = FakeClaude("andy_mann")
        self.r.client = types.SimpleNamespace(messages=fake)
        self.r.route("remind me to call the dentist", "gio")          # seen before: confident
        self.assertEqual((fake.calls, self.dispatched), (0, ["will_due"]))
        self.r.route("xylophone zebra quartet", "gio")                # unknown: Claude decides
        self.assertEqual(fake.calls, 1)

    def test_on_mode_leaves_small_talk_and_task_hints_to_claude(self):
        self.r.CLASSIFIER_MODE = "on"
        fake = FakeClaude("jarvis")
        self.r.client = types.SimpleNamespace(messages=fake)
        out = self.r.route("good morning jarvis", "gio")
        self.assertEqual((fake.calls, out["response_text"]), (1, "Hello."), "small talk needs Claude's reply")
        fake = FakeClaude("will_due")
        self.r.client = types.SimpleNamespace(messages=fake)
        self.r.route("I finished the taxes", "gio",
                     task_sources={"will_due": "do the taxes", "andy_mann": ""})
        self.assertEqual(fake.calls, 1, "completed-task hints go to Claude")

    def test_off_mode(self):
        self.r.CLASSIFIER_MODE = "off"
        self.r.client = types.SimpleNamespace(messages=FakeClaude("will_due"))
        self.r.route("remind me to call the electrician", "gio")
        self.assertFalse(os.path.exists(self.r.ROUTER_SHADOW_PATH))


if __name__ == "__main__":
    unittest.main()
