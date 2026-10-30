#!/usr/bin/env python3
"""HT decision 2026-09-26 #1: gap_close_levered_book.tick() must not clobber a powder transfer that lands during
its price download. Offline (fake fetch), temp dir only; never touches the live powder ledger."""
import fcntl, importlib, json, os, sys, tempfile, unittest
DESK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DESK)
import pandas as pd

class PowderLock(unittest.TestCase):
    def setUp(self):
        self.g = importlib.import_module("gap_close_levered_book"); self.dl = importlib.import_module("desk_lock")
        self.old = (self.g.BOOK, self.g.fetch, self.g.save, self.dl.LOCKDIR)
        self.d = tempfile.TemporaryDirectory(); d = self.d.name
        self.g.BOOK = os.path.join(d, "gap_close_levered_book.json"); self.dl.LOCKDIR = d
        json.dump({"cash": 100000.0, "reserved_usd": 0, "holdings": {}, "peak_equity": 100000.0, "capital_events": []}, open(self.g.BOOK, "w"))
    def tearDown(self):
        self.g.BOOK, self.g.fetch, self.g.save, self.dl.LOCKDIR = self.old; self.d.cleanup()

    def _lock_free(self):
        with open(os.path.join(self.d.name, ".%s.lock" % self.g.POWDER_LOCK), "a+") as fh:
            try: fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError: return False
            fcntl.flock(fh, fcntl.LOCK_UN); return True

    def test_transfer_during_download_survives_and_save_is_locked(self):
        seen = {}
        def fake_fetch():
            seen["lock_free_during_fetch"] = self._lock_free()      # download happens OUTSIDE the lock
            b = json.load(open(self.g.BOOK))                          # an HT tool moves powder mid-download
            b["cash"] -= 25000.0; b["capital_events"].append({"type": "capital_transfer", "usd": -25000.0})
            json.dump(b, open(self.g.BOOK, "w"))
            return pd.DataFrame({"TQQQ": [50.0, 51.0], "SOXL": [30.0, 31.0]})
        real_save = self.g.save
        def spy_save(b):
            seen["lock_free_during_save"] = self._lock_free(); real_save(b)
        self.g.fetch, self.g.save = fake_fetch, spy_save
        self.assertEqual(self.g.tick(), 0)
        b = json.load(open(self.g.BOOK))
        self.assertEqual(b["cash"], 75000.0)                           # transfer not overwritten
        self.assertEqual(len(b["capital_events"]), 1)
        self.assertEqual((b["equity"], b["peak_equity"]), (75000.0, 100000.0))
        self.assertEqual(b["drawdown_pct"], -25.0)
        self.assertTrue(seen["lock_free_during_fetch"]); self.assertFalse(seen["lock_free_during_save"])
        self.assertTrue(self._lock_free())                             # released afterwards

    def test_kill_switch_still_applies(self):
        self.g.fetch = lambda: pd.DataFrame({"TQQQ": [10.0]})
        b = json.load(open(self.g.BOOK)); b["cash"] = 60000.0; json.dump(b, open(self.g.BOOK, "w"))
        self.g.tick(); b = json.load(open(self.g.BOOK))
        self.assertTrue(b["frozen"]); self.assertEqual(b["frozen_reason"], "sleeve kill -35%")

if __name__ == "__main__":
    unittest.main()
