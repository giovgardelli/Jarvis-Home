"""Which path each phrase takes through the router's keyword rules."""
import unittest

from router_under_test import get_router

OFFERED_MORE = "There are a few more — want to hear them?"


def classify(r, text, last_full=OFFERED_MORE):
    """Mirror of the order route() checks things in (before Haiku)."""
    if r._is_text_me(text, last_full):
        return "text_me"
    if r._is_hear_more(text, last_full):
        return "hear_more"
    if r._is_timer_request(text):
        return "timer"
    if r._is_multi_agent(text):
        return "multi"
    if r._is_web_search(text):
        return "web"
    return r._local_route(text) or "haiku"


CASES = {
    # P0: generic questions must not be hijacked by web search
    "what's my brief": "haiku",
    "when is the furnace filter due": "haiku",
    "who's our plumber": "haiku",
    "what are my tasks": "haiku",
    "how many tasks do I have": "haiku",
    "what is the capital of France": "haiku",
    "look up my tasks": "haiku",
    # shopping fast path; to-do requests must not land on the shopping list
    "add more milk to the list": "shawn_ping",
    "add eggs to the list": "shawn_ping",
    "what's on the shopping list": "shawn_ping",
    "what’s on the shopping list": "shawn_ping",
    "add call the plumber to my to do list": "haiku",
    "we need to call the electrician": "haiku",
    # flights: whole words only
    "when do I land": "drew_parture",
    "when does Kenz land": "drew_parture",
    "book a trip to the island": "haiku",
    "what time does Costco close": "haiku",
    # timers need a timer/alarm noun to cancel
    "cancel the dentist appointment": "haiku",
    "cancel the pizza timer": "timer",
    "stop all alarms": "timer",
    "set a timer for 10 minutes": "timer",
    "what time is it": "timer",
    # multi-agent read questions, never for writes
    "what's my priority for today": "multi",
    "update my address today": "haiku",
    # hear more / text me
    "more": "hear_more",
    "tell me more": "hear_more",
    "continue": "hear_more",
    "text me that": "text_me",
    # explicit search phrasing goes straight to Gemini
    "search for the best pizza in LA": "web",
    "who won the Lakers game": "web",
}


class RoutingRules(unittest.TestCase):
    def test_phrase_table(self):
        r = get_router()
        failures = []
        for text, want in CASES.items():
            got = classify(r, text)
            if got != want:
                failures.append(f"{text!r}: got {got}, want {want}")
        self.assertEqual(failures, [], "\n" + "\n".join(failures))

    def test_bare_yes_needs_an_offer(self):
        r = get_router()
        self.assertTrue(r._is_hear_more("yes", OFFERED_MORE))
        self.assertFalse(r._is_hear_more("yes", "Added milk."))
        self.assertTrue(r._is_text_me("sure", "Want me to text it to you?"))
        self.assertFalse(r._is_text_me("sure", "Added milk."))


if __name__ == "__main__":
    unittest.main()
