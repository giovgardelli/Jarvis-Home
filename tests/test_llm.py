"""Haiku 5.5: one model setting, thinking off where someone waits, replies read
by block type (thinking blocks first, refusals) — and Haiku 4.5 still works."""
import json
import os
import types
import unittest

from helpers import AGENTS, install_stubs, isolated_modules, load_file
from router_under_test import get_router


def block(kind, text=""):
    return types.SimpleNamespace(type=kind, text=text) if kind == "text" else \
        types.SimpleNamespace(type=kind, thinking="", signature="sig")


def reply(text, thinking=True, stop_reason="end_turn"):
    content = ([block("thinking")] if thinking else []) + ([block("text", text)] if text is not None else [])
    return types.SimpleNamespace(content=content, stop_reason=stop_reason, stop_details=None)


class Helpers(unittest.TestCase):
    def setUp(self):
        get_router()
        from jarvis_core import llm
        self.llm = llm

    def test_default_model_and_fast_calls(self):
        if not os.environ.get("CLAUDE_MODEL"):
            self.assertEqual(self.llm.MODEL, "claude-haiku-5-5")
        self.assertEqual(self.llm.fast(), {"thinking": {"type": "disabled"}})
        self.assertEqual(self.llm.fast({"output_config": 1}),
                         {"thinking": {"type": "disabled"}, "output_config": 1})

    def test_text_skips_thinking_and_handles_refusals(self):
        self.assertEqual(self.llm.text(reply(" Added butter. ")), "Added butter.")
        self.assertEqual(self.llm.text(reply("Added butter.", thinking=False)), "Added butter.")
        self.assertEqual(self.llm.text(reply(None, stop_reason="refusal")), self.llm.REFUSAL_TEXT)


class RouterOnHaiku55(unittest.TestCase):
    def setUp(self):
        self.r = get_router()
        self.saved = (self.r.client, self.r.dispatch)
        self.calls = []

        def create(**k):
            self.calls.append(k)
            return reply(json.dumps({"agent": "jarvis", "intent": "hi", "direct_response": "Hello, Gio."}))
        self.r.client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))

    def tearDown(self):
        self.r.client, self.r.dispatch = self.saved

    def test_routing_call_is_fast_and_reads_past_thinking(self):
        out = self.r.route("hello there jarvis", "gio", channel="telegram", voice_mode=False)
        self.assertEqual(out["response_text"], "Hello, Gio.")
        call = self.calls[0]
        self.assertEqual(call["model"], self.r.ROUTER_MODEL)
        self.assertEqual(call["extra_body"]["thinking"], {"type": "disabled"})
        self.assertIn("output_config", call["extra_body"], "structured output kept")

    def test_refusal_is_an_honest_failure_not_a_crash(self):
        self.r.client = types.SimpleNamespace(messages=types.SimpleNamespace(
            create=lambda **k: reply(None, stop_reason="refusal")))
        out = self.r.route("hello there jarvis", "gio", channel="telegram", voice_mode=False)
        self.assertEqual(out["response_text"], "Sorry, I didn't manage to process that. Please try again.")


class AgentsOnHaiku55(unittest.TestCase):
    def test_shopping_agent_reads_past_thinking_and_refusals(self):
        install_stubs()
        os.environ.setdefault("ANTHROPIC_API_KEY", "test")
        sheets = types.ModuleType("sheets")
        for name in ("get_shopping_list", "add_item", "remove_items", "remove_item_by_name", "get_similar_items"):
            setattr(sheets, name, lambda *a, **k: [])
        with isolated_modules({"sheets": sheets}, path=os.path.join(AGENTS, "shawn_ping")):
            agent = load_file(os.path.join(AGENTS, "shawn_ping", "agent.py"), "shawn_agent_llm_test")
        calls = []
        replies = [reply("The list is empty.\nVOICE|Your list is empty."), reply(None, stop_reason="refusal")]

        def create(**k):
            calls.append(k)
            return replies[len(calls) - 1]
        agent.client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
        r = agent.process_message("what's on the list", "Gio", voice_mode=True)
        self.assertEqual((r["response_text"], r["voice_text"]), ("The list is empty.", "Your list is empty."))
        self.assertEqual(calls[0]["extra_body"], {"thinking": {"type": "disabled"}})
        r = agent.process_message("what's on the list", "Gio", voice_mode=True)
        self.assertEqual(r["response_text"], "Sorry, I can't help with that one.")


if __name__ == "__main__":
    unittest.main()
