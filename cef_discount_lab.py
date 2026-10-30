#!/usr/bin/env python3
"""cef_discount_lab.py — Honest COLD test of the CLOSED-END-FUND DISCOUNT-TO-NAV
REVERSION anomaly (Pontiff 1995/1997) — a genuinely distinct mechanism: the signal
is price-vs-NAV, not price history.

RESEARCH ONLY. Paper desk. No orders, no capital, no pre-registration, no secrets.
Read-only. Writes only its own JSON receipt + note.

WHY DISTINCT
------------
A closed-end fund's market price can drift from the NAV of the assets it holds.
The documented anomaly: buy funds at WIDE discounts / short funds at PREMIUMS, and
the discount mean-reverts. The signal is orthogonal to every price-history factor
we have killed — it is a market-structure / sentiment mechanism.

DATA (honest, free)
-------------------
Market price: plain Yahoo ticker (raw close + cash distributions).
NAV: the Yahoo "X<TICKER>X" pseudo-ticker carries the fund's official NAV series
(verified: e.g. RVT price 18.85 vs NAV 21.44 = a real -12% discount; PTY at a
+2.8% premium). NAV history runs 15-25y for most names. discount = price/NAV - 1.

DESIGN
------
  universe : ~30 liquid CEFs across bond/credit, equity/covered-call, muni
  signals  : (1) discount LEVEL cross-section; (2) discount Z vs each fund's own
             trailing 24m mean/std (the "unusually cheap vs its own norm" version)
  trade    : dollar-neutral, long widest-discount tercile / short narrowest, monthly
  costs    : 25 bps/side (CEFs are less liquid than ETFs) + short-borrow swept 1/3%
  scoring  : gross vs net, quant_validation on NET, honest search count, effective-N
             discounted for discount persistence, crash windows, beta vs SPY.

Report real output, win or lose.
"""
import json
import math
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import quant_validation as qv

CACHE = "/private/tmp/claude-501/-Users-test/326d8bdd-e63d-4126-a322-4582de2ba921/scratchpad/cef_cache"
os.makedirs(CACHE, exist_ok=True)
RECEIPT = "reports/cef_discount_lab.json"
NOTE = "reports/research_cef_discount.md"

HOUSE_PRIOR_TRIALS = 1163
COST_BPS_SIDE = 25.0
VOL_TARGET = 0.10
TERCILE = 1.0 / 3.0

CEFS = ["PDI", "PTY", "PCN", "PFN", "RCS", "GOF", "DSL", "JPC", "JQC", "NCV",
        "RVT", "ADX", "EOS", "ETV", "ETY", "ETW", "EXG", "UTF", "BME", "BDJ",
        "BOE", "GAB", "CET", "NEA", "NAD", "NZF", "VMO", "VKQ", "BST", "BGY"]


def yf_hist(ticker, auto=False):
    tag = ("ta_" if auto else "tr_") + ticker
    path = os.path.join(CACHE, "%s.csv" % tag)
    if os.path.exists(path):
        return pd.read_csv(path, index_col=0, parse_dates=True)
    import yfinance as yf
    h = yf.Ticker(ticker).history(period="max", auto_adjust=auto, actions=True)
    if h.empty:
        raise RuntimeError("no data %s" % ticker)
    df = pd.DataFrame({"close": h["Close"],
                       "div": h.get("Dividends", pd.Series(0.0, index=h.index))})
    df.index = df.index.tz_localize(None)
    df.to_csv(path)
    return df


def build_panels():
    """Monthly total-return R and discount D (price/NAV - 1)."""
    rets, disc, ok = {}, {}, []
    for t in CEFS:
        try:
            px = yf_hist(t, auto=False)                 # raw price + distributions
            nav = yf_hist("X%sX" % t, auto=False)       # NAV series
        except Exception:
            continue
        pm = px.resample("ME").agg({"close": "last", "div": "sum"}).dropna(subset=["close"])
        nm = nav.resample("ME").agg({"close": "last"}).dropna()
        common = pm.index.intersection(nm.index)
        if len(common) < 60:
            continue
        pm, nm = pm.loc[common], nm.loc[common]
        tr = (pm["close"] + pm["div"]) / pm["close"].shift(1) - 1.0
        d = pm["close"] / nm["close"] - 1.0
        rets[t] = tr
        disc[t] = d
        ok.append(t)
    return pd.DataFrame(rets), pd.DataFrame(disc), ok


def factor(R, SIG, borrow_annual, higher_is_long):
    """Dollar-neutral tercile factor. higher_is_long=True -> long high-signal names.
    For discount reversion we LONG the widest discount = the LOWEST (most negative)
    discount, so we pass the signal as -discount and higher_is_long=True."""
    idx = R.index
    dates, net, gross, turns = [], [], [], []
    prev_w = pd.Series(0.0, index=R.columns)
    rank_prev, autocorrs = None, []
    bm = borrow_annual / 12.0
    for i in range(1, len(idx)):
        t0, t1 = idx[i - 1], idx[i]
        sig = SIG.loc[t0].dropna()
        fr = R.loc[t1]
        names = [n for n in sig.index if n in fr.index and not np.isnan(fr[n])]
        sig = sig[names]
        if len(sig) < 6:
            continue
        ranks = sig.rank()
        if rank_prev is not None:
            c = ranks.index.intersection(rank_prev.index)
            if len(c) >= 4:
                autocorrs.append(np.corrcoef(ranks[c], rank_prev[c])[0, 1])
        rank_prev = ranks
        k = max(1, int(len(sig) * TERCILE))
        order = sig.sort_values()
        low, high = order.index[:k], order.index[-k:]
        w = pd.Series(0.0, index=R.columns)
        if higher_is_long:
            w[high] = 1.0 / k; w[low] = -1.0 / k
        else:
            w[high] = -1.0 / k; w[low] = 1.0 / k
        g = float((w * R.loc[t1].reindex(w.index).fillna(0.0)).sum())
        turnover = float((w - prev_w).abs().sum())
        cost = turnover * (COST_BPS_SIDE / 10000.0)
        short_cost = float(w[w < 0].abs().sum()) * bm
        dates.append(t1); gross.append(g); net.append(g - cost - short_cost)
        turns.append(turnover); prev_w = w
    ac = float(np.nanmean(autocorrs)) if autocorrs else 0.8
    di = pd.DatetimeIndex(dates)
    return pd.Series(net, index=di), pd.Series(gross, index=di), pd.Series(turns, index=di), ac


def zscore(D, win=24):
    return (D - D.rolling(win).mean()) / D.rolling(win).std()


def vol_scale(s, target=VOL_TARGET):
    v = s.std(ddof=1) * math.sqrt(12)
    return s * (target / v) if v > 0 else s


def maxdd(s):
    eq = (1 + s).cumprod()
    return float((eq / eq.cummax() - 1).min())


def crash(s, lo, hi):
    w = s[(s.index >= lo) & (s.index <= hi)]
    return round(float((1 + w).prod() - 1) * 100, 2) if len(w) else None


def beta_spy(s):
    try:
        spy = yf_hist("SPY", auto=True)
    except Exception:
        return None
    m = spy.resample("ME").agg({"close": "last"}).dropna()
    tr = m["close"] / m["close"].shift(1) - 1.0
    c = s.index.intersection(tr.index)
    if len(c) < 24:
        return None
    x, y = tr.reindex(c).values, s.reindex(c).values
    return round(float(np.polyfit(x, y, 1)[0]), 3) if np.nanstd(x) > 0 else None


def main():
    R, D, ok = build_panels()
    Z = zscore(D)

    variants, trial_sharpes, series = [], [], {}
    n_here = 0
    for borrow in [0.01, 0.03]:
        for signame, sig in [("disc_level", -D), ("disc_z", -Z)]:
            net, gross, turn, ac = factor(R, sig, borrow, higher_is_long=True)
            n_here += 1
            m = qv.moments(net.values)
            trial_sharpes.append(m["sharpe"])
            series[f"{signame}_b{int(borrow*100)}"] = (net, gross, ac)
            variants.append({
                "name": f"{signame}_borrow{int(borrow*100)}pct",
                "signal": signame, "borrow_annual": borrow,
                "n_months": int(net.size),
                "net_sharpe_ann": round(m["sharpe"] * math.sqrt(12), 3),
                "net_cagr_pct": round(((1 + net).prod() ** (12 / max(1, net.size)) - 1) * 100, 2),
                "maxdd_pct": round(maxdd(net) * 100, 2),
                "avg_turnover": round(float(turn.mean()), 3),
            })

    scored = sorted([v for v in variants], key=lambda v: v["net_sharpe_ann"], reverse=True)
    best = scored[0]
    bkey = f"{best['signal']}_b{int(best['borrow_annual']*100)}"
    net, gross, ac = series[bkey]
    net_s = vol_scale(net)
    eff = qv.effective_bets(net_s.size, ac)
    ev = qv.evaluate(f"cef_{best['name']}", net_s.values,
                     n_trials=HOUSE_PRIOR_TRIALS + n_here, trial_sharpes=trial_sharpes,
                     periods_per_year=12, trades_per_period=1.0,
                     gross_returns=gross.reindex(net_s.index).values, n_effective=eff["independent_bets"])
    # (n_strategies, n_obs) — strategies in ROWS. The original column_stack call
    # was transposed; caught by the 2026-08-14 workflow audit of a sibling lab.
    wrc = qv.whites_reality_check(
        np.vstack([series[k][0].reindex(net_s.index).fillna(0).values
                   for k in series]))

    out = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "mechanism": "closed-end-fund discount-to-NAV reversion (Pontiff) — off-frontier, free NAV data",
        "universe_ok": ok, "n_funds": len(ok),
        "panel_start": str(R.index[0].date()), "panel_end": str(R.index[-1].date()),
        "house_prior_trials": HOUSE_PRIOR_TRIALS, "lab_variants": n_here,
        "total_deflation_trials": HOUSE_PRIOR_TRIALS + n_here,
        "cost_bps_side": COST_BPS_SIDE,
        "best_variant": best["name"],
        "best_rank_autocorr": round(ac, 3),
        "best_effective_bets": eff,
        "best_beta_to_spy": beta_spy(net_s),
        "crash_2008_pct": crash(net_s, pd.Timestamp("2008-06-01"), pd.Timestamp("2009-03-31")),
        "crash_2020_pct": crash(net_s, pd.Timestamp("2020-02-01"), pd.Timestamp("2020-04-30")),
        "crash_2022_pct": crash(net_s, pd.Timestamp("2022-01-01"), pd.Timestamp("2022-10-31")),
        "variants": variants,
        "whites_reality_check": wrc,
        "best_evaluate": ev,
        "verdict_line": qv.verdict_line(ev),
    }
    with open(RECEIPT, "w") as f:
        json.dump(_j(out), f, indent=2)

    print("=" * 80)
    print("CEF DISCOUNT-TO-NAV REVERSION LAB (Pontiff) — off-frontier, free NAV")
    print("=" * 80)
    print(f"  universe : {len(ok)} closed-end funds   panel {out['panel_start']} .. {out['panel_end']}")
    print(f"  deflation: {out['total_deflation_trials']} trials (house {HOUSE_PRIOR_TRIALS} + {n_here})")
    print(f"  best rank-autocorr {ac:.3f}  beta->SPY {out['best_beta_to_spy']}")
    print("  variants (annualised net Sharpe, best first):")
    for v in scored:
        print(f"    {v['name']:<26} netSh {v['net_sharpe_ann']:+.3f}  "
              f"CAGR {v['net_cagr_pct']:+.2f}%  maxDD {v['maxdd_pct']:.1f}%  "
              f"turn {v['avg_turnover']:.2f}  n={v['n_months']}")
    print(f"\n  crash 2008 {out['crash_2008_pct']}%  2020 {out['crash_2020_pct']}%  2022 {out['crash_2022_pct']}%")
    print(f"  White's reality check p={wrc['p_value']} survives {wrc['survives']}")
    print("\n  HARNESS on best NET variant (vol-targeted):")
    print(f"    n_obs {ev['n_obs']}  n_effective {ev['n_effective']}  t {ev['t_stat']}  p {ev['p_value']}")
    print(f"    gross/trade {ev.get('gross_per_trade_bps')}bps  net/trade {ev['per_trade_bps']}bps")
    print(f"    deflated_sharpe {ev['deflated_sharpe']}  survives_deflation {ev['survives_deflation']}")
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
