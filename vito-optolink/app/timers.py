"""
Time programs (Schaltzeiten) of the controller: heating, hot water, circulation pump.

Each program is 7 days (Monday first) of 8 bytes: up to four on/off pairs. A byte is
hours * 8 + minutes / 10, 0xFF means the slot is unused (vcontrold src/unit.c, "cycletime").
The base addresses are the usual ones for this controller family (vcontrold vito.xml); they
are not confirmed for every device.
"""

import json
import logging
import time

from app import p300
from app.datapoints import Datapoint

log = logging.getLogger(__name__)

PROGRAMS = {"heizen": ("Heizen", 0x2000), "ww": ("Warmwasser", 0x2100), "zirk": ("Zirkulation", 0x2200)}
DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
UNUSED = 0xFF
READ_EVERY_S = 1800


def _sensor(id, name, **kw):
    return Datapoint(id=id, uid="vito_" + id, name=name, addr=0, len=0, type="derived", group="derived", **kw)


DERIVED = (
    [_sensor("zeiten_%s_heute" % key, "%s-Zeiten heute" % label, attributes=True)
     for key, (label, _) in PROGRAMS.items()]
    + [Datapoint(id="ww_programm_aktiv", uid="vito_ww_programm_aktiv", name="Warmwasser-Programm aktiv", addr=0, len=0,
                 type="derived", group="derived", component="binary_sensor", payload_on="an", payload_off="aus")]
)


def decode_day(raw):
    """8 bytes -> [(start_minute, end_minute)]"""
    windows = []
    for i in range(0, len(raw) - 1, 2):
        if raw[i] == UNUSED or raw[i + 1] == UNUSED:
            continue
        windows.append(((raw[i] >> 3) * 60 + (raw[i] & 7) * 10, (raw[i + 1] >> 3) * 60 + (raw[i + 1] & 7) * 10))
    return windows


def format_minute(m):
    return "%02d:%02d" % (m // 60, m % 60)


def format_windows(windows):
    return ", ".join("%s–%s" % (format_minute(a), format_minute(b)) for a, b in windows) or "aus"


def is_active(windows, minute):
    return any(a <= minute < b for a, b in windows)


def read_program(link, base):
    """[windows of Monday ... Sunday]"""
    return [decode_day(link.read(base + 8 * day, 8)) for day in range(7)]


class Timers:
    def __init__(self):
        self.week = {}          # key -> [windows per day]
        self._read_at = None
        self._published = {}

    def due(self, now):
        return self._read_at is None or now - self._read_at >= READ_EVERY_S

    def read(self, link, now):
        self._read_at = now
        for key, (label, base) in PROGRAMS.items():
            try:
                self.week[key] = read_program(link, base)
            except p300.P300Error as err:
                log.warning("%s time program (%04X) not readable: %s", label, base, err)
                self.week.pop(key, None)

    def values(self, local):
        """{derived id: payload} for 'today' and 'active now'; local is a time.struct_time."""
        out, minute = {}, local.tm_hour * 60 + local.tm_min
        for key, days in self.week.items():
            out["zeiten_%s_heute" % key] = format_windows(days[local.tm_wday])
        if "ww" in self.week:
            out["ww_programm_aktiv"] = "an" if is_active(self.week["ww"][local.tm_wday], minute) else "aus"
        return out

    def attributes(self, key):
        days = self.week[key]
        return {DAYS[d]: format_windows(days[d]) for d in range(7)}

    def publish(self, bridge, now=None):
        now = time.time() if now is None else now
        local = time.localtime(now)
        by_id = {dp.id: dp for dp in DERIVED}
        for dp_id, payload in self.values(local).items():
            if self._published.get(dp_id) != payload:
                bridge.publish_state(by_id[dp_id], payload)
                self._published[dp_id] = payload
        for key in self.week:
            attrs = json.dumps(self.attributes(key), ensure_ascii=False)
            if self._published.get("attr_" + key) != attrs:
                bridge.publish_attributes(by_id["zeiten_%s_heute" % key], attrs)
                self._published["attr_" + key] = attrs
