"""Track B: local speech-to-text (Moonshine) adapter, shadow mode and on mode."""
import csv
import os
import sys
import tempfile
import time
import types
import unittest

from helpers import install_stubs

install_stubs()
from jarvis_core import local_stt  # noqa: E402


def reset_backend(fake_module=None):
    local_stt._backend = None
    local_stt._unavailable = None
    if fake_module is None:
        sys.modules.pop("moonshine_onnx", None)
        sys.modules["moonshine_onnx"] = None          # import fails → "not installed"
    else:
        sys.modules["moonshine_onnx"] = fake_module


class Adapter(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("moonshine_onnx", None)
        local_stt._backend = local_stt._unavailable = None

    def test_transcribes_with_moonshine(self):
        seen = {}

        def transcribe(path, model):
            seen.update(path=path, model=model, existed=os.path.exists(path))
            return ["add eggs to the list"]
        reset_backend(types.SimpleNamespace(transcribe=transcribe))
        self.assertEqual(local_stt.transcribe(b"RIFF fake wav"), "add eggs to the list")
        self.assertEqual(seen["model"], local_stt.MODEL)
        self.assertTrue(seen["existed"])
        self.assertFalse(os.path.exists(seen["path"]), "temp file cleaned up")

    def test_not_installed_is_reported_not_fatal(self):
        reset_backend(None)
        self.assertFalse(local_stt.available())
        self.assertIn("moonshine_onnx", local_stt.unavailable_reason())
        with self.assertRaises(RuntimeError):
            local_stt.transcribe(b"wav")


class VoiceLoopModes(unittest.TestCase):
    def setUp(self):
        from test_voice_loop import voice_loop
        self.jp = voice_loop()
        self.saved_mode = local_stt.MODE
        self.saved_path = local_stt.SHADOW_PATH
        local_stt.SHADOW_PATH = os.path.join(tempfile.mkdtemp(), "stt_shadow.csv")
        self.patches = {
            "record_command": lambda *a, **k: b"\x00\x00" * 64,
            "prepare_audio": lambda raw: (b"stt-wav", b"id-wav"),
            "transcribe": lambda wav: "add eggs to the list",            # Deepgram
            "identify_speaker_voice": lambda wav: {"status": "disabled", "speaker": None},
            "router_route": lambda **k: (self.routed.append(k["text"]), {"response_text": "Done.", "full_response": "Done."})[1],
            "play_ack": lambda: None, "speak": lambda t: None,
        }
        self.saved = {k: getattr(self.jp, k) for k in self.patches}
        self.jp.__dict__.update(self.patches)
        self.routed = []

    def tearDown(self):
        self.jp.__dict__.update(self.saved)
        local_stt.MODE, local_stt.SHADOW_PATH = self.saved_mode, self.saved_path
        local_stt._backend = local_stt._unavailable = None
        sys.modules.pop("moonshine_onnx", None)

    def flush_shadow(self):
        self.jp._shadow_executor.submit(lambda: None).result(timeout=10)

    def test_shadow_logs_both_and_uses_deepgram(self):
        local_stt.MODE = "shadow"
        reset_backend(types.SimpleNamespace(transcribe=lambda path, model: ["add eggs to the lisp"]))
        self.jp._handle_command(None, None, time.time())
        self.flush_shadow()
        self.assertEqual(self.routed, ["add eggs to the list"], "Jarvis keeps using Deepgram")
        with open(local_stt.SHADOW_PATH, newline="") as f:
            row = list(csv.DictReader(f))[0]
        self.assertEqual((row["deepgram_text"], row["local_text"]), ("add eggs to the list", "add eggs to the lisp"))
        self.assertEqual(row["deepgram_decision"], "shawn_ping")
        self.assertTrue(row["local_decision"])
        self.assertEqual(row["error"], "")

    def test_shadow_survives_missing_moonshine(self):
        local_stt.MODE = "shadow"
        reset_backend(None)
        self.jp._handle_command(None, None, time.time())
        self.flush_shadow()
        self.assertEqual(self.routed, ["add eggs to the list"])
        with open(local_stt.SHADOW_PATH, newline="") as f:
            self.assertIn("unavailable", list(csv.DictReader(f))[0]["error"])

    def test_on_mode_uses_local_with_deepgram_fallback(self):
        local_stt.MODE = "on"
        reset_backend(types.SimpleNamespace(transcribe=lambda path, model: ["what time is it"]))
        self.jp._handle_command(None, None, time.time())
        self.assertEqual(self.routed, ["what time is it"])
        reset_backend(types.SimpleNamespace(transcribe=lambda path, model: (_ for _ in ()).throw(RuntimeError("model crashed"))))
        self.jp._handle_command(None, None, time.time())
        self.assertEqual(self.routed[-1], "add eggs to the list", "falls back to Deepgram")
        self.assertFalse(os.path.exists(local_stt.SHADOW_PATH), "no shadow comparison in on mode")


if __name__ == "__main__":
    unittest.main()
