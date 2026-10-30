#!/usr/bin/env python3
"""Mining-fundamentals lab — does the physical economy under Bitcoin predict its price?

WHY MINING DATA IS A LEGITIMATE FUNDAMENTAL
-------------------------------------------
Hashrate is the one "balance sheet" crypto has: real capital, real electricity,
real margins. When price falls below mining cost, weak miners capitulate — they
switch off, hashrate falls, difficulty adjusts down. The documented hypothesis
("hash ribbons", Charles Edwards 2019) is that the END of a capitulation — hashrate
recovering — marks durable bottoms, because forced sellers are exhausted.

Unlike sentiment or order flow, this is slow, structural, and physically costly to
fake. It is also FREE to test: blockchain.com publishes complete daily hashrate and
difficulty history back to the genesis block (2009). This desk has never tested it.

Signals tested (all long-only events on BTC, entry at signal-day close):
    ribbon_recovery    30d MA of hashrate crosses back ABOVE 60d MA after being
                       below (the classic published buy signal)
    capitulation_start 30d MA crosses BELOW 60d (contrarian early entry)
    difficulty_drop    first negative difficulty change after positives
                       (protocol-confirmed miner shutdown)
    ribbon_healthy     state filter: in market only while 30d > 60d

Honest constraints:
* Price history (yfinance BTC-USD) starts 2014-09, so the study window is
  2014-09 -> now: four capitulation eras (2014-15, 2018, 2020, 2022) but a SMALL
  number of discrete events — these signals fire rarely. Small n is reported, not
  hidden, and no claim is made past what it supports.
* Non-overlapping entries; 30 bps round-trip cost; every config counted for
  deflation; single asset so n_eff = trade count.

PAPER ONLY. Research, not advice.

Usage:  python3 miner_lab.py
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
REPORT = os.path.join(BASE, "reports", "miner_lab.json")

COST_BPS = 30.0
HORIZONS = [10, 21, 60]
MIN_TRADES = 8          # these events are rare; small n is reported, not hidden


def fetch_chart(name):
    url = ("https://api.blockchain.info/charts/%s?timespan=all&format=json"
           "&sampled=false" % name)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research)"})
    with urllib.request.urlopen(req, timeout=40) as r:
        d = json.load(r)
    out = {}
    for v in d["values"]:
        out[time.strftime("%Y-%m-%d", time.gmtime(int(v["x"])))] = float(v["y"])
    return out


def fetch_btc():
    import yfinance as yf
    px = yf.download("BTC-USD", start="2014-09-17", interval="1d",
                     auto_adjust=True, progress=False)["Close"]
    if hasattr(px, "columns"):
        px = px.iloc[:, 0]
    return {ts.strftime("%Y-%m-%d"): float(v) for ts, v in px.items()
            if np.isfinite(v)}


def align(hashrate, difficulty, btc):
    days = sorted(set(hashrate) & set(difficulty) & set(btc))
    H = np.array([hashrate[d] for d in days])
    D = np.array([difficulty[d] for d in days])
    C = np.array([btc[d] for d in days])
    return days, H, D, C


def ma(x, w):
    out = np.full(len(x), np.nan)
    c = np.cumsum(np.insert(x, 0, 0.0))
    out[w - 1:] = (c[w:] - c[:-w]) / w
    return out


def build_signals(H, D):
    """Event arrays (True on the day the event fires) and one state array."""
    m30, m60 = ma(H, 30), ma(H, 60)
    below = m30 < m60
    n = len(H)
    recovery = np.zeros(n, bool)
    capit = np.zeros(n, bool)
    for i in range(61, n):
        if below[i - 1] and not below[i]:
            recovery[i] = True
        if not below[i - 1] and below[i]:
            capit[i] = True
    # difficulty: first drop after a run of non-drops (14d lookback change)
    ddrop = np.zeros(n, bool)
    for i in range(15, n):
        cur = D[i] < D[i - 14] * 0.999
        prev = D[i - 1] < D[i - 15] * 0.999
        if cur and not prev:
            ddrop[i] = True
    healthy = ~below
    return {"ribbon_recovery": recovery, "capitulation_start": capit,
            "difficulty_drop": ddrop}, healthy


def run(verbose=True):
    if verbose:
        print("Fetching hashrate + difficulty (blockchain.com) and BTC-USD...")
    days, H, D, C = align(fetch_chart("hash-rate"), fetch_chart("difficulty"),
                          fetch_btc())
    if verbose:
        print("  %d aligned days, %s -> %s\n" % (len(days), days[0], days[-1]))

    events, healthy = build_signals(H, D)
    cost = COST_BPS / 10000.0
    n = len(C)

    uncond = {}
    for h in HORIZONS:
        u = np.array([C[i + h] / C[i] - 1.0 for i in range(61, n - h, h)])
        uncond[h] = (float(u.mean()), u)

    rows, trial_sharpes, n_trials = [], [], 0
    best = None

    for name, ev_arr in events.items():
        for h in HORIZONS:
            n_trials += 1
            trades, i = [], 61
            while i < n - h:
                if ev_arr[i]:
                    trades.append(C[i + h] / C[i] - 1.0 - cost)
                    i += h
                else:
                    i += 1
            if len(trades) < MIN_TRADES:
                rows.append({"signal": name, "horizon_days": h,
                             "n_trades": len(trades), "verdict": "too few events"})
                continue
            t_arr = np.array(trades)
            sd = t_arr.std(ddof=1)
            u_mean, u_arr = uncond[h]
            edge = float(t_arr.mean() + cost - u_mean)
            se = sd / np.sqrt(len(t_arr)) if sd > 0 else 0.0
            row = {
                "signal": name, "horizon_days": h, "n_trades": len(t_arr),
                "mean_net_bps": round(float(t_arr.mean()) * 10000, 1),
                "uncond_mean_bps": round(u_mean * 10000, 1),
                "edge_vs_uncond_bps": round(edge * 10000, 1),
                "edge_t": round(edge / se, 2) if se > 0 else None,
                "win_rate_pct": round(float((t_arr > 0).mean()) * 100, 1),
            }
            rows.append(row)
            trial_sharpes.append(float(t_arr.mean() / sd) if sd > 0 else 0.0)
            if best is None or (row["edge_vs_uncond_bps"] or -1e9) > best[0]["edge_vs_uncond_bps"]:
                best = (row, t_arr, u_arr)

    # state filter: in market only while ribbon healthy, monthly evaluation
    n_trials += 1
    h = 21
    state_r, i = [], 61
    while i < n - h:
        if healthy[i]:
            state_r.append(C[i + h] / C[i] - 1.0 - cost * (0 if healthy[i - h] else 1))
        i += h
    u_mean, _ = uncond[h]
    if len(state_r) >= 15:
        s_arr = np.array(state_r)
        sd = s_arr.std(ddof=1)
        se = sd / np.sqrt(len(s_arr))
        rows.append({"signal": "ribbon_healthy_state", "horizon_days": h,
                     "n_trades": len(s_arr),
                     "mean_net_bps": round(float(s_arr.mean()) * 10000, 1),
                     "uncond_mean_bps": round(u_mean * 10000, 1),
                     "edge_vs_uncond_bps": round(float(s_arr.mean() - u_mean) * 10000, 1),
                     "edge_t": round(float(s_arr.mean() - u_mean) / se, 2),
                     "win_rate_pct": round(float((s_arr > 0).mean()) * 100, 1)})
        trial_sharpes.append(float(s_arr.mean() / sd) if sd > 0 else 0.0)

    best_row, best_arr, bench_arr = best
    k = min(len(best_arr), len(bench_arr))
    ev = qv.evaluate("%s h=%dd" % (best_row["signal"], best_row["horizon_days"]),
                     best_arr, n_trials=n_trials, trial_sharpes=trial_sharpes,
                     periods_per_year=365.0 / best_row["horizon_days"],
                     trades_per_period=1.0, benchmark_returns=bench_arr[:k] - cost)

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
    print("=" * 96)
    print("MINING-FUNDAMENTALS LAB — hash ribbons & difficulty vs BTC, %s to %s (%d configs)"
          % (p["sample"][0], p["sample"][1], p["configs_tested"]))
    print("=" * 96)
    print("  %-22s %5s %7s %12s %13s %13s %7s %6s"
          % ("signal", "hold", "trades", "net/trade", "uncond", "edge", "edge t", "win%"))
    for r in p["results"]:
        if r.get("verdict") == "too few events":
            print("  %-22s %4dd %7d   (too few events to test)"
                  % (r["signal"], r["horizon_days"], r["n_trades"]))
            continue
        print("  %-22s %4dd %7d %10.1fbp %11.1fbp %11.1fbp %7s %5s%%"
              % (r["signal"], r["horizon_days"], r["n_trades"], r["mean_net_bps"],
                 r["uncond_mean_bps"], r["edge_vs_uncond_bps"], r["edge_t"],
                 r["win_rate_pct"]))
    b, ev = p["best"], p["best_deflated"]
    print("\nBEST: %s at %dd — %d trades, edge %+.1f bps (t=%s)"
          % (b["signal"], b["horizon_days"], b["n_trades"],
             b["edge_vs_uncond_bps"], b["edge_t"]))
    print("  deflated for %d configs: DSR %s | beats B&H: %s (excess t=%s)"
          % (p["configs_tested"], ev["deflated_sharpe"],
             ev.get("beats_benchmark"), ev.get("excess_t_stat")))
    print("\n  -> %s" % p["verdict"])
    print("\nReport -> %s" % REPORT)


if __name__ == "__main__":
    report(run())
