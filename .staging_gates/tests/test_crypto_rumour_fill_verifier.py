#!/usr/bin/env python3
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import crypto_rumour_fill_verifier as verifier


class CryptoRumourReceiptTests(unittest.TestCase):
    def setUp(self):
        self.pair = {
            "band": "top50",
            "symbol": "TESTUSDT",
            "limit": 100.0,
            "posted_utc": "2026-09-02T00:00:30Z",
            "fill_utc": "2026-09-02T00:02:10Z",
            "ticks_to_fill": 2,
        }

    def test_full_minute_is_strictly_after_even_at_boundary(self):
        boundary = verifier.parse_utc("2026-09-02T00:00:00Z")
        self.assertEqual(
            verifier.full_minute_after(boundary),
            verifier.parse_utc("2026-09-02T00:01:00Z"),
        )

    def test_exact_receipt_records_first_qualifying_print(self):
        post = verifier.parse_utc(self.pair["posted_utc"])
        trades = [
            {"a": 1, "T": post, "p": "90.00"},  # not after posting
            {"a": 2, "T": post + 1, "p": "100.00"},
            {"a": 3, "T": post + 2, "p": "99.98"},
            {"a": 4, "T": post + 3, "p": "99.00"},
        ]
        result = verifier.evaluate_aggregate_trades(self.pair, trades)
        self.assertEqual(result["status"], "VERIFIED_EXACT_AGGTRADE_STRICT_THROUGH")
        self.assertFalse(result["quarantined"])
        self.assertEqual(result["first_qualifying_aggregate_trade_id"], 3)
        self.assertEqual(result["first_qualifying_trade_price"], 99.98)
        self.assertEqual(result["verification_method"], "EXACT_BINANCE_AGGREGATE_TRADES")

    def test_threshold_touch_is_not_strict_through(self):
        post = verifier.parse_utc(self.pair["posted_utc"])
        threshold = self.pair["limit"] * (1.0 - verifier.STRICT_THROUGH_BUFFER)
        result = verifier.evaluate_aggregate_trades(
            self.pair,
            [{"a": 1, "T": post + 1, "p": str(threshold)}],
        )
        self.assertTrue(result["quarantined"])

    def test_exact_no_cross_does_not_fall_back_to_candles(self):
        with mock.patch.object(verifier, "fetch_aggregate_trades", return_value=[]), mock.patch.object(
            verifier, "fetch_klines"
        ) as candle_fetch:
            result = verifier.verify_pair(self.pair)
        self.assertTrue(result["quarantined"])
        self.assertEqual(result["verification_method"], "EXACT_BINANCE_AGGREGATE_TRADES")
        candle_fetch.assert_not_called()

    def test_candle_proof_is_only_a_labelled_exact_tape_failure_fallback(self):
        rows = [[
            verifier.parse_utc("2026-09-02T00:01:00Z"),
            "", "", "99.98", "", "",
            verifier.parse_utc("2026-09-02T00:01:59.999Z"),
        ]]
        with mock.patch.object(
            verifier, "fetch_aggregate_trades", side_effect=OSError("tape unavailable")
        ), mock.patch.object(verifier, "fetch_klines", return_value=rows):
            result = verifier.verify_pair(self.pair)
        self.assertEqual(result["status"], "VERIFIED_BOUNDED_1M_CANDLE_STRICT_THROUGH")
        self.assertFalse(result["fallback_is_exact_print"])
        self.assertNotIn("first_qualifying_trade_utc", result)

    def test_posting_minute_cross_is_ambiguous_and_quarantined(self):
        rows = [[
            verifier.parse_utc("2026-09-02T00:00:00Z"),
            "", "", "90.00", "", "",
            verifier.parse_utc("2026-09-02T00:00:59.999Z"),
        ]]
        result = verifier.evaluate_candles(self.pair, rows, "exact unavailable")
        self.assertTrue(result["quarantined"])
        self.assertTrue(result["posting_minute_cross_is_ambiguous"])

    def test_post_fill_candle_cannot_prove_fill(self):
        rows = [[
            verifier.parse_utc("2026-09-02T00:02:00Z"),
            "", "", "90.00", "", "",
            verifier.parse_utc("2026-09-02T00:02:59.999Z"),
        ]]
        result = verifier.evaluate_candles(self.pair, rows, "exact unavailable")
        self.assertTrue(result["quarantined"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
