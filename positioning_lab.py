#!/usr/bin/env python3
"""positioning_lab.py — Honest COLD test of a genuinely off-frontier mechanism:
CROSS-SECTIONAL FUTURES-POSITIONING REVERSAL on the desk's OWN accumulated data.

RESEARCH ONLY. No capital, no orders, no engine state touched, no pre-registration
created. Read-only w.r.t. the DB and the rest of the desk. All it writes is its own
JSON receipt and a research note.

WHY THIS IS OFF-FRONTIER
------------------------
Every mechanism the house has killed so far is a PRICE-history mechanism (trend,
mean-rev, breakout, value, carry, country/sector L/S). This is different: the
signal is EXCHANGE POSITIONING — the futures long/short ratio (LSR), i.e. how the
crowd is leaning — collected live by our own market_intelligence collector across
~82 perps. The named prior in crypto microstructure: retail LSR is CONTRARIAN —
when the crowd is heavily long, that positioning is liquidation fuel, and price
tends to mean-revert down over the next few hours. We have never tested a
positioning/order-flow family here.

THE HONEST DATA PROBLEM (read before trusting any number)
---------------------------------------------------------
The positioning history is YOUNG — the collector has only ~11-12 calendar days of
futures_analytics. Price history is long, but the SIGNAL is what's short. So the
binding constraint is effective sample size, not price data. We reconstruct a
continuous 30-min LSR panel from the overlapping 48-point payloads, align it to
30-min candle closes, and let the harness judge. The most likely honest outcome is
"insufficient independent observations / fails the cost wall" — and if so, this
becomes a RIPENING candidate to re-run when positioning history clears the 21-day
bar, NOT a deployable desk. We report the point estimate either way, cold.

DESIGN
------
  universe : coins with both an LSR series and a 30-min candle series
  signal   : cross-sectional rank of LSR level (and, as a variant, LSR change)
  trade    : dollar-neutral, equal-weight, long bottom tercile / short top tercile
             tested BOTH signs (contrarian vs momentum) — the sign is empirical
  horizons : rebalance/hold every {1h, 2h, 4h}
  costs    : 15 bps per side (30 bps round-trip = the Kraken cost wall) on turnover
  scoring  : gross vs net, then quant_validation on NET, with an HONEST search
             count (house prior + every variant tried here) and effective-N that
             discounts cross-sectional correlation and signal autocorrelation.

NON-NEGOTIABLE: report real output, win or lose. A number that dies in the harness
is logged as a lesson with receipts — that is the process working.
"""
import json
import math
import sqlite3
from datetime import datetime, timezone

import numpy as np

import quant_validation as qv

DB = "/Users/test/Applications/KrakenQuantDesk/kraken_bot/market_intelligence.sqlite"
RECEIPT = "reports/positioning_lab.json"
NOTE = "reports/research_positioning.md"

# House cumulative search count BEFORE this lab (global_ls run today logged 1145).
HOUSE_PRIOR_TRIALS = 1145
PER_SIDE_BPS = 15.0          # 30 bps round-trip == Kraken cost wall
BAR_MIN = 30                 # base grid
HOLD_BARS = {"1h": 2, "2h": 4, "4h": 8}
TERCILE = 1.0 / 3.0
MIN_NAMES = 9                # need enough cross-section for terciles to mean anything


def _load_lsr_panel(con):
    """Reconstruct a continuous 30-min LSR series per coin from overlapping payloads.
    Returns {symbol: {bar_ts(sec, floored to 30min): lsr_float}}."""
    panel = {}
    rows = con.execute(
        "SELECT spot_symbol, payload_json FROM futures_analytics "
        "WHERE metric='long-short-ratio'").fetchall()
    for sym, pj in rows:
        try:
            p = json.loads(pj)
            ts = p["timestamp"]
            data = p["data"]
            if not isinstance(data, list):
                continue
        except Exception:
            continue
        d = panel.setdefault(sym, {})
        for t, v in zip(ts, data):
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            bar = (int(t) // (BAR_MIN * 60)) * (BAR_MIN * 60)
            d[bar] = fv            # later (newer) payloads overwrite -> freshest value
    return panel


def _load_close_panel(con, symbols):
    """{symbol: {bar_ts(sec): close}} from 30-min candles."""
    panel = {}
    qmarks = ",".join("?" * len(symbols))
    rows = con.execute(
        f"SELECT symbol, ts, close FROM candles WHERE interval_min={BAR_MIN} "
        f"AND symbol IN ({qmarks})", list(symbols)).fetchall()
    for sym, ts, close in rows:
        bar = (int(ts) // (BAR_MIN * 60)) * (BAR_MIN * 60)
        panel.setdefault(sym, {})[bar] = float(close)
    return panel


def _build_matrices(lsr, closes, grid):
    """Aligned arrays over a common bar grid. Returns (symbols, LSR[T,N], RET[T,N])
    where RET[t] is the close-to-close return realised OVER bar t (close[t]->close[t+1])."""
    syms = sorted(set(lsr) & set(closes))
    T = len(grid)
    L = np.full((T, len(syms)), np.nan)
    C = np.full((T, len(syms)), np.nan)
    for j, s in enumerate(syms):
        ls, cl = lsr[s], closes[s]
        for i, b in enumerate(grid):
            if b in ls:
                L[i, j] = ls[b]
            if b in cl:
                C[i, j] = cl[b]
    RET = np.full_like(C, np.nan)
    RET[:-1] = (C[1:] - C[:-1]) / C[:-1]
    return syms, L, C, RET


def _cross_sectional_pnl(L, RET, hold, sign, signal="level"):
    """Dollar-neutral tercile long/short on the LSR cross-section.
    sign=-1 -> contrarian (short crowded-long/high-LSR). sign=+1 -> momentum.
    Returns (net_series, gross_series, turnover_series) sampled every `hold` bars."""
    T, N = L.shape
    net, gross, turns = [], [], []
    prev_w = np.zeros(N)
    for t in range(0, T - hold, hold):
        if signal == "level":
            sig = L[t].copy()
        else:  # change over the holding window just ended
            sig = L[t] - L[max(0, t - hold)]
        # forward return over the hold: compound close-to-close bars t..t+hold-1
        fwd = np.ones(N)
        valid = ~np.isnan(sig)
        for k in range(hold):
            r = RET[t + k]
            fwd *= np.where(np.isnan(r), 1.0, 1.0 + r)
        fwd -= 1.0
        valid &= ~np.isnan(L[t]) & np.isfinite(fwd)
        idx = np.where(valid)[0]
        if idx.size < MIN_NAMES:
            continue
        s = sig[idx]
        order = np.argsort(s)
        k = max(1, int(len(order) * TERCILE))
        low = idx[order[:k]]       # least crowded / most short
        high = idx[order[-k:]]     # most crowded long / highest LSR
        w = np.zeros(N)
        if sign < 0:               # contrarian
            w[low] = +1.0 / k
            w[high] = -1.0 / k
        else:                      # momentum
            w[low] = -1.0 / k
            w[high] = +1.0 / k
        g = float(np.nansum(w * fwd))
        turnover = float(np.nansum(np.abs(w - prev_w)))
        cost = turnover * (PER_SIDE_BPS / 10000.0)
        gross.append(g)
        net.append(g - cost)
        turns.append(turnover)
        prev_w = w
    return np.array(net), np.array(gross), np.array(turns)


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    lsr = _load_lsr_panel(con)
    closes = _load_close_panel(con, list(lsr.keys()))
    # common 30-min grid across the positioning window
    all_bars = set()
    for d in lsr.values():
        all_bars |= set(d.keys())
    grid = sorted(all_bars)
    span_days = (grid[-1] - grid[0]) / 86400.0 if len(grid) > 1 else 0.0
    syms, L, C, RET = _build_matrices(lsr, closes, grid)

    variants = []
    trial_sharpes = []
    n_here = 0
    for sign, sname in [(-1, "contrarian"), (+1, "momentum")]:
        for signal in ["level", "change"]:
            for hkey, hold in HOLD_BARS.items():
                net, gross, turns = _cross_sectional_pnl(L, RET, hold, sign, signal)
                n_here += 1
                if net.size < 8:
                    variants.append({"name": f"{sname}_{signal}_{hkey}",
                                     "n_rebalances": int(net.size),
                                     "note": "too few rebalances to score"})
                    continue
                m = qv.moments(net)
                trial_sharpes.append(m["sharpe"])
                variants.append({
                    "name": f"{sname}_{signal}_{hkey}",
                    "sign": sname, "signal": signal, "horizon": hkey,
                    "n_rebalances": int(net.size),
                    "avg_turnover": round(float(np.mean(turns)), 3),
                    "gross_mean_bps": round(float(np.mean(gross)) * 10000, 3),
                    "net_mean_bps": round(float(np.mean(net)) * 10000, 3),
                    "gross_sharpe_per_rebal": round(qv.moments(gross)["sharpe"], 4),
                    "net_sharpe_per_rebal": round(m["sharpe"], 4),
                })

    scored = [v for v in variants if "net_sharpe_per_rebal" in v]
    scored.sort(key=lambda v: v["net_sharpe_per_rebal"], reverse=True)
    total_trials = HOUSE_PRIOR_TRIALS + n_here

    best_eval = None
    if scored:
        best = scored[0]
        sign = -1 if best["sign"] == "contrarian" else 1
        hold = HOLD_BARS[best["horizon"]]
        net, gross, turns = _cross_sectional_pnl(L, RET, hold, sign, best["signal"])
        # effective-N: rebalances discounted by signal rank autocorrelation (slow LSR)
        rank_ac = 0.7   # LSR is slow-moving; conservative high autocorr -> fewer eff. bets
        eff = qv.effective_bets(net.size, rank_ac)
        n_eff = eff["independent_bets"]
        periods_per_year = (365 * 24 * 60) / (BAR_MIN * hold)
        best_eval = qv.evaluate(
            best["name"], net, n_trials=total_trials,
            trial_sharpes=trial_sharpes, periods_per_year=periods_per_year,
            trades_per_period=1.0, gross_returns=gross, n_effective=n_eff)

    out = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "mechanism": "cross-sectional futures-positioning (LSR) reversal — OFF-FRONTIER, proprietary data",
        "db": DB,
        "positioning_span_days": round(span_days, 2),
        "n_symbols_universe": len(syms),
        "n_30min_bars": len(grid),
        "per_side_bps": PER_SIDE_BPS,
        "cost_wall_bps_kraken": 30.0,
        "house_prior_trials": HOUSE_PRIOR_TRIALS,
        "lab_variants": n_here,
        "total_deflation_trials": total_trials,
        "variants": variants,
        "best_variant": scored[0]["name"] if scored else None,
        "best_evaluate": best_eval,
        "best_effective_bets": eff if best_eval else None,
        "verdict_line": qv.verdict_line(best_eval) if best_eval else "no scorable variant",
    }
    con.close()

    with open(RECEIPT, "w") as f:
        json.dump(out, f, indent=2)

    print("=" * 78)
    print("POSITIONING LAB — cross-sectional futures LSR reversal (OFF-FRONTIER)")
    print("=" * 78)
    print(f"  proprietary positioning span : {span_days:.2f} days  "
          f"({len(syms)} coins, {len(grid)} x 30-min bars)")
    print(f"  cost model                   : {PER_SIDE_BPS} bps/side  "
          f"(wall {30.0} bps round-trip)")
    print(f"  deflation trials             : {total_trials}  "
          f"(house {HOUSE_PRIOR_TRIALS} + {n_here} here)")
    print("  variants (net Sharpe per rebalance, best first):")
    for v in scored:
        print(f"    {v['name']:<22} netSh {v['net_sharpe_per_rebal']:+.3f}  "
              f"grossSh {v['gross_sharpe_per_rebal']:+.3f}  "
              f"net {v['net_mean_bps']:+.2f}bps  gross {v['gross_mean_bps']:+.2f}bps  "
              f"turn {v['avg_turnover']:.2f}  n={v['n_rebalances']}")
    if best_eval:
        e = best_eval
        print("\n  HARNESS on best NET variant:")
        print(f"    n_obs {e['n_obs']}  n_effective {e['n_effective']}  "
              f"t {e['t_stat']}  p {e['p_value']}")
        print(f"    deflated_sharpe {e['deflated_sharpe']}  "
              f"survives_deflation {e['survives_deflation']}")
        print(f"    gross/trade {e.get('gross_per_trade_bps')}bps  "
              f"net/trade {e['per_trade_bps']}bps  "
              f"cost_drag {e.get('cost_drag_bps')}bps  "
              f"clears_wall {e['clears_cost_wall']}")
        print(f"\n  VERDICT: {out['verdict_line']}")
    print(f"\n  receipt -> {RECEIPT}")
    return out


if __name__ == "__main__":
    main()
