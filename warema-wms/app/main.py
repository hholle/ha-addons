"""Warema WMS → MQTT bridge with Home Assistant discovery.

Topics:
  warema/<snr>/state         → open | closed | stopped | opening | closing
  warema/<snr>/position      → 0–100 (HA scale: 100 = open)
  warema/<snr>/tilt          → angle
  warema/<snr>/availability  → online | offline
  warema/<snr>/set           ← OPEN | CLOSE | STOP
  warema/<snr>/set_position  ← 0–100
  warema/<snr>/set_tilt      ← angle
  warema/bridge/state        → online | offline (last will)
"""

import json
import logging
import os
import signal
import sys
import threading
import time

import paho.mqtt.client as mqtt

from .wms import Stick

log = logging.getLogger("warema")

BRIDGE = "warema/bridge/state"
SCAN_TOPIC = "warema/bridge/scan"
DEBOUNCE = 0.7            # s, absorbs slider drags in HA
QUIET_PERIOD = 60         # s an unresponsive device is left alone before polling resumes

# WMS device type → HA model name. Remote controls (07) and web controllers (09) are ignored.
MODELS = {
    0x06: "Weather station eco",
    0x20: "Plug receiver",
    0x21: "Actuator UP",
    0x25: "Vertical awning",
    0x2A: "Lamellendach",
}
# Type 2A motors only react to these tilt angles; no position, no poll answers.
TILT_STEPS_2A = (-60, -33, 0, 33, 66, 100)
TILT_OPEN_2A, TILT_CLOSED_2A = 100, -60

WEATHER = (  # key, component, device_class, unit
    ("illuminance", "sensor", "illuminance", "lx"),
    ("temperature", "sensor", "temperature", "°C"),
    ("wind", "sensor", "wind_speed", "m/s"),
    ("rain", "binary_sensor", "moisture", None),
)


DISCOVERY_HELP = (
    "PAN ID FFFF: network discovery (no MQTT connection, no devices). Steps:",
    "- Open the battery case of the WMS handheld transmitter.",
    "- Select a channel with the (+) button. If the channel is unassigned, hold (+) for 5 s until the LED",
    "  flashes; then all channels can be selected.",
    "- Hold the learn button in the battery case for about 5 s: LEDs go green and the handheld scans.",
    "- Every time the scan stops with a red LED, press (A) to see which device was found:",
    "    * no \"*** Waving and Hello!\" in this log: another device, press (C) for the next receiver",
    "    * \"*** Waving and Hello!\" in this log: that is the stick; press STOP to assign it to the channel",
    "- After STOP the channel, PAN ID and key are logged here. Put them into the options and restart.",
    "Keep the handheld close to the stick. Stop the add-on to abort.",
)


class Device:
    def __init__(self, snr, type_):
        self.snr, self.type = snr, type_
        self.position = self.angle = None   # last WMS values (0 = open, 100 = closed)
        self.last_seen = time.monotonic()
        self.offline = False


def parse_groups(text):
    """"Name:snr1,snr2;Other:snr3" → {snr: (name, group_id)}"""
    groups = {}
    for group in filter(None, (g.strip() for g in text.split(";"))):
        name, _, snrs = group.partition(":")
        if name and snrs:
            for snr in snrs.split(","):
                groups[snr.strip()] = (name.strip(), "warema_group_" + "_".join(name.split()))
    return groups


def snap_tilt_2a(value):
    return min(TILT_STEPS_2A, key=lambda step: abs(step - value))


class Bridge:
    def __init__(self, env):
        self.ignored = {s.strip() for s in env.get("IGNORED_DEVICES", "").split(",") if s.strip()}
        self.forced = [s.strip() for s in env.get("FORCE_DEVICES", "").split(",") if s.strip()]
        self.groups = parse_groups(env.get("DEVICE_GROUPS", ""))
        self.poll = int(env.get("POLLING_INTERVAL") or 30000) / 1000
        self.moving = int(env.get("MOVING_INTERVAL") or 1000) / 1000
        self.rescan_minutes = int(env.get("RESCAN_INTERVAL") or 0)
        self.devices = {}
        self.timers = {}
        self.scan_lock = threading.Lock()
        self.connected = threading.Event()

        self.stick = Stick(
            env.get("WMS_SERIAL_PORT") or "/dev/ttyUSB0",
            int(env.get("WMS_CHANNEL") or 17),
            env.get("WMS_PAN_ID") or "FFFF",
            env.get("WMS_KEY") or "00112233445566778899AABBCCDDEEFF",
            self.poll, self.moving,
            on_position=self.on_position, on_seen=self.on_seen,
            on_weather=self.on_weather, on_join=self.on_join,
        )

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "warema")
        self.client.username_pw_set(env.get("MQTT_USER"), env.get("MQTT_PASSWORD"))
        if env.get("MQTT_SSL") == "true":
            self.client.tls_set()
        self.client.will_set(BRIDGE, "offline", retain=True)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.mqtt_host = env.get("MQTT_HOST", "localhost")
        self.mqtt_port = int(env.get("MQTT_PORT") or 1883)

    # -- MQTT -----------------------------------------------------------------

    def publish(self, topic, payload, retain=True):
        self.client.publish(topic, payload, retain=retain)

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            log.error("MQTT connect refused: %s", reason_code)
            return
        log.info("Connected to MQTT %s:%s", self.mqtt_host, self.mqtt_port)
        client.subscribe([("warema/+/set", 0), ("warema/+/set_position", 0), ("warema/+/set_tilt", 0),
                          (SCAN_TOPIC, 0), ("homeassistant/status", 0)])
        self.publish(BRIDGE, "online")
        self.publish_all()
        self.connected.set()

    def publish_all(self):
        button = {  # "Scan for devices" button on a bridge device
            "name": "Scan for devices", "unique_id": "warema_bridge_scan", "entity_category": "config",
            "command_topic": SCAN_TOPIC, "payload_press": "PRESS",
            "availability": [{"topic": BRIDGE}],
            "device": {"identifiers": ["warema_bridge"], "name": "Warema WMS", "manufacturer": "Warema"},
        }
        self.publish("homeassistant/button/warema_bridge/scan/config", json.dumps(button))
        for dev in self.devices.values():
            self.publish_discovery(dev)

    def on_message(self, client, userdata, msg):
        message = msg.payload.decode(errors="replace")
        if msg.topic == "homeassistant/status":
            if message == "online":
                log.info("Home Assistant online — re-publishing discovery")
                self.publish_all()
            return
        if msg.topic == SCAN_TOPIC:
            threading.Thread(target=self.scan, daemon=True).start()
            return
        _, snr, command = msg.topic.split("/")
        log.debug("%s %s: %s", snr, command, message)
        try:
            self.command(snr, command, message)
        except Exception:
            log.exception("Cannot handle %s=%s for %s", command, message, snr)

    # -- Home Assistant commands ----------------------------------------------

    def command(self, snr, command, message):
        dev = self.devices.get(snr)
        if not dev or dev.type == 0x06:
            log.warning("%s for unknown device %s", command, snr)
            return
        n = int(snr)
        if command == "set":
            if message == "STOP":
                self.stick.stop(n)
            elif message in ("OPEN", "CLOSE"):
                opening = message == "OPEN"
                if dev.type == 0x2A:   # tilt-only motor: position is ignored
                    tilt = TILT_OPEN_2A if opening else TILT_CLOSED_2A
                    self.stick.set_position(n, 0, tilt)
                    self.set_tilt_state(dev, tilt)
                else:
                    self.stick.set_position(n, 0 if opening else 100, dev.angle or 0)
                self.publish(f"warema/{snr}/state", "opening" if opening else "closing", retain=False)
        elif command == "set_position":
            # HA: 100 = open; WMS: 0 = open
            wms_pos = 100 - int(float(message))
            self.debounce(f"{snr}:pos", lambda: self.stick.set_position(n, wms_pos, dev.angle or 0))
        elif command == "set_tilt":
            angle = int(float(message))
            if dev.type == 0x2A:
                angle = snap_tilt_2a(angle)
            self.debounce(f"{snr}:tilt", lambda: self.send_tilt(dev, angle))

    def send_tilt(self, dev, angle):
        self.stick.set_position(int(dev.snr), dev.position or 0, angle)
        if dev.type == 0x2A:   # no poll answers from this motor: assume the command worked
            self.set_tilt_state(dev, angle)

    def set_tilt_state(self, dev, angle):
        dev.angle = angle
        self.publish(f"warema/{dev.snr}/tilt", str(angle))

    def debounce(self, key, fn):
        if key in self.timers:
            self.timers[key].cancel()
        self.timers[key] = threading.Timer(DEBOUNCE, fn)
        self.timers[key].start()

    # -- Devices --------------------------------------------------------------

    def register(self, snr, type_):
        snr = str(snr)
        if type_ not in MODELS:
            log.debug("Ignoring device %s (type %02X)", snr, type_)
            return
        if snr in self.ignored:
            log.debug("Ignoring device %s (configured)", snr)
            return
        if snr in self.devices:
            return False
        log.info("Adding device %s (%s)", snr, MODELS[type_])
        dev = self.devices[snr] = Device(snr, type_)
        if type_ != 0x06:   # weather stations broadcast on their own
            self.stick.add_blind(int(snr), no_poll=type_ == 0x2A)
        self.publish_discovery(dev)
        return True

    def scan(self):
        """Radio scan; registers devices that are not known yet."""
        if not self.scan_lock.acquire(blocking=False):
            log.info("Scan already running")
            return
        try:
            log.info("Scanning for WMS devices...")
            new = [snr for snr, type_ in self.stick.scan() if self.register(snr, int(type_, 16))]
            log.info("Scan finished: %d new device(s), %d registered", len(new), len(self.devices))
        finally:
            self.scan_lock.release()

    def rescan_loop(self):
        while True:
            time.sleep(self.rescan_minutes * 60)
            self.scan()

    def publish_discovery(self, dev):
        snr, model = dev.snr, MODELS[dev.type]
        group = self.groups.get(snr)
        device = ({"identifiers": [group[1]], "name": group[0], "manufacturer": "Warema"} if group else
                  {"identifiers": [f"warema_{snr}"], "name": f"{model} {snr}",
                   "model": model, "manufacturer": "Warema"})
        base = {
            "availability": [{"topic": BRIDGE}, {"topic": f"warema/{snr}/availability"}],
            "availability_mode": "all",
            "device": device,
            "name": None,   # entity takes the device (or device class) name
        }
        t = f"warema/{snr}"

        if dev.type == 0x06:
            for key, component, device_class, unit in WEATHER:
                payload = {**base, "unique_id": f"{snr}_{key}",
                           "state_topic": f"{t}/{key}/state", "device_class": device_class}
                if unit:
                    payload.update(unit_of_measurement=unit, state_class="measurement")
                self.publish(f"homeassistant/{component}/{snr}/{key}/config", json.dumps(payload))
        else:
            payload = {**base, "unique_id": snr, "command_topic": f"{t}/set"}
            # position_open/position_closed are left at the HA defaults (100 = open);
            # the WMS scale (0 = open) is inverted in this bridge.
            if dev.type != 0x2A:
                payload.update(position_topic=f"{t}/position", set_position_topic=f"{t}/set_position")
            if dev.type in (0x20, 0x25):
                payload["state_topic"] = f"{t}/state"
            if dev.type in (0x20, 0x21):
                payload.update(tilt_status_topic=f"{t}/tilt", tilt_command_topic=f"{t}/set_tilt",
                               tilt_min=-100, tilt_max=100, tilt_opened_value=100, tilt_closed_value=-100)
            if dev.type == 0x2A:   # tilt only: OPEN/CLOSE map to the two end angles
                payload.update(tilt_status_topic=f"{t}/tilt", tilt_command_topic=f"{t}/set_tilt",
                               tilt_min=TILT_CLOSED_2A, tilt_max=TILT_OPEN_2A,
                               tilt_opened_value=TILT_OPEN_2A, tilt_closed_value=TILT_CLOSED_2A)
            self.publish(f"homeassistant/cover/{snr}/{snr}/config", json.dumps(payload))
        self.publish(f"{t}/availability", "offline" if dev.offline else "online")

    # -- Stick events ---------------------------------------------------------

    def on_position(self, snr, pos, ang, moving, changed):
        dev = self.devices.get(str(snr))
        if not dev or not changed:
            return
        dev.position, dev.angle = pos, ang
        t = f"warema/{snr}"
        self.publish(f"{t}/position", str(100 - pos))
        if not moving:   # WMS 0 = open, 100 = closed
            self.publish(f"{t}/state", "open" if pos == 0 else "closed" if pos == 100 else "stopped")
        self.publish(f"{t}/tilt", str(ang))

    def on_seen(self, snr):
        dev = self.devices.get(str(snr))
        if dev:
            dev.last_seen = time.monotonic()
            if dev.offline:
                log.info("Device %s is back online", snr)
                self.set_offline(dev, False)

    def on_weather(self, snr, w):
        if str(snr) not in self.devices:
            self.register(snr, 0x06)
        if str(snr) not in self.devices:   # ignored
            return
        t = f"warema/{snr}"
        self.publish(f"{t}/illuminance/state", str(w["lumen"]))
        self.publish(f"{t}/temperature/state", str(w["temp"]))
        self.publish(f"{t}/wind/state", str(w["wind"]))
        self.publish(f"{t}/rain/state", "ON" if w["rain"] else "OFF")

    def on_join(self, p):
        log.info("*** WMS network parameters successfully detected:")
        log.info("    Channel: %s", p["channel"])
        log.info("    PAN ID:  %s", p["pan_id"])
        log.info("    Key:     %s", p["key"])
        log.info("Write them down, set them in the add-on options and restart.")

    def set_offline(self, dev, offline):
        dev.offline = offline
        blind = self.stick.blinds.get(int(dev.snr))
        if blind:
            blind.paused = offline
        self.publish(f"warema/{dev.snr}/availability", "offline" if offline else "online")

    def watchdog(self):
        """Devices silent for 3 poll cycles go offline and are left alone for a
        minute: a flooded radio channel makes them stop answering."""
        time.sleep(self.poll * 4)
        while True:
            for dev in list(self.devices.values()):
                if dev.type in (0x06, 0x2A) or dev.offline:
                    continue
                silent = time.monotonic() - dev.last_seen
                if silent > self.poll * 3:
                    log.warning("Device %s silent for %ds — offline for %ds", dev.snr, silent, QUIET_PERIOD)
                    self.set_offline(dev, True)
                    threading.Timer(QUIET_PERIOD, self.resume, [dev]).start()
            time.sleep(self.poll * 1.5)

    def resume(self, dev):
        if dev.offline:
            log.info("Device %s: quiet period over, polling again", dev.snr)
            dev.last_seen = time.monotonic()
            self.set_offline(dev, False)

    # -- main -----------------------------------------------------------------

    def run(self):
        self.stick.start()
        self.stick.wait_ready()
        if self.stick.pan_id.upper() == "FFFF":
            for line in DISCOVERY_HELP:
                log.info(line)
            threading.Timer(180, log.warning, ["No network parameters received after 3 minutes — "
                                               "keep the remote close to the stick and try again."]).start()
            return
        self.client.connect_async(self.mqtt_host, self.mqtt_port)
        self.client.loop_start()
        self.connected.wait()

        if self.forced:   # known devices: skip the radio scan
            for entry in self.forced:
                snr, _, type_ = entry.partition(":")
                self.register(snr, int(type_ or "25", 16))
        else:
            self.scan()
        log.info("Registered devices: %s", sorted(self.devices))
        threading.Thread(target=self.watchdog, daemon=True).start()
        if self.rescan_minutes:
            threading.Thread(target=self.rescan_loop, daemon=True).start()

    def shutdown(self, *_):
        log.info("Shutting down")
        if self.connected.is_set():
            self.client.publish(BRIDGE, "offline", retain=True).wait_for_publish(2)
            self.client.disconnect()
        os._exit(0)


def main():
    name = os.environ.get("LOG_LEVEL", "info").lower()
    level = {"error": "ERROR", "warn": "WARNING", "warning": "WARNING", "debug": "DEBUG",
             "verbose": "DEBUG", "silly": "DEBUG"}.get(name, "INFO")
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    bridge = Bridge(os.environ)
    signal.signal(signal.SIGTERM, bridge.shutdown)
    signal.signal(signal.SIGINT, bridge.shutdown)
    bridge.run()
    signal.pause()


if __name__ == "__main__":
    main()
