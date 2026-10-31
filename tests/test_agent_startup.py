"""Every agent's entrypoint imports cleanly when started the way systemd
starts it: from its own folder, finding jarvis_core via pi_run.py's own
sys.path bootstrap (not the test suite's)."""
import os
import subprocess
import sys
import tempfile
import unittest

from helpers import AGENTS

AGENT_NAMES = ["andy_mann", "drew_parture", "shawn_ping", "will_due", "miles_ahead", "ray_mote", "jarvis"]

STARTUP_SCRIPT = r'''
import os, runpy, sys
sys.path.insert(0, {tests!r})
import helpers
helpers.install_stubs()
while helpers.REPO in sys.path:
    sys.path.remove(helpers.REPO)
for m in [m for m in sys.modules if m.startswith("jarvis_core")]:
    del sys.modules[m]
sys.path.insert(0, os.getcwd())
import time
time.sleep = lambda s: None   # no network here: skip Google token retry back-off at startup
runpy.run_path("pi_run.py", run_name="startup_check")
import jarvis_core
print("OK", jarvis_core.__file__)
'''


class AgentStartup(unittest.TestCase):
    def test_each_agent_imports(self):
        tests_dir = os.path.dirname(os.path.abspath(__file__))
        env = dict(os.environ, JARVIS_STATE_DB=os.path.join(tempfile.mkdtemp(), "state.db"))
        for key in ("TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY", "JARVIS_SECRET", "GIO_CHAT_ID",
                    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN", "GEMINI_API_KEY"):
            env.setdefault(key, "test")
        for name in AGENT_NAMES:
            with self.subTest(agent=name):
                result = subprocess.run(
                    [sys.executable, "-c", STARTUP_SCRIPT.format(tests=tests_dir)],
                    cwd=os.path.join(AGENTS, name), env=env, capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                self.assertIn("OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
