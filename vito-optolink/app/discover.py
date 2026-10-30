"""
Discovery mode: find out which controller addresses mean what. Read-only.

Reads address ranges repeatedly while the heating does its normal thing, notes the state of
the burner flame in every round and writes a report with
  - bytes/words that change at all,
  - bytes/words whose value separates "flame on" from "flame off" (modulation, flue gas, ...),
  - 2-byte words that decode to one of the reference temperatures (value / 10).
"""

import logging
import os
import time

from app import datapoints, p300

log = logging.getLogger(__name__)

CHUNK = 8                      # bytes per read request (vcontrold reads 8-byte timer blocks)
REPORT_DIR = "/share/vito-optolink"
TEMP_TOLERANCE = 0.3           # °C


def parse_ranges(text):
    """'0800-08FF,5500-55FF' -> [(0x0800, 0x08FF), (0x5500, 0x55FF)]"""
    ranges = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        start, _, end = part.partition("-")
        lo, hi = int(start, 16), int(end or start, 16)
        if hi < lo:
            raise ValueError("Bad range %r" % part)
        ranges.append((lo, hi))
    return ranges


def parse_refs(text):
    """'11.8 41,1' or '11.8; 41.1' -> [11.8, 41.1] (decimal point or comma)"""
    return [float(t.replace(",", ".")) for t in text.replace(";", " ").split()]


def scan_once(link, ranges):
    """Read all ranges. Returns ({addr: byte}, [addresses of rejected chunks])."""
    values, rejected = {}, []
    for lo, hi in ranges:
        addr = lo
        while addr <= hi:
            length = min(CHUNK, hi - addr + 1)
            try:
                data = link.read(addr, length)
                for i, b in enumerate(data):
                    values[addr + i] = b
            except p300.P300Rejected:
                rejected.append(addr)
            addr += length
    return values, rejected


def _chunk_len(ranges, addr):
    for lo, hi in ranges:
        if lo <= addr <= hi:
            return min(CHUNK, hi - addr + 1)
    return 1


def probe_single(link, ranges, rejected):
    """A rejected chunk may still contain readable bytes: probe it byte by byte."""
    values = {}
    for start in rejected:
        for addr in range(start, start + _chunk_len(ranges, start)):
            try:
                values[addr] = link.read(addr, 1)[0]
            except p300.P300Rejected:
                pass
    return values


def to_runs(addresses):
    """Sorted addresses -> [(start, length)] of contiguous runs, at most CHUNK bytes each."""
    runs, start, length = [], None, 0
    for a in sorted(addresses):
        if start is not None and a == start + length and length < CHUNK:
            length += 1
        else:
            if start is not None:
                runs.append((start, length))
            start, length = a, 1
    if start is not None:
        runs.append((start, length))
    return runs


def scan_runs(link, runs):
    values = {}
    for start, length in runs:
        try:
            for i, b in enumerate(link.read(start, length)):
                values[start + i] = b
        except p300.P300Rejected:
            log.warning("Address %04X was readable before but is rejected now", start)
    return values


def analyze(rounds, flames, refs=()):
    """
    rounds: list of {addr: byte}; flames: list of True/False/None (same length).
    Returns dict with 'changing', 'separating' and 'temp_matches' (lists of text lines).
    """
    common = set(rounds[0])
    for r in rounds[1:]:
        common &= set(r)

    def series_byte(addr):
        return [r[addr] for r in rounds]

    def series_word(addr):
        return [r[addr] | r[addr + 1] << 8 for r in rounds]

    def signed(v):
        return v - 0x10000 if v >= 0x8000 else v

    result = {"changing": [], "separating": [], "temp_matches": []}
    known = [i for i, f in enumerate(flames) if f is not None]
    on = [i for i in known if flames[i]]
    off = [i for i in known if not flames[i]]

    for addr in sorted(common):
        series = series_byte(addr)
        if len(set(series)) > 1:
            result["changing"].append("%04X  byte  %d..%d  (%d distinct)" % (addr, min(series), max(series), len(set(series))))
        if on and off:
            on_vals, off_vals = {series[i] for i in on}, {series[i] for i in off}
            if on_vals.isdisjoint(off_vals):
                result["separating"].append("%04X  byte  flame on: %s  off: %s" % (
                    addr, _fmt_set(on_vals), _fmt_set(off_vals)))

        if addr + 1 in common:
            words = series_word(addr)
            if on and off:
                on_w, off_w = {words[i] for i in on}, {words[i] for i in off}
                if on_w.isdisjoint(off_w):
                    result["separating"].append("%04X  word  flame on: %s  off: %s" % (
                        addr, _fmt_set(on_w, 10), _fmt_set(off_w, 10)))
            temp = signed(words[0]) / 10
            for ref in refs:
                if abs(temp - ref) <= TEMP_TOLERANCE:
                    result["temp_matches"].append("%04X  word  %.1f °C in round 1 (reference %.1f)" % (addr, temp, ref))
    return result


def _fmt_set(values, divisor=1):
    items = sorted(values)
    text = ", ".join(("%g" % (v / divisor)) for v in items[:6])
    return text + (", ..." if len(items) > 6 else "")


def write_report(lines, report_dir=None):
    report_dir = report_dir or REPORT_DIR
    try:
        os.makedirs(report_dir, exist_ok=True)
        path = os.path.join(report_dir, "discovery_%s.txt" % time.strftime("%Y%m%d_%H%M%S"))
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path
    except OSError as err:
        log.warning("Could not write report: %s", err)
        return None


def run(link, ranges, rounds_wanted, interval_s, flame_addr, refs=(), context_points=()):
    log.info("Discovery: %d rounds every %ds over %s, read-only",
             rounds_wanted, interval_s, ", ".join("%04X-%04X" % r for r in ranges))
    rounds, flames = [], []
    known_runs = None   # readable addresses, found in the first round
    context = {dp.name: [] for dp in context_points}  # what the controller was doing, per round
    for n in range(1, rounds_wanted + 1):
        started = time.monotonic()
        try:
            if known_runs is None:
                # first round: coarse scan, then probe rejected chunks byte by byte (a chunk is
                # refused as a whole if it touches unreadable memory)
                values, rejected = scan_once(link, ranges)
                values.update(probe_single(link, ranges, rejected))
                known_runs = to_runs(values)
                log.info("Address map: %d readable bytes in %d runs", len(values), len(known_runs))
            else:
                values = scan_runs(link, known_runs)
            flame = link.read(flame_addr, 1)[0] != 0
            for dp in context_points:
                context[dp.name].append(datapoints.to_payload(datapoints.decode(dp, link.read(dp.addr, dp.len))))
        except p300.P300Error as err:
            log.error("Round %d failed: %s", n, err)
            time.sleep(interval_s)
            continue
        rounds.append(values)
        flames.append(flame)
        log.info("Round %d/%d: %d bytes read, flame %s (%.1fs)",
                 n, rounds_wanted, len(values), "ON" if flame else "off", time.monotonic() - started)
        time.sleep(max(0.0, interval_s - (time.monotonic() - started)))

    if len(rounds) < 2:
        log.error("Not enough successful rounds for a report")
        return

    result = analyze(rounds, flames, refs)
    lines = ["Discovery report (read-only)",
             "rounds: %d, flame on in %d, off in %d" % (len(rounds), sum(flames), len(flames) - sum(flames)),
             "ranges: " + ", ".join("%04X-%04X" % r for r in ranges),
             "readable addresses: %d in %d runs: %s" % (
                 len(rounds[0]), len(known_runs), " ".join("%04X+%d" % r for r in known_runs)),
             ""]
    for name, seen in context.items():
        lines.append("%s: %s" % (name, ", ".join(sorted(set(seen))) or "-"))
    if context:
        lines.append("")
    if not (any(flames) and not all(flames)):
        lines += ["NOTE: the flame state did not change during the run, so no flame-related addresses "
                  "can be found. Run again while the burner starts or stops.", ""]
    for title, key in (("Addresses that separate flame ON from OFF", "separating"),
                       ("2-byte words matching a reference temperature (value/10, signed)", "temp_matches"),
                       ("Addresses that changed at all", "changing")):
        lines.append("== %s (%d)" % (title, len(result[key])))
        lines += result[key][:200] or ["(none)"]
        lines.append("")
    for line in lines:
        log.info(line)
    path = write_report(lines)
    log.info("Report written to %s" % path if path else "Report not written (see warning above)")
