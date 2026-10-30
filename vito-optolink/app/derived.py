"""
Burner statistics (short-cycling analysis) derived from the polled values.

Inputs per fast pass: flame on/off, burner start counter, diverter valve text.
The start counter is exact; run and pause times are only as exact as the poll interval.
"""

import json
import logging
import os
import time

from app.datapoints import Datapoint

log = logging.getLogger(__name__)

DAY_S = 86400
HOUR_S = 3600
MAX_GAP_S = 120          # longer gaps between two passes are not counted as burner on-time
MAX_STARTS_PER_PASS = 10  # sanity limit for counter jumps
VALVES = {"Heizung": "heizen", "Warmwasser": "ww"}


def _sensor(id, name, unit="", device_class="", state_class="measurement", precision=None):
    return Datapoint(id=id, uid="vito_" + id, name=name, addr=0, len=0, type="derived", group="derived",
                     unit=unit, device_class=device_class, state_class=state_class, precision=precision)


DERIVED = [
    _sensor("starts_heute", "Brennerstarts heute", state_class="total_increasing"),
    _sensor("starts_stunde", "Brennerstarts letzte Stunde"),
    _sensor("starts_24h", "Brennerstarts 24 h"),
    _sensor("starts_24h_heizen", "Brennerstarts 24 h Heizen"),
    _sensor("starts_24h_ww", "Brennerstarts 24 h Warmwasser"),
    _sensor("laufzeit_letzte", "Letzte Brennerlaufzeit", "min", "duration", precision=1),
    _sensor("pause_letzte", "Letzte Brennerpause", "min", "duration", precision=1),
    _sensor("laufzeit_mittel_24h", "Mittlere Laufzeit pro Start (24 h)", "min", "duration", precision=1),
    _sensor("laufzeit_mittel_24h_heizen", "Mittlere Laufzeit Heizen (24 h)", "min", "duration", precision=1),
    _sensor("laufzeit_mittel_24h_ww", "Mittlere Laufzeit Warmwasser (24 h)", "min", "duration", precision=1),
    _sensor("laufzeit_heute", "Brennerlaufzeit heute", "h", "duration", "total_increasing", precision=2),
    _sensor("anteil_heute", "Brenneranteil heute", "%", precision=1),
    Datapoint(id="takt_warnung", uid="vito_takt_warnung", name="Takt-Warnung", addr=0, len=0, type="derived",
              group="derived", component="binary_sensor", device_class="problem",
              payload_on="an", payload_off="aus"),
]


# the controller's device ID (read from 0x00F8 at start), shown as a diagnostic entity
DEVICE_ID = Datapoint(id="geraetekennung", uid="vito_geraetekennung", name="Gerätekennung", addr=0, len=0,
                      type="derived", group="derived", category="diagnostic")


def _day(now):
    return time.strftime("%Y-%m-%d", time.localtime(now))


def _seconds_since_midnight(now):
    t = time.localtime(now)
    return t.tm_hour * HOUR_S + t.tm_min * 60 + t.tm_sec


def _min(seconds):
    return round(seconds / 60, 1)


class BurnerStats:
    def __init__(self, warn_starts_per_hour=6, state=None):
        self.warn_starts_per_hour = warn_starts_per_hour
        self.events = []        # [timestamp, valve]  one per burner start
        self.runs = []          # [end timestamp, duration s, valve]
        self.last_run_s = None
        self.last_pause_s = None
        self.day = None
        self.on_s_today = 0.0
        self.seen_s_today = 0.0   # time actually observed today (the add-on may start mid-day)
        self._flame = None
        self._since = None      # when the current flame state began (None: unknown)
        self._run_valve = None
        self._starts = None
        self._prev = None
        if state:
            self.events = state.get("events", [])
            self.runs = state.get("runs", [])
            self.last_run_s = state.get("last_run_s")
            self.last_pause_s = state.get("last_pause_s")
            self.day = state.get("day")
            self.on_s_today = state.get("on_s_today", 0.0)
            self.seen_s_today = state.get("seen_s_today", 0.0)
            if "seen_s_today" not in state:
                self.on_s_today = 0.0   # saved by an older version: on-time without observed time would give shares above 100 %

    @property
    def flame(self):
        return self._flame

    def state(self):
        return {"events": self.events, "runs": self.runs, "last_run_s": self.last_run_s,
                "last_pause_s": self.last_pause_s, "day": self.day, "on_s_today": self.on_s_today,
                "seen_s_today": self.seen_s_today}

    def update(self, now, flame, starts, valve=None):
        """flame: True/False, starts: burner start counter, valve: 'Heizung'/'Warmwasser'/other."""
        kind = VALVES.get(valve, "sonst")
        if _day(now) != self.day:
            self.day, self.on_s_today, self.seen_s_today = _day(now), 0.0, 0.0

        # exact start count from the controller counter (also catches runs shorter than a poll interval)
        if self._starts is not None and starts > self._starts:
            for _ in range(min(starts - self._starts, MAX_STARTS_PER_PASS)):
                self.events.append([now, kind])
        self._starts = starts

        if self._prev is not None and now - self._prev <= MAX_GAP_S:
            self.seen_s_today += now - self._prev
            if self._flame:
                self.on_s_today += now - self._prev
        self._prev = now

        if self._flame is not None and flame != self._flame:
            if flame:
                if self._since is not None:
                    self.last_pause_s = now - self._since
                self._run_valve = kind
            elif self._since is not None and self._run_valve is not None:
                self.last_run_s = now - self._since
                self.runs.append([now, self.last_run_s, self._run_valve])
            self._since = now
        elif self._flame is None:
            # first pass: start of the current state is unknown, so no duration yet
            self._since = None
            self._run_valve = kind if flame else None
        self._flame = flame

        cutoff = now - DAY_S
        self.events = [e for e in self.events if e[0] >= cutoff]
        self.runs = [r for r in self.runs if r[0] >= cutoff]

    def values(self, now):
        """{derived id: payload}. Values that cannot be known yet are left out."""
        out = {}
        hour_ago = now - HOUR_S
        midnight = now - _seconds_since_midnight(now)
        starts_hour = sum(1 for e in self.events if e[0] >= hour_ago)
        out["starts_heute"] = sum(1 for e in self.events if e[0] >= midnight)
        out["starts_stunde"] = starts_hour
        out["starts_24h"] = len(self.events)
        for kind in ("heizen", "ww"):
            out["starts_24h_" + kind] = sum(1 for e in self.events if e[1] == kind)
        if self.last_run_s is not None:
            out["laufzeit_letzte"] = _min(self.last_run_s)
        if self.last_pause_s is not None:
            out["pause_letzte"] = _min(self.last_pause_s)
        if self.runs:
            out["laufzeit_mittel_24h"] = _min(sum(r[1] for r in self.runs) / len(self.runs))
        for kind in ("heizen", "ww"):
            durations = [r[1] for r in self.runs if r[2] == kind]
            if durations:
                out["laufzeit_mittel_24h_" + kind] = _min(sum(durations) / len(durations))
        on_s = self.on_s_today
        out["laufzeit_heute"] = round(on_s / HOUR_S, 2)
        if self.seen_s_today > 0:
            out["anteil_heute"] = round(min(100.0, 100 * on_s / self.seen_s_today), 1)
        out["takt_warnung"] = "an" if starts_hour >= self.warn_starts_per_hour else "aus"
        return out


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_state(path, stats):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(stats.state(), f)
        os.replace(tmp, path)
    except OSError as err:
        log.warning("Could not save statistics: %s", err)
