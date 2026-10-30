"""
P300 protocol driver for the Viessmann Optolink interface.

Framing follows vcontrold src/framer.c:

    request : 41 | len | 00 | fn | addr_hi addr_lo | n | [data] | chk
    response: 41 | len | 01 | fn | addr_hi addr_lo | n | data   | chk

len is the number of bytes between len and chk, chk is the sum of all bytes
from len up to the last payload byte, modulo 256. Every request is acknowledged
with a single 0x06 before the response frame follows.
"""

import logging
import time

log = logging.getLogger(__name__)

LEADIN = 0x41
RESET = 0x04
ENABLE = bytes([0x16, 0x00, 0x00])

ACK_OK = 0x06
ACK_NOT_INIT = 0x05
ACK_ERROR = 0x15

TYPE_REQUEST = 0x00
TYPE_RESPONSE = 0x01
TYPE_ERROR = 0x03

FN_READ = 0x01
FN_WRITE = 0x02

OPEN_ATTEMPTS = 3

# serial line settings of the Optolink (vcontrold src/io.c)
BAUDRATE = 4800
READ_TIMEOUT_S = 5.0


class P300Error(Exception):
    """Protocol or link failure. The link is re-opened before the next request."""


class P300Rejected(P300Error):
    """The controller answered with an error report (e.g. unknown address). The link stays usable."""


class P300Timeout(P300Error):
    """No (complete) answer from the controller."""


def checksum(data):
    return sum(data) & 0xFF


def build_request(function, addr, length, data=b""):
    body = bytes([TYPE_REQUEST, function, addr >> 8, addr & 0xFF, length]) + bytes(data)
    frame = bytes([len(body)]) + body
    return bytes([LEADIN]) + frame + bytes([checksum(frame)])


def open_serial(device, timeout=READ_TIMEOUT_S):
    import serial  # imported lazily so the protocol code can be tested without pyserial

    port = serial.Serial(
        device,
        BAUDRATE,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_EVEN,
        stopbits=serial.STOPBITS_TWO,
        timeout=timeout,
    )
    # vcontrold sets DTR high: the opto adapter takes its supply voltage from it
    port.dtr = True
    return port


class _LoggedPort:
    """Wraps a serial port and logs every byte at debug level."""

    def __init__(self, port):
        self._port = port

    def reset_input_buffer(self):
        self._port.reset_input_buffer()

    def write(self, data):
        log.debug("TX %s", bytes(data).hex(" "))
        return self._port.write(data)

    def read(self, count):
        data = self._port.read(count)
        log.debug("RX %s (wanted %d)", data.hex(" ") if data else "-", count)
        return data


class P300Link:
    """One serial Optolink connection. Not thread-safe: use one caller at a time."""

    def __init__(self, port, retries=2):
        self._port = _LoggedPort(port)
        self._retries = retries
        self._open = False

    def open(self):
        """Reset to a known state and switch the controller to P300 mode."""
        self._open = False
        for attempt in range(1, OPEN_ATTEMPTS + 1):
            try:
                self._port.reset_input_buffer()
                self._port.write(bytes([RESET]))
                ack = self._read_exact(1)[0]
                if ack not in (ACK_OK, ACK_NOT_INIT):
                    log.warning("Unexpected reset answer 0x%02X", ack)
                self._port.write(ENABLE)
                ack = self._read_exact(1)[0]
                if ack == ACK_OK:
                    self._open = True
                    log.info("P300 link open")
                    return
                log.warning("Enable answered 0x%02X (attempt %d)", ack, attempt)
            except P300Timeout:
                log.warning("No answer while opening P300 (attempt %d)", attempt)
        raise P300Error("Could not open P300 link (%d attempts)" % OPEN_ATTEMPTS)

    def close(self):
        """Send the reset byte so the controller leaves P300 mode, then drop the link."""
        if self._open:
            try:
                self._port.write(bytes([RESET]))
                self._read_exact(1)
            except P300Error:
                pass
        self._open = False

    def read(self, addr, length):
        """Read length bytes from controller memory at addr."""
        body = self._transaction(FN_READ, addr, length)
        if len(body) < 5 + length:
            raise P300Error("Short read answer for %04X: %d bytes" % (addr, len(body) - 5))
        return bytes(body[5:5 + length])

    def write(self, addr, data):
        """Write data to controller memory at addr."""
        self._transaction(FN_WRITE, addr, len(data), data)

    def _transaction(self, function, addr, length, data=b""):
        last = None
        for attempt in range(self._retries + 1):
            try:
                if not self._open:
                    self.open()
                return self._exchange(function, addr, length, data)
            except P300Rejected:
                raise  # a clean "no" from the controller: retrying will not change the answer
            except P300Error as err:
                last = err
                self._open = False  # force re-sync before the next try
                log.warning("P300 %02X %04X failed (attempt %d): %s", function, addr, attempt + 1, err)
                time.sleep(0.2)
        raise last

    def _exchange(self, function, addr, length, data):
        self._port.reset_input_buffer()
        self._port.write(build_request(function, addr, length, data))

        ack = self._read_exact(1)[0]
        if ack != ACK_OK:
            raise P300Error("Request not acknowledged: 0x%02X" % ack)

        lead = self._read_exact(1)[0]
        if lead != LEADIN:
            raise P300Error("Bad leadin 0x%02X" % lead)
        size = self._read_exact(1)[0]
        rest = self._read_exact(size + 1)  # body + checksum
        body, chk = rest[:-1], rest[-1]

        if checksum(bytes([size]) + body) != chk:
            raise P300Error("Checksum mismatch")
        if len(body) < 5:
            raise P300Error("Frame too short")
        if body[0] == TYPE_ERROR:
            code = body[5] if len(body) > 5 else -1
            raise P300Rejected("Controller error report at %02X%02X, code %d" % (body[2], body[3], code))
        if body[0] != TYPE_RESPONSE or body[1] != function:
            raise P300Error("Unexpected response type/function %02X/%02X" % (body[0], body[1]))
        if (body[2] << 8 | body[3]) != addr:
            raise P300Error("Response address %02X%02X does not match %04X" % (body[2], body[3], addr))
        return body

    def _read_exact(self, count):
        data = self._port.read(count)
        if len(data) != count:
            raise P300Timeout("Expected %d bytes, got %d" % (count, len(data)))
        return data


def decode_int(data, signed=False):
    """Controller values are little-endian (vcontrold src/unit.c)."""
    return int.from_bytes(data, "little", signed=signed)
