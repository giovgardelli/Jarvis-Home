"""Music: finding a station, playing it, turning it down while Jarvis listens
or talks, and making way for AirPlay."""
import importlib
import json
import os
import tempfile
import unittest

from helpers import install_stubs


class FakeMpv:
    """A running mpv process: poll() is None until it "dies"."""
    def __init__(self, url, volume):
        self.url, self.volume, self.exit = url, volume, None

    def poll(self):
        return self.exit

    def terminate(self):
        self.exit = 0

    def wait(self, timeout=None):
        return self.exit


def fresh_player(tmp, playback="jarvis"):
    install_stubs()
    os.environ["JARVIS_PLAYBACK"] = playback
    from jarvis_core import config
    importlib.reload(config)
    import music_player
    mp = importlib.reload(music_player)
    os.environ.pop("JARVIS_PLAYBACK", None)
    importlib.reload(config)
    mp.STATE_FILE = os.path.join(tmp, "music.json")
    mp.AIRPLAY_FLAG = os.path.join(tmp, "airplay")
    mp.airplay_active = lambda: os.path.exists(mp.AIRPLAY_FLAG)
    return mp


class Music(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mp = mp = fresh_player(self.tmp)
        self.started, self.volumes, self.amixer, self.searches = [], [], [], []

        def start(url, volume):
            proc = FakeMpv(url, volume)
            self.started.append(proc)
            return proc

        def ipc(*command, wait=0.0):
            if command[:2] == ("set_property", "volume"):
                self.volumes.append(command[2])
            if command == ("get_property", "metadata"):
                return self.metadata
            return None

        def radio_browser(**params):
            self.searches.append(params)
            return self.found.get(tuple(params.items())[0], [])

        self.metadata, self.found = {}, {}
        mp._start_mpv, mp._ipc, mp._radio_browser = start, ipc, radio_browser
        mp.subprocess.run = lambda cmd, **k: self.amixer.append(cmd[-1])

    def airplay(self, on):
        if on:
            open(self.mp.AIRPLAY_FLAG, "w").close()
        elif os.path.exists(self.mp.AIRPLAY_FLAG):
            os.remove(self.mp.AIRPLAY_FLAG)

    def test_query_cleaning_and_presets(self):
        mp = self.mp
        for said, want in [("some jazz music please", "jazz"), ("the radio", ""), ("", ""),
                           ("some music", ""), ("radio paradise", "radio paradise"),
                           ("KEXP.", "kexp"), ("a little smooth jazz", "smooth jazz")]:
            self.assertEqual(mp.clean_query(said), want, said)
        self.assertEqual(mp.find_preset("jazz")["name"], "Sonic Universe")
        self.assertEqual(mp.find_preset("groove salad")["name"], "Groove Salad")
        self.assertEqual(mp.find_preset("80s")["name"], "Underground 80s")
        self.assertIsNone(mp.find_preset("jazzercise"), "whole words only")

    def test_preset_then_radio_browser_name_then_tag(self):
        mp = self.mp
        self.assertEqual(mp.play("some jazz"), "Playing Sonic Universe.")
        self.assertEqual(self.searches, [], "presets need no search")
        self.found = {("tag", "reggae"): [{"name": "Reggae  141", "url_resolved": "http://r/141"}]}
        self.assertEqual(mp.play("reggae"), "Playing Reggae 141.")
        self.assertEqual([list(s)[0] for s in self.searches], ["name", "tag"])
        self.assertEqual(self.started[-1].url, "http://r/141")
        self.assertEqual(self.started[0].exit, 0, "the previous station was stopped")
        self.assertIn("couldn't find a radio station for taylor swift", mp.play("Taylor Swift"))

    def test_resume_remembers_last_station_and_volume_across_restarts(self):
        mp = self.mp
        self.assertEqual(mp.play(""), "Playing Groove Salad.", "first preset by default")
        mp.play("80s")
        mp.change_volume("up")
        mp.stop()
        mp = fresh_player(self.tmp)
        mp._start_mpv = lambda url, volume: self.started.append(FakeMpv(url, volume)) or self.started[-1]
        mp._load_state()
        self.assertEqual(mp.play("the radio"), "Playing Underground 80s.")
        self.assertEqual(self.started[-1].volume, mp.DEFAULT_VOLUME + mp.VOLUME_STEP)

    def test_ducking_nests_and_restores(self):
        mp = self.mp
        mp.play("jazz")
        mp.duck()                          # wake word
        mp.duck()                          # Jarvis talks
        mp.unduck()
        self.assertEqual(self.volumes, [25], "only the first duck changes anything")
        mp.unduck()
        self.assertEqual(self.volumes, [25, 50])
        with mp.ducked():
            self.assertEqual(mp.play("80s"), "Playing Underground 80s.")
            self.assertEqual(self.started[-1].volume, 25, "a station started mid-command starts low")
        self.assertEqual(self.volumes[-1], 50)

    def test_stop_while_ducked_stays_stopped(self):
        mp = self.mp
        mp.play("jazz")
        mp.duck()
        self.assertEqual(mp.stop(), "Stopped.")
        mp.unduck()
        self.assertFalse(mp.is_playing())
        self.assertEqual(len(self.started), 1)
        self.assertEqual(mp.stop(), "", "nothing to stop: say nothing")

    def test_volume_limits(self):
        mp = self.mp
        self.assertEqual(mp.change_volume("up"), "Nothing's playing.")
        mp.play("jazz")
        for _ in range(5):
            mp.change_volume("up")
        self.assertEqual(mp.change_volume("up"), "That's as loud as it goes.")
        self.assertEqual(self.volumes[-1], 100)

    def test_now_playing(self):
        mp = self.mp
        mp.play("jazz")
        self.metadata = {"icy-title": "Miles Davis - So What"}
        self.assertEqual(mp.now_playing(), "This is Miles Davis - So What, on Sonic Universe.")
        self.metadata = {}
        self.assertIn("isn't saying", mp.now_playing())

    def test_airplay_stops_radio_and_is_ducked(self):
        mp = self.mp
        mp.play("jazz")
        self.airplay(True)
        mp.check()
        self.assertFalse(mp.is_playing(), "AirPlay takes over the speaker")
        self.assertEqual(self.amixer, ["100%"], "AirPlay starts at full volume")
        self.assertIn("AirPlay", mp.play("jazz"))
        with mp.ducked():
            self.assertEqual(self.amixer[-1], "65%")
        self.assertEqual(self.amixer[-1], "100%")
        self.assertEqual(mp.change_volume("up"), "AirPlay's volume is on your phone.")
        self.airplay(False)
        mp.check()
        with mp.ducked():
            pass
        self.assertEqual(len(self.amixer), 3, "no AirPlay, no mixer calls")

    def test_dropped_station_restarts(self):
        mp = self.mp
        mp.play("jazz")
        self.started[0].exit = 1
        mp.check()
        self.assertEqual(len(self.started), 1, "not straight away")
        mp._last_start -= mp.RESTART_AFTER
        mp.check()
        self.assertEqual(len(self.started), 2)
        self.assertEqual(self.started[1].url, self.started[0].url)

    def test_refuses_on_an_unshared_speaker(self):
        mp = fresh_player(self.tmp, playback="hw:2,0")
        mp._start_mpv = lambda *a: self.fail("mpv must not grab the speaker")
        self.assertIn("isn't set up", mp.play("jazz"))

    def test_speaker_card(self):
        mp = self.mp
        for device, card in [("hw:2,0", "2"), ("plughw:1,0", "1"), ("hw:CARD=Speaker,DEV=0", "Speaker")]:
            mp.AUDIO_OUT_DEVICE = device
            self.assertEqual(mp._speaker_card(), card)

    def test_stations_file_is_valid(self):
        stations = json.load(open(self.mp.STATIONS_PATH))["stations"]  # noqa: SIM115
        self.assertTrue(stations)
        for s in stations:
            self.assertTrue(s["name"] and s["url"].startswith("http"), s)


class MusicRouting(unittest.TestCase):
    def test_voice_only_and_dispatch(self):
        from router_under_test import get_router
        r = get_router()
        calls = []
        r.music_player = type("MP", (), {
            "play": staticmethod(lambda q: calls.append(("play", q)) or "Playing."),
            "stop": staticmethod(lambda: calls.append("stop") or ""),
            "change_volume": staticmethod(lambda d: calls.append(d) or "Louder."),
            "now_playing": staticmethod(lambda: "This is jazz."),
        })
        try:
            self.assertEqual(r.route("play some jazz", "gio")["response_text"], "Playing.")
            self.assertEqual(r.route("Stop.", None, voice_status="unauthorized")["response_text"], "",
                             "anyone in the room can stop the music")
            r.route("turn it up", "gio")
            r.route("play the radio", "gio")
            self.assertEqual(calls, [("play", "some jazz"), "stop", "up", ("play", "the radio")])
            tg = r.route("play some jazz", "gio", voice_mode=False, channel="telegram")
            self.assertIn("speaker", tg["response_text"])
            self.assertEqual(len(calls), 4)
        finally:
            r.music_player = None


if __name__ == "__main__":
    unittest.main()
