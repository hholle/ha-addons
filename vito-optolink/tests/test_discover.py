import unittest
from unittest import mock

from app import discover, p300
from tests.fake_serial import FakeController


class FakeLink:
    """read(addr, length) from a {addr: byte} dict; missing addresses are rejected."""

    def __init__(self, memory):
        self.memory = memory
        self.calls = []

    def read(self, addr, length):
        self.calls.append((addr, length))
        if any(addr + i not in self.memory for i in range(length)):
            raise p300.P300Rejected("unknown address %04X" % addr)
        return bytes(self.memory[addr + i] for i in range(length))


class ParseTests(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(discover.parse_ranges("0800-08FF, 5500-55FF"), [(0x0800, 0x08FF), (0x5500, 0x55FF)])
        self.assertEqual(discover.parse_ranges("55DE"), [(0x55DE, 0x55DE)])
        with self.assertRaises(ValueError):
            discover.parse_ranges("08FF-0800")

    def test_refs(self):
        self.assertEqual(discover.parse_refs("11.8 41,1"), [11.8, 41.1])
        self.assertEqual(discover.parse_refs("11,8; 41.1"), [11.8, 41.1])
        self.assertEqual(discover.parse_refs(""), [])


class ScanTests(unittest.TestCase):
    def test_reads_in_chunks_and_collects_rejections(self):
        link = FakeLink({a: a & 0xFF for a in range(0x0800, 0x0810)})
        values, rejected = discover.scan_once(link, [(0x0800, 0x081F)])
        self.assertEqual(len(values), 16)
        self.assertEqual(rejected, [0x0810, 0x0818])
        self.assertEqual(link.calls[0], (0x0800, 8))

    def test_last_chunk_is_shortened(self):
        link = FakeLink({a: 0 for a in range(0x0800, 0x080A)})
        discover.scan_once(link, [(0x0800, 0x0809)])
        self.assertEqual(link.calls[-1], (0x0808, 2))

    def test_scan_does_not_use_write(self):
        class ReadOnly(FakeController):
            pass
        ctrl = ReadOnly({0x0800: bytes(8)})
        with mock.patch("app.p300.time.sleep"):
            discover.scan_once(p300.P300Link(ctrl, retries=0), [(0x0800, 0x0807)])
        self.assertTrue(all(fn == p300.FN_READ for fn, *_ in ctrl.requests))


class ProbeTests(unittest.TestCase):
    def test_rejected_chunk_is_probed_byte_by_byte(self):
        # 0803 and 0805 readable, the rest of the chunk is not
        link = FakeLink({0x0803: 11, 0x0805: 22})
        link.read = lambda addr, length, orig=link.read: orig(addr, length)
        values, rejected = discover.scan_once(link, [(0x0800, 0x0807)])
        self.assertEqual(values, {})
        self.assertEqual(rejected, [0x0800])
        self.assertEqual(discover.probe_single(link, [(0x0800, 0x0807)], rejected), {0x0803: 11, 0x0805: 22})

    def test_runs_merge_contiguous_addresses_up_to_chunk_size(self):
        self.assertEqual(discover.to_runs([0x0803, 0x0804, 0x0805, 0x0810]), [(0x0803, 3), (0x0810, 1)])
        self.assertEqual(discover.to_runs(range(0x0800, 0x080A)), [(0x0800, 8), (0x0808, 2)])
        self.assertEqual(discover.to_runs([]), [])

    def test_later_rounds_only_read_known_runs(self):
        link = FakeLink({a: a & 0xFF for a in (0x0803, 0x0804, 0x0810)})
        discover.scan_runs(link, [(0x0803, 2), (0x0810, 1)])
        self.assertEqual(link.calls, [(0x0803, 2), (0x0810, 1)])


class AnalyzeTests(unittest.TestCase):
    def rounds(self, modulation, flue):
        # 0900: modulation byte, 0910/0911: flue gas word /10, 0920: constant
        out = []
        for mod, fl in zip(modulation, flue):
            out.append({0x0900: mod, 0x0910: fl & 0xFF, 0x0911: fl >> 8, 0x0920: 7})
        return out

    def test_finds_byte_that_separates_flame(self):
        rounds = self.rounds([0, 0, 40, 55, 0], [450, 440, 800, 850, 500])
        flames = [False, False, True, True, False]
        res = discover.analyze(rounds, flames)
        self.assertTrue(any(l.startswith("0900") for l in res["separating"]))
        self.assertTrue(any(l.startswith("0910") and "word" in l for l in res["separating"]))
        self.assertFalse(any(l.startswith("0920") for l in res["changing"]))

    def test_no_flame_change_gives_no_separation(self):
        rounds = self.rounds([0, 0, 0], [450, 440, 430])
        res = discover.analyze(rounds, [False, False, False])
        self.assertEqual(res["separating"], [])

    def test_temperature_match_uses_signed_tenths(self):
        rounds = [{0x0800: 0x76, 0x0801: 0x00}, {0x0800: 0x77, 0x0801: 0x00}]
        res = discover.analyze(rounds, [None, None], refs=[11.8])
        self.assertTrue(any(l.startswith("0800") for l in res["temp_matches"]))
        res = discover.analyze(rounds, [None, None], refs=[30.0])
        self.assertEqual(res["temp_matches"], [])


if __name__ == "__main__":
    unittest.main()


class RunTests(unittest.TestCase):
    def test_report_contains_operating_context(self):
        from app import datapoints
        import os, tempfile
        _, pts = datapoints.load(os.path.join(os.path.dirname(__file__), "..", "datapoints", "20CB.yaml"))
        by_id = {p.id: p for p in pts}
        flame = iter([0, 0, 0x0B, 0x0B])
        memory = {0x0900: 0}

        class Link(FakeLink):
            def read(self, addr, length):
                if addr == by_id["flamme"].addr:
                    return bytes([next(flame)])
                if addr == by_id["betriebsart_hk"].addr:
                    return bytes([0x00])
                memory[0x0900] = 0 if memory[0x0900] else 50
                return super().read(addr, length)

        link = Link(memory)
        with tempfile.TemporaryDirectory() as tmp, mock.patch("app.discover.REPORT_DIR", tmp), \
                mock.patch("app.discover.time.sleep"), self.assertLogs("app.discover", "INFO") as logs:
            discover.run(link, [(0x0900, 0x0900)], 4, 1, by_id["flamme"].addr, (), [by_id["betriebsart_hk"]])
            files = os.listdir(tmp)
        text = "\n".join(logs.output)
        self.assertIn("Betriebsart Heizkreis: Abschaltbetr. (Dauernd)", text)
        self.assertEqual(len(files), 1)
