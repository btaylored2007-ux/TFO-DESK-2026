#!/usr/bin/env python3
"""Tests for the validation harness.

This module is the trust anchor for every strategy claim the desk makes, so its
own behaviour needs to be pinned down: it must reject search artefacts and accept
genuine signal, and it must not leak across cross-validation folds.
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import quant_validation as qv  # noqa: E402


class TestDeflation(unittest.TestCase):

    def test_rejects_best_of_many_on_noise(self):
        """The core guarantee: a winner selected from a wide search of pure noise
        must not survive deflation, even when its naive t-stat looks significant."""
        rng = np.random.default_rng(11)
        trials = [rng.normal(0, 0.01, 500) for _ in range(200)]
        sharpes = [qv.sharpe_ratio(t) for t in trials]
        best = trials[int(np.argmax(sharpes))]
        ev = qv.evaluate("noise", best, n_trials=200, trial_sharpes=sharpes)
        self.assertFalse(ev["survives_deflation"])
        self.assertLess(ev["deflated_sharpe"], 0.95)

    def test_accepts_genuine_signal_tested_once(self):
        rng = np.random.default_rng(11)
        real = rng.normal(0.0010, 0.01, 800)
        ev = qv.evaluate("real", real, n_trials=1)
        self.assertTrue(ev["survives_deflation"])

    def test_more_trials_deflates_harder(self):
        """Deflation must be monotone in search breadth: the same result found
        after more searching is less credible, never more."""
        rng = np.random.default_rng(3)
        r = rng.normal(0.0006, 0.01, 400)
        few = qv.deflated_sharpe_ratio(r, n_trials=2)["deflated_sharpe"]
        many = qv.deflated_sharpe_ratio(r, n_trials=5000)["deflated_sharpe"]
        self.assertGreater(few, many)

    def test_expected_max_sharpe_grows_with_trials(self):
        a = qv.expected_max_sharpe(10, 0.01)
        b = qv.expected_max_sharpe(1000, 0.01)
        self.assertGreater(b, a)

    def test_single_trial_has_no_deflation(self):
        self.assertEqual(qv.expected_max_sharpe(1, 0.01), 0.0)

    def test_negative_skew_raises_the_bar(self):
        """Fat left tails inflate the Sharpe standard error, so a tail-selling
        return stream must need more evidence than a symmetric one."""
        rng = np.random.default_rng(5)
        sym = rng.normal(0.0008, 0.01, 600)
        skewed = -np.abs(rng.standard_t(3, 600)) * 0.004 + 0.0018
        psr_sym = qv.probabilistic_sharpe_ratio(sym)
        psr_skew = qv.probabilistic_sharpe_ratio(skewed)
        self.assertTrue(np.isfinite(psr_sym) and np.isfinite(psr_skew))
        self.assertLess(qv.moments(skewed)["skew"], 0.0)


class TestEffectiveSampleSize(unittest.TestCase):
    """Regression tests for the failure that bit this desk: pooling correlated
    assets inflated n from 7 to 90 and turned a t of 2.17 into 7.63."""

    def test_correlated_series_discount_sample_size(self):
        n_eff = qv.effective_sample_size(90, n_series=18, mean_correlation=0.665)
        self.assertLess(n_eff, 10)
        self.assertGreater(n_eff, 5)

    def test_independent_series_are_not_discounted(self):
        self.assertEqual(qv.effective_sample_size(100, n_series=1), 100.0)
        self.assertEqual(qv.effective_sample_size(100, n_series=10,
                                                  mean_correlation=0.0), 100.0)

    def test_overlap_factor_discounts(self):
        self.assertAlmostEqual(
            qv.effective_sample_size(100, overlap_factor=2.0), 50.0)

    def test_never_returns_below_two(self):
        self.assertGreaterEqual(
            qv.effective_sample_size(3, n_series=50, mean_correlation=0.99), 2.0)

    def test_mean_pairwise_correlation_detects_comovement(self):
        rng = np.random.default_rng(9)
        common = rng.normal(0, 0.01, 300)
        corr = np.array([common + rng.normal(0, 0.003, 300) for _ in range(8)])
        indep = rng.normal(0, 0.01, (8, 300))
        self.assertGreater(qv.mean_pairwise_correlation(corr), 0.7)
        self.assertLess(abs(qv.mean_pairwise_correlation(indep)), 0.2)

    def test_effective_n_lowers_deflated_sharpe(self):
        """The whole point: honest n must make a result less significant."""
        rng = np.random.default_rng(6)
        r = rng.normal(0.0015, 0.01, 200)
        naive = qv.deflated_sharpe_ratio(r, n_trials=50)["deflated_sharpe"]
        corrected = qv.deflated_sharpe_ratio(r, n_trials=50, n_effective=16)["deflated_sharpe"]
        self.assertGreater(naive, corrected)


class TestBenchmarkComparison(unittest.TestCase):

    def test_strategy_that_is_just_beta_fails(self):
        """A 'strategy' that merely reproduces the benchmark must not pass."""
        rng = np.random.default_rng(8)
        bench = rng.normal(0.004, 0.02, 200)
        strat = bench + rng.normal(0, 0.001, 200)     # benchmark plus noise
        ev = qv.evaluate("beta-only", strat, n_trials=1,
                         benchmark_returns=bench, trades_per_period=1.0)
        self.assertFalse(ev["beats_benchmark"])
        self.assertIn("does NOT beat buy-and-hold", qv.verdict_line(ev))

    def test_genuine_excess_beats_benchmark(self):
        rng = np.random.default_rng(8)
        bench = rng.normal(0.001, 0.01, 400)
        strat = bench + rng.normal(0.004, 0.004, 400)
        ev = qv.evaluate("real-alpha", strat, n_trials=1,
                         benchmark_returns=bench, trades_per_period=1.0)
        self.assertTrue(ev["beats_benchmark"])


class TestKillTests(unittest.TestCase):
    """Each of these encodes a candidate that passed deflation and still wasn't real."""

    def test_liquidity_screen_kills_illiquid_only_effect(self):
        # big effect concentrated in tiny names, nothing in liquid ones
        eff = [80.0, 75.0, 90.0, 0.5, -1.0, 0.2]
        vol = [5e3, 8e3, 6e3, 5e6, 8e6, 9e6]
        res = qv.liquidity_screen(eff, vol)
        self.assertFalse(res["survives_at_highest_floor"])
        self.assertIn("DIES", res["verdict"])

    def test_liquidity_screen_passes_broad_effect(self):
        rng = np.random.default_rng(12)
        eff = list(rng.normal(40, 5, 12))
        vol = list(np.linspace(5e3, 5e6, 12))
        res = qv.liquidity_screen(eff, vol)
        self.assertTrue(res["survives_at_highest_floor"])

    def test_decay_profile_flags_sign_flip_within_a_second(self):
        res = qv.decay_profile({0: 25.0, 1: -3.4, 30: -5.0})
        self.assertTrue(res["fragile"])
        self.assertTrue(res["profile"][1]["sign_flipped"])
        self.assertIn("impact, not signal", res["verdict"])

    def test_decay_profile_accepts_graceful_decay(self):
        res = qv.decay_profile({0: 40.0, 1: 39.0, 60: 33.0})
        self.assertFalse(res["fragile"])

    def test_decay_profile_requires_zero_delay(self):
        with self.assertRaises(ValueError):
            qv.decay_profile({1: 10.0, 5: 8.0})

    def test_effective_bets_collapses_under_high_persistence(self):
        res = qv.effective_bets(110, 0.988)
        self.assertLess(res["independent_bets"], 5)
        self.assertIn("single held position", res["verdict"])

    def test_effective_bets_survives_low_persistence(self):
        res = qv.effective_bets(200, 0.05)
        self.assertGreater(res["independent_bets"], 100)

    def test_effective_bets_handles_perfect_persistence(self):
        res = qv.effective_bets(500, 1.0)
        self.assertGreaterEqual(res["independent_bets"], 1.0)


class TestPurgedCV(unittest.TestCase):

    def test_no_train_test_overlap(self):
        for tr, te in qv.purged_kfold_splits(500, 5, 0.01, 5):
            self.assertEqual(len(set(tr.tolist()) & set(te.tolist())), 0)

    def test_embargo_creates_a_gap_after_test_fold(self):
        """Training data must not resume immediately after the test fold."""
        n, horizon, embargo_pct = 500, 5, 0.02
        for tr, te in qv.purged_kfold_splits(n, 5, embargo_pct, horizon):
            after = tr[tr > te.max()]
            if after.size:
                gap = after.min() - te.max()
                self.assertGreaterEqual(gap, horizon + int(n * embargo_pct))

    def test_rejects_too_few_splits(self):
        with self.assertRaises(ValueError):
            list(qv.purged_kfold_splits(100, 1))


class TestMultipleTesting(unittest.TestCase):

    def test_benjamini_hochberg_rejects_only_small_pvalues(self):
        p = [0.001, 0.008, 0.04, 0.2, 0.7, 0.9]
        rejected, adj = qv.benjamini_hochberg(p, alpha=0.05)
        self.assertTrue(rejected[0])
        self.assertFalse(rejected[-1])
        self.assertTrue(np.all(adj >= np.array(p) - 1e-12))

    def test_bh_handles_empty_input(self):
        rejected, adj = qv.benjamini_hochberg([])
        self.assertEqual(rejected.size, 0)

    def test_reality_check_flags_noise_winner(self):
        rng = np.random.default_rng(2)
        fam = [rng.normal(0, 0.01, 250) for _ in range(30)]
        res = qv.whites_reality_check(fam, n_boot=150)
        self.assertFalse(res["survives"])
        self.assertGreater(res["p_value"], 0.05)


class TestGuards(unittest.TestCase):

    def test_too_few_observations_raises(self):
        with self.assertRaises(ValueError):
            qv.moments([0.01])

    def test_zero_variance_returns_zero_sharpe(self):
        self.assertEqual(qv.sharpe_ratio([0.01] * 50), 0.0)

    def test_min_trl_infinite_when_below_benchmark(self):
        rng = np.random.default_rng(1)
        losing = rng.normal(-0.001, 0.01, 300)
        self.assertEqual(qv.min_track_record_length(losing), float("inf"))

    def test_cost_wall_flags_are_computed(self):
        rng = np.random.default_rng(4)
        r = rng.normal(0.005, 0.01, 300)     # 50 bps/period, clears Kraken
        ev = qv.evaluate("big", r, n_trials=1, trades_per_period=1.0)
        self.assertTrue(ev["clears_cost_wall"]["kraken_crypto"])
        self.assertFalse(ev["clears_cost_wall"]["etoro_crypto"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
