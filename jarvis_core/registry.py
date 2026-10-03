"""
The agent registry — one file (<repo>/registry.json) for every process.

Each entry: id, display_name, routing_hint (one line the router's prompt
uses), access (who may use it by voice: "public", "household" or
"personal" — see router "Sensitivity tiers"), needs_speaker (optional: the
agent must know who is asking — e.g. "my to-do list"), endpoint, capabilities, example_phrases,
notify_targets. Adding an
agent means adding an entry here; the router builds its prompt and its list
of allowed agents from it.
"""
import json
import os

REGISTRY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "registry.json")

with open(REGISTRY_PATH) as f:
    REGISTRY = json.load(f)

AGENTS = REGISTRY["agents"]


def agent(agent_id):
    """Registry entry for agent_id, or None."""
    return next((a for a in AGENTS if a["id"] == agent_id), None)


def access(agent_id):
    """Sensitivity tier for voice requests; unknown agents count as personal."""
    return (agent(agent_id) or {}).get("access", "personal")


def needs_speaker(agent_id):
    """True if the agent's answer depends on who is asking."""
    return bool((agent(agent_id) or {}).get("needs_speaker"))


def agent_ids():
    return [a["id"] for a in AGENTS]


def routing_lines():
    """'- id: hint' lines for the routing prompt."""
    return "\n".join(
        f"- {a['id']}: {a.get('routing_hint') or ', '.join(a['capabilities'])}" for a in AGENTS
    )
