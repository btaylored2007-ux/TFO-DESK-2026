#!/usr/bin/env python3
"""audit 2026-09-26: tfo_crypto.rails_heartbeat liveness file. Offline; temp dir only; never touches ledgers."""
import importlib, json, os, sys, tempfile, unittest
DESK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DESK)

class Heartbeat(unittest.TestCase):
    def test_atomic_liveness_file_is_not_ledger_shaped(self):
        tc = importlib.import_module("tfo_crypto"); bc = importlib.import_module("book_coverage")
        old = (tc.R, tc.HB)
        with tempfile.TemporaryDirectory() as d:
            try:
                tc.R, tc.HB = d, os.path.join(d, "ht_crypto_rails_heartbeat.json")
                tc.rails_heartbeat(True, 5, ["SOL/USD"], {"XBT/USD": "HOLD"}, 0)
                hb = json.load(open(tc.HB))
                self.assertEqual((hb["n_open_clips"], hb["quote_fails"], hb["ok"], hb["runner"]), (5, ["SOL/USD"], False, "tfo_crypto.py rails --execute"))
                self.assertFalse(set(hb) & set(bc.LEDGER_HINTS))          # never an 'unclaimed ledger' for book_coverage
                self.assertEqual([f for f in os.listdir(d) if f.endswith(".tmp")], [])
                tc.HB = os.path.join(d, "missing_dir", "hb.json"); tc.R = os.path.join(d, "missing_dir")
                tc.rails_heartbeat(True, 0, [], {}, 0)                    # write failure is swallowed, never raises
            finally:
                tc.R, tc.HB = old

class HeartbeatFollowsR(unittest.TestCase):
    def test_redirecting_only_R_keeps_live_heartbeat_untouched(self):
        tc = importlib.import_module("tfo_crypto")
        live = tc._HB_DEFAULT; before = os.path.getmtime(live) if os.path.exists(live) else None
        old = (tc.R, tc.HB)
        with tempfile.TemporaryDirectory() as d:
            try:
                tc.R = d
                tc.rails_heartbeat(True, 1, [], {"X/USD": "HOLD"}, 0)
                self.assertTrue(os.path.exists(os.path.join(d, "ht_crypto_rails_heartbeat.json")))
            finally:
                tc.R, tc.HB = old
        self.assertEqual(before, os.path.getmtime(live) if os.path.exists(live) else None)

if __name__ == "__main__":
    unittest.main()
