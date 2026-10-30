#!/usr/bin/env python3
"""rebalance_premium_lab.py — does systematic rebalancing between volatile,
low-correlation assets generate real after-cost GEOMETRIC return above the same
mix bought and held (drifting)?  "Shannon's Demon" / volatility harvesting,
tested as a MECHANISM: no prediction anywhere.

THE MEASUREMENT
---------------
For each mix and rebalance rule:

    rebalancing premium = geometric ann. return of the REBALANCED portfolio
                        - geometric ann. return of the SAME initial-weight mix
                          BOUGHT AND HELD (weights drifting, zero trades)

reported gross and net of costs, side by side with the theoretical expectation
(Fernholz excess growth rate; for a 50/50 two-asset mix
    gamma* = (sigma_A^2 + sigma_B^2 - 2*sigma_AB) / 8 ).

HONESTY NOTES (written before running)
--------------------------------------
* The theory (gamma*) is the harvest over the WEIGHTED AVERAGE of the legs'
  geometric growth rates. Buy-and-hold is NOT that average: a drifting book
  concentrates into whichever leg trends, and in a strong trend that "winner
  drift" can exceed the harvest. So we decompose:
      premium_vs_BH = (harvest vs weighted-avg leg growth)   [~ theory]
                    - (BH winner-drift bonus vs same baseline)
                    - (cost drag)
  and report all three. Theory-vs-realized is the check on the first term.
* Cash leg earns 0% by default (conservative; a 4% variant is shown for
  BTC/cash). Entry costs are identical for both books and cancel; no entry
  trade is charged to either.
* Costs: 10 bps/side on turnover for ETF legs, 30 bps/side for crypto legs,
  0 for cash. Charged on EVERY rebalance trade, per leg, on traded notional.
* Data: yfinance daily adjusted closes, max available history per mix
  (intersection of leg calendars). Kraken public daily OHLC was checked and
  caps at 720 bars (2024-08 onward) — SHORTER than yfinance for BTC (2014-09)
  and ETH (2017-11), so yfinance is used for crypto too.
* Search count: house documented 1,402 prior trials + every mix x rule combo
  tested here (counted exactly at runtime). No pre-registration is created.

READ-ONLY research. No capital, no orders, no engine, no secrets.
"""
import json
import math
import os
import sys
import warnings
from datetime import date

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import quant_validation as qv  # noqa: E402  (read-only import, never modified)

REPORTS = os.path.join(HERE, "reports")
PRICE_CACHE = os.path.join(REPORTS, "rebalance_premium_prices.csv")
RECEIPT = os.path.join(REPORTS, "rebalance_premium_lab.json")

HOUSE_PRIOR_TRIALS = 1402          # documented house search count before this lab

BPS = 1e-4
COST_ETF = 10 * BPS                # per side, on traded notional
COST_CRYPTO = 30 * BPS
CASH_RATE_BASE = 0.00              # conservative: cash earns nothing
CASH_RATE_ALT = 0.04               # variant shown for BTC/cash only

TICKERS = ["BTC-USD", "ETH-USD", "GLD", "TLT", "SPY"]
CRYPTO = {"BTC-USD", "ETH-USD"}

# name, legs [(asset, weight)], cash_rate (only used if a leg is CASH)
MIXES = [
    ("BTC/cash(0%)",   [("BTC-USD", 0.5), ("CASH", 0.5)], CASH_RATE_BASE),
    ("BTC/cash(4%)",   [("BTC-USD", 0.5), ("CASH", 0.5)], CASH_RATE_ALT),
    ("BTC/GLD",        [("BTC-USD", 0.5), ("GLD", 0.5)],  None),
    ("BTC/TLT",        [("BTC-USD", 0.5), ("TLT", 0.5)],  None),
    ("SPY/TLT",        [("SPY", 0.5), ("TLT", 0.5)],      None),
    ("GLD/TLT",        [("GLD", 0.5), ("TLT", 0.5)],      None),
    ("SPY/GLD",        [("SPY", 0.5), ("GLD", 0.5)],      None),
    ("BTC/ETH",        [("BTC-USD", 0.5), ("ETH-USD", 0.5)], None),
    ("BTC/GLD/TLT",    [("BTC-USD", 1/3), ("GLD", 1/3), ("TLT", 1/3)], None),
]

RULES = ["daily", "weekly", "monthly", "band5", "band10"]  # band = +/- pp of weight


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def fetch_prices():
    if os.path.exists(PRICE_CACHE):
        px = pd.read_csv(PRICE_CACHE, index_col=0, parse_dates=True)
        if set(TICKERS) <= set(px.columns):
            return px
    import yfinance as yf
    cols = {}
    for t in TICKERS:
        d = yf.download(t, start="1990-01-01", interval="1d",
                        progress=False, auto_adjust=True)
        s = d["Close"]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
        cols[t] = s
    px = pd.DataFrame(cols).sort_index()
    px.to_csv(PRICE_CACHE)
    return px


def leg_returns(px, legs, cash_rate):
    """Daily simple returns for each leg, aligned on the intersection of the
    non-cash legs' calendars. Cash accrues at the stated annual rate on that
    calendar (365-day accrual for pure-crypto calendars, 252 otherwise)."""
    non_cash = [a for a, _ in legs if a != "CASH"]
    rets = px[non_cash].dropna().pct_change().dropna()
    if any(a == "CASH" for a, _ in legs):
        crypto_only = all(a in CRYPTO for a in non_cash)
        per_year = 365.0 if crypto_only else 252.0
        daily = (1.0 + cash_rate) ** (1.0 / per_year) - 1.0
        rets = rets.copy()
        rets["CASH"] = daily
    order = [a for a, _ in legs]
    return rets[order]


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
def rebalance_days(index, rule):
    """Boolean: is this bar a calendar-rule rebalance bar? (bands handled live)"""
    if rule == "daily":
        return np.ones(len(index), dtype=bool)
    if rule == "weekly":
        wk = index.isocalendar()
        key = wk["year"].astype(int) * 100 + wk["week"].astype(int)
        return (key.values != np.roll(key.values, 1)) | (np.arange(len(index)) == 0)
    if rule == "monthly":
        key = index.year * 100 + index.month
        return (key.values != np.roll(key.values, 1)) | (np.arange(len(index)) == 0)
    return np.zeros(len(index), dtype=bool)  # band rules: threshold check instead


def simulate(rets, targets, costs, rule):
    """One pass produces gross and net wealth (same trade path — costs are
    multiplicative on wealth and do not alter the weight path).

    Convention: at each close, weights drift with the day's returns; if the rule
    fires (calendar bar, or any |w - target| > band), the book trades back to
    target AT THAT CLOSE and pays cost_i * |dw_i| per leg.
    """
    R = rets.values
    T, k = R.shape
    targets = np.asarray(targets, float)
    costs = np.asarray(costs, float)
    band = {"band5": 0.05, "band10": 0.10}.get(rule)
    cal = rebalance_days(rets.index, rule)

    w = targets.copy()
    Wn = Wg = 1.0
    wealth_n = np.empty(T)
    wealth_g = np.empty(T)
    n_events = 0
    turnover_sum = 0.0     # two-sided: sum over events of sum_i |dw_i|

    for t in range(T):
        r = R[t]
        rp = float(np.dot(w, r))
        Wn *= (1.0 + rp)
        Wg *= (1.0 + rp)
        w = w * (1.0 + r) / (1.0 + rp)

        fire = bool(cal[t]) if band is None else bool(
            np.max(np.abs(w - targets)) > band)
        if fire:
            dw = np.abs(targets - w)
            tot = float(dw.sum())
            if tot > 1e-12:
                n_events += 1
                turnover_sum += tot
                Wn *= (1.0 - float(np.dot(dw, costs)))
            w = targets.copy()
        wealth_n[t] = Wn
        wealth_g[t] = Wg

    return wealth_n, wealth_g, n_events, turnover_sum


def buy_hold(rets, targets):
    """Same initial weights, zero trades, weights drift."""
    growth = (1.0 + rets.values).cumprod(axis=0)
    w = np.asarray(targets, float)
    return growth @ w


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def years_of(index):
    return max((index[-1] - index[0]).days / 365.25, 1e-9)


def cagr(wealth, yrs):
    return wealth[-1] ** (1.0 / yrs) - 1.0


def max_dd(wealth):
    w = np.asarray(wealth, float)
    peak = np.maximum.accumulate(w)
    return float((w / peak - 1.0).min())


def daily_returns(wealth):
    w = np.concatenate([[1.0], np.asarray(wealth, float)])
    return w[1:] / w[:-1] - 1.0


def ann_sharpe(returns, ppy):
    r = np.asarray(returns, float)
    sd = r.std(ddof=1)
    return float(r.mean() / sd * math.sqrt(ppy)) if sd > 0 else 0.0


def theory_gamma(rets, targets, ppy):
    """Fernholz excess growth rate, annualized (log terms).
    For 50/50 two assets this is (s_A^2 + s_B^2 - 2*s_AB)/8."""
    w = np.asarray(targets, float)
    S = np.cov(rets.values, rowvar=False) * ppy
    S = np.atleast_2d(S)
    return 0.5 * (float(np.dot(w, np.diag(S))) - float(w @ S @ w))


def log_growth(series_1p, yrs):
    return float(np.log(series_1p[-1]) / yrs)


def lag1_autocorr(x):
    x = np.asarray(x, float)
    if x.size < 3 or x.std() == 0:
        return 0.0
    return float(np.corrcoef(x[:-1], x[1:])[0, 1])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    px = fetch_prices()
    results = []
    premium_series_store = {}   # (mix, rule) -> (dates, net premium daily series, ppy)

    for mix_name, legs, cash_rate in MIXES:
        rets = leg_returns(px, legs, cash_rate if cash_rate is not None else 0.0)
        targets = [wt for _, wt in legs]
        costs = [0.0 if a == "CASH" else (COST_CRYPTO if a in CRYPTO else COST_ETF)
                 for a, _ in legs]
        yrs = years_of(rets.index)
        ppy = len(rets) / yrs

        bh_wealth = buy_hold(rets, targets)
        bh_ret = daily_returns(bh_wealth)
        bh_cagr = cagr(bh_wealth, yrs)

        # decomposition baselines (log terms)
        leg_g = {a: float(np.log(1.0 + rets[a].values).mean() * ppy)
                 for a, _ in legs}
        wavg_leg_g = sum(wt * leg_g[a] for a, wt in legs)
        bh_bonus = log_growth(bh_wealth, yrs) - wavg_leg_g
        gamma = theory_gamma(rets, targets, ppy)

        # pairwise correlation of the (non-cash) legs
        non_cash = [a for a, _ in legs if a != "CASH"]
        corr = (float(rets[non_cash].corr().values[np.triu_indices(len(non_cash), 1)].mean())
                if len(non_cash) > 1 else 0.0)

        for rule in RULES:
            wn, wg, n_ev, tov = simulate(rets, targets, costs, rule)
            rn = daily_returns(wn)
            prem_series = rn - bh_ret
            g_cagr, n_cagr = cagr(wg, yrs), cagr(wn, yrs)
            harvest = log_growth(wg, yrs) - wavg_leg_g   # gross harvest vs leg avg
            row = {
                "mix": mix_name, "rule": rule,
                "start": str(rets.index[0].date()), "end": str(rets.index[-1].date()),
                "years": round(yrs, 2), "n_obs": len(rets), "ppy": round(ppy, 1),
                "leg_corr": round(corr, 3),
                "bh_cagr": bh_cagr, "gross_cagr": g_cagr, "net_cagr": n_cagr,
                "gross_premium": g_cagr - bh_cagr,
                "net_premium": n_cagr - bh_cagr,
                "theory_gamma": gamma,
                "harvest_vs_legavg": harvest,
                "bh_winnerdrift_bonus": bh_bonus,
                "cost_drag": g_cagr - n_cagr,
                "n_rebalances": n_ev,
                "rebalances_per_year": round(n_ev / yrs, 1),
                "ann_turnover_2sided": round(tov / yrs, 3),
                "net_sharpe": round(ann_sharpe(rn, ppy), 3),
                "bh_sharpe": round(ann_sharpe(bh_ret, ppy), 3),
                "net_maxdd": round(max_dd(wn), 4),
                "bh_maxdd": round(max_dd(bh_wealth), 4),
                "prem_sharpe_perperiod": round(qv.sharpe_ratio(prem_series), 6),
            }
            results.append(row)
            premium_series_store[(mix_name, rule)] = (rets.index, prem_series, ppy, wn, bh_wealth)

    n_combos = len(results)
    n_trials = HOUSE_PRIOR_TRIALS + n_combos

    # ---------------- regime windows (best net rule per mix) ----------------
    best_rule_per_mix = {}
    for r in results:
        cur = best_rule_per_mix.get(r["mix"])
        if cur is None or r["net_premium"] > cur["net_premium"]:
            best_rule_per_mix[r["mix"]] = r

    regimes = {}
    for mix_name, row in best_rule_per_mix.items():
        idx, _, ppy, wn, wbh = premium_series_store[(mix_name, row["rule"])]
        L = np.log(wn) - np.log(wbh)
        win = int(min(round(ppy), max(30, len(L) // 3)))
        if len(L) > win + 2:
            d = L[win:] - L[:-win]
            ann = d * (ppy / win)
            i_hi, i_lo = int(np.argmax(ann)), int(np.argmin(ann))
            regimes[mix_name] = {
                "rule": row["rule"], "window_bars": win,
                "best_ann_premium": float(ann[i_hi]),
                "best_window": [str(idx[i_hi].date()), str(idx[i_hi + win].date())],
                "worst_ann_premium": float(ann[i_lo]),
                "worst_window": [str(idx[i_lo].date()), str(idx[i_lo + win].date())],
            }

    # ---------------- harness: best NET premium combo ----------------
    trial_sharpes = [r["prem_sharpe_perperiod"] for r in results]
    best = max(results, key=lambda r: r["net_premium"])
    idx, prem, ppy, _, _ = premium_series_store[(best["mix"], best["rule"])]
    rho = lag1_autocorr(prem)
    n_eff = len(prem)
    if rho > 0:
        n_eff = len(prem) * (1.0 - rho) / (1.0 + rho)
    ev = qv.evaluate(
        "rebalancing-premium %s %s (net, vs drift BH)" % (best["mix"], best["rule"]),
        prem, n_trials=n_trials, trial_sharpes=trial_sharpes,
        periods_per_year=ppy, trades_per_period=1.0, n_effective=n_eff)

    # FDR across the whole family (autocorr-adjusted per-combo p-values)
    pvals = []
    for r in results:
        _, s, _, _, _ = premium_series_store[(r["mix"], r["rule"])]
        rr = lag1_autocorr(s)
        ne = len(s) * (1.0 - rr) / (1.0 + rr) if rr > 0 else len(s)
        t = qv.sharpe_ratio(s) * math.sqrt(max(ne, 2))
        from scipy import stats as st
        pvals.append(float(2 * (1 - st.norm.cdf(abs(t)))))
    rejected, adj = qv.benjamini_hochberg(pvals, alpha=0.05)

    receipt = {
        "date": str(date.today()),
        "n_combos_tested": n_combos,
        "house_prior_trials": HOUSE_PRIOR_TRIALS,
        "n_trials_total": n_trials,
        "cost_model": {"etf_bps_side": 10, "crypto_bps_side": 30, "cash": 0},
        "cash_rate_base": CASH_RATE_BASE, "cash_rate_variant": CASH_RATE_ALT,
        "kraken_check": "public daily OHLC caps at 720 bars (2024-08→) — shorter "
                        "than yfinance for BTC/ETH, so yfinance used throughout",
        "results": results,
        "regimes": regimes,
        "harness_best_net": ev,
        "harness_best_net_lag1_autocorr": round(rho, 4),
        "bh_fdr_any_survivor": bool(rejected.any()),
        "bh_fdr_min_adj_p": float(adj.min()) if adj.size else None,
    }
    with open(RECEIPT, "w") as f:
        json.dump(receipt, f, indent=2, default=str)

    # ---------------- console report ----------------
    pc = lambda x: "%+.2f%%" % (100 * x)
    print("=" * 100)
    print("REBALANCING PREMIUM LAB — rebalanced vs identical mix buy-and-held (drifting)")
    print("costs: 10 bps/side ETF, 30 bps/side crypto, cash 0%% | combos tested: %d | N total: %d"
          % (n_combos, n_trials))
    print("=" * 100)
    hdr = ("%-14s %-8s %-10s %5s | %8s %8s %8s | %8s %8s %8s | %6s %7s"
           % ("mix", "rule", "start", "yrs", "BH CAGR", "grossPr", "netPrem",
              "theory", "harvest", "BHdrift", "reb/yr", "tov/yr"))
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        print("%-14s %-8s %-10s %5.1f | %8s %8s %8s | %8s %8s %8s | %6.1f %7.2f"
              % (r["mix"], r["rule"], r["start"], r["years"],
                 pc(r["bh_cagr"]), pc(r["gross_premium"]), pc(r["net_premium"]),
                 pc(r["theory_gamma"]), pc(r["harvest_vs_legavg"]),
                 pc(r["bh_winnerdrift_bonus"]),
                 r["rebalances_per_year"], r["ann_turnover_2sided"]))
    print()
    print("Risk view (best net rule per mix): rebalanced vs buy-hold")
    for m, r in best_rule_per_mix.items():
        print("  %-14s %-8s | net CAGR %8s (Sh %5.2f, DD %6.1f%%) vs BH %8s (Sh %5.2f, DD %6.1f%%)"
              % (m, r["rule"], pc(r["net_cagr"]), r["net_sharpe"], 100 * r["net_maxdd"],
                 pc(r["bh_cagr"]), r["bh_sharpe"], 100 * r["bh_maxdd"]))
    print()
    print("Regime windows (rolling ~1y, best net rule per mix):")
    for m, g in regimes.items():
        print("  %-14s best %s %s→%s | worst %s %s→%s"
              % (m, pc(g["best_ann_premium"]), g["best_window"][0], g["best_window"][1],
                 pc(g["worst_ann_premium"]), g["worst_window"][0], g["worst_window"][1]))
    print()
    print("HARNESS — best net-premium combo: %s / %s" % (best["mix"], best["rule"]))
    print("  lag-1 autocorr of premium series: %.4f -> n_eff %.0f of %d"
          % (rho, ev["n_effective"], ev["n_obs"]))
    for k in ["sharpe_annualized", "t_stat", "p_value", "n_trials_searched",
              "expected_max_sharpe_under_null", "deflated_sharpe",
              "survives_deflation"]:
        print("  %-32s %s" % (k, ev[k]))
    print("  " + qv.verdict_line(ev))
    print("  BH-FDR across all %d combos: any survivor at 5%%: %s (min adj p %.3f)"
          % (n_combos, bool(rejected.any()), float(adj.min())))
    print()
    print("Receipt: %s" % RECEIPT)


if __name__ == "__main__":
    main()
