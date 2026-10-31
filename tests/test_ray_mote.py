"""Ray Mote (Nest thermostats): what's sent to Nest, the 68–75°F limit, the
"Upstairs or downstairs?" question, and the router passing the answer back."""
import os
import types
import unittest

from helpers import AGENTS, install_stubs, isolated_modules, load_file
from router_under_test import get_router

PATH = os.path.join(AGENTS, "ray_mote")


class FakeNest(types.ModuleType):
    """The nest module with two thermostats and a log of every command."""

    class NestError(Exception):
        pass

    def __init__(self):
        super().__init__("nest")
        self.calls = []
        self.devices = [self.device("Upstairs"), self.device("Downstairs")]

    @staticmethod
    def device(label, **kw):
        d = {"name": f"enterprises/p/devices/{label}", "label": label, "room": label, "online": True,
             "mode": "HEAT", "modes": ["HEAT", "COOL", "HEATCOOL", "OFF"], "eco": False, "hvac": "OFF",
             "ambient_f": 67.0, "heat_f": 69.0, "cool_f": None}
        d.update(kw)
        return d

    def thermostats(self, fresh=False):
        return self.devices

    def _log(name):
        def call(self, device, *args):
            self.calls.append((name, device["label"]) + args)
        return call

    set_mode, set_eco, set_heat, set_cool, set_range = (
        _log("mode"), _log("eco"), _log("heat"), _log("cool"), _log("range"))


def load_agent(nest):
    install_stubs()
    os.environ.setdefault("ANTHROPIC_API_KEY", "test")
    with isolated_modules({"nest": nest}, path=PATH):
        return load_file(os.path.join(PATH, "agent.py"), "ray_mote_agent_under_test")


class RayMote(unittest.TestCase):
    def setUp(self):
        self.nest = FakeNest()
        self.agent = load_agent(self.nest)
        self.agent.claude_parse = self.no_claude

    def no_claude(self, text, devices):
        self.fail(f"Claude called for {text!r}: common requests are parsed in code")

    def say(self, text):
        return self.agent.process_message(text, "gio")

    def test_set_one_floor(self):
        out = self.say("set upstairs to 70")
        self.assertEqual(self.nest.calls, [("heat", "Upstairs", 70)])
        self.assertEqual(out["response_text"], "Upstairs is set to 70.")
        self.assertEqual(out["voice_text"], out["response_text"], "spoken as is")

    def test_no_floor_asks_then_uses_the_answer(self):
        out = self.say("set it to 72")
        self.assertEqual((out["status"], out["response_text"]), ("question", "Upstairs or downstairs?"))
        self.assertIn("downstairs", out["answers"])
        self.assertEqual(self.nest.calls, [], "nothing changes before the answer")
        out = self.say("downstairs")
        self.assertEqual(self.nest.calls, [("heat", "Downstairs", 72)])
        self.assertEqual(out["response_text"], "Downstairs is set to 72.")

    def test_both(self):
        out = self.say("set both to 69")
        self.assertEqual(self.nest.calls, [("heat", "Upstairs", 69), ("heat", "Downstairs", 69)])
        self.assertEqual(out["response_text"], "Upstairs and downstairs are set to 69.")

    def test_outside_the_limits_asks_first(self):
        out = self.say("set upstairs to 80")
        self.assertEqual(out["response_text"], "I can only set between 68 and 75. Set upstairs to 75?")
        self.assertEqual(self.nest.calls, [])
        self.say("yes")
        self.assertEqual(self.nest.calls, [("heat", "Upstairs", 75)])

    def test_outside_the_limits_no_leaves_it(self):
        self.say("set downstairs to 60")
        self.assertEqual(self.say("no")["response_text"], "Okay, I'll leave it as it is.")
        self.assertEqual(self.nest.calls, [])

    def test_a_question_expires_with_a_new_request(self):
        self.say("set it to 72")
        self.say("what's the temperature upstairs")
        self.agent.claude_parse = lambda text, devices: self.agent._command("unsupported")
        self.say("downstairs")
        self.assertEqual(self.nest.calls, [], "the old question was dropped")

    def test_warmer_stops_at_the_limit(self):
        self.nest.devices[0]["heat_f"] = 74.0
        self.assertEqual(self.say("make upstairs warmer")["response_text"], "Upstairs is set to 75.")
        self.nest.devices[0]["heat_f"] = 75.0
        self.assertEqual(self.say("make upstairs warmer")["response_text"],
                         "Upstairs is already at 75, the most I can set.")
        self.assertEqual(self.nest.calls, [("heat", "Upstairs", 75)])

    def test_cooler_in_cool_mode(self):
        self.nest.devices[1].update(mode="COOL", heat_f=None, cool_f=74.0)
        self.say("make it a bit cooler downstairs")
        self.assertEqual(self.nest.calls, [("cool", "Downstairs", 73)])

    def test_heat_and_cool_keeps_a_band_inside_the_limits(self):
        self.nest.devices[0].update(mode="HEATCOOL", heat_f=68.0, cool_f=74.0)
        out = self.say("set upstairs to 74")
        self.assertEqual(self.nest.calls, [("range", "Upstairs", 72, 75)])
        self.assertEqual(out["response_text"], "Upstairs is kept between 72 and 75.")

    def test_eco_mode_is_turned_off_before_setting(self):
        self.nest.devices[0]["eco"] = True
        self.say("set upstairs to 70")
        self.assertEqual(self.nest.calls, [("eco", "Upstairs", False), ("heat", "Upstairs", 70)])

    def test_system_off_is_left_alone(self):
        self.nest.devices[0]["mode"] = "OFF"
        out = self.say("set upstairs to 70")
        self.assertEqual(self.nest.calls, [])
        self.assertIn("switched off", out["response_text"])

    def test_the_ac_set_point_is_not_the_heat_one(self):
        out = self.say("set the AC upstairs to 72")
        self.assertEqual(self.nest.calls, [])
        self.assertEqual(out["response_text"], 'Upstairs is on heat, so I left it. Say "turn on the AC" first.')
        self.say("set the heat upstairs to 72")
        self.assertEqual(self.nest.calls, [("heat", "Upstairs", 72)])

    def test_modes_and_eco(self):
        self.say("turn on the AC upstairs")
        self.say("turn off the heat downstairs")
        self.say("put both in eco mode")
        self.assertEqual(self.nest.calls, [
            ("mode", "Upstairs", "COOL"), ("mode", "Downstairs", "OFF"),
            ("eco", "Upstairs", True), ("eco", "Downstairs", True)])

    def test_status_covers_every_floor_without_asking(self):
        self.nest.devices[1].update(hvac="HEATING", heat_f=70.0, ambient_f=66.4)
        out = self.say("what's the thermostat set to")
        self.assertEqual(out["response_text"],
                         "Upstairs is 67 degrees, set to 69. Downstairs is 66 degrees, heating to 70.")
        self.assertEqual(self.nest.calls, [])

    def test_offline_and_errors_are_said_plainly(self):
        self.nest.devices[0]["online"] = False
        self.assertEqual(self.say("set upstairs to 70")["response_text"],
                         "Upstairs is offline, so I couldn't change it.")

        def fail(device, f):
            raise self.nest.NestError("Nest didn't accept that change.")
        self.nest.set_heat = fail
        out = self.say("set downstairs to 70")
        self.assertEqual((out["status"], out["response_text"]), ("error", "Nest didn't accept that change."))

    def test_schedules_are_not_done_now(self):
        out = self.say("turn on the heat at 7pm")
        self.assertEqual(out["status"], "unsupported")
        self.assertEqual(self.nest.calls, [])

    def test_unusual_phrasing_goes_to_claude_and_limits_still_apply(self):
        calls = []

        def create(**k):
            calls.append(k)
            return types.SimpleNamespace(stop_reason="end_turn", content=[types.SimpleNamespace(
                type="text", text='{"action": "set", "zone": "upstairs", "target_f": 90, '
                                  '"delta_f": null, "mode": null, "eco": null}')])
        agent = load_agent(self.nest)
        agent.client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
        out = agent.process_message("make upstairs nice and toasty for movie night", "gio")
        self.assertEqual(calls[0]["extra_body"]["thinking"], {"type": "disabled"})
        self.assertEqual(out["response_text"], "I can only set between 68 and 75. Set upstairs to 75?")

    def test_zone_aliases(self):
        self.nest.devices = [self.nest.device("Hallway"), self.nest.device("Living Room")]
        os.environ["NEST_ZONES"] = "upstairs=Hallway, downstairs=Living Room"
        try:
            self.say("set downstairs to 70")
        finally:
            del os.environ["NEST_ZONES"]
        self.assertEqual(self.nest.calls, [("heat", "Living Room", 70)])


class RouterPassesTheAnswerBack(unittest.TestCase):
    def setUp(self):
        self.r = get_router()
        self.saved = (self.r.dispatch, self.r._route_with_haiku)
        self.sent = []

        def dispatch(agent_id, intent, params, speaker, voice_mode=True, chat_id=0):
            self.sent.append((agent_id, params["original_message"]))
            if len(self.sent) == 1:
                return {"response_text": "Upstairs or downstairs?", "voice_text": "Upstairs or downstairs?",
                        "status": "question", "answers": ["upstairs", "downstairs", "both"], "tasks": []}
            return {"response_text": "Downstairs is set to 72.", "voice_text": None, "status": "done", "tasks": []}
        self.r.dispatch = dispatch
        self.r._route_with_haiku = lambda *a, **k: self.fail("answer went through routing")

    def tearDown(self):
        self.r.dispatch, self.r._route_with_haiku = self.saved
        self.r._pending_agent.clear()

    def test_short_answer_goes_to_the_agent_that_asked(self):
        self.r._route_with_haiku = lambda *a, **k: ("ray_mote", "set to 72", None, "local")
        out = self.r.route("set it to 72", None)
        self.assertEqual(out["response_text"], "Upstairs or downstairs?")
        self.r._route_with_haiku = lambda *a, **k: self.fail("answer went through routing")
        out = self.r.route("downstairs please", None)
        self.assertEqual(self.sent, [("ray_mote", "set it to 72"), ("ray_mote", "downstairs please")])
        self.assertEqual(out["response_text"], "Downstairs is set to 72.")

    def test_anything_else_is_a_new_request(self):
        self.r._route_with_haiku = lambda *a, **k: ("jarvis", "chat", "Certainly.", "haiku")
        for text in ("the upstairs neighbours are very loud tonight", "hello"):
            self.r._pending_agent["voice"] = {"agent": "ray_mote", "answers": ["upstairs"], "expires": 1e12}
            self.assertEqual(self.r.route(text, None)["response_text"], "Certainly.")
            self.assertNotIn("voice", self.r._pending_agent)
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
