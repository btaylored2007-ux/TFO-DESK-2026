import contextlib
import json
import math
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import book_kill
import carry_paper_desk as carry


class CarrySafetyTests(unittest.TestCase):
    def test_malformed_or_missing_snapshot_never_blocks_registered_exit(self):
        for snapshot in (None, {"error": "missing"}, {"assets": [None]},
                         {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                          "preferred_verdict": "HARVESTABLE", "assets": [
                              {"base": "BTC", "annualised_pct": float("nan"), "age_sec": "bad"}]}):
            with tempfile.TemporaryDirectory() as td:
                book = Path(td) / "book.json"; log = Path(td) / "ledger.jsonl"
                book.write_text(json.dumps({"positions": {"BTC": {
                    "notional_aud": 100, "funding_accrued_aud": 1, "entry_cost_aud": .25,
                    "opened_epoch": time.time()-86400, "last_accrual_epoch": time.time()+3600,
                    "opened_at": "2026-08-21"}}, "funding_accrued_aud": 1,
                    "costs_paid_aud": .25, "realized_aud": 0}))
                with patch.multiple(carry, BOOK_FILE=str(book), LEDGER_FILE=str(log)), \
                     patch.object(carry.desk_lock, "ledger_lock", return_value=contextlib.nullcontext()), \
                     patch.object(carry, "monitor_snapshot", return_value=snapshot), \
                     patch.object(carry, "trailing_mean_carry", return_value=(-1, 6)), \
                     patch.object(book_kill, "firm_entries_allowed", return_value=(False, "halt")):
                    carry.tick(False)
                result = json.loads(book.read_text())
                self.assertEqual(result["positions"], {})
                self.assertEqual(result["realized_aud"], .5)
                self.assertEqual(result["accounting_status"]["historical_funding_reconciliation"], "REQUIRED")

    def test_elapsed_hour_accrual_and_invalid_input(self):
        self.assertAlmostEqual(carry.funding_accrual(33000, 10, 24), 3300 / 365)
        self.assertAlmostEqual(carry.funding_accrual(33000, -10, 24), -3300 / 365)
        for bad in (-1, float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                carry.funding_accrual(33000, 10, bad)

    def test_remainder_never_reallocates_full_cap(self):
        self.assertEqual(carry.remaining_notional({"BTC": {"notional_aud": 33000}}), 0)
        self.assertEqual(carry.remaining_notional({"BTC": {"notional_aud": 20000}}), 13000)
        self.assertEqual(carry.remaining_notional({"BTC": {"notional_aud": 66000}}), 0)
        with self.assertRaises(ValueError):
            carry.remaining_notional({"BTC": {"notional_aud": float("nan")}})

    def test_row_requires_correct_units_finite_fresh_price(self):
        good = {"annualised_pct": 20, "age_sec": 1, "mark": 100, "liquid": True}
        self.assertTrue(carry.valid_entry_row(good))
        for key, val in (("annualised_pct", 3), ("mark", math.inf), ("mark", 0),
                         ("age_sec", -1), ("age_sec", math.nan), ("liquid", False)):
            self.assertFalse(carry.valid_entry_row(good | {key: val}))

    def test_staggered_admission_keeps_total_cap_and_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            book = Path(td) / "book.json"
            log = Path(td) / "ledger.jsonl"
            def snap(btc, eth):
                return {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "preferred_verdict": "HARVESTABLE", "assets": [
                    {"base": "BTC", "annualised_pct": btc, "age_sec": 0, "mark": 100, "liquid": True},
                    {"base": "ETH", "annualised_pct": eth, "age_sec": 0, "mark": 100, "liquid": True}]}
            with patch.multiple(carry, BOOK_FILE=str(book), LEDGER_FILE=str(log)), \
                 patch.object(carry.desk_lock, "ledger_lock", return_value=contextlib.nullcontext()), \
                 patch.object(carry, "trailing_mean_carry", return_value=(1, 10)), \
                 patch.object(book_kill, "firm_entries_allowed", return_value=(True, "")):
                with patch.object(carry, "monitor_snapshot", return_value=snap(60, 10)):
                    carry.tick(False)
                with patch.object(carry, "monitor_snapshot", return_value=snap(60, 30)):
                    carry.tick(False); carry.tick(False)
            d = json.loads(book.read_text())
            self.assertEqual(sum(p["notional_aud"] for p in d["positions"].values()), 33000)
            self.assertEqual(set(d["positions"]), {"BTC"})
            self.assertEqual(sum(json.loads(line)["event"] == "OPEN" for line in log.read_text().splitlines()), 1)

    def test_corrupt_book_is_not_reinitialised(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "book.json"; p.write_text("invalid")
            with patch.object(carry, "BOOK_FILE", str(p)), self.assertRaises(ValueError):
                carry.load_book()
            self.assertEqual(p.read_text(), "invalid")


class FirmGateSafetyTests(unittest.TestCase):
    def test_missing_invalid_stale_future_refuse(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "risk.json"
            with patch.object(book_kill, "FIRM_STATE", str(p)):
                self.assertTrue(book_kill.firm_gate(False)[0])
                for d in ({}, {"epoch": time.time(), "new_entries_halted": "false"},
                          {"epoch": time.time(), "new_entries_halted": False, "level": "UNMEASURED", "drawdown_valid": False},
                          {"epoch": time.time(), "new_entries_halted": False, "level": "OK", "books_readable": 0, "books_total": 35},
                          {"epoch": float("nan"), "new_entries_halted": False},
                          {"epoch": time.time()-7*3600, "new_entries_halted": False},
                          {"epoch": time.time()+3600, "new_entries_halted": False}):
                    p.write_text(json.dumps(d)); self.assertTrue(book_kill.firm_gate(False)[0])
                p.write_text(json.dumps({"epoch": time.time(), "new_entries_halted": False, "level": "OK"}))
                self.assertFalse(book_kill.firm_gate(False)[0])


if __name__ == "__main__":
    unittest.main()
