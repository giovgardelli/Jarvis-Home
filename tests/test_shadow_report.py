"""The shadow report turns the logs into a clear go / not-yet."""
import csv
import os
import tempfile
import unittest

from helpers import install_stubs

install_stubs()
from jarvis_core import shadow_report as sr  # noqa: E402


def write(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


class Report(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.paths = {k: os.path.join(d, f"{k}.csv") for k in ("routing", "router", "stt")}

    def run_report(self):
        return sr.report(self.paths["routing"], self.paths["router"], self.paths["stt"], min_similarity=0.5)

    def test_word_error_rate(self):
        self.assertEqual(sr.word_error_rate("add eggs to the list", "add eggs to the list"), 0.0)
        self.assertAlmostEqual(sr.word_error_rate("add eggs to the list", "add legs to the list"), 0.2)

    def test_classifier_ready_at_the_right_confidence(self):
        rows = [["t", f"q{i}", "will_due", "0.95", "0.9", "will_due"] for i in range(40)]   # confident & right
        rows += [["t", f"w{i}", "andy_mann", "0.7", "0.9", "will_due"] for i in range(10)]  # unsure & wrong
        write(self.paths["router"], ["timestamp", "text", "predicted", "confidence", "similarity", "claude_agent"], rows)
        s = sr.classifier_summary(self.paths["router"], 0.5)
        self.assertEqual(s["recommended"]["confidence"], 0.8)
        self.assertIn("READY: set ROUTER_CLASSIFIER=on and ROUTER_CLASSIFIER_MIN_CONFIDENCE=0.8", self.run_report())

    def test_classifier_not_ready_with_little_data(self):
        write(self.paths["router"], ["timestamp", "text", "predicted", "confidence", "similarity", "claude_agent"],
              [["t", "q", "will_due", "0.99", "0.9", "will_due"]] * 5)
        self.assertIn("NOT YET", self.run_report().split("Track A")[1].split("Track B")[0])

    def test_stt_verdicts(self):
        header = ["timestamp", "deepgram_text", "local_text", "deepgram_seconds", "local_seconds",
                  "deepgram_decision", "local_decision", "error"]
        good = [["t", "add eggs to the list", "add eggs to the list", "0.6", "0.4", "shawn_ping", "shawn_ping", ""]] * 35
        write(self.paths["stt"], header, good)
        self.assertIn("READY: set LOCAL_STT=on", self.run_report())
        slow = [["t", "add eggs", "add eggs", "0.4", "0.9", "shawn_ping", "shawn_ping", ""]] * 35
        write(self.paths["stt"], header, slow)
        self.assertIn("slower than Deepgram", self.run_report())
        write(self.paths["stt"], header, [["t", "", "", "0", "0", "", "", "local speech-to-text unavailable: ImportError"]])
        self.assertIn("failed: local speech-to-text unavailable", self.run_report())

    def test_empty_logs(self):
        text = self.run_report()
        self.assertIn("0 requests logged", text)
        self.assertIn("No data yet", text)


if __name__ == "__main__":
    unittest.main()
