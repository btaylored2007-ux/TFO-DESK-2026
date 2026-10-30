#!/usr/bin/env python3
"""commodity_carry_lab.py — Honest test of COMMODITY ROLL-YIELD / TERM-STRUCTURE
CARRY (long backwardated, short/underweight contango) as an after-cost mechanism.

RESEARCH ONLY. No capital, no orders, no engine state touched, no pre-registration
created. Read-only w.r.t. the rest of the desk. All it writes is its own JSON
receipt and the report.

THE QUESTION
------------
Roll yield is a DISTINCT carry expression from the delta-neutral basis carry the
desk already validated: its driver is physical storage / convenience yield (the
shape of the futures curve), and it is supposed to carry low equity beta. Is it a
real AFTER-COST mechanism on freely available data, once we deflate for how hard
the desk has already searched (~1,121 prior trials incl. trend) plus the variants
tried here?

THE HONEST DATA PROBLEM (read this before trusting any number below)
-------------------------------------------------------------------
True roll yield is the return from rolling a futures position down/up a sloped
curve. We do NOT have free clean futures term-structure here. What we DO have are
ETFs that sit at different points of the SAME commodity's curve:

  * USO  — front-month WTI crude (maximally exposed to roll drag in contango)
  * USL  — 12-month laddered WTI crude (same spot beta, far less roll drag)
  * UNG  — front-month Henry Hub natural gas
  * UNL  — 12-month laddered natural gas
  * USCI — SummerHaven index that PICKS contracts to maximise backwardation
  * DBC  — Invesco broad commodity, optimised-roll but energy-heavy naive-ish
  * GSG  — iShares GSCI, front-of-curve, energy-heavy (a naive long anchor)
  * PDBC — active broad commodity (roll-managed), short history (2014+)

The cleanest isolation available for free is a SAME-COMMODITY MATURITY SPREAD:
long the deferred ETF, short the front ETF (e.g. USL - USO). Both are ~100% the
same spot; their difference is dominated by the differential roll yield along the
curve. This is a PROXY, not true term-structure carry, and it is contaminated by:
  (a) ETF tracking error, expense ratios, and rebalance mechanics;
  (b) the two funds not holding identical spot exposure intraday;
  (c) for the broad USCI-vs-DBC/GSG spread, DIFFERENT COMMODITY WEIGHTS — that
      spread mixes roll selection with sector composition and is NOT pure roll.
We quantify (a)/(c) where we can and flag the verdict accordingly.

DESIGN
------
  * expressions: same-commodity maturity spreads (oil, gas) + broad roll spread
  * signal: static harvest (always long deferred / short front) AND a dynamic
            carry-state version (follow the sign of the trailing curve slope)
  * sizing: dollar-neutral legs, vol-targeted to 10% annual, monthly rebalance
  * costs:  10 bps per side on turnover (base) and 35 bps per side (stress)
  * scoring: gross vs net Sharpe, then quant_validation on the NET series with an
             honest search count.
"""
import json
import math
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

import quant_validation as qv

warnings.filterwarnings("ignore")

REPORT_DIR = "reports"
JSON_OUT = f"{REPORT_DIR}/commodity_carry_lab.json"

# Everything we pull. Pairs are (deferred, front) on the SAME commodity.
UNIVERSE = {
    "USO": "WTI crude, front-month (contango-exposed)",
    "USL": "WTI crude, 12-month ladder (roll-managed)",
    "UNG": "Henry Hub gas, front-month",
    "UNL": "Henry Hub gas, 12-month ladder",
    "USCI": "SummerHaven backwardation-weighted broad basket",
    "DBC": "Invesco broad commodity (optimised roll, energy-heavy)",
    "GSG": "iShares GSCI, front-of-curve broad (naive long anchor)",
    "PDBC": "Active broad commodity, roll-managed (short history)",
    "SPY": "US equity (beta anchor)",
}

# Same-commodity maturity spreads: (deferred_leg, front_leg). Long deferred,
# short front => positive return when the front bleeds roll (contango).
ROLL_PAIRS = {
    "oil_USL_USO":   ("USL", "USO"),
    "gas_UNL_UNG":   ("UNL", "UNG"),
    # broad: backwardation-selected minus naive front-of-curve. SECTOR-CONTAMINATED.
    "broad_USCI_GSG": ("USCI", "GSG"),
    "broad_USCI_DBC": ("USCI", "DBC"),
}

START = "2006-01-01"
END = "2026-08-10"

TARGET_VOL_ANNUAL = 0.10
VOL_LOOKBACK = 63
CARRY_LOOKBACK_DAYS = 63       # trailing window to read the curve-slope state
COST_BPS_PER_SIDE = 10.0
COST_BPS_STRESS = 35.0
TRADING_DAYS = 252

# Honest search count: 1,120 prior desk trials + trend (1) = 1,121, plus the
# variants tried in THIS study.
PRIOR_TRIALS = 1121


def fetch(tickers, start, end):
    """Download adjusted daily closes. Returns (prices_df, ok_list, failed_list)."""
    import time
    frames = {}
    for t in tickers:
        for attempt in range(3):
            df = yf.download(t, start=start, end=end, auto_adjust=True,
                             progress=False)
            if len(df) > 200:
                s = df["Close"]
                frames[t] = s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s
                break
            time.sleep(1.5)
    close = pd.DataFrame(frames)
    ok, failed = [], []
    for t in tickers:
        if t in close.columns and close[t].notna().sum() > 200:
            ok.append(t)
        else:
            failed.append(t)
    close = close[ok]
    return close, ok, failed


def perf_stats(rets):
    r = rets.dropna()
    if len(r) < 2:
        return {}
    ann_ret = (1 + r).prod() ** (TRADING_DAYS / len(r)) - 1
    ann_vol = r.std() * math.sqrt(TRADING_DAYS)
    sharpe = (r.mean() / r.std()) * math.sqrt(TRADING_DAYS) if r.std() > 0 else 0.0
    curve = (1 + r).cumprod()
    dd = (curve / curve.cummax() - 1).min()
    return {
        "cagr": float(ann_ret),
        "ann_vol": float(ann_vol),
        "sharpe": float(sharpe),
        "max_drawdown": float(dd),
        "n_days": int(len(r)),
        "start": str(r.index[0].date()),
        "end": str(r.index[-1].date()),
    }


def voltarget_series(raw_spread, target_vol=TARGET_VOL_ANNUAL):
    """Scale a raw dollar-neutral spread return to a target annual vol using a
    trailing realized-vol estimate (lagged, no look-ahead). Returns scaled series
    and the daily gross exposure (sum of |leg weights|) for turnover/cost."""
    rv = raw_spread.rolling(VOL_LOOKBACK).std() * math.sqrt(TRADING_DAYS)
    scale = (target_vol / rv).replace([np.inf, -np.inf], np.nan)
    scale = scale.clip(upper=5.0).shift(1)          # lag: size on yesterday's vol
    return scale


def backtest_static(prices, deferred, front, cost_bps, target_vol=TARGET_VOL_ANNUAL):
    """Static roll harvest: always long `deferred`, short `front`, dollar-neutral,
    vol-targeted, rebalanced monthly. Positive when front bleeds roll (contango)."""
    if deferred not in prices.columns or front not in prices.columns:
        return None
    sub = prices[[deferred, front]].dropna()
    rets = sub.pct_change()
    raw = rets[deferred] - rets[front]                # unit dollar-neutral spread
    scale = voltarget_series(raw, target_vol)
    # Sample the vol scale monthly (held constant within month) -> realistic rebal.
    month_ends = sub.resample("ME").last().index
    scale_me = scale.reindex(month_ends).reindex(sub.index, method="ffill")
    pos = scale_me                                    # signed +1 * scale (long spread)
    gross = (pos.shift(1) * raw)
    # Gross exposure per leg = |pos|; turnover = |Δ gross exposure| across 2 legs.
    dexp = (2.0 * pos).diff().abs()                   # two legs each size |pos|
    cost = dexp * (cost_bps / 10000.0)
    net = gross - cost
    warmup = max(VOL_LOOKBACK, 21) + 5
    valid = sub.index[warmup:]
    gross, net = gross.reindex(valid).dropna(), net.reindex(valid).dropna()
    turnover_annual = float(dexp.reindex(valid).sum() / (len(valid) / TRADING_DAYS))
    return {"gross": gross, "net": net, "turnover_annual": turnover_annual,
            "raw": raw.reindex(valid).dropna()}


def backtest_dynamic(prices, pairs, cost_bps, target_vol=TARGET_VOL_ANNUAL,
                     carry_lb=CARRY_LOOKBACK_DAYS):
    """Dynamic carry-state harvest across several same-commodity spreads.

    For each pair, the trailing `carry_lb`-day sign of the deferred-minus-front
    relative performance is a PROXY for the current curve state: if the deferred
    leg has been outperforming, the curve is (was) in contango and the front is
    bleeding roll -> stay long deferred / short front; if the front has been
    outperforming, the curve is backwardated -> flip (long front / short deferred).
    This tries to harvest roll yield in EITHER regime rather than betting on
    permanent contango.

    Positions are equal-risk across active pairs, dollar-neutral within each pair,
    vol-targeted at the portfolio level, monthly rebalance.
    """
    rets = prices.pct_change()
    month_ends = prices.resample("ME").last().index

    leg_returns = {}
    signs = {}
    for name, (dfd, frt) in pairs.items():
        if dfd not in prices.columns or frt not in prices.columns:
            continue
        raw = rets[dfd] - rets[frt]                   # long-deferred spread return
        # curve-state proxy: sign of trailing cumulative spread
        state = np.sign(raw.rolling(carry_lb).sum())
        leg_returns[name] = raw
        signs[name] = state

    if not leg_returns:
        return None

    leg_df = pd.DataFrame(leg_returns)
    sign_df = pd.DataFrame(signs)
    # sample signs monthly, lag one day (trade on prior month-end state)
    sign_me = sign_df.reindex(month_ends).shift(1).reindex(prices.index, method="ffill")
    n_active = sign_me.notna().sum(axis=1).replace(0, np.nan)
    # equal-risk: each active pair gets 1/n of the risk budget
    pair_pos = sign_me.div(n_active, axis=0).fillna(0.0)

    # portfolio raw return = sum(pair_state_{t-1} * spread_return_t)
    raw_port = (pair_pos.shift(1) * leg_df).sum(axis=1)
    scale = voltarget_series(raw_port, target_vol)
    scale_me = scale.reindex(month_ends).reindex(prices.index, method="ffill")
    gross = (scale_me.shift(1) * raw_port)

    # turnover: each pair has 2 legs; leg exposure = scale * |pair_pos|.
    leg_exposure = (pair_pos.abs().sum(axis=1) * 2.0) * scale_me
    dexp = leg_exposure.diff().abs()
    # also charge when a pair flips sign (position fully reverses on both legs)
    flip = (pair_pos.diff().abs().sum(axis=1)) * scale_me
    turnover = dexp.fillna(0) + flip.fillna(0)
    cost = turnover * (cost_bps / 10000.0)
    net = gross - cost

    warmup = max(VOL_LOOKBACK, carry_lb, 21) + 5
    valid = prices.index[warmup:]
    gross, net = gross.reindex(valid).dropna(), net.reindex(valid).dropna()
    turnover_annual = float(turnover.reindex(valid).sum() / (len(valid) / TRADING_DAYS))
    return {"gross": gross, "net": net, "turnover_annual": turnover_annual,
            "raw": raw_port.reindex(valid).dropna()}


def window_stats(rets, spy_rets, label, lo, hi):
    spy_al = spy_rets.reindex(rets.index)
    mask = (rets.index >= lo) & (rets.index <= hi)
    sub = rets[mask]
    spy_sub = spy_al[mask]
    if len(sub) < 5:
        return {"window": label, "n": int(len(sub)), "note": "insufficient data"}
    cum = float((1 + sub).prod() - 1)
    spy_cum = float((1 + spy_sub.dropna()).prod() - 1)
    return {"window": label, "n": int(len(sub)),
            "strategy_return": cum, "spy_return": spy_cum,
            "outperformance": cum - spy_cum}


def tracking_error_note(prices):
    """Quantify the PROXY assumption for the oil pair: how much of USL/USO daily
    variance is common spot beta vs. idiosyncratic (roll+tracking)? A high common
    beta means the spread genuinely isolates roll rather than spot direction."""
    out = {}
    for name, (dfd, frt) in ROLL_PAIRS.items():
        if dfd not in prices.columns or frt not in prices.columns:
            continue
        sub = prices[[dfd, frt]].dropna()
        r = sub.pct_change().dropna()
        if len(r) < 50:
            continue
        corr = float(r[dfd].corr(r[frt]))
        beta = float(np.polyfit(r[frt], r[dfd], 1)[0])
        spread = r[dfd] - r[frt]
        # fraction of deferred-leg variance explained by the front leg (common spot)
        common_var_frac = corr ** 2
        out[name] = {
            "leg_daily_corr": round(corr, 4),
            "beta_deferred_on_front": round(beta, 3),
            "common_variance_fraction": round(common_var_frac, 4),
            "spread_ann_vol": round(float(spread.std() * math.sqrt(TRADING_DAYS)), 4),
            "n_days": int(len(r)),
            "span": [str(sub.index[0].date()), str(sub.index[-1].date())],
        }
    return out


def main():
    print("Fetching commodity term-structure proxy universe...")
    tickers = list(UNIVERSE.keys())
    prices, ok, failed = fetch(tickers, START, END)
    print(f"  downloaded: {ok}")
    print(f"  failed/short: {failed}")
    for t in ok:
        s = prices[t].dropna()
        print(f"    {t:5s} {s.index[0].date()} -> {s.index[-1].date()}  "
              f"({len(s)} bars)")

    spy_rets = prices["SPY"].pct_change() if "SPY" in prices else pd.Series(dtype=float)
    dbc_rets = prices["DBC"].pct_change() if "DBC" in prices else pd.Series(dtype=float)

    # --- Proxy diagnostics: does the spread isolate roll, or is it spot noise?
    proxy = tracking_error_note(prices)
    print("\nProxy diagnostics (how well each spread isolates roll vs spot):")
    for k, v in proxy.items():
        print(f"  {k:16s} leg-corr {v['leg_daily_corr']:+.3f} | "
              f"common-var {v['common_variance_fraction']:.2%} | "
              f"spread vol {v['spread_ann_vol']:.1%} | {v['span'][0]}..{v['span'][1]}")

    # --- Static single-pair harvests.
    variants = {}
    for name, (dfd, frt) in ROLL_PAIRS.items():
        bt = backtest_static(prices, dfd, frt, COST_BPS_PER_SIDE)
        bts = backtest_static(prices, dfd, frt, COST_BPS_STRESS)
        if bt is None:
            continue
        variants[f"static_{name}"] = {"bt": bt, "bt_stress": bts, "kind": "static"}

    # --- Dynamic carry-state, broad (uses the same-commodity pairs that exist).
    dyn = backtest_dynamic(prices, {k: v for k, v in ROLL_PAIRS.items()
                                    if k in ("oil_USL_USO", "gas_UNL_UNG")},
                           COST_BPS_PER_SIDE)
    dyn_s = backtest_dynamic(prices, {k: v for k, v in ROLL_PAIRS.items()
                                      if k in ("oil_USL_USO", "gas_UNL_UNG")},
                             COST_BPS_STRESS)
    if dyn is not None:
        variants["dynamic_carry_oil_gas"] = {"bt": dyn, "bt_stress": dyn_s,
                                             "kind": "dynamic"}

    # Dynamic including the broad sector-contaminated pair (flagged).
    dyn2 = backtest_dynamic(prices, ROLL_PAIRS, COST_BPS_PER_SIDE)
    dyn2_s = backtest_dynamic(prices, ROLL_PAIRS, COST_BPS_STRESS)
    if dyn2 is not None:
        variants["dynamic_carry_all_pairs"] = {"bt": dyn2, "bt_stress": dyn2_s,
                                               "kind": "dynamic"}

    # --- Score every variant.
    table = {}
    for name, v in variants.items():
        g = perf_stats(v["bt"]["gross"])
        n = perf_stats(v["bt"]["net"])
        ns = perf_stats(v["bt_stress"]["net"])
        common = v["bt"]["net"].index.intersection(spy_rets.dropna().index)
        corr_spy = float(v["bt"]["net"].reindex(common).corr(spy_rets.reindex(common)))
        commond = v["bt"]["net"].index.intersection(dbc_rets.dropna().index)
        corr_dbc = float(v["bt"]["net"].reindex(commond).corr(dbc_rets.reindex(commond)))
        table[name] = {
            "kind": v["kind"],
            "gross": g, "net_10bps": n, "net_35bps": ns,
            "spy_correlation": corr_spy, "dbc_correlation": corr_dbc,
            "turnover_annual": v["bt"]["turnover_annual"],
            "net_series": v["bt"]["net"], "gross_series": v["bt"]["gross"],
        }
        print(f"{name:26s} grossSR {g.get('sharpe',0):+.2f} | net10 "
              f"{n.get('sharpe',0):+.2f} | net35 {ns.get('sharpe',0):+.2f} | "
              f"corrSPY {corr_spy:+.2f} | corrDBC {corr_dbc:+.2f} | "
              f"maxDD {n.get('max_drawdown',0):.1%} | turn {v['bt']['turnover_annual']:.1f}x")

    # --- Pick best NET (10bps) variant for the full harness.
    best_name = max(table, key=lambda k: table[k]["net_10bps"].get("sharpe", -9))
    best = table[best_name]
    net = best["net_series"].dropna()
    gross = best["gross_series"].reindex(net.index).dropna()
    common = net.index.intersection(spy_rets.dropna().index)
    print(f"\nBest NET variant: {best_name}")

    # --- Effective sample size. Spreads persist within the month; the pairs are
    #     mutually correlated. For a single static pair n_series=1 but the within-
    #     month hold still creates overlap.
    n_series = 2 if best["kind"] == "dynamic" else 1
    # crude mean abs corr among the spread returns used
    spread_cols = []
    for nm, (dfd, frt) in ROLL_PAIRS.items():
        if dfd in prices and frt in prices:
            spread_cols.append((prices[dfd].pct_change() - prices[frt].pct_change())
                               .rename(nm))
    sdf = pd.concat(spread_cols, axis=1).reindex(net.index) if spread_cols else None
    if sdf is not None and sdf.shape[1] > 1:
        cm = sdf.corr().values
        off = cm[np.triu_indices_from(cm, 1)]
        mean_corr = float(np.nanmean(np.abs(off)))
    else:
        mean_corr = 0.0
    overlap = 21.0  # monthly hold on daily returns
    n_eff = qv.effective_sample_size(len(net), n_series=max(1, n_series),
                                     mean_correlation=mean_corr, overlap_factor=overlap)

    # --- Deflated Sharpe on NET, honest search count.
    n_trials = PRIOR_TRIALS + len(variants)
    ev = qv.evaluate(
        best_name, net.values, n_trials=n_trials, trial_sharpes=None,
        periods_per_year=TRADING_DAYS,
        trades_per_period=best["turnover_annual"] / TRADING_DAYS
        if best["turnover_annual"] > 0 else 1.0,
        gross_returns=gross.values, n_effective=n_eff,
        benchmark_returns=spy_rets.reindex(common).dropna().values,
    )
    ev_line = qv.verdict_line(ev)
    print("\n" + ev_line)

    # --- Purged CV.
    net_arr = net.values
    fold_sharpes = []
    for tr, te in qv.purged_kfold_splits(len(net_arr), n_splits=5,
                                         embargo_pct=0.02, label_horizon=21):
        seg = net_arr[te]
        if len(seg) > 5 and seg.std() > 0:
            fold_sharpes.append(float(seg.mean() / seg.std() * math.sqrt(TRADING_DAYS)))
    cv_mean = float(np.mean(fold_sharpes)) if fold_sharpes else None
    cv_min = float(np.min(fold_sharpes)) if fold_sharpes else None

    # --- Kill-test: effective bets. Static harvest = essentially one held position.
    n_rebalances = max(1, len(net) // 21)
    monthly = (1 + net).resample("ME").prod() - 1
    sign_ac = float(pd.Series(np.sign(monthly.values)).autocorr()) if len(monthly) > 3 else 0.0
    eff_bets = qv.effective_bets(n_rebalances, max(0.0, sign_ac))

    # --- Kill-test: execution-delay decay (lag entry 0/1/3/5 extra days).
    delay_curve = {}
    for delay in [0, 1, 3, 5]:
        if best["kind"] == "static":
            dfd, frt = ROLL_PAIRS[best_name.replace("static_", "")]
            sub = prices[[dfd, frt]].dropna()
            raw = sub[dfd].pct_change() - sub[frt].pct_change()
            scale = voltarget_series(raw)
            me = sub.resample("ME").last().index
            scale_me = scale.reindex(me).reindex(sub.index, method="ffill")
            g = (scale_me.shift(1 + delay) * raw)
            dexp = (2.0 * scale_me).diff().abs()
            gnet = (g - dexp * (COST_BPS_PER_SIDE / 10000.0)).reindex(net.index).dropna()
        else:
            pairs = ({k: v for k, v in ROLL_PAIRS.items()
                      if k in ("oil_USL_USO", "gas_UNL_UNG")}
                     if best_name == "dynamic_carry_oil_gas" else ROLL_PAIRS)
            r = prices.pct_change()
            me = prices.resample("ME").last().index
            legr, sg = {}, {}
            for nm, (dfd, frt) in pairs.items():
                if dfd in prices and frt in prices:
                    raw = r[dfd] - r[frt]
                    legr[nm] = raw
                    sg[nm] = np.sign(raw.rolling(CARRY_LOOKBACK_DAYS).sum())
            legd, sgd = pd.DataFrame(legr), pd.DataFrame(sg)
            sme = sgd.reindex(me).shift(1).reindex(prices.index, method="ffill")
            na = sme.notna().sum(axis=1).replace(0, np.nan)
            pp = sme.div(na, axis=0).fillna(0.0)
            rawp = (pp.shift(1) * legd).sum(axis=1)
            scale = voltarget_series(rawp)
            scale_me = scale.reindex(me).reindex(prices.index, method="ffill")
            g = (scale_me.shift(1 + delay) * rawp)
            gnet = g.reindex(net.index).dropna()
        delay_curve[delay] = round(float(gnet.mean()) * 10000, 4)

    # --- Crisis windows: 2008 GFC, 2014-16 oil crash, 2020 COVID, 2022.
    crisis = [
        window_stats(net, spy_rets, "2008 GFC (Jun08-Feb09)",
                     pd.Timestamp("2008-06-01"), pd.Timestamp("2009-02-28")),
        window_stats(net, spy_rets, "2014-16 oil crash (Jul14-Feb16)",
                     pd.Timestamp("2014-07-01"), pd.Timestamp("2016-02-29")),
        window_stats(net, spy_rets, "2020 COVID (Feb20-Apr20)",
                     pd.Timestamp("2020-02-01"), pd.Timestamp("2020-04-30")),
        window_stats(net, spy_rets, "2022 inflation/energy spike",
                     pd.Timestamp("2022-01-01"), pd.Timestamp("2022-12-31")),
    ]

    out = {
        "generated": datetime.now().isoformat(),
        "universe_ok": ok, "universe_failed": failed,
        "proxy_diagnostics": proxy,
        "span": [str(prices.index[0].date()), str(prices.index[-1].date())],
        "variants": {k: {kk: vv for kk, vv in v.items()
                         if kk not in ("net_series", "gross_series")}
                     for k, v in table.items()},
        "best_variant": best_name,
        "search_count": n_trials,
        "n_effective": round(float(n_eff), 1),
        "mean_spread_abs_corr": round(mean_corr, 3),
        "evaluate": ev,
        "verdict_line": ev_line,
        "purged_cv": {"fold_sharpes": [round(s, 3) for s in fold_sharpes],
                      "mean": cv_mean, "min": cv_min},
        "kill_effective_bets": eff_bets,
        "kill_execution_delay_bps": delay_curve,
        "crisis_windows": crisis,
    }
    with open(JSON_OUT, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nWrote {JSON_OUT}")
    return out


if __name__ == "__main__":
    main()
