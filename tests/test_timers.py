"""Timers: firing, persistence across restarts, late announcements."""
import importlib
import json
import os
import tempfile
import time
import unittest

from helpers import install_stubs


def read_json(path):
    with open(path) as f:
        return json.load(f)


def fresh_timer_manager(path):
    install_stubs()
    import timer_manager
    tm = importlib.reload(timer_manager)
    tm.TIMERS_FILE = path
    return tm


class Timers(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "timers.json")
        self.said = []

    def start(self):
        tm = fresh_timer_manager(self.path)
        tm.init(play_fn=lambda audio: self.said.append(audio), synthesize_fn=lambda text: text)
        return tm

    def test_timer_fires_and_survives_restart(self):
        tm = self.start()
        tm.set_timer("pizza", 2)
        tm.set_alarm("wake up", time.time() + 3600)
        self.assertEqual(sorted(read_json(self.path)), ["pizza", "wake up"])
        for entry in tm._timers.values():      # simulate the process dying
            entry["cancel_event"].set()
        tm = self.start()                      # restart restores both
        self.assertIn("Pizza", tm.list_timers())
        time.sleep(2.3)
        self.assertEqual(self.said, ["Time's up — your pizza timer is done."])
        self.assertEqual(sorted(read_json(self.path)), ["wake up"])
        tm.cancel_all()

    def test_late_timer_announced_stale_one_dropped(self):
        now = time.time()
        json.dump({"pasta": {"end_time": now - 300, "type": "timer"},
                   "old": {"end_time": now - 3600, "type": "timer"},
                   "tea": {"end_time": now + 60, "type": "timer"}}, open(self.path, "w"))  # noqa
        tm = self.start()
        time.sleep(0.3)
        self.assertEqual(self.said, ["Sorry, your pasta timer went off while I was restarting."])
        self.assertEqual(sorted(read_json(self.path)), ["tea"])
        tm.cancel_all()
        self.assertEqual(read_json(self.path), {})


if __name__ == "__main__":
    unittest.main()
