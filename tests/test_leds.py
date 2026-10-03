"""LEDs stay dark 10pm-7am and never crash the voice loop."""
import time
import unittest
from datetime import datetime

from helpers import install_stubs


class QuietHours(unittest.TestCase):
    def test_lights_by_hour(self):
        install_stubs()
        import led_controller as led
        writes = []
        led._available = True
        led._stick = type("Stick", (), {"set_color": lambda self, index, red, green, blue: writes.append((red, green, blue))})()
        real_dt = led.datetime
        try:
            for hour, should_light in [(21, True), (22, False), (3, False), (6, False), (7, True), (12, True)]:
                class FakeDT(real_dt):
                    @classmethod
                    def now(cls, tz=None):
                        return real_dt(2026, 10, 5, hour, 30, tzinfo=tz)
                led.datetime = FakeDT
                writes.clear()
                led.listening(); time.sleep(0.12)
                led.unauthorized(); led.uncertain(); led.error(); led.public_query()  # used to raise NameError
                time.sleep(0.05)
                lit = any(w != (0, 0, 0) for w in writes)
                self.assertEqual(lit, should_light, f"{hour}:30")
                led.off()
        finally:
            led.datetime = real_dt
            led.off()


if __name__ == "__main__":
    unittest.main()
