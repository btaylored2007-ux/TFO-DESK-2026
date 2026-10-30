"""Offline tests for the V1 rail bad-tick guard (8766.T incident 2026-09-29). No network, no live ledgers.

A quote implying a >30% one-print move vs the last good mark, or an adverse >30% move vs the original fill,
must never fill a rail (the row is BLOCKED and execute() refuses it). Legit moves still fill.
"""
import os, sys, unittest
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_tfo_trail_rail as base
import tfo_ticket as tt


class PureGuard(unittest.TestCase):
    def test_8766_unapplied_split_is_bad_tick_even_when_mark_is_contaminated(self):
        # entry $51.248 (JPY 8081 pre-split); marker already wrote the post-split $3.353 into mark_usd
        why = tt.bad_tick(3.353335, "long", 3.353335, 51.24806)
        self.assertIsNotNone(why); self.assertIn("original fill", why); self.assertIn("15-for-1", why)

    def test_mark_leg_both_directions(self):
        self.assertIn("last good mark", tt.bad_tick(64.0, "long", 100.0, 60.0))        # -36% vs mark
        self.assertIn("last good mark", tt.bad_tick(140.0, "long", 100.0, 100.0))      # +40% vs mark
        self.assertIsNone(tt.bad_tick(71.0, "long", 100.0, 100.0))                    # -29%: plausible, rails decide

    def test_entry_leg_is_adverse_only(self):
        self.assertIsNone(tt.bad_tick(142.0, "long", 140.0, 100.0))                   # +42% winner exits freely
        self.assertIsNotNone(tt.bad_tick(135.0, "short", 134.0, 100.0))               # short: +35% adverse
        self.assertIsNone(tt.bad_tick(60.0, "short", 61.0, 100.0))                    # short winner
        self.assertIsNotNone(tt.bad_tick(0.0, "long", 1.0, 1.0))

    def test_split_hint(self):
        self.assertIn("5-for-1", tt.split_hint(20.1, 100.0))
        self.assertIn("reverse split", tt.split_hint(1000.0, 100.0))
        self.assertEqual(tt.split_hint(71.0, 100.0), "")


class GuardEngine(base.EquityEngine):
    def test_bad_tick_blocks_stop_and_writes_nothing(self):
        self.book(JPX=self.pos(entry_px_usd=51.248, rail_orig_entry_usd=51.248, notional_usd=5124.8, mark_usd=3.353)); self.px["JPX"] = 3.353
        rows = self.tt.scan()
        self.assertEqual((rows[0]["rail"], rows[0]["status"]), ("STOP", "BLOCKED"))
        self.assertTrue(rows[0]["bad_tick"]); self.assertIn("bad tick", rows[0]["why"])
        self.run_cycle()
        d = self.r("fundamental_book")
        self.assertIn("JPX", d["positions"]); self.assertEqual(d["positions"]["JPX"]["units"], 100.0)
        self.assertNotIn("head_trader_tickets", d)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "log.txt")))

    def test_execute_refuses_bad_tick_even_if_row_was_ready(self):
        self.book(JPY=self.pos(entry_px_usd=51.248, rail_orig_entry_usd=51.248, mark_usd=3.353)); self.px["JPY"] = 3.353
        row = self.tt.scan()[0]; row.update(status="READY")
        with patch("builtins.print"):
            self.assertIsNone(self.tt.execute(row))
        self.assertIn("JPY", self.r("fundamental_book")["positions"])

    def test_real_minus_six_stop_still_fills(self):
        self.book(RL=self.pos()); self.px["RL"] = 94.0
        self.run_cycle()
        d = self.r("fundamental_book")
        self.assertNotIn("RL", d["positions"]); self.assertEqual(d["head_trader_tickets"][-1]["action"], "STOP")

    def test_speculative_sleeve_keeps_only_the_2_5x_check(self):
        self.book()
        self.w("fundamental_spec_book", {"positions": {"SPC": self.pos(mark_usd=62.0)}, "cash": 0.0, "seed_usd": 0.0}); self.px["SPC"] = 60.0
        rows = [r for r in self.tt.scan() if r["sym"] == "SPC"]
        self.assertEqual((rows[0]["rail"], rows[0]["status"]), ("STOP", "READY"))


for _n in dir(base.EquityEngine):   # run only this file's engine tests here (the base suite runs in its own file)
    if _n.startswith("test_") and _n not in GuardEngine.__dict__: setattr(GuardEngine, _n, None)


if __name__ == "__main__":
    unittest.main()
