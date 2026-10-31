"""WAREMA WMS wire format: frames, encodings and the decoder. Pure functions, no I/O.

A frame is ASCII between ``{`` and ``}``. Host → stick frames start with an upper-case command letter,
stick → host frames with the lower-case one. Radio reports are

    {R<key mode><transmission mode><snr 3 bytes LE><type+id 2 bytes><payload>}   host → device
    {r<snr 3 bytes LE><type+id 2 bytes><payload>}                                device → host

See PROTOCOL.md for the full description and the confidence of every item.
"""

import math
import re

BAUD = 125000
ANGLE_SCALE = 75   # WMS angle byte = percent / 100 * 75 + 127
GAP = 0.005        # pause between two commands (s)

# key modes (first digit after R)
KEY0, KEY1, PLAIN, STACK_DEFAULT = 0, 1, 2, 3
# transmission modes (second digit after R)
ONE_HOP_BC, P2P, WIDE_BC, WIDE_P2P, NWK2CHILD = 0, 1, 4, 6, 8

# report kinds (type + id)
K_REMOVE_FROM_PAN = "5010"
K_ADD_TO_PAN = "5018"
K_PING = "5040"
K_SET_CHANNEL = "5060"
K_NETWORK_ACK = "50AC"
K_SCAN_REQUEST = "7020"
K_SCAN_RESPONSE = "7021"
K_BECKON = "7050"
K_MANUAL = "7070"
K_MANUAL_RESPONSE = "7071"
K_SENSOR_VALUES = "7080"
K_MB8_READ = "8010"
K_MB8_READ_RESPONSE = "8011"
K_MB8_WRITE = "8020"
K_MB8_WRITE_RESPONSE = "8021"
K_MB8_FAILURE = "80FE"

BROADCAST = "FFFFFF"


def _round(x):
    """Round half up, like JavaScript's Math.round (Python rounds half to even)."""
    return math.floor(x + 0.5)


# ─── Value encodings ──────────────────────────────────────────────────────────

def snr_to_hex(snr):
    """Serial number → 6 hex digits with reversed byte order (0A2469 → 69240A)."""
    h = f"{snr:06X}"
    return h[4:6] + h[2:4] + h[0:2]


def snr_from_hex(h):
    h = h.zfill(6)
    return int(h[4:6] + h[2:4] + h[0:2], 16)


def pos_to_hex(percent):
    return f"{min(max(int(percent), 0), 100) * 2:02X}"


def pos_from_hex(h):
    return _round(int(h, 16) / 2)


def angle_to_hex(percent):
    return f"{min(max(_round(percent / 100 * ANGLE_SCALE), -ANGLE_SCALE), ANGLE_SCALE) + 127:02X}"


def angle_from_hex(h):
    return _round((int(h, 16) - 127) / ANGLE_SCALE * 100)


def u16_le(value):
    return f"{value & 0xFF:02X}{(value >> 8) & 0xFF:02X}"


# ─── Frame builders ───────────────────────────────────────────────────────────

def report(snr_hex, kind, payload="", key=KEY0, tx=WIDE_P2P):
    """A radio report frame; ``snr_hex`` is already in wire order (see snr_to_hex)."""
    return f"{{R{key}{tx}{snr_hex}{kind}{payload}}}"


def mb8_payload(block, address, size, data=""):
    """Payload of an MB8 read (data empty) or write request: block, address LE, size, data."""
    return f"{block:02X}{u16_le(address)}{size:02X}{data}"


# ─── Decoding ─────────────────────────────────────────────────────────────────

_QUALITY = re.compile(r"^\{q([0-9A-Fa-f]{6})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})\}$")
_PERFORMANCE = re.compile(r"^\{yC(\d{2}):([0-9A-Fa-f]{2})\}$")


def decode(frame):
    """Decode a received frame → (message type, snr hex, params)."""
    snr, params = "000000", {}
    if frame.startswith("{a}"):
        return "ack", snr, params
    if frame.startswith("{f}"):
        return "fwd", snr, params
    if frame.startswith("{f") and len(frame) >= 4:
        return "stickFailure", snr, {"code": frame[2]}   # c = wrong checksum, s = unauthorized
    if frame.startswith("{g"):
        return "stickName", snr, {}
    if frame.startswith("{v"):
        return "stickVersion", snr, {}
    m = _QUALITY.match(frame)
    if m:
        tx_lqi, tx_rssi, rx_lqi, rx_rssi = (int(g, 16) for g in m.groups()[1:])
        return "connectionQuality", m.group(1).upper(), {
            "tx_lqi": tx_lqi, "tx_rssi": tx_rssi, "rx_lqi": rx_lqi, "rx_rssi": rx_rssi}
    m = _PERFORMANCE.match(frame)
    if m:
        return "performance", snr, {"channel": int(m.group(1)), "level": int(m.group(2), 16)}
    if not frame.startswith("{r"):
        return "unknown", snr, params

    snr, kind, payload = frame[2:8], frame[8:12], frame[12:].rstrip("}")
    try:
        if kind == K_MB8_READ_RESPONSE:
            if payload[:8] in ("01000003", "01000005"):   # block 1, address 0: movement state
                return "position", snr, {
                    "position": pos_from_hex(payload[8:10]),
                    "angle": None if payload[10:12].upper() == "FF" else angle_from_hex(payload[10:12]),   # FF = no angle
                    "moving": payload[16:18] != "00",
                }
            size = int(payload[6:8], 16)
            return "mb8Read", snr, {
                "block": int(payload[0:2], 16),
                "address": int(payload[4:6] + payload[2:4], 16),
                "size": size,
                "data": payload[8:8 + 2 * size],
            }
        elif kind == K_MB8_WRITE_RESPONSE:
            return "mb8Write", snr, {}
        elif kind == K_MB8_FAILURE:
            return "mb8Failure", snr, {"code": int(payload[0:2], 16)}
        elif kind == K_NETWORK_ACK:
            return "networkAck", snr, {"data": payload}   # 2 bytes seen, meaning unknown
        elif kind == K_MANUAL_RESPONSE:
            return "blindMoveToPosResponse", snr, params
        elif kind == K_SENSOR_VALUES:
            lumen = int(payload[12:14], 16) * 2
            if payload[4:6] != "00":
                lumen *= int(payload[4:6], 16)
            return "weatherBroadcast", snr, {
                "wind": int(payload[2:4], 16),
                "lumen": lumen,
                "rain": payload[16:18] == "C8",
                "temp": int(payload[18:20], 16) / 2 - 35,
            }
        elif kind == K_ADD_TO_PAN:
            return "joinNetworkRequest", snr, {
                "pan_id": payload[0:4],
                "key": "".join(reversed([payload[4:36][i:i + 2] for i in range(0, 32, 2)])),
                "channel": int(payload[38:40], 16),
            }
        elif kind == K_SET_CHANNEL:
            return "switchChannelRequest", snr, {"pan_id": payload[0:4], "channel": int(payload[6:8], 16)}
        elif kind == K_SCAN_REQUEST:
            return "scanRequest", snr, {"pan_id": payload[0:4]}
        elif kind == K_SCAN_RESPONSE:
            return "scanResponse", snr, {"device_type": payload[4:6]}
        elif kind == K_BECKON:
            return "waveRequest", snr, params
    except ValueError:
        pass
    return "unknown", snr, params
