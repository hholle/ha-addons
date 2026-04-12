#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KACO Powador solar inverter → MQTT bridge.
Reads data continuously from RS232 serial and publishes to Home Assistant via MQTT discovery.
"""

import json
import logging
import os
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

HA_DEVICE = {
    "identifiers": ["wechselrichter"],
    "name": "KACO Powador",
    "model": "KACO Powador",
    "manufacturer": "KACO new energy",
}

# (unique_id, name, state_topic, device_class, unit, state_class)
SENSORS = [
    ("wechselrichter_einspeisung",       "Eingespeiste Leistung",  "wechselrichter/einspeisung",         "power",       "W",  "measurement"),
    ("wechselrichter_temperatur",         "Gerätetemperatur",       "wechselrichter/temperatur",           "temperature", "°C", "measurement"),
    ("wechselrichter_generator_spannung", "Generator Spannung",     "wechselrichter/generator_spannung",   "voltage",     "V",  "measurement"),
    ("wechselrichter_generator_strom",    "Generator Strom",        "wechselrichter/generator_strom",      "current",     "A",  "measurement"),
    ("wechselrichter_netz_spannung",      "Netzspannung",           "wechselrichter/netz_spannung",        "voltage",     "V",  "measurement"),
    ("wechselrichter_netz_strom",         "Netzstrom",              "wechselrichter/netz_strom",           "current",     "A",  "measurement"),
    ("wechselrichter_betriebszustand",    "Betriebszustand",        "wechselrichter/betriebszustand",      None,          None, None),
]

# KACO Powador operating state codes (table 4.2)
BETRIEBSZUSTAND = {
    "0": "Nacht",
    "1": "Warten",
    "2": "Einspeisen",
    "3": "Abschaltung (Netz)",
    "4": "Störung",
    "5": "Fehler",
}


def publish_discovery(client):
    for uid, name, topic, dc, unit, sc in SENSORS:
        payload = {
            "name": name,
            "unique_id": uid,
            "state_topic": topic,
            "device": HA_DEVICE,
        }
        if dc:   payload["device_class"] = dc
        if unit: payload["unit_of_measurement"] = unit
        if sc:   payload["state_class"] = sc
        client.publish(f"homeassistant/sensor/{uid}/config", json.dumps(payload), retain=True)
    log.info("MQTT discovery published")


def connect_mqtt():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "wechselrichter")
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    client.connect(MQTT_HOST)
    client.loop_start()
    return client


def parse_and_publish(client, line):
    values = line.split()
    if len(values) < 10:
        log.warning("Short line, skipping: %s", line)
        return

    # KACO Powador RS232 line format (space-separated):
    # idx: 0          1         2                3                    4                5   6             7          8                    9
    #      date       time      Betriebszustand  Generator-Spannung   Generator-Strom  ?   Netzspannung  Netzstrom  Eingespeiste-Leistung Gerätetemperatur
    # e.g: 00.00.0000 15:38:50  2                229.6                0.00             0   226.7         0.00       0                     33
    betriebszustand_raw = values[2]
    betriebszustand     = BETRIEBSZUSTAND.get(betriebszustand_raw, betriebszustand_raw)

    client.publish("wechselrichter/betriebszustand",    betriebszustand, retain=True)
    client.publish("wechselrichter/generator_spannung", values[3],       retain=True)
    client.publish("wechselrichter/generator_strom",    values[4],       retain=True)
    client.publish("wechselrichter/netz_spannung",      values[6],       retain=True)
    client.publish("wechselrichter/netz_strom",         values[7],       retain=True)
    client.publish("wechselrichter/einspeisung",        values[8],       retain=True)
    client.publish("wechselrichter/temperatur",         values[9],       retain=True)

    log.info("einspeisung=%sW temp=%s°C zustand=%s", values[8], values[9], betriebszustand)


def main():
    log.info("wechselrichter starting — device=%s mqtt=%s", DEVICE, MQTT_HOST)

    client = connect_mqtt()
    publish_discovery(client)

    while True:
        try:
            with serial.Serial(DEVICE) as port:
                log.info("Serial port opened: %s", DEVICE)
                while True:
                    line = port.readline().decode("utf-8", errors="replace").strip()
                    if len(line) < 3:
                        continue
                    parse_and_publish(client, line)
        except serial.SerialException as e:
            log.error("Serial error: %s — retrying in 10s", e)
            time.sleep(10)
        except Exception:
            log.exception("Unexpected error — retrying in 10s")
            time.sleep(10)


if __name__ == "__main__":
    main()
