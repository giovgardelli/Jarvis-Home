"""Miles Ahead: which calendar flights get fare tracking, and the deal alert."""
import os
import types
import unittest

from helpers import AGENTS, install_stubs, isolated_modules, load_file

MILES = os.path.join(AGENTS, "miles_ahead")


def load_calendar():
    install_stubs()
    with isolated_modules(path=MILES, drop=("calendar_reader",)):
        return load_file(os.path.join(MILES, "calendar_reader.py"), "miles_calendar_under_test")


def event(title, description=""):
    return {"summary": title, "description": description,
            "start": {"dateTime": "2026-10-06T07:45:00-07:00"}, "end": {}}


class TrackedFlightParsing(unittest.TestCase):
    def test_southwest_is_tracked(self):
        cal = load_calendar()
        f = cal.parse_tracked_flight(event("Southwest WN 1234", "LAX 08:00 - SFO 09:30"))
        self.assertEqual((f["flight_number"], f["airline"], f["origin"], f["destination"]),
                         ("WN1234", "Southwest", "LAX", "SFO"))
        f = cal.parse_tracked_flight(event("Flight to Denver (WN 88)", "Burbank BUR to Denver DEN"))
        self.assertEqual((f["origin"], f["destination"]), ("BUR", "DEN"))
        f = cal.parse_tracked_flight(event("WN 7 OAK to SAN"))
        self.assertEqual((f["origin"], f["destination"]), ("OAK", "SAN"), "route from title codes")

    def test_alaska_and_other_airlines_are_not_price_tracked(self):
        # No credit on Alaska Saver fares, so only Drew Parture tracks them (delays etc.)
        cal = load_calendar()
        self.assertIsNone(cal.parse_tracked_flight(event("Flight to Seattle (AS 1234)", "LAX to SEA")))
        self.assertFalse(cal._is_tracked_title("Flight to Seattle (AS 1234)"))
        self.assertIsNone(cal.parse_tracked_flight(event("Delta DL 400", "LAX 08:00 - JFK 16:30")))
        self.assertIsNone(cal.parse_tracked_flight(event("Flight to Denver (WN 88)")),
                          "no route → can't price it")
        self.assertTrue(cal._is_tracked_title("Flight to Denver (WN 88)"))
        self.assertFalse(cal._is_tracked_title("Dentist"))


class SouthwestDealAlert(unittest.TestCase):
    def test_only_southwest_fares_count(self):
        install_stubs()
        cal = load_calendar()
        sheets = types.ModuleType("sheets")
        sheets.get_flight = lambda n, d: {"Flight": n}
        sheets.add_flight = lambda *a: None
        sheets.update_flight_field = lambda *a: None
        sheets.set_flight_status = lambda *a: None
        sheets.should_check_today = lambda f: True
        sheets.get_active_flights = lambda: [{"Flight": "WN1234", "Date": "2026-10-06", "Route": "LAX-SFO",
                                              "Departure": "2026-10-06T07:45:00-07:00", "Paid Price": "200"}]
        scraper = types.ModuleType("scraper")
        scraper.get_google_flights = lambda o, d, date: [
            {"flight": "AS 900", "airline": "Alaska", "departs": "2026-10-06 08:00", "price": 99},
            {"flight": "WN 1234", "airline": "Southwest", "departs": "2026-10-06 07:45", "price": 160},
        ]
        scraper.get_flight_price = lambda *a: 200
        cal.get_tracked_flights = lambda days_ahead=60: []
        with isolated_modules({"calendar_reader": cal, "sheets": sheets, "scraper": scraper},
                              path=MILES, drop=("checker",)):
            checker = load_file(os.path.join(MILES, "checker.py"), "miles_checker_under_test")
        alerts, summary = checker.run_daily_check()
        self.assertEqual(summary, {"checked": 1, "deals": 1})
        self.assertIn("now: *$160* (save *$40*)", alerts[0])
        self.assertIn("Rebook on southwest.com", alerts[0])


class FlightNumberMatch(unittest.TestCase):
    def test_exact_number_not_substring(self):
        install_stubs()
        with isolated_modules(path=MILES, drop=("scraper",)):
            scraper = load_file(os.path.join(MILES, "scraper.py"), "miles_scraper_under_test")
        self.assertTrue(scraper._same_flight("WN 1234", "WN1234"))
        self.assertTrue(scraper._same_flight("1234", "WN1234"))
        self.assertFalse(scraper._same_flight("WN 123", "WN1234"))
        self.assertFalse(scraper._same_flight("AS 1234", "WN1234"))


if __name__ == "__main__":
    unittest.main()
