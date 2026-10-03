"""Model check: finds models in the code, runs every 14 days, feeds the brief."""
import os
import sys
import tempfile
import types
import unittest

from helpers import AGENTS, install_stubs, isolated_modules


class ModelWatch(unittest.TestCase):
    def setUp(self):
        install_stubs()
        os.environ["JARVIS_STATE_DB"] = os.path.join(tempfile.mkdtemp(), "state.db")
        os.environ.setdefault("GEMINI_API_KEY", "test")
        self.ctx = isolated_modules(path=os.path.join(AGENTS, "jarvis"), drop=("jarvis_core.state", "model_watch"))
        self.ctx.__enter__()
        import model_watch
        self.mw = model_watch
        self.prompts = []
        replies = self.replies = []

        class Client:
            def __init__(inner, **k):
                inner.models = inner

            def generate_content(inner, model, contents, config):
                self.prompts.append((model, contents))
                return types.SimpleNamespace(text=replies.pop(0))
        self._genai_client = sys.modules["google.genai"].Client
        sys.modules["google.genai"].Client = Client
        self.mw.PROVIDER_LISTERS = {"Anthropic (Claude)": lambda: ["claude-haiku-4-5-20251001", "claude-sonnet-5-5"]}

    def tearDown(self):
        sys.modules["google.genai"].Client = self._genai_client
        self.ctx.__exit__(None, None, None)

    def test_finds_models_actually_used(self):
        in_use = self.mw.find_models_in_use()
        self.assertIn("claude-haiku-4-5-20251001", in_use["Anthropic (Claude)"])
        self.assertIn("gemini-3.5-flash-lite", in_use["Google (Gemini)"])
        self.assertIn("nova-3", in_use["Deepgram (speech-to-text)"])
        self.assertIn("eleven_flash_v2_5", in_use["ElevenLabs (text-to-speech)"])

    def test_cadence_and_brief(self):
        self.replies[:] = ["• Something to upgrade (example.com)", "NONE"]
        self.mw.maybe_run_model_watch()
        self.assertEqual(self.mw.todays_advisory(), "• Something to upgrade (example.com)")
        self.mw.maybe_run_model_watch()
        self.assertEqual(len(self.prompts), 1, "runs at most once per 14 days")
        from jarvis_core import state
        record = state.get_model_watch(); record["last_run"] = "2000-01-01"; state.set_model_watch(record)
        self.mw.maybe_run_model_watch()
        self.assertEqual(len(self.prompts), 2)
        self.assertIn("Already reported last time", self.prompts[1][1])
        self.assertIsNone(self.mw.todays_advisory(), "NONE means nothing in the brief")


if __name__ == "__main__":
    unittest.main()
