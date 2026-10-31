import time
import unittest
from unittest import mock

import wmslib
from wmslib import codec
from tests.test_wms import FakeSerial


class CodecTest(unittest.TestCase):
    def test_report_envelope(self):
        # key mode 0 + WideP2P = the "R06" the community library uses
        self.assertEqual(codec.report("9D9611", "5040"), "{R069D96115040}")
        self.assertEqual(codec.report("FFFFFF", "7020", "387102", codec.KEY0, codec.WIDE_BC), "{R04FFFFFF7020387102}")
        self.assertEqual(codec.report("9D9611", "50AC", "", codec.PLAIN, codec.P2P), "{R219D961150AC}")
        self.assertEqual(codec.report("9D9611", "8010", "01000005", codec.KEY1, codec.P2P), "{R119D9611801001000005}")

    def test_mb8_payload(self):
        self.assertEqual(codec.mb8_payload(1, 0, 5), "01000005")
        self.assertEqual(codec.mb8_payload(9, 19, 2), "09130002")            # no-sleep counter
        self.assertEqual(codec.mb8_payload(38, 0x01E0, 1), "26E00101")        # address little endian
        self.assertEqual(codec.mb8_payload(9, 19, 2, "7800"), "091300027800")

    def test_commands(self):
        self.assertEqual(wmslib.ping_cmd(1152669).frame, "{R069D96115040}")
        self.assertEqual(wmslib.beckon_cmd(1152669).frame, "{R069D96117050}")
        self.assertEqual(wmslib.quality_cmd(1152669).frame, "{Q9D9611}")
        self.assertEqual(wmslib.mb8_read_cmd(1152669, 15, 4, 4).frame, "{R069D96118010" "0F040004}")
        self.assertEqual(wmslib.mb8_write_cmd(1152669, 9, 19, "7800").frame, "{R069D96118020" "091300027800}")
        self.assertEqual(wmslib.ping_cmd(1152669).expect, "networkAck")

    def test_decode_quality(self):
        self.assertEqual(codec.decode("{q9D96114A5F3C58}"),
                         ("connectionQuality", "9D9611", {"tx_lqi": 0x4A, "tx_rssi": 0x5F, "rx_lqi": 0x3C, "rx_rssi": 0x58}))
        self.assertEqual(codec.decode("{q9D9611}")[0], "unknown")

    def test_decode_performance_and_failure(self):
        self.assertEqual(codec.decode("{yC17:5A}"), ("performance", "000000", {"channel": 17, "level": 0x5A}))
        self.assertEqual(codec.decode("{fc}"), ("stickFailure", "000000", {"code": "c"}))
        self.assertEqual(codec.decode("{f}")[0], "fwd")

    def test_decode_mb8(self):
        # block 15 address 4: last reception time of sensor 0 (4 bytes)
        self.assertEqual(codec.decode("{r9D96118011" + "0F040004" + "78563412" + "}"),
                         ("mb8Read", "9D9611", {"block": 15, "address": 4, "size": 4, "data": "78563412"}))
        self.assertEqual(codec.decode("{r9D96118021}")[0], "mb8Write")
        self.assertEqual(codec.decode("{r9D961180FE05}"), ("mb8Failure", "9D9611", {"code": 5}))
        self.assertEqual(codec.decode("{r9D961150AC}")[0], "networkAck")
        self.assertEqual(codec.decode("{r9D96118011}")[0], "unknown")   # truncated

    def test_position_still_wins_over_generic_read(self):
        self.assertEqual(codec.decode("{r6925118011010000050A81FFFF01}")[0], "position")


class QualityFakeSerial(FakeSerial):
    """Stick that implements ping and connection quality; configurable per test."""

    quality_supported = True
    ping_supported = True

    def write(self, data):
        frame = data.decode()
        if frame.startswith("{Q"):
            self.sent.append(frame)
            self.rx.put(f"{{q{frame[2:8]}4A5F3C58}}".encode() if self.quality_supported else b"{fs}")
        elif frame.startswith("{R06") and frame[10:14] == "5040":
            self.sent.append(frame)
            self.rx.put(b"{a}")
            if self.ping_supported:
                self.rx.put(f"{{r{frame[4:10]}50AC}}".encode())
        elif frame.startswith("{R06") and frame[10:14] == "8010" and frame[14:22] == "0F040004":
            self.sent.append(frame)
            self.rx.put(f"{{r{frame[4:10]}80110F04000478563412}}".encode())
        else:
            super().write(data)


class DriverApiTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(wmslib.driver.serial, "Serial", QualityFakeSerial)
        patcher.start()
        self.addCleanup(patcher.stop)
        QualityFakeSerial.quality_supported = QualityFakeSerial.ping_supported = True
        self.stick = wmslib.Stick("/dev/null", 17, "3871", "00" * 16, poll_interval=60, moving_interval=60)
        self.stick.start()
        self.assertTrue(self.stick.wait_ready(5))

    def call(self, fn, *args):
        box = []
        fn(*args, lambda *res: box.append(res))
        end = time.time() + 5
        while time.time() < end and not box:
            time.sleep(0.02)
        self.assertTrue(box, "no callback")
        return box[0]

    def test_ping_ok(self):
        ok, latency = self.call(self.stick.ping, 1152669)
        self.assertTrue(ok)
        self.assertIsNotNone(latency)

    def test_ping_silent_device(self):
        QualityFakeSerial.ping_supported = False
        ok, _ = self.call(self.stick.ping, 1152669)
        self.assertFalse(ok)

    def test_connection_quality(self):
        values, failure = self.call(self.stick.connection_quality, 1152669)
        self.assertEqual(values, {"tx_lqi": 0x4A, "tx_rssi": 0x5F, "rx_lqi": 0x3C, "rx_rssi": 0x58})
        self.assertIsNone(failure)

    def test_connection_quality_refused_by_stick(self):
        QualityFakeSerial.quality_supported = False
        started = time.time()
        values, failure = self.call(self.stick.connection_quality, 1152669)
        self.assertIsNone(values)
        self.assertEqual(failure, "s")
        self.assertLess(time.time() - started, 0.4)   # no retry cycle

    def test_read_block(self):
        (data,) = self.call(self.stick.read_block, 1152669, 15, 4, 4)
        self.assertEqual(data, "78563412")


if __name__ == "__main__":
    unittest.main()


class NoAngleTest(unittest.TestCase):
    def test_ff_angle_means_none(self):
        # real answer of an awning at rest: position 0 %, angle FF, no valances
        self.assertEqual(codec.decode("{r9D961180110100000500FFFFFF00}")[2],
                         {"position": 0, "angle": None, "moving": False})

    def test_network_ack_keeps_its_data(self):
        self.assertEqual(codec.decode("{rF2471150AC2BBE}"), ("networkAck", "F24711", {"data": "2BBE"}))

    def test_real_quality_answers(self):
        # seen on the WMS USB-Stick, firmware 37605107
        self.assertEqual(codec.decode("{qF24711DE1ADD63}")[2], {"tx_lqi": 222, "tx_rssi": 26, "rx_lqi": 221, "rx_rssi": 99})
        self.assertEqual(codec.decode("{q083C12E124E46C}")[2], {"tx_lqi": 225, "tx_rssi": 36, "rx_lqi": 228, "rx_rssi": 108})
