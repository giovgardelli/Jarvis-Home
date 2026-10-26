"""Voice loop (jarvis-pi.py): recovery from failures, wake word samples,
routing overlapping the ack, and the audio sent to voice ID."""
import io
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
import wave

from helpers import REPO, have, install_stubs, load_file

HAVE_NUMPY = have("numpy")


def _install_voice_stubs():
    install_stubs()
    if not HAVE_NUMPY and "numpy" not in sys.modules:
        class Arr(list):
            def __getitem__(self, k):
                r = list.__getitem__(self, k)
                return Arr(r) if isinstance(k, slice) else r

            def copy(self):
                return Arr(self)

            def astype(self, t):
                return self

            def tobytes(self):
                return b"\x00" * (2 * len(self))
        sys.modules["numpy"] = types.SimpleNamespace(
            int16="int16", int32="int32", float32="float32", ndarray=Arr,
            frombuffer=lambda b, dtype=None: Arr([0] * (len(b) // 2)),
            concatenate=lambda xs: Arr([v for x in xs for v in x]))
    for name, attrs in {
        "pyaudio": {"paInt16": 8, "PyAudio": type("PyAudio", (), {"terminate": lambda self: None})},
        "deepgram": {"DeepgramClient": lambda *a, **k: None},
        "elevenlabs": {"ElevenLabs": lambda **k: types.SimpleNamespace(text_to_speech=None)},
    }.items():
        if not have(name):
            sys.modules[name] = types.SimpleNamespace(**attrs)
    if not have("openwakeword"):
        sys.modules["openwakeword"] = types.ModuleType("openwakeword")
        sys.modules["openwakeword.model"] = types.SimpleNamespace(Model=object)
    for key in ("ELEVENLABS_API_KEY", "JARVIS_SECRET", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY"):
        os.environ.setdefault(key, "test")


_jp = None


def voice_loop():
    global _jp
    if _jp is None:
        _install_voice_stubs()
        from router_under_test import get_router
        get_router()  # the voice loop imports the router; load it the test way
        _jp = load_file(os.path.join(REPO, "jarvis-pi.py"), "jarvis_pi_under_test")
    return _jp


class MainLoopRecovery(unittest.TestCase):
    def test_failed_request_mic_error_and_samples(self):
        jp = voice_loop()
        saved = {k: getattr(jp, k) for k in ("Model", "wakeword_model_path", "find_audio_device", "_open_stream", "_handle_command",
                                             "_apologize", "_reopen_audio", "SAMPLE_RATE", "CHUNK",
                                             "WAKEWORD_SAMPLES_DIR", "WAKEWORD_MAX_SAMPLES", "timer_manager",
                                             "music_player")}
        samples_dir = tempfile.mkdtemp()
        events = []
        # per-chunk wake scores: near-miss event, failed command, mic error, good command, 2 near-misses
        scores = [0.1, 0.35, 0.5, 0.4, 0.1, 0.7, 0.1, "OSERR", 0.1, 0.8, 0.1, 0.4, 0.1, 0.45, 0.1, "STOP"]

        class Stream:
            def read(self, n, exception_on_overflow=True):
                if scores and scores[0] == "OSERR":
                    scores.pop(0)
                    raise OSError("device gone")
                if scores and scores[0] == "STOP":
                    raise KeyboardInterrupt
                return b"\x00" * 4096

            def stop_stream(self):
                pass

            def close(self):
                pass

        class WakeModel:
            def __init__(self, **k):
                pass

            def predict(self, audio):
                return {"hey_jarvis": scores.pop(0) if scores and isinstance(scores[0], float) else 0.0}

            def reset(self):
                pass

        calls = {"n": 0}

        music = []

        def handle(p, device, t0):
            calls["n"] += 1
            events.append(f"command {calls['n']}")
            music.append("command")
            if calls["n"] == 1:
                raise ConnectionError("Deepgram unreachable")

        real_reopen = jp._reopen_audio
        try:
            jp.Model = WakeModel
            jp.wakeword_model_path = lambda: "hey_jarvis.onnx"
            jp.find_audio_device = lambda name: 3
            jp._open_stream = lambda p, d: Stream()
            jp._handle_command = handle
            jp._apologize = lambda: events.append("apologized")
            jp._reopen_audio = lambda *a: (events.append("mic reopened"), real_reopen(*a))[1]
            jp.SAMPLE_RATE = jp.CHUNK = 1024           # 1 chunk = "1 second": short warm-ups
            jp.WAKEWORD_SAMPLES_DIR, jp.WAKEWORD_MAX_SAMPLES = samples_dir, 3
            jp.timer_manager = types.SimpleNamespace(init=lambda **k: None)
            jp.music_player = types.SimpleNamespace(init=lambda: music.append("init"),
                                                    duck=lambda: music.append("duck"),
                                                    unduck=lambda: music.append("unduck"))
            jp.main()
        finally:
            for k, v in saved.items():
                setattr(jp, k, v)
        self.assertEqual(events, ["command 1", "apologized", "mic reopened", "command 2"])
        # music down from the wake word to the end of the exchange, even when it failed
        self.assertEqual(music, ["init", "duck", "command", "unduck", "duck", "command", "unduck"])
        kept = os.listdir(samples_dir)
        self.assertEqual(len(kept), 3, "one sample per event, capped")
        shutil.rmtree(samples_dir)


class CommandFlow(unittest.TestCase):
    def test_routing_overlaps_ack_and_voice_checks(self):
        jp = voice_loop()
        log, state = [], {}
        start = [time.time()]

        def ev(x):
            log.append((round(time.time() - start[0], 1), x))

        def router(**k):
            state["routed_speaker"] = k.get("speaker")
            state["voice_status"] = k.get("voice_status")
            ev("router start"); time.sleep(0.5); ev("router done")
            if k.get("voice_status") == "unauthorized":      # the real router gates personal tiers
                return {"response_text": "only residents", "full_response": None, "gate": "unauthorized"}
            if k.get("voice_status") in ("uncertain", "error"):
                return {"response_text": "try again?", "full_response": None, "gate": "repeat"}
            return {"response_text": "Added dentist to your list.", "full_response": "full"}
        patches = {
            "record_command": lambda *a, **k: b"\x00\x00" * 64,
            "prepare_audio": lambda raw: (b"stt", b"id"),
            "transcribe": lambda wav: state["text"],
            "identify_speaker_voice": lambda wav: state["voice"],
            "router_route": router,
            "play_ack": lambda: (ev("ack start"), time.sleep(0.5), ev("ack done")),
            "speak": lambda t: ev(f"speak: {t}"),
            "play_file": lambda f: ev(f"play {os.path.basename(f)}"),
            "play_brief_if_available": lambda: (ev("brief"), True)[1],
            "_play_gate": lambda gate, text="": ev(f"gate: {gate}"),
        }
        saved = {k: getattr(jp, k) for k in patches}
        jp.__dict__.update(patches)
        try:
            gio = {"status": "confirmed", "speaker": "gio"}
            for text, voice, expect in [
                ("remind me to call the dentist", gio, "speak: Added dentist to your list."),
                ("add milk to the list", {"status": "unauthorized", "speaker": None}, "gate: unauthorized"),
                ("add milk to the list", {"status": "error", "speaker": None}, "gate: repeat"),
                ("give me my brief", {"status": "unauthorized", "speaker": None}, "gate: unauthorized"),
                ("give me my brief", gio, "brief"),
                ("remind me to call the dentist", {"status": "disabled", "speaker": None, "score": 0.0},
                 "speak: Added dentist to your list."),
            ]:
                state.update(text=text, voice=voice); log.clear(); start[0] = time.time()
                jp._handle_command(None, None, time.time())
                self.assertEqual(log[-1][1], expect, text)
                if voice["status"] == "disabled":
                    self.assertIsNone(state["routed_speaker"], "voice ID off → router resolves who it's for")
            # routing ran during the ack: answer ready at ~0.5s, not ~1.0s
            state.update(text="remind me to call the dentist", voice=gio); log.clear(); start[0] = time.time()
            jp._handle_command(None, None, time.time())
            self.assertLessEqual(dict((x, t) for t, x in log)["speak: Added dentist to your list."], 0.7)
        finally:
            jp.__dict__.update(saved)


class MusicDucking(unittest.TestCase):
    def test_every_jarvis_sound_turns_the_music_down(self):
        jp = voice_loop()
        log = []

        class Ducked:
            def __enter__(self):
                log.append("down")

            def __exit__(self, *exc):
                log.append("up")
        saved = (jp.music_player, jp._to_speaker_locked)
        jp.music_player = types.SimpleNamespace(ducked=Ducked)
        jp._to_speaker_locked = lambda args, feed: log.append("sound")
        try:
            jp.play_file("timer.mp3")
        finally:
            jp.music_player, jp._to_speaker_locked = saved
        self.assertEqual(log, ["down", "sound", "up"])

    def test_sounds_go_to_the_playback_device(self):
        jp = voice_loop()
        self.assertEqual(jp.APLAY_CMD[-1], jp.PLAYBACK_DEVICE)


class VoiceIdSwitch(unittest.TestCase):
    def test_off_means_default_speaker_and_no_analysis(self):
        jp = voice_loop()
        calls = []
        saved = (jp.VOICE_ID_ENABLED, jp.voice_id_identify)
        jp.voice_id_identify = lambda wav, rate: calls.append(wav) or {"status": "confirmed", "speaker": "kenz", "score": 0.9}
        try:
            jp.VOICE_ID_ENABLED = False
            self.assertEqual(jp.identify_speaker_voice(b"wav"), {"status": "disabled", "speaker": None, "score": 0.0})
            self.assertEqual(calls, [], "voice model not used while off")
            jp.VOICE_ID_ENABLED = True
            self.assertEqual(jp.identify_speaker_voice(b"wav")["speaker"], "kenz")
            self.assertEqual(calls, [b"wav"])
        finally:
            jp.VOICE_ID_ENABLED, jp.voice_id_identify = saved

    def test_off_by_default(self):
        import importlib
        from jarvis_core import config
        saved = os.environ.pop("VOICE_ID_ENABLED", None)
        try:
            self.assertFalse(importlib.reload(config).VOICE_ID_ENABLED)
            os.environ["VOICE_ID_ENABLED"] = "true"
            self.assertTrue(importlib.reload(config).VOICE_ID_ENABLED)
        finally:
            os.environ.pop("VOICE_ID_ENABLED", None)
            if saved is not None:
                os.environ["VOICE_ID_ENABLED"] = saved
            importlib.reload(config)


class Config(unittest.TestCase):
    def test_wakeword_model_found_from_installed_package(self):
        from jarvis_core import config
        fake_pkg = types.ModuleType("openwakeword")
        fake_pkg.__file__ = "/opt/venv/lib/python3.99/site-packages/openwakeword/__init__.py"
        saved = sys.modules.get("openwakeword")
        sys.modules["openwakeword"] = fake_pkg
        try:
            os.environ.pop("JARVIS_WAKEWORD_MODEL", None)
            self.assertEqual(config.wakeword_model_path(),
                             "/opt/venv/lib/python3.99/site-packages/openwakeword/resources/models/hey_jarvis_v0.1.onnx")
            os.environ["JARVIS_WAKEWORD_MODEL"] = "/custom/model.onnx"
            self.assertEqual(config.wakeword_model_path(), "/custom/model.onnx")
        finally:
            os.environ.pop("JARVIS_WAKEWORD_MODEL", None)
            if saved is None:
                sys.modules.pop("openwakeword", None)
            else:
                sys.modules["openwakeword"] = saved

    @unittest.skipIf(any(os.environ.get(k) for k in ("JARVIS_HOME", "JARVIS_AUDIO_OUT", "JARVIS_MIC_NAME", "JARVIS_TIMEZONE")),
                     "settings overridden in the environment")
    def test_defaults_match_the_pi(self):
        from jarvis_core import config
        self.assertEqual((config.HOME, config.AUDIO_OUT_DEVICE, config.MIC_NAME, config.TIMEZONE),
                         (os.path.expanduser("~"), "hw:2,0", "EMEET", "America/Los_Angeles"))


@unittest.skipUnless(HAVE_NUMPY, "needs numpy (runs on the Pi)")
class AudioForVoiceId(unittest.TestCase):
    def test_voice_id_gets_boosted_stereo_like_before(self):
        import numpy as np
        jp = voice_loop()
        left = np.array([1000, -2000, 15000], dtype=np.int16)
        right = np.array([3000, -4000, 15000], dtype=np.int16)
        raw = np.column_stack([left, right]).ravel().tobytes()
        stt, vid = jp.prepare_audio(raw)

        def read(w):
            with wave.open(io.BytesIO(w)) as wf:
                return wf.getnchannels(), np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).tolist()
        self.assertEqual(read(vid), (2, [3000, 9000, -6000, -12000, 32767, 32767]))
        self.assertEqual(read(stt), (1, [6000, -9000, 32767]))


if __name__ == "__main__":
    unittest.main()
