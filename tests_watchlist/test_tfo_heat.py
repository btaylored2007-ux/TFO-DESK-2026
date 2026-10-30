#!/usr/bin/env python3
"""HT decision 2026-09-26 #2: heat at live stops. Pure; no I/O."""
import os, sys, unittest
DESK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DESK)
import tfo_heat as th

class Heat(unittest.TestCase):
    def test_pre_half_unchanged_5pct_of_entry_notional(self):
        self.assertAlmostEqual(th.crypto_position_heat({"qty": 10, "entry_px": 100.0}, 120.0), 50.0)
        self.assertAlmostEqual(th.equity_clip_heat({"ht_clip": True, "notional_usd": 200000.0, "units": 1000, "entry_px_usd": 200.0}), 10000.0)

    def test_banked_crypto_floored_at_breakeven_charges_1pct_current_notional(self):
        p = {"qty": 500, "entry_px": 100.0, "orig_entry": 100.0, "rebase": 5000.0, "trail": {"half_banked": True, "level_usd": 104.0}}
        self.assertAlmostEqual(th.crypto_position_heat(p, 110.0), 0.01 * 500 * 110.0)
        p["trail"]["level_usd"] = 100.0                                  # exactly breakeven still floored
        self.assertAlmostEqual(th.crypto_position_heat(p, 110.0), 550.0)
        p["trail"].pop("level_usd")                                       # no level yet: variant C floor = entry
        self.assertAlmostEqual(th.crypto_position_heat(p, 110.0), 550.0)

    def test_banked_below_breakeven_charges_actual_loss_at_live_stop(self):
        p = {"qty": 500, "entry_px": 100.0, "orig_entry": 100.0, "trail": {"half_banked": True, "level_usd": 97.0}}
        self.assertAlmostEqual(th.crypto_position_heat(p, 99.0), 1500.0)  # 500 * (100-97) > 1% buffer 495

    def test_equity_residual_long_and_short(self):
        base = {"ht_clip": True, "units": 1000, "entry_px_usd": 50.0, "rail_orig_entry_usd": 50.0, "notional_usd": 50000.0,
                "rail_half_count": 1, "mark_usd": 55.0, "rail_trail": {"half_banked": True, "level_usd": 51.0}}
        self.assertAlmostEqual(th.equity_clip_heat(base), 550.0)
        s = dict(base, side="short", mark_usd=45.0, rail_trail={"half_banked": True, "level_usd": 49.0})
        self.assertAlmostEqual(th.equity_clip_heat(s), 450.0)
        s["rail_trail"] = {"half_banked": True, "level_usd": 52.0}        # short stop above entry = real loss
        self.assertAlmostEqual(th.equity_clip_heat(s), 2000.0)

    def test_book_total_and_legacy_basis(self):
        fund = {"XLE": {"ht_clip": True, "notional_usd": 100000.0, "units": 1000, "entry_px_usd": 100.0},
                "OLD": {"notional_usd": 999999.0}}                        # non-HT positions never count
        cb = {"positions": {"SOL/USD": {"qty": 1000, "entry_px": 50.0, "orig_entry": 100.0, "rebase": 1.0,
                                        "trail": {"half_banked": True, "level_usd": 100.0}},
                            "XBT/USD": {"qty": 1, "entry_px": 100000.0}},
              "mark_px_usd": {"SOL/USD": 110.0}}
        total, rows = th.clip_heat(fund, cb)
        self.assertAlmostEqual(total, 5000.0 + 1100.0 + 5000.0)
        self.assertEqual(len(rows), 3)
        self.assertAlmostEqual(th.legacy_flat_heat(fund, cb), 0.05 * (100000 + 50000 + 100000))

if __name__ == "__main__":
    unittest.main()
