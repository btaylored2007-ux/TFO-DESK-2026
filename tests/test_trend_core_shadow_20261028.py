"""TREND-CORE SHADOW profit-taking: rules fire in shadow only; real ledger untouched. PAPER."""
import os, sys, json, copy, unittest
import numpy as np, pandas as pd
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import trend_core_shadow as tcs


def L0(px):
    return {"holdings": {"UPRO": 300.0, "DBMF": 100.0}, "mark_px_usd": {"UPRO": px, "DBMF": 30.0},
            "entry_px_usd": {"UPRO": 100.0, "DBMF": 30.0}, "equity_aud": 100000.0,
            "fills": [{"side": "BUY", "utc": "2025-06-02T00:00:00Z"}]}


def series(vals, end="2026-10-23"):
    idx = pd.bdate_range(end=end, periods=len(vals))
    return pd.Series(vals, index=idx)


class T(unittest.TestCase):
    def test_trim_when_stretched(self):
        rng = np.random.default_rng(1)
        base = 100 * np.cumprod(1 + rng.normal(0, 0.01, 300))
        c = series(list(base))
        px = float(c.iloc[-200:].mean()) * 1.6  # very stretched
        L = L0(px); before = copy.deepcopy(L); S = tcs.new_state()
        acts = tcs.evaluate(L, S, {"UPRO": c}, None, 0.7)
        self.assertEqual(L, before)  # real book untouched
        trims = [a for a in acts if a["rule"] == "TRIM_STRETCHED"]
        self.assertEqual(len(trims), 1)
        self.assertAlmostEqual(trims[0]["qty"], 100.0, places=3)
        # second tick does not re-trim (not re-armed)
        self.assertEqual(len(tcs.evaluate(L, S, {"UPRO": c}, None, 0.7)), 0)

    def test_weekly_trail_3x_20pct(self):
        vals = [100.0] * 250 + [150.0] * 20 + [118.0] * 10  # 3x ETF: -21% from 150
        c = series(vals)
        L = L0(118.0); S = tcs.new_state()
        acts = tcs.evaluate(L, S, {"UPRO": c}, "2026-W43", 0.7)
        self.assertTrue(any(a["rule"] == "TRAIL_WEEKLY" for a in acts))
        self.assertTrue(S["pos"]["UPRO"]["exited"])
        # not re-checked same week
        self.assertEqual(S["last_week_checked"], "2026-W43")

    def test_trail_not_fired_at_16pct_for_3x(self):
        c = series([100.0] * 250 + [150.0] * 20 + [126.0] * 10)  # -16%
        S = tcs.new_state()
        acts = tcs.evaluate(L0(126.0), S, {"UPRO": c}, "2026-W43", 0.7)
        self.assertFalse(any(a["rule"] == "TRAIL_WEEKLY" for a in acts))

    def test_btc_stop_label(self):
        c = series([80000.0] * 250 + [100000.0] * 20 + [79000.0] * 10)
        L = {"holdings": {"XBT/USD": 1.0}, "mark_px_usd": {"XBT/USD": 79000.0}, "entry_px_usd": {"XBT/USD": 80000.0},
             "equity_aud": 1e6, "fills": [{"side": "BUY", "utc": "2025-06-02T00:00:00Z"}]}
        S = tcs.new_state()
        acts = tcs.evaluate(L, S, {"XBT/USD": c}, "2026-W43", 0.7)
        self.assertEqual([a["rule"] for a in acts], ["BTC_STOP"])

    def test_diff_sign(self):
        c = series([100.0] * 250 + [150.0] * 20 + [118.0] * 10)
        S = tcs.new_state(); L = L0(118.0)
        tcs.evaluate(L, S, {"UPRO": c}, "2026-W43", 0.7)
        L["mark_px_usd"]["UPRO"] = 90.0  # price keeps falling -> shadow ahead
        S["last_week_checked"] = "2026-W43"
        tcs.evaluate(L, S, {"UPRO": c}, "2026-W43", 0.7)
        self.assertGreater(S["pos"]["UPRO"]["diff_aud"], 0)

    def test_status_has_shadow_block(self):
        s = json.load(open(os.path.join(BASE, "static", "trend_core_status.json")))
        self.assertIn("shadow", s)
        self.assertFalse(s["shadow"]["in_book_pnl"])

    def test_desk_tick_wired(self):
        self.assertIn("trend_core_shadow.py", open(os.path.join(BASE, "desk_tick.sh")).read())


if __name__ == "__main__":
    unittest.main()
