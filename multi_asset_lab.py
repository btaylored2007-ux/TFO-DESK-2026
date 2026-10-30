#!/usr/bin/env python3
"""All-markets allocation lab — every asset class, long horizon, honestly benchmarked.

THE HYPOTHESIS BEING TESTED
---------------------------
The equity lab found that five price-based stock-picking factors all collapsed to
zero once market beta was stripped out (long-short returns of +1.6% to -12%). The
long-only versions "worked" only because they held equities in a bull market.

That points somewhere specific: if picking WITHIN an asset class adds nothing, the
decision that matters is WHICH ASSET CLASSES YOU HOLD AND IN WHAT PROPORTION. That
is the allocation question, and it is the one thing in finance with a large,
replicated, decades-long evidence base behind it.

So this lab drops stock-picking entirely and tests allocation across every market
Robinhood gives exposure to, using liquid ETF proxies.

WHAT IS BEING COMPARED
----------------------
Two benchmarks, because one is not enough:
  * SPY buy-and-hold   -- the "just buy the index" alternative
  * 60/40 (SPY/AGG)    -- the actual default a sensible person would hold

A strategy has to beat BOTH, after costs, to be worth running. And it has to beat
them on RISK-ADJUSTED terms: a strategy returning more with a far worse drawdown is
not better, it is just more levered to the same bet.

WHY DRAWDOWN IS A FIRST-CLASS METRIC HERE
-----------------------------------------
SPY's max drawdown over this sample is -33.7%. On a A$50,000 book that is A$16,850
gone. Every headline return in this report should be read next to its drawdown, and
the report prints them together for exactly that reason.

HONEST LIMITS
-------------
* The sample is one regime: post-GFC recovery, COVID stimulus, an AI-led melt-up,
  and a historically unusual bond bear market inside it. It flatters equities and
  punishes bonds relative to their long-run behaviour.
* ETF proxies are not the underlying markets. Futures roll costs, options premia and
  prediction-market spreads are NOT modelled.
* Options and event contracts are excluded deliberately: retail options are
  structurally negative-sum after spread and premium decay, and event-contract
  history is not available from any free feed. Modelling them badly would be worse
  than omitting them honestly.
* PAPER ONLY. Places no orders, holds no broker credentials.

Usage:
    python3 multi_asset_lab.py
    python3 multi_asset_lab.py --rebalance Q --years 16
"""
import argparse
import json
import os
import time
import warnings

import numpy as np

import quant_validation as qv

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(BASE, "reports", "multi_asset_lab.json")

# Every asset class Robinhood gives retail exposure to, via the most liquid,
# longest-listed ETF proxy for each.
ASSETS = {
    "SPY":  "US large-cap equity",
    "QQQ":  "US tech / growth",
    "IWM":  "US small-cap equity",
    "EFA":  "Developed international equity",
    "EEM":  "Emerging-market equity",
    "TLT":  "Long US treasuries",
    "IEF":  "Intermediate US treasuries",
    "LQD":  "Investment-grade credit",
    "HYG":  "High-yield credit",
    "GLD":  "Gold",
    "SLV":  "Silver",
    "DBC":  "Broad commodities",
    "USO":  "Crude oil",
    "IYR":  "US real estate",
    "AGG":  "US aggregate bonds",
}

BENCH_EQUITY = "SPY"
BENCH_6040 = ("SPY", "AGG", 0.6)

COST_BPS = 10.0
REBALANCE = {"M": 21, "Q": 63, "SA": 126, "A": 252}


# ---------------------------------------------------------------------------
def fetch(years, verbose=True):
    import yfinance as yf
    start = time.strftime("%Y-%m-%d", time.gmtime(time.time() - years * 365.25 * 86400))
    tick = list(ASSETS)
    if verbose:
        print("Fetching %d asset-class proxies from %s..." % (len(tick), start))
    raw = yf.download(tick, start=start, interval="1d", auto_adjust=True,
                      progress=False, threads=True)
    px = raw["Close"].dropna(axis=1, how="all").dropna()
    if verbose:
        print("  %d dates x %d assets, %s -> %s\n"
              % (px.shape[0], px.shape[1], px.index[0].date(), px.index[-1].date()))
    return px


# ---------------------------------------------------------------------------
# Allocation rules. Each returns a weight vector over `names` at bar i, using only
# data up to and including i. Weights are non-negative and sum to 1 (long-only,
# which is what a retail cash account can actually do).
# ---------------------------------------------------------------------------
def w_equal(P, i, names, **kw):
    return np.ones(len(names)) / len(names)


def w_spy_only(P, i, names, **kw):
    w = np.zeros(len(names))
    w[names.index("SPY")] = 1.0
    return w


def w_6040(P, i, names, **kw):
    w = np.zeros(len(names))
    w[names.index("SPY")] = 0.6
    w[names.index("AGG")] = 0.4
    return w


def _vols(P, i, w=126):
    if i < w + 1:
        return None
    r = np.diff(P[i - w:i + 1], axis=0) / np.maximum(P[i - w:i, :], 1e-12)
    sd = r.std(axis=0, ddof=1)
    return np.where(sd > 0, sd, np.nan)


def w_risk_parity(P, i, names, **kw):
    """Inverse-volatility weights — the simple, robust form of risk parity."""
    sd = _vols(P, i)
    if sd is None or not np.isfinite(sd).any():
        return w_equal(P, i, names)
    inv = np.where(np.isfinite(sd), 1.0 / sd, 0.0)
    return inv / inv.sum() if inv.sum() > 0 else w_equal(P, i, names)


def w_dual_momentum(P, i, names, top=4, lb=252, **kw):
    """Hold the top-N assets by trailing return, but ONLY those beating cash.

    Absolute momentum (the "is it beating cash" filter) is what turns this from a
    return-chaser into a defensive rule — it goes to bonds when nothing is working.
    """
    if i < lb + 1:
        return w_equal(P, i, names)
    trail = P[i, :] / P[i - lb, :] - 1.0
    order = np.argsort(trail)[::-1]
    picks = [j for j in order[:top] if trail[j] > 0.0]
    w = np.zeros(len(names))
    if not picks:
        w[names.index("IEF")] = 1.0        # nothing beating cash -> duration
        return w
    w[picks] = 1.0 / len(picks)
    return w


def w_trend_overlay(P, i, names, lb=252, **kw):
    """Equal-weight, but each asset is held only when above its own 12m trend.

    Whatever fails the filter goes to short treasuries. This is the classic
    absolute-momentum defensive overlay.
    """
    if i < lb + 1:
        return w_equal(P, i, names)
    above = (P[i, :] / P[i - lb, :] - 1.0) > 0.0
    w = np.zeros(len(names))
    if above.sum() == 0:
        w[names.index("IEF")] = 1.0
        return w
    w[above] = 1.0 / above.sum()
    return w


def w_min_var(P, i, names, w_lb=252, **kw):
    """Minimum-variance via inverse covariance, long-only and normalised.

    Shrunk toward the diagonal because a sample covariance over 15 assets is
    badly conditioned and unshrunk min-var is famous for concentrating into
    whatever asset was quietest by luck.
    """
    if i < w_lb + 1:
        return w_equal(P, i, names)
    r = np.diff(P[i - w_lb:i + 1], axis=0) / np.maximum(P[i - w_lb:i, :], 1e-12)
    C = np.cov(r, rowvar=False)
    C = 0.5 * C + 0.5 * np.diag(np.diag(C))          # shrinkage
    try:
        inv = np.linalg.pinv(C)
        w = inv.sum(axis=1)
    except Exception:
        return w_equal(P, i, names)
    w = np.clip(w, 0, None)
    return w / w.sum() if w.sum() > 0 else w_equal(P, i, names)


STRATEGIES = {
    "buy_hold_SPY": w_spy_only,
    "sixty_forty": w_6040,
    "equal_weight_all": w_equal,
    "risk_parity": w_risk_parity,
    "dual_momentum_top4": w_dual_momentum,
    "trend_overlay": w_trend_overlay,
    "min_variance": w_min_var,
}


# ---------------------------------------------------------------------------
def backtest(px, weight_fn, hold_days, cost_bps, **kw):
    names = list(px.columns)
    P = px.to_numpy(float)
    n = P.shape[0]
    rets, turns = [], []
    prev_w = None
    start = 260
    for i in range(start, n - hold_days, hold_days):
        w = weight_fn(P, i, names, **kw)
        w = np.nan_to_num(w, nan=0.0)
        if w.sum() <= 0:
            continue
        w = w / w.sum()
        fwd = P[i + hold_days, :] / P[i, :] - 1.0
        r = float(np.dot(w, fwd))
        turn = 1.0 if prev_w is None else float(np.abs(w - prev_w).sum() / 2.0)
        prev_w = w
        r -= turn * (cost_bps / 10000.0)
        rets.append(r)
        turns.append(turn)
    return np.array(rets), (float(np.mean(turns)) if turns else 0.0)


def annualise(r, hold_days):
    if len(r) == 0:
        return 0.0
    return (1.0 + np.mean(r)) ** (252.0 / hold_days) - 1.0


def max_drawdown(r):
    if len(r) == 0:
        return 0.0
    eq = np.cumprod(1.0 + r)
    return float((eq / np.maximum.accumulate(eq) - 1.0).min())


def run(years=16, rebalance="Q", cost_bps=COST_BPS, verbose=True):
    px = fetch(years, verbose)
    hold = REBALANCE[rebalance]

    rows, trial_sharpes = [], []
    series = {}
    for name, fn in STRATEGIES.items():
        r, turn = backtest(px, fn, hold, cost_bps)
        if len(r) < 12:
            continue
        sd = r.std(ddof=1)
        ann = annualise(r, hold)
        sharpe = float(r.mean() / sd * np.sqrt(252.0 / hold)) if sd > 0 else 0.0
        series[name] = r
        trial_sharpes.append(float(r.mean() / sd) if sd > 0 else 0.0)
        rows.append({
            "strategy": name,
            "annualised_pct": round(ann * 100, 2),
            "sharpe": round(sharpe, 3),
            "max_drawdown_pct": round(max_drawdown(r) * 100, 1),
            "avg_turnover_pct": round(turn * 100, 1),
            "n_periods": len(r),
            "return_per_unit_dd": round(abs(ann / max_drawdown(r)), 2)
            if max_drawdown(r) < 0 else None,
        })

    spy = series.get("buy_hold_SPY")
    sf = series.get("sixty_forty")
    spy_ann = annualise(spy, hold) if spy is not None else 0.0
    sf_ann = annualise(sf, hold) if sf is not None else 0.0

    for row in rows:
        r = series[row["strategy"]]
        k = min(len(r), len(spy))
        row["vs_spy_pct"] = round((annualise(r, hold) - spy_ann) * 100, 2)
        row["vs_6040_pct"] = round((annualise(r, hold) - sf_ann) * 100, 2)
        row["beats_both"] = bool(row["vs_spy_pct"] > 0 and row["vs_6040_pct"] > 0)

    # The honest winner is the best RISK-ADJUSTED result that is not a benchmark.
    cands = [x for x in rows if x["strategy"] not in ("buy_hold_SPY", "sixty_forty")]
    best = max(cands, key=lambda x: x["sharpe"]) if cands else None

    payload = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "years": years, "rebalance": rebalance, "hold_days": hold,
        "cost_bps": cost_bps,
        "assets": {k: ASSETS[k] for k in px.columns},
        "sample_start": str(px.index[0].date()), "sample_end": str(px.index[-1].date()),
        "benchmark_spy_pct": round(spy_ann * 100, 2),
        "benchmark_6040_pct": round(sf_ann * 100, 2),
        "configs_tested": len(rows),
        "results": rows,
    }

    if best is not None:
        r = series[best["strategy"]]
        k = min(len(r), len(spy))
        ev = qv.evaluate(best["strategy"], r, n_trials=len(rows),
                         trial_sharpes=trial_sharpes,
                         periods_per_year=252.0 / hold, trades_per_period=1.0,
                         benchmark_returns=spy[:k])
        payload["best_risk_adjusted"] = best
        payload["best_deflated"] = ev
        payload["verdict"] = qv.verdict_line(ev)

    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w") as f:
        json.dump(payload, f, indent=2)
    return payload


def report(p):
    print("=" * 92)
    print("ALL-MARKETS ALLOCATION LAB — %d asset classes, %s to %s, %s rebalance, %.0f bps"
          % (len(p["assets"]), p["sample_start"], p["sample_end"],
             p["rebalance"], p["cost_bps"]))
    print("=" * 92)
    print("Asset classes: %s\n" % ", ".join(p["assets"]))
    print("  %-20s %10s %8s %10s %9s %10s %10s"
          % ("strategy", "ann %", "Sharpe", "maxDD %", "ret/DD", "vs SPY", "vs 60/40"))
    for r in sorted(p["results"], key=lambda x: -x["sharpe"]):
        bench = " <-- benchmark" if r["strategy"] in ("buy_hold_SPY", "sixty_forty") else ""
        print("  %-20s %9.2f%% %8.2f %9.1f%% %9s %+9.2f%% %+9.2f%%%s"
              % (r["strategy"], r["annualised_pct"], r["sharpe"],
                 r["max_drawdown_pct"], r["return_per_unit_dd"],
                 r["vs_spy_pct"], r["vs_6040_pct"], bench))

    print("\n  BENCHMARKS:  SPY %.2f%%/yr   60/40 %.2f%%/yr"
          % (p["benchmark_spy_pct"], p["benchmark_6040_pct"]))
    beat = [r for r in p["results"] if r.get("beats_both")
            and r["strategy"] not in ("buy_hold_SPY", "sixty_forty")]
    print("  %d of %d strategies beat BOTH benchmarks on raw return."
          % (len(beat), p["configs_tested"] - 2))

    if "best_risk_adjusted" in p:
        b, ev = p["best_risk_adjusted"], p["best_deflated"]
        print("\nBEST RISK-ADJUSTED (non-benchmark): %s" % b["strategy"])
        print("  annualised %.2f%%   Sharpe %.2f   maxDD %.1f%%   turnover %.0f%%/rebal"
              % (b["annualised_pct"], b["sharpe"], b["max_drawdown_pct"],
                 b["avg_turnover_pct"]))
        print("  vs SPY %+.2f%%/yr    vs 60/40 %+.2f%%/yr"
              % (b["vs_spy_pct"], b["vs_6040_pct"]))
        print("\n  HONEST ACCOUNTING")
        print("    strategies tested     : %d" % p["configs_tested"])
        print("    periods (n)           : %d" % ev["n_obs"])
        print("    t-stat                : %+.2f" % ev["t_stat"])
        print("    DEFLATED Sharpe       : %s" % ev["deflated_sharpe"])
        print("    excess over SPY       : %+.1f bps/period, t=%s, beats it: %s"
              % (ev.get("excess_over_benchmark_bps", 0), ev.get("excess_t_stat"),
                 ev.get("beats_benchmark")))
        print("\n  -> %s" % p["verdict"])

    print("\n  CAVEAT: one regime (%s-%s). It flatters equities and punishes bonds"
          % (p["sample_start"][:4], p["sample_end"][:4]))
    print("  relative to their long-run behaviour. Options and event contracts are")
    print("  excluded deliberately — see the module docstring.")
    print("\nReport -> %s" % REPORT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=16)
    ap.add_argument("--rebalance", choices=list(REBALANCE), default="Q")
    ap.add_argument("--cost-bps", type=float, default=COST_BPS)
    args = ap.parse_args()
    report(run(years=args.years, rebalance=args.rebalance, cost_bps=args.cost_bps))


if __name__ == "__main__":
    main()
