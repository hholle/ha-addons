import os
import tempfile
import time
import unittest

from app import derived, mqtt_bridge

# a fixed local noon, so "today" and "last hour" do not depend on the time zone of the test host
NOON = time.mktime((2026, 10, 29, 12, 0, 0, 0, 0, -1))


def feed(stats, steps):
    """steps: (seconds after noon, flame, start counter, valve)"""
    for t, flame, starts, valve in steps:
        stats.update(NOON + t, flame, starts, valve)


class BurnerStatsTests(unittest.TestCase):
    def test_run_and_pause(self):
        s = derived.BurnerStats()
        feed(s, [(0, False, 100, "Heizung"), (60, True, 101, "Heizung"), (360, False, 101, "Heizung"),
                 (660, True, 102, "Heizung")])
        v = s.values(NOON + 660)
        self.assertEqual(v["laufzeit_letzte"], 5.0)
        self.assertEqual(v["pause_letzte"], 5.0)
        self.assertEqual(v["starts_24h"], 2)
        self.assertEqual(v["starts_stunde"], 2)
        self.assertEqual(v["laufzeit_mittel_24h"], 5.0)

    def test_split_by_valve(self):
        s = derived.BurnerStats()
        feed(s, [(0, False, 10, "Warmwasser"), (10, True, 11, "Warmwasser"), (70, False, 11, "Warmwasser"),
                 (100, True, 12, "Heizung"), (700, False, 12, "Heizung")])
        v = s.values(NOON + 700)
        self.assertEqual(v["starts_24h_ww"], 1)
        self.assertEqual(v["starts_24h_heizen"], 1)
        self.assertEqual(v["laufzeit_mittel_24h_ww"], 1.0)
        self.assertEqual(v["laufzeit_mittel_24h_heizen"], 10.0)

    def test_counter_catches_run_shorter_than_poll(self):
        s = derived.BurnerStats()
        feed(s, [(0, False, 5, "Heizung"), (10, False, 6, "Heizung")])
        self.assertEqual(s.values(NOON + 10)["starts_24h"], 1)
        self.assertNotIn("laufzeit_letzte", s.values(NOON + 10))

    def test_startup_in_the_middle_of_a_run_gives_no_duration(self):
        s = derived.BurnerStats()
        feed(s, [(0, True, 5, "Heizung"), (300, False, 5, "Heizung")])
        self.assertNotIn("laufzeit_letzte", s.values(NOON + 300))
        self.assertEqual(s.values(NOON + 300)["starts_24h"], 0)

    def test_on_time_today_and_share(self):
        s = derived.BurnerStats()
        feed(s, [(0, True, 5, "Heizung"), (60, True, 5, "Heizung"), (120, False, 5, "Heizung")])
        v = s.values(NOON + 120)
        self.assertEqual(v["laufzeit_heute"], round(120 / 3600, 2))
        self.assertEqual(v["anteil_heute"], 100.0)

    def test_gap_is_not_counted_as_on_time(self):
        s = derived.BurnerStats()
        feed(s, [(0, True, 5, "Heizung"), (3000, True, 5, "Heizung")])
        self.assertEqual(s.on_s_today, 0)

    def test_warning_threshold(self):
        s = derived.BurnerStats(warn_starts_per_hour=3)
        feed(s, [(0, False, 0, "Heizung")] + [(60 * i, False, i, "Heizung") for i in range(1, 3)])
        self.assertEqual(s.values(NOON + 120)["takt_warnung"], "aus")
        feed(s, [(180, False, 3, "Heizung")])
        self.assertEqual(s.values(NOON + 180)["takt_warnung"], "an")
        # an hour later the starts have aged out
        self.assertEqual(s.values(NOON + 180 + 3601)["takt_warnung"], "aus")

    def test_old_events_are_dropped_after_24h(self):
        s = derived.BurnerStats()
        feed(s, [(0, False, 1, "Heizung"), (10, False, 2, "Heizung"), (90000, False, 2, "Heizung")])
        self.assertEqual(s.values(NOON + 90000)["starts_24h"], 0)

    def test_counter_reset_is_ignored(self):
        s = derived.BurnerStats()
        feed(s, [(0, False, 500, "Heizung"), (10, False, 3, "Heizung"), (20, False, 4, "Heizung")])
        self.assertEqual(s.values(NOON + 20)["starts_24h"], 1)

    def test_state_survives_restart(self):
        s = derived.BurnerStats()
        feed(s, [(0, False, 1, "Heizung"), (10, True, 2, "Heizung"), (310, False, 2, "Heizung")])
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "stats.json")
            derived.save_state(path, s)
            again = derived.BurnerStats(state=derived.load_state(path))
        feed(again, [(400, False, 2, "Heizung")])
        v = again.values(NOON + 400)
        self.assertEqual(v["starts_24h"], 1)
        self.assertEqual(v["laufzeit_letzte"], 5.0)

    def test_state_from_older_version_gives_no_share_above_100(self):
        s = derived.BurnerStats(state={"events": [], "runs": [], "day": derived._day(NOON), "on_s_today": 3500.0})
        feed(s, [(0, False, 5, "Heizung"), (10, False, 5, "Heizung")])
        self.assertLessEqual(s.values(NOON + 10)["anteil_heute"], 100.0)
        self.assertEqual(s.values(NOON + 10)["laufzeit_heute"], 0.0)

    def test_load_state_missing_file(self):
        self.assertIsNone(derived.load_state("/nonexistent/stats.json"))


class DerivedEntityTests(unittest.TestCase):
    def test_every_value_has_an_entity(self):
        s = derived.BurnerStats()
        feed(s, [(0, False, 1, "Heizung"), (10, True, 2, "Heizung"), (70, False, 2, "Warmwasser")])
        ids = {dp.id for dp in derived.DERIVED}
        self.assertTrue(set(s.values(NOON + 70)) <= ids)

    def test_unique_ids_do_not_clash_with_datapoints(self):
        uids = [dp.uid for dp in derived.DERIVED]
        self.assertEqual(len(uids), len(set(uids)))
        self.assertTrue(all(u.startswith("vito_") for u in uids))

    def test_device_id_reaches_home_assistant(self):
        payload = mqtt_bridge.discovery_payload(derived.DEVICE_ID, "20CB")
        self.assertEqual(payload["device"]["model_id"], "20CB")
        self.assertEqual(payload["entity_category"], "diagnostic")
        self.assertEqual(payload["state_topic"], "vito_optolink/geraetekennung")
        self.assertNotIn("model_id", mqtt_bridge.discovery_payload(derived.DEVICE_ID)["device"])

    def test_obsolete_entities_are_not_current_ones(self):
        current = {(dp.component, dp.uid) for dp in derived.DERIVED}
        self.assertFalse(current & set(mqtt_bridge.OBSOLETE))

    def test_discovery_payload(self):
        dp = next(d for d in derived.DERIVED if d.id == "laufzeit_letzte")
        payload = mqtt_bridge.discovery_payload(dp)
        self.assertEqual(payload["unit_of_measurement"], "min")
        self.assertEqual(payload["suggested_display_precision"], 1)
        self.assertEqual(payload["state_topic"], "vito_optolink/laufzeit_letzte")
        warn = next(d for d in derived.DERIVED if d.id == "takt_warnung")
        self.assertEqual(mqtt_bridge.discovery_topic(warn), "homeassistant/binary_sensor/vito_takt_warnung/config")


if __name__ == "__main__":
    unittest.main()
