"""Warema WMS USB stick: wire protocol and serial driver.

Frames are ASCII, delimited by '{' ... '}', 125000 baud. A single worker thread
sends one command at a time and waits for the expected reply (with timeout,
retry and a pause after each answer, so the radio channel is not flooded).
"""

import logging
import math
import threading
import time
from collections import deque

import serial

log = logging.getLogger(__name__)

BAUD = 125000
ANGLE_SCALE = 75   # WMS angle byte = percent/100 * 75 + 127
GAP = 0.005        # pause between two commands (s)


def _round(x):
    """Round half up, like JavaScript's Math.round (Python rounds half to even)."""
    return math.floor(x + 0.5)


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


# ─── Commands ─────────────────────────────────────────────────────────────────

class Command:
    """A frame to send. `expect` is the reply type to wait for (None = fire and
    forget, wait `timeout` s). `snr_hex` restricts the reply to one device."""

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
        self.on_done = on_done   # called with the reply (type, snr_hex, params) or None on timeout
        self.event = threading.Event()
        self.reply = None


def get_pos_cmd(snr):
    h = snr_to_hex(snr)
    return Command("blindGetPos", f"{{R06{h}801001000005}}", "position", h,
                   timeout=0.5, delay_after=0.1, retry=2, snr=snr)


def move_cmd(snr, pos, ang):
    h = snr_to_hex(snr)
    return Command("blindMoveToPos", f"{{R06{h}707003{pos_to_hex(pos)}{angle_to_hex(ang)}FFFF}}",
                   "blindMoveToPosResponse", h, timeout=0.5, delay_after=0.3, retry=1, snr=snr)


def stop_cmd(snr):
    h = snr_to_hex(snr)
    return Command("blindStopMove", f"{{R06{h}707001FFFFFFFF00}}",
                   "blindMoveToPosResponse", h, timeout=0.2, delay_after=0.005, retry=3, snr=snr)


def scan_request_cmd(pan_id):
    return Command("scanRequest", f"{{R04FFFFFF7020{pan_id}02}}", timeout=0.75)


def scan_response_cmd(snr_hex, pan_id):
    return Command("scanResponse", f"{{R01{snr_hex}7021{pan_id}02}}", timeout=0.1)


def ack_cmd():
    return Command("ack", "{a}", timeout=0.05)


# ─── Decoding ─────────────────────────────────────────────────────────────────

def decode(frame):
    """Decode a received frame → (msg_type, snr_hex, params)."""
    snr, params = "000000", {}
    if frame.startswith("{a}"):
        return "ack", snr, params
    if frame.startswith("{f}"):
        return "fwd", snr, params
    if frame.startswith("{g"):
        return "stickName", snr, {}
    if frame.startswith("{v"):
        return "stickVersion", snr, {}
    if not frame.startswith("{r"):
        return "unknown", snr, params

    snr, kind, payload = frame[2:8], frame[8:12], frame[12:]
    try:
        if kind == "8011":
            if payload[:8] in ("01000003", "01000005"):
                return "position", snr, {
                    "position": pos_from_hex(payload[8:10]),
                    "angle": angle_from_hex(payload[10:12]),
                    "moving": payload[16:18] != "00",
                }
        elif kind == "7071":
            return "blindMoveToPosResponse", snr, params
        elif kind == "7080":
            lumen = int(payload[12:14], 16) * 2
            if payload[4:6] != "00":
                lumen *= int(payload[4:6], 16)
            return "weatherBroadcast", snr, {
                "wind": int(payload[2:4], 16),
                "lumen": lumen,
                "rain": payload[16:18] == "C8",
                "temp": int(payload[18:20], 16) / 2 - 35,
            }
        elif kind == "5018":
            return "joinNetworkRequest", snr, {
                "pan_id": payload[0:4],
                "key": "".join(reversed([payload[4:36][i:i + 2] for i in range(0, 32, 2)])),
                "channel": int(payload[38:40], 16),
            }
        elif kind == "5060":
            return "switchChannelRequest", snr, {"pan_id": payload[0:4], "channel": int(payload[6:8], 16)}
        elif kind == "7020":
            return "scanRequest", snr, {"pan_id": payload[0:4]}
        elif kind == "7021":
            return "scanResponse", snr, {"device_type": payload[4:6]}
        elif kind == "7050":
            return "waveRequest", snr, params
    except ValueError:
        log.debug("Cannot decode frame %s", frame)
    return "unknown", snr, params


# ─── Driver ───────────────────────────────────────────────────────────────────

class Blind:
    def __init__(self, snr, no_poll=False):
        self.snr = snr
        self.hex = snr_to_hex(snr)
        self.no_poll = no_poll   # device does not answer blindGetPos (type 2A)
        self.paused = False      # temporarily excluded from polling
        self.pos, self.ang, self.moving = -1, 0, False
        self.last_poll = float("-inf")


class Stick:
    """Callbacks (all optional, called from the driver threads):
      on_position(snr, pos, ang, moving, changed)  answer to a position poll
      on_seen(snr)                                  any answer from a device
      on_weather(snr, params)                       weather station broadcast
      on_join(params)                               network parameters (PAN ID FFFF)
    """

    def __init__(self, path, channel, pan_id, key, poll_interval=30.0, moving_interval=1.0,
                 on_position=None, on_seen=None, on_weather=None, on_join=None):
        self.path, self.channel, self.pan_id, self.key = path, channel, pan_id, key
        self.poll_interval, self.moving_interval = poll_interval, moving_interval
        self.on_position, self.on_seen = on_position, on_seen
        self.on_weather, self.on_join = on_weather, on_join
        self.blinds = {}
        self._queue = deque()
        self._cond = threading.Condition()
        self._current = None
        self._port = None
        self._write_lock = threading.Lock()
        self._connected = threading.Event()
        self._ready = threading.Event()
        self._scanned = {}

    # -- lifecycle ------------------------------------------------------------

    def start(self):
        for target in (self._read_loop, self._work_loop, self._poll_loop):
            threading.Thread(target=target, daemon=True).start()

    def wait_ready(self, timeout=None):
        return self._ready.wait(timeout)

    def _read_loop(self):
        """Own the serial port: (re)open, re-init the stick, split frames at '}'."""
        while True:
            try:
                with serial.Serial(self.path, BAUD, timeout=1) as port:
                    log.info("Serial port opened: %s", self.path)
                    self._port = port
                    self._start_init()
                    self._connected.set()
                    buf = b""
                    while True:
                        buf += port.read_until(b"}")
                        if buf.endswith(b"}"):
                            self._handle(buf.decode("latin-1"))
                            buf = b""
            except (serial.SerialException, OSError) as e:
                log.error("Serial error: %s — retrying in 5s", e)
            self._connected.clear()
            self._ready.clear()
            self._port = None
            time.sleep(5)

    def _start_init(self):
        """Name, version, network key, channel/PAN → stick is on the radio network."""
        def ready(reply):   # also on timeout: the stick may already be configured
            log.info("Stick ready (channel %s, PAN %s)", self.channel, self.pan_id)
            self._ready.set()

        with self._cond:
            self._queue.clear()
        for cmd in (
            Command("stickGetName", "{G}", "stickName"),
            Command("stickGetVersion", "{V}", "stickVersion"),
            Command("stickSetKey", f"{{K401{self.key}}}", "ack"),
            Command("stickSwitchChannel", f"{{M%{self.channel}{self.pan_id}}}", "ack", on_done=ready),
        ):
            self._enqueue(cmd)

    # -- command queue --------------------------------------------------------

    def _enqueue(self, cmd):
        with self._cond:
            self._queue.append(cmd)
            self._cond.notify()

    def _remove(self, snr_hex, kind=None):
        """Drop queued commands for a device (optionally only one kind)."""
        with self._cond:
            keep = [c for c in self._queue
                    if not (c.snr_hex == snr_hex and (kind is None or c.kind == kind))]
            self._queue = deque(keep)

    def _queued(self, kind, snr_hex):
        with self._cond:
            return any(c.kind == kind and c.snr_hex == snr_hex for c in self._queue)

    def _work_loop(self):
        while True:
            with self._cond:
                while not self._queue or not self._connected.is_set():
                    self._cond.wait(1)
                cmd = self._queue.popleft()
            cmd.event.clear()
            cmd.reply = None
            self._current = cmd
            try:
                log.debug("SND %s", cmd.frame)
                with self._write_lock:
                    self._port.write(cmd.frame.encode("latin-1"))
                    self._port.flush()
            except (serial.SerialException, OSError, AttributeError) as e:
                log.error("Serial write failed: %s", e)
                self._current = None
                continue
            got = cmd.event.wait(cmd.timeout)
            self._current = None
            if got:
                self._finish(cmd, cmd.reply)
                time.sleep(GAP + cmd.delay_after)
            elif cmd.expect is None:
                self._finish(cmd, None)
            elif cmd.retry > 0:
                cmd.retry -= 1
                log.info("Retry %s %s", cmd.kind, cmd.snr_hex)
                self._enqueue(cmd)
            else:
                log.info("Timeout %s %s", cmd.kind, cmd.snr_hex or "")
                self._finish(cmd, None)
                time.sleep(GAP)

    def _finish(self, cmd, reply):
        if cmd.on_done:
            try:
                cmd.on_done(reply)
            except Exception:
                log.exception("Error in handler for %s", cmd.kind)

    # -- receive --------------------------------------------------------------

    def _handle(self, raw):
        frame = raw[raw.rfind("{"):] if "{" in raw else raw
        kind, snr, params = decode(frame)
        log.debug("RCV %s → %s %s", frame, kind, snr)

        cur = self._current
        if cur and cur.expect == kind and cur.snr_hex in (None, snr):
            cur.reply = (kind, snr, params)
            cur.event.set()
        elif kind == "weatherBroadcast":
            if self.on_weather:
                self.on_weather(snr_from_hex(snr), params)
        elif kind == "scanResponse":
            self._scanned[snr] = params["device_type"]
        elif kind == "scanRequest":   # another controller scans: answer so the stick shows up
            log.info("Stick scanned by SNR %s", snr_from_hex(snr))
            self._enqueue(scan_response_cmd(snr, self.pan_id))
        elif kind == "switchChannelRequest":
            self._enqueue(Command("stickSwitchChannel", f"{{M%{params['channel']}{params['pan_id']}}}", "ack"))
        elif kind == "joinNetworkRequest":
            if self.on_join:
                self.on_join(params)
            self._enqueue(ack_cmd())
        elif kind == "waveRequest":
            log.info("*** Waving and Hello! (requested from SNR %s)", snr_from_hex(snr))
            self._enqueue(ack_cmd())
        elif kind not in ("ack", "fwd"):
            log.debug("Unexpected message %s from %s", kind, snr)

    # -- API ------------------------------------------------------------------

    def scan(self):
        """Radio scan (3 requests, some devices ignore the first) → [(snr, type_hex)]."""
        self._scanned = {}
        done = threading.Event()
        for i in range(3):
            cmd = scan_request_cmd(self.pan_id)
            if i == 2:
                cmd.on_done = lambda _: done.set()
            self._enqueue(cmd)
        done.wait(30)
        return sorted((snr_from_hex(h), t) for h, t in self._scanned.items())

    def add_blind(self, snr, no_poll=False):
        self.blinds[snr] = Blind(snr, no_poll)

    def remove_blind(self, snr):
        blind = self.blinds.pop(snr, None)
        if blind:
            self._remove(blind.hex)

    def set_position(self, snr, pos, ang):
        blind = self.blinds.get(snr)
        if not blind:
            log.warning("set_position: unknown blind %s", snr)
            return
        self._remove(blind.hex, "blindMoveToPos")   # only the latest target counts

        def done(reply):
            if reply:
                blind.moving = True
                self._seen(snr)
        cmd = move_cmd(snr, pos, ang)
        cmd.on_done = done
        self._enqueue(cmd)

    def stop(self, snr):
        blind = self.blinds.get(snr)
        if not blind:
            log.warning("stop: unknown blind %s", snr)
            return
        self._remove(blind.hex)
        cmd = stop_cmd(snr)
        cmd.on_done = lambda reply: reply and self._seen(snr)
        self._enqueue(cmd)
        self._enqueue(self._poll_cmd(blind))

    def _seen(self, snr):
        if self.on_seen:
            self.on_seen(snr)

    # -- polling --------------------------------------------------------------

    def _poll_cmd(self, blind):
        def done(reply):
            if not reply or self.blinds.get(blind.snr) is not blind:
                return
            _, _, p = reply
            changed = (p["position"], p["angle"], p["moving"]) != (blind.pos, blind.ang, blind.moving)
            blind.pos, blind.ang, blind.moving = p["position"], p["angle"], p["moving"]
            self._seen(blind.snr)
            if self.on_position:
                self.on_position(blind.snr, blind.pos, blind.ang, blind.moving, changed)
        cmd = get_pos_cmd(blind.snr)
        cmd.on_done = done
        return cmd

    def _poll_loop(self):
        """Poll each blind every poll_interval, or every moving_interval while it moves."""
        while True:
            time.sleep(0.25)
            if not self._ready.is_set():
                continue
            now = time.monotonic()
            for blind in list(self.blinds.values()):
                if blind.no_poll or blind.paused:
                    continue
                interval = self.moving_interval if blind.moving else self.poll_interval
                if now - blind.last_poll >= interval and not self._queued("blindGetPos", blind.hex):
                    blind.last_poll = now
                    self._enqueue(self._poll_cmd(blind))
