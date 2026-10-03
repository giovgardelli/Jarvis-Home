"""
The household — who lives here (<repo>/household.json).

Used to match names said in a request ("add X to Kenz's list"), to address
people ("Good morning, Gio"), to ask "Is that for Gio or Kenz?", and as
speech-to-text hints. JARVIS_SPEAKER overrides default_speaker.
"""
import json
import os

HOUSEHOLD_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "household.json")

with open(HOUSEHOLD_PATH) as f:
    _data = json.load(f)

MEMBERS = _data["members"]
DEFAULT_SPEAKER = os.environ.get("JARVIS_SPEAKER", _data.get("default_speaker") or MEMBERS[0]["id"])


def member_ids():
    return [m["id"] for m in MEMBERS]


def display_name(member_id):
    """'Gio' for 'gio'; falls back to the default member for unknown ids."""
    for m in MEMBERS:
        if m["id"] == member_id:
            return m["name"]
    return display_name(DEFAULT_SPEAKER) if member_id != DEFAULT_SPEAKER else member_id.title()


def spoken_names():
    """{member id: [lowercase names/aliases that refer to them]}"""
    return {m["id"]: [m["name"].lower()] + [a.lower() for a in m.get("aliases", [])] for m in MEMBERS}


def keyterms():
    """Words to boost in speech-to-text: each name plus any extra keyterms.
    (Aliases are not boosted — they're misrecognitions to match, not encourage.)"""
    return [m["name"] for m in MEMBERS] + [k for m in MEMBERS for k in m.get("keyterms", [])]


def names_question():
    """'Is that for Gio or Kenz?' (or 'Gio, Kenz or Sam?' for bigger households)."""
    names = [m["name"] for m in MEMBERS]
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]
    return f"Is that for {joined}?"


def everyone():
    """'Gio and Kenz' (or 'Gio, Kenz and Sam')."""
    names = [m["name"] for m in MEMBERS]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
