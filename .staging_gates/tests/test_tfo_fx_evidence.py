"""Timestamped FX and minor-unit execution guards, without network or ledgers."""
import unittest
from unittest.mock import patch

from test_tfo_equity_refresh import refresh, chart, quote, NOW, EPOCH
import fundamental_book as book


class FXEvidenceTests(unittest.TestCase):
    def test_pence_conversion_uses_same_timestamp_and_exact_scale(self):
        raw = chart(bars=[(EPOCH - 60, 1.34)])
        pound = refresh.parse_fx_chart("GBP", raw, NOW)
        for ccy in ("GBp", "GBX", "gbp"):
            pence = refresh.parse_fx_chart(ccy, raw, NOW)
            self.assertAlmostEqual(pence["rate"], pound["rate"] / 100)
            self.assertEqual(pence["quote_utc"], pound["quote_utc"])
            self.assertEqual(pence["status"], "current")

    def test_metadata_without_bar_cannot_supply_fx(self):
        with self.assertRaisesRegex(ValueError, "no timestamped FX bar"):
            refresh.parse_fx_chart("EUR", chart(), NOW)

    def test_stale_bar_is_valuation_only(self):
        q = refresh.parse_fx_chart("EUR", chart(bars=[(EPOCH - 121, 1.1)]), NOW)
        self.assertEqual(q["status"], "stale")

    def test_invalid_future_and_wrong_currency_are_rejected(self):
        for raw in (chart(bars=[(EPOCH + 1, 1.1)]),
                    chart(bars=[(EPOCH - 1, float("nan"))]),
                    chart(ccy="EUR", bars=[(EPOCH - 1, 1.1)])):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                refresh.parse_fx_chart("EUR", raw, NOW)

    def test_collection_requires_fx_evidence_for_execution(self):
        for status, executable in (("current", True), ("stale", False),
                                   ("source_time_unknown", False)):
            with self.subTest(status=status):
                evidence = {"rate": .0134, "status": status, "quote_utc": NOW}
                quotes, _ = refresh.collect_quotes(
                    ["TEST.L"], lambda _: quote(100, "GBp"),
                    lambda _: ({"GBp": evidence}, {}))
                q = quotes["TEST.L"]
                self.assertAlmostEqual(q["px_usd"], 1.34)
                self.assertEqual(q["executable"], executable)
                self.assertEqual(q["fx_quote_utc"], NOW)

    def test_legacy_scalar_fx_never_certifies_execution(self):
        quotes, _ = refresh.collect_quotes(
            ["TEST.L"], lambda _: quote(100, "GBp"),
            lambda _: ({"GBp": .0134}, {}))
        self.assertFalse(quotes["TEST.L"]["executable"])

    def test_missing_fx_keeps_price_but_refuses_conversion(self):
        quotes, errors = refresh.collect_quotes(
            ["TEST.L"], lambda _: quote(100, "GBp"),
            lambda _: ({}, {"GBp": "offline"}))
        self.assertIsNone(quotes["TEST.L"]["px_usd"])
        self.assertFalse(quotes["TEST.L"]["executable"])
        self.assertEqual(errors["TEST.L"], "offline")

    def test_entry_and_exit_consumers_receive_fx_guard(self):
        q = quote(100, "GBp")
        q.update(executable=False, fx_status="stale", fx_quote_utc=NOW)
        with patch.object(refresh, "collect_quotes", return_value=({"TEST.L": q}, {})):
            prices, errors = book.fetch_prices(["TEST.L"])
            self.assertFalse(prices)
            self.assertIn("FX stale", errors["TEST.L"])
            prices, _ = book.fetch_prices(["TEST.L"], require_executable=False)
            meta = book.quote_metadata(prices["TEST.L"])
            self.assertTrue(meta["mark_stale"])
            self.assertEqual(meta["mark_fx_status"], "stale")


if __name__ == "__main__":
    unittest.main()
