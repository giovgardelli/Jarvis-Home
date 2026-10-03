"""route() end to end with a fake Haiku, fake agents and fake web search."""
import json
import time
import types
import unittest

from helpers import fake_message
from router_under_test import get_router

LONG = " ".join(["word"] * 60)


class FakeClaude:
    """Answers routing / merge / shortening prompts; records which were made."""

    def __init__(self, route=None, merge_text=None, fail=False):
        self.route = route or {"agent": "will_due", "intent": "x", "params": {}, "direct_response": None}
        self.merge_text = merge_text
        self.fail = fail
        self.timer = None
        self.calls = []
        self.requests = []

    def create(self, **kwargs):
        if self.fail:
            raise RuntimeError("API down")
        self.requests.append(kwargs)
        prompt = kwargs["messages"][0]["content"]
        if "Merge the following" in prompt:
            self.calls.append("merge")
            return fake_message(self.merge_text)
        if "Rewrite the following" in prompt:
            self.calls.append("shorten")
            return fake_message("SHORTENED")
        if "timer/alarm request" in prompt:
            self.calls.append("timer")
            return fake_message(json.dumps(self.timer or {"action": "list", "name": None, "seconds": None, "alarm_time": None}))
        self.calls.append("route")
        return fake_message(json.dumps(self.route))


class RouteFlowBase(unittest.TestCase):
    """Fake Haiku, fake agents (self.agent_replies), fake web search."""

    def setUp(self):
        self.r = get_router()
        self._saved = (self.r.client, self.r.dispatch, self.r._web_search)
        self.dispatched = []
        self.dispatch_speakers = []
        self.dispatch_intents = []
        self.r._pending_owner.clear()
        self.agent_replies = {}

        def dispatch(agent_id, intent, params, speaker, voice_mode=True, chat_id=0):
            self.dispatched.append(agent_id)
            self.dispatch_speakers.append(speaker)
            self.dispatch_intents.append(params.get("original_message"))
            self.dispatch_kwargs = {"voice_mode": voice_mode, "chat_id": chat_id}
            time.sleep(0.2)
            return self.agent_replies.get(agent_id, {"response_text": f"{agent_id} says " + LONG, "voice_text": None})
        self.r.dispatch = dispatch
        self.r._web_search = lambda q, s, *a: f"WEB:{q}"

    def tearDown(self):
        self.r.client, self.r.dispatch, self.r._web_search = self._saved

    def use(self, fake):
        self.r.client = types.SimpleNamespace(messages=fake)
        return fake


class RouteFlow(RouteFlowBase):
    def test_small_talk_gets_haikus_reply(self):
        self.use(FakeClaude(route={"agent": "jarvis", "intent": "greet", "params": {}, "direct_response": "Good evening, Gio."}))
        self.assertEqual(self.r.route("hey how are you doing", "gio")["response_text"], "Good evening, Gio.")

    def test_haiku_can_pick_web_search(self):
        self.use(FakeClaude(route={"agent": "gemini_search", "intent": "q", "params": {}, "direct_response": None}))
        self.assertEqual(self.r.route("what is the capital of France", "gio")["response_text"],
                         "WEB:what is the capital of France")

    def test_agent_request_with_history(self):
        fake = self.use(FakeClaude())
        self.agent_replies["will_due"] = {"response_text": "Added it.", "voice_text": None}
        out = self.r.route("remind me to call the dentist", "gio", history=[{"role": "user", "content": "hi"}])
        self.assertEqual(out["response_text"], "Added it.")
        self.assertEqual(self.dispatched, ["will_due"])
        self.assertEqual(fake.calls, ["route"])

    def test_haiku_outage_is_not_a_fake_success(self):
        self.use(FakeClaude(fail=True))
        out = self.r.route("remind me to call the dentist", "gio")
        self.assertNotEqual(out["response_text"], "Understood.")
        self.assertIn("didn't manage", out["response_text"])
        self.assertEqual(self.dispatched, [])

    def test_agent_voice_text_skips_shortening(self):
        fake = self.use(FakeClaude())
        self.agent_replies["will_due"] = {"response_text": "Full list " + LONG, "voice_text": "Your top task is the dentist."}
        out = self.r.route("remind me to call the dentist", "gio")
        self.assertEqual(out["response_text"], "Your top task is the dentist.")
        self.assertTrue(out["full_response"].startswith("Full list"))
        self.assertEqual(fake.calls, ["route"])

    def test_long_reply_without_voice_text_is_shortened(self):
        fake = self.use(FakeClaude())
        out = self.r.route("remind me to call the dentist", "gio")
        self.assertEqual(out["response_text"], "SHORTENED")
        self.assertEqual(fake.calls, ["route", "shorten"])

    def test_multi_agent_is_parallel_with_one_merge_call(self):
        fake = self.use(FakeClaude(merge_text="*Tasks*\n• Dentist\nVOICE|Call the dentist and change the filter."))
        start = time.time()
        out = self.r.route("what are my priorities", "gio")
        self.assertLess(time.time() - start, 0.35, "agents should be asked in parallel")
        self.assertEqual(sorted(self.dispatched), ["andy_mann", "will_due"])
        self.assertEqual(out["response_text"], "Call the dentist and change the filter.")
        self.assertTrue(out["full_response"].startswith("*Tasks*"))
        self.assertEqual(fake.calls, ["merge"])

    def test_text_me_and_hear_more(self):
        self.use(FakeClaude())
        out = self.r.route("text me that", "gio", last_full="the full list")
        self.assertEqual((out["response_text"], out["force_text"]), ("the full list", True))
        out = self.r.route("tell me more", "gio", last_full="short")
        self.assertEqual(out["response_text"], "short")

    def test_shopping_fast_path_skips_haiku(self):
        fake = self.use(FakeClaude())
        self.agent_replies["shawn_ping"] = {"response_text": "Added eggs.", "voice_text": None}
        self.assertEqual(self.r.route("add eggs to the list", "gio")["response_text"], "Added eggs.")
        self.assertEqual(fake.calls, [])


class StructuredOutputs(unittest.TestCase):
    def setUp(self):
        self.r = get_router()
        self._saved = (self.r.client, self.r.timer_manager)

    def tearDown(self):
        self.r.client, self.r.timer_manager = self._saved

    def test_routing_schema_allows_only_registry_agents(self):
        from jarvis_core.registry import agent_ids
        fake = FakeClaude(route={"agent": "jarvis", "intent": "greet", "direct_response": "Hello."})
        self.r.client = types.SimpleNamespace(messages=fake)
        self.r.route("hello there jarvis", "gio")
        fmt = fake.requests[0]["extra_body"]["output_config"]["format"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertEqual(fmt["schema"]["properties"]["agent"]["enum"], agent_ids() + ["gemini_search", "jarvis"])

    def test_alarm_time_resolves_to_next_occurrence(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        pt = ZoneInfo("America/Los_Angeles")
        set_calls = []
        self.r.timer_manager = types.SimpleNamespace(set_alarm=lambda name, ts: set_calls.append((name, ts)) or "ok")
        fake = FakeClaude()
        fake.timer = {"action": "set_alarm", "name": None, "seconds": None, "alarm_time": "07:30"}
        self.r.client = types.SimpleNamespace(messages=fake)
        self.r.route("set an alarm for 7:30", "gio")
        name, ts = set_calls[0]
        target = datetime.fromtimestamp(ts, pt)
        now = datetime.now(pt)
        self.assertEqual((name, target.hour, target.minute), ("alarm", 7, 30))
        self.assertGreater(target, now)
        self.assertLess((target - now).total_seconds(), 24 * 3600 + 1)
        self.assertIn("output_config", fake.requests[0]["extra_body"])


class TelegramAndTaskHints(RouteFlowBase):
    """Features from the old Telegram router, now shared."""

    def test_telegram_gets_full_text_and_no_timers(self):
        fake = self.use(FakeClaude())
        out = self.r.route("remind me to call the dentist", "gio", voice_mode=False, channel="telegram", chat_id=42)
        self.assertTrue(out["response_text"].startswith("will_due says"), "Telegram gets the full reply")
        self.assertEqual(fake.calls, ["route"], "no shortening on Telegram")
        self.assertEqual(self.dispatch_kwargs, {"voice_mode": False, "chat_id": 42})
        out = self.r.route("set a timer for 10 minutes", "gio", voice_mode=False, channel="telegram")
        self.assertIn("speaker", out["response_text"])

    def test_completed_task_hint_and_retry_on_the_other_list(self):
        fake = self.use(FakeClaude(route={"agent": "will_due", "intent": "mark gutter done", "direct_response": None}))
        self.agent_replies["will_due"] = {"response_text": "I don't see that one.", "status": "not_found", "voice_text": None}
        self.agent_replies["andy_mann"] = {"response_text": "Marked gutter cleaning done.", "status": "completed",
                                           "voice_text": "Done, gutters are marked as cleaned."}
        sources = {"will_due": "Call dentist", "andy_mann": "Clean the gutters before winter"}
        out = self.r.route("I cleaned the gutters", "gio", task_sources=sources)
        self.assertEqual(self.dispatched, ["will_due", "andy_mann"])
        self.assertEqual(out["response_text"], "Done, gutters are marked as cleaned.")
        self.assertIn("STRONG HINT", fake.requests[0]["messages"][0]["content"])
        self.assertIn("andy_mann", fake.requests[0]["messages"][0]["content"])

    def test_structured_lists_are_combined_in_code(self):
        fake = self.use(FakeClaude())
        self.agent_replies["will_due"] = {"response_text": "prose", "status": "list", "voice_text": None,
                                          "tasks": [{"task": "Call dentist", "priority": "High", "score": 1}]}
        self.agent_replies["andy_mann"] = {"response_text": "prose", "status": "list", "voice_text": None,
                                           "tasks": [{"task": "Change HVAC filter", "score": 2},
                                                     {"task": "Old job", "status": "Done"}]}
        out = self.r.route("what are my priorities", "gio", voice_mode=False, channel="telegram")
        self.assertEqual(out["response_text"],
                         "📋 *Your combined list:*\n\n• Call dentist _High_\n• Change HVAC filter 🏠")
        self.assertEqual(fake.calls, [], "no LLM involved, so nothing can be invented")


class OwnerQuestions(RouteFlowBase):
    """Voice ID off (speaker=None): only the to-do list needs to know who's asking."""

    TODO = {"agent": "will_due", "intent": "add task", "direct_response": None}

    def test_name_in_request_is_used(self):
        self.use(FakeClaude(route=self.TODO))
        self.r.route("add call the plumber to Kenz's to do list", None)
        self.assertEqual((self.dispatched, self.dispatch_speakers), (["will_due"], ["kenz"]))

    def test_shared_words_need_no_question(self):
        self.use(FakeClaude(route=self.TODO))
        self.r.route("add plan the trip to our to do list", None)
        self.assertEqual(self.dispatched, ["will_due"])

    def test_asks_then_runs_original_for_the_answer(self):
        self.use(FakeClaude(route=self.TODO))
        out = self.r.route("add call the plumber to my to do list", None)
        self.assertEqual(out["response_text"], "Is that for Gio or Kenz?")
        self.assertEqual(self.dispatched, [], "nothing runs before the answer")
        self.r.route("it's for Kenz", None)
        self.assertEqual(self.dispatch_speakers, ["kenz"])
        self.assertEqual(self.dispatch_intents, ["add call the plumber to my to do list"])

    def test_answer_both_makes_it_shared(self):
        self.use(FakeClaude(route=self.TODO))
        self.r.route("add call the plumber to the to do list", None)
        self.r.route("both of us", None)
        self.assertEqual(self.dispatched, ["will_due"])
        self.assertIn("shared", self.dispatch_intents[0])

    def test_priorities_ask_too(self):
        self.use(FakeClaude(merge_text="merged\nVOICE|spoken"))
        self.assertEqual(self.r.route("what are my priorities", None)["response_text"], "Is that for Gio or Kenz?")
        self.r.route("Gio", None)
        self.assertEqual(sorted(self.dispatched), ["andy_mann", "will_due"])
        self.assertEqual(self.dispatch_speakers, ["gio", "gio"])

    def test_a_new_long_request_is_not_an_answer(self):
        self.use(FakeClaude(route=self.TODO))
        self.r.route("add call the plumber to my to do list", None)
        self.agent_replies["shawn_ping"] = {"response_text": "Added milk.", "voice_text": None}
        self.r.route("add milk to the list for Kenz please right now", None)
        self.assertEqual(self.dispatched, ["shawn_ping"], "the old to-do request must not re-run")

    def test_other_agents_never_ask(self):
        self.use(FakeClaude(route={"agent": "andy_mann", "intent": "x", "direct_response": None}))
        self.r.route("when is the furnace filter due", None)
        self.assertEqual((self.dispatched, self.dispatch_speakers), (["andy_mann"], ["gio"]))

    def test_known_speaker_never_asks(self):
        self.use(FakeClaude(route=self.TODO))
        self.r.route("add call the plumber to my to do list", "kenz", voice_mode=False, channel="telegram")
        self.assertEqual(self.dispatch_speakers, ["kenz"])


class SensitivityTiers(RouteFlowBase):
    """Voice requests are gated by the destination's tier (registry "access")."""

    def route_to(self, agent, status, text="do the thing"):
        self.use(FakeClaude(route={"agent": agent, "intent": "x", "direct_response": "Hi."}))
        self.dispatched.clear()
        return self.r.route(text, None, voice_status=status)

    def test_tier_matrix(self):
        cases = [
            # (destination, voice status, allowed?)
            ("gemini_search", "unauthorized", True),     # public
            ("jarvis", "unauthorized", True),
            ("shawn_ping", "uncertain", True),           # household: "probably household" is enough
            ("shawn_ping", "unauthorized", False),
            ("will_due", "uncertain", False),            # personal: confirmed only
            ("will_due", "unauthorized", False),
            ("drew_parture", "confirmed", True),
            ("will_due", "disabled", True),              # voice ID off: no gating
        ]
        for agent, status, allowed in cases:
            with self.subTest(agent=agent, status=status):
                out = self.route_to(agent, status, text="add milk to Kenz's list please" if agent == "will_due" else "do the thing")
                self.assertEqual("gate" not in out, allowed, out)
                if not allowed:
                    self.assertEqual(self.dispatched, [], "a blocked request never reaches the agent")

    def test_blocked_reply_kind(self):
        self.assertEqual(self.route_to("will_due", "unauthorized")["gate"], "unauthorized")
        self.assertEqual(self.route_to("will_due", "uncertain")["gate"], "repeat")
        self.assertEqual(self.route_to("will_due", "error")["gate"], "repeat")

    def test_replaying_last_answer_is_personal(self):
        self.use(FakeClaude())
        out = self.r.route("text me that", None, last_full="your private list", voice_status="unauthorized")
        self.assertEqual(out["gate"], "unauthorized")
        out = self.r.route("tell me more", None, last_full="your private list", voice_status="uncertain")
        self.assertEqual(out["gate"], "repeat")

    def test_public_timer_and_search_for_guests(self):
        self.use(FakeClaude())
        out = self.r.route("search for the best pizza in LA", None, voice_status="unauthorized")
        self.assertTrue(out["response_text"].startswith("WEB:"))

    def test_priorities_take_the_strictest_tier(self):
        self.use(FakeClaude(merge_text="m\nVOICE|v"))
        out = self.r.route("what are my priorities for Gio", None, voice_status="uncertain")
        self.assertEqual(out.get("gate"), "repeat", "includes the personal to-do list")

    def test_telegram_is_never_gated(self):
        self.use(FakeClaude(route={"agent": "will_due", "intent": "x", "direct_response": None}))
        out = self.r.route("what's on my to do list", "kenz", voice_mode=False, channel="telegram")
        self.assertNotIn("gate", out)


class PartialInstalls(RouteFlowBase):
    def test_fan_out_skips_agents_not_installed(self):
        from jarvis_core import registry
        saved = list(registry.AGENTS)
        registry.AGENTS[:] = [a for a in saved if a["id"] != "andy_mann"]
        try:
            self.use(FakeClaude())
            self.agent_replies["will_due"] = {"response_text": "Call dentist.", "voice_text": "Call the dentist."}
            out = self.r.route("what are my priorities", "gio")
            self.assertEqual(self.dispatched, ["will_due"])
            self.assertEqual(out["response_text"], "Call the dentist.")
        finally:
            registry.AGENTS[:] = saved


class DispatchErrors(unittest.TestCase):
    def test_error_messages(self):
        r = get_router()
        import requests
        saved = r.requests.post
        try:
            def raise_401(*a, **k):
                resp = types.SimpleNamespace(status_code=401, ok=False, __bool__=lambda self: False)
                raise requests.exceptions.HTTPError("401", response=resp)
            r.requests.post = raise_401
            self.assertIn("check JARVIS_SECRET", r.dispatch("will_due", "x", {}, "gio")["response_text"])

            def timeout(*a, **k):
                raise requests.exceptions.Timeout()
            r.requests.post = timeout
            self.assertIn("taking too long", r.dispatch("will_due", "x", {}, "gio")["response_text"])
        finally:
            r.requests.post = saved


class CentralServiceWrapper(unittest.TestCase):
    def test_jarvis_respond_uses_shared_router_as_telegram(self):
        import os
        from helpers import AGENTS, isolated_modules, load_file
        r = get_router()
        with isolated_modules(path=os.path.join(AGENTS, "jarvis")):
            agent = load_file(os.path.join(AGENTS, "jarvis", "agent.py"), "central_agent_under_test")
        seen = {}
        saved = r.route
        r.route = lambda *a, **k: seen.update(args=a, kwargs=k) or {"response_text": "ok"}
        try:
            agent.jarvis_respond("hello", "kenz", 7, history=[], last_full_response=None, task_sources={})
        finally:
            r.route = saved
        self.assertEqual(seen["args"], ("hello", "kenz"))
        self.assertEqual(seen["kwargs"]["channel"], "telegram")
        self.assertEqual(seen["kwargs"]["chat_id"], 7)


if __name__ == "__main__":
    unittest.main()
