import os
import unittest

from app import codings, p300

FILE = os.path.join(os.path.dirname(__file__), "..", "datapoints", "20CB_codings.yaml")


class FakeLink:
    def __init__(self, memory):
        self.memory = memory

    def read(self, addr, length):
        if addr not in self.memory:
            raise p300.P300Rejected("no")
        return self.memory[addr].to_bytes(length, "little", signed=self.memory[addr] < 0)


class CodingTests(unittest.TestCase):
    def test_file_loads_and_is_consistent(self):
        entries = codings.load(FILE)
        self.assertGreater(len(entries), 90)
        addrs = [e["addr"] for e in entries]
        self.assertEqual(len(addrs), len(set(addrs)))
        by_addr = {e["addr"]: e for e in entries}
        self.assertEqual(by_addr[0x6759]["code"], "59")
        self.assertEqual(by_addr[0x6762]["code"], "62")
        self.assertEqual(by_addr[0x27D4]["signed"], True)
        for e in entries:
            self.assertIn(e["len"], (1, 2))
            self.assertEqual(e["addr"] & 0xFF, int(e["code"], 16))   # code NN lives at 0xGGNN

    def test_read_all_marks_refused_addresses(self):
        entries = [{"addr": 0x6759, "code": "59", "group": "Warmwasser", "len": 1, "signed": False, "name": "Einschaltpunkt"},
                   {"addr": 0x27D4, "code": "D4", "group": "Heizkreis", "len": 1, "signed": True, "name": "Niveau"},
                   {"addr": 0x6762, "code": "62", "group": "Warmwasser", "len": 1, "signed": False, "name": "Nachlauf"}]
        rows = codings.read_all(FakeLink({0x6759: 0, 0x27D4: -3}), entries)
        self.assertEqual([v for _, v in rows], [0, -3, None])
        text = "\n".join(codings.format_rows(rows))
        self.assertIn("59  6759  0     Einschaltpunkt", text)
        self.assertIn("62  6762  -     Nachlauf", text)

    def test_info_falls_back_to_shorter_reads(self):
        class Link:
            def read(self, addr, length):
                if addr == 0x00F8 and length <= 4:
                    return bytes([0x20, 0xCB, 0x00, 0x4F])[:length]
                raise p300.P300Rejected("no")

        text = "\n".join(codings.read_info(Link()))
        self.assertIn("00F8  20 CB 00 4F", text)
        self.assertIn("1010  -", text)

    def test_datapoints_follow_the_heating_circuit_scheme(self):
        entries = codings.load(FILE)
        groups = lambda scheme: {e.name.split()[-1] for e in codings.to_datapoints(entries, scheme)}
        ids = lambda scheme: {e.id for e in codings.to_datapoints(entries, scheme)}
        self.assertNotIn("kodierung_37a0", ids(2))      # scheme 2: A1 + hot water, no M2
        self.assertIn("kodierung_27a0", ids(2))
        self.assertIn("kodierung_6759", ids(2))
        self.assertNotIn("kodierung_27a0", ids(4))      # scheme 4: M2 + hot water, no A1
        self.assertIn("kodierung_37a0", ids(4))
        self.assertTrue({"kodierung_27a0", "kodierung_37a0"} <= ids(None))   # unknown: keep all
        point = next(p for p in codings.to_datapoints(entries, 2) if p.id == "kodierung_6759")
        self.assertEqual(point.name, "Codierung 59 Einschaltpunkt Speicher")
        self.assertEqual((point.category, point.group, point.uid), ("diagnostic", "config", "vito_kodierung_6759"))
        signed = next(p for p in codings.to_datapoints(entries, 2) if p.id == "kodierung_27d4")
        self.assertTrue(signed.signed)


if __name__ == "__main__":
    unittest.main()
