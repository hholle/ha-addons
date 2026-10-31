"""Commands the host can send: a frame plus how to wait for the answer."""

import threading

from . import codec
from .codec import (BROADCAST, K_BECKON, K_MANUAL, K_MB8_READ, K_MB8_WRITE, K_NETWORK_ACK, K_PING,
                    K_SCAN_REQUEST, K_SCAN_RESPONSE, KEY0, P2P, PLAIN, WIDE_BC, mb8_payload, report)


class Command:
    """A frame to send. ``expect`` is the answer type to wait for (None = fire and forget, wait
    ``timeout`` s). ``snr_hex`` restricts the answer to one device."""

    def __init__(self, kind, frame, expect=None, snr_hex=None, timeout=2.0,
                 delay_after=0.0, retry=0, snr=None, on_done=None):
        self.kind = kind
        self.frame = frame
        self.expect = expect
        self.snr_hex = snr_hex
        self.timeout = timeout
        self.delay_after = delay_after
        self.retry = retry
        self.snr = snr
        self.on_done = on_done   # called with the answer (type, snr_hex, params) or None on timeout/failure
        self.event = threading.Event()
        self.reply = None
        self.attempts = 0        # frames sent so far (1 + retries)
        self.t_sent = None
        self.latency = None      # ms from the last send to the answer
        self.failure = None      # stick failure code if the stick refused the command


def get_pos_cmd(snr):
    """Movement state poll: MB8 read of block 1, address 0, 5 bytes."""
    h = codec.snr_to_hex(snr)
    return Command("blindGetPos", report(h, K_MB8_READ, mb8_payload(1, 0, 5)), "position", h,
                   timeout=0.5, delay_after=0.1, retry=2, snr=snr)


def move_cmd(snr, pos, ang):
    h = codec.snr_to_hex(snr)
    return Command("blindMoveToPos",
                   report(h, K_MANUAL, f"03{codec.pos_to_hex(pos)}{codec.angle_to_hex(ang)}FFFF"),
                   "blindMoveToPosResponse", h, timeout=0.5, delay_after=0.3, retry=1, snr=snr)


def stop_cmd(snr):
    h = codec.snr_to_hex(snr)
    return Command("blindStopMove", report(h, K_MANUAL, "01FFFFFFFF00"),
                   "blindMoveToPosResponse", h, timeout=0.2, delay_after=0.005, retry=3, snr=snr)


def ping_cmd(snr):
    """Radio ping: the device answers with a network ack (silent, the device does not move)."""
    h = codec.snr_to_hex(snr)
    return Command("ping", report(h, K_PING), "networkAck", h, timeout=0.5, delay_after=0.05, retry=2, snr=snr)


def beckon_cmd(snr):
    """Wave: the device moves briefly or blinks and answers with a network ack."""
    h = codec.snr_to_hex(snr)
    return Command("beckon", report(h, K_BECKON), "networkAck", h, timeout=0.5, delay_after=0.3, retry=1, snr=snr)


def quality_cmd(snr):
    """Stick command: link quality (LQI and RSSI, both directions) of one device."""
    h = codec.snr_to_hex(snr)
    return Command("connectionQuality", f"{{Q{h}}}", "connectionQuality", h,
                   timeout=0.5, delay_after=0.05, retry=1, snr=snr)


def mb8_read_cmd(snr, block, address, size):
    """Read ``size`` bytes of receiver memory; the answer type is ``mb8Read``."""
    h = codec.snr_to_hex(snr)
    return Command("mb8Read", report(h, K_MB8_READ, mb8_payload(block, address, size)), "mb8Read", h,
                   timeout=0.5, delay_after=0.1, retry=2, snr=snr)


def mb8_write_cmd(snr, block, address, data_hex):
    """Write receiver memory. Not used by the add-on; changes device behaviour, handle with care."""
    h = codec.snr_to_hex(snr)
    size = len(data_hex) // 2
    return Command("mb8Write", report(h, K_MB8_WRITE, mb8_payload(block, address, size, data_hex)), "mb8Write", h,
                   timeout=0.5, delay_after=0.1, retry=1, snr=snr)


def scan_request_cmd(pan_id):
    return Command("scanRequest", report(BROADCAST, K_SCAN_REQUEST, f"{pan_id}02", KEY0, WIDE_BC), timeout=0.75)


def scan_response_cmd(snr_hex, pan_id):
    return Command("scanResponse", report(snr_hex, K_SCAN_RESPONSE, f"{pan_id}02", KEY0, P2P), timeout=0.1)


def ack_cmd():
    return Command("ack", "{a}", timeout=0.05)


def network_ack_cmd(snr):
    """Acknowledge a message of another device (plain, point to point)."""
    return Command("networkAck", report(codec.snr_to_hex(snr), K_NETWORK_ACK, "", PLAIN, P2P), timeout=0.1, snr=snr)
