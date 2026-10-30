#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Vito Optolink → MQTT bridge.
Polls the datapoints of datapoints/<device>.yaml over P300 and publishes them to Home Assistant
via MQTT discovery. "fast" datapoints are polled every POLL_FAST_S, "slow" ones every POLL_SLOW_S.
"""

import logging
import os
import sys
import time

from app import codings, datapoints, derived, discover, p300, timers
from app.mqtt_bridge import Bridge

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "info").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

DEVICE        = os.environ["SERIAL_DEVICE"]
MQTT_HOST     = os.environ["MQTT_HOST"]
MQTT_USER     = os.environ["MQTT_USER"]
MQTT_PASSWORD = os.environ["MQTT_PASSWORD"]
POLL_FAST_S   = int(os.environ.get("POLL_FAST_S", "10"))
POLL_SLOW_S   = int(os.environ.get("POLL_SLOW_S", "60"))
DATAPOINTS    = os.environ.get("DATAPOINTS_FILE", "/datapoints/20CB.yaml")
MODE          = os.environ.get("MODE", "run")
CODINGS_FILE  = os.environ.get("CODINGS_FILE", "/datapoints/20CB_codings.yaml")
PUBLISH_CODINGS = os.environ.get("PUBLISH_CODINGS", "true").lower() == "true"
CONFIG_POLL_S = 6 * 3600   # coding values change rarely
SCAN_RANGES   = os.environ.get("SCAN_RANGES", "0800-08FF,5500-55FF")
SCAN_ROUNDS   = int(os.environ.get("SCAN_ROUNDS", "30"))
SCAN_INTERVAL_S = int(os.environ.get("SCAN_INTERVAL_S", "10"))
REFERENCE_TEMPS = os.environ.get("REFERENCE_TEMPS", "").replace("null", "")
TAKT_WARN_STARTS_H = int(os.environ.get("TAKT_WARN_STARTS_H", "6"))
STATS_FILE    = os.environ.get("STATS_FILE", "/data/stats.json")
STATS_SAVE_S  = 300

ADDR_DEVICE_ID = 0x00F8
OFFLINE_AFTER_FAILED_PASSES = 3


def poll_group(link, bridge, points):
    """Read and publish all points. Returns (number of successful reads, {id: decoded value})."""
    ok = 0
    values = {}
    for dp in points:
        try:
            value = datapoints.decode(dp, link.read(dp.addr, dp.len))
        except p300.P300Rejected as err:
            log.warning("%s (%04X): %s", dp.id, dp.addr, err)
            continue
        except p300.P300Error as err:
            log.error("%s (%04X): %s", dp.id, dp.addr, err)
            continue
        bridge.publish_state(dp, datapoints.to_payload(value))
        values[dp.id] = value
        ok += 1
    return ok, values


def log_burner_edge(stats, was_on, now_on, latest):
    """One log line per burner start/stop with the temperatures around it (for the short-cycling analysis)."""
    if was_on is None or was_on == now_on:
        return
    log.info("Burner %s | %s | boiler %s °C, storage %s °C, flue gas %s °C, modulation %s %% | %s",
             "ON" if now_on else "OFF", latest.get("umschaltventil", "?"),
             latest.get("temp_kessel_ist", "?"), latest.get("temp_speicher", "?"),
             latest.get("temp_abgas", "?"), latest.get("modulation", "?"),
             "last run %s min, pause %s min" % (
                 round(stats.last_run_s / 60, 1) if stats.last_run_s is not None else "?",
                 round(stats.last_pause_s / 60, 1) if stats.last_pause_s is not None else "?"))


def update_stats(stats, bridge, latest):
    if "flamme" not in latest or "starts_brenner" not in latest:
        return
    now = time.time()
    was_on = stats.flame
    stats.update(now, latest["flamme"] == "an", latest["starts_brenner"], latest.get("umschaltventil"))
    log_burner_edge(stats, was_on, stats.flame, latest)
    by_id = {dp.id: dp for dp in derived.DERIVED}
    for key, value in stats.values(now).items():
        bridge.publish_state(by_id[key], datapoints.to_payload(value))


def run_discovery(points):
    flame = next(dp for dp in points if dp.id == "flamme")
    context = [dp for dp in points if dp.id in ("betriebsart", "betriebsart_hk", "umschaltventil",
                                                  "temp_kessel_ist", "temp_kessel_soll", "temp_vl_soll")]
    link = p300.P300Link(p300.open_serial(DEVICE), retries=0)
    try:
        discover.run(link, discover.parse_ranges(SCAN_RANGES), SCAN_ROUNDS, SCAN_INTERVAL_S,
                     flame.addr, discover.parse_refs(REFERENCE_TEMPS), context)
    finally:
        link.close()
    log.info("Discovery finished. Set mode back to 'run' and restart the add-on.")


def run_codings():
    link = p300.P300Link(p300.open_serial(DEVICE))
    try:
        codings.run(link, codings.load(CODINGS_FILE))
    finally:
        link.close()
    log.info("Coding dump finished. Set mode back to 'run' and restart the add-on.")


def main():
    expected_id, points = datapoints.load(DATAPOINTS)
    if MODE == "discover":
        run_discovery(points)
        return
    if MODE == "codings":
        run_codings()
        return
    link = p300.P300Link(p300.open_serial(DEVICE))
    config_points = []
    if PUBLISH_CODINGS:
        try:
            scheme = link.read(codings.ADDR_SCHEME, 1)[0]
            log.info("Heating circuit scheme (code 00): %d", scheme)
        except p300.P300Error as err:
            scheme = None
            log.warning("Could not read the heating circuit scheme: %s", err)
        config_points = codings.to_datapoints(codings.load(CODINGS_FILE), scheme)
    groups = {
        "fast": ([dp for dp in points if dp.group == "fast"], POLL_FAST_S),
        "slow": ([dp for dp in points if dp.group == "slow"], POLL_SLOW_S),
    }
    if config_points:
        groups["config"] = (config_points, CONFIG_POLL_S)
    log.info("%d datapoints (%d fast every %ds, %d slow every %ds), %d coding values every %dh",
             len(points), len(groups["fast"][0]), POLL_FAST_S, len(groups["slow"][0]), POLL_SLOW_S,
             len(config_points), CONFIG_POLL_S // 3600)
    for dp in points:
        if not dp.verified:
            log.warning("%s (%04X) is not verified against the display yet", dp.id, dp.addr)

    bridge = Bridge(MQTT_HOST, MQTT_USER, MQTT_PASSWORD, points + config_points + derived.DERIVED + timers.DERIVED + [derived.DEVICE_ID],
                    device_id=expected_id)
    stats = derived.BurnerStats(TAKT_WARN_STARTS_H, derived.load_state(STATS_FILE))
    next_save = time.monotonic() + STATS_SAVE_S
    bridge.start()

    try:
        device_id = link.read(ADDR_DEVICE_ID, 2).hex().upper()
        log.info("Device ID %s", device_id)
        bridge.publish_state(derived.DEVICE_ID, device_id)
        if device_id != expected_id:
            log.warning("Datapoint file is for %s but the controller reports %s", expected_id, device_id)
    except p300.P300Error as err:
        log.error("Could not read device ID: %s", err)

    next_due = {name: 0.0 for name in groups}
    time_programs = timers.Timers()
    latest = {}  # most recent decoded value of every datapoint
    failed_passes = 0
    available = False
    try:
        while True:
            for name, (group_points, interval) in groups.items():
                if time.monotonic() < next_due[name]:
                    continue
                ok, values = poll_group(link, bridge, group_points)
                latest.update(values)
                if name == "fast":
                    update_stats(stats, bridge, latest)
                    if time_programs.due(time.time()):
                        time_programs.read(link, time.time())
                    time_programs.publish(bridge)
                next_due[name] = time.monotonic() + interval
                if name == "config":
                    continue   # rarely read, says nothing about whether the controller answers
                if group_points and ok == 0:
                    failed_passes += 1
                elif ok:
                    failed_passes = 0
                    if not available:
                        bridge.set_available(True)
                        available = True
                if failed_passes >= OFFLINE_AFTER_FAILED_PASSES and available:
                    log.error("No answer from the controller, marking entities unavailable")
                    bridge.set_available(False)
                    available = False
            if time.monotonic() >= next_save:
                derived.save_state(STATS_FILE, stats)
                next_save = time.monotonic() + STATS_SAVE_S
            time.sleep(0.5)
    finally:
        derived.save_state(STATS_FILE, stats)
        bridge.stop()
        link.close()


if __name__ == "__main__":
    main()
