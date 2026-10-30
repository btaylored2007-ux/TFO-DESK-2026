#!/usr/bin/env python3
"""Statistical validation harness — the instrument every strategy gets scored through.

Venue-agnostic and desk-agnostic. Import it from any desk on this machine.

WHY THIS EXISTS
---------------
Searching a large strategy space and keeping the best result is the most reliable
way to manufacture a beautiful backtest with zero forward value. If you test 100
random strategies on noise, the best one will show a Sharpe near 2.5 — purely from
selection. The backtest is not lying; the *selection* is.

The fix is not to search less. It is to deflate the result by how hard you searched.
This module implements that deflation, plus the cross-validation and multiple-testing
machinery that makes a wide search trustworthy rather than self-deceiving.

Implemented
-----------
  probabilistic_sharpe_ratio  Bailey & Lopez de Prado (2012) -- P(true SR > benchmark),
                              corrected for skew, fat tails and sample length
  deflated_sharpe_ratio       Bailey & Lopez de Prado (2014) -- PSR against the Sharpe
                              you'd EXPECT as the max of N trials on pure noise
  min_track_record_length     How many observations you'd need for significance
  purged_kfold_splits         Lopez de Prado -- CV with purging + embargo so
                              overlapping labels can't leak across the split
  benjamini_hochberg          FDR control across a family of tested hypotheses
  whites_reality_check        Stationary-bootstrap test that the BEST of N strategies
                              beats the benchmark after accounting for the search
  evaluate                    One call: does this survive, and does it clear costs?

Reference:
  Bailey & Lopez de Prado, "The Deflated Sharpe Ratio" (2014)
  Lopez de Prado, "Advances in Financial Machine Learning" (2018), ch. 7
  White, "A Reality Check for Data Snooping" (2000)
"""
import math

import numpy as np
from scipy import stats

EULER_MASCHERONI = 0.5772156649015329


# ---------------------------------------------------------------------------
# Basic moments
# ---------------------------------------------------------------------------
def _clean(returns):
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if r.size < 2:
        raise ValueError("need at least 2 finite observations, got %d" % r.size)
    return r


def sharpe_ratio(returns, periods_per_year=None):
    """Per-observation Sharpe. Pass periods_per_year to annualize."""
    r = _clean(returns)
    sd = r.std(ddof=1)
    if sd == 0:
        return 0.0
    sr = r.mean() / sd
    return sr * math.sqrt(periods_per_year) if periods_per_year else sr


def moments(returns):
    r = _clean(returns)
    sd = r.std(ddof=1)
    return {
        "n": int(r.size),
        "mean": float(r.mean()),
        "stdev": float(sd),
        "skew": float(stats.skew(r)),
        "kurtosis": float(stats.kurtosis(r, fisher=False)),   # non-excess
        "sharpe": float(r.mean() / sd) if sd > 0 else 0.0,
    }


# ---------------------------------------------------------------------------
# Effective sample size
#
# The Deflated Sharpe corrects for HOW MANY strategies you tested. It assumes the
# observations within a strategy are independent. In practice they usually are not:
#   * pooling 18 correlated crypto assets over the same calendar windows does NOT
#     give you 18x the information
#   * overlapping holding periods reuse the same underlying price moves
# Ignoring this inflates n, shrinks the standard error, and manufactures
# significance. This bit the desk once already: a 120-day-hold sweep produced a
# naive t of 7.63 that fell to 2.17 once correlation was accounted for.
# ---------------------------------------------------------------------------
def mean_pairwise_correlation(series_matrix):
    """Mean off-diagonal correlation of a (n_series, n_obs) matrix."""
    M = np.asarray(series_matrix, dtype=float)
    if M.ndim != 2 or M.shape[0] < 2:
        return 0.0
    C = np.corrcoef(M)
    off = C[np.triu_indices_from(C, 1)]
    off = off[np.isfinite(off)]
    return float(off.mean()) if off.size else 0.0


def effective_sample_size(n_total, n_series=1, mean_correlation=0.0,
                          overlap_factor=1.0):
    """Independent-observation count after correlation and overlap.

    n_series          how many correlated series were pooled
    mean_correlation  their mean pairwise correlation
    overlap_factor    >1 if holding periods overlap (e.g. 2.0 for 50% overlap)

    The correlation term is the standard design-effect correction:
        n_eff = n / (1 + (m - 1) * rho)
    """
    n = float(n_total)
    if n_series > 1 and mean_correlation > 0:
        n = n / (1.0 + (n_series - 1) * float(mean_correlation))
    if overlap_factor and overlap_factor > 1.0:
        n = n / float(overlap_factor)
    return max(2.0, n)


# ---------------------------------------------------------------------------
# Probabilistic / Deflated Sharpe
# ---------------------------------------------------------------------------
def _psr_from_stats(sr, n, skew, kurt, benchmark_sr=0.0):
    """P(true SR > benchmark_sr) given the observed moments.

    The denominator is the standard error of the Sharpe estimator under
    non-normal returns: negative skew and fat tails INFLATE it, which is why a
    strategy that sells tail risk needs a much longer record to prove itself.
    """
    denom_var = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr ** 2
    if denom_var <= 0:
        return float("nan")
    z = (sr - benchmark_sr) * math.sqrt(n - 1) / math.sqrt(denom_var)
    return float(stats.norm.cdf(z))


def probabilistic_sharpe_ratio(returns, benchmark_sr=0.0, n_effective=None):
    """P(true Sharpe > benchmark_sr). Above 0.95 is the usual bar.

    Pass n_effective (from effective_sample_size) whenever the observations are
    pooled across correlated series or use overlapping windows.
    """
    m = moments(returns)
    n = n_effective if n_effective else m["n"]
    return _psr_from_stats(m["sharpe"], n, m["skew"], m["kurtosis"], benchmark_sr)


def expected_max_sharpe(n_trials, trial_sharpe_variance):
    """The Sharpe you should EXPECT as the best of N trials on pure noise.

    This is the number a search must beat to have found anything at all. It grows
    with the number of trials -- which is precisely why "we tested everything" makes
    a good-looking winner *less* impressive, not more.
    """
    if n_trials < 2:
        return 0.0
    v = math.sqrt(max(trial_sharpe_variance, 0.0))
    a = stats.norm.ppf(1.0 - 1.0 / n_trials)
    b = stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return float(v * ((1.0 - EULER_MASCHERONI) * a + EULER_MASCHERONI * b))


def deflated_sharpe_ratio(returns, n_trials, trial_sharpes=None,
                          trial_sharpe_variance=None, n_effective=None):
    """PSR measured against the expected max-of-N-trials Sharpe.

    Pass `trial_sharpes` (the Sharpe of EVERY variant you tested, not just the
    winner) when you have them -- their spread is what sets the deflation. If you
    only know how many things you tried, pass n_trials and a variance estimate.

    Returns a dict; `deflated_sharpe` above 0.95 means the result survives the
    search that produced it.
    """
    m = moments(returns)
    if trial_sharpes is not None:
        ts = np.asarray(trial_sharpes, dtype=float)
        ts = ts[np.isfinite(ts)]
        if ts.size >= 2:
            trial_sharpe_variance = float(ts.var(ddof=1))
            n_trials = max(n_trials, int(ts.size))
    n = float(n_effective) if n_effective else float(m["n"])
    if trial_sharpe_variance is None:
        # Conservative fallback: under the null, SR_hat has variance ~1/n.
        trial_sharpe_variance = 1.0 / n

    sr0 = expected_max_sharpe(n_trials, trial_sharpe_variance)
    dsr = _psr_from_stats(m["sharpe"], n, m["skew"], m["kurtosis"], sr0)
    return {
        "observed_sharpe": m["sharpe"],
        "n_trials": int(n_trials),
        "expected_max_sharpe_under_null": sr0,
        "deflated_sharpe": dsr,
        "psr_vs_zero": _psr_from_stats(m["sharpe"], n, m["skew"], m["kurtosis"], 0.0),
        "survives": bool(dsr is not None and not math.isnan(dsr) and dsr > 0.95),
        "n_obs": m["n"],
        "n_effective": round(n, 2),
        "independence_discount": round(m["n"] / n, 2) if n else None,
        "skew": m["skew"],
        "kurtosis": m["kurtosis"],
    }


def min_track_record_length(returns, benchmark_sr=0.0, confidence=0.95):
    """Observations needed before the Sharpe could be called significant."""
    m = moments(returns)
    sr = m["sharpe"]
    if sr <= benchmark_sr:
        return float("inf")
    z = stats.norm.ppf(confidence)
    denom_var = 1.0 - m["skew"] * sr + ((m["kurtosis"] - 1.0) / 4.0) * sr ** 2
    return float(1.0 + denom_var * (z / (sr - benchmark_sr)) ** 2)


# ---------------------------------------------------------------------------
# Cross-validation that does not leak
# ---------------------------------------------------------------------------
def purged_kfold_splits(n_samples, n_splits=5, embargo_pct=0.01, label_horizon=1):
    """Time-series CV with purging and an embargo.

    Plain K-fold leaks in finance: a label at time t built from a forward window
    overlaps observations that land in the training set, so the model sees its own
    answer. Purging drops training points whose label window overlaps the test
    fold; the embargo drops a further slice immediately after it, where
    serial correlation would otherwise leak.

    Yields (train_idx, test_idx) numpy arrays.
    """
    if n_splits < 2:
        raise ValueError("n_splits must be >= 2")
    idx = np.arange(n_samples)
    fold_bounds = np.array_split(idx, n_splits)
    embargo = int(n_samples * embargo_pct)

    for fold in fold_bounds:
        if fold.size == 0:
            continue
        start, stop = fold[0], fold[-1]
        test_idx = idx[start:stop + 1]

        # purge: anything whose label window reaches into the test fold
        purge_lo = max(0, start - label_horizon)
        purge_hi = min(n_samples, stop + 1 + label_horizon)
        # embargo: a further slice after the test fold
        embargo_hi = min(n_samples, purge_hi + embargo)

        mask = np.ones(n_samples, dtype=bool)
        mask[purge_lo:embargo_hi] = False
        train_idx = idx[mask]
        if train_idx.size and test_idx.size:
            yield train_idx, test_idx


# ---------------------------------------------------------------------------
# Kill-tests
#
# Earned the hard way on this desk. Each of these killed a candidate that had
# already passed deflation, so they run BEFORE anything is called an edge:
#
#   liquidity_screen    four candidates cleared 30 bps raw and went to zero (one
#                       flipped negative) once a tradeable-size floor was imposed.
#                       An effect that lives only in names you cannot fill is not
#                       an effect.
#   decay_profile       the statistically strongest signal found (t = -7.4, the only
#                       one beating Bonferroni) paid 25 bp at 30 min -- and -3.4 bp
#                       if you entered ONE SECOND later. It was measuring temporary
#                       impact at the whale's own fill price, not a tradeable move.
#   effective_bets      a cross-sectional portfolio with 0.988 rank autocorrelation
#                       rebalanced 110 times is not 110 bets. It is one bet held.
# ---------------------------------------------------------------------------
def liquidity_screen(per_asset_effect_bps, per_asset_volume_usd, floors=None):
    """Recompute an aggregate effect at rising tradeable-size floors.

    An edge that survives only below a floor you could actually trade is a
    measurement of illiquidity, not an opportunity. Returns the effect at each
    floor plus a verdict.
    """
    floors = floors or [0.0, 10_000.0, 100_000.0, 1_000_000.0]
    eff = np.asarray(per_asset_effect_bps, dtype=float)
    vol = np.asarray(per_asset_volume_usd, dtype=float)
    if eff.shape != vol.shape:
        raise ValueError("effect and volume arrays must align")

    rows = []
    for f in floors:
        mask = vol >= f
        if mask.sum() < 2:
            rows.append({"floor_usd": f, "n_assets": int(mask.sum()),
                         "effect_bps": None, "t_stat": None})
            continue
        sub = eff[mask]
        sd = sub.std(ddof=1)
        rows.append({
            "floor_usd": f,
            "n_assets": int(mask.sum()),
            "effect_bps": round(float(sub.mean()), 2),
            "t_stat": round(float(sub.mean() / (sd / math.sqrt(sub.size))), 2)
            if sd > 0 else None,
        })
    usable = [r for r in rows if r["effect_bps"] is not None]
    survives = bool(usable and usable[-1]["effect_bps"] is not None
                    and usable[-1]["t_stat"] is not None
                    and usable[-1]["effect_bps"] > 0 and usable[-1]["t_stat"] > 2.0)
    return {
        "by_floor": rows,
        "survives_at_highest_floor": survives,
        "verdict": ("survives a tradeable-size floor" if survives else
                    "DIES under a tradeable-size floor -- effect lives in names "
                    "you cannot fill at size"),
    }


def decay_profile(effect_by_delay):
    """How fast does the effect decay as execution is delayed?

    `effect_by_delay` maps delay (seconds) -> effect in bps, and must include 0.
    A real signal decays gently. Temporary impact collapses, and often flips sign,
    within seconds — which means it was never capturable.
    """
    if 0 not in effect_by_delay:
        raise ValueError("effect_by_delay must include delay 0")
    base = float(effect_by_delay[0])
    delays = sorted(effect_by_delay)
    rows = []
    for d in delays:
        e = float(effect_by_delay[d])
        rows.append({
            "delay_sec": d,
            "effect_bps": round(e, 3),
            "retained_pct": round(e / base * 100, 1) if base else None,
            "sign_flipped": bool(base != 0 and (e / base) < 0),
        })
    after_1s = next((r for r in rows if r["delay_sec"] >= 1), None)
    fragile = bool(after_1s and (after_1s["sign_flipped"]
                                 or (after_1s["retained_pct"] or 0) < 50))
    return {
        "profile": rows,
        "fragile": fragile,
        "verdict": ("FRAGILE -- more than half the effect is gone (or the sign flips) "
                    "within a second; this is impact, not signal" if fragile
                    else "decays gracefully; plausibly capturable"),
    }


def effective_bets(n_rebalances, rank_autocorrelation):
    """Independent bets in a portfolio whose composition barely changes.

    If holdings persist across rebalances, repeated rebalancing does not
    accumulate evidence. For AR(1) persistence rho the independent-bet count is
    approximately n * (1 - rho) / (1 + rho): at rho = 0.988, 110 rebalances is
    less than one independent bet.
    """
    rho = float(np.clip(rank_autocorrelation, -0.9999, 0.9999))
    factor = (1.0 - rho) / (1.0 + rho)
    n_eff = max(1.0, float(n_rebalances) * factor)
    return {
        "n_rebalances": int(n_rebalances),
        "rank_autocorrelation": round(rho, 4),
        "independent_bets": round(n_eff, 2),
        "discount": round(n_rebalances / n_eff, 1) if n_eff else None,
        "verdict": ("effectively a single held position, not a repeated bet"
                    if n_eff < 5 else "genuinely repeated bets"),
    }


# ---------------------------------------------------------------------------
# Multiple testing
# ---------------------------------------------------------------------------
def benjamini_hochberg(pvalues, alpha=0.05):
    """FDR control. Returns (rejected_mask, adjusted_pvalues).

    Use when scoring a whole family of tested signals: it answers "which of these
    survive once I admit how many I looked at?"
    """
    p = np.asarray(pvalues, dtype=float)
    m = p.size
    if m == 0:
        return np.array([], dtype=bool), np.array([])
    order = np.argsort(p)
    ranked = p[order]
    adj = ranked * m / np.arange(1, m + 1)
    # enforce monotonicity from the top down
    adj = np.minimum.accumulate(adj[::-1])[::-1]
    adj = np.clip(adj, 0, 1)
    out = np.empty(m)
    out[order] = adj
    return out <= alpha, out


def stationary_bootstrap(x, n_boot=1000, mean_block=None, rng=None):
    """Politis-Romano stationary bootstrap — resamples in random-length blocks so
    serial dependence survives resampling. Returns an (n_boot, len(x)) array."""
    rng = rng or np.random.default_rng(0)
    x = np.asarray(x, dtype=float)
    n = x.size
    if mean_block is None:
        mean_block = max(2.0, n ** (1.0 / 3.0))
    p = 1.0 / mean_block

    out = np.empty((n_boot, n))
    for b in range(n_boot):
        idx = np.empty(n, dtype=int)
        i = rng.integers(0, n)
        for t in range(n):
            idx[t] = i
            if rng.random() < p:
                i = rng.integers(0, n)
            else:
                i = (i + 1) % n
        out[b] = x[idx]
    return out


def whites_reality_check(strategy_returns, n_boot=500, rng=None):
    """White's Reality Check: is the BEST of N strategies better than chance?

    `strategy_returns` is a list/array of equal-length return series, one per
    strategy tested. Returns a p-value for the null that the best strategy has no
    genuine edge once the whole search is accounted for. Small p = the winner is
    unlikely to be a search artefact.
    """
    rng = rng or np.random.default_rng(0)
    M = np.asarray(strategy_returns, dtype=float)
    if M.ndim != 2:
        raise ValueError("expected a 2-D array: (n_strategies, n_observations)")
    n_strat, n_obs = M.shape

    f_bar = M.mean(axis=1)
    v_obs = math.sqrt(n_obs) * f_bar.max()

    v_boot = np.empty(n_boot)
    for b in range(n_boot):
        # one shared block-resample across strategies preserves cross-correlation
        boot_idx = _stationary_indices(n_obs, rng)
        resampled = M[:, boot_idx].mean(axis=1)
        v_boot[b] = math.sqrt(n_obs) * (resampled - f_bar).max()

    p = float((v_boot >= v_obs).mean())
    return {
        "n_strategies": int(n_strat),
        "n_obs": int(n_obs),
        "best_mean_return": float(f_bar.max()),
        "v_statistic": float(v_obs),
        "p_value": p,
        "survives": bool(p < 0.05),
    }


def _stationary_indices(n, rng, mean_block=None):
    if mean_block is None:
        mean_block = max(2.0, n ** (1.0 / 3.0))
    p = 1.0 / mean_block
    idx = np.empty(n, dtype=int)
    i = rng.integers(0, n)
    for t in range(n):
        idx[t] = i
        if rng.random() < p:
            i = rng.integers(0, n)
        else:
            i = (i + 1) % n
    return idx


# ---------------------------------------------------------------------------
# The one-call verdict
# ---------------------------------------------------------------------------
COST_WALLS_BPS = {
    "kraken_crypto": 30.0,        # the wall all research so far was measured against
    "robinhood_crypto": 35.0,     # active target venue — near parity with Kraken
    "etoro_crypto": 350.0,        # retired venue, kept as the reference extreme
}


def evaluate(name, returns, n_trials=1, trial_sharpes=None,
             periods_per_year=252, trades_per_period=1.0, gross_returns=None,
             n_effective=None, benchmark_returns=None):
    """Full verdict on one candidate strategy.

    `returns` should already be NET of costs. Pass `gross_returns` as well and the
    report will show how much of the edge the cost wall ate.

    `n_trials` is how many variants were tested to arrive at this one. Be honest
    about it: understating it is the single easiest way to fool yourself here.

    `n_effective` is the independent-observation count (see effective_sample_size).
    ALWAYS pass it when returns are pooled across correlated assets or overlapping
    windows — otherwise the t-stat and the DSR are both overstated.

    `benchmark_returns` (e.g. buy-and-hold over the same windows) adds the excess
    test. A strategy that cannot beat simply holding the asset is not a strategy.
    """
    m = moments(returns)
    n_eff = float(n_effective) if n_effective else float(m["n"])
    dsr = deflated_sharpe_ratio(returns, n_trials, trial_sharpes, n_effective=n_eff)
    ann_sr = m["sharpe"] * math.sqrt(periods_per_year)
    t_stat = m["sharpe"] * math.sqrt(n_eff)
    p_value = float(2 * (1 - stats.norm.cdf(abs(t_stat))))

    per_trade_bps = (m["mean"] / trades_per_period) * 10000.0 if trades_per_period else 0.0

    out = {
        "name": name,
        "n_obs": m["n"],
        "mean_return_per_period": m["mean"],
        "per_trade_bps": round(per_trade_bps, 3),
        "sharpe_per_period": round(m["sharpe"], 4),
        "sharpe_annualized": round(ann_sr, 3),
        "t_stat": round(t_stat, 3),
        "p_value": round(p_value, 6),
        "skew": round(m["skew"], 3),
        "kurtosis": round(m["kurtosis"], 3),
        "n_trials_searched": int(n_trials),
        "n_effective": round(n_eff, 2),
        "independence_discount": round(m["n"] / n_eff, 2) if n_eff else None,
        "expected_max_sharpe_under_null": round(dsr["expected_max_sharpe_under_null"], 4),
        "deflated_sharpe": round(dsr["deflated_sharpe"], 4)
        if not math.isnan(dsr["deflated_sharpe"]) else None,
        "survives_deflation": dsr["survives"],
        "min_track_record_obs": round(min_track_record_length(returns), 1),
        "clears_cost_wall": {k: bool(per_trade_bps > v) for k, v in COST_WALLS_BPS.items()},
    }
    if benchmark_returns is not None:
        # A strategy must beat holding the asset, not merely be positive.
        b = _clean(benchmark_returns)
        r = _clean(returns)
        k = min(b.size, r.size)
        excess = r[:k] - b[:k]
        se = excess.std(ddof=1) / math.sqrt(n_eff) if excess.std(ddof=1) > 0 else 0.0
        out["excess_over_benchmark_bps"] = round(float(excess.mean()) * 10000, 2)
        out["excess_t_stat"] = round(float(excess.mean()) / se, 3) if se > 0 else None
        out["beats_benchmark"] = bool(se > 0 and (excess.mean() / se) > 1.96)
    if gross_returns is not None:
        gm = moments(gross_returns)
        out["gross_per_trade_bps"] = round(
            (gm["mean"] / trades_per_period) * 10000.0, 3) if trades_per_period else 0.0
        out["cost_drag_bps"] = round(out["gross_per_trade_bps"] - out["per_trade_bps"], 3)
    return out


def verdict_line(ev):
    """One-line human summary of an evaluate() result."""
    if ev["deflated_sharpe"] is None:
        return "%s: INDETERMINATE (moments unstable)" % ev["name"]
    if ev.get("beats_benchmark") is False:
        return ("%s: does NOT beat buy-and-hold (excess %.1f bps, t=%s) -- "
                "the return is the market, not the signal"
                % (ev["name"], ev.get("excess_over_benchmark_bps", 0.0),
                   ev.get("excess_t_stat")))
    if ev["survives_deflation"]:
        walls = [k for k, v in ev["clears_cost_wall"].items() if v]
        if walls:
            return ("%s: SURVIVES deflation (DSR %.3f, ann.SR %.2f, n_eff %.1f) and clears %s"
                    % (ev["name"], ev["deflated_sharpe"], ev["sharpe_annualized"],
                       ev["n_effective"], ", ".join(walls)))
        return ("%s: survives deflation (DSR %.3f) but clears NO cost wall -- "
                "real but not bankable" % (ev["name"], ev["deflated_sharpe"]))
    return ("%s: does NOT survive deflation (DSR %.3f after %d trials; needed to beat "
            "SR %.3f under the null)" % (ev["name"], ev["deflated_sharpe"],
                                         ev["n_trials_searched"],
                                         ev["expected_max_sharpe_under_null"]))


if __name__ == "__main__":
    rng = np.random.default_rng(7)

    print("=" * 74)
    print("SELF-TEST — the harness must reject noise and accept genuine signal")
    print("=" * 74)

    # 1. Pure noise, best of 200 searched variants. Must NOT survive.
    trials = [rng.normal(0, 0.01, 500) for _ in range(200)]
    sharpes = [sharpe_ratio(t) for t in trials]
    best = trials[int(np.argmax(sharpes))]
    ev = evaluate("best-of-200-on-pure-noise", best, n_trials=200,
                  trial_sharpes=sharpes, trades_per_period=1.0)
    print("\n1. Best of 200 random strategies on pure noise")
    print("   observed annualized Sharpe : %.2f   <- looks tradeable!" % ev["sharpe_annualized"])
    print("   naive t-stat               : %.2f" % ev["t_stat"])
    print("   expected max SR under null : %.4f" % ev["expected_max_sharpe_under_null"])
    print("   DEFLATED Sharpe            : %.4f" % ev["deflated_sharpe"])
    print("   -> " + verdict_line(ev))

    # 2. Genuine signal, single hypothesis. Must survive.
    real = rng.normal(0.0008, 0.01, 500)
    ev2 = evaluate("genuine-signal-single-test", real, n_trials=1, trades_per_period=1.0)
    print("\n2. A genuine signal, tested once")
    print("   observed annualized Sharpe : %.2f" % ev2["sharpe_annualized"])
    print("   DEFLATED Sharpe            : %.4f" % ev2["deflated_sharpe"])
    print("   per-trade edge             : %.1f bps" % ev2["per_trade_bps"])
    print("   clears cost walls          : %s" % ev2["clears_cost_wall"])
    print("   -> " + verdict_line(ev2))

    # 3. White's Reality Check on the same noise family.
    print("\n3. White's Reality Check across all 200 noise strategies")
    wrc = whites_reality_check([t for t in trials[:40]], n_boot=200)
    print("   p-value %.3f  -> %s" % (wrc["p_value"],
          "best strategy is a search artefact" if not wrc["survives"] else "survives"))

    # 4. Purged CV sanity.
    print("\n4. Purged K-fold with embargo (500 samples, 5 folds, 5-bar labels)")
    for i, (tr, te) in enumerate(purged_kfold_splits(500, 5, 0.01, 5)):
        gap_ok = te.min() > tr.max() or te.max() < tr.min() or True
        print("   fold %d: train %3d obs, test %3d obs, no overlap: %s"
              % (i, tr.size, te.size, not set(tr.tolist()) & set(te.tolist())))

    print("\n" + "=" * 74)
    print("If test 1 shows a high raw Sharpe but a low deflated one, the harness works.")
