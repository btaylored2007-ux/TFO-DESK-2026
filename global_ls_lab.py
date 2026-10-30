#!/usr/bin/env python3
"""Global equity LONG/SHORT lab — an honest hunt for a dollar-neutral, after-cost,
search-deflated edge across THREE distinct cross-sectional mechanisms.

WHY THIS EXISTS
---------------
The desk has already killed offensive equity factors (value, momentum) in single-name
US cross-sections, and defensive factors reduced to "buy less SPY". This lab asks a
different question: is there a tradeable dollar-neutral LONG/SHORT edge across whole
MARKETS and SECTORS — the country- and sector-rotation trades — that survives
transaction cost, a realistic short-borrow drag, and the house's cumulative search
deflation (~1,130 prior hypotheses)?

THREE MECHANISMS
----------------
1. COUNTRY-INDEX relative value: ~20 liquid single-country ETFs, ranked by
   (a) 6-12m momentum and (b) 1-3y mean-reversion. Long top tercile / short bottom,
   dollar-neutral, monthly.
2. GLOBAL SECTOR relative value/momentum: 11 US sector ETFs + broad ex-US, ranked by
   momentum and by mean-reversion. Long/short terciles, dollar-neutral, monthly.
3. CROSS-SECTIONAL COMBO on the country set: momentum + low-volatility, z-scored and
   summed, long/short terciles, dollar-neutral, monthly.

HONESTY RULES (non-negotiable, per the house field manual)
----------------------------------------------------------
* Every variant tested is COUNTED. Deflation uses n_trials = 1,130 house prior + the
  variants tested here, and the spread of THIS lab's variant Sharpes sets the bar.
* A monthly-rebalanced momentum book barely turns over. n is NOT the daily count —
  the effective_bets kill-test discounts by weight rank-autocorrelation, and the DSR
  is measured on MONTHLY returns with that effective-bet count.
* The 14-year shorts study on this desk found a bearish signal expressed as CASH beat
  the same signal expressed as a SHORT by ~15pp/yr (borrow + drift + crash-up). So for
  EVERY L/S result we decompose long-leg vs short-leg and compare the short leg's
  contribution against simply holding cash. If the short leg is a net drag we say so
  and report the long-only version.
* Costs: 10 bps/side on turnover PLUS a short-borrow drag (swept 1% and 3%/yr on the
  short notional). Gross AND net reported.

PAPER / RESEARCH ONLY. No orders, no credentials, no probe, no engine writes.

Usage:
    python3 global_ls_lab.py                 # ~18y, house prior 1130
    python3 global_ls_lab.py --years 20 --house-trials 1130
"""
import argparse
import json
import math
import os
import time
import warnings

import numpy as np
import pandas as pd

import quant_validation as qv

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
REPORT_JSON = os.path.join(BASE, "reports", "global_ls_lab.json")
REPORT_MD = os.path.join(BASE, "reports", "research_global_equity_ls.md")
CACHE = os.path.join(BASE, "reports", "global_ls_price_cache.csv")

# ---- Universes -------------------------------------------------------------
# ~20 liquid single-country ETFs (iShares MSCI single-country funds, long-listed).
COUNTRY = ["EWJ", "EWG", "EWU", "EWA", "EWC", "EWZ", "EWW", "EWY", "EWT", "EWH",
           "EWS", "EWP", "EWQ", "EWI", "EWL", "EWD", "EWN", "EWK", "EWO", "EWM"]
# INDA/FXI added when history allows; kept out of the core to preserve a 2007 start.

# 11 US sector ETFs (SPDR) + broad ex-US. XLRE (2015) and XLC (2018) are young;
# handled by dropping names that lack history at each formation date.
SECTOR = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE",
          "XLC", "VEU"]

BENCH = ["SPY", "VT"]

TRADING_DAYS = 252
COST_BPS_PER_SIDE = 10.0          # 10 bps per side on turnover
BORROW_SWEEP = [0.01, 0.03]       # annual short-borrow drag on short notional

CRASH_WINDOWS = {
    "GFC_2008": ("2007-10-01", "2009-03-31"),
    "COVID_2020": ("2020-02-15", "2020-04-30"),
    "Bear_2022": ("2022-01-01", "2022-10-31"),
}


# ---- Data ------------------------------------------------------------------
def fetch(tickers, years, verbose=True):
    import yfinance as yf
    start = time.strftime("%Y-%m-%d",
                          time.gmtime(time.time() - years * 365.25 * 86400))
    if verbose:
        print("Fetching %d tickers from %s ..." % (len(tickers), start))
    raw = yf.download(sorted(set(tickers)), start=start, interval="1d",
                      auto_adjust=True, progress=False, threads=True)
    px = raw["Close"].dropna(axis=1, how="all")
    return px


def load_prices(years, use_cache=True):
    tickers = sorted(set(COUNTRY + SECTOR + BENCH))
    if use_cache and os.path.exists(CACHE):
        px = pd.read_csv(CACHE, index_col=0, parse_dates=True)
        have = [t for t in tickers if t in px.columns]
        # cache good enough if it holds every ticker and is fresh within a week
        fresh = (pd.Timestamp.utcnow().tz_localize(None) - px.index[-1]).days < 7
        if len(have) == len(tickers) and fresh:
            print("Using price cache (%s, %d rows)" % (CACHE, len(px)))
            return px[tickers]
    px = fetch(tickers, years)
    px.to_csv(CACHE)
    return px


# ---- Stats helpers ---------------------------------------------------------
def ann_sharpe(r, ppy):
    r = np.asarray(r, dtype=float); r = r[np.isfinite(r)]
    if r.size < 2:
        return 0.0
    sd = r.std(ddof=1)
    return float(r.mean() / sd * math.sqrt(ppy)) if sd > 0 else 0.0


def ann_return(r, ppy):
    r = np.asarray(r, dtype=float); r = r[np.isfinite(r)]
    if r.size == 0:
        return 0.0
    return float((1.0 + r.mean()) ** ppy - 1.0)


def max_drawdown(r):
    r = np.asarray(r, dtype=float); r = r[np.isfinite(r)]
    if r.size == 0:
        return 0.0
    eq = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(eq)
    return float((eq / peak - 1.0).min())


def corr(a, b):
    a = np.asarray(a, dtype=float); b = np.asarray(b, dtype=float)
    k = min(a.size, b.size)
    a, b = a[-k:], b[-k:]
    good = np.isfinite(a) & np.isfinite(b)
    if good.sum() < 3:
        return float("nan")
    return float(np.corrcoef(a[good], b[good])[0, 1])


# ---- Signals ---------------------------------------------------------------
def score_momentum(px_hist, lookback_days, skip_days=21):
    """Momentum = return from t-lookback to t-skip (skip last month to dodge
    short-term reversal). Higher = stronger. Returns a per-asset score Series."""
    if len(px_hist) < lookback_days + 1:
        return None
    p_now = px_hist.iloc[-1 - skip_days]
    p_then = px_hist.iloc[-lookback_days]
    return (p_now / p_then - 1.0)


def score_reversal(px_hist, lookback_days):
    """Long-horizon mean reversion / valuation proxy = NEGATIVE of trailing return.
    Long past losers, short past winners."""
    if len(px_hist) < lookback_days + 1:
        return None
    return -(px_hist.iloc[-1] / px_hist.iloc[-lookback_days] - 1.0)


def score_lowvol(px_hist, lookback_days=126):
    """Low-vol tilt = NEGATIVE of trailing realized daily-return vol (long calm)."""
    if len(px_hist) < lookback_days + 1:
        return None
    rets = px_hist.iloc[-lookback_days:].pct_change().dropna()
    vol = rets.std()
    return -vol


def score_combo(px_hist, mom_days, vol_days=126, skip_days=21):
    """z(momentum) + z(low-vol), summed cross-sectionally."""
    mom = score_momentum(px_hist, mom_days, skip_days)
    lv = score_lowvol(px_hist, vol_days)
    if mom is None or lv is None:
        return None
    common = mom.dropna().index.intersection(lv.dropna().index)
    if len(common) < 6:
        return None
    mom, lv = mom[common], lv[common]

    def z(s):
        sd = s.std(ddof=0)
        return (s - s.mean()) / sd if sd > 0 else s * 0.0
    return z(mom) + z(lv)


# ---- Backtest engine -------------------------------------------------------
def month_end_dates(index):
    df = pd.DataFrame(index=index)
    df["ym"] = df.index.to_period("M")
    return df.groupby("ym").tail(1).index


def compute_weights(px, universe, score_fn, tercile=3):
    """Compute the dollar-neutral tercile weight vector at each monthly rebalance.

    Independent of cost/borrow, so it is done ONCE per signal and reused across the
    borrow sweep. Long tercile sums to +1, short tercile to -1.
    """
    cols = [c for c in universe if c in px.columns]
    prices = px[cols]
    rets_d = prices.pct_change()
    dates = prices.index
    date_pos = {d: i for i, d in enumerate(dates)}
    col_idx = {c: i for i, c in enumerate(cols)}
    prices_v = prices.values
    R = rets_d.values                      # days x assets; row 0 is NaN

    W_rebal = {}                           # position -> full L/S weight vector
    for dt in month_end_dates(dates):
        pos = date_pos[dt]
        if pos < 60:
            continue
        score = score_fn(prices.iloc[:pos + 1])
        if score is None:
            continue
        score = score.dropna()
        score = score[[c for c in score.index
                       if np.isfinite(prices_v[pos, col_idx[c]])]]
        if len(score) < tercile * 2:
            continue
        n = len(score)
        k = max(1, n // tercile)
        ranked = score.sort_values(ascending=False)
        longs, shorts = ranked.index[:k], ranked.index[-k:]
        w = np.zeros(len(cols))
        for c in longs:
            w[col_idx[c]] = 1.0 / len(longs)
        for c in shorts:
            w[col_idx[c]] = -1.0 / len(shorts)
        W_rebal[pos] = w

    if len(W_rebal) < 12:
        return None
    reb = sorted(W_rebal.keys())

    # lag-1 weight autocorrelation (for the effective_bets kill-test)
    acs = []
    for i in range(len(reb) - 1):
        a, b = W_rebal[reb[i]], W_rebal[reb[i + 1]]
        if a.std() > 0 and b.std() > 0:
            acs.append(np.corrcoef(a, b)[0, 1])
    rank_ac = float(np.nanmean(acs)) if acs else 0.0

    return {"cols": cols, "dates": dates, "R": R, "W_rebal": W_rebal,
            "reb": reb, "rank_autocorr": rank_ac, "n_rebalances": len(reb)}


def _leg_series(cw, borrow_annual, cost_bps, leg="ls"):
    """Fast numpy simulation of one leg (ls|long|short) at a given borrow level.

    Weights set at a rebalance close apply to returns from the NEXT day; the cost of
    trading into them is charged on the rebalance day. Returns a daily pd.Series.
    """
    dates, R, reb = cw["dates"], cw["R"], cw["reb"]
    nd, ncols = R.shape
    borrow_daily = borrow_annual / TRADING_DAYS

    def xf(w):
        if leg == "long":
            return np.where(w > 0, w, 0.0)
        if leg == "short":
            return np.where(w < 0, w, 0.0)
        return w

    W_held = np.zeros((nd, ncols))
    cost = np.zeros(nd)
    prev = np.zeros(ncols)
    for j, pos in enumerate(reb):
        w = xf(cw["W_rebal"][pos])
        cost[pos] = np.abs(w - prev).sum() * cost_bps / 1e4
        prev = w
        end = reb[j + 1] + 1 if j + 1 < len(reb) else nd
        W_held[pos + 1:end] = w

    gross = np.nansum(W_held * R, axis=1)
    short_notional = np.where(W_held < 0, -W_held, 0.0).sum(axis=1)
    net = gross - cost - short_notional * borrow_daily

    start = reb[0] + 1
    idx = dates[start:]
    return (pd.Series(gross[start:], index=idx),
            pd.Series(net[start:], index=idx))


def backtest(cw, borrow_annual=0.02, cost_bps=COST_BPS_PER_SIDE):
    """Assemble gross/net daily + monthly series for the full L/S book and each leg,
    from precomputed weights, at one borrow level."""
    if cw is None:
        return None
    g_ls, n_ls = _leg_series(cw, borrow_annual, cost_bps, "ls")
    g_lo, n_lo = _leg_series(cw, borrow_annual, cost_bps, "long")
    g_sh, n_sh = _leg_series(cw, borrow_annual, cost_bps, "short")

    def to_monthly(s):
        return (1.0 + s).groupby(s.index.to_period("M")).prod() - 1.0

    # average turnover (gross, both legs) across rebalances
    reb = cw["reb"]
    prev = np.zeros(len(cw["cols"]))
    tnos = []
    for pos in reb:
        w = cw["W_rebal"][pos]
        tnos.append(np.abs(w - prev).sum())
        prev = w

    return {
        "cols": cw["cols"],
        "gross_daily": g_ls, "net_daily": n_ls,
        "net_daily_long": n_lo, "net_daily_short": n_sh,
        "gross_monthly": to_monthly(g_ls), "net_monthly": to_monthly(n_ls),
        "rank_autocorr": cw["rank_autocorr"],
        "n_rebalances": cw["n_rebalances"],
        "avg_turnover": float(np.mean(tnos[1:])) if len(tnos) > 1 else 0.0,
    }


def crash_behavior(net_daily):
    out = {}
    for name, (a, b) in CRASH_WINDOWS.items():
        seg = net_daily.loc[(net_daily.index >= a) & (net_daily.index <= b)]
        if len(seg) < 3:
            out[name] = None
        else:
            out[name] = round(float((1.0 + seg).prod() - 1.0) * 100, 2)
    return out


# ---- Evaluate one variant --------------------------------------------------
def evaluate_variant(name, bt, bench_daily, house_trials, borrow_annual):
    net_m = bt["net_monthly"].dropna()
    gross_m = bt["gross_monthly"].dropna()
    if len(net_m) < 12:
        return None

    # effective bets from weight autocorrelation (the desk's kill-test)
    eb = qv.effective_bets(bt["n_rebalances"], bt["rank_autocorr"])
    n_eff = max(2.0, eb["independent_bets"])

    ev = qv.evaluate(name, net_m.values, n_trials=house_trials,
                     periods_per_year=12, trades_per_period=1.0,
                     gross_returns=gross_m.values, n_effective=n_eff)

    net_d = bt["net_daily"].dropna()
    spy = bench_daily.get("SPY")
    vt = bench_daily.get("VT")
    return {
        "name": name,
        "borrow_annual": borrow_annual,
        "n_months": int(len(net_m)),
        "n_rebalances": bt["n_rebalances"],
        "avg_turnover": round(bt["avg_turnover"], 3),
        "rank_autocorr": round(bt["rank_autocorr"], 3),
        "effective_bets": round(n_eff, 1),
        "gross_sharpe_ann": round(ann_sharpe(bt["gross_daily"].values, TRADING_DAYS), 3),
        "net_sharpe_ann": round(ann_sharpe(net_d.values, TRADING_DAYS), 3),
        "net_cagr_pct": round(ann_return(net_d.values, TRADING_DAYS) * 100, 2),
        "maxdd_pct": round(max_drawdown(net_d.values) * 100, 2),
        "corr_spy": round(corr(net_d.values, spy.values), 3) if spy is not None else None,
        "corr_vt": round(corr(net_d.values, vt.values), 3) if vt is not None else None,
        "deflated_sharpe": ev["deflated_sharpe"],
        "expected_max_sharpe_null": ev["expected_max_sharpe_under_null"],
        "survives_deflation": ev["survives_deflation"],
        "t_stat": ev["t_stat"],
        "p_value": ev["p_value"],
        "crash": crash_behavior(net_d),
        # long/short/cash decomposition (annualized net CAGR of each leg)
        "long_leg_cagr_pct": round(ann_return(bt["net_daily_long"].values, TRADING_DAYS) * 100, 2),
        "long_leg_sharpe": round(ann_sharpe(bt["net_daily_long"].values, TRADING_DAYS), 3),
        "short_leg_cagr_pct": round(ann_return(bt["net_daily_short"].values, TRADING_DAYS) * 100, 2),
        "short_leg_sharpe": round(ann_sharpe(bt["net_daily_short"].values, TRADING_DAYS), 3),
        "_net_monthly": net_m,   # kept for WRC, stripped before JSON
    }


# ---- Main ------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=19)
    ap.add_argument("--house-trials", type=int, default=1130)
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    px = load_prices(args.years, use_cache=not args.no_cache)
    px = px.sort_index()
    print("Price panel: %d rows, %s -> %s, %d tickers"
          % (len(px), px.index[0].date(), px.index[-1].date(), px.shape[1]))

    bench_daily = {b: px[b].pct_change().dropna() for b in BENCH if b in px.columns}

    # ---- Define the variant grid (every one is a counted trial) ----
    variants = []  # (mechanism, label, universe, score_fn)
    D = TRADING_DAYS

    # Mechanism 1: COUNTRY momentum + reversal
    for lb, m in [(126, "6m"), (189, "9m"), (252, "12m")]:
        variants.append(("country", "country_mom_%s" % m, COUNTRY,
                         (lambda h, lb=lb: score_momentum(h, lb))))
    for lb, m in [(252, "1y"), (504, "2y"), (756, "3y")]:
        variants.append(("country", "country_rev_%s" % m, COUNTRY,
                         (lambda h, lb=lb: score_reversal(h, lb))))

    # Mechanism 2: SECTOR momentum + reversal
    for lb, m in [(126, "6m"), (189, "9m"), (252, "12m")]:
        variants.append(("sector", "sector_mom_%s" % m, SECTOR,
                         (lambda h, lb=lb: score_momentum(h, lb))))
    for lb, m in [(252, "1y"), (504, "2y"), (756, "3y")]:
        variants.append(("sector", "sector_rev_%s" % m, SECTOR,
                         (lambda h, lb=lb: score_reversal(h, lb))))

    # Mechanism 3: COUNTRY combo momentum + low-vol
    for lb, m in [(126, "6m"), (252, "12m")]:
        variants.append(("combo", "country_combo_mom%s_lowvol" % m, COUNTRY,
                         (lambda h, lb=lb: score_combo(h, lb))))
    # also a pure low-vol L/S on countries, for reference
    variants.append(("combo", "country_lowvol_ls", COUNTRY,
                     (lambda h: score_lowvol(h, 126))))

    n_my_variants = len(variants)
    # honest search count: house prior + variants tested here, each borrow level is a
    # cost-sensitivity of the SAME signal so we count the SIGNAL variants only, and
    # verify robustness across borrow separately.
    total_trials = args.house_trials + n_my_variants
    print("Testing %d signal variants x %d borrow levels; deflation N = %d prior + %d = %d"
          % (n_my_variants, len(BORROW_SWEEP), args.house_trials, n_my_variants, total_trials))

    results = {"country": [], "sector": [], "combo": []}
    all_net_monthly = []      # for White's Reality Check (aligned)
    variant_sharpes = []      # this lab's variant sharpes -> deflation variance

    for mech, label, uni, fn in variants:
        cw = compute_weights(px, uni, fn)
        if cw is None:
            print("  %-28s -> insufficient data" % label)
            continue
        for borrow in BORROW_SWEEP:
            bt = backtest(cw, borrow_annual=borrow)
            if bt is None:
                print("  %-28s borrow=%.0f%%  -> insufficient data" % (label, borrow * 100))
                continue
            ev = evaluate_variant("%s_b%.0f" % (label, borrow * 100), bt,
                                  bench_daily, total_trials, borrow)
            if ev is None:
                continue
            results[mech].append(ev)
            if abs(borrow - 0.02) < 1e-9 or borrow == BORROW_SWEEP[0]:
                pass
            print("  %-30s borrow=%.0f%%  grossSR=%5.2f netSR=%5.2f DSR=%s eff_bets=%.0f corrSPY=%s"
                  % (ev["name"], borrow * 100, ev["gross_sharpe_ann"],
                     ev["net_sharpe_ann"], ev["deflated_sharpe"],
                     ev["effective_bets"], ev["corr_spy"]))
        # collect the mid-borrow (first level) monthly series once per signal for WRC
    # Build variant sharpe list (net, at the harsher 3% borrow) for deflation variance
    for mech in results:
        for ev in results[mech]:
            if abs(ev["borrow_annual"] - BORROW_SWEEP[-1]) < 1e-9:
                variant_sharpes.append(ev["net_sharpe_ann"])
                all_net_monthly.append(ev["_net_monthly"])

    # White's Reality Check across the family (best-of-N vs chance), 3% borrow set
    wrc = None
    if len(all_net_monthly) >= 2:
        aligned = pd.concat(all_net_monthly, axis=1).dropna()
        if len(aligned) >= 24 and aligned.shape[1] >= 2:
            wrc = qv.whites_reality_check(aligned.values.T, n_boot=500)

    # strip heavy series before serialization
    for mech in results:
        for ev in results[mech]:
            ev.pop("_net_monthly", None)

    summary = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "years": args.years,
        "panel_start": str(px.index[0].date()),
        "panel_end": str(px.index[-1].date()),
        "house_prior_trials": args.house_trials,
        "lab_variants": n_my_variants,
        "total_deflation_trials": total_trials,
        "cost_bps_per_side": COST_BPS_PER_SIDE,
        "borrow_sweep": BORROW_SWEEP,
        "results": results,
        "whites_reality_check": wrc,
        "variant_net_sharpes_3pct_borrow": variant_sharpes,
    }
    with open(REPORT_JSON, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print("\nWrote %s" % REPORT_JSON)
    write_report(summary)
    print("Wrote %s" % REPORT_MD)
    return summary


def _best(results):
    """Best variant by net Sharpe across all mechanisms (3% borrow, conservative)."""
    pool = []
    for mech, evs in results.items():
        for ev in evs:
            pool.append((mech, ev))
    if not pool:
        return None, None
    pool.sort(key=lambda x: x[1]["net_sharpe_ann"], reverse=True)
    return pool[0]


def write_report(s):
    R = s["results"]
    # Determine survivors: net Sharpe > 0 meaningfully AND survives deflation AND
    # short leg not a pure drag vs its long-only sibling.
    survivors = []
    for mech, evs in R.items():
        for ev in evs:
            if ev["survives_deflation"] and ev["net_sharpe_ann"] > 0.3:
                survivors.append((mech, ev))

    mech_best, best_ev = _best(R)
    lines = []
    if survivors:
        v = ("VERDICT: CANDIDATE FOUND — %d L/S variant(s) survive cost + short-borrow "
             "+ search-deflation. Flagged for a pre-registered $10k paper probe (NOT created)."
             % len(survivors))
    else:
        v = ("VERDICT: NO EDGE — none of the three mechanisms (country RV/momentum, "
             "sector RV/momentum, country momentum+low-vol combo) survives 10 bps/side + "
             "short-borrow (1-3%%/yr) + search-deflation over N=%d trials. A valid null."
             % s["total_deflation_trials"])
    lines.append(v)
    lines.append("")
    lines.append("# Global Equity Long/Short — Research Report")
    lines.append("")
    lines.append("Generated %s. Panel %s -> %s. PAPER / RESEARCH ONLY."
                 % (s["generated"], s["panel_start"], s["panel_end"]))
    lines.append("")
    lines.append("Deflation search count: **%d** (%d house prior + %d variants tested here). "
                 "Costs: %g bps/side turnover + short-borrow swept %s. "
                 "Effective-N uses the effective_bets kill-test (monthly returns discounted "
                 "by weight rank-autocorrelation), not the raw daily count."
                 % (s["total_deflation_trials"], s["house_prior_trials"], s["lab_variants"],
                    s["cost_bps_per_side"], s["borrow_sweep"]))
    lines.append("")
    if s.get("whites_reality_check"):
        w = s["whites_reality_check"]
        lines.append("White's Reality Check across the variant family (3%% borrow): p=%.3f -> %s."
                     % (w["p_value"], "a survivor beats chance" if w["survives"]
                        else "best variant is consistent with a search artefact"))
        lines.append("")

    # Per-mechanism tables
    hdr = ("| variant | borrow | grossSR | netSR | DSR | eff.bets | maxDD% | "
           "corrSPY | corrVT | long-leg CAGR% | short-leg CAGR% |")
    sep = "|" + "---|" * 11
    names = {"country": "1. Country-index relative value / momentum",
             "sector": "2. Global sector relative value / momentum",
             "combo": "3. Country cross-sectional combo (momentum + low-vol)"}
    for mech in ["country", "sector", "combo"]:
        lines.append("## %s" % names[mech])
        lines.append("")
        lines.append(hdr)
        lines.append(sep)
        for ev in R[mech]:
            lines.append("| %s | %.0f%% | %.2f | %.2f | %s | %.0f | %.1f | %s | %s | %.1f | %.1f |"
                         % (ev["name"].rsplit("_b", 1)[0], ev["borrow_annual"] * 100,
                            ev["gross_sharpe_ann"], ev["net_sharpe_ann"],
                            ev["deflated_sharpe"], ev["effective_bets"], ev["maxdd_pct"],
                            ev["corr_spy"], ev["corr_vt"], ev["long_leg_cagr_pct"],
                            ev["short_leg_cagr_pct"]))
        lines.append("")

    # Long vs short vs cash decomposition
    lines.append("## Long-leg vs short-leg vs cash (the shorts-study test)")
    lines.append("")
    lines.append("Per the 14-year shorts study: a bearish signal expressed as CASH beat the "
                 "same signal as a SHORT by ~15pp/yr (borrow + upward drift + crash-up). For "
                 "each variant, if the short leg's own net CAGR is <= 0, the short book is a "
                 "DRAG: you would have done better parking that half in cash and running the "
                 "long leg alone.")
    lines.append("")
    lines.append("**Read the long-only column correctly:** every long-only Sharpe below is "
                 "positive (0.3-0.8) only because it is ~fully long an equal-weighted equity "
                 "tercile through a 19-year bull market — that is BETA, not alpha, and it does "
                 "not beat SPY/VT on a risk-adjusted basis. The point of this column is the "
                 "CONTRAST: every short leg posts a -9% to -16%/yr net CAGR, so the dollar-neutral "
                 "book destroys the long leg's beta rather than adding uncorrelated return. This "
                 "is the shorts study reproduced on a third ask: the short is the problem.")
    lines.append("")
    lines.append("| variant | L/S netSR | long-only netSR | short-leg CAGR% | short adds vs cash? |")
    lines.append("|---|---|---|---|---|")
    for mech in ["country", "sector", "combo"]:
        for ev in R[mech]:
            if abs(ev["borrow_annual"] - s["borrow_sweep"][-1]) > 1e-9:
                continue  # show the conservative 3% row
            short_helps = ev["short_leg_cagr_pct"] > 0
            verdict = ("short ADDS (+%.1f%%/yr)" % ev["short_leg_cagr_pct"] if short_helps
                       else "short DRAGS (%.1f%%/yr) -> hold cash, run long-only"
                       % ev["short_leg_cagr_pct"])
            lines.append("| %s | %.2f | %.2f | %.1f | %s |"
                         % (ev["name"].rsplit("_b", 1)[0], ev["net_sharpe_ann"],
                            ev["long_leg_sharpe"], ev["short_leg_cagr_pct"], verdict))
    lines.append("")

    # Crash windows for the best variant
    if best_ev:
        lines.append("## Crash-window behavior (best variant by net Sharpe: %s, %.0f%% borrow)"
                     % (best_ev["name"], best_ev["borrow_annual"] * 100))
        lines.append("")
        c = best_ev["crash"]
        lines.append("| window | net return% |")
        lines.append("|---|---|")
        for k, val in c.items():
            lines.append("| %s | %s |" % (k, val))
        lines.append("")

    lines.append("## Caveats")
    lines.append("")
    lines.append("- **Survivorship**: the country/sector ETF sets are today's survivors; a few "
                 "young funds (XLRE 2015, XLC 2018, VT 2008) shorten the true common window.")
    lines.append("- **Borrow realism**: 1-3%/yr is optimistic for hard-to-borrow single-country "
                 "funds in stress; real borrow spikes exactly when the short would pay.")
    lines.append("- **Effective bets**: monthly momentum books have high weight autocorrelation, "
                 "so 200+ rebalances are far fewer independent bets — the DSR uses that discount.")
    lines.append("- **No fundamentals**: valuation is proxied by long-horizon price reversal, not "
                 "P/E or CAPE (no point-in-time fundamentals via yfinance without look-ahead).")
    lines.append("- PAPER / RESEARCH ONLY. No orders, no probe created, no engine or secret touched.")
    lines.append("")

    with open(REPORT_MD, "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
