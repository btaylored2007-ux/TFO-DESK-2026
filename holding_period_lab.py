#!/usr/bin/env python3
"""Holding-period sensitivity lab — where does turnover stop eating the edge?

THE QUESTION
------------
A fixed cost wall is paid per round trip. So the annual drag from costs is

    drag_per_year = cost_per_round_trip x (252 / holding_days)

Hold for 1 day and a 30 bps wall costs 7,560 bps/yr. Hold for 60 days and the same
wall costs 126 bps/yr. Meanwhile signal amplitude usually GROWS with horizon, but
sub-linearly — it decays relative to the noise. Somewhere between "too often" and
"too rarely" there is an optimum, and nobody can guess where it is.

This lab finds it empirically: for every (signal, lookback, hold) combination it
measures gross per-trade edge, subtracts the real cost wall, and annualizes. The
output is net annualized return as a function of holding period.

THE LADDER
----------
The operator wants the desk to trade DAILY without paying daily turnover. That is a
laddered book: hold N concurrent positions with an average life of H days, and
roughly N/H of them mature each day. Daily entries, daily exits, daily P&L — at
1/H the turnover cost. This lab reports the N required for ~1 close/day at the
optimal H.

HONESTY
-------
Every combination tested is counted and reported, and the winner is scored through
the deflation harness against that full count. A best-of-N result without deflation
is not evidence. Buy-and-hold is included as a benchmark: if nothing beats simply
holding the asset, that is the finding and it will be stated plainly.

Usage:
    python3 holding_period_lab.py                # full sweep
    python3 holding_period_lab.py --symbols 12   # smaller/faster universe
"""
import argparse
import json
import os
import sys
import time

import numpy as np

import quant_validation as qv

KRAKEN_DESK = "/Users/test/Applications/KrakenQuantDesk"
if KRAKEN_DESK not in sys.path:
    sys.path.insert(0, KRAKEN_DESK)
from kraken_backtest import fetch_ohlc, sym_to_pair  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(BASE, "reports", "holding_period_lab.json")

TRADING_DAYS = 365          # crypto trades every day
COST_WALL_BPS = 30.0        # Kraken crypto round trip; the venue we can actually use

HOLD_PERIODS = [1, 2, 3, 5, 7, 10, 14, 21, 30, 45, 60, 90, 120, 180]
LOOKBACKS = [5, 10, 20, 30, 60, 90]

DEFAULT_UNIVERSE = [
    "XBT/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD", "XLM/USD",
    "LTC/USD", "LINK/USD", "DOT/USD", "AVAX/USD", "ATOM/USD", "ALGO/USD",
    "FIL/USD", "ETC/USD", "UNI/USD", "AAVE/USD", "NEAR/USD", "ICP/USD",
]


# ---------------------------------------------------------------------------
# Signals — each returns +1 (long), -1 (short) or 0 (flat) for bar i, using only
# information available up to and including bar i. No look-ahead anywhere.
# ---------------------------------------------------------------------------
def sig_momentum(closes, i, lb):
    if i < lb:
        return 0
    past = closes[i - lb]
    if past <= 0:
        return 0
    return 1 if closes[i] > past else -1


def sig_reversal(closes, i, lb):
    return -sig_momentum(closes, i, lb)


def sig_vol_scaled_momentum(closes, i, lb):
    """Momentum, but only when the trailing move is large relative to its own noise."""
    if i < lb + 1:
        return 0
    window = closes[i - lb:i + 1]
    rets = np.diff(window) / window[:-1]
    sd = rets.std(ddof=1) if rets.size > 1 else 0.0
    if sd <= 0:
        return 0
    total = (closes[i] - closes[i - lb]) / closes[i - lb]
    z = total / (sd * np.sqrt(lb))
    if z > 0.5:
        return 1
    if z < -0.5:
        return -1
    return 0


def sig_long_only_momentum(closes, i, lb):
    """Long-only variant — the short leg is not implementable on Kraken spot."""
    s = sig_momentum(closes, i, lb)
    return 1 if s > 0 else 0


SIGNALS = {
    "momentum": sig_momentum,
    "reversal": sig_reversal,
    "vol_scaled_momentum": sig_vol_scaled_momentum,
    "long_only_momentum": sig_long_only_momentum,
}


# ---------------------------------------------------------------------------
# Fixed-hold trade generation
# ---------------------------------------------------------------------------
def fixed_hold_trades(closes, signal_fn, lookback, hold):
    """Enter when the signal is non-zero, exit exactly `hold` bars later.

    Fixed hold isolates the holding-period effect: no stop, no early exit, nothing
    else that would confound the comparison across hold lengths. Non-overlapping,
    so each trade is an independent observation.
    """
    out = []
    i = lookback
    n = len(closes)
    while i + hold < n:
        s = signal_fn(closes, i, lookback)
        if s == 0:
            i += 1
            continue
        entry, exit_ = closes[i], closes[i + hold]
        if entry <= 0:
            i += 1
            continue
        gross = (exit_ - entry) / entry * s
        out.append(gross)
        i += hold          # non-overlapping
    return out


def buy_and_hold(closes, hold):
    """Benchmark: no signal at all, just hold. Same non-overlapping cadence."""
    out = []
    i = 0
    n = len(closes)
    while i + hold < n:
        entry, exit_ = closes[i], closes[i + hold]
        if entry > 0:
            out.append((exit_ - entry) / entry)
        i += hold
    return out


def annualize(mean_net_per_trade, hold_days):
    """Net return per trade -> annualized, assuming capital is continuously deployed."""
    trades_per_year = TRADING_DAYS / float(hold_days)
    return mean_net_per_trade * trades_per_year


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------
def load_universe(symbols, bars=720, verbose=True):
    data = {}
    for s in symbols:
        try:
            b = fetch_ohlc(sym_to_pair(s), 1440, bars)
            if len(b) >= 250:
                data[s] = np.array([x[4] for x in b], dtype=float)
        except Exception as e:
            if verbose:
                print("  skip %s (%s)" % (s, str(e)[:60]))
        time.sleep(0.25)
    return data


def run(symbols=None, bars=720, cost_bps=COST_WALL_BPS, verbose=True):
    symbols = symbols or DEFAULT_UNIVERSE
    if verbose:
        print("Fetching %d daily bars for %d symbols..." % (bars, len(symbols)))
    data = load_universe(symbols, bars, verbose)
    if verbose:
        print("  usable: %d symbols\n" % len(data))
    if not data:
        raise RuntimeError("no usable price history fetched")

    cost = cost_bps / 10000.0
    rows = []
    n_trials = 0

    for sig_name, fn in SIGNALS.items():
        for lb in LOOKBACKS:
            for hold in HOLD_PERIODS:
                trades = []
                for closes in data.values():
                    trades.extend(fixed_hold_trades(closes, fn, lb, hold))
                if len(trades) < 30:
                    continue
                n_trials += 1
                g = np.array(trades, dtype=float)
                net = g - cost
                mean_net = float(net.mean())
                sd = float(net.std(ddof=1)) if net.size > 1 else 0.0
                rows.append({
                    "signal": sig_name,
                    "lookback": lb,
                    "hold_days": hold,
                    "n_trades": int(g.size),
                    "gross_bps": round(float(g.mean()) * 10000, 2),
                    "net_bps": round(mean_net * 10000, 2),
                    "cost_drag_bps_per_year": round(cost * (TRADING_DAYS / hold) * 10000, 1),
                    "net_annualized_pct": round(annualize(mean_net, hold) * 100, 2),
                    "sharpe_per_trade": round(mean_net / sd, 4) if sd > 0 else 0.0,
                    "t_stat": round(mean_net / (sd / np.sqrt(g.size)), 2) if sd > 0 else 0.0,
                    "win_rate_pct": round(float((net > 0).mean()) * 100, 1),
                })

    # benchmark: buy and hold, same cadence
    bench = []
    for hold in HOLD_PERIODS:
        trades = []
        for closes in data.values():
            trades.extend(buy_and_hold(closes, hold))
        if len(trades) < 30:
            continue
        g = np.array(trades, dtype=float)
        net = g - cost
        bench.append({
            "hold_days": hold,
            "n_trades": int(g.size),
            "gross_bps": round(float(g.mean()) * 10000, 2),
            "net_bps": round(float(net.mean()) * 10000, 2),
            "net_annualized_pct": round(annualize(float(net.mean()), hold) * 100, 2),
        })

    payload = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "symbols": sorted(data.keys()),
        "bars": bars,
        "cost_wall_bps": cost_bps,
        "combinations_tested": n_trials,
        "results": rows,
        "buy_and_hold_benchmark": bench,
    }

    # Cross-sectional correlation of the pooled assets. Pooling 18 crypto names that
    # move together does NOT give 18x the information, and ignoring that manufactures
    # significance -- it produced a spurious t=7.63 on this very sweep once.
    daily = np.array([np.diff(c) / c[:-1] for c in data.values()])
    rho = qv.mean_pairwise_correlation(daily)
    payload["mean_pairwise_correlation"] = round(rho, 4)

    # Deflate the winner against the full search, at its EFFECTIVE sample size,
    # and require it to beat buy-and-hold over the same windows.
    if rows:
        best = max(rows, key=lambda r: r["net_annualized_pct"])
        fn = SIGNALS[best["signal"]]
        trades, bench_trades = [], []
        for closes in data.values():
            trades.extend(fixed_hold_trades(closes, fn, best["lookback"], best["hold_days"]))
            bench_trades.extend(buy_and_hold(closes, best["hold_days"]))
        gross = np.array(trades, dtype=float)
        net = gross - cost
        n_eff = qv.effective_sample_size(gross.size, n_series=len(data),
                                         mean_correlation=rho)
        trial_sharpes = [r["sharpe_per_trade"] for r in rows]
        ev = qv.evaluate(
            "%s lb=%d hold=%dd" % (best["signal"], best["lookback"], best["hold_days"]),
            net, n_trials=n_trials, trial_sharpes=trial_sharpes,
            periods_per_year=TRADING_DAYS / best["hold_days"], trades_per_period=1.0,
            gross_returns=gross, n_effective=n_eff,
            benchmark_returns=np.array(bench_trades, dtype=float) - cost)
        payload["best"] = best
        payload["best_deflated"] = ev
        payload["best_verdict"] = qv.verdict_line(ev)

    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w") as f:
        json.dump(payload, f, indent=2)
    return payload


def ladder_design(hold_days, target_closes_per_day=1.0):
    """Positions needed so roughly `target_closes_per_day` mature each day."""
    n = int(round(hold_days * target_closes_per_day))
    return {
        "hold_days": hold_days,
        "concurrent_positions": max(1, n),
        "closes_per_day": round(max(1, n) / hold_days, 2),
        "turnover_vs_daytrading": "1/%dth" % hold_days,
    }


def report(p):
    print("=" * 82)
    print("HOLDING-PERIOD SENSITIVITY   (%d symbols, %d bars, %.0f bps cost wall)"
          % (len(p["symbols"]), p["bars"], p["cost_wall_bps"]))
    print("=" * 82)
    print("Combinations tested: %d  <- this is the number the winner must be deflated against\n"
          % p["combinations_tested"])

    # net annualized by hold period, best signal at each
    print("BEST NET ANNUALIZED RETURN AT EACH HOLDING PERIOD")
    print("  %-6s %-22s %10s %10s %12s %8s" %
          ("hold", "best signal", "gross bps", "net bps", "net ann %", "t"))
    by_hold = {}
    for r in p["results"]:
        h = r["hold_days"]
        if h not in by_hold or r["net_annualized_pct"] > by_hold[h]["net_annualized_pct"]:
            by_hold[h] = r
    for h in sorted(by_hold):
        r = by_hold[h]
        print("  %-6d %-22s %10.1f %10.1f %11.1f%% %8.2f"
              % (h, "%s/%d" % (r["signal"], r["lookback"]), r["gross_bps"],
                 r["net_bps"], r["net_annualized_pct"], r["t_stat"]))

    print("\nCOST DRAG BY HOLDING PERIOD (why turnover is the whole game)")
    for h in sorted(by_hold):
        print("  hold %3dd -> %6.0f bps/yr of pure cost drag"
              % (h, by_hold[h]["cost_drag_bps_per_year"]))

    print("\nBUY-AND-HOLD BENCHMARK (no signal at all)")
    for b in p["buy_and_hold_benchmark"]:
        print("  hold %3dd  net %8.1f bps/trade  -> %8.1f%% annualized"
              % (b["hold_days"], b["net_bps"], b["net_annualized_pct"]))

    if "best" in p:
        b, ev = p["best"], p["best_deflated"]
        print("\n" + "-" * 82)
        print("BEST COMBINATION FOUND")
        print("  %s, lookback %d, hold %dd" % (b["signal"], b["lookback"], b["hold_days"]))
        print("  gross %.1f bps -> net %.1f bps per trade over %d trades"
              % (b["gross_bps"], b["net_bps"], b["n_trades"]))
        print("  net annualized: %.1f%%   naive t-stat: %.2f" % (b["net_annualized_pct"], b["t_stat"]))
        print("\n  INDEPENDENCE CORRECTION (mean pairwise correlation %.3f):"
              % p["mean_pairwise_correlation"])
        print("    nominal observations  : %d" % ev["n_obs"])
        print("    EFFECTIVE observations: %.1f  (discount %.1fx)"
              % (ev["n_effective"], ev["independence_discount"]))
        print("    corrected t-stat      : %.2f" % ev["t_stat"])
        print("\n  VS BUY-AND-HOLD OVER THE SAME WINDOWS:")
        print("    excess: %.1f bps/trade   t=%s   beats benchmark: %s"
              % (ev.get("excess_over_benchmark_bps", 0.0), ev.get("excess_t_stat"),
                 ev.get("beats_benchmark")))
        print("\n  AFTER DEFLATION FOR %d COMBINATIONS SEARCHED:" % p["combinations_tested"])
        print("    expected max Sharpe under null : %.4f" % ev["expected_max_sharpe_under_null"])
        print("    deflated Sharpe                : %s" % ev["deflated_sharpe"])
        print("    -> %s" % p["best_verdict"])

        lad = ladder_design(b["hold_days"])
        print("\n  LADDER TO TRADE DAILY AT THIS HOLD:")
        print("    %d concurrent positions -> ~%.1f closes/day, %s the turnover cost of day trading"
              % (lad["concurrent_positions"], lad["closes_per_day"], lad["turnover_vs_daytrading"]))

    print("\nReport -> %s" % REPORT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=len(DEFAULT_UNIVERSE))
    ap.add_argument("--bars", type=int, default=720)
    ap.add_argument("--cost-bps", type=float, default=COST_WALL_BPS)
    args = ap.parse_args()
    report(run(symbols=DEFAULT_UNIVERSE[:args.symbols], bars=args.bars,
               cost_bps=args.cost_bps))


if __name__ == "__main__":
    main()
