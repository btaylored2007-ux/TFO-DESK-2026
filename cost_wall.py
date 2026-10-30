#!/usr/bin/env python3
"""Cost-wall comparison — the same strategy, the same trades, two venues.

Active target venue comes from venue_costs.ACTIVE_VENUE.

Method
------
1. Pull real daily OHLC (public Kraken endpoint) for the target venue's listed universe.
2. Walk the bars and generate trades with EXACTLY the entry/exit rules the live
   desks use (mean-reversion z-score, Donchian breakout) — bar-indexed so the
   walk is deterministic and repeatable.
3. Record each trade's GROSS return and hold length. Gross is venue-independent:
   it is what the signal captured before anybody charged for it.
4. Apply the Kraken cost schedule and the target venue's schedule to that same set of
   trades, and compare net expectancy.

Because step 3 is shared, the only difference between the two columns is cost.
That is the entire point: it isolates venue from strategy.

Honest limits
-------------
* Venue costs here are MODELLED. No broker account is connected.
* Prices are Kraken's; the venue's quotes differ slightly. Real, but second-order.
* No survivorship correction, and the universe is today's listings walked
  backwards — mildly optimistic for both columns equally.
* This measures cost drag, not whether the signal has edge. The Kraken desk's own
  research already answered that (HANDOVER.md §5: it does not).

Usage:
    python3 cost_wall.py                 # default: both strategies
    python3 cost_wall.py --bars 720      # ~2 years of daily bars
"""
import argparse
import json
import math
import os
import sys
import time

import venue_costs
import venue_universe

KRAKEN_DESK = "/Users/test/Applications/KrakenQuantDesk"
if KRAKEN_DESK not in sys.path:
    sys.path.insert(0, KRAKEN_DESK)
from kraken_backtest import fetch_ohlc, sym_to_pair  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(BASE, "reports", "cost_wall.json")

STRATS = {
    "meanrev":  {"z_window": 20, "z_entry": 2.0, "z_exit": 0.5, "stop": 8.0,
                 "max_hold_days": 15},
    "breakout": {"n": 20, "m": 10, "stop": 8.0, "max_hold_days": 40},
}


def _ms(xs):
    n = len(xs)
    m = sum(xs) / n
    return m, (math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0)


def zscore(c, w):
    if len(c) < w + 1:
        return None
    m, sd = _ms(c[-w:])
    return None if sd <= 0 else (c[-1] - m) / sd


def simulate(strat, cfg, H, L, C):
    """Walk bars, emit (side, gross_return, hold_bars) per closed trade.

    Bar-indexed mirror of the live desks' entry_signal/exit_signal. Entry and exit
    both execute at that bar's close, so no look-ahead: the decision at bar i uses
    only bars up to and including i.
    """
    trades = []
    pos = None
    start = max(cfg.get("z_window", 0), cfg.get("n", 0)) + 2
    for i in range(start, len(C)):
        h, l, c = H[:i + 1], L[:i + 1], C[:i + 1]
        px = c[-1]

        if pos is not None:
            side, entry, ebar = pos
            move = (px - entry) / entry if side == "long" else (entry - px) / entry
            held = i - ebar
            reason = None
            if move <= -cfg["stop"] / 100.0:
                reason = "stop"
            elif strat == "meanrev":
                z = zscore(c, cfg["z_window"])
                if z is not None and abs(z) <= cfg["z_exit"]:
                    reason = "reverted"
            if reason is None and strat == "breakout":
                m = cfg["m"]
                if side == "long" and px < min(l[-m - 1:-1]):
                    reason = "channel-exit"
                elif side == "short" and px > max(h[-m - 1:-1]):
                    reason = "channel-exit"
            if reason is None and held >= cfg["max_hold_days"]:
                reason = "max-hold"
            if reason:
                trades.append((side, move, held))
                pos = None
                continue

        if pos is None:
            side = None
            if strat == "meanrev":
                z = zscore(c, cfg["z_window"])
                if z is not None:
                    if z <= -cfg["z_entry"]:
                        side = "long"
                    elif z >= cfg["z_entry"]:
                        side = "short"
            elif strat == "breakout":
                n = cfg["n"]
                hi, lo = max(h[-n - 1:-1]), min(l[-n - 1:-1])
                if px > hi:
                    side = "long"
                elif px < lo:
                    side = "short"
            if side:
                pos = (side, px, i)
    return trades


def cost_pct(venue, instrument_class, side, hold_days):
    """Round-trip cost as a fraction of notional, for one trade at one venue.

    Kraken lists a single crypto schedule, so alt-tier symbols map onto it; other
    venues price majors and alts differently and get the class they resolve to.
    """
    cls = instrument_class
    if cls not in venue_costs.classes(venue):
        cls = "crypto"
    r = venue_costs.round_trip(cls, 10000.0, hold_days, side, venue_name=venue)
    return r["total"] / 10000.0


def expectancy(trades, venue):
    """Net expectancy per trade at a venue, as a percentage."""
    if not trades:
        return None
    nets = []
    for side, gross, held, icls in trades:
        nets.append(gross - cost_pct(venue, icls, side, held))
    n = len(nets)
    mean = sum(nets) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in nets) / (n - 1)) if n > 1 else 0.0
    wins = len([x for x in nets if x > 0])
    return {
        "trades": n,
        "expectancy_pct": round(mean * 100, 4),
        "win_rate_pct": round(wins / n * 100, 1),
        "stdev_pct": round(sd * 100, 3),
        "t_stat": round(mean / (sd / math.sqrt(n)), 2) if sd > 0 else None,
        "total_return_pct": round(sum(nets) * 100, 2),
    }


def run(bars=720, symbols=None, verbose=True):
    if symbols is None:
        syms = venue_universe.kraken_live_universe() or [
            "XBT/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD", "XLM/USD",
            "LTC/USD", "LINK/USD", "DOT/USD", "AVAX/USD"]
        symbols = [s for s in syms if venue_universe.is_tradeable(s)]

    if verbose:
        print("Fetching %d daily bars for %d venue-listed symbols..."
              % (bars, len(symbols)))

    data = {}
    for s in symbols:
        try:
            b = fetch_ohlc(sym_to_pair(s), 1440, bars)
            if len(b) >= 120:
                data[s] = ([x[2] for x in b], [x[3] for x in b], [x[4] for x in b])
        except Exception as e:
            if verbose:
                print("  skip %s (%s)" % (s, e))
        time.sleep(0.25)

    if verbose:
        print("  got usable history for %d symbols\n" % len(data))

    results = {}
    for strat, cfg in STRATS.items():
        all_trades = []
        for s, (H, L, C) in data.items():
            _, icls = venue_universe.resolve(s)
            for side, gross, held in simulate(strat, cfg, H, L, C):
                all_trades.append((side, gross, held, icls))
        results[strat] = {
            "gross": None,   # filled in below from the zero-cost returns
            "kraken": expectancy(all_trades, "kraken"),
            "venue": expectancy(all_trades, venue_costs.ACTIVE_VENUE),
            "n_symbols": len(data),
        }
        # gross (zero-cost) baseline
        if all_trades:
            gr = [g for _, g, _, _ in all_trades]
            n = len(gr)
            mean = sum(gr) / n
            sd = math.sqrt(sum((x - mean) ** 2 for x in gr) / (n - 1)) if n > 1 else 0.0
            results[strat]["gross"] = {
                "trades": n,
                "expectancy_pct": round(mean * 100, 4),
                "win_rate_pct": round(len([x for x in gr if x > 0]) / n * 100, 1),
                "t_stat": round(mean / (sd / math.sqrt(n)), 2) if sd > 0 else None,
            }
            results[strat]["avg_hold_days"] = round(
                sum(h for _, _, h, _ in all_trades) / n, 1)

    payload = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "bars": bars,
        "symbols": sorted(data.keys()),
        "costs_verified": venue_costs.VERIFIED_AGAINST_LIVE_ACCOUNT,
        "universe_verified": venue_universe.VERIFIED_AGAINST_LIVE_ACCOUNT,
        "results": results,
    }
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w") as f:
        json.dump(payload, f, indent=2)
    return payload


def report(payload):
    print("=" * 78)
    print("COST WALL: identical trades, Kraken vs %s" % venue_costs.active()["label"])
    print("=" * 78)
    print(venue_costs.verification_notice())
    print("Universe: %d symbols, %d daily bars\n" % (len(payload["symbols"]), payload["bars"]))

    for strat, r in payload["results"].items():
        g, k, e = r["gross"], r["kraken"], r["venue"]
        if not g:
            print("%s: no trades generated\n" % strat)
            continue
        print("--- %s  (%d trades across %d symbols, avg hold %.1f days)"
              % (strat.upper(), g["trades"], r["n_symbols"], r.get("avg_hold_days", 0)))
        print("    %-22s %12s %12s %10s" % ("", "expectancy", "win rate", "t-stat"))
        print("    %-22s %11.3f%% %11.1f%% %10s"
              % ("gross (zero cost)", g["expectancy_pct"], g["win_rate_pct"], g["t_stat"]))
        print("    %-22s %11.3f%% %11.1f%% %10s"
              % ("net @ Kraken", k["expectancy_pct"], k["win_rate_pct"], k["t_stat"]))
        print("    %-22s %11.3f%% %11.1f%% %10s"
              % ("net @ " + venue_costs.active()["label"], e["expectancy_pct"],
                 e["win_rate_pct"], e["t_stat"]))
        drag = k["expectancy_pct"] - e["expectancy_pct"]
        print("    %s costs %.3f%% more per trade" % (venue_costs.active()["label"], drag))
        # Sum of per-trade returns, NOT a compounded account path -- a book running
        # 8 positions at 10% each never experiences this as an equity curve.
        print("    sum of per-trade returns (not compounded): Kraken %.1f%%  |  %s %.1f%%"
              % (k["total_return_pct"], venue_costs.active()["label"],
                 e["total_return_pct"]))
        print()

    print("Report written to", REPORT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=720)
    args = ap.parse_args()
    report(run(bars=args.bars))


if __name__ == "__main__":
    main()
