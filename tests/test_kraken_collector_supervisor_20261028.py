"""Kraken collector supervisor: all 5 data collectors managed, exact matching, stale watchdog. Read-only data."""
import os, sys, unittest
KQ = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "KrakenQuantDesk")
sys.path.insert(0, KQ)


class T(unittest.TestCase):
    def setUp(self):
        if not os.path.exists(os.path.join(KQ, "kraken_collector_supervisor.py")):
            self.skipTest("no KrakenQuantDesk")
        import kraken_collector_supervisor as k
        self.k = k

    def test_five_data_collectors_managed(self):
        names = [s for s, _ in self.k.COLLECTORS]
        for s in self.k.DATA5:
            self.assertIn(s, names)
        self.assertEqual(len(self.k.DATA5), 5)

    def test_engine_never_in_lists(self):
        names = [s for s, _ in self.k.COLLECTORS] + list(self.k.DATA5)
        self.assertFalse(any("paper_bot" in n or "supervisor" in n or "engine" in n for n in names))

    def test_etime(self):
        self.assertEqual(self.k._etime_s("01:05"), 65)
        self.assertEqual(self.k._etime_s("1-00:00:10"), 86410)

    def test_thin_tag_not_matched_as_perp(self):
        import subprocess
        real = subprocess.run
        class R: stdout = "111 10:00 /x/Python kraken_perp_orderbook_collector.py --tag thin\n"
        subprocess.run = lambda *a, **kw: R()
        try:
            self.assertEqual(self.k._pids("kraken_perp_orderbook_collector.py"), [])
        finally:
            subprocess.run = real


if __name__ == "__main__":
    unittest.main()
