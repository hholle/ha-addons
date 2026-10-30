"""
Coding dump: read every known coding value once (read-only) and write a table.
The values are raw, the same numbers as in the coding menu of the controller.
"""

import logging
import os
import time

import yaml

from app import p300
from app.datapoints import Datapoint
from app.discover import REPORT_DIR

log = logging.getLogger(__name__)


def load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["codings"]


ADDR_SCHEME = 0x7700     # code 00 "Heizkreis-Warmwasserschema"
# 1 A1, 2 A1 + WW, 3 M2, 4 M2 + WW, 5 A1 + M2 (ViessData21 list)
NO_A1 = (3, 4)
NO_M2 = (1, 2)


def to_datapoints(codings, scheme=None):
    """Read-only diagnostic datapoints for the codings of the heating circuits that exist."""
    skip = set()
    if scheme in NO_A1:
        skip.add("Heizkreis A1M1")
    if scheme in NO_M2:
        skip.add("Heizkreis M2")
    points = []
    for e in codings:
        if e["group"] in skip:
            continue
        points.append(Datapoint(
            id="kodierung_%04x" % e["addr"], uid="vito_kodierung_%04x" % e["addr"],
            name="Codierung %s %s" % (e["code"], e["name"].strip()), addr=e["addr"], len=e["len"],
            type="int", signed=e["signed"], category="diagnostic", group="config", verified=False))
    return points


def read_all(link, codings):
    """[(entry, value or None)]; None when the controller refuses the address."""
    rows = []
    for entry in codings:
        try:
            raw = link.read(entry["addr"], entry["len"])
            rows.append((entry, p300.decode_int(raw, signed=entry["signed"])))
        except p300.P300Rejected:
            rows.append((entry, None))
        except p300.P300Error as err:
            log.error("%04X: %s", entry["addr"], err)
            rows.append((entry, None))
    return rows


def format_rows(rows):
    lines, group = [], None
    for entry, value in rows:
        if entry["group"] != group:
            group = entry["group"]
            lines += ["", "== %s" % group]
        lines.append("%s  %04X  %-5s %s" % (entry["code"], entry["addr"],
                                            "-" if value is None else value, entry["name"]))
    return lines


# device identification, from the ViessData21 list for VScotHO1 (lengths unknown, so 8 bytes are tried)
INFO = [
    (0x00F8, "Gerätekennung (00F8/00F9) und Software-Index (00FB)"),
    (0x1010, "Sachnummer Codierstecker"),
    (0x7330, "Bedienteil Software-Index"),
    (0x7656, "Codierkarte (Gerätetyp, Revision)"),
    (0x0A44, "Mischererweiterung Software-Index"),
]


def read_info(link, length=8):
    lines = ["", "== Geräteinformation (rohe Bytes, hex)"]
    for addr, name in INFO:
        for n in (length, 4, 2, 1):   # a refused length may still work shorter
            try:
                raw = link.read(addr, n)
                text = "".join(chr(b) if 32 <= b < 127 else "." for b in raw)
                lines.append("%04X  %-23s %s  %s" % (addr, raw.hex(" ").upper(), text, name))
                break
            except p300.P300Rejected:
                continue
            except p300.P300Error as err:
                lines.append("%04X  error: %s  %s" % (addr, err, name))
                break
        else:
            lines.append("%04X  -  %s" % (addr, name))
    return lines


def run(link, codings, report_dir=None):
    rows = read_all(link, codings)
    readable = sum(1 for _, v in rows if v is not None)
    lines = ["Coding dump (read-only): %d of %d values readable" % (readable, len(rows)),
             "code  addr  value name (raw values as in the coding menu; '-' = not readable)"]
    lines += format_rows(rows)
    lines += read_info(link)
    for line in lines:
        log.info(line)
    report_dir = report_dir or REPORT_DIR
    try:
        os.makedirs(report_dir, exist_ok=True)
        path = os.path.join(report_dir, "codings_%s.txt" % time.strftime("%Y%m%d_%H%M%S"))
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        log.info("Report written to %s", path)
    except OSError as err:
        log.warning("Could not write report: %s", err)
