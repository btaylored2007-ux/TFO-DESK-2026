#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import book_coverage as coverage  # noqa: E402
import maker_calibration_probe as maker  # noqa: E402


class TestMakerCoverageDenominator(unittest.TestCase):
    def _load(self, payload):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(payload, f)
            path = f.name
        try:
            return coverage._load_maker_probe("maker", "Maker", path)
        finally:
            os.unlink(path)

    def test_registered_book_is_start_not_current_equity(self):
        book = self._load({
            "params": {"book_usd": 10_000.0},
            "equity": 9_617.441,
            "marked_equity": 9_617.44,
            "position": None,
        })
        self.assertEqual(book["start_usd"], 10_000.0)
        self.assertEqual(book["equity_usd"], 9_617.44)

    def test_missing_registered_book_never_uses_current_equity_as_start(self):
        book = self._load({"equity": 9_617.441,
                           "marked_equity": 9_617.44,
                           "position": None})
        self.assertIsNone(book["start_usd"])
        self.assertEqual(book["equity_usd"], 9_617.44)
        self.assertTrue(any("start unknown" in x for x in book["warnings"]))

    def test_native_long_and_short_match_probe_mark_and_exit_economics(self):
        for direction in (1, -1):
            with self.subTest(direction=direction):
                state = {"params": {"book_usd": 10_000.0}, "equity": 9_200.0,
                         "peak": 10_000.0, "max_dd": 0.0,
                         "baseline": {"units": 0.1, "peak": 10_000.0,
                                      "max_dd": 0.0}}
                maker.apply_fill(state, {"purpose": "entry", "direction": direction,
                                         "price": 80_000.0}, "2026-09-20T00:00:00Z")
                maker.mark_book(state, [{"close": 88_000.0}])
                book = self._load(state)
                self.assertIsNone(book["error"])
                pos = book["positions"][0]
                expected_units = 9_200.0 * (1.0 - maker.MAKER_FEE) / 80_000.0
                self.assertEqual(pos["side"], "long" if direction > 0 else "short")
                self.assertEqual(pos["ticker"], "BTC-USD")
                self.assertAlmostEqual(pos["units"], expected_units)
                self.assertAlmostEqual(pos["fallback_usd"],
                                       direction * expected_units * 88_000.0, delta=0.005)
                # A $100 move changes the native exit equity by Q*$100*side,
                # less the registered exit fee: independently verify delta.
                outcomes = []
                for exit_px in (88_000.0, 88_100.0):
                    clone = json.loads(json.dumps(state))
                    maker.apply_fill(clone, {"purpose": "exit", "price": exit_px},
                                     "2026-09-20T01:00:00Z")
                    outcomes.append(clone["equity"])
                self.assertAlmostEqual(outcomes[1] - outcomes[0],
                                       pos["sign"] * pos["units"] * 100.0
                                       * (1.0 - maker.MAKER_FEE))

    def test_legacy_explicit_long_and_short_positions_remain_supported(self):
        for side, field, price in [("long", "mark", 81_000.0),
                                   ("short", "entry", 80_000.0)]:
            with self.subTest(side=side):
                book = self._load({"params": {"book_usd": 10_000.0},
                                   "marked_equity": 10_000.0,
                                   "position": {"side": side, "units": 0.1,
                                                field: price, "sym": "XBT/USD"}})
                self.assertIsNone(book["error"])
                pos = book["positions"][0]
                self.assertEqual(pos["fallback_usd"],
                                 (1 if side == "long" else -1) * 0.1 * price)

    def test_unmeasured_open_positions_fail_loudly_instead_of_zero_exposure(self):
        valid = {"entry_px": 80_000.0, "entry_equity": 9_200.0, "direction": 1}
        bad_positions = [
            ["invalid"],
            {**valid, "entry_px": 0},
            {**valid, "entry_equity": float("nan")},
            {**valid, "direction": True},
            {**valid, "direction": 0},
            {"side": "long", "mark": 80_000.0},
            {"units": 0.1, "mark": 80_000.0},
            {"side": "long", "units": 0.1, "mark": -1, "entry": 80_000.0},
        ]
        for pos in bad_positions:
            with self.subTest(position=pos):
                book = self._load({"params": {"book_usd": 10_000.0},
                                   "marked_equity": 9_200.0, "position": pos})
                self.assertIn("UNREADABLE", book["error"])
                self.assertEqual(book["positions"], [])
        for marked in (None, float("nan"), float("inf"), True):
            with self.subTest(marked_equity=marked):
                book = self._load({"params": {"book_usd": 10_000.0},
                                   "marked_equity": marked, "position": valid})
                self.assertIn("UNREADABLE", book["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
