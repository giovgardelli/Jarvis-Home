"""Drew Parture's 7am heads-up window and live flight alerts."""
import os
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from helpers import AGENTS, install_stubs, isolated_modules, load_file

PT = ZoneInfo("America/Los_Angeles")
DAY = datetime(2026, 10, 5, tzinfo=PT)


def load_drew(flights, live, sent):
    install_stubs()
    cal = types.ModuleType("calendar_reader")
    cal.get_todays_flights = lambda: flights
    cal.get_upcoming_flights = lambda **k: []
    cal.get_flights_to_track = lambda: flights
    cal.invalidate_cache = lambda: None
    ft = types.ModuleType("flight_tracker")
    ft.get_live_flight = lambda n, d: live.get(n)
    ft.parse_flight_status = lambda x: x
    ft.format_time = lambda d: d.strftime("%H:%M") if d else "N/A"
    fakes = {"calendar_reader": cal, "flight_tracker": ft,
             "agent": types.SimpleNamespace(chat=None, split_voice=lambda t: (t, None)),
             "uber": types.SimpleNamespace(parse_uber_request=None, format_uber_message=None)}
    with isolated_modules(fakes, path=os.path.join(AGENTS, "drew_parture")):
        m = load_file(os.path.join(AGENTS, "drew_parture", "main.py"), "drew_main_under_test")
    m.ALERT_STATE_FILE = tempfile.mktemp(suffix=".json")
    m.send_to_recipient = lambda msg: sent.append(msg.split("\n")[0])
    # flight_check is a Flask route (it returns jsonify(...)); with the real
    # Flask that needs an app context, which the HTTP request provides in prod
    route = m.flight_check

    def flight_check():
        with m.app.app_context():
            return route()
    m.flight_check = flight_check
    return m


def at(m, when):
    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return when
    m.datetime = FakeDT


def status(dep, arr, **extra):
    s = {"flight_status": "scheduled", "delay_minutes": 0, "scheduled_departure": dep,
         "scheduled_arrival": arr, "origin": "LAX", "destination": "SFO", "destination_airport": "SFO Intl"}
    s.update(extra)
    return s


class FlightAlerts(unittest.TestCase):
    def test_morning_flight_skipped_by_heads_up_but_tracked_live(self):
        sent, live = [], {}
        dep = DAY.replace(hour=8)
        flights = [{"flight_number": "WN100", "departure_time": dep.isoformat(), "destination": "SFO"}]
        live["WN100"] = status(dep, dep + timedelta(hours=1, minutes=30))
        m = load_drew(flights, live, sent)

        at(m, DAY.replace(hour=7)); m.morning_flight_check()
        self.assertEqual(sent, [], "7-9am flights are skipped by the 7am heads-up (by design)")

        at(m, DAY.replace(hour=6, minute=30)); m.flight_check()
        self.assertEqual(sent, ["✅ *WN100 is on time*"])

        live["WN100"].update(actual_departure=dep + timedelta(minutes=5), flight_status="active")
        at(m, DAY.replace(hour=8, minute=30)); sent.clear(); m.flight_check()
        self.assertEqual(sent, ["🛫 *Gio's flight has departed!*"])

        live["WN100"].update(actual_arrival=DAY.replace(hour=9, minute=25), flight_status="landed")
        at(m, DAY.replace(hour=9, minute=30)); sent.clear(); m.flight_check()
        self.assertEqual(sent, ["🛬 *Gio has landed!*"])

        calls = []
        m.get_live_flight = lambda n, d: calls.append(n) or live.get(n)
        at(m, DAY.replace(hour=10)); sent.clear(); m.flight_check()
        self.assertEqual((sent, calls), ([], []), "landed flights stop polling AviationStack")

    def test_heads_up_announces_pre_7am_flight_tomorrow(self):
        sent = []
        dep = DAY.replace(hour=6) + timedelta(days=1)
        m = load_drew([{"flight_number": "WN300", "departure_time": dep.isoformat(), "destination": "DEN"}], {}, sent)
        at(m, DAY.replace(hour=7)); m.morning_flight_check()
        self.assertEqual(len(sent), 1)
        self.assertIn("tomorrow", sent[0])

    def test_red_eye_landing_after_midnight_and_daily_prune(self):
        sent, live = [], {}
        dep = DAY.replace(hour=23)
        flights = [{"flight_number": "WN200", "departure_time": dep.isoformat()}]
        live["WN200"] = status(dep, dep + timedelta(hours=7, minutes=30))
        m = load_drew(flights, live, sent)

        at(m, DAY.replace(hour=21, minute=30)); m.flight_check()
        live["WN200"].update(actual_departure=dep + timedelta(minutes=10), flight_status="active")
        at(m, DAY.replace(hour=23, minute=30)); m.flight_check()
        nxt = DAY + timedelta(days=1)
        at(m, nxt.replace(hour=7)); m.prune_alert_state(); m.flight_check()
        self.assertEqual(sent, ["✅ *WN200 is on time*", "🛫 *Gio's flight has departed!*"],
                         "the 7am prune must not re-send alerts for a flight still in the air")
        live["WN200"].update(actual_arrival=nxt.replace(hour=7, minute=20), flight_status="landed")
        at(m, nxt.replace(hour=7, minute=30)); sent.clear(); m.flight_check()
        self.assertEqual(sent, ["🛬 *Gio has landed!*"])



class SouthwestTitleParsing(unittest.TestCase):
    def parse(self, title, description=""):
        install_stubs()
        with isolated_modules(path=os.path.join(AGENTS, "drew_parture"), drop=("calendar_reader",)):
            cal = load_file(os.path.join(AGENTS, "drew_parture", "calendar_reader.py"), "drew_calendar_under_test")
        return cal.parse_southwest_flight({"summary": title, "description": description,
                                           "start": {"dateTime": "2026-10-05T08:00:00-07:00"}, "end": {}})

    def test_destination_from_city_or_code_no_guessed_origin(self):
        f = self.parse("Southwest WN 1234 to San Francisco")
        self.assertEqual((f["origin"], f["destination"]), ("", "SFO"))
        f = self.parse("Southwest WN 88 to DEN")
        self.assertEqual((f["origin"], f["destination"]), ("", "DEN"))
        f = self.parse("Southwest WN 7 to Chicago")
        self.assertEqual(f["destination"], "", "ambiguous metro areas are not guessed")

    def test_route_in_description_still_wins(self):
        f = self.parse("Southwest WN 1234", "LAX 08:00 - SFO 09:30")
        self.assertEqual((f["origin"], f["destination"]), ("LAX", "SFO"))


class NextFlightAnswer(unittest.TestCase):
    """"What's my next flight?" must say airline, destination city and departure time."""

    def load(self):
        install_stubs()
        path = os.path.join(AGENTS, "drew_parture")
        with isolated_modules(path=path, drop=("calendar_reader",)):
            cal = load_file(os.path.join(path, "calendar_reader.py"), "calendar_reader")
            agent = load_file(os.path.join(path, "agent.py"), "drew_agent_under_test")
        return cal, agent

    def parse(self, cal, title, description=""):
        return cal.parse_flight_from_event({"summary": title, "description": description,
                                            "start": {"dateTime": "2026-10-06T07:45:00-07:00"}, "end": {}})

    def test_alaska_event_gives_airline_name_number_and_destination(self):
        cal, _ = self.load()
        f = self.parse(cal, "Flight to Seattle (AS 1234)")
        self.assertEqual((f["airline_name"], f["flight_number"], f["destination"]),
                         ("Alaska Airlines", "AS1234", "SEA"))
        f = self.parse(cal, "Alaska Airlines AS 1234", "LAX to SEA")
        self.assertEqual((f["origin"], f["destination"]), ("LAX", "SEA"))

    def test_google_calendar_flight_event_format(self):
        # The format Google Calendar uses for flights it adds from booking
        # emails (made-up flight)
        cal, _ = self.load()
        f = cal.parse_flight_from_event({
            "summary": "Flight to Portland (AS 517)", "location": "Seattle SEA",
            "description": "Alaska Airlines flight 517\nSeattle SEA 14:10 (local time) - "
                           "Portland PDX 15:05 (local time)\n\nConfirmation number: TESTCF",
            "start": {"dateTime": "2025-03-14T14:10:00-07:00"}, "end": {"dateTime": "2025-03-14T15:05:00-07:00"}})
        self.assertEqual((f["flight_number"], f["airline_name"], f["origin"], f["destination"], f["confirmation"]),
                         ("AS517", "Alaska Airlines", "SEA", "PDX", "TESTCF"))

    def test_spoken_answer_has_time_and_city(self):
        cal, agent = self.load()
        f = self.parse(cal, "Flight to Seattle (AS 1234)")

        class FakeDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 10, 3, 9, 0, tzinfo=PT)
        agent.datetime = FakeDT
        self.assertEqual(agent.format_flight_spoken(f),
                         "Your next flight is Alaska Airlines to Seattle, on Tuesday at 7:45 AM.")
        self.assertEqual(agent.format_flight_spoken(f, speaker="kenz"),
                         "Gio's next flight is Alaska Airlines to Seattle, on Tuesday at 7:45 AM.")


if __name__ == "__main__":
    unittest.main()
