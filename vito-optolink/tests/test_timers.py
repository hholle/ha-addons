import time
import unittest

from app import mqtt_bridge, p300, timers

# 05:30-08:00 and 16:00-21:00; bytes are hours * 8 + minutes / 10
DAY = bytes([5 * 8 + 3, 8 * 8, 16 * 8, 21 * 8, 0xFF, 0xFF, 0xFF, 0xFF])


class FakeLink:
    def __init__(self, memory):
        self.memory = memory

    def read(self, addr, length):
        if addr not in self.memory:
            raise p300.P300Rejected("no")
        return self.memory[addr]


class DecodeTests(unittest.TestCase):
    def test_decode_and_format(self):
        windows = timers.decode_day(DAY)
        self.assertEqual(windows, [(330, 480), (960, 1260)])
        self.assertEqual(timers.format_windows(windows), "05:30–08:00, 16:00–21:00")

    def test_unused_day(self):
        self.assertEqual(timers.format_windows(timers.decode_day(bytes([0xFF] * 8))), "aus")

    def test_active_window_end_is_exclusive(self):
        windows = timers.decode_day(DAY)
        self.assertTrue(timers.is_active(windows, 20 * 60 + 59))
        self.assertFalse(timers.is_active(windows, 21 * 60))
        self.assertFalse(timers.is_active(windows, 12 * 60))


class TimersTests(unittest.TestCase):
    def make(self):
        memory = {timers.PROGRAMS["ww"][1] + 8 * d: DAY for d in range(7)}
        t = timers.Timers()
        t.read(FakeLink(memory), 0)
        return t

    def test_values_for_today(self):
        t = self.make()
        local = time.struct_time((2026, 10, 29, 21, 9, 0, 3, 302, -1))     # Thursday 21:09
        self.assertEqual(t.values(local), {"zeiten_ww_heute": "05:30–08:00, 16:00–21:00", "ww_programm_aktiv": "aus"})
        local = time.struct_time((2026, 10, 29, 20, 55, 0, 3, 302, -1))
        self.assertEqual(t.values(local)["ww_programm_aktiv"], "an")

    def test_unreadable_programs_are_left_out(self):
        t = self.make()
        self.assertNotIn("zeiten_heizen_heute", t.values(time.localtime()))
        self.assertEqual(set(t.week), {"ww"})

    def test_attributes_hold_the_week(self):
        attrs = self.make().attributes("ww")
        self.assertEqual(list(attrs), timers.DAYS)
        self.assertEqual(attrs["Do"], "05:30–08:00, 16:00–21:00")

    def test_reads_only_when_due(self):
        t = self.make()
        self.assertFalse(t.due(10))
        self.assertTrue(t.due(timers.READ_EVERY_S + 1))

    def test_publish_only_changes(self):
        class Bridge:
            def __init__(self):
                self.states, self.attrs = [], []

            def publish_state(self, dp, payload):
                self.states.append((dp.id, payload))

            def publish_attributes(self, dp, payload):
                self.attrs.append(dp.id)

        t, bridge = self.make(), Bridge()
        noon = time.mktime((2026, 10, 29, 12, 0, 0, 0, 0, -1))
        t.publish(bridge, noon)
        t.publish(bridge, noon + 60)
        self.assertEqual(len(bridge.states), 2)
        self.assertEqual(bridge.attrs, ["zeiten_ww_heute"])

    def test_discovery_has_attributes_topic(self):
        dp = next(d for d in timers.DERIVED if d.id == "zeiten_ww_heute")
        payload = mqtt_bridge.discovery_payload(dp)
        self.assertEqual(payload["json_attributes_topic"], "vito_optolink/zeiten_ww_heute/attributes")
        self.assertNotIn("unit_of_measurement", payload)


if __name__ == "__main__":
    unittest.main()
