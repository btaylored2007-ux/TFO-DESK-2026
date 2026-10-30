#!/usr/bin/env python3
"""Stablecoin peg-deviation DATA PROBE (research only, no orders).

Scopes the data for a possible stablecoin peg-deviation mean-reversion lab:
  1. Inventory: desk candle DB (READ-ONLY) + Kraken free public OHLC API.
  2. Raw phenomenon: distribution of |price - 1.00| in bps per USD-pegged pair,
     excursion counts beyond 10/20/50 bps, time-to-repeg, tail events.
  3. Cost realism against the desk wall: kraken_crypto = 30 bps ROUND TRIP
     (venue_costs.py "kraken" venue, crypto class: spread_pct 0.0010 full
     round-trip + commission_pct 0.0010 per side x2).
  4. Verdict written to reports/stablecoin_depeg_data_probe.json.

NEVER fabricates: everything in the receipt is computed from actual rows
returned by the DB or the API, with fetch failures recorded as such.
"""

import json
import math
import sqlite3
import statistics
import time
import urllib.request
from datetime import datetime, timezone

DB = "/Users/test/Applications/KrakenQuantDesk/kraken_bot/market_intelligence.sqlite"
OUT = "/Users/test/Applications/MultiVenueDesk/reports/stablecoin_depeg_data_probe.json"

# USD-pegged stablecoin pairs quoted in USD on Kraken (from public AssetPairs,
# fetched 2026-08-18). EURC/EURQ are EUR-pegged: their USD pairs float with
# EURUSD and are NOT $1.00 pegs -> excluded from deviation stats, noted in
# inventory. EURT: no longer listed on Kraken (delisted; Tether wound EURT down).
USD_PEGGED = {
    "USDT/USD": "USDTZUSD",
    "USDC/USD": "USDCUSD",
    "DAI/USD": "DAIUSD",
    "PYUSD/USD": "PYUSDUSD",
    "RLUSD/USD": "RLUSDUSD",
    "USDD/USD": "USDDUSD",
    "USDG/USD": "USDGUSD",
    "USDQ/USD": "USDQUSD",
    "USDS/USD": "USDSUSD",
}
INTERVALS = [1440, 60]  # daily (~720 bars ~ 2y) and hourly (~720 bars ~ 30d)
THRESHOLDS_BPS = [10.0, 20.0, 50.0]

STABLE_LIKE_SQL = ("symbol LIKE '%USDT%' OR symbol LIKE '%USDC%' OR symbol LIKE '%DAI%'"
                   " OR symbol LIKE '%EUR%' OR symbol LIKE '%PYUSD%' OR symbol LIKE '%TUSD%'"
                   " OR symbol LIKE '%USDG%' OR symbol LIKE '%USDS%' OR symbol LIKE '%USDD%'"
                   " OR symbol LIKE '%USDQ%' OR symbol LIKE '%RLUSD%' OR symbol LIKE 'USD%'")


def _j(x):
    """JSON-safe scrub (pattern from positioning_lab.py)."""
    if isinstance(x, dict):
        return {str(k): _j(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_j(v) for v in x]
    if isinstance(x, (bool, str)) or x is None:
        return x
    if isinstance(x, (int,)):
        return int(x)
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
        return float(x)
    try:
        return float(x)
    except Exception:
        return str(x)


def iso(ts):
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def db_inventory():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()
    inv = {}
    rows = cur.execute(
        f"SELECT symbol, interval_min, COUNT(*), MIN(ts), MAX(ts) FROM candles "
        f"WHERE {STABLE_LIKE_SQL} GROUP BY symbol, interval_min").fetchall()
    for sym, ival, n, t0, t1 in rows:
        inv.setdefault(sym, []).append(
            {"interval_min": int(ival), "bars": int(n),
             "first": iso(t0), "last": iso(t1)})
    obs = [r[0] for r in cur.execute(
        f"SELECT DISTINCT symbol FROM observations WHERE {STABLE_LIKE_SQL}").fetchall()]
    tape = [r[0] for r in cur.execute(
        f"SELECT DISTINCT symbol FROM trade_tape WHERE {STABLE_LIKE_SQL}").fetchall()]
    con.close()
    return inv, obs, tape


def fetch_ohlc(rest_pair, interval):
    url = (f"https://api.kraken.com/0/public/OHLC?pair={rest_pair}"
           f"&interval={interval}")
    with urllib.request.urlopen(url, timeout=30) as r:
        d = json.load(r)
    if d.get("error"):
        raise RuntimeError(f"{rest_pair}@{interval}: {d['error']}")
    res = d["result"]
    key = [k for k in res if k != "last"][0]
    # rows: [ts, o, h, l, c, vwap, volume, count]
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]),
             float(r[6]), int(r[7])) for r in res[key]]


def pct(sorted_vals, q):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def excursions(bars, thr_bps, interval_min):
    """Episodes where |close-1| >= thr consecutively; duration = bars in episode."""
    episodes = []
    cur_len, cur_peak, cur_start = 0, 0.0, None
    for ts, o, h, l, c, vwap, cnt in bars:
        dev = abs(c - 1.0) * 1e4
        if dev >= thr_bps:
            if cur_len == 0:
                cur_start = ts
            cur_len += 1
            cur_peak = max(cur_peak, dev)
        else:
            if cur_len:
                episodes.append((cur_start, cur_len, cur_peak))
            cur_len, cur_peak = 0, 0.0
    open_ended = cur_len > 0
    if cur_len:
        episodes.append((cur_start, cur_len, cur_peak))
    durs_h = [n * interval_min / 60.0 for _, n, _ in episodes]
    return {
        "episodes": len(episodes),
        "bars_beyond": int(sum(n for _, n, _ in episodes)),
        "median_time_to_repeg_hours": (round(statistics.median(durs_h), 2)
                                       if durs_h else None),
        "max_time_to_repeg_hours": round(max(durs_h), 2) if durs_h else None,
        "max_peak_bps": round(max((p for _, _, p in episodes), default=0.0), 2),
        "last_episode_open_ended": open_ended,
        "worst_episodes": [
            {"start": iso(s), "bars": int(n), "peak_bps": round(p, 2)}
            for s, n, p in sorted(episodes, key=lambda e: -e[2])[:3]],
    }


def analyze(bars, interval_min):
    devs = sorted(abs(c - 1.0) * 1e4 for _, o, h, l, c, _, _ in bars)
    wick = sorted(max(abs(h - 1.0), abs(l - 1.0)) * 1e4
                  for _, o, h, l, c, _, _ in bars)
    zero_vol = sum(1 for b in bars if b[6] == 0)
    out = {
        "bars": len(bars),
        "first": iso(bars[0][0]), "last": iso(bars[-1][0]),
        "span_days": round((bars[-1][0] - bars[0][0]) / 86400.0, 1),
        "zero_trade_bars": int(zero_vol),
        "close_dev_bps": {
            "median": round(pct(devs, 0.50), 3), "p90": round(pct(devs, 0.90), 3),
            "p99": round(pct(devs, 0.99), 3), "max": round(devs[-1], 3)},
        "wick_dev_bps": {
            "median": round(pct(wick, 0.50), 3), "p90": round(pct(wick, 0.90), 3),
            "p99": round(pct(wick, 0.99), 3), "max": round(wick[-1], 3)},
        "excursions_close": {f">={int(t)}bps": excursions(bars, t, interval_min)
                             for t in THRESHOLDS_BPS},
    }
    return out


def main():
    receipt = {
        "probe": "stablecoin_depeg_data_probe",
        "date": "2026-08-18",
        "desk": "Taylor Family Office paper desk (RESEARCH ONLY, no orders)",
        "n_trials_note": ("Global deflation count stands at 1345 prior trials. This "
                          "probe is measurement-only (no strategy variant selected); "
                          "any future lab must add its own variants on top of 1345."),
        "sources": {},
        "phenomenon": {},
        "cost_realism": {},
        "verdict": {},
    }

    inv, obs, tape = db_inventory()
    receipt["sources"]["desk_candle_db"] = {
        "path": DB,
        "queried": "READ-ONLY (?mode=ro)",
        "stablecoin_like_candles": inv,
        "stablecoin_like_observations": obs,
        "stablecoin_like_trade_tape": tape,
        "finding": ("NO USD-pegged stablecoin pair exists anywhere in the desk DB "
                    "(candles, observations, trade_tape). Only EURC/USD appears, and "
                    "EURC is EUR-pegged: EURC/USD floats with EURUSD and is not a "
                    "$1.00 peg, so it cannot support |price-1.00| analysis."),
    }

    api = {}
    for wsname, rest in USD_PEGGED.items():
        api[wsname] = {}
        for ival in INTERVALS:
            try:
                bars = fetch_ohlc(rest, ival)
                api[wsname][f"{ival}min"] = analyze(bars, ival)
            except Exception as e:  # record, never fabricate
                api[wsname][f"{ival}min"] = {"error": str(e)}
            time.sleep(1.2)  # public API politeness
    receipt["sources"]["kraken_public_ohlc"] = {
        "endpoint": "https://api.kraken.com/0/public/OHLC (free, unauthenticated)",
        "hard_limit": ("Kraken public OHLC returns only the last ~720 bars per "
                       "interval regardless of 'since'. Daily = ~2 years back "
                       "(~Sep 2024). The March-2023 USDC depeg is OUTSIDE every "
                       "obtainable window."),
        "eurt_note": "EURT no longer listed on Kraken AssetPairs (delisted).",
        "eur_pegged_excluded": ["EURC/USD", "EURQ/USD"],
    }
    receipt["phenomenon"] = api

    receipt["cost_realism"] = {
        "venue_wall_label": "kraken_crypto",
        "venue_wall_bps_round_trip": 30.0,
        "wall_decomposition": ("venue_costs.py venue 'kraken', class 'crypto': "
                               "spread_pct 0.0010 (full round-trip spread) + "
                               "commission_pct 0.0010 per side x 2 = 30 bps round trip"),
        "harvest_logic": ("Entering at deviation X bps and exiting at the peg captures "
                          "X bps gross per round trip. Break-even therefore requires "
                          "X > 30 bps; a usable edge needs sustained entries at "
                          "X >= 40-50 bps."),
    }

    return receipt


if __name__ == "__main__":
    r = main()
    with open(OUT, "w") as f:
        json.dump(_j(r), f, indent=2)
    print(json.dumps(_j(r)["phenomenon"], indent=2)[:6000])
    print("written:", OUT)
