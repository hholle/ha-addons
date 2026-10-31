import json
import unittest
from types import SimpleNamespace

from tests.test_main import make_bridge


def message(topic, payload="PRESS"):
    return SimpleNamespace(topic=topic, payload=payload.encode())


class DiagnosticsTest(unittest.TestCase):
    def test_buttons_and_ping_sensor_for_every_receiver(self):
        bridge, _ = make_bridge()
        for snr, typ in ((1152669, 0x25), (1132530, 0x2A), (99, 0x06)):
            bridge.register(snr, typ)
        for snr in ("1152669", "1132530"):
            ping = json.loads(bridge.published[f"homeassistant/button/{snr}/ping/config"])
            self.assertEqual(ping["command_topic"], f"warema/{snr}/ping")
            self.assertEqual(ping["availability"], [{"topic": "warema/bridge/state"}])
            identify = json.loads(bridge.published[f"homeassistant/button/{snr}/identify/config"])
            self.assertEqual(identify["device_class"], "identify")
            self.assertIn(f"homeassistant/sensor/{snr}/ping/config", bridge.published)
        self.assertNotIn("homeassistant/button/99/ping/config", bridge.published)

    def test_ping_button_calls_the_stick_and_publishes_the_result(self):
        bridge, stick = make_bridge()
        bridge.register(1152669, 0x25)
        bridge.on_message(None, None, message("warema/1152669/ping"))
        number, callback = stick.ping.call_args[0]
        self.assertEqual(number, 1152669)
        callback(True, 42)
        self.assertEqual(bridge.published["warema/1152669/link/ping"], "ok (42 ms)")
        callback(False, None)
        self.assertEqual(bridge.published["warema/1152669/link/ping"], "no answer")

    def test_identify_button_waves(self):
        bridge, stick = make_bridge()
        bridge.register(1195016, 0x25)
        bridge.on_message(None, None, message("warema/1195016/identify"))
        self.assertEqual(stick.beckon.call_args[0][0], 1195016)

    def test_quality_values_are_published_and_discovered_once(self):
        bridge, _ = make_bridge()
        bridge.register(1195016, 0x25)
        dev = bridge.devices["1195016"]
        self.assertNotIn("homeassistant/sensor/1195016/tx_rssi/config", bridge.published)
        values = {"tx_lqi": 200, "tx_rssi": 90, "rx_lqi": 180, "rx_rssi": 85}
        bridge.on_quality(dev, values, None)
        self.assertEqual(bridge.published["warema/1195016/link/tx_rssi"], "90")
        sensor = json.loads(bridge.published["homeassistant/sensor/1195016/rx_lqi/config"])
        self.assertEqual(sensor["unique_id"], "1195016_rx_lqi")
        self.assertEqual(sensor["entity_category"], "diagnostic")
        self.assertTrue(bridge.quality_ever)

    def test_stick_without_quality_support_switches_it_off(self):
        bridge, _ = make_bridge()
        bridge.register(1195016, 0x25)
        bridge.on_quality(bridge.devices["1195016"], None, "s")
        self.assertFalse(bridge.quality_enabled)

    def test_silent_stick_switches_it_off_after_repeated_misses(self):
        bridge, _ = make_bridge()
        bridge.register(1195016, 0x25)
        for _ in range(5):
            bridge.on_quality(bridge.devices["1195016"], None, None)
        self.assertTrue(bridge.quality_enabled)
        bridge.on_quality(bridge.devices["1195016"], None, None)
        self.assertFalse(bridge.quality_enabled)

    def test_quality_can_be_disabled_by_option(self):
        bridge, _ = make_bridge(QUALITY_INTERVAL="0")
        self.assertFalse(bridge.quality_enabled)
        bridge, _ = make_bridge()
        self.assertTrue(bridge.quality_enabled)


if __name__ == "__main__":
    unittest.main()


class NoAngleBridgeTest(unittest.TestCase):
    def test_awning_without_angle_publishes_no_tilt(self):
        bridge, _ = make_bridge()
        bridge.register(1152669, 0x25)
        bridge.on_position(1152669, 0, None, False, True)
        self.assertEqual(bridge.published["warema/1152669/position"], "100")
        self.assertEqual(bridge.published["warema/1152669/state"], "open")
        self.assertNotIn("warema/1152669/tilt", bridge.published)
        self.assertIsNone(bridge.devices["1152669"].angle)

    def test_tilt_only_ignores_missing_angle(self):
        bridge, _ = make_bridge()
        bridge.register(1132530, 0x2A)
        bridge.on_position(1132530, 255, None, False, True)
        self.assertNotIn("warema/1132530/tilt", bridge.published)


class GroupedNamesTest(unittest.TestCase):
    def test_grouped_devices_get_the_serial_in_diagnostic_names(self):
        bridge, _ = make_bridge(DEVICE_GROUPS="Terrassendach:1152669,1195016")
        bridge.register(1152669, 0x25)
        bridge.register(1195016, 0x25)
        bridge.on_quality(bridge.devices["1152669"], {"tx_lqi": 1, "tx_rssi": 2, "rx_lqi": 3, "rx_rssi": 4}, None)
        names = [json.loads(bridge.published[f"homeassistant/{c}/{s}/{k}/config"])["name"]
                 for c, s, k in (("sensor", "1152669", "reach"), ("sensor", "1152669", "tx_rssi"),
                                 ("sensor", "1195016", "reach"), ("button", "1195016", "ping"),
                                 ("sensor", "1195016", "ping"))]
        self.assertEqual(names, ["1152669 Reachability", "1152669 TX signal (RSSI)", "1195016 Reachability",
                                 "1195016 Ping", "1195016 Last ping"])

    def test_ungrouped_devices_keep_plain_names(self):
        bridge, _ = make_bridge()
        bridge.register(1152669, 0x25)
        self.assertEqual(json.loads(bridge.published["homeassistant/sensor/1152669/reach/config"])["name"], "Reachability")
