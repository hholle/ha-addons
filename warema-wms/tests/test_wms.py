import queue
import threading
import time
import unittest
from unittest import mock

from app import wms

# Expected values generated with the previous Node.js implementation (wms-util.js).


class ProtocolTest(unittest.TestCase):
    def test_snr_byte_order(self):
        for num, hexa in [(1132530, "F24711"), (1152669, "9D9611"), (10, "0A0000"), (16777215, "FFFFFF")]:
            self.assertEqual(wms.snr_to_hex(num), hexa)
            self.assertEqual(wms.snr_from_hex(hexa), num)

    def test_encode(self):
        self.assertEqual(wms.get_pos_cmd(1132530).frame, "{R06F24711801001000005}")
        self.assertEqual(wms.move_cmd(1132530, 0, -60).frame, "{R06F247117070030052FFFF}")
        self.assertEqual(wms.move_cmd(1152669, 37, 33).frame, "{R069D96117070034A98FFFF}")
        self.assertEqual(wms.move_cmd(1152669, 100, 100).frame, "{R069D9611707003C8CAFFFF}")
        self.assertEqual(wms.move_cmd(1152669, 50, -100).frame, "{R069D96117070036434FFFF}")
        self.assertEqual(wms.stop_cmd(1152669).frame, "{R069D9611707001FFFFFFFF00}")
        self.assertEqual(wms.scan_request_cmd("3871").frame, "{R04FFFFFF7020387102}")
        self.assertEqual(wms.scan_response_cmd("F24711", "3871").frame, "{R01F247117021387102}")

    def test_decode_position(self):
        self.assertEqual(wms.decode("{r6925118011010000050A81FFFF01}"),
                         ("position", "692511", {"position": 5, "angle": 3, "moving": True}))
        self.assertEqual(wms.decode("{r6925118011010000036480FFFF00}")[2],
                         {"position": 50, "angle": 1, "moving": False})
        self.assertEqual(wms.decode("{r6925118011010000050181FFFF00}")[2]["position"], 1)   # 0.5 rounds up

    def test_decode_misc(self):
        self.assertEqual(wms.decode("{a}")[0], "ack")
        self.assertEqual(wms.decode("{f}")[0], "fwd")
        self.assertEqual(wms.decode("{gWMS Stick}")[0], "stickName")
        self.assertEqual(wms.decode("{r1234567050}")[0], "waveRequest")
        self.assertEqual(wms.decode("{r1234567020387102}"), ("scanRequest", "123456", {"pan_id": "3871"}))
        self.assertEqual(wms.decode("{r1234567021387102}")[2], {"device_type": "02"})

    def test_decode_weather(self):
        _, snr, w = wms.decode("{r4A00E87080001E00000000000500C85A}")
        self.assertEqual((snr, w["wind"], w["lumen"], w["rain"], w["temp"]), ("4A00E8", 30, 0, False, 65))
        _, _, w = wms.decode("{r4A00E87080001E000000000540C85A}")
        self.assertEqual((w["lumen"], w["rain"], w["temp"]), (10, True, 10))

    def test_decode_network_params(self):
        _, _, p = wms.decode("{r123456501838710123456789ABCDEF0123456789ABCDEFFF11}")
        self.assertEqual(p, {"pan_id": "3871", "key": "EFCDAB8967452301EFCDAB8967452301", "channel": 17})
        _, _, p = wms.decode("{r123456506038712011}")
        self.assertEqual(p, {"pan_id": "3871", "channel": 17})

    def test_garbage(self):
        self.assertEqual(wms.decode("{r12}")[0], "unknown")
        self.assertEqual(wms.decode("noise")[0], "unknown")


class FakeSerial:
    """Plays the stick: answers every frame the driver sends."""

    def __init__(self, *args, **kwargs):
        self.sent, self.rx, self.pos = [], queue.Queue(), 40
        FakeSerial.last = self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def flush(self):
        pass

    def write(self, data):
        frame = data.decode()
        self.sent.append(frame)
        if frame in ("{G}",):
            self.rx.put(b"{gWMS}")
        elif frame == "{V}":
            self.rx.put(b"{v1}")
        elif frame.startswith(("{K401", "{M%")):
            self.rx.put(b"{a}")
        elif frame.startswith("{R06") and frame[10:14] == "8010":
            self.rx.put(f"{{r{frame[4:10]}8011010000050A81FFFF{'01' if self.pos else '00'}}}".replace("0A", f"{self.pos:02X}", 1).encode())
        elif frame.startswith("{R06") and frame[10:14] == "7070":
            self.rx.put(f"{{r{frame[4:10]}70710000000000}}".encode())
        elif frame.startswith("{R04"):
            self.rx.put(b"{r2000117021387125}")   # one device, SNR hex 200011, type 25

    def read_until(self, end):
        try:
            return self.rx.get(timeout=0.05)
        except queue.Empty:
            return b""

    def close(self):
        pass


class StickTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(wms.serial, "Serial", FakeSerial)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.positions, self.seen = [], []
        self.stick = wms.Stick("/dev/null", 17, "3871", "00" * 16, poll_interval=0.2, moving_interval=0.05,
                               on_position=lambda *a: self.positions.append(a),
                               on_seen=self.seen.append)
        self.stick.start()
        self.assertTrue(self.stick.wait_ready(5))

    def wait(self, cond, timeout=5):
        end = time.time() + timeout
        while time.time() < end and not cond():
            time.sleep(0.02)
        self.assertTrue(cond())

    def test_init_sequence(self):
        self.assertEqual(FakeSerial.last.sent[:2], ["{G}", "{V}"])
        self.assertTrue(FakeSerial.last.sent[2].startswith("{K401"))
        self.assertEqual(FakeSerial.last.sent[3], "{M%173871}")

    def test_poll_reports_changes_only_once(self):
        self.stick.add_blind(1132530)
        self.wait(lambda: len(self.seen) >= 3)
        changed = [p for p in self.positions if p[4]]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0][:4], (1132530, 20, 3, True))

    def test_move_marks_moving(self):
        self.stick.add_blind(1152669)
        self.wait(lambda: self.positions)
        self.stick.blinds[1152669].moving = False
        FakeSerial.last.sent.clear()
        self.stick.set_position(1152669, 70, 0)
        self.wait(lambda: any(f.startswith("{R069D9611707003") for f in FakeSerial.last.sent))
        self.wait(lambda: self.stick.blinds[1152669].moving)

    def test_newer_move_replaces_queued_one(self):
        self.stick.add_blind(1152669)
        with self.stick._cond:   # hold the worker off so both commands are queued together
            self.stick.set_position(1152669, 10, 0)
            self.stick.set_position(1152669, 90, 0)
            queued = [c.frame for c in self.stick._queue if c.kind == "blindMoveToPos"]
        self.assertEqual(queued, [wms.move_cmd(1152669, 90, 0).frame])

    def test_scan(self):
        self.assertEqual(self.stick.scan(), [(wms.snr_from_hex("200011"), "25")])

    def test_network_discovery(self):
        joins = []
        stick = wms.Stick("/dev/null", 17, "FFFF", "00" * 16, on_join=joins.append)
        stick.start()
        self.assertTrue(stick.wait_ready(5))
        port = FakeSerial.last
        self.assertEqual(port.sent[3], "{M%17FFFF}")   # stick listens on PAN FFFF
        port.sent.clear()
        port.rx.put(b"{r123456501838710123456789ABCDEF0123456789ABCDEFFF11}")   # handheld sends network parameters
        self.wait(lambda: joins and "{a}" in port.sent)
        self.assertEqual(joins[0], {"pan_id": "3871", "key": "EFCDAB8967452301EFCDAB8967452301", "channel": 17})
        port.sent.clear()
        port.rx.put(b"{r1234567020387102}")   # handheld scans: stick answers with its own scan response
        self.wait(lambda: "{R011234567021FFFF02}" in port.sent)

    def test_no_poll_blind_is_not_polled(self):
        self.stick.add_blind(1132530, no_poll=True)
        time.sleep(0.6)
        self.assertFalse([f for f in FakeSerial.last.sent if f.startswith("{R06")])


if __name__ == "__main__":
    unittest.main()
