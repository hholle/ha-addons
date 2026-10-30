import unittest
from unittest import mock

from app import p300
from tests.fake_serial import FakeController


class FrameTests(unittest.TestCase):
    def test_read_request_layout(self):
        # read 2 bytes at 0x0800: 41 05 00 01 08 00 02 <chk>
        frame = p300.build_request(p300.FN_READ, 0x0800, 2)
        self.assertEqual(frame[:7], bytes([0x41, 0x05, 0x00, 0x01, 0x08, 0x00, 0x02]))
        self.assertEqual(frame[7], (0x05 + 0x00 + 0x01 + 0x08 + 0x00 + 0x02) & 0xFF)

    def test_write_request_includes_data_in_length(self):
        frame = p300.build_request(p300.FN_WRITE, 0x6300, 1, b"\x32")
        self.assertEqual(frame[1], 6)
        self.assertEqual(frame[-2], 0x32)
        self.assertEqual(frame[-1], sum(frame[1:-1]) & 0xFF)

    def test_decode_little_endian(self):
        self.assertEqual(p300.decode_int(bytes([0xD7, 0x00])), 215)
        self.assertEqual(p300.decode_int(bytes([0x38, 0xFF]), signed=True), -200)


class LinkTests(unittest.TestCase):
    def setUp(self):
        sleeper = mock.patch("app.p300.time.sleep")
        sleeper.start()
        self.addCleanup(sleeper.stop)
        self.ctrl = FakeController({
            0x00F8: bytes([0x20, 0xCB]),
            0x0800: bytes([0xD7, 0x00]),
            0x088A: bytes([0x10, 0x27, 0x00, 0x00]),
        })
        self.link = p300.P300Link(self.ctrl)

    def test_open_switches_controller_to_p300(self):
        self.link.open()
        self.assertTrue(self.ctrl.p300_mode)

    def test_open_works_when_controller_is_already_in_p300(self):
        self.ctrl.p300_mode = True
        self.link.open()
        self.assertTrue(self.ctrl.p300_mode)

    def test_read_opens_lazily_and_returns_data(self):
        self.assertEqual(self.link.read(0x00F8, 2), bytes([0x20, 0xCB]))
        self.assertTrue(self.ctrl.p300_mode)

    def test_read_four_bytes(self):
        self.assertEqual(p300.decode_int(self.link.read(0x088A, 4)), 10000)

    def test_write_is_stored(self):
        self.link.write(0x6300, b"\x32")
        self.assertEqual(self.ctrl.memory[0x6300], b"\x32")
        self.assertEqual(self.ctrl.requests[-1][0], p300.FN_WRITE)

    def test_retry_after_lost_request(self):
        self.link.open()
        self.ctrl.drop_next = 1
        self.assertEqual(self.link.read(0x0800, 2), bytes([0xD7, 0x00]))

    def test_retry_after_bad_checksum(self):
        self.link.open()
        self.ctrl.corrupt_next = 1
        self.assertEqual(self.link.read(0x0800, 2), bytes([0xD7, 0x00]))

    def test_gives_up_after_retries(self):
        self.link.open()
        self.ctrl.drop_next = 10
        with self.assertRaises(p300.P300Error):
            self.link.read(0x0800, 2)
        self.assertEqual(len(self.ctrl.requests), 3)  # 1 try + 2 retries

    def test_open_fails_without_controller(self):
        class Silent:
            def reset_input_buffer(self): pass
            def write(self, data): pass
            def read(self, count): return b""
        with self.assertRaises(p300.P300Error):
            p300.P300Link(Silent()).open()

    def test_controller_error_report_raises(self):
        self.link.open()
        original = self.ctrl._handle_request

        def error_report(frame):
            body = bytes([p300.TYPE_ERROR, p300.FN_READ, frame[4], frame[5], 0x01, 0x03])
            resp = bytes([len(body)]) + body
            self.ctrl._out += bytes([p300.ACK_OK, p300.LEADIN]) + resp + bytes([p300.checksum(resp)])

        self.ctrl._handle_request = error_report
        with self.assertRaises(p300.P300Error):
            self.link.read(0x0800, 2)
        self.ctrl._handle_request = original


if __name__ == "__main__":
    unittest.main()
