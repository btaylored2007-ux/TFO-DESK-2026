#!/usr/bin/env python3
"""cross_asset_carry_lab.py — Honest COLD test of the CROSS-ASSET CARRY FACTOR
(Koijen, Moskowitz, Pedersen, Vrugt 2018, "Carry") — the single highest-prior
mechanism the house has NOT yet tested.

RESEARCH ONLY. Paper desk. No orders, no capital, no pre-registration, no secrets.
Read-only w.r.t. the rest of the desk. Writes only its own JSON receipt + note.

WHY THIS IS DISTINCT FROM WHAT WE ALREADY KILLED
------------------------------------------------
We killed FX-carry-ALONE (fx_carry_lab) and commodity-carry-ALONE
(commodity_carry_lab). The Koijen et al. result is NOT that any single carry
sleeve is a goldmine — it is that carry harvested SIMULTANEOUSLY across asset
classes diversifies into a factor with a high Sharpe and low equity beta, because
the sleeves' crashes are imperfectly correlated. That diversification is the
untested claim. This lab builds two carry sleeves the house can source cleanly
from FREE data and asks: does COMBINING them survive our cost + search-deflation
gate, and is it genuinely low-beta to the firm's existing +0.24 US-equity tilt?

SLEEVES (within-class, dollar-neutral each — keeps them closer to market-neutral,
which is the honest version of the claim)
  EQUITY : ~16 liquid single-country equity ETFs. Carry = trailing-12m cash
           dividend yield. Long top tercile yield / short bottom tercile.
  BONDS  : ~9 liquid bond ETFs (USTs across the curve, IG, HY, EM, intl). Carry =
           trailing-12m distribution yield (a proxy for term + credit carry).
           Long top tercile / short bottom tercile.
  COMBINED = 0.5*EQUITY + 0.5*BONDS, vol-targeted to 10%/yr.

COSTS  : 10 bps/side on turnover + short-borrow drag swept at 1% and 3% annual.
SCORING: gross vs net, then quant_validation on NET, honest search count
         (house prior + every sleeve/variant formed here), effective-N discounted
         for monthly rank persistence, crash windows (2008/2020/2022) measured,
         and equity beta vs SPY reported (the whole point is low beta).

Report real output, win or lose. A number that dies in the harness is a logged
lesson, not a failure.
"""
import io
import json
import math
import os
import urllib.request
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import quant_validation as qv

CACHE = "/private/tmp/claude-501/-Users-test/326d8bdd-e63d-4126-a322-4582de2ba921/scratchpad/xac_cache"
os.makedirs(CACHE, exist_ok=True)
RECEIPT = "reports/cross_asset_carry_lab.json"
NOTE = "reports/research_cross_asset_carry.md"

HOUSE_PRIOR_TRIALS = 1157        # cumulative before this lab (positioning_lab logged 1157)
COST_BPS_SIDE = 10.0
VOL_TARGET = 0.10
TERCILE = 1.0 / 3.0

EQUITY = ["EWJ", "EWG", "EWU", "EWA", "EWC", "EWL", "EWQ", "EWP", "EWZ", "EWW",
          "EWY", "EWT", "EWH", "EWS", "EWM", "EZA"]
BONDS = ["SHY", "IEF", "TLT", "LQD", "HYG", "EMB", "BWX", "TIP", "MUB"]


def yf_hist(ticker, start="2003-01-01"):
    """Daily raw close + cash dividends, cached. Returns DataFrame[close, div]."""
    path = os.path.join(CACHE, "h_%s.csv" % ticker)
    if os.path.exists(path):
        return pd.read_csv(path, index_col=0, parse_dates=True)
    import yfinance as yf
    h = yf.Ticker(ticker).history(start=start, auto_adjust=False, actions=True)
    if h.empty:
        raise RuntimeError("no data %s" % ticker)
    df = pd.DataFrame({"close": h["Close"],
                       "div": h.get("Dividends", pd.Series(0.0, index=h.index))})
    df.index = df.index.tz_localize(None)
    df.to_csv(path)
    return df


def monthly_panels(tickers):
    """Returns (tot_ret[M x N], carry[M x N]) monthly.
    tot_ret = price return + dividends-in-month / prev close.
    carry   = trailing-12m dividends / month-end close (a yield)."""
    rets, carrys, ok = {}, {}, []
    for t in tickers:
        try:
            d = yf_hist(t)
        except Exception:
            continue
        me = d.resample("ME").agg({"close": "last", "div": "sum"})
        me = me.dropna(subset=["close"])
        if len(me) < 48:
            continue
        prev = me["close"].shift(1)
        tr = (me["close"] + me["div"]) / prev - 1.0
        ttm_div = me["div"].rolling(12).sum()
        yld = ttm_div / me["close"]
        rets[t] = tr
        carrys[t] = yld
        ok.append(t)
    R = pd.DataFrame(rets)
    C = pd.DataFrame(carrys)
    return R, C, ok


def sleeve_factor(R, C, borrow_annual):
    """Dollar-neutral tercile carry factor within one sleeve.
    Long top-tercile carry, short bottom-tercile. Monthly. Returns (net, gross,
    turnover) monthly Series and avg rank autocorrelation."""
    idx = R.index
    dates, net, gross, turns = [], [], [], []
    prev_w = pd.Series(0.0, index=R.columns)
    rank_prev = None
    autocorrs = []
    borrow_m = borrow_annual / 12.0
    for i in range(1, len(idx)):
        t0, t1 = idx[i - 1], idx[i]
        sig = C.loc[t0].dropna()
        # need forward return realised over the month t1
        fr = R.loc[t1]
        names = [n for n in sig.index if n in fr.index and not np.isnan(fr[n])]
        sig = sig[names]
        if len(sig) < 6:
            continue
        ranks = sig.rank()
        if rank_prev is not None:
            common = ranks.index.intersection(rank_prev.index)
            if len(common) >= 4:
                autocorrs.append(np.corrcoef(ranks[common], rank_prev[common])[0, 1])
        rank_prev = ranks
        k = max(1, int(len(sig) * TERCILE))
        order = sig.sort_values()
        lo = order.index[:k]       # low carry -> short
        hi = order.index[-k:]      # high carry -> long
        w = pd.Series(0.0, index=R.columns)
        w[hi] = 1.0 / k
        w[lo] = -1.0 / k
        g = float((w * R.loc[t1].reindex(w.index).fillna(0.0)).sum())
        turnover = float((w - prev_w).abs().sum())
        cost = turnover * (COST_BPS_SIDE / 10000.0)
        short_cost = float(w[w < 0].abs().sum()) * borrow_m
        dates.append(t1)           # label by the month the return is REALISED
        gross.append(g)
        net.append(g - cost - short_cost)
        turns.append(turnover)
        prev_w = w
    ac = float(np.nanmean(autocorrs)) if autocorrs else 0.8
    return (pd.Series(net, index=pd.DatetimeIndex(dates)),
            pd.Series(gross, index=pd.DatetimeIndex(dates)),
            pd.Series(turns, index=pd.DatetimeIndex(dates)), ac)


def vol_scale(series, target=VOL_TARGET):
    v = series.std(ddof=1) * math.sqrt(12)
    return series * (target / v) if v > 0 else series


def crash(series, lo, hi):
    w = series[(series.index >= lo) & (series.index <= hi)]
    return round(float((1 + w).prod() - 1) * 100, 2) if len(w) else None


def beta_to_spy(series):
    try:
        spy = yf_hist("SPY")
    except Exception:
        return None
    me = spy.resample("ME").agg({"close": "last", "div": "sum"})
    tr = (me["close"] + me["div"]) / me["close"].shift(1) - 1.0
    common = series.index.intersection(tr.index)
    if len(common) < 24:
        return None
    x = tr.reindex(common).values
    y = series.reindex(common).values
    if np.nanstd(x) == 0:
        return None
    b = np.polyfit(x, y, 1)[0]
    return round(float(b), 3)


def main():
    Req, Ceq, eqok = monthly_panels(EQUITY)
    Rbd, Cbd, bdok = monthly_panels(BONDS)

    variants = []
    trial_sharpes = []
    combos = {}
    n_here = 0
    for borrow in [0.01, 0.03]:
        eq_net, eq_gross, eq_turn, eq_ac = sleeve_factor(Req, Ceq, borrow)
        bd_net, bd_gross, bd_turn, bd_ac = sleeve_factor(Rbd, Cbd, borrow)
        n_here += 2
        trial_sharpes += [qv.moments(eq_net.values)["sharpe"],
                          qv.moments(bd_net.values)["sharpe"]]
        # combined on the common months
        common = eq_net.index.intersection(bd_net.index)
        comb_net = 0.5 * eq_net.reindex(common) + 0.5 * bd_net.reindex(common)
        comb_gross = 0.5 * eq_gross.reindex(common) + 0.5 * bd_gross.reindex(common)
        comb_net_s = vol_scale(comb_net)
        n_here += 1
        trial_sharpes.append(qv.moments(comb_net_s.values)["sharpe"])
        combos[borrow] = (comb_net_s, comb_gross, common, eq_net, bd_net, eq_ac, bd_ac)
        for nm, s in [("equity_carry", eq_net), ("bond_carry", bd_net),
                      ("combined_voltgt", comb_net_s)]:
            m = qv.moments(s.values)
            variants.append({
                "name": f"{nm}_borrow{int(borrow*100)}pct",
                "borrow_annual": borrow,
                "n_months": int(s.size),
                "net_sharpe_ann": round(m["sharpe"] * math.sqrt(12), 3),
                "net_cagr_pct": round(((1 + s).prod() ** (12 / max(1, s.size)) - 1) * 100, 2),
                "maxdd_pct": round(_maxdd(s) * 100, 2),
            })

    # Evaluate the COMBINED factor at the honest (3% borrow) setting through the gate
    comb_net_s, comb_gross, common, eq_net, bd_net, eq_ac, bd_ac = combos[0.03]
    corr_eq_bd = float(np.corrcoef(eq_net.reindex(common).values,
                                   bd_net.reindex(common).values)[0, 1])
    rank_ac = max(eq_ac, bd_ac)
    eff = qv.effective_bets(comb_net_s.size, rank_ac)
    n_eff = eff["independent_bets"]
    ev = qv.evaluate("cross_asset_carry_combined_net", comb_net_s.values,
                     n_trials=HOUSE_PRIOR_TRIALS + n_here,
                     trial_sharpes=trial_sharpes, periods_per_year=12,
                     trades_per_period=1.0, gross_returns=comb_gross.values,
                     n_effective=n_eff)
    # (n_strategies, n_obs) — strategies in ROWS. The original column_stack call
    # was transposed; caught by the 2026-08-14 workflow audit of a sibling lab.
    wrc = qv.whites_reality_check(
        np.vstack([eq_net.reindex(common).values,
                   bd_net.reindex(common).values,
                   comb_net_s.values]))

    out = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "mechanism": "cross-asset carry factor (Koijen et al.) — equity+bond within-class carry, combined",
        "equity_universe_ok": eqok, "bond_universe_ok": bdok,
        "panel_start": str(common[0].date()), "panel_end": str(common[-1].date()),
        "n_months_combined": int(comb_net_s.size),
        "house_prior_trials": HOUSE_PRIOR_TRIALS, "lab_variants": n_here,
        "total_deflation_trials": HOUSE_PRIOR_TRIALS + n_here,
        "cost_bps_side": COST_BPS_SIDE, "cost_wall_note": "ETF costs ~10bps/side; borrow swept 1/3%",
        "sleeve_corr_equity_bond": round(corr_eq_bd, 3),
        "combined_effective_bets": eff,
        "combined_beta_to_spy": beta_to_spy(comb_net_s),
        "combined_crash_2008_pct": crash(comb_net_s, pd.Timestamp("2008-06-01"), pd.Timestamp("2009-03-31")),
        "combined_crash_2020_pct": crash(comb_net_s, pd.Timestamp("2020-02-01"), pd.Timestamp("2020-04-30")),
        "combined_crash_2022_pct": crash(comb_net_s, pd.Timestamp("2022-01-01"), pd.Timestamp("2022-10-31")),
        "variants": variants,
        "whites_reality_check": wrc,
        "combined_evaluate": ev,
        "verdict_line": qv.verdict_line(ev),
    }
    with open(RECEIPT, "w") as f:
        json.dump(_j(out), f, indent=2)

    print("=" * 80)
    print("CROSS-ASSET CARRY LAB (Koijen et al.) — equity + bond carry, combined")
    print("=" * 80)
    print(f"  equity sleeve : {len(eqok)} country ETFs   bond sleeve: {len(bdok)} ETFs")
    print(f"  panel         : {out['panel_start']} .. {out['panel_end']}  "
          f"({out['n_months_combined']} months)")
    print(f"  deflation     : {out['total_deflation_trials']} trials "
          f"(house {HOUSE_PRIOR_TRIALS} + {n_here} here)")
    print(f"  sleeve corr (equity vs bond carry): {corr_eq_bd:+.3f}   "
          f"combined beta->SPY: {out['combined_beta_to_spy']}")
    print("  variants (annualised net Sharpe, maxDD):")
    for v in variants:
        print(f"    {v['name']:<30} netSh {v['net_sharpe_ann']:+.3f}  "
              f"CAGR {v['net_cagr_pct']:+.2f}%  maxDD {v['maxdd_pct']:.1f}%  n={v['n_months']}")
    print(f"\n  crash test  2008 {out['combined_crash_2008_pct']}%  "
          f"2020 {out['combined_crash_2020_pct']}%  2022 {out['combined_crash_2022_pct']}%")
    print(f"  White's reality check p = {wrc['p_value']}  survives {wrc['survives']}")
    print("\n  HARNESS on COMBINED net factor (3% borrow):")
    print(f"    n_obs {ev['n_obs']}  n_effective {ev['n_effective']}  "
          f"t {ev['t_stat']}  p {ev['p_value']}")
    print(f"    gross/trade {ev.get('gross_per_trade_bps')}bps  net/trade {ev['per_trade_bps']}bps")
    print(f"    deflated_sharpe {ev['deflated_sharpe']}  "
          f"survives_deflation {ev['survives_deflation']}")
    print(f"\n  VERDICT: {out['verdict_line']}")
    print(f"\n  receipt -> {RECEIPT}")
    return out


def _maxdd(series):
    eq = (1 + series).cumprod()
    return float((eq / eq.cummax() - 1).min())


def _j(o):
    if isinstance(o, dict):
        return {k: _j(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_j(x) for x in o]
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    return o


if __name__ == "__main__":
    main()
