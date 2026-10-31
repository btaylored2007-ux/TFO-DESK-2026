"""Brett 2026-10-27 21:18 WITA: run_scan.py - each US row sets its own direction (trend + RS + catalyst); SPY regime is a
rank penalty only, never a block. Kraken keeps the venue regime. No network (pure functions only)."""
import os, sys, importlib.util, unittest
import pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_rs = os.path.join(ROOT, "state", "calendar", "ranker", "run_scan.py")
if not os.path.exists(_rs): _rs = os.path.join(ROOT, "staged_fixes", "tforul", "ranker", "run_scan.py")
_spec = importlib.util.spec_from_file_location("run_scan_rowdir", _rs)
RS = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(RS)
def row(sym="AAA", venue="ibkr", px=110, m50=100, stack=3, mstack=0, rs20=5.0, score=3.0, short_score=1.0):
    return dict(sym=sym, venue=venue, px=px, m50=m50, stack=stack, rs20=rs20, score=score, short_score=short_score, _mirror=dict(stack=mstack))
class RowDirection(unittest.TestCase):
    def test_uptrend_strong_rs_is_long(self):
        self.assertEqual(RS.us_direction(row())[0], "long")
    def test_downtrend_weak_rs_is_short(self):
        self.assertEqual(RS.us_direction(row(px=90, stack=0, mstack=3, rs20=-4))[0], "short")
    def test_catalyst_breaks_trend_rs_conflict(self):
        r = row(rs20=-4)                                          # trend +1, rs -1
        self.assertEqual(RS.us_direction(r, cat_score=0.8)[0], "long")
        self.assertEqual(RS.us_direction(r, cat_score=-0.8)[0], "short")
        self.assertEqual(RS.us_direction(dict(r, score=0.5, short_score=2.0))[0], "short")   # tie -> higher own score
        self.assertIn("tie", RS.us_direction(dict(r, score=0.5, short_score=2.0))[1])
    def test_small_rs_and_cat_do_not_vote(self):
        d, b = RS.us_direction(row(rs20=0.5), cat_score=0.1); self.assertEqual((d, b), ("long", "trend+1 rs+0 cat+0"))
    def test_regime_is_rank_input_not_block(self):
        REG = {"ibkr": "short", "kraken": "short"}; pen = RS.CFG["direction"]["counter_regime_rank_penalty"]
        R = pd.DataFrame([row("UP"), row("DN", px=90, stack=0, mstack=3, rs20=-4, score=1.0, short_score=3.0), row("XBT/USD", venue="kraken")])
        R = RS.set_directions(R, REG).set_index("sym")
        self.assertEqual(R.loc["UP", "dir"], "long"); self.assertTrue(R.loc["UP", "counter_regime"])        # long allowed in a short regime
        self.assertAlmostEqual(R.loc["UP", "rank_score"], 3.0 - pen)                                           # ... but ranked lower
        self.assertEqual(R.loc["DN", "dir"], "short"); self.assertFalse(R.loc["DN", "counter_regime"]); self.assertAlmostEqual(R.loc["DN", "rank_score"], 3.0)
        self.assertEqual(R.loc["XBT/USD", "dir"], "short"); self.assertFalse(R.loc["XBT/USD", "counter_regime"])   # crypto: venue regime unchanged
        self.assertGreater(pen, 0)
    def test_config_keeps_risk_and_caps(self):
        self.assertEqual(RS.CFG["max_open"], 3); self.assertEqual(RS.CFG["max_stop_pct"], 5.0); self.assertTrue(RS.CFG["no_short_if_squeeze_flag"])
        self.assertIn("RANK input only", RS.CFG["regime"]["rule"])
if __name__ == "__main__": unittest.main()
