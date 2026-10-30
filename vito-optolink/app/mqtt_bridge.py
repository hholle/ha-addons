"""MQTT side: discovery, state publishing and availability."""

import json
import logging

import paho.mqtt.client as mqtt

log = logging.getLogger(__name__)

BASE_TOPIC = "vito_optolink"
AVAILABILITY_TOPIC = BASE_TOPIC + "/status"

# Same device identifier as the vcontrold add-on: existing entities keep their device.
HA_DEVICE = {
    "identifiers": ["vcontrold"],
    "name": "Viessmann Heizung",
    "model": "Vitotronic 200 (VScotHO1)",
    "manufacturer": "Viessmann",
}


# entities of earlier versions that are gone: their retained discovery messages are cleared on connect
OBSOLETE = [
    ("sensor", "vcontrold_drehzahl_pumpe"),     # 7663 is a 0/1 status, not a speed
    ("sensor", "vcontrold_temp_rl_ist"),        # 0808 is the flue gas sensor; there is no return flow sensor
] + [("sensor", "vito_kodierung_" + code) for code in ("56", "58", "59", "60", "62", "6d", "6f", "06")]


def state_topic(dp):
    return "%s/%s" % (BASE_TOPIC, dp.id)


def attributes_topic(dp):
    return state_topic(dp) + "/attributes"


def discovery_topic(dp):
    return "homeassistant/%s/%s/config" % (dp.component, dp.uid)


def display_precision(dp):
    """Decimals for the Home Assistant display, so 22.5 stays 22.5 and 249 does not become 249.0."""
    if dp.component != "sensor" or dp.type == "enum":
        return None
    if dp.precision is not None:
        return dp.precision
    if dp.scale != 1.0:
        return 1
    return 0 if dp.unit else None


def discovery_payload(dp, device_id=None):
    device = dict(HA_DEVICE)
    if device_id:
        device["model_id"] = device_id
    payload = {
        "name": dp.name,
        "unique_id": dp.uid,
        "state_topic": state_topic(dp),
        "availability_topic": AVAILABILITY_TOPIC,
        "device": device,
    }
    if dp.device_class:
        payload["device_class"] = dp.device_class
    if dp.category:
        payload["entity_category"] = dp.category
    if dp.attributes:
        payload["json_attributes_topic"] = attributes_topic(dp)
    if dp.component == "binary_sensor":
        payload["payload_on"] = dp.payload_on
        payload["payload_off"] = dp.payload_off
    else:
        if dp.unit:
            payload["unit_of_measurement"] = dp.unit
        if dp.state_class:
            payload["state_class"] = dp.state_class
        precision = display_precision(dp)
        if precision is not None:
            payload["suggested_display_precision"] = precision
    return payload


class Bridge:
    def __init__(self, host, user, password, datapoints, device_id=None):
        self._datapoints = datapoints
        self._device_id = device_id
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="vito-optolink")
        self._client.username_pw_set(user, password)
        self._client.will_set(AVAILABILITY_TOPIC, "offline", qos=1, retain=True)
        self._client.on_connect = self._on_connect
        self._host = host

    def start(self):
        self._client.connect_async(self._host, 1883, keepalive=60)
        self._client.loop_start()

    def stop(self):
        self.set_available(False)
        self._client.loop_stop()
        self._client.disconnect()

    def set_available(self, available):
        self._client.publish(AVAILABILITY_TOPIC, "online" if available else "offline", qos=1, retain=True)

    def publish_state(self, dp, payload):
        self._client.publish(state_topic(dp), payload, qos=0, retain=True)

    def publish_attributes(self, dp, payload):
        self._client.publish(attributes_topic(dp), payload, qos=0, retain=True)

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("MQTT connect failed: %s", reason_code)
            return
        log.info("MQTT connected, publishing discovery for %d entities", len(self._datapoints))
        for component, uid in OBSOLETE:
            client.publish("homeassistant/%s/%s/config" % (component, uid), b"", qos=1, retain=True)
        for dp in self._datapoints:
            client.publish(discovery_topic(dp), json.dumps(discovery_payload(dp, self._device_id)), qos=1, retain=True)
