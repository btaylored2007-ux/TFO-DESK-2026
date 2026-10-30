#!/usr/bin/env python3
"""Natural-resources & shorts lab — the extractive complex, and betting against the market.

TWO QUESTIONS, BOTH ANSWERED WITH DATA
--------------------------------------
1. MINING / NATURAL RESOURCES: does momentum WITHIN the resources complex (gold
   and silver miners, copper, uranium, energy, steel, agri, timber, rare earths)
   add anything beyond what the Growth Book already harvests? The Growth Book's
   universe holds commodity WRAPPERS (GLD SLV DBC USO); this tests the operating
   COMPANIES — leveraged plays on the same commodities with equity beta attached.

2. SHORTS: "betting against the market" tested honestly. Three forms:
     - short the weakest resources names (relative bet)
     - short SPY whenever it is below its 200-day average (the classic bear rule)
     - versus the defensive alternative: simply going to CASH below the 200-day
   The 36-year lab already showed trend's value comes from stepping ASIDE, not
   betting against. This tests whether the short leg ever pays for itself.

Costs 10 bps/side on turnover. Monthly evaluation. Long-only fallback is cash at
0%. Short borrow cost is NOT modelled — which flatters every short row, so a short
strategy that fails here fails a fortiori in reality.

PAPER ONLY. Research, not advice.

Usage:  python3 resources_lab.py
"""
import json
import os
import time
import warnings

import numpy as np

import quant_validation as qv

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(BASE, "reports", "resources_lab.json")

RESOURCES = {
    "GDX": "Gold miners", "SIL": "Silver miners", "COPX": "Copper miners",
    "XME": "Metals & mining", "XLE": "Energy majors", "XOP": "Oil & gas E&P",
    "URA": "Uranium", "MOO": "Agribusiness", "WOOD": "Timber",
    "LIT": "Lithium", "REMX": "Rare earths", "PICK": "Global miners",
}
BENCH = "SPY"
HOLD = 21
COST_BPS = 10.0
LOOKBACK = 252


def fetch():
    import yfinance as yf
    ticks = list(RESOURCES) + [BENCH]
    raw = yf.download(ticks, start="2012-01-01", interval="1d", auto_adjust=True,
                      progress=False, threads=True)
    return raw["Close"].dropna()


def run(verbose=True):
    if verbose:
        print("Fetching %d resources ETFs + %s from 2012..." % (len(RESOURCES), BENCH))
    px = fetch()
    if verbose:
        print("  %d dates x %d tickers, %s -> %s\n"
              % (px.shape[0], px.shape[1], px.index[0].date(), px.index[-1].date()))

    names = [c for c in px.columns if c != BENCH]
    P = px[names].to_numpy(float)
    S = px[BENCH].to_numpy(float)
    n = len(P)
    cost = COST_BPS / 10000.0

    def periods():
        return range(LOOKBACK + 5, n - HOLD, HOLD)

    def fwd(i):
        return P[i + HOLD] / P[i] - 1.0

    def sfwd(i):
        return S[i + HOLD] / S[i] - 1.0

    strategies = {}

    # benchmarks
    strategies["bh_SPY"] = [sfwd(i) for i in periods()]
    strategies["bh_equal_resources"] = [float(np.mean(fwd(i))) for i in periods()]

    # dual momentum within resources (long-only, positive filter, cash fallback)
    for top in (2, 3):
        rets, prev = [], set()
        for i in periods():
            trail = P[i] / P[i - LOOKBACK] - 1.0
            order = np.argsort(trail)[::-1]
            picks = [j for j in order[:top] if trail[j] > 0]
            r = float(np.mean(fwd(i)[picks])) if picks else 0.0
            cur = set(picks)
            turn = 1.0 if not prev else len(cur - prev) / max(1, top)
            prev = cur
            rets.append(r - turn * cost)
        strategies["res_dual_mom_top%d" % top] = rets

    # trend filter long (each above its 200dma slice, else cash)
    rets = []
    for i in periods():
        above = P[i] > np.array([P[max(0, i - 200):i, j].mean() for j in range(len(names))])
        r = float(np.mean(fwd(i)[above])) * (above.sum() / len(names)) if above.any() else 0.0
        rets.append(r - cost * 0.1)
    strategies["res_trend_long"] = rets

    # SHORT the weakest 2 resources names (borrow cost unmodelled — flattered)
    rets, prev = [], set()
    for i in periods():
        trail = P[i] / P[i - LOOKBACK] - 1.0
        worst = list(np.argsort(trail)[:2])
        r = -float(np.mean(fwd(i)[worst]))
        cur = set(worst)
        turn = 1.0 if not prev else len(cur - prev) / 2.0
        prev = cur
        rets.append(r - turn * cost * 2)
    strategies["res_short_worst2"] = rets

    # bet against the market: short SPY below its 200dma...
    rets_short, rets_cash = [], []
    for i in periods():
        below = S[i] < S[max(0, i - 200):i].mean()
        rets_short.append((-sfwd(i) - cost) if below else 0.0)
        rets_cash.append(0.0 if below else sfwd(i))
    strategies["short_SPY_below_200dma"] = rets_short
    strategies["cash_SPY_below_200dma"] = rets_cash     # the defensive alternative

    # score
    bench_arr = np.array(strategies["bh_SPY"])
    rows, trial_sharpes, series = [], [], {}
    NONBENCH = [k for k in strategies if not k.startswith("bh_")]
    for name, r in strategies.items():
        a = np.array(r)
        series[name] = a
        sd = a.std(ddof=1)
        ann = (1 + a.mean()) ** (252.0 / HOLD) - 1
        eq = np.cumprod(1 + a)
        rows.append({
            "strategy": name,
            "annualised_pct": round(ann * 100, 2),
            "sharpe": round(float(a.mean() / sd * np.sqrt(252.0 / HOLD)), 3) if sd > 0 else 0.0,
            "max_drawdown_pct": round(float((eq / np.maximum.accumulate(eq) - 1).min()) * 100, 1),
            "vs_SPY_pct": round((ann - ((1 + bench_arr.mean()) ** (252.0 / HOLD) - 1)) * 100, 2),
            "n_periods": len(a),
            "benchmark": name.startswith("bh_"),
        })
        if name in NONBENCH:
            trial_sharpes.append(float(a.mean() / sd) if sd > 0 else 0.0)

    best = max((x for x in rows if not x["benchmark"]), key=lambda x: x["sharpe"])
    a = series[best["strategy"]]
    k = min(len(a), len(bench_arr))
    ev = qv.evaluate(best["strategy"], a, n_trials=len(NONBENCH),
                     trial_sharpes=trial_sharpes, periods_per_year=252.0 / HOLD,
                     trades_per_period=1.0, benchmark_returns=bench_arr[:k])

    payload = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sample": [str(px.index[0].date()), str(px.index[-1].date())],
        "universe": RESOURCES, "cost_bps": COST_BPS,
        "configs_tested": len(NONBENCH),
        "results": rows, "best": best, "best_deflated": ev,
        "verdict": qv.verdict_line(ev),
        "note_shorts": "Short borrow costs NOT modelled — short rows are flattered.",
    }
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w") as f:
        json.dump(payload, f, indent=2)
    return payload


def report(p):
    print("=" * 92)
    print("RESOURCES & SHORTS LAB — %s to %s, %d strategies (%s)"
          % (p["sample"][0], p["sample"][1], p["configs_tested"],
             ", ".join(p["universe"])))
    print("=" * 92)
    print("  %-26s %9s %8s %10s %10s" % ("strategy", "ann %", "Sharpe", "maxDD %", "vs SPY"))
    for r in sorted(p["results"], key=lambda x: -x["sharpe"]):
        tag = "  <-- benchmark" if r["benchmark"] else ""
        print("  %-26s %8.2f%% %8.2f %9.1f%% %+9.2f%%%s"
              % (r["strategy"], r["annualised_pct"], r["sharpe"],
                 r["max_drawdown_pct"], r["vs_SPY_pct"], tag))
    ev = p["best_deflated"]
    print("\nBest non-benchmark: %s — DSR %s, beats SPY: %s (excess t=%s)"
          % (p["best"]["strategy"], ev["deflated_sharpe"],
             ev.get("beats_benchmark"), ev.get("excess_t_stat")))
    print("  -> %s" % p["verdict"])
    print("  NOTE: %s" % p["note_shorts"])
    print("\nReport -> %s" % REPORT)


if __name__ == "__main__":
    report(run())
