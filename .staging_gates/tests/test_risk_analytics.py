"""Economic invariants for the canonical dial/risk model; no network or ledgers."""
from datetime import datetime, timezone
import json
import os
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd

import exposure_dial
import risk_analytics as ra
import risk_engine

NOW = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)


def panel():
    dates = pd.bdate_range(end="2026-09-09", periods=100)
    rng = np.random.default_rng(21)
    returns = rng.normal(0, 0.01, len(dates))
    return pd.DataFrame({"A": 100 * np.cumprod(1 + returns),
                         "B": 100 * np.cumprod(1 - returns),
                         "SPY": 100 * np.cumprod(1 + returns)}, index=dates)


def coverage(exposures, equity=1000):
    positions = [{"book": book, "label": "%s:%s:%d" % (book, ticker, i),
                  "ticker": ticker, "fallback_usd": value, "units": abs(value) / 100,
                  "fallback_basis": "mark"}
                 for i, (book, ticker, value) in enumerate(exposures)]
    names = sorted({p["book"] for p in positions}) or ["cash"]
    books = [{"key": name, "label": name, "kind": "test", "equity_usd": equity / len(names),
              "positions": [p for p in positions if p["book"] == name],
              "error": None, "stale": False, "equity_basis": "fixture", "asof": "2026-09-10"}
             for name in names]
    return {"books": books, "positions": positions, "n_books": len(books),
            "n_books_ok": len(books), "n_positions": len(positions),
            "firm_equity_usd": equity, "errors": [], "warnings": []}


class RiskEconomics(unittest.TestCase):
    def analyze(self, g, raw=None, **kwargs):
        return ra.analyze(g, panel() if raw is None else raw, now=NOW,
                          check_files=False, **kwargs)

    def test_cash_in_equity_dilutes_percentage_risk_not_dollar_risk(self):
        g = coverage([("long", "A", 1000)])
        full = self.analyze(g)
        cash = self.analyze(coverage([("long", "A", 1000)], equity=2000))
        self.assertAlmostEqual(full["statistics"]["ann_vol_pct"], 2 * cash["statistics"]["ann_vol_pct"])
        self.assertAlmostEqual(full["statistics"]["daily_sigma_usd"], cash["statistics"]["daily_sigma_usd"])

    def test_entry_cost_only_is_revalued_with_fx_without_mutating_ledger_snapshot(self):
        g = coverage([("long", "A", 500)])
        p = g["positions"][0]
        p.update(fallback_basis="entry cost (no mark)", units=10, mark_fx_usd=.5)
        raw = panel()
        a = self.analyze(g, raw=raw)
        expected = 10 * float(raw["A"].iloc[-1]) * .5
        self.assertAlmostEqual(a["net_exposures_usd"]["A"], expected)
        self.assertEqual(p["fallback_usd"], 500)
        self.assertEqual(a["equity_usd"], 1000)
        self.assertEqual(a["valuation"]["positions_remarked"], 1)
        p["fallback_basis"] = "mark_usd"
        self.assertEqual(self.analyze(g)["net_exposures_usd"]["A"], 500)

    def test_entry_cost_missing_fx_refuses_risk_completeness(self):
        g = coverage([("long", "A", 500)])
        g["positions"][0]["fallback_basis"] = "entry cost"
        a = self.analyze(g)
        self.assertFalse(a["complete"])
        self.assertEqual(a["valuation"]["still_at_cost_usd"], 500)

    def test_identical_long_short_offsets_price_risk_but_preserves_gross(self):
        a = self.analyze(coverage([("long", "A", 500), ("short", "A", -500)]))
        self.assertEqual(a["gross_usd"], 1000)
        self.assertEqual(a["net_usd"], 0)
        self.assertEqual(a["statistics"]["ann_vol_pct"], 0)
        self.assertTrue(a["complete"])
        self.assertIsNone(a["concentration"]["effective_holdings"])

    def test_short_sign_reverses_return_and_factor_beta(self):
        a = self.analyze(coverage([("short", "A", -500)]), factors={"SPY": "equity"})
        self.assertAlmostEqual(a["factor_betas"]["equity"], -0.5)
        self.assertTrue((a["return_series"] < 0).any())

    def test_all_cash_is_known_zero_even_without_price_panel(self):
        a = self.analyze(coverage([]), raw=pd.DataFrame())
        self.assertTrue(a["complete"])
        self.assertEqual(a["statistics"]["ann_vol_pct"], 0)

    def test_zero_negative_or_nan_equity_cannot_create_risk_number(self):
        for equity in (0, -100, float("nan")):
            a = self.analyze(coverage([("long", "A", 500)], equity=equity))
            self.assertFalse(a["complete"])
            self.assertFalse(a["statistics"]["available"])

    def test_unpriced_exposure_never_renormalizes_measured_weights(self):
        a = self.analyze(coverage([("long", "A", 500), ("unknown", "MISSING", 500)]))
        self.assertFalse(a["complete"])
        self.assertEqual(a["weights"]["A"], 0.5)
        self.assertEqual(a["unpriced_gross_usd"], 500)
        self.assertEqual(a["mapped_capital_pct"], 50)

    def test_unpriced_exact_hedge_is_still_unknown_basis_risk(self):
        a = self.analyze(coverage([("long", "MISSING", 500), ("short", "MISSING", -500)]))
        self.assertFalse(a["complete"])
        self.assertEqual(a["unpriced_gross_usd"], 1000)
        self.assertFalse(a["statistics"]["available"])

    def test_unknown_usd_value_cannot_disappear(self):
        g = coverage([("bad", "A", 500)])
        g["positions"][0]["fallback_usd"] = None
        a = self.analyze(g)
        self.assertFalse(a["complete"])
        self.assertEqual(len(a["unvaluable_positions"]), 1)

    def test_stale_quotes_are_detected_before_forward_fill(self):
        raw = panel()
        raw.loc[raw.index[-10:], "A"] = np.nan
        a = self.analyze(coverage([("long", "A", 500)]), raw=raw)
        self.assertFalse(a["complete"])
        self.assertIn("last settled quote is stale", a["price_freshness"]["A"]["reasons"])

    def test_short_common_window_cannot_open_dial(self):
        raw = panel().tail(20)
        d = exposure_dial.compute(coverage([("long", "A", 500)]), raw,
                                  now=NOW, unclaimed=[], check_files=False)
        self.assertFalse(d["gate_open_for_new_probes"])
        self.assertEqual(d["scale"], 0)

    def test_whole_inventory_equity_and_position_counts_are_required(self):
        g = coverage([("long", "A", 500)])
        g["n_books"] = 2
        self.assertFalse(self.analyze(g)["complete"])
        g = coverage([("long", "A", 500)])
        g["n_positions"] = 2
        self.assertFalse(self.analyze(g)["complete"])

    def test_unclaimed_ledger_and_stale_book_fail_closed(self):
        g = coverage([("long", "A", 500)])
        self.assertFalse(self.analyze(g, unclaimed=[{"path": "unregistered.json"}])["complete"])
        g["books"][0]["stale"] = True
        self.assertFalse(self.analyze(g)["complete"])

    def test_file_freshness_checks_actual_ledger_timestamp(self):
        g = coverage([("long", "A", 500)])
        with tempfile.NamedTemporaryFile() as f:
            g["books"][0]["path"] = f.name
            old = NOW.timestamp() - 7 * 3600
            os.utime(f.name, (old, old))
            status = ra.inventory_status(g, now=NOW)
        self.assertFalse(status["complete"])
        self.assertTrue(any("freshness failed" in x for x in status["errors"]))

    def test_current_partial_bar_excluded_and_weekends_compounded(self):
        dates = pd.to_datetime(["2026-09-04", "2026-09-05", "2026-09-06", "2026-09-07", "2026-09-10"])
        clean, last = ra.prepare_panel(pd.DataFrame({"A": [100, 101, 102, 103, 900]}, index=dates), NOW)
        self.assertEqual(last["A"], pd.Timestamp("2026-09-07"))
        self.assertAlmostEqual(clean["A"].pct_change(fill_method=None).iloc[-1], .03)

    def test_risk_and_dial_share_current_equity_denominator(self):
        g = coverage([("long", "A", 500), ("short", "A", -100)], equity=2000)
        raw = panel()
        d = exposure_dial.compute(g, raw, now=NOW, unclaimed=[], check_files=False)
        r = risk_engine.run(False, g, raw, refresh_stress=False, check_files=False,
                            unclaimed=[], now=NOW, write_report=False)
        self.assertEqual(d["total_capital_usd"], r["risk_denominator_usd"])
        self.assertEqual(r["weights"]["A"], .2)
        self.assertIsNone(r["effective_bets"])
        self.assertEqual(r["effective_holdings"], 1)

    def test_failed_recomputation_replaces_an_open_dial_with_closed_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "dial.json")
            hist = os.path.join(tmp, "history.jsonl")
            with open(out, "w") as f:
                json.dump({"scale": 1.0, "state": "FULL"}, f)
            with mock.patch.multiple(exposure_dial, OUT=out, HIST=hist, REPORTS=tmp), \
                    mock.patch.object(exposure_dial, "compute", side_effect=ValueError("bad data")), \
                    mock.patch("sys.argv", ["exposure_dial.py", "--force"]):
                exposure_dial.main()
            with open(out) as f:
                d = json.load(f)
            self.assertEqual(d["scale"], 0)
            self.assertEqual(d["state"], "DEGRADED")
            self.assertFalse(d["gate_open_for_new_probes"])

    def test_book_contributions_and_common_factors_describe_overlap(self):
        g = coverage([("one", "A", 250), ("two", "SPY", 750)])
        a = self.analyze(g, factors={"SPY": "equity"})
        contribution = {x["book"]: x["variance_contribution_pct"] for x in a["book_risk_contributions"]}
        self.assertAlmostEqual(contribution["one"], 25)
        self.assertAlmostEqual(contribution["two"], 75)
        self.assertAlmostEqual(a["book_correlations"][0]["correlation"], 1)
        self.assertAlmostEqual(a["common_factor_model"]["r_squared"], 1)

    def test_stress_subset_preserves_equity_and_flags_missing_names(self):
        idx = pd.bdate_range("2020-02-19", "2020-03-23")
        prices = pd.DataFrame({"A": 100 * np.cumprod(np.repeat(.99, len(idx)))}, index=idx)
        r = risk_engine.replay_window(prices, {"A": 500, "MISSING": 500},
                                      "2020-02-19", "2020-03-23", denominator=2000)
        expected = ((1 - .01 * .25) ** (len(idx) - 1) - 1) * 100
        self.assertEqual(r["denominator_usd"], 2000)
        self.assertAlmostEqual(r["return_pct"], round(expected, 1))
        self.assertTrue(r["partial"])
        self.assertEqual(r["covered_pct"], 50)


class CorrelationMeaning(unittest.TestCase):
    def test_equal_independent_exposures_have_two_dimensions(self):
        r = pd.DataFrame({"A": [.01, -.01, .01, -.01], "B": [.01, .01, -.01, -.01]})
        c = ra.correlation_metrics(r, {"A": .5, "B": .5})
        self.assertAlmostEqual(c["effective_holdings"], 2)
        self.assertAlmostEqual(c["effective_risk_dimensions"], 2)
        self.assertAlmostEqual(c["diversification_ratio"], np.sqrt(2))

    def test_duplicate_correlated_holdings_are_one_risk_dimension(self):
        r = pd.DataFrame({"A": [.01, -.01, .01, -.01], "B": [.01, -.01, .01, -.01]})
        c = ra.correlation_metrics(r, {"A": .5, "B": .5})
        self.assertAlmostEqual(c["effective_holdings"], 2)
        self.assertAlmostEqual(c["effective_risk_dimensions"], 1)
        self.assertAlmostEqual(c["diversification_ratio"], 1)

    def test_correlated_short_hedge_reports_no_finite_diversification_ratio(self):
        r = pd.DataFrame({"A": [.01, -.01, .01, -.01], "B": [.01, -.01, .01, -.01]})
        c = ra.correlation_metrics(r, {"A": .5, "B": -.5})
        self.assertAlmostEqual(c["effective_risk_dimensions"], 1)
        self.assertIsNone(c["diversification_ratio"])


if __name__ == "__main__":
    unittest.main()
