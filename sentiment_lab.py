#!/usr/bin/env python3
"""Sentiment lab — does the crowd's state predict returns? Tested on 8 years of history.

WHY THIS IS ANSWERABLE TODAY WHEN THE NEWS STUDY IS NOT
-------------------------------------------------------
The desk's news/Reddit collectors accumulate forward and need weeks. But the crypto
Fear & Greed index (alternative.me) publishes its COMPLETE daily history back to
2018-02-01 — a composite of volatility, volume, social-media activity, dominance and
trends. ~3,100 days spanning three full regimes: the 2018 bear, the 2020-21 mania,
the 2022 collapse, and everything since. That is enough history to test the
popular claim properly:

    "Buy extreme fear, sell extreme greed."

WHAT DISCIPLINE REQUIRES HERE
-----------------------------
* Signal on day t uses the index printed that day; entry at day-t close. No look-ahead.
* Non-overlapping trades (entry, hold h days, no re-entry until exit).
* Two comparisons, because they answer different questions:
    - conditional vs unconditional: does fear predict ABOVE-AVERAGE forward returns?
      (the scientific question)
    - strategy vs buy-and-hold: would trading it have beaten holding? (the money question)
  In a sample where BTC rose enormously, a long-only rule can have real predictive
  power and still lose to never selling. Both get reported.
* Every threshold/horizon/style combination is counted as a trial and the winner is
  deflated against the full search.
* Single asset (BTC) — no cross-asset pooling, so n_eff is just the trade count.

PAPER ONLY. Research, not advice.

Usage:  python3 sentiment_lab.py
"""
import json
import os
import time
import urllib.request
import warnings

import numpy as np

import quant_validation as qv

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(BASE, "reports", "sentiment_lab.json")

COST_BPS = 30.0
HORIZONS = [1, 5, 10, 21]


def fetch_fng():
    req = urllib.request.Request("https://api.alternative.me/fng/?limit=0&format=json",
                                 headers={"User-Agent": "Mozilla/5.0 (research)"})
    with urllib.request.urlopen(req, timeout=25) as r:
        data = json.load(r)["data"]
    out = {}
    for d in data:
        day = time.strftime("%Y-%m-%d", time.gmtime(int(d["timestamp"])))
        out[day] = float(d["value"])
    return out


def fetch_btc(start="2018-01-25"):
    import yfinance as yf
    px = yf.download("BTC-USD", start=start, interval="1d", auto_adjust=True,
                     progress=False)["Close"]
    if hasattr(px, "columns"):
        px = px.iloc[:, 0]
    return {ts.strftime("%Y-%m-%d"): float(v) for ts, v in px.items() if np.isfinite(v)}


def build_series(fng, btc):
    days = sorted(set(fng) & set(btc))
    return days, np.array([fng[d] for d in days]), np.array([btc[d] for d in days])


# ---------------------------------------------------------------------------
# Signal styles. Each returns True (enter long) / "short" / False for day i.
# ---------------------------------------------------------------------------
def make_styles():
    styles = {}
    for lo in (10, 15, 20, 25, 30):
        styles["fear_long_%d" % lo] = ("long_if", lambda f, i, lo=lo: f[i] <= lo)
    for hi in (55, 65, 75, 80):
        styles["greed_long_%d" % hi] = ("long_if", lambda f, i, hi=hi: f[i] >= hi)
    for hi in (75, 80, 85):
        styles["greed_short_%d" % hi] = ("short_if", lambda f, i, hi=hi: f[i] >= hi)
    for drop in (10, 20):
        styles["fear_spike_%d" % drop] = (
            "long_if", lambda f, i, d=drop: i >= 5 and (f[i] - f[i - 5]) <= -d)
    return styles


def run(verbose=True):
    if verbose:
        print("Fetching Fear & Greed history + BTC-USD...")
    fng_map = fetch_fng()
    btc_map = fetch_btc()
    days, F, C = build_series(fng_map, btc_map)
    if verbose:
        print("  %d aligned days, %s -> %s\n" % (len(days), days[0], days[-1]))

    cost = COST_BPS / 10000.0
    rows, trial_sharpes = [], []
    best_leg = None
    n_trials = 0

    # unconditional baselines per horizon (non-overlapping)
    uncond = {}
    for h in HORIZONS:
        u = [C[i + h] / C[i] - 1.0 for i in range(0, len(C) - h, h)]
        uncond[h] = (float(np.mean(u)), np.array(u))

    styles = make_styles()
    for name, (kind, fires) in styles.items():
        for h in HORIZONS:
            n_trials += 1
            trades = []
            i = 5
            while i < len(C) - h:
                if fires(F, i):
                    g = C[i + h] / C[i] - 1.0
                    if kind == "short_if":
                        g = -g
                    trades.append(g - cost)
                    i += h
                else:
                    i += 1
            if len(trades) < 15:
                continue
            t_arr = np.array(trades)
            sd = t_arr.std(ddof=1)
            u_mean, u_arr = uncond[h]
            sign = -1.0 if kind == "short_if" else 1.0
            # conditional edge vs the unconditional same-horizon mean
            edge = float(t_arr.mean() + cost - sign * u_mean)
            se = sd / np.sqrt(len(t_arr)) if sd > 0 else 0.0
            row = {
                "style": name, "horizon_days": h, "n_trades": len(t_arr),
                "mean_net_bps": round(float(t_arr.mean()) * 10000, 1),
                "t_stat": round(float(t_arr.mean() / se), 2) if se > 0 else None,
                "uncond_mean_bps": round(sign * u_mean * 10000, 1),
                "edge_vs_uncond_bps": round(edge * 10000, 1),
                "edge_t": round(edge / se, 2) if se > 0 else None,
                "win_rate_pct": round(float((t_arr > 0).mean()) * 100, 1),
            }
            rows.append(row)
            trial_sharpes.append(float(t_arr.mean() / sd) if sd > 0 else 0.0)
            if best_leg is None or row["edge_vs_uncond_bps"] > best_leg[0]["edge_vs_uncond_bps"]:
                best_leg = (row, t_arr, uncond[h][1])

    best_row, best_arr, bench_arr = best_leg
    k = min(len(best_arr), len(bench_arr))
    ev = qv.evaluate("%s h=%dd" % (best_row["style"], best_row["horizon_days"]),
                     best_arr, n_trials=n_trials, trial_sharpes=trial_sharpes,
                     periods_per_year=365.0 / best_row["horizon_days"],
                     trades_per_period=1.0,
                     benchmark_returns=bench_arr[:k] - cost)

    payload = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sample": [days[0], days[-1]], "n_days": len(days),
        "cost_bps": COST_BPS, "configs_tested": n_trials,
        "results": rows, "best": best_row, "best_deflated": ev,
        "verdict": qv.verdict_line(ev),
    }
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w") as f:
        json.dump(payload, f, indent=2)
    return payload


def report(p):
    print("=" * 94)
    print("SENTIMENT LAB — Fear & Greed vs BTC, %s to %s (%d days, %d configs, %.0f bps cost)"
          % (p["sample"][0], p["sample"][1], p["n_days"], p["configs_tested"],
             p["cost_bps"]))
    print("=" * 94)
    print("  %-18s %5s %7s %12s %8s %13s %13s %7s"
          % ("style", "hold", "trades", "net/trade", "t", "uncond mean", "edge v unc", "edge t"))
    for r in sorted(p["results"], key=lambda x: -(x["edge_vs_uncond_bps"] or 0))[:14]:
        print("  %-18s %4dd %7d %10.1fbp %8s %11.1fbp %11.1fbp %7s"
              % (r["style"], r["horizon_days"], r["n_trades"], r["mean_net_bps"],
                 r["t_stat"], r["uncond_mean_bps"], r["edge_vs_uncond_bps"], r["edge_t"]))
    print("  ... (%d rows total, full table in report)" % len(p["results"]))

    b, ev = p["best"], p["best_deflated"]
    print("\nBEST BY CONDITIONAL EDGE: %s at %dd" % (b["style"], b["horizon_days"]))
    print("  %d trades, net %+.1f bps/trade vs unconditional %+.1f bps  (edge %+.1f bps, t=%s)"
          % (b["n_trades"], b["mean_net_bps"], b["uncond_mean_bps"],
             b["edge_vs_uncond_bps"], b["edge_t"]))
    print("  after deflation for %d configs: DSR %s | vs buy-and-hold: %s (excess t=%s)"
          % (p["configs_tested"], ev["deflated_sharpe"],
             ev.get("beats_benchmark"), ev.get("excess_t_stat")))
    print("\n  -> %s" % p["verdict"])
    print("\nReport -> %s" % REPORT)


if __name__ == "__main__":
    report(run())
