import json
import unittest

from app.stats import LinkStats
from tests.test_main import make_bridge


class LinkStatsTest(unittest.TestCase):
    def test_reach_retries_latency(self):
        s = LinkStats()
        s.record(0, True, 1, 40)
        s.record(30, True, 2, 80)
        s.record(60, False, 3)
        self.assertEqual(s.reach(60), 66.7)
        self.assertEqual(s.retries(60), 3)
        self.assertEqual(s.latency(60), 60)
        self.assertEqual(s.count(60), 3)

    def test_old_polls_age_out(self):
        s = LinkStats()
        s.record(0, False, 3)
        s.record(4000, True, 1, 50)
        self.assertEqual(s.reach(4000), 100.0)
        self.assertEqual(s.retries(4000), 0)

    def test_empty(self):
        s = LinkStats()
        self.assertIsNone(s.reach(0))
        self.assertIsNone(s.latency(0))
        self.assertEqual(s.retries(0), 0)


class BridgeLinkTest(unittest.TestCase):
    def test_link_sensors_for_polled_devices_only(self):
        bridge, _ = make_bridge()
        bridge.register(1152669, 0x25)
        bridge.register(1132530, 0x2A)
        sensor = json.loads(bridge.published["homeassistant/sensor/1152669/reach/config"])
        self.assertEqual(sensor["unique_id"], "1152669_reach")
        self.assertEqual(sensor["entity_category"], "diagnostic")
        self.assertEqual(sensor["availability"], [{"topic": "warema/bridge/state"}])   # stays visible while offline
        self.assertNotIn("homeassistant/sensor/1132530/reach/config", bridge.published)

    def test_poll_results_reach_the_values(self):
        bridge, _ = make_bridge()
        bridge.register(1152669, 0x25)
        dev = bridge.devices["1152669"]
        self.assertNotIn("last_seen", bridge.link_values(dev, 1000))
        bridge.on_poll(1152669, False, 3, None)
        bridge.on_poll(1152669, True, 1, 45)
        bridge.on_seen(1152669)
        bridge.set_offline(dev, True)
        values = bridge.link_values(dev, __import__("time").time())
        self.assertEqual((values["reach"], values["latency"], values["retries"], values["offline_events"]),
                         (50.0, 45, 2, 1))
        self.assertIn("last_seen", values)


if __name__ == "__main__":
    unittest.main()
