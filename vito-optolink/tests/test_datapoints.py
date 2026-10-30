import json
import os
import unittest

from app import datapoints
from app.mqtt_bridge import discovery_payload, discovery_topic, state_topic

FILE = os.path.join(os.path.dirname(__file__), "..", "datapoints", "20CB.yaml")


class DatapointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.device_id, cls.points = datapoints.load(FILE)
        cls.by_id = {p.id: p for p in cls.points}

    def test_file_loads(self):
        self.assertEqual(self.device_id, "20CB")
        self.assertEqual(len(self.points), 31)

    def test_uids_match_old_addon(self):
        # entities of the vcontrold add-on must keep their unique_id
        old = {
            "vcontrold_temp_aussen", "vcontrold_temp_kessel_ist", "vcontrold_temp_kessel_soll",
            "vcontrold_temp_ww_ist", "vcontrold_temp_ww_soll", "vcontrold_temp_speicher",
            "vcontrold_temp_ww_auslauf", "vcontrold_temp_vl_soll",
            "vcontrold_temp_raum_nor", "vcontrold_temp_raum_red", "vcontrold_starts_brenner",
            "vcontrold_kennlinie_neigung", "vcontrold_kennlinie_niveau",
            "vcontrold_betriebsart", "vcontrold_betriebsart_hk", "vcontrold_umschaltventil",
            "vcontrold_flamme", "vcontrold_pumpe_speicher", "vcontrold_pumpe_intern",
            "vcontrold_pumpe_hk", "vcontrold_pumpe_zirku", "vcontrold_stoerung",
        }
        self.assertEqual({p.uid for p in self.points} - {p.uid for p in self.points if p.uid.startswith("vito_")}, old)

    def test_diagnostic_category_in_discovery(self):
        payload = discovery_payload(self.by_id["leistung_a38f"])
        self.assertEqual(payload["entity_category"], "diagnostic")
        self.assertNotIn("entity_category", discovery_payload(self.by_id["temp_aussen"]))

    def test_placeholder_values_are_unknown(self):
        soll = self.by_id["temp_kessel_soll"]
        self.assertEqual(datapoints.decode(soll, bytes([50])), 50)
        self.assertIsNone(datapoints.decode(soll, bytes([249])))
        self.assertEqual(datapoints.to_payload(None), "None")

    def test_display_precision(self):
        self.assertEqual(discovery_payload(self.by_id["temp_aussen"])["suggested_display_precision"], 1)
        self.assertEqual(discovery_payload(self.by_id["temp_kessel_soll"])["suggested_display_precision"], 0)
        self.assertNotIn("suggested_display_precision", discovery_payload(self.by_id["starts_brenner"]))
        self.assertNotIn("suggested_display_precision", discovery_payload(self.by_id["flamme"]))

    def test_decode_temperature(self):
        dp = self.by_id["temp_aussen"]
        self.assertEqual(datapoints.decode(dp, bytes([0x76, 0x00])), 11.8)
        self.assertEqual(datapoints.decode(dp, bytes([0x38, 0xFF])), -20.0)

    def test_decode_one_byte_setpoint(self):
        self.assertEqual(datapoints.decode(self.by_id["temp_ww_soll"], bytes([50])), 50)

    def test_decode_counter(self):
        self.assertEqual(datapoints.decode(self.by_id["starts_brenner"], bytes([0x10, 0x27, 0, 0])), 10000)

    def test_decode_enum(self):
        self.assertEqual(datapoints.decode(self.by_id["flamme"], bytes([0x0B])), "an")
        self.assertEqual(datapoints.decode(self.by_id["flamme"], bytes([0x08])), "an")
        self.assertEqual(datapoints.decode(self.by_id["flamme"], bytes([0x00])), "aus")
        self.assertEqual(datapoints.decode(self.by_id["flamme"], bytes([0x7F])), "?")
        self.assertEqual(datapoints.decode(self.by_id["betriebsart"], bytes([0x02])), "Heizen und Warmwasser")

    def test_pump_night_mode_state_counts_as_on(self):
        # vcontrold repo: 20CB heating circuit pump answers 03 when running in night mode
        self.assertEqual(datapoints.decode(self.by_id["pumpe_hk"], bytes([0x03])), "an")

    def test_payload_formatting(self):
        self.assertEqual(datapoints.to_payload(21.0), "21")
        self.assertEqual(datapoints.to_payload(11.8), "11.8")
        self.assertEqual(datapoints.to_payload(0.0), "0")
        self.assertEqual(datapoints.to_payload(50), "50")
        self.assertEqual(datapoints.to_payload("an"), "an")

    def test_neigung_scaled(self):
        self.assertEqual(datapoints.to_payload(datapoints.decode(self.by_id["kennlinie_neigung"], bytes([12]))), "1.2")


class DiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, cls.points = datapoints.load(FILE)
        cls.by_id = {p.id: p for p in cls.points}

    def test_sensor_payload(self):
        p = discovery_payload(self.by_id["temp_aussen"])
        self.assertEqual(p["unique_id"], "vcontrold_temp_aussen")
        self.assertEqual(p["unit_of_measurement"], "°C")
        self.assertEqual(p["device_class"], "temperature")
        self.assertEqual(p["state_topic"], "vito_optolink/temp_aussen")
        self.assertEqual(p["availability_topic"], "vito_optolink/status")
        self.assertEqual(p["device"]["identifiers"], ["vcontrold"])

    def test_binary_sensor_payload(self):
        dp = self.by_id["stoerung"]
        p = discovery_payload(dp)
        self.assertEqual(p["payload_on"], "FEHLERMELDUNG")
        self.assertEqual(p["payload_off"], "kein Fehler")
        self.assertNotIn("unit_of_measurement", p)
        self.assertEqual(discovery_topic(dp), "homeassistant/binary_sensor/vcontrold_stoerung/config")

    def test_all_payloads_are_json_and_topics_unique(self):
        topics = [state_topic(p) for p in self.points]
        self.assertEqual(len(topics), len(set(topics)))
        for dp in self.points:
            json.dumps(discovery_payload(dp))

    def test_every_binary_state_is_known_to_its_enum(self):
        for dp in self.points:
            if dp.component == "binary_sensor":
                self.assertIn(dp.payload_on, dp.enum.values())
                self.assertIn(dp.payload_off, dp.enum.values())


if __name__ == "__main__":
    unittest.main()
