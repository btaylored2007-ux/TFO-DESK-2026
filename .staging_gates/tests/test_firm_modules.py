#!/usr/bin/env python3
"""Tests for the firm's newest organs: books, tournament, risk engine, screen.

All synthetic data — no network, no live files touched. These pin the accounting
and rule logic that real money would one day depend on, which is exactly why the
paper versions must already be exact.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------- growth book
class TestGrowthTargets(unittest.TestCase):
    def _px(self, cols):
        import pandas as pd
        n = 300
        data = {}
        for name, drift in cols.items():
            steps = np.full(n, drift)
            data[name] = 100.0 * np.cumprod(1 + steps)
        return pd.DataFrame(data)

    def test_top2_positive_ranked(self):
        import growth_book as gb
        px = self._px({t: 0.0001 for t in gb.UNIVERSE})
        px[gb.UNIVERSE[3]] *= 0
        px[gb.UNIVERSE[3]] += np.linspace(100, 300, len(px))   # strongest
        px[gb.UNIVERSE[7]] += np.linspace(0, 120, len(px))     # second
        w, top5 = gb.choose_targets(px)
        self.assertAlmostEqual(sum(w.values()), 1.0, places=9)
        self.assertIn(gb.UNIVERSE[3], w)
        self.assertEqual(len([t for t in w if w[t] > 0]), 2)

    def test_negative_trailing_falls_back_to_safe(self):
        import growth_book as gb
        px = self._px({t: -0.001 for t in gb.UNIVERSE})   # everything falling
        w, _ = gb.choose_targets(px)
        self.assertEqual(list(w), [gb.SAFE])
        self.assertAlmostEqual(w[gb.SAFE], 1.0, places=9)

    def test_insufficient_history_raises(self):
        import pandas as pd
        import growth_book as gb
        px = pd.DataFrame({t: np.ones(50) for t in gb.UNIVERSE})
        with self.assertRaises(RuntimeError):
            gb.choose_targets(px)


# ---------------------------------------------------------------- tournament
class TestSleeveAccounting(unittest.TestCase):
    def _sleeve(self):
        import tournament as tn
        return {"name": "t", "cash": tn.START, "holdings": {}, "entry_px": {},
                "last_rebalance": None, "costs": 0.0, "peak": tn.START,
                "frozen": False, "started": "2026-08-06"}

    def test_set_holdings_conserves_equity_minus_cost(self):
        import tournament as tn
        s = self._sleeve()
        px = {"A": 100.0, "B": 50.0}
        cost = tn.set_holdings(s, {"A": 0.5, "B": 0.5}, px, 30.0)
        eq = s["cash"] + sum(u * px[t] for t, u in s["holdings"].items())
        self.assertAlmostEqual(eq, tn.START - cost, places=6)
        self.assertAlmostEqual(cost, tn.START * 0.003, places=6)  # full turnover

    def test_rebalance_charges_only_turnover(self):
        import tournament as tn
        s = self._sleeve()
        px = {"A": 100.0}
        tn.set_holdings(s, {"A": 1.0}, px, 30.0)
        c2 = tn.set_holdings(s, {"A": 1.0}, px, 30.0)   # same target
        # Only the cost-drag residue (~$30 of $10k) should trade — cents, not the
        # ~$30 a full-turnover recharge would cost.
        self.assertLess(c2, 0.5)

    def test_kill_rule_freezes_at_minus_30(self):
        import tournament as tn
        s = self._sleeve()
        tn.set_holdings(s, {"A": 1.0}, {"A": 100.0}, 0.0)
        tn.mark(s, {"A": 69.0})     # -31%
        self.assertTrue(s["frozen"])
        self.assertIn("kill rule", s.get("frozen_reason", ""))

    def test_mark_tracks_peak_and_dd(self):
        import tournament as tn
        s = self._sleeve()
        tn.set_holdings(s, {"A": 1.0}, {"A": 100.0}, 0.0)
        tn.mark(s, {"A": 120.0})
        tn.mark(s, {"A": 108.0})
        self.assertAlmostEqual(s["dd_pct"], -10.0, delta=0.2)
        self.assertFalse(s["frozen"])


# ---------------------------------------------------------------- risk engine
class TestRiskGather(unittest.TestCase):
    def test_signs_and_uncovered(self):
        import risk_engine as re_
        # gather_positions now consumes the canonical book_coverage boundary.
        # The old fixture patched risk_engine.BASE, which the refactored function
        # no longer reads, and accidentally exercised live ledgers. Keep this a
        # genuinely synthetic unit test by stubbing the normalized registry
        # payload it is responsible for consuming.
        coverage = {
            "generated_utc": "2026-09-02T00:00:00Z",
            "errors": [],
            "positions": [
                {"label": "Growth / SLV", "ticker": "SLV", "fallback_usd": 5000.0},
                {"label": "Floor / XLM", "ticker": "XLM-USD", "fallback_usd": -4000.0},
                {"label": "Floor / PUMP", "ticker": "KRK:PUMP", "fallback_usd": 2000.0},
                {"label": "Floor / malformed", "ticker": None, "fallback_usd": 1000.0},
            ],
        }
        with mock.patch.object(re_.book_coverage, "gather", return_value=coverage):
            pos, unc = re_.gather_positions()

        vals = {t: v for _, t, v in pos}
        self.assertAlmostEqual(vals["SLV"], 5000.0)
        self.assertAlmostEqual(vals["XLM-USD"], -4000.0)      # short is negative
        # 2026-08-15: updated for the KRK: fallback — a coin absent from
        # CRYPTO_MAP is now MEASURED via Kraken OHLC, not dropped. (The old
        # assertion pinned PUMP as unmeasurable, a data-vintage claim that
        # went stale when the fallback landed; unmeasured-at-fetch-time names
        # are handled downstream as `dropped`, counted into uncovered_usd.)
        self.assertAlmostEqual(vals["KRK:PUMP"], 2000.0)      # fallback, measured
        self.assertEqual(len(unc), 1)                          # only the malformed sym
        self.assertAlmostEqual(unc[0][1], 1000.0)


# ---------------------------------------------------------------- value screen
class TestValueScreen(unittest.TestCase):
    def _fund(self):
        base = dict(name="x", country="US", sector="s", market_cap_b=10.0,
                    trailing_pe=None, forward_pe=None, p_b=None, ev_ebitda=None,
                    fcf_yield=None, roe=None, margin=None, debt_to_equity=None,
                    rev_growth=None, peg=None)
        f = {}
        f["CHEAP"] = dict(base, trailing_pe=6, forward_pe=5, p_b=0.8,
                          ev_ebitda=4, fcf_yield=0.12, roe=0.2, margin=0.15,
                          debt_to_equity=40, rev_growth=0.08, peg=0.6)
        f["DEAR"] = dict(base, trailing_pe=60, forward_pe=55, p_b=12,
                         ev_ebitda=40, fcf_yield=0.005, roe=0.1, margin=0.05,
                         debt_to_equity=40, rev_growth=0.02, peg=4.0)
        f["TRAP"] = dict(base, trailing_pe=3, forward_pe=3, p_b=0.3,
                         ev_ebitda=2, fcf_yield=0.2, roe=0.05, margin=0.02,
                         debt_to_equity=300, rev_growth=-0.2, peg=None)
        f["THIN"] = dict(base, trailing_pe=10)     # too few metrics
        # a few mid names so percentile ranks have texture
        for i in range(6):
            f["MID%d" % i] = dict(base, trailing_pe=15 + i, forward_pe=14 + i,
                                  p_b=2 + i * 0.3, ev_ebitda=10 + i,
                                  fcf_yield=0.04, roe=0.12, margin=0.1,
                                  debt_to_equity=80, rev_growth=0.04, peg=1.5)
        return f

    def test_cheap_quality_outranks_expensive(self):
        import global_value_screen as gvs
        p = gvs.screen(fund=self._fund(), verbose=False)
        order = [r["ticker"] for r in p["results"]]
        self.assertLess(order.index("CHEAP"), order.index("DEAR"))

    def test_trap_flagged_and_thin_excluded(self):
        import global_value_screen as gvs
        p = gvs.screen(fund=self._fund(), verbose=False)
        by = {r["ticker"]: r for r in p["results"]}
        self.assertTrue(by["TRAP"]["value_trap_flag"])
        self.assertNotIn("THIN", by)                  # < MIN_METRICS
        picks = [r["ticker"] for r in gvs.top_picks(p, 5)]
        self.assertNotIn("TRAP", picks)


# ---------------------------------------------------------------- carry monitor
class TestCarryUnits(unittest.TestCase):
    def test_absolute_rate_converted_by_mark(self):
        import carry_monitor as cm
        # absolute funding of 0.0001 on a 100k mark = 1e-9 relative per interval
        rel = cm.relative_rate(0.0001, 100000.0)
        self.assertAlmostEqual(rel, 1e-9)
        self.assertIsNone(cm.relative_rate(0.1, 0))
        ann = cm.annualise(rel)
        # 17 Oct 2026: stale 8h assumption; Kraken Futures funding is HOURLY (carry_monitor.INTERVALS_PER_YEAR = 24*365, data-verified)
        self.assertAlmostEqual(ann, 1e-9 * 24 * 365 * 100)

    def test_xbt_aliases_to_btc(self):
        import carry_monitor as cm
        rows = [{"base": "XBT", "t": 2, "fundingRate": 1.0, "markPrice": 100.0},
                {"base": "XBT", "t": 1, "fundingRate": 2.0, "markPrice": 100.0}]
        latest = cm.latest_by_base(rows)
        self.assertIn("BTC", latest)
        self.assertEqual(latest["BTC"]["t"], 2)       # newest wins


if __name__ == "__main__":
    unittest.main(verbosity=2)
