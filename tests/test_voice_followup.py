"""The Pi keeps listening (no wake word) only after a question Jarvis needs answered."""
import unittest

from router_under_test import get_router

CASES = {
    # real questions: open the 4 s window
    "Is that for Gio or Kenz?": True,
    "You have 6 tasks this week. There are a few more — want to hear them?": True,
    "You have 6 items on the list. Want me to text it to you?": True,
    "Added milk. That's 7 items now. Want me to text you the list?": True,
    # statements and courtesy closers: straight back to "Hey Jarvis"
    "Added butter to the shopping list.": False,
    "Your next flight is United to Denver, on Friday at 9:20 AM.": False,
    "Good evening, Gio. Anything else I can help with?": False,
    "Done. Is there anything else you'd like?": False,
    "Timer set for ten minutes. How else can I help?": False,
    "The Dodgers play at 7:10 tonight. Let me know if you need anything?": False,
    "Is it raining? Yes, light rain until noon.": False,
    "": False,
}


class FollowUpWindow(unittest.TestCase):
    def test_phrase_table(self):
        get_router()
        from jarvis_core.voice import expects_reply
        failures = [f"{t!r}: got {expects_reply(t)}, want {want}"
                    for t, want in CASES.items() if expects_reply(t) != want]
        self.assertEqual(failures, [], "\n" + "\n".join(failures))

    def test_spoken_prompts_discourage_courtesy_questions(self):
        r = get_router()
        from jarvis_core.voice import NO_COURTESY_QUESTION, VOICE_INSTRUCTION
        self.assertIn(NO_COURTESY_QUESTION, VOICE_INSTRUCTION)
        self.assertIn(NO_COURTESY_QUESTION, r._build_routing_prompt("hi", "gio", [], voice_mode=True))
        self.assertNotIn(NO_COURTESY_QUESTION, r._build_routing_prompt("hi", "gio", [], voice_mode=False),
                         "Telegram replies can ask freely")


if __name__ == "__main__":
    unittest.main()
