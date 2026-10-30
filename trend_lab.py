#!/usr/bin/env python3
"""trend_lab.py — Honest test of multi-asset time-series (absolute) momentum,
the managed-futures / CTA mechanism, on a diversified liquid ETF proxy universe.

RESEARCH ONLY. No capital, no orders, no engine state touched. Read-only w.r.t.
the rest of the desk. All it writes is its own JSON receipt and the report.

The question: is time-series trend a real AFTER-COST mechanism on this account,
once we deflate for how hard the desk has already searched (~1,120+ prior trials)
and the handful of lookbacks tried here?

Design (kept deliberately close to the CTA literature, Moskowitz-Ooi-Pedersen 2012
"Time Series Momentum" and Hurst-Ooi-Pedersen):
  * universe: liquid ETFs spanning equities / rates / commodities / REIT / USD
  * signal: sign of trailing K-month return per asset (long if >0 else short)
  * sizing: volatility target per leg (equal risk), leverage-capped, monthly rebal
  * costs:  10 bps per side on turnover (base) and 35 bps per side (stress)
  * scoring: gross vs net Sharpe, then the firm's quant_validation harness on NET,
             with an honest search count.
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
JSON_OUT = f"{REPORT_DIR}/trend_lab.json"

# CTA proxy universe: diversified, liquid, spans asset classes.
UNIVERSE = {
    "SPY": "US equity",
    "EFA": "Dev ex-US equity",
    "EEM": "EM equity",
    "TLT": "Long UST",
    "IEF": "7-10y UST",
    "GLD": "Gold",
    "DBC": "Broad commodity",
    "SLV": "Silver",
    "USO": "Crude oil",
    "IYR": "US REIT",
    "UUP": "US dollar index",
}

START = "2006-01-01"
END = "2026-08-08"

TARGET_VOL_ANNUAL = 0.10      # per-leg vol target
LEG_LEVERAGE_CAP = 2.0        # cap on |weight| per leg before portfolio scaling
VOL_LOOKBACK = 63             # ~3 months of daily obs for realized vol
COST_BPS_PER_SIDE = 10.0      # base transaction cost
COST_BPS_STRESS = 35.0        # stress transaction cost
TRADING_DAYS = 252

# Honest search count: prior desk trials + the lookbacks/variants tried here.
PRIOR_TRIALS = 1120
LOOKBACKS_MONTHS = [3, 6, 12]  # plus a blend => 4 signal variants


def fetch(tickers, start, end):
    """Download adjusted daily closes. Returns (prices_df, ok_list, failed_list)."""
    import time
    frames = {}
    for t in tickers:
        for attempt in range(3):
            df = yf.download(t, start=start, end=end, auto_adjust=True,
                             progress=False)
            if len(df) > 250:
                s = df["Close"]
                frames[t] = s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s
                break
            time.sleep(1.5)
    close = pd.DataFrame(frames)
    ok, failed = [], []
    for t in tickers:
        if t in close.columns and close[t].notna().sum() > 250:
            ok.append(t)
        else:
            failed.append(t)
    close = close[ok].dropna(how="all")
    return close, ok, failed


def build_signal(prices, lookback_days):
    """Absolute (time-series) momentum: sign of trailing lookback return.
    +1 long / -1 short. Computed on daily closes, sampled monthly."""
    trailing = prices / prices.shift(lookback_days) - 1.0
    return np.sign(trailing)


def realized_vol(daily_rets, lookback=VOL_LOOKBACK):
    return daily_rets.rolling(lookback).std() * math.sqrt(TRADING_DAYS)


def backtest(prices, lookback_months, cost_bps):
    """Monthly-rebalanced, vol-targeted, equal-risk TS-momentum portfolio.

    Returns dict with daily gross & net return series (pd.Series) and turnover.
    """
    daily_rets = prices.pct_change()

    if isinstance(lookback_months, (list, tuple)):
        lookback_days = max(int(m * 21) for m in lookback_months)
        # blend: average sign across the component lookbacks
        sig = None
        for lm in lookback_months:
            s = build_signal(prices, int(lm * 21))
            sig = s if sig is None else sig + s
        signal_daily = np.sign(sig)
    else:
        lookback_days = int(lookback_months * 21)
        signal_daily = build_signal(prices, lookback_days)

    # Per-leg vol scaling: target_vol / realized_vol, capped.
    rv = realized_vol(daily_rets)
    leg_scale = (TARGET_VOL_ANNUAL / rv).clip(upper=LEG_LEVERAGE_CAP)

    # Month-end rebalance dates.
    month_ends = prices.resample("ME").last().index
    # Position = signal * leg_scale, held constant within month, set at prior month end.
    raw_pos = (signal_daily * leg_scale)

    # Sample positions at month ends, forward-fill through the next month,
    # and lag by one day so we trade on info available at rebalance.
    pos_me = raw_pos.reindex(month_ends).shift(1)  # use prior month-end signal
    pos_daily = pos_me.reindex(prices.index, method="ffill")

    # Equal-risk aggregation: average across active legs (each already ~vol-targeted).
    n_active = pos_daily.notna().sum(axis=1).replace(0, np.nan)
    # Portfolio leg weights: divide by number of legs so total book ~ target vol.
    weights = pos_daily.div(n_active, axis=0).fillna(0.0)

    # Gross daily return = sum(weight_{t-1} * asset_return_t).
    gross = (weights.shift(1) * daily_rets).sum(axis=1)

    # Turnover = sum |Δweight| at rebalance points -> cost.
    dw = weights.diff().abs().sum(axis=1)
    cost = dw * (cost_bps / 10000.0)
    net = gross - cost

    # Trim warmup (need longest lookback + vol window).
    warmup = max(lookback_days if isinstance(lookback_months, int)
                 else max(int(m * 21) for m in lookback_months), VOL_LOOKBACK) + 5
    valid = prices.index[warmup:]
    gross = gross.reindex(valid).dropna()
    net = net.reindex(valid).dropna()
    turnover_annual = float(dw.reindex(valid).sum() / (len(valid) / TRADING_DAYS))

    return {
        "gross": gross,
        "net": net,
        "turnover_annual": turnover_annual,
        "avg_daily_turnover": float(dw.reindex(valid).mean()),
    }


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
    }


def window_stats(rets, spy_rets, label, lo, hi):
    spy_al = spy_rets.reindex(rets.index)
    mask = (rets.index >= lo) & (rets.index <= hi)
    sub = rets[mask]
    spy_sub = spy_al[mask]
    if len(sub) < 5:
        return {"window": label, "n": int(len(sub)), "note": "insufficient data"}
    cum = float((1 + sub).prod() - 1)
    spy_cum = float((1 + spy_sub).prod() - 1)
    return {
        "window": label,
        "n": int(len(sub)),
        "strategy_return": cum,
        "spy_return": spy_cum,
        "outperformance": cum - spy_cum,
    }


def main():
    print("Fetching universe...")
    tickers = list(UNIVERSE.keys())
    prices, ok, failed = fetch(tickers, START, END)
    print(f"  downloaded: {ok}")
    print(f"  failed/short: {failed}")
    print(f"  span: {prices.index[0].date()} -> {prices.index[-1].date()}, "
          f"{len(prices)} daily bars")

    spy = yf.download("SPY", start=START, end=END, auto_adjust=True,
                      progress=False)["Close"]
    if isinstance(spy, pd.DataFrame):
        spy = spy.iloc[:, 0]
    spy_rets = spy.pct_change()

    variants = {
        "TSMOM_3m": 3,
        "TSMOM_6m": 6,
        "TSMOM_12m": 12,
        "TSMOM_blend_3_6_12": [3, 6, 12],
    }

    results = {}
    all_net_sharpes = []
    for name, lb in variants.items():
        bt = backtest(prices, lb, COST_BPS_PER_SIDE)
        bt_stress = backtest(prices, lb, COST_BPS_STRESS)
        g = perf_stats(bt["gross"])
        n = perf_stats(bt["net"])
        ns = perf_stats(bt_stress["net"])
        # correlation to SPY B&H over common window
        common = bt["net"].index.intersection(spy_rets.dropna().index)
        corr = float(bt["net"].reindex(common).corr(spy_rets.reindex(common)))
        results[name] = {
            "lookback": lb,
            "gross": g,
            "net_10bps": n,
            "net_35bps": ns,
            "spy_correlation": corr,
            "turnover_annual": bt["turnover_annual"],
            "net_series": bt["net"],
        }
        all_net_sharpes.append(n["sharpe"])
        print(f"{name:24s} gross SR {g['sharpe']:+.2f} | net10 SR "
              f"{n['sharpe']:+.2f} | net35 SR {ns['sharpe']:+.2f} | "
              f"corrSPY {corr:+.2f} | maxDD {n['max_drawdown']:.1%}")

    # Pick the best NET (10bps) variant as the candidate to run through the harness.
    best_name = max(results, key=lambda k: results[k]["net_10bps"]["sharpe"])
    best = results[best_name]
    net = best["net_series"].dropna()
    gross = backtest(prices, best["lookback"], COST_BPS_PER_SIDE)["gross"].dropna()
    common = net.index.intersection(spy_rets.dropna().index)

    print(f"\nBest NET variant: {best_name}")

    # --- Effective sample size: legs are correlated; positions persist within month.
    leg_daily = prices.pct_change().reindex(net.index)
    corr_matrix = leg_daily.corr().values
    off = corr_matrix[np.triu_indices_from(corr_matrix, 1)]
    mean_corr = float(np.nanmean(np.abs(off)))
    n_legs = len(ok)
    # Overlap: monthly rebalance on daily returns -> ~21x overlap within a held month.
    overlap = 21.0
    n_eff = qv.effective_sample_size(len(net), n_series=n_legs,
                                     mean_correlation=mean_corr,
                                     overlap_factor=overlap)

    # --- Deflated Sharpe on NET, honest search count.
    n_trials = PRIOR_TRIALS + len(variants)
    ev = qv.evaluate(
        best_name,
        net.values,
        n_trials=n_trials,
        trial_sharpes=None,
        periods_per_year=TRADING_DAYS,
        trades_per_period=best["turnover_annual"] / TRADING_DAYS
        if best["turnover_annual"] > 0 else 1.0,
        gross_returns=gross.reindex(net.index).dropna().values,
        n_effective=n_eff,
        benchmark_returns=spy_rets.reindex(common).dropna().values,
    )
    ev_line = qv.verdict_line(ev)
    print("\n" + ev_line)

    # --- Purged CV: net Sharpe fold-by-fold.
    net_arr = net.values
    fold_sharpes = []
    for tr, te in qv.purged_kfold_splits(len(net_arr), n_splits=5,
                                         embargo_pct=0.02, label_horizon=21):
        seg = net_arr[te]
        if len(seg) > 5 and seg.std() > 0:
            fold_sharpes.append(float(seg.mean() / seg.std() * math.sqrt(TRADING_DAYS)))
    cv_mean = float(np.mean(fold_sharpes)) if fold_sharpes else None
    cv_min = float(np.min(fold_sharpes)) if fold_sharpes else None

    # --- Kill-test: effective bets. Monthly TSMOM sign persists strongly.
    # Estimate month-to-month sign autocorrelation of the aggregate signal.
    n_rebalances = len(net) // 21
    # Rank/sign persistence proxy: autocorr of monthly portfolio direction.
    monthly = (1 + net).resample("ME").prod() - 1
    sign_ac = float(pd.Series(np.sign(monthly.values)).autocorr()) if len(monthly) > 3 else 0.0
    eff_bets = qv.effective_bets(n_rebalances, max(0.0, sign_ac))

    # --- Kill-test: execution-delay decay. Re-run best variant lagging entry by
    #     1..5 extra trading days after the monthly signal date.
    delay_curve = {}
    daily_rets = prices.pct_change()
    for delay in [0, 1, 3, 5]:
        lb = best["lookback"]
        if isinstance(lb, list):
            sig = None
            for lm in lb:
                s = build_signal(prices, int(lm * 21))
                sig = s if sig is None else sig + s
            signal_daily = np.sign(sig)
        else:
            signal_daily = build_signal(prices, int(lb * 21))
        rv = realized_vol(daily_rets)
        leg_scale = (TARGET_VOL_ANNUAL / rv).clip(upper=LEG_LEVERAGE_CAP)
        raw_pos = signal_daily * leg_scale
        month_ends = prices.resample("ME").last().index
        pos_me = raw_pos.reindex(month_ends).shift(1)
        pos_daily = pos_me.reindex(prices.index, method="ffill")
        n_active = pos_daily.notna().sum(axis=1).replace(0, np.nan)
        weights = pos_daily.div(n_active, axis=0).fillna(0.0)
        # extra execution delay
        g = (weights.shift(1 + delay) * daily_rets).sum(axis=1)
        dw = weights.diff().abs().sum(axis=1)
        gnet = (g - dw * (COST_BPS_PER_SIDE / 10000.0)).reindex(net.index).dropna()
        # express as mean daily bps retained
        delay_curve[delay] = round(float(gnet.mean()) * 10000, 4)

    # --- Crisis windows: 2008 GFC and 2020 COVID.
    crisis = [
        window_stats(net, spy_rets, "2008 GFC (Jun08-Feb09)",
                     pd.Timestamp("2008-06-01"), pd.Timestamp("2009-02-28")),
        window_stats(net, spy_rets, "2020 COVID (Feb20-Apr20)",
                     pd.Timestamp("2020-02-01"), pd.Timestamp("2020-04-30")),
        window_stats(net, spy_rets, "2022 bond/equity bear",
                     pd.Timestamp("2022-01-01"), pd.Timestamp("2022-12-31")),
    ]

    out = {
        "generated": datetime.now().isoformat(),
        "universe_ok": ok,
        "universe_failed": failed,
        "span": [str(prices.index[0].date()), str(prices.index[-1].date())],
        "n_daily_bars": int(len(prices)),
        "variants": {k: {kk: vv for kk, vv in v.items() if kk != "net_series"}
                     for k, v in results.items()},
        "best_variant": best_name,
        "search_count": n_trials,
        "n_effective": round(float(n_eff), 1),
        "mean_leg_abs_corr": round(mean_corr, 3),
        "evaluate": {k: (v if not isinstance(v, dict) else v)
                     for k, v in ev.items()},
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
