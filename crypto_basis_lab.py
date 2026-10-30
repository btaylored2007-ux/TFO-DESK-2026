#!/usr/bin/env python3
"""crypto_basis_lab.py — Honest COLD test of CROSS-SECTIONAL FUNDING/BASIS CARRY as
a DIRECTIONAL spot signal, on the desk's OWN futures_analytics data.

RESEARCH ONLY. Paper. No orders, no capital, no pre-registration. Read-only.

MECHANISM & how it differs from what we've done
-----------------------------------------------
- Distinct from the VALIDATED delta-neutral basis carry (carry_paper_desk): that
  harvests funding market-neutral. Here we ask the DIRECTIONAL question — does the
  funding rate / futures basis predict SPOT returns cross-sectionally? Prior: high
  funding = longs pay = crowded = spot underperforms (contrarian).
- Adjacent to positioning_lab (which used long/short RATIO). Funding/basis is the
  PRICE of leverage, not the quantity of positioning — related but not identical.
  We run it and let the harness say whether it adds anything.

HONEST CONSTRAINT: same young data as positioning (~13 days of futures_analytics).
Effective-N will be small; the most likely honest outcome is "insufficient / cost-
killed", making it another ripening item. Reported cold either way.

Signals: funding (relativeRate close) and future-basis, cross-sectional terciles,
dollar-neutral, both signs, horizons {1h,2h,4h}. Costs 15 bps/side (30 bps wall).
"""
import json
import math
import sqlite3
from datetime import datetime, timezone

import numpy as np

import quant_validation as qv

DB = "/Users/test/Applications/KrakenQuantDesk/kraken_bot/market_intelligence.sqlite"
RECEIPT = "reports/crypto_basis_lab.json"
NOTE = "reports/research_crypto_basis.md"
HOUSE_PRIOR_TRIALS = 1167
PER_SIDE_BPS = 15.0
BAR_MIN = 30
HOLD_BARS = {"1h": 2, "2h": 4, "4h": 8}
TERCILE = 1.0 / 3.0
MIN_NAMES = 9


def _bar(t_sec):
    return (int(t_sec) // (BAR_MIN * 60)) * (BAR_MIN * 60)


def _load_signal(con, metric):
    """{symbol: {bar_ts: value}} reconstructed from overlapping payloads.
    funding -> data.relativeRate close; future-basis -> data.basis."""
    panel = {}
    rows = con.execute(
        "SELECT spot_symbol, payload_json FROM futures_analytics WHERE metric=?",
        (metric,)).fetchall()
    for sym, pj in rows:
        try:
            p = json.loads(pj)
            ts = p["timestamp"]
            data = p["data"]
        except Exception:
            continue
        d = panel.setdefault(sym, {})
        if metric == "funding":
            arr = data.get("relativeRate") if isinstance(data, dict) else None
            if not arr:
                continue
            for t, v in zip(ts, arr):
                try:
                    val = float(v[3]) if isinstance(v, list) else float(v)
                except (TypeError, ValueError):
                    continue
                d[_bar(int(t) // 1000 if t > 2e10 else t)] = val
        else:  # future-basis
            arr = data.get("basis") if isinstance(data, dict) else None
            if not arr:
                continue
            for t, v in zip(ts, arr):
                try:
                    val = float(v)
                except (TypeError, ValueError):
                    continue
                d[_bar(t)] = val
    return panel


def _load_close(con, symbols):
    panel = {}
    qm = ",".join("?" * len(symbols))
    for sym, ts, close in con.execute(
            f"SELECT symbol, ts, close FROM candles WHERE interval_min={BAR_MIN} "
            f"AND symbol IN ({qm})", list(symbols)).fetchall():
        panel.setdefault(sym, {})[_bar(ts)] = float(close)
    return panel


def _matrices(sig, closes, grid):
    syms = sorted(set(sig) & set(closes))
    T = len(grid)
    S = np.full((T, len(syms)), np.nan)
    C = np.full((T, len(syms)), np.nan)
    for j, s in enumerate(syms):
        for i, b in enumerate(grid):
            if b in sig[s]:
                S[i, j] = sig[s][b]
            if b in closes[s]:
                C[i, j] = closes[s][b]
    RET = np.full_like(C, np.nan)
    RET[:-1] = (C[1:] - C[:-1]) / C[:-1]
    return syms, S, RET


def _pnl(S, RET, hold, sign):
    T, N = S.shape
    net, gross, turns = [], [], []
    prev_w = np.zeros(N)
    for t in range(0, T - hold, hold):
        s = S[t].copy()
        fwd = np.ones(N)
        for k in range(hold):
            r = RET[t + k]
            fwd *= np.where(np.isnan(r), 1.0, 1.0 + r)
        fwd -= 1.0
        valid = ~np.isnan(s) & np.isfinite(fwd)
        idx = np.where(valid)[0]
        if idx.size < MIN_NAMES:
            continue
        order = np.argsort(s[idx])
        k = max(1, int(len(order) * TERCILE))
        low, high = idx[order[:k]], idx[order[-k:]]
        w = np.zeros(N)
        if sign < 0:            # contrarian: short high-funding, long low
            w[low] = 1.0 / k; w[high] = -1.0 / k
        else:
            w[low] = -1.0 / k; w[high] = 1.0 / k
        g = float(np.nansum(w * fwd))
        turnover = float(np.nansum(np.abs(w - prev_w)))
        gross.append(g); net.append(g - turnover * PER_SIDE_BPS / 10000.0)
        turns.append(turnover); prev_w = w
    return np.array(net), np.array(gross), np.array(turns)


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    results = {}
    variants, trial_sharpes = [], []
    n_here = 0
    for metric in ["funding", "future-basis"]:
        sig = _load_signal(con, metric)
        closes = _load_close(con, list(sig.keys()))
        bars = set()
        for d in sig.values():
            bars |= set(d.keys())
        grid = sorted(bars)
        span = (grid[-1] - grid[0]) / 86400.0 if len(grid) > 1 else 0.0
        syms, S, RET = _matrices(sig, closes, grid)
        for sign, snm in [(-1, "contrarian"), (+1, "momentum")]:
            for hk, hold in HOLD_BARS.items():
                net, gross, turns = _pnl(S, RET, hold, sign)
                n_here += 1
                if net.size < 8:
                    continue
                m = qv.moments(net)
                trial_sharpes.append(m["sharpe"])
                key = f"{metric}_{snm}_{hk}"
                results[key] = (net, gross)
                variants.append({
                    "name": key, "metric": metric, "sign": snm, "horizon": hk,
                    "n_rebalances": int(net.size),
                    "span_days": round(span, 2),
                    "avg_turnover": round(float(np.mean(turns)), 3),
                    "gross_mean_bps": round(float(np.mean(gross)) * 10000, 2),
                    "net_mean_bps": round(float(np.mean(net)) * 10000, 2),
                    "net_sharpe_per_rebal": round(m["sharpe"], 4),
                })

    scored = sorted(variants, key=lambda v: v["net_sharpe_per_rebal"], reverse=True)
    best_eval = None
    eff = None
    if scored:
        b = scored[0]
        net, gross = results[b["name"]]
        eff = qv.effective_bets(net.size, 0.6)
        hold = HOLD_BARS[b["horizon"]]
        ppy = (365 * 24 * 60) / (BAR_MIN * hold)
        best_eval = qv.evaluate(b["name"], net, n_trials=HOUSE_PRIOR_TRIALS + n_here,
                                trial_sharpes=trial_sharpes, periods_per_year=ppy,
                                trades_per_period=1.0, gross_returns=gross,
                                n_effective=eff["independent_bets"])
    out = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "mechanism": "cross-sectional funding/basis carry as directional spot signal — proprietary data",
        "house_prior_trials": HOUSE_PRIOR_TRIALS, "lab_variants": n_here,
        "total_deflation_trials": HOUSE_PRIOR_TRIALS + n_here,
        "per_side_bps": PER_SIDE_BPS, "cost_wall_bps": 30.0,
        "best_variant": scored[0]["name"] if scored else None,
        "best_effective_bets": eff,
        "variants": variants,
        "best_evaluate": best_eval,
        "verdict_line": qv.verdict_line(best_eval) if best_eval else "no scorable variant",
    }
    con.close()
    with open(RECEIPT, "w") as f:
        json.dump(_j(out), f, indent=2)

    print("=" * 78)
    print("CRYPTO BASIS/FUNDING LAB — cross-sectional directional (proprietary data)")
    print("=" * 78)
    for v in scored:
        print(f"  {v['name']:<26} netSh {v['net_sharpe_per_rebal']:+.3f}  "
              f"net {v['net_mean_bps']:+.2f}bps  gross {v['gross_mean_bps']:+.2f}bps  "
              f"turn {v['avg_turnover']:.2f}  n={v['n_rebalances']}  span {v['span_days']}d")
    if best_eval:
        e = best_eval
        print(f"\n  HARNESS best NET: n_eff {e['n_effective']}  t {e['t_stat']}  p {e['p_value']}  "
              f"net/trade {e['per_trade_bps']}bps")
        print(f"    deflated_sharpe {e['deflated_sharpe']}  survives_deflation {e['survives_deflation']}")
        print(f"    clears_wall {e['clears_cost_wall']}")
        print(f"\n  VERDICT: {out['verdict_line']}")
    print(f"\n  receipt -> {RECEIPT}")
    return out


def _j(o):
    if isinstance(o, dict):
        return {k: _j(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_j(x) for x in o]
    if isinstance(o, np.floating):
        return None if (math.isnan(o) or math.isinf(o)) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else o
    return o


if __name__ == "__main__":
    main()
