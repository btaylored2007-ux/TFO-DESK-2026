#!/usr/bin/env python3
"""portfolio_construction_lab.py — Honest test of a PORTFOLIO-CONSTRUCTION edge.

RESEARCH ONLY. No capital, no orders, no engine state touched, no pre-registration
created. Read-only w.r.t. the rest of the desk. All it writes is its own JSON
receipt (reports/portfolio_construction_lab.json) and feeds the report.

THE QUESTION (no new alpha claimed):
    Does COMBINING our uncorrelated building blocks into one book beat any single
    block on RISK-ADJUSTED return (Sharpe) and drawdown, out-of-sample, after cost?
    Diversification is a real, free lunch in theory. The honest bar is whether it
    survives WALK-FORWARD (weights estimated only on past data) and beats the BEST
    single sleeve's Sharpe out-of-sample after costs — not by in-sample weight-fit.

BUILDING BLOCKS (real, cost-netted monthly returns, ~19y):
    TREND  time-series momentum sleeve (reuses trend_lab logic, net of 10bps/side)
    EQ     broad equity (SPY, total return)
    BOND   7-10y Treasuries (IEF, total return)
    GOLD   gold (GLD, total return)
    COMM   broad commodity (DBC, total return)

CONSTRUCTION METHODS (monthly rebalance, 10 bps/side on turnover):
    EW     equal weight            (zero estimation — OOS by construction)
    IV     inverse-volatility      (trailing 12m vol — walk-forward-safe)
    RP     risk parity / ERC       (trailing 36m covariance)
    MV     min-variance long-only  (trailing 36m covariance)
    Each built two ways: IN-SAMPLE (full-sample weights, look-ahead) vs
    WALK-FORWARD (trailing-window weights, no look-ahead). The gap between them is
    the fitting test.

BENCHMARKS: each standalone sleeve, a 60/40 (SPY/IEF) book, and 100% SPY.
SCORING: Sharpe / CAGR / vol / maxDD, Sharpe gain and DD reduction vs best sleeve
and vs 60/40, a stationary-bootstrap significance test on the Sharpe improvement,
and the firm's quant_validation harness on the combined NET series.
"""
import json
import math
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.optimize import minimize

import quant_validation as qv
import trend_lab

warnings.filterwarnings("ignore")

REPORT_DIR = "reports"
JSON_OUT = f"{REPORT_DIR}/portfolio_construction_lab.json"

START = "2006-01-01"
END = "2026-08-08"
MONTHS_PER_YEAR = 12
COST_BPS_PER_SIDE = 10.0

# Passive sleeve tickers (all also live inside the trend universe, so one download).
SLEEVE_TICKERS = {"EQ": "SPY", "BOND": "IEF", "GOLD": "GLD", "COMM": "DBC"}

# Walk-forward estimation windows (months).
IV_WINDOW = 12          # inverse-vol: trailing 1y realized vol
COV_WINDOW = 36         # risk-parity & min-var: trailing 3y covariance
# Headline evaluation window starts after only the inverse-vol burn-in, so the
# 2008 GFC — the single most important stress for a diversification claim — is IN
# the sample. EW needs no estimation and IV needs 12m, so both are walk-forward
# valid across essentially the whole window. The covariance methods (RP/MV) need
# 36m and are therefore reported over their own shorter, GFC-excluding sub-window
# and flagged as such — never compared head-to-head on an unequal window.
BURN_IN = IV_WINDOW

# Honest search count: the desk's prior alpha trials + construction methods here.
# NB: portfolio construction of pre-existing sleeves is NOT an alpha search in the
# same sense; we still charge the conservative count so the harness cannot flatter us.
PRIOR_TRIALS = 1120
N_CONSTRUCTION_METHODS = 8   # {EW,IV,RP,MV} x {in-sample, walk-forward}


# ---------------------------------------------------------------------------
# Performance helpers (monthly series)
# ---------------------------------------------------------------------------
def perf(m):
    r = pd.Series(m).dropna()
    if len(r) < 3:
        return {}
    ann_ret = (1 + r).prod() ** (MONTHS_PER_YEAR / len(r)) - 1
    ann_vol = r.std(ddof=1) * math.sqrt(MONTHS_PER_YEAR)
    sharpe = (r.mean() / r.std(ddof=1)) * math.sqrt(MONTHS_PER_YEAR) if r.std(ddof=1) > 0 else 0.0
    curve = (1 + r).cumprod()
    dd = float((curve / curve.cummax() - 1).min())
    return {"cagr": float(ann_ret), "vol": float(ann_vol), "sharpe": float(sharpe),
            "maxdd": dd, "n": int(len(r))}


def sharpe_monthly(arr):
    a = np.asarray(arr, dtype=float)
    a = a[np.isfinite(a)]
    sd = a.std(ddof=1)
    return (a.mean() / sd) * math.sqrt(MONTHS_PER_YEAR) if sd > 0 else 0.0


# ---------------------------------------------------------------------------
# Weight functions.  Each takes a (window x n_sleeves) DataFrame of PAST monthly
# returns and returns a weight vector (sums to 1, long-only).
# ---------------------------------------------------------------------------
def w_equal(hist):
    n = hist.shape[1]
    return np.full(n, 1.0 / n)


def w_inverse_vol(hist):
    vol = hist.std(ddof=1).values
    vol = np.where(vol > 0, vol, np.nan)
    inv = 1.0 / vol
    inv = np.nan_to_num(inv, nan=0.0)
    if inv.sum() == 0:
        return np.full(hist.shape[1], 1.0 / hist.shape[1])
    return inv / inv.sum()


def w_risk_parity(hist):
    cov = np.cov(hist.values, rowvar=False)
    n = cov.shape[0]
    w = np.full(n, 1.0 / n)
    for _ in range(1000):
        rc = w * (cov @ w)                      # risk contribution per asset
        if rc.sum() <= 0:
            break
        target = rc.mean()
        w = w * (target / np.where(rc > 0, rc, target))
        w = np.clip(w, 1e-8, None)
        w = w / w.sum()
    return w


def w_min_var(hist):
    cov = np.cov(hist.values, rowvar=False)
    n = cov.shape[0]
    x0 = np.full(n, 1.0 / n)
    cons = ({"type": "eq", "fun": lambda w: w.sum() - 1.0},)
    bnds = [(0.0, 1.0)] * n
    res = minimize(lambda w: float(w @ cov @ w), x0, method="SLSQP",
                   bounds=bnds, constraints=cons,
                   options={"maxiter": 500, "ftol": 1e-12})
    w = res.x if res.success else x0
    w = np.clip(w, 0, None)
    return w / w.sum()


WEIGHT_FUNCS = {"EW": w_equal, "IV": w_inverse_vol, "RP": w_risk_parity, "MV": w_min_var}
COV_BASED = {"RP", "MV"}


# ---------------------------------------------------------------------------
# Rebalance engine with realistic turnover cost and weight drift.
# ---------------------------------------------------------------------------
def run_portfolio(rets, weight_series, cost_bps=COST_BPS_PER_SIDE):
    """rets: (T x n) monthly returns. weight_series: (T x n) TARGET weights at the
    START of each month t (already lagged / OOS-safe by the caller). Returns the net
    monthly portfolio return series with turnover costs and inter-rebalance drift."""
    idx = rets.index
    cols = rets.columns
    prev_w = np.zeros(len(cols))
    out = []
    for t in idx:
        tgt = weight_series.loc[t].values
        if not np.isfinite(tgt).all():
            out.append(np.nan)
            prev_w = np.zeros(len(cols))
            continue
        turnover = np.abs(tgt - prev_w).sum()
        cost = turnover * (cost_bps / 10000.0)
        r = rets.loc[t].values
        gross = float(np.nansum(tgt * r))
        out.append(gross - cost)
        # drift weights to end of month for next turnover calc
        grown = tgt * (1.0 + r)
        s = grown.sum()
        prev_w = grown / s if s > 0 else tgt
    return pd.Series(out, index=idx)


def constant_weight_series(rets, w):
    return pd.DataFrame(np.tile(w, (len(rets), 1)), index=rets.index, columns=rets.columns)


def walkforward_weight_series(rets, func, window):
    """At month t, weights are estimated from returns[t-window : t] (strictly past),
    so month t's return is fully out-of-sample. Rows before `window` are NaN."""
    W = pd.DataFrame(index=rets.index, columns=rets.columns, dtype=float)
    vals = rets.values
    for i in range(len(rets)):
        if i < window:
            continue
        hist = pd.DataFrame(vals[i - window:i], columns=rets.columns)
        W.iloc[i] = func(hist)
    return W


# ---------------------------------------------------------------------------
# Bootstrap significance of the Sharpe improvement (combined vs best single sleeve)
# ---------------------------------------------------------------------------
def sharpe_gap_pvalue(combined, sleeve, n_boot=2000, seed=7):
    """Stationary-bootstrap p-value for H0: combined Sharpe <= sleeve Sharpe.
    Shared block-resample preserves the cross-correlation between the two series."""
    rng = np.random.default_rng(seed)
    a = np.asarray(combined, dtype=float)
    b = np.asarray(sleeve, dtype=float)
    k = min(len(a), len(b))
    a, b = a[-k:], b[-k:]
    obs = sharpe_monthly(a) - sharpe_monthly(b)
    cnt = 0
    for _ in range(n_boot):
        idx = qv._stationary_indices(k, rng)
        if sharpe_monthly(a[idx]) - sharpe_monthly(b[idx]) <= 0:
            cnt += 1
    return obs, cnt / n_boot


# ---------------------------------------------------------------------------
def main():
    print("Downloading trend universe (covers all passive sleeves too)...")
    tickers = list(trend_lab.UNIVERSE.keys())
    prices, ok, failed = trend_lab.fetch(tickers, START, END)
    print(f"  ok: {ok}  failed: {failed}")

    # --- TREND sleeve: reuse trend_lab backtest (net of 10 bps/side), 3m TSMOM. ---
    print("Building TREND sleeve from trend_lab (TSMOM 3m, net 10bps)...")
    bt = trend_lab.backtest(prices, 3, COST_BPS_PER_SIDE)
    trend_daily_net = bt["net"].dropna()
    trend_monthly = (1 + trend_daily_net).resample("ME").prod() - 1

    # --- Passive sleeves: monthly total returns from adjusted closes. ---
    sleeves = {"TREND": trend_monthly}
    for key, tk in SLEEVE_TICKERS.items():
        if tk not in prices.columns:
            raise RuntimeError(f"missing sleeve ticker {tk}")
        sleeves[key] = prices[tk].resample("ME").last().pct_change()

    R = pd.DataFrame(sleeves).dropna()
    order = ["TREND", "EQ", "BOND", "GOLD", "COMM"]
    R = R[order]
    print(f"  common monthly sample: {R.index[0].date()} -> {R.index[-1].date()}, "
          f"{len(R)} months")

    # Evaluation window: after the longest burn-in, so IS vs OOS use identical months.
    eval_idx = R.index[BURN_IN:]
    Rw = R.loc[eval_idx]

    # --- Standalone sleeves (buy-and-hold, no turnover cost) over eval window. ---
    sleeve_stats = {c: perf(Rw[c]) for c in R.columns}
    best_sleeve = max(sleeve_stats, key=lambda c: sleeve_stats[c]["sharpe"])

    # --- Benchmarks over eval window. ---
    spy_series = Rw["EQ"]
    spy_stats = perf(spy_series)
    # 60/40 monthly-rebalanced with cost.
    w6040 = np.array([0.0, 0.6, 0.4, 0.0, 0.0])  # TREND/EQ/BOND/GOLD/COMM
    p6040 = run_portfolio(Rw, constant_weight_series(Rw, w6040)).dropna()
    stats_6040 = perf(p6040)

    # --- Combined portfolios: in-sample weights vs walk-forward weights. ---
    full_hist = R  # for in-sample (look-ahead) weights
    combos = {}
    combined_series = {}
    # Methods whose walk-forward series spans the FULL eval window (GFC included):
    # EW (no estimation) and IV (12m burn <= BURN_IN). RP/MV need 36m => shorter.
    FULL_WINDOW_METHODS = {"EW", "IV"}
    for m, func in WEIGHT_FUNCS.items():
        window = COV_WINDOW if m in COV_BASED else (IV_WINDOW if m == "IV" else 1)

        # IN-SAMPLE: estimate weights on the FULL sample (look-ahead) then hold.
        w_is = func(full_hist)              # uses entire history => look-ahead
        is_series = run_portfolio(Rw, constant_weight_series(Rw, w_is)).dropna()

        # WALK-FORWARD: trailing-window weights, strictly past data.
        Wwf_full = walkforward_weight_series(R, func, max(window, 1))
        wf_series = run_portfolio(Rw, Wwf_full.loc[eval_idx]).dropna()

        # Fair give-back: compare IS vs WF over the SAME (WF-valid) months.
        is_on_wf = is_series.reindex(wf_series.index)

        combos[m] = {
            "full_window_valid": m in FULL_WINDOW_METHODS,
            "wf_span": [str(wf_series.index[0].date()), str(wf_series.index[-1].date())],
            "wf_n_months": int(len(wf_series)),
            "insample_weights": {c: round(float(x), 4) for c, x in zip(R.columns, w_is)},
            "insample": perf(is_series),
            "insample_on_wf_window": perf(is_on_wf),
            "walkforward": perf(wf_series),
            "avg_wf_weights": {c: round(float(v), 4)
                               for c, v in Wwf_full.loc[wf_series.index].mean().items()},
        }
        combined_series[m + "_is"] = is_series
        combined_series[m + "_wf"] = wf_series
        print(f"  {m}: IS Sharpe {combos[m]['insample']['sharpe']:+.2f} | "
              f"WF Sharpe {combos[m]['walkforward']['sharpe']:+.2f} "
              f"({combos[m]['wf_n_months']}mo)")

    # --- Headline candidate: the best WALK-FORWARD book that is valid over the FULL
    #     GFC-inclusive window, so it is compared apples-to-apples with the sleeves
    #     and benchmarks below. (RP/MV run on a shorter, GFC-excluding sub-window and
    #     are reported for completeness, not as the headline.) ---
    best_wf_method = max(FULL_WINDOW_METHODS,
                         key=lambda m: combos[m]["walkforward"]["sharpe"])
    best_wf = combined_series[best_wf_method + "_wf"]
    best_wf_stats = combos[best_wf_method]["walkforward"]

    # Improvements vs best single sleeve, vs 60/40, vs SPY.
    def gain(a, b):
        return {"sharpe_gain": round(a["sharpe"] - b["sharpe"], 3),
                "dd_reduction_pp": round((b["maxdd"] - a["maxdd"]) * 100, 1),
                "vol_reduction_pp": round((b["vol"] - a["vol"]) * 100, 1)}

    improvements = {
        "vs_best_sleeve(%s)" % best_sleeve: gain(best_wf_stats, sleeve_stats[best_sleeve]),
        "vs_60_40": gain(best_wf_stats, stats_6040),
        "vs_SPY": gain(best_wf_stats, spy_stats),
    }

    # --- Significance of the Sharpe improvement over the best single sleeve (OOS). ---
    obs_gap, pval = sharpe_gap_pvalue(best_wf.values,
                                      Rw[best_sleeve].reindex(best_wf.index).values)
    gap_vs_best = {"observed_sharpe_gap": round(obs_gap, 3), "bootstrap_p_value": round(pval, 4),
                   "significant_at_5pct": bool(pval < 0.05)}

    # --- Fitting check: how much does walk-forward give back vs in-sample, measured
    #     over the SAME months (in-sample look-ahead weights vs trailing weights)? ---
    fitting = {m: {"insample_sharpe": round(combos[m]["insample_on_wf_window"]["sharpe"], 3),
                   "walkforward_sharpe": round(combos[m]["walkforward"]["sharpe"], 3),
                   "give_back": round(combos[m]["insample_on_wf_window"]["sharpe"]
                                      - combos[m]["walkforward"]["sharpe"], 3),
                   "wf_window": combos[m]["wf_span"]}
                for m in WEIGHT_FUNCS}

    # --- Sleeve correlation matrix (evidence the blocks are genuinely diversifying). ---
    corr = Rw.corr()
    corr_dict = {a: {b: round(float(corr.loc[a, b]), 2) for b in R.columns} for a in R.columns}

    # --- Run the headline walk-forward combined NET series through the harness. ---
    # n_eff: single combined series, monthly non-overlapping -> n_eff = n.
    n_trials = PRIOR_TRIALS + N_CONSTRUCTION_METHODS
    ev = qv.evaluate(
        "combined_%s_walkforward" % best_wf_method,
        best_wf.values,
        n_trials=n_trials,
        periods_per_year=MONTHS_PER_YEAR,
        trades_per_period=1.0,
        n_effective=float(len(best_wf)),
        benchmark_returns=spy_series.reindex(best_wf.index).values,
    )
    ev_line = qv.verdict_line(ev)
    # Also the excess vs the BEST SINGLE SLEEVE (the mandate's real benchmark).
    ev_vs_sleeve = qv.evaluate(
        "combined_vs_best_sleeve",
        best_wf.values, n_trials=n_trials, periods_per_year=MONTHS_PER_YEAR,
        trades_per_period=1.0, n_effective=float(len(best_wf)),
        benchmark_returns=Rw[best_sleeve].reindex(best_wf.index).values,
    )

    # --- Purged CV on the headline walk-forward series. ---
    arr = best_wf.values
    fold_sharpes = []
    for tr, te in qv.purged_kfold_splits(len(arr), n_splits=5, embargo_pct=0.02, label_horizon=1):
        seg = arr[te]
        if len(seg) > 3 and seg.std(ddof=1) > 0:
            fold_sharpes.append(round(sharpe_monthly(seg), 3))

    # --- Crisis windows: combined vs SPY vs 60/40. 2022 is the honest stress. ---
    def cum(series, lo, hi):
        s = series[(series.index >= lo) & (series.index <= hi)]
        return round(float((1 + s).prod() - 1) * 100, 1) if len(s) else None

    crisis = {}
    for label, lo, hi in [
        ("2008_GFC", "2007-11-01", "2009-02-28"),
        ("2020_COVID", "2020-02-01", "2020-04-30"),
        ("2022_stock_bond_bear", "2022-01-01", "2022-12-31"),
    ]:
        lo, hi = pd.Timestamp(lo), pd.Timestamp(hi)
        crisis[label] = {
            "combined_%s" % best_wf_method: cum(best_wf, lo, hi),
            "SPY": cum(spy_series, lo, hi),
            "60_40": cum(p6040, lo, hi),
            "best_sleeve_%s" % best_sleeve: cum(Rw[best_sleeve], lo, hi),
        }

    out = {
        "generated": datetime.now().isoformat(),
        "span_full": [str(R.index[0].date()), str(R.index[-1].date())],
        "n_months_full": int(len(R)),
        "eval_window": [str(eval_idx[0].date()), str(eval_idx[-1].date())],
        "n_months_eval": int(len(eval_idx)),
        "burn_in_months": BURN_IN,
        "cost_bps_per_side": COST_BPS_PER_SIDE,
        "sleeve_stats": sleeve_stats,
        "best_single_sleeve": best_sleeve,
        "benchmark_60_40": stats_6040,
        "benchmark_SPY": spy_stats,
        "sleeve_correlations": corr_dict,
        "construction_methods": combos,
        "best_walkforward_method": best_wf_method,
        "best_walkforward_stats": best_wf_stats,
        "improvements": improvements,
        "sharpe_gap_vs_best_sleeve": gap_vs_best,
        "fitting_check_is_vs_wf": fitting,
        "search_count": n_trials,
        "harness_vs_SPY": ev,
        "harness_vs_best_sleeve": ev_vs_sleeve,
        "harness_verdict_line": ev_line,
        "purged_cv_fold_sharpes": fold_sharpes,
        "crisis_windows_pct": crisis,
    }
    with open(JSON_OUT, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nBest walk-forward method: {best_wf_method}  Sharpe {best_wf_stats['sharpe']:+.2f}")
    print(f"Best single sleeve: {best_sleeve}  Sharpe {sleeve_stats[best_sleeve]['sharpe']:+.2f}")
    print(f"60/40 Sharpe {stats_6040['sharpe']:+.2f} | SPY Sharpe {spy_stats['sharpe']:+.2f}")
    print(f"Sharpe gap vs best sleeve {obs_gap:+.2f}  bootstrap p={pval:.3f}")
    print(ev_line)
    print(f"Wrote {JSON_OUT}")
    return out


if __name__ == "__main__":
    main()
