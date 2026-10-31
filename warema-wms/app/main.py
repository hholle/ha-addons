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
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from .stats import LinkStats
from wmslib import Stick

log = logging.getLogger("warema")

BRIDGE = "warema/bridge/state"
SCAN_TOPIC = "warema/bridge/scan"
DEBOUNCE = 0.7            # s, absorbs slider drags in HA
STATS_INTERVAL = 60       # s between radio link diagnostics updates
QUALITY_SENSORS = (  # key, name: raw bytes from the stick's connection quality answer (units not documented)
    ("tx_lqi", "TX link quality (LQI)"),
    ("tx_rssi", "TX signal (RSSI)"),
    ("rx_lqi", "RX link quality (LQI)"),
    ("rx_rssi", "RX signal (RSSI)"),
)
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


LINK_SENSORS = (  # key, name, device_class, unit, state_class (diagnostics of the radio link)
    ("reach", "Reachability", None, "%", "measurement"),
    ("latency", "Response time", "duration", "ms", "measurement"),
    ("retries", "Poll retries (1 h)", None, None, "measurement"),
    ("offline_events", "Offline events", None, None, "total_increasing"),
    ("last_seen", "Last seen", "timestamp", None, None),
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
        self.last_seen_wall = None          # wall clock of the last real answer (None: not yet)
        self.offline = False
        self.direction = "opening"          # last commanded direction (tilt-only motors)
        self.quality_seen = False           # the stick answered a connection quality request for this device
        self.stats = LinkStats()


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
        q = env.get("QUALITY_INTERVAL")
        self.quality_interval = int(q) if q not in (None, "") else 300
        self.quality_enabled = self.quality_interval > 0
        self.quality_ever = False   # at least one device answered
        self.quality_misses = 0
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
            on_weather=self.on_weather, on_join=self.on_join, on_poll=self.on_poll,
            on_command_failed=self.on_command_failed,
        )

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "warema")
        self.client.username_pw_set(env.get("MQTT_USER"), env.get("MQTT_PASSWORD"))
        if env.get("MQTT_SSL") == "true":
            self.client.tls_set()
        self.client.will_set(BRIDGE, "offline", retain=True)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect
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
                          ("warema/+/ping", 0), ("warema/+/identify", 0),
                          (SCAN_TOPIC, 0), ("homeassistant/status", 0)])
        self.publish(BRIDGE, "online")
        self.publish_all()
        self.connected.set()

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        log.warning("MQTT disconnected (%s) — reconnecting", reason_code)

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
        log.info("Command from HA: %s %s = %s", snr, command, message)
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
        if command == "ping":
            self.ping(dev)
        elif command == "identify":
            log.info("Device %s: wave (identify)", snr)
            self.stick.beckon(n, lambda ok: log.info("Device %s: wave %s", snr, "acknowledged" if ok else "not acknowledged"))
        elif command == "set":
            if message == "STOP":
                self.stick.stop(n)
                if dev.type == 0x2A:
                    self.stick.watch(n)
            elif message in ("OPEN", "CLOSE"):
                opening = message == "OPEN"
                if dev.type == 0x2A:   # tilt-only motor: position is ignored
                    tilt = TILT_OPEN_2A if opening else TILT_CLOSED_2A
                    self.stick.set_position(n, 0, tilt)
                    self.set_tilt_state(dev, tilt)
                    dev.direction = "opening" if opening else "closing"
                    self.stick.watch(n)
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
        if dev.type == 0x2A:   # assume the command worked; the follow-up polls correct it
            dev.direction = "opening" if angle > (dev.angle if dev.angle is not None else TILT_CLOSED_2A) else "closing"
            self.set_tilt_state(dev, angle)
            self.publish(f"warema/{dev.snr}/state", dev.direction, retain=False)
            self.stick.watch(int(dev.snr))

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
            if type_ == 0x2A:   # never polled otherwise: ask once at startup so HA does not keep a stale state
                self.stick.watch(int(snr))
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
                payload.update(state_topic=f"{t}/state", tilt_status_topic=f"{t}/tilt", tilt_command_topic=f"{t}/set_tilt",
                               tilt_min=TILT_CLOSED_2A, tilt_max=TILT_OPEN_2A,
                               tilt_opened_value=TILT_OPEN_2A, tilt_closed_value=TILT_CLOSED_2A)
            self.publish(f"homeassistant/cover/{snr}/{snr}/config", json.dumps(payload))
            # grouped devices share one HA device: the serial number tells their entities apart
            tag = f"{snr} " if group else ""
            for key, name, device_class, category in (("ping", "Ping", None, "diagnostic"),
                                                      ("identify", "Identify", "identify", "config")):
                button = {"name": tag + name, "unique_id": f"{snr}_{key}_button", "command_topic": f"{t}/{key}",
                          "payload_press": "PRESS", "entity_category": category,
                          "availability": [{"topic": BRIDGE}], "device": device}
                if device_class:
                    button["device_class"] = device_class
                self.publish(f"homeassistant/button/{snr}/{key}/config", json.dumps(button))
            ping = {"name": tag + "Last ping", "unique_id": f"{snr}_ping_result", "state_topic": f"{t}/link/ping",
                    "availability": [{"topic": BRIDGE}], "entity_category": "diagnostic", "device": device}
            self.publish(f"homeassistant/sensor/{snr}/ping/config", json.dumps(ping))
            if dev.quality_seen:   # only for devices where the stick answered a quality request
                for key, name in QUALITY_SENSORS:
                    sensor = {"name": tag + name, "unique_id": f"{snr}_{key}", "state_topic": f"{t}/link/{key}",
                              "availability": [{"topic": BRIDGE}], "entity_category": "diagnostic",
                              "state_class": "measurement", "device": device}
                    self.publish(f"homeassistant/sensor/{snr}/{key}/config", json.dumps(sensor))
            if dev.type != 0x2A:   # polled devices get radio link diagnostics
                for key, name, device_class, unit, state_class in LINK_SENSORS:
                    sensor = {"name": tag + name, "unique_id": f"{snr}_{key}", "state_topic": f"{t}/link/{key}",
                              "availability": [{"topic": BRIDGE}],   # also meaningful while the device is offline
                              "entity_category": "diagnostic", "device": device}
                    if device_class:
                        sensor["device_class"] = device_class
                    if unit:
                        sensor["unit_of_measurement"] = unit
                    if state_class:
                        sensor["state_class"] = state_class
                    self.publish(f"homeassistant/sensor/{snr}/{key}/config", json.dumps(sensor))
        self.publish(f"{t}/availability", "offline" if dev.offline else "online")

    # -- Stick events ---------------------------------------------------------

    def on_position(self, snr, pos, ang, moving, changed):
        dev = self.devices.get(str(snr))
        if not dev or not changed:
            return
        if dev.type == 0x2A:
            self.on_tilt_only(dev, ang, moving)
            return
        dev.position = pos
        if ang is not None:   # products without slats report no angle
            dev.angle = ang
        log.info("Device %s: position %d%% open%s, %s", snr, 100 - pos,
                 "" if ang is None else f", tilt {ang}", "moving" if moving else "stopped")
        t = f"warema/{snr}"
        self.publish(f"{t}/position", str(100 - pos))
        if not moving:   # WMS 0 = open, 100 = closed
            self.publish(f"{t}/state", "open" if pos == 0 else "closed" if pos == 100 else "stopped")
        if ang is not None:
            self.publish(f"{t}/tilt", str(ang))

    def on_seen(self, snr):
        dev = self.devices.get(str(snr))
        if dev:
            dev.last_seen = time.monotonic()
            dev.last_seen_wall = time.time()
            if dev.offline:
                log.info("Device %s is back online", snr)
                self.set_offline(dev, False)

    def on_tilt_only(self, dev, ang, moving):
        """Answer of a type 2A motor: its angle is real, the position byte is not."""
        if ang is None:
            return
        dev.angle = ang
        state = dev.direction if moving else (
            "open" if ang >= TILT_OPEN_2A else "closed" if ang <= TILT_CLOSED_2A else "stopped")
        log.info("Device %s: tilt %d, %s", dev.snr, ang, state)
        self.publish(f"warema/{dev.snr}/tilt", str(ang))
        self.publish(f"warema/{dev.snr}/state", state)

    def on_command_failed(self, snr):
        """No answer to a move command: do not leave the optimistic opening/closing state behind."""
        dev = self.devices.get(str(snr))
        if not dev or dev.type == 0x2A or dev.position is None:
            return
        pos = dev.position
        self.publish(f"warema/{snr}/state", "open" if pos == 0 else "closed" if pos == 100 else "stopped")

    def on_poll(self, snr, ok, attempts, latency):
        dev = self.devices.get(str(snr))
        if not dev:
            return
        dev.stats.record(time.time(), ok, attempts, latency)
        if ok and attempts > 1:
            log.info("Device %s: answered after %d attempts (%s ms)", snr, attempts, latency)
        elif ok:
            log.debug("Device %s: answered in %s ms", snr, latency)

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
        if offline and not dev.offline:
            dev.stats.offline_events += 1
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

    def link_values(self, dev, now):
        s = dev.stats
        values = {"retries": s.retries(now), "offline_events": s.offline_events}
        for key, value in (("reach", s.reach(now)), ("latency", s.latency(now))):
            if value is not None:
                values[key] = value
        if dev.last_seen_wall:
            values["last_seen"] = datetime.fromtimestamp(dev.last_seen_wall, timezone.utc).isoformat()
        return values

    def ping(self, dev):
        """Radio ping from a button press; the result goes to the "Last ping" sensor and the log."""
        def done(ok, latency):
            text = f"ok ({latency} ms)" if ok else "no answer"
            log.info("Device %s: ping %s", dev.snr, text)
            self.publish(f"warema/{dev.snr}/link/ping", text)
        self.stick.ping(int(dev.snr), done)

    def quality_loop(self):
        """Ask the stick for each device's link quality (LQI/RSSI) every quality_interval seconds."""
        time.sleep(30)
        while self.quality_enabled:
            if not any(b.moving for b in self.stick.blinds.values()):
                for dev in list(self.devices.values()):
                    if dev.type == 0x06 or not self.quality_enabled:
                        continue
                    self.stick.connection_quality(int(dev.snr), lambda values, failure, d=dev: self.on_quality(d, values, failure))
            time.sleep(self.quality_interval)

    def on_quality(self, dev, values, failure):
        if failure is not None:
            log.warning("The stick does not support connection quality requests (failure code %r); "
                        "signal diagnostics are switched off", failure)
            self.quality_enabled = False
            return
        if values is None:
            self.quality_misses += 1
            log.debug("Device %s: no connection quality answer", dev.snr)
            if not self.quality_ever and self.quality_misses >= 6:
                log.warning("No device ever answered a connection quality request; signal diagnostics are switched off")
                self.quality_enabled = False
            return
        self.quality_ever = True
        log.debug("Device %s: link quality %s", dev.snr, values)
        if not dev.quality_seen:
            dev.quality_seen = True
            log.info("Device %s: connection quality available (TX LQI %d, RSSI %d; RX LQI %d, RSSI %d)", dev.snr,
                     values["tx_lqi"], values["tx_rssi"], values["rx_lqi"], values["rx_rssi"])
            self.publish_discovery(dev)
        for key, _ in QUALITY_SENSORS:
            self.publish(f"warema/{dev.snr}/link/{key}", str(values[key]))

    def stats_loop(self):
        """Publish radio link diagnostics every minute and log a summary every 5 minutes."""
        cycle = 0
        while True:
            time.sleep(STATS_INTERVAL)
            cycle += 1
            now = time.time()
            for dev in list(self.devices.values()):
                if dev.type in (0x06, 0x2A):
                    continue
                values = self.link_values(dev, now)
                for key, value in values.items():
                    self.publish(f"warema/{dev.snr}/link/{key}", str(value))
                if cycle % 5 == 0:
                    log.info("Device %s link (1 h): %s%% of %d polls answered, response %s ms, "
                             "%d retries, %d offline event(s)%s", dev.snr, values.get("reach", "-"),
                             dev.stats.count(now), values.get("latency", "-"), values["retries"],
                             dev.stats.offline_events, " — offline now" if dev.offline else "")

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
        threading.Thread(target=self.stats_loop, daemon=True).start()
        if self.quality_enabled:
            threading.Thread(target=self.quality_loop, daemon=True).start()
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
