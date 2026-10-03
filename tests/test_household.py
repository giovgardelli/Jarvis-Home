"""household.json drives names, the owner question and speech hints."""
import os
import unittest

from helpers import install_stubs


class Household(unittest.TestCase):
    def setUp(self):
        install_stubs()
        from jarvis_core import household
        self.h = household

    def test_names_and_question(self):
        self.assertEqual(self.h.display_name("kenz"), "Kenz")
        self.assertEqual(self.h.names_question(), "Is that for Gio or Kenz?")
        self.assertEqual(self.h.everyone(), "Gio and Kenz")
        self.assertIn("makenzie", self.h.spoken_names()["kenz"])

    def test_keyterms_exclude_misheard_aliases(self):
        self.assertEqual(self.h.keyterms(), ["Gio", "Kenz", "Makenzie"])
        self.assertNotIn("geo", [k.lower() for k in self.h.keyterms()])

    def test_router_uses_household(self):
        from router_under_test import get_router
        r = get_router()
        self.assertEqual(r.OWNER_QUESTION, "Is that for Gio or Kenz?")
        self.assertEqual(r._speaker_from_text("add milk to Makenzie's list"), "kenz")
        self.assertEqual(r._address("gio"), "Gio")

    def test_timezone_setting(self):
        import importlib
        from jarvis_core import config
        saved = os.environ.get("JARVIS_TIMEZONE")
        try:
            os.environ["JARVIS_TIMEZONE"] = "Europe/Rome"
            self.assertEqual(importlib.reload(config).TIMEZONE, "Europe/Rome")
        finally:
            if saved is None:
                os.environ.pop("JARVIS_TIMEZONE", None)
            else:
                os.environ["JARVIS_TIMEZONE"] = saved
            importlib.reload(config)


if __name__ == "__main__":
    unittest.main()
