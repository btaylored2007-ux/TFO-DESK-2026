import json
import os
import tempfile
import unittest
from unittest.mock import patch

import book_coverage as coverage


class WeightBookCoverageTests(unittest.TestCase):
    def load(self, **updates):
        payload = {"equity": 1000.0, "cash": 500.0,
                   "holdings": {"BNP.PA": 5.0}, "entry_px": {"BNP.PA": 80.0},
                   "holding_ccy": {"BNP.PA": "EUR"},
                   "fx_usd_per_unit": {"BNP.PA": 1.25},
                   "mark_px_usd": {"BNP.PA": 110.0}}
        payload.update(updates)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "book.json")
            with open(path, "w") as f:
                json.dump(payload, f)
            return coverage._load_weights_book("value", "Value", "book", path)

    def test_refused_quote_does_not_crash_registry_or_reuse_last_good_equity(self):
        book = self.load(equity=None, equity_status="UNAVAILABLE: BNP.PA stale",
                         mark_px_usd={"BNP.PA": None}, fx_usd_per_unit={"BNP.PA": None},
                         last_valued_equity=999.0)
        self.assertIsNone(book["equity_usd"])
        self.assertIn("BNP.PA stale", book["error"])
        with patch.object(coverage, "registry", return_value=[book]):
            result = coverage.gather(verbose=False)
        self.assertEqual(result["n_books_ok"], 0)
        self.assertEqual(len(result["errors"]), 1)

    def test_finite_mark_and_listing_fx_are_preserved(self):
        book = self.load()
        self.assertIsNone(book["error"])
        self.assertEqual(book["positions"][0]["fallback_usd"], 550.0)
        self.assertEqual(book["positions"][0]["mark_fx_usd"], 1.25)

    def test_null_mark_with_valid_fx_uses_declared_entry_cost(self):
        book = self.load(mark_px_usd={"BNP.PA": None})
        self.assertIsNone(book["error"])
        self.assertEqual(book["positions"][0]["fallback_usd"], 500.0)
        self.assertIn("entry cost", book["positions"][0]["fallback_basis"])
        self.assertTrue(book["warnings"])

    def test_missing_or_invalid_foreign_fx_never_defaults_to_usd(self):
        for fx in ({}, {"BNP.PA": None}, {"BNP.PA": 0}, {"BNP.PA": float("nan")}):
            with self.subTest(fx=fx):
                self.assertIn("USD rate", self.load(fx_usd_per_unit=fx)["error"])

    def test_no_price_or_cost_is_unreadable_not_zero(self):
        book = self.load(mark_px_usd={"BNP.PA": None}, entry_px={})
        self.assertIn("neither a usable mark nor entry cost", book["error"])

    def test_nonfinite_equity_is_unreadable(self):
        self.assertIsNotNone(self.load(equity=float("nan"))["error"])

    def test_usd_legacy_books_without_fx_metadata_remain_supported(self):
        book = self.load(holdings={"AAPL": 5}, entry_px={"AAPL": 100},
                         holding_ccy={}, fx_usd_per_unit={}, mark_px_usd={})
        self.assertIsNone(book["error"])
        self.assertEqual(book["positions"][0]["fallback_usd"], 500.0)

    def dated(self, ccy, fx):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "dated.json")
            with open(path, "w") as f:
                json.dump({"seed_usd": 1000, "cash": 500, "positions": {"DATA.L": {
                    "units": 500, "mark_usd": 1, "entry_ccy": ccy, "entry_fx_usd": fx}}}, f)
            return coverage._load_dated_event("dated", "Dated", path)

    def test_dated_event_pence_uses_quote_unit_fx_not_one(self):
        book = self.dated("GBp", 0.0135)
        self.assertEqual(book["positions"][0]["mark_fx_usd"], 0.0135)
        self.assertEqual(book["positions"][0]["fallback_usd"], 500)
        self.assertEqual(book["equity_usd"], 1000)

    def test_dated_foreign_missing_fx_stays_explicitly_unremarkable(self):
        book = self.dated("GBp", None)
        self.assertIsNone(book["positions"][0]["mark_fx_usd"])
        self.assertTrue(book["warnings"])
        self.assertEqual(book["positions"][0]["fallback_usd"], 500)

    def test_dated_usd_needs_no_conversion(self):
        self.assertEqual(self.dated("USD", None)["positions"][0]["mark_fx_usd"], 1.0)


if __name__ == "__main__":
    unittest.main()
