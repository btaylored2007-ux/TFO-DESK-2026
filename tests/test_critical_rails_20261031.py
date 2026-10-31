"""Locks for the 2026-10-31 critical rail fixes. Offline. No live ledgers."""
import json, os, sys, tempfile, time, unittest
from unittest.mock import patch
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import book_kill
import cal_paper_bench as CB


def _ok_state(halt=False):
    return {"epoch": time.time(), "new_entries_halted": halt,
            "level": "HALT_NEW_ENTRIES" if halt else "OK",
            "drawdown_valid": True, "capital_accounting_valid": True,
            "headline": "test"}


class LedgerLoadTests(unittest.TestCase):
    def test_missing_ledger_uses_default(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "nope.json")
            self.assertEqual(CB.load(p, {"trades": []}, strict=True), {"trades": []})

    def test_unreadable_ledger_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "ledger.json")
            with open(p, "w") as fh:
                fh.write("{not json")
            with self.assertRaises(CB.LedgerUnreadable):
                CB.load(p, {"trades": []}, strict=True)
            self.assertEqual(open(p).read(), "{not json")


class FirmHaltTests(unittest.TestCase):
    def test_missing_state_does_not_block(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(book_kill, "FIRM_STATE", os.path.join(td, "missing.json")):
                self.assertEqual(book_kill.firm_halt_blocks_entry("tfo_buy"), (False, "ok"))

    def test_published_halt_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "firm.json")
            json.dump(_ok_state(True), open(p, "w"))
            with patch.object(book_kill, "FIRM_STATE", p):
                blocked, why = book_kill.firm_halt_blocks_entry("hunt48_asx")
                self.assertTrue(blocked)
                self.assertIn("FIRM HALT", why)

    def test_clear_state_allows(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "firm.json")
            json.dump(_ok_state(False), open(p, "w"))
            with patch.object(book_kill, "FIRM_STATE", p):
                self.assertEqual(book_kill.firm_halt_blocks_entry("oanda_aggro")[0], False)


class EntriesAllowedTests(unittest.TestCase):
    def _clear(self, td):
        p = os.path.join(td, "firm.json")
        json.dump(_ok_state(False), open(p, "w"))
        return p

    def test_sidecar_frozen_survives_ledger_false(self):
        with tempfile.TemporaryDirectory() as td:
            peaks = os.path.join(td, "peaks.json")
            json.dump({"demo": {"frozen": True, "frozen_reason": "kill rule"}}, open(peaks, "w"))
            with patch.object(book_kill, "PEAKS", peaks), patch.object(book_kill, "FIRM_STATE", self._clear(td)):
                ok, why = book_kill.entries_allowed({"book_risk": {"frozen": False}}, "demo", verbose=False)
            self.assertFalse(ok)
            self.assertIn("FROZEN", why)

    def test_unmeasured_book_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            peaks = os.path.join(td, "peaks.json")
            json.dump({}, open(peaks, "w"))
            with patch.object(book_kill, "PEAKS", peaks), patch.object(book_kill, "FIRM_STATE", self._clear(td)):
                ok, why = book_kill.entries_allowed({"book_risk": {"error": "no peak"}}, "x", verbose=False)
            self.assertFalse(ok)
            self.assertIn("UNMEASURED", why)

    def test_firm_halt_refuses_entries(self):
        with tempfile.TemporaryDirectory() as td:
            peaks = os.path.join(td, "peaks.json"); json.dump({}, open(peaks, "w"))
            firm = os.path.join(td, "firm.json"); json.dump(_ok_state(True), open(firm, "w"))
            with patch.object(book_kill, "PEAKS", peaks), patch.object(book_kill, "FIRM_STATE", firm):
                ok, why = book_kill.entries_allowed({}, "demo", verbose=False)
            self.assertFalse(ok)
            self.assertIn("FIRM-WIDE HALT", why)

    def test_refusal_log_is_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "kill.jsonl")
            led = {"book_risk": {"entry_refusals": [{"utc": "old"}] * 250}}
            with patch.object(book_kill, "KILL_LOG", log):
                book_kill.record_refusal(led, "demo", "AAA", "desk", "because")
            self.assertEqual(len(led["book_risk"]["entry_refusals"]), 200)
            self.assertEqual(led["book_risk"]["entry_refusals"][0]["symbol"], "AAA")


class EntryCallSites(unittest.TestCase):
    def test_entry_modules_call_the_shared_halt(self):
        root = ROOT
        need = {
            "tfo_buy.py": "firm_halt_blocks_entry",
            "tfo_crypto.py": "firm_halt_blocks_entry",
            "asx_hunt48.py": "firm_halt_blocks_entry",
            "asia_hunt48.py": "firm_halt_blocks_entry",
            "hunt48_short_paper.py": "firm_halt_blocks_entry",
            "trend18.py": "firm_halt_blocks_entry",
            "trend_core.py": "firm_halt_blocks_entry",
            "trend_core_global.py": "firm_halt_blocks_entry",
            "oanda_aggro.py": "firm_halt_blocks_entry",
        }
        for name, needle in need.items():
            text = open(os.path.join(root, name)).read()
            self.assertIn(needle, text, name)


class TrendWeekTests(unittest.TestCase):
    def test_unchecked_when_a_holding_was_not_priced(self):
        import datetime as dt
        import pandas as pd
        import trend18_trail as tt
        idx = pd.bdate_range("2026-09-01", periods=20)
        hist = {"UPRO": pd.Series([100.0] * 20, index=idx)}
        L = {"holdings": {"UPRO": 10.0, "TQQQ": 10.0}, "entry_px": {"UPRO": 100.0, "TQQQ": 50.0},
             "mark_px_usd": {}, "fills": []}
        S = {"pos": {}, "pending": [], "stopped": [], "exits": [], "last_week_checked": None}
        tt.check(L, S, hist, 0.70, "2026-W45", dt.date(2026, 11, 10))
        self.assertIsNone(S["last_week_checked"])


class LaunchdReconcileTests(unittest.TestCase):
    def test_non_mac_host_does_not_fail(self):
        import tools.launchd_reconcile as lr
        with patch.object(lr, "INSTALLED", os.path.join(tempfile.gettempdir(), "no-such-launchagents-tfo")):
            self.assertEqual(lr.main(), 0)


if __name__ == "__main__":
    unittest.main()
