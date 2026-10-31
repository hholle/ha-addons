import json
import time
import unittest
from unittest import mock

from app import main


def make_bridge(**env):
    with mock.patch.object(main, "Stick") as stick, mock.patch.object(main.mqtt, "Client") as client:
        bridge = main.Bridge({"POLLING_INTERVAL": "30000", **env})
    bridge.published = {}
    bridge.publish = lambda topic, payload, retain=True: bridge.published.__setitem__(topic, payload)
    bridge.debounce = lambda key, fn: fn()
    return bridge, stick.return_value


class BridgeTest(unittest.TestCase):
    def test_parse_groups(self):
        self.assertEqual(main.parse_groups("Terrassendach:1,2; Garten:3"),
                         {"1": ("Terrassendach", "warema_group_Terrassendach"),
                          "2": ("Terrassendach", "warema_group_Terrassendach"),
                          "3": ("Garten", "warema_group_Garten")})

    def test_snap_tilt(self):
        self.assertEqual([main.snap_tilt_2a(v) for v in (-100, -45, 10, 20, 50, 80)], [-60, -33, 0, 33, 66, 66])

    def test_discovery_types(self):
        bridge, stick = make_bridge(DEVICE_GROUPS="Dach:1132530")
        for snr, typ in ((1132530, 0x2A), (1152669, 0x25), (5, 0x21), (6, 0x20), (7, 0x07)):
            bridge.register(snr, typ)
        cover = json.loads(bridge.published["homeassistant/cover/1132530/1132530/config"])
        self.assertNotIn("set_position_topic", cover)
        self.assertEqual((cover["tilt_min"], cover["tilt_max"]), (-60, 100))
        self.assertEqual(cover["device"]["identifiers"], ["warema_group_Dach"])
        self.assertEqual(cover["availability_mode"], "all")
        awning = json.loads(bridge.published["homeassistant/cover/1152669/1152669/config"])
        self.assertIn("set_position_topic", awning)
        self.assertNotIn("tilt_command_topic", awning)
        self.assertNotIn("state_topic", json.loads(bridge.published["homeassistant/cover/5/5/config"]))
        self.assertIn("tilt_command_topic", json.loads(bridge.published["homeassistant/cover/6/6/config"]))
        self.assertNotIn("homeassistant/cover/7/7/config", bridge.published)
        stick.add_blind.assert_any_call(1132530, no_poll=True)   # 2A is never polled
        stick.add_blind.assert_any_call(1152669, no_poll=False)

    def test_ignored_device(self):
        bridge, stick = make_bridge(IGNORED_DEVICES="1152669")
        bridge.register(1152669, 0x25)
        self.assertEqual(bridge.published, {})
        stick.add_blind.assert_not_called()

    def test_position_inverted_for_ha(self):
        bridge, _ = make_bridge()
        bridge.register(1152669, 0x25)
        bridge.on_position(1152669, 0, 0, False, True)    # WMS 0 = open
        self.assertEqual(bridge.published["warema/1152669/position"], "100")
        self.assertEqual(bridge.published["warema/1152669/state"], "open")
        bridge.on_position(1152669, 100, 0, False, True)
        self.assertEqual(bridge.published["warema/1152669/state"], "closed")
        bridge.on_position(1152669, 30, 0, True, True)    # moving: no state
        self.assertEqual(bridge.published["warema/1152669/position"], "70")
        self.assertEqual(bridge.published["warema/1152669/state"], "closed")

    def test_set_position_inverted_for_wms(self):
        bridge, stick = make_bridge()
        bridge.register(1152669, 0x25)
        bridge.command("1152669", "set_position", "25")
        stick.set_position.assert_called_with(1152669, 75, 0)

    def test_open_close(self):
        bridge, stick = make_bridge()
        bridge.register(1152669, 0x21)
        bridge.register(1132530, 0x2A)
        bridge.command("1152669", "set", "CLOSE")
        stick.set_position.assert_called_with(1152669, 100, 0)
        bridge.command("1132530", "set", "CLOSE")
        stick.set_position.assert_called_with(1132530, 0, -60)
        self.assertEqual(bridge.published["warema/1132530/tilt"], "-60")
        bridge.command("1132530", "set_tilt", "20")   # snapped to 33
        stick.set_position.assert_called_with(1132530, 0, 33)
        bridge.command("1152669", "set", "STOP")
        stick.stop.assert_called_with(1152669)

    def test_scan_registers_only_new_devices(self):
        bridge, stick = make_bridge()
        bridge.register(1152669, 0x25)
        stick.scan.return_value = [(1152669, "25"), (1132530, "2A"), (99, "07")]
        bridge.scan()
        self.assertEqual(sorted(bridge.devices), ["1132530", "1152669"])
        self.assertEqual(stick.add_blind.call_count, 2)   # the known awning was not added twice

    def test_scan_button(self):
        bridge, stick = make_bridge()
        bridge.client = mock.Mock()
        bridge.publish = main.Bridge.publish.__get__(bridge)
        bridge.publish_all()
        topics = [c.args[0] for c in bridge.client.publish.call_args_list]
        self.assertIn("homeassistant/button/warema_bridge/scan/config", topics)

    def test_scan_not_started_twice(self):
        bridge, stick = make_bridge()
        bridge.scan_lock.acquire()
        bridge.scan()
        stick.scan.assert_not_called()

    def test_unknown_device_ignored(self):
        bridge, stick = make_bridge()
        bridge.command("999", "set", "OPEN")
        stick.set_position.assert_not_called()

    def test_republish_after_ha_restart(self):
        bridge, _ = make_bridge()
        bridge.register(1152669, 0x25)
        bridge.published.clear()
        msg = mock.Mock(topic="homeassistant/status", payload=b"online")
        bridge.on_message(None, None, msg)
        self.assertIn("homeassistant/cover/1152669/1152669/config", bridge.published)

    def test_weather_auto_registers(self):
        bridge, stick = make_bridge()
        bridge.on_weather(4, {"lumen": 100, "temp": 12.5, "wind": 3, "rain": True})
        self.assertEqual(bridge.published["warema/4/rain/state"], "ON")
        self.assertIn("homeassistant/sensor/4/temperature/config", bridge.published)
        stick.add_blind.assert_not_called()

    def test_offline_and_recovery(self):
        bridge, stick = make_bridge()
        bridge.register(1152669, 0x25)
        stick.blinds = {1152669: mock.Mock(paused=False)}
        dev = bridge.devices["1152669"]
        bridge.set_offline(dev, True)
        self.assertTrue(stick.blinds[1152669].paused)
        self.assertEqual(bridge.published["warema/1152669/availability"], "offline")
        bridge.on_seen(1152669)   # any answer brings it back
        self.assertFalse(dev.offline)
        self.assertEqual(bridge.published["warema/1152669/availability"], "online")


if __name__ == "__main__":
    unittest.main()
