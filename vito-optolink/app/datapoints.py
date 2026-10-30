"""Datapoint definitions (YAML) and raw-bytes <-> value conversion."""

from dataclasses import dataclass, field

import yaml

from app.p300 import decode_int


@dataclass
class Datapoint:
    id: str
    uid: str
    name: str
    addr: int
    len: int
    type: str                      # "int" or "enum"
    group: str = "slow"            # "fast" or "slow"
    signed: bool = False
    scale: float = 1.0
    unit: str = ""
    enum: dict = field(default_factory=dict)
    component: str = "sensor"      # "sensor" or "binary_sensor"
    device_class: str = ""
    state_class: str = ""
    payload_on: str = ""
    payload_off: str = ""
    max_value: float = None        # larger decoded values are a placeholder, not a reading (published as "None")
    precision: int = None          # decimals Home Assistant shows; derived from scale when not set
    attributes: bool = False       # also publish a JSON attributes topic
    category: str = ""             # "diagnostic" for configuration values shown read-only
    verified: bool = True


def load(path):
    """Return (device_id, [Datapoint]) from a datapoint YAML file."""
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)

    enums = doc.get("enums", {})
    points = []
    for raw in doc["datapoints"]:
        raw = dict(raw)
        enum_name = raw.pop("enum", None)
        dp = Datapoint(enum=enums[enum_name] if enum_name else {}, **raw)
        if dp.type == "enum" and not dp.enum:
            raise ValueError("%s: enum type without enum map" % dp.id)
        points.append(dp)

    for attr in ("id", "uid"):
        values = [getattr(p, attr) for p in points]
        dupes = {v for v in values if values.count(v) > 1}
        if dupes:
            raise ValueError("Duplicate %s in datapoints: %s" % (attr, sorted(dupes)))
    return str(doc["device_id"]), points


def decode(dp, raw):
    """Raw controller bytes -> number (int/float) or enum text."""
    value = decode_int(raw, signed=dp.signed)
    if dp.type == "enum":
        return dp.enum.get(value, "?")
    value = round(value * dp.scale, 3) if dp.scale != 1.0 else value
    if dp.max_value is not None and value > dp.max_value:
        return None
    return value


def to_payload(value):
    """State topic payload: integers without decimals, floats trimmed."""
    if value is None:
        return "None"          # Home Assistant shows an MQTT sensor with payload "None" as unknown
    if isinstance(value, str):
        return value
    if isinstance(value, float):
        return ("%.3f" % value).rstrip("0").rstrip(".")
    return str(value)
