#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Itron ACE3000 electricity meter → MQTT bridge.

Reads the meter via IEC 62056-21 D0 mode optical interface, parses OBIS codes,
and publishes to Home Assistant via MQTT with auto-discovery.

Protocol notes:
  - Serial: 300 baud, 7E1 (7 data bits, even parity, 1 stop bit)
  - Trigger: send /?!\\r\\n to request a telegram
  - Response: identification line (/...), data lines (OBIS), closing line (!)
  - OBIS format: <code>(<value>*<unit>)  e.g.  1.8.0(001234.567*kWh)

Alternative: the `iec62056` library (pip install iec62056) can parse telegrams
with Telegram.from_string(raw). Not used here due to uncertain ACE3000
compatibility — manual regex is more transparent and easy to debug.
"""

import json
import logging
import os
import re
import sys
import time

import paho.mqtt.client as mqtt
import serial

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

DEVICE        = os.environ["SERIAL_DEVICE"]
MQTT_HOST     = os.environ["MQTT_HOST"]
MQTT_USER     = os.environ["MQTT_USER"]
MQTT_PASSWORD = os.environ["MQTT_PASSWORD"]

# Interval between meter requests in seconds.
# The ACE3000 needs ~10-15s to send a full telegram at 300 baud.
POLL_INTERVAL = 55

HA_DEVICE = {
    "identifiers": ["stromzaehler"],
    "name": "Stromzähler",
    "model": "Itron ACE3000",
    "manufacturer": "Itron",
}

# OBIS code → (mqtt_topic, discovery_id, display_name, device_class, unit, state_class)
# Only codes present in the telegram are published — missing codes are silently skipped.
OBIS_MAP = {
    "1.8.0": ("zaehler/bezug",       "zaehler_bezug",       "Bezug gesamt",       "energy", "kWh", "total_increasing"),
    "2.8.0": ("zaehler/einspeisung", "zaehler_einspeisung", "Einspeisung gesamt", "energy", "kWh", "total_increasing"),
}

# Regex: matches 1.8.0(001234.567*kWh) or 1.7.0(000.345*kW)
_OBIS_RE = re.compile(r"(\d+\.\d+\.\d+)\(([^*)\s]+)(?:\*([^)]+))?\)")


def parse_telegram(lines):
    """Return dict of {obis_code: value_string} from raw telegram lines."""
    values = {}
    for line in lines:
        for m in _OBIS_RE.finditer(line):
            obis, value = m.group(1), m.group(2)
            values[obis] = value
    return values


def publish_discovery(client):
    for obis, (topic, uid, name, dc, unit, sc) in OBIS_MAP.items():
        payload = {
            "name": name,
            "unique_id": uid,
            "state_topic": topic,
            "device_class": dc,
            "unit_of_measurement": unit,
            "state_class": sc,
            "device": HA_DEVICE,
        }
        client.publish(f"homeassistant/sensor/{uid}/config", json.dumps(payload), retain=True)
log.info("MQTT discovery published")


def connect_mqtt():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "stromzaehler")
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    client.connect(MQTT_HOST)
    client.loop_start()
    return client


def read_telegram(port):
    """Trigger meter and read one full telegram. Returns list of data lines."""
    # Flush any stale bytes (e.g. BCC checksum after !, noise during sleep)
    # before sending the trigger to avoid corrupting the next read.
    port.reset_input_buffer()
    # Request telegram (IEC 62056-21 sign-on message)
    port.write(b"\x2f\x3f\x21\x0d\x0a")
    port.flush()

    lines = []
    while True:
        raw = port.readline()
        if not raw:
            log.warning("Serial read timeout — no data from meter")
            break
        line = raw.decode("latin-1", errors="replace").strip()
        if not line:
            continue
        if line.startswith("!"):
            break   # end of telegram
        if line.startswith("/"):
            log.debug("Meter ident: %s", line)
            continue  # skip identification header
        lines.append(line)

    return lines


def main():
    log.info("strom-zaehler starting — device=%s mqtt=%s", DEVICE, MQTT_HOST)

    client = connect_mqtt()
    publish_discovery(client)

    while True:
        try:
            with serial.Serial(
                DEVICE, baudrate=300, bytesize=7,
                parity="E", stopbits=1, timeout=30
            ) as port:
                log.info("Serial port opened: %s", DEVICE)
                while True:
                    lines = read_telegram(port)

                    if not lines:
                        log.warning("Empty telegram received")
                    else:
                        values = parse_telegram(lines)
                        published = []

                        for obis, (topic, uid, name, dc, unit, sc) in OBIS_MAP.items():
                            if obis in values:
                                client.publish(topic, values[obis], retain=True)
                                published.append(f"{obis}={values[obis]}")

                        if published:
                            log.info("Published: %s", "  ".join(published))
                        else:
                            log.warning("No known OBIS codes found in telegram: %s", lines)

                    time.sleep(POLL_INTERVAL)

        except serial.SerialException as e:
            log.error("Serial error: %s — retrying in 10s", e)
            time.sleep(10)
        except Exception:
            log.exception("Unexpected error — retrying in 10s")
            time.sleep(10)


if __name__ == "__main__":
    main()
