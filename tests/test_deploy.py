"""deploy/pi_deploy.sh runs only the agents listed in registry.json, so an
optional agent (Ray Mote, Miles Ahead…) is left out by deleting its entry."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from helpers import REPO

SCRIPT = os.path.join(REPO, "deploy", "pi_deploy.sh")


def wanted(name, agent_ids):
    """Run the script's own wanted() against a registry with these agents."""
    body = re.search(r"^wanted\(\) \{.*?^\}", open(SCRIPT).read(), re.S | re.M).group(0)
    repo = tempfile.mkdtemp()
    with open(os.path.join(repo, "registry.json"), "w") as f:
        json.dump({"agents": [{"id": a} for a in agent_ids]}, f, indent=2)
    r = subprocess.run(["bash", "-c", f'{body}\nwanted "$1"', "_", name], env=dict(os.environ, REPO=repo))
    return r.returncode == 0


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class OptionalAgents(unittest.TestCase):
    def test_only_registered_agents_run(self):
        registered = ["andy_mann", "will_due"]
        self.assertTrue(wanted("andy_mann", registered))
        self.assertFalse(wanted("ray_mote", registered), "left out of registry.json: not run")
        self.assertFalse(wanted("andy", registered), "whole ids only")
        self.assertTrue(wanted("jarvis", []), "the central service always runs")

    def test_every_registered_agent_has_a_port(self):
        ids = [a["id"] for a in json.load(open(os.path.join(REPO, "registry.json")))["agents"]]
        ports = open(SCRIPT).read()
        for agent_id in ids:
            self.assertIn(f"[{agent_id}]=", ports, f"{agent_id} needs a port in pi_deploy.sh")


if __name__ == "__main__":
    unittest.main()
