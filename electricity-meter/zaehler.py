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

# Seconds between the start of two meter requests (add-on option poll_interval).
# The ACE3000 needs ~10-15s to send a full telegram at 300 baud.
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))
STATUS_TOPIC  = "zaehler/status"
OFFLINE_AFTER = 3   # consecutive failed polls before entities become unavailable

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

# Text values published as diagnostic entities: OBIS → (topic, discovery_id, name)
DIAG_MAP = {
    "C.1": ("zaehler/id", "zaehler_id", "Zähler-ID"),
}
# Other OBIS codes under which meters send their ID; the first one present in the telegram is used.
DIAG_ALIASES = {"C.1": ("C.1.0", "0.0.0", "96.1.0", "0.0.1")}

# Discovery IDs of entities published by earlier versions. Their retained discovery
# configs are cleared on every connect so HA removes the dead entities.
LEGACY_IDS = (
    "zaehler_leistung_bezug",
    "zaehler_leistung_einspeisung",
    "zaehler_bezug_t1",
    "zaehler_bezug_t2",
)

STX, ETX = b"\x02", b"\x03"

# Regex: matches 1.8.0(001234.567*kWh), 1.7.0(000.345*kW) and codes with letters such as C.1.0(12345678)
_OBIS_RE = re.compile(r"([0-9A-Za-z]+\.[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)?)\(([^*)\s]+)(?:\*([^)]+))?\)")


def parse_telegram(lines):
    """Return dict of {obis_code: (value_string, unit_or_None)} from raw telegram lines."""
    values = {}
    for line in lines:
        for m in _OBIS_RE.finditer(line):
            values[m.group(1)] = (m.group(2), m.group(3))
    return values


def publish_discovery(client):
    for uid in LEGACY_IDS:
        client.publish(f"homeassistant/sensor/{uid}/config", "", retain=True)
    for obis, (topic, uid, name, dc, unit, sc) in OBIS_MAP.items():
        payload = {
            "name": name,
            "unique_id": uid,
            "state_topic": topic,
            "availability_topic": STATUS_TOPIC,
            "device_class": dc,
            "unit_of_measurement": unit,
            "state_class": sc,
            "device": HA_DEVICE,
        }
        client.publish(f"homeassistant/sensor/{uid}/config", json.dumps(payload), retain=True)
    for obis, (topic, uid, name) in DIAG_MAP.items():
        payload = {
            "name": name,
            "unique_id": uid,
            "state_topic": topic,
            "availability_topic": STATUS_TOPIC,
            "entity_category": "diagnostic",
            "device": HA_DEVICE,
        }
        client.publish(f"homeassistant/sensor/{uid}/config", json.dumps(payload), retain=True)
    log.info("MQTT discovery published")


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        publish_discovery(client)
    else:
        log.error("MQTT connect refused: %s", reason_code)


def connect_mqtt():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "stromzaehler")
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    client.will_set(STATUS_TOPIC, "offline", retain=True)
    client.on_connect = on_connect
    while True:
        try:
            client.connect(MQTT_HOST)
            break
        except OSError as e:
            log.error("MQTT connect failed: %s — retrying in 10s", e)
            time.sleep(10)
    client.loop_start()
    return client


def check_bcc(raw, tail):
    """Verify the IEC 62056-21 block check character.

    raw  = every byte received so far (ident line + data lines incl. the "!" line)
    tail = the bytes following the "!" line: ETX + BCC
    BCC is the XOR of all bytes after STX up to and including ETX.
    Returns True/False, or None if the meter sent no STX/ETX framing.
    """
    stx = raw.find(STX)
    if stx < 0 or len(tail) < 2 or tail[:1] != ETX:
        return None
    bcc = 0
    for b in raw[stx + 1:] + ETX:
        bcc ^= b
    return bcc == tail[1]


def read_telegram(port):
    """Trigger meter and read one full telegram.

    Returns (data_lines, bcc_ok); bcc_ok is None when the telegram carries no BCC.
    """
    # Flush any stale bytes (e.g. BCC checksum after !, noise during sleep)
    # before sending the trigger to avoid corrupting the next read.
    port.reset_input_buffer()
    # Request telegram (IEC 62056-21 sign-on message)
    port.write(b"\x2f\x3f\x21\x0d\x0a")
    port.flush()

    raw = b""
    lines = []
    bcc_ok = None
    while True:
        chunk = port.readline()
        if not chunk:
            log.warning("Serial read timeout — no data from meter")
            break
        raw += chunk
        line = chunk.decode("latin-1", errors="replace").strip()
        if not line:
            continue
        if line.startswith("!"):
            # ETX + BCC follow the closing line (2 bytes ≈ 0.1s at 300 baud)
            timeout, port.timeout = port.timeout, 2
            tail = port.read(2)
            port.timeout = timeout
            bcc_ok = check_bcc(raw, tail)
            break   # end of telegram
        if line.startswith("/"):
            log.debug("Meter ident: %s", line)
            continue  # skip identification header
        lines.append(line)

    return lines, bcc_ok


def main():
    log.info("strom-zaehler starting — device=%s mqtt=%s", DEVICE, MQTT_HOST)

    client = connect_mqtt()

    while True:
        try:
            with serial.Serial(
                DEVICE, baudrate=300, bytesize=7,
                parity="E", stopbits=1, timeout=30
            ) as port:
                log.info("Serial port opened: %s", DEVICE)
                failures = 0
                logged_first = False
                while True:
                    started = time.monotonic()
                    lines, bcc_ok = read_telegram(port)
                    published = []

                    if bcc_ok is False:
                        log.warning("BCC mismatch — discarding corrupted telegram")
                        lines = []
                    elif bcc_ok is None and lines:
                        log.debug("Telegram has no BCC — not verified")

                    if not lines:
                        log.warning("Empty telegram received")
                    else:
                        values = parse_telegram(lines)
                        if not logged_first:
                            log.info("First telegram: %s", lines)
                            logged_first = True

                        for obis, (topic, uid, name) in DIAG_MAP.items():
                            for code in (obis,) + DIAG_ALIASES.get(obis, ()):
                                if code in values:
                                    client.publish(topic, values[code][0], retain=True)
                                    break

                        for obis, (topic, uid, name, dc, u, sc) in OBIS_MAP.items():
                            if obis in values:
                                value, unit = values[obis]
                                if unit is not None and unit != u:
                                    log.warning("Unexpected unit for %s: %s (expected %s)", obis, unit, u)
                                    continue
                                try:
                                    number = float(value)
                                except ValueError:
                                    log.warning("Non-numeric value for %s: %s", obis, value)
                                    continue
                                client.publish(topic, str(number), retain=True)
                                published.append(f"{obis}={number}")

                        if published:
                            log.info("Published: %s", "  ".join(published))
                        else:
                            log.warning("No known OBIS codes found in telegram: %s", lines)

                    if published:
                        failures = 0
                        client.publish(STATUS_TOPIC, "online", retain=True)
                    else:
                        failures += 1
                        if failures == OFFLINE_AFTER:
                            client.publish(STATUS_TOPIC, "offline", retain=True)

                    time.sleep(max(0, POLL_INTERVAL - (time.monotonic() - started)))

        except serial.SerialException as e:
            log.error("Serial error: %s — retrying in 10s", e)
            client.publish(STATUS_TOPIC, "offline", retain=True)
            time.sleep(10)
        except Exception:
            log.exception("Unexpected error — retrying in 10s")
            time.sleep(10)


if __name__ == "__main__":
    main()
