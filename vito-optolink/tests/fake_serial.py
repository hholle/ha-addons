"""Simulated Vitotronic on a serial port, for tests without hardware."""

from app import p300


class FakeController:
    """Answers P300 requests from a dict {addr: bytes}."""

    def __init__(self, memory=None):
        self.memory = dict(memory or {})
        self.p300_mode = False
        self.requests = []   # parsed (function, addr, length, data) of every request
        self.drop_next = 0   # swallow this many requests without answering
        self.corrupt_next = 0  # flip the checksum of this many responses
        self._out = bytearray()

    # -- serial port interface used by P300Link --
    def reset_input_buffer(self):
        self._out.clear()

    def write(self, data):
        data = bytes(data)
        if data == bytes([p300.RESET]):
            self._out.append(p300.ACK_OK if self.p300_mode else p300.ACK_NOT_INIT)
            self.p300_mode = False
        elif data == p300.ENABLE:
            self.p300_mode = True
            self._out.append(p300.ACK_OK)
        elif data and data[0] == p300.LEADIN and self.p300_mode:
            self._handle_request(data)

    def read(self, count):
        chunk = bytes(self._out[:count])
        del self._out[:count]
        return chunk

    # -- simulation --
    def _handle_request(self, frame):
        size = frame[1]
        body = frame[2:2 + size]
        assert frame[2 + size] == p300.checksum(frame[1:2 + size]), "bad request checksum"
        function, addr, length = body[1], body[2] << 8 | body[3], body[4]
        data = body[5:]
        self.requests.append((function, addr, length, data))

        if self.drop_next:
            self.drop_next -= 1
            return

        self._out.append(p300.ACK_OK)
        if function == p300.FN_READ:
            payload = self.memory.get(addr, bytes(length))[:length]
        else:
            self.memory[addr] = data
            payload = b""
        resp_body = bytes([p300.TYPE_RESPONSE, function, body[2], body[3], length]) + payload
        resp = bytes([len(resp_body)]) + resp_body
        chk = p300.checksum(resp)
        if self.corrupt_next:
            self.corrupt_next -= 1
            chk ^= 0xFF
        self._out += bytes([p300.LEADIN]) + resp + bytes([chk])
