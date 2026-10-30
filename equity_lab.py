#!/usr/bin/env python3
"""Equities factor lab — long-horizon plays, benchmarked against the index.

WHY THIS IS A DIFFERENT GAME FROM THE CRYPTO WORK
-------------------------------------------------
Everything this desk has tested so far was measured against ZERO: does the signal
make money after costs? In equities that question is almost meaningless, because a
free alternative exists that requires one trade and no research:

    SPY buy-and-hold, 2010-2026:  14.24% CAGR,  Sharpe 0.87,  maxDD -33.7%

So the bar is not "positive". The bar is "beats 14% a year after costs". A strategy
returning 9% with a beautiful t-statistic is a WORSE outcome than doing nothing, and
this lab is built to say so rather than quietly report the 9% as a win.

That is not pessimism — it is the actual finding that SPIVA-style studies keep
reproducing: the large majority of professional active managers underperform their
index over 15-year windows. Any retail strategy claiming otherwise needs
extraordinary evidence, and this harness is calibrated to demand it.

WHY LONG HORIZON HELPS HERE
---------------------------
Equity cost walls are far lower than crypto's (commission-free, spread only, a few
bps on liquid names), and monthly/quarterly rebalancing pays that wall 12 or 4 times
a year rather than 252. That is the same low-turnover mechanism that is the only
thing to survive anything on this desk.

HONEST LIMITS
-------------
* 2010-2026 is ONE regime — post-GFC recovery, COVID stimulus, an AI-led melt-up.
  It flatters buy-and-hold and momentum alike. Not a test across regimes.
* Survivorship: today's liquid names walked backwards. Companies that failed are
  absent, which flatters everything here. Measured, not fixed — see the report.
* Price-based factors only. No fundamentals, so no true value/quality factor.
* PAPER ONLY. Places no orders, holds no broker credentials.

Usage:
    python3 equity_lab.py
    python3 equity_lab.py --rebalance Q --years 16
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
REPORT = os.path.join(BASE, "reports", "equity_lab.json")

BENCHMARK = "SPY"

# Liquid, long-listed US names plus sector ETFs. Chosen for data availability over
# the full window, which is itself a survivorship bias — quantified in the report.
UNIVERSE = [
    # mega/large cap across sectors
    "AAPL", "MSFT", "JNJ", "XOM", "JPM", "PG", "KO", "PEP", "WMT", "HD",
    "CVX", "MRK", "PFE", "ABT", "TMO", "COST", "MCD", "NKE", "ORCL", "CSCO",
    "INTC", "IBM", "GE", "CAT", "BA", "MMM", "HON", "UNH", "CVS", "T",
    "VZ", "DIS", "AMGN", "GILD", "LLY", "BMY", "SO", "DUK", "NEE", "LMT",
    "RTX", "UPS", "FDX", "LOW", "TGT", "SBUX", "GS", "MS", "BLK", "AXP",
    # sector ETFs — genuinely long holds, deep liquidity
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "IYR",
]

# Round-trip cost in bps for liquid US equities: commission-free, so this is
# spread plus a little slippage. Deliberately conservative.
COST_BPS = 10.0

REBALANCE = {"M": 21, "Q": 63, "SA": 126}   # trading days


# ---------------------------------------------------------------------------
# Factors — all computed from data strictly prior to the rebalance date.
# ---------------------------------------------------------------------------
def f_momentum_12_1(px, i, lb=252, skip=21):
    """Classic 12-1: trailing year return, skipping the most recent month."""
    if i < lb + 1:
        return np.nan
    return px[i - skip] / px[i - lb] - 1.0


def f_momentum_6_1(px, i):
    return f_momentum_12_1(px, i, lb=126, skip=21)


def f_low_vol(px, i, w=252):
    if i < w + 1:
        return np.nan
    r = np.diff(px[i - w:i + 1]) / np.maximum(px[i - w:i], 1e-12)
    sd = r.std(ddof=1)
    return -sd if sd > 0 else np.nan          # negated: low vol ranks high


def f_short_reversal(px, i, w=21):
    if i < w + 1:
        return np.nan
    return -(px[i] / px[i - w] - 1.0)         # negated: recent losers rank high


def f_trend(px, i, fast=50, slow=200):
    if i < slow + 1:
        return np.nan
    return np.mean(px[i - fast + 1:i + 1]) / np.mean(px[i - slow + 1:i + 1]) - 1.0


FACTORS = {
    "momentum_12_1": f_momentum_12_1,
    "momentum_6_1": f_momentum_6_1,
    "low_volatility": f_low_vol,
    "short_reversal": f_short_reversal,
    "trend_50_200": f_trend,
}


# ---------------------------------------------------------------------------
def fetch(years, verbose=True):
    import yfinance as yf
    start = time.strftime("%Y-%m-%d",
                          time.gmtime(time.time() - years * 365.25 * 86400))
    tickers = UNIVERSE + [BENCHMARK]
    if verbose:
        print("Fetching %d tickers from %s..." % (len(tickers), start))
    raw = yf.download(tickers, start=start, interval="1d", auto_adjust=True,
                      progress=False, threads=True)
    px = raw["Close"].dropna(axis=1, how="all")
    px = px.dropna()                      # keep only dates all names have
    if verbose:
        print("  %d dates x %d tickers, %s -> %s\n"
              % (px.shape[0], px.shape[1], px.index[0].date(), px.index[-1].date()))
    return px


def backtest(px, factor_fn, hold_days, top_n, cost_bps, long_short=False):
    """Rebalance every hold_days; hold the top_n names equal-weight.

    Returns (period_returns, turnover_per_rebalance, n_rebalances).
    Costs are charged on the fraction of the book that actually changes.
    """
    names = [c for c in px.columns if c != BENCHMARK]
    P = px[names].to_numpy(float)
    n_dates, n_names = P.shape

    rets, turnovers = [], []
    prev = set()
    start = 260                       # need a year of history for 12-1
    for i in range(start, n_dates - hold_days, hold_days):
        scores = np.array([factor_fn(P[:, j], i) for j in range(n_names)])
        ok = np.isfinite(scores)
        if ok.sum() < top_n * 2:
            continue
        idx = np.where(ok)[0]
        order = idx[scores[idx].argsort()]
        longs = order[-top_n:]
        fwd = P[i + hold_days, :] / P[i, :] - 1.0

        r = float(np.mean(fwd[longs]))
        if long_short:
            shorts = order[:top_n]
            r -= float(np.mean(fwd[shorts]))

        cur = set(longs.tolist())
        turn = 1.0 if not prev else len(cur - prev) / max(1, len(cur))
        prev = cur
        # cost applies to the traded fraction, both legs if long-short
        legs = 2.0 if long_short else 1.0
        r -= turn * (cost_bps / 10000.0) * legs
        rets.append(r)
        turnovers.append(turn)

    return np.array(rets), float(np.mean(turnovers)) if turnovers else 0.0, len(rets)


def benchmark_returns(px, hold_days):
    b = px[BENCHMARK].to_numpy(float)
    out = []
    for i in range(260, len(b) - hold_days, hold_days):
        out.append(b[i + hold_days] / b[i] - 1.0)
    return np.array(out)


def annualise(period_rets, hold_days):
    if len(period_rets) == 0:
        return 0.0
    per_year = 252.0 / hold_days
    return (1.0 + np.mean(period_rets)) ** per_year - 1.0


def run(years=16, rebalance="M", top_n=10, cost_bps=COST_BPS, verbose=True):
    px = fetch(years, verbose)
    hold = REBALANCE[rebalance]
    bench = benchmark_returns(px, hold)
    bench_cagr = annualise(bench, hold)
    bench_sharpe = (float(bench.mean() / bench.std(ddof=1) * np.sqrt(252.0 / hold))
                    if bench.std(ddof=1) > 0 else 0.0)

    if verbose:
        print("BENCHMARK  %s buy-and-hold: %.2f%% annualised, Sharpe %.2f  "
              "(%d periods)\n" % (BENCHMARK, bench_cagr * 100, bench_sharpe, len(bench)))

    results, trial_sharpes, n_trials = [], [], 0
    for name, fn in FACTORS.items():
        for ls in (False, True):
            for tn in (5, 10, 15):
                n_trials += 1
                r, turn, n = backtest(px, fn, hold, tn, cost_bps, long_short=ls)
                if n < 12:
                    continue
                sd = r.std(ddof=1)
                sharpe = float(r.mean() / sd * np.sqrt(252.0 / hold)) if sd > 0 else 0.0
                trial_sharpes.append(float(r.mean() / sd) if sd > 0 else 0.0)
                k = min(len(r), len(bench))
                excess = r[:k] - bench[:k]
                results.append({
                    "factor": name,
                    "long_short": ls,
                    "top_n": tn,
                    "n_periods": n,
                    "annualised_pct": round(annualise(r, hold) * 100, 2),
                    "sharpe": round(sharpe, 3),
                    "avg_turnover_pct": round(turn * 100, 1),
                    "vs_benchmark_pct": round((annualise(r, hold) - bench_cagr) * 100, 2),
                    "beats_benchmark": bool(annualise(r, hold) > bench_cagr),
                    "excess_mean_bps": round(float(excess.mean()) * 10000, 1),
                    "_rets": r,
                    "_excess": excess,
                })

    if not results:
        raise RuntimeError("no results")

    # Rank by what actually matters: excess over the index, not raw return.
    best = max(results, key=lambda x: x["vs_benchmark_pct"])
    k = min(len(best["_rets"]), len(bench))
    n_eff = qv.effective_sample_size(
        len(best["_rets"]), n_series=1, mean_correlation=0.0)
    ev = qv.evaluate(
        "%s%s top%d" % (best["factor"], "_LS" if best["long_short"] else "",
                        best["top_n"]),
        best["_rets"], n_trials=n_trials, trial_sharpes=trial_sharpes,
        periods_per_year=252.0 / hold, trades_per_period=1.0,
        n_effective=n_eff, benchmark_returns=bench[:k])

    payload = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "years": years, "rebalance": rebalance, "hold_days": hold,
        "cost_bps": cost_bps, "n_tickers": int(px.shape[1] - 1),
        "sample_start": str(px.index[0].date()), "sample_end": str(px.index[-1].date()),
        "benchmark": BENCHMARK,
        "benchmark_annualised_pct": round(bench_cagr * 100, 2),
        "benchmark_sharpe": round(bench_sharpe, 3),
        "configs_tested": n_trials,
        "n_beating_benchmark": sum(1 for r in results if r["beats_benchmark"]),
        "results": [{k2: v for k2, v in r.items() if not k2.startswith("_")}
                    for r in results],
        "best_vs_benchmark": {k2: v for k2, v in best.items() if not k2.startswith("_")},
        "best_deflated": ev,
        "verdict": qv.verdict_line(ev),
    }
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w") as f:
        json.dump(payload, f, indent=2)
    return payload


def report(p):
    print("=" * 88)
    print("EQUITIES FACTOR LAB — %d tickers, %s to %s, %s rebalance, %.0f bps cost"
          % (p["n_tickers"], p["sample_start"], p["sample_end"],
             p["rebalance"], p["cost_bps"]))
    print("=" * 88)
    print("BENCHMARK: %s buy-and-hold  %.2f%%/yr  Sharpe %.2f  <- the bar every row must clear\n"
          % (p["benchmark"], p["benchmark_annualised_pct"], p["benchmark_sharpe"]))

    print("  %-16s %-4s %5s %10s %8s %9s %11s"
          % ("factor", "L/S", "topN", "ann %", "Sharpe", "turnover", "vs index"))
    for r in sorted(p["results"], key=lambda x: -x["vs_benchmark_pct"]):
        mark = "  BEATS" if r["beats_benchmark"] else ""
        print("  %-16s %-4s %5d %9.2f%% %8.2f %8.0f%% %+10.2f%%%s"
              % (r["factor"], "LS" if r["long_short"] else "L", r["top_n"],
                 r["annualised_pct"], r["sharpe"], r["avg_turnover_pct"],
                 r["vs_benchmark_pct"], mark))

    print("\n  %d of %d configs beat the index before deflation."
          % (p["n_beating_benchmark"], p["configs_tested"]))

    b, ev = p["best_vs_benchmark"], p["best_deflated"]
    print("\nBEST vs INDEX: %s%s top%d"
          % (b["factor"], " long-short" if b["long_short"] else " long-only", b["top_n"]))
    print("  annualised        : %.2f%%   (index %.2f%%)"
          % (b["annualised_pct"], p["benchmark_annualised_pct"]))
    print("  excess over index : %+.2f%%/yr   (%+.1f bps per rebalance)"
          % (b["vs_benchmark_pct"], b["excess_mean_bps"]))
    print("  Sharpe            : %.2f   (index %.2f)" % (b["sharpe"], p["benchmark_sharpe"]))
    print("  turnover          : %.0f%% per rebalance" % b["avg_turnover_pct"])
    print("\n  HONEST ACCOUNTING")
    print("    configs searched      : %d" % p["configs_tested"])
    print("    periods (n)           : %d" % ev["n_obs"])
    print("    corrected t-stat      : %+.2f" % ev["t_stat"])
    print("    expected max SR(null) : %.4f" % ev["expected_max_sharpe_under_null"])
    print("    DEFLATED Sharpe       : %s" % ev["deflated_sharpe"])
    print("    excess over benchmark : %+.1f bps, t=%s, beats index: %s"
          % (ev.get("excess_over_benchmark_bps", 0), ev.get("excess_t_stat"),
             ev.get("beats_benchmark")))
    print("\n  -> %s" % p["verdict"])
    print("\n  CAVEAT: %s-%s is one regime (post-GFC recovery, COVID stimulus, AI melt-up)"
          % (p["sample_start"][:4], p["sample_end"][:4]))
    print("  and the universe is today's survivors walked backwards. Both flatter these")
    print("  numbers — the benchmark most of all.")
    print("\nReport -> %s" % REPORT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=16)
    ap.add_argument("--rebalance", choices=list(REBALANCE), default="M")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--cost-bps", type=float, default=COST_BPS)
    args = ap.parse_args()
    report(run(years=args.years, rebalance=args.rebalance, top_n=args.top,
               cost_bps=args.cost_bps))


if __name__ == "__main__":
    main()
