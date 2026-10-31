"""Serial driver for the WMS USB stick: one worker thread sends one command at a time and waits for
the expected answer (with timeout, retry and a short pause after each answer, so the radio is not flooded).
Callbacks are called from the driver threads."""

import logging
import threading
import time
from collections import deque

import serial

from .codec import BAUD, GAP, decode, snr_from_hex, snr_to_hex
from .commands import (Command, ack_cmd, beckon_cmd, get_pos_cmd, mb8_read_cmd, move_cmd, ping_cmd,
                       quality_cmd, scan_request_cmd, scan_response_cmd, stop_cmd)

log = logging.getLogger(__name__)

# ─── Driver ───────────────────────────────────────────────────────────────────

class Blind:
    def __init__(self, snr, no_poll=False):
        self.snr = snr
        self.hex = snr_to_hex(snr)
        self.no_poll = no_poll   # device does not answer blindGetPos (type 2A)
        self.paused = False      # temporarily excluded from polling
        self.pos, self.ang, self.moving = -1, 0, False
        self.last_poll = float("-inf")
        self.watch_until = 0.0   # no_poll devices are polled until then (after a command)
        self.misses = 0          # unanswered polls in a row while watching


class Stick:
    """Callbacks (all optional, called from the driver threads):
      on_position(snr, pos, ang, moving, changed)  answer to a position poll
      on_seen(snr)                                  any answer from a device
      on_weather(snr, params)                       weather station broadcast
      on_join(params)                               network parameters (PAN ID FFFF)
      on_poll(snr, ok, attempts, latency_ms)        result of a position poll (answered or timed out)
      on_command_failed(snr)                        a move command got no answer from the device
    """

    def __init__(self, path, channel, pan_id, key, poll_interval=30.0, moving_interval=1.0,
                 on_position=None, on_seen=None, on_weather=None, on_join=None, on_poll=None,
                 on_command_failed=None):
        self.path, self.channel, self.pan_id, self.key = path, channel, pan_id, key
        self.poll_interval, self.moving_interval = poll_interval, moving_interval
        self.on_position, self.on_seen = on_position, on_seen
        self.on_weather, self.on_join = on_weather, on_join
        self.on_poll = on_poll
        self.on_command_failed = on_command_failed
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
                cmd.attempts += 1
                cmd.t_sent = time.monotonic()
                log.debug("SND %s (attempt %d)", cmd.frame, cmd.attempts)
                with self._write_lock:
                    self._port.write(cmd.frame.encode("latin-1"))
                    self._port.flush()
            except (serial.SerialException, OSError, AttributeError) as e:
                log.error("Serial write failed: %s", e)
                self._current = None
                continue
            got = cmd.event.wait(cmd.timeout)
            self._current = None
            if got and cmd.failure is not None:
                self._finish(cmd, None)
                time.sleep(GAP)
            elif got:
                cmd.latency = round((time.monotonic() - cmd.t_sent) * 1000)
                self._finish(cmd, cmd.reply)
                time.sleep(GAP + cmd.delay_after)
            elif cmd.expect is None:
                self._finish(cmd, None)
            elif cmd.retry > 0:
                cmd.retry -= 1
                log.info("Retry %s %s (attempt %d got no answer)", cmd.kind, cmd.snr_hex, cmd.attempts)
                self._enqueue(cmd)
            else:
                log.info("Timeout %s %s after %d attempt(s)", cmd.kind, cmd.snr_hex or "", cmd.attempts)
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
        elif kind == "stickFailure" and cur and cur.expect is not None:
            log.info("Stick refused %s: failure code %r", cur.kind, params.get("code"))
            cur.failure = params.get("code")
            cur.event.set()   # reply stays None: no retry, the stick will not answer differently
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
            else:
                log.warning("Device %s did not answer the move command", snr)
                if self.on_command_failed:
                    self.on_command_failed(snr)
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

    def ping(self, snr, callback):
        """Radio ping. ``callback(ok, latency_ms)``; silent, the device does not move."""
        cmd = ping_cmd(snr)
        cmd.on_done = lambda reply: callback(reply is not None, cmd.latency)
        self._enqueue(cmd)

    def beckon(self, snr, callback=None):
        """Make a device wave (short move or blink). ``callback(ok)``."""
        cmd = beckon_cmd(snr)
        if callback:
            cmd.on_done = lambda reply: callback(reply is not None)
        self._enqueue(cmd)

    def connection_quality(self, snr, callback):
        """Link quality of a device from the stick. ``callback(values, failure)``: values is
        {tx_lqi, tx_rssi, rx_lqi, rx_rssi} or None; failure is the stick failure code when the
        stick refused the command (it does not implement it), otherwise None."""
        cmd = quality_cmd(snr)
        cmd.on_done = lambda reply: callback(reply[2] if reply else None, cmd.failure)
        self._enqueue(cmd)

    def read_block(self, snr, block, address, size, callback):
        """Read receiver memory. ``callback(data_hex or None)``."""
        cmd = mb8_read_cmd(snr, block, address, size)
        cmd.on_done = lambda reply: callback(reply[2]["data"] if reply else None)
        self._enqueue(cmd)

    def watch(self, snr, seconds=60):
        """Poll a device that is normally not polled (type 2A) until it stops moving."""
        blind = self.blinds.get(snr)
        if blind:
            blind.watch_until = time.monotonic() + seconds
            blind.misses = 0
            blind.last_poll = float("-inf")

    def _seen(self, snr):
        if self.on_seen:
            self.on_seen(snr)

    # -- polling --------------------------------------------------------------

    def _poll_cmd(self, blind):
        def done(reply):
            if self.blinds.get(blind.snr) is not blind:
                return
            if self.on_poll:
                self.on_poll(blind.snr, bool(reply), cmd.attempts, cmd.latency)
            if not reply:
                if blind.no_poll:   # this motor type may stay silent: give up after 3 misses
                    blind.misses += 1
                    if blind.misses >= 3:
                        blind.watch_until = 0.0
                return
            _, _, p = reply
            blind.misses = 0
            if blind.no_poll and not p["moving"]:
                blind.watch_until = 0.0
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
                if blind.paused or (blind.no_poll and now >= blind.watch_until):
                    continue
                interval = self.moving_interval if blind.moving else self.poll_interval
                if now - blind.last_poll >= interval and not self._queued("blindGetPos", blind.hex):
                    blind.last_poll = now
                    self._enqueue(self._poll_cmd(blind))
