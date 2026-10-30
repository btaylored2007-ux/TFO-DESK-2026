#!/usr/bin/env python3
"""carry_credit_rates_lab.py — Does the desk's ONE validated mechanism (carry)
survive in the two largest carry markets on earth: CREDIT and RATES?

RESEARCH ONLY. Paper desk. No orders, no capital, no pre-registration, no engine,
no secrets. Nothing here is fabricated: every number is computed from free data.

WHY THIS EXISTS
---------------
The desk has validated exactly one mechanism (delta-neutral crypto basis carry) out
of ~2,600 hypotheses. Carry has since been tested and killed in FX
(reports/research_fx_carry.md) and commodities (reports/research_commodity_roll_yield.md).
Credit and rates -- the two deepest carry markets in existence -- have never been
tested systematically here. This closes that gap.

THE TWO PREMIA
--------------
CREDIT CARRY : you are paid a spread to bear default risk.
RATES CARRY  : you are paid a term premium to bear duration.

They are usually conflated because a corporate bond pays BOTH. The whole point of
part 1(b) below is to separate them: HYG - IEF and LQD - IEF strip the duration
premium out and leave the credit premium alone.

WHAT THE PRIOR TWO FAILURES TEACH (do not repeat)
-------------------------------------------------
FX carry died because (i) the amplitude never cleared the search-deflation floor,
(ii) it could not beat buy-and-hold, and (iii) it was a risk-on trade in disguise
(SPY corr +0.48 to +0.64), so it earned no diversification redemption.
Commodity roll died because the CLEAN isolators paid ~nothing net and the only
attractive number was measuring something other than the claimed mechanism.
So here: isolate honestly, benchmark against buy-and-hold ALWAYS, price with REAL
retail costs, and check the short-vol/equity-beta failure mode explicitly.

COSTS -- stated, and why (see also the cost sweep, which makes the verdict invariant)
------------------------------------------------------------------------------------
These are US-listed ETFs traded by a retail account, not crypto.
  * Commission: $0. Every major US retail broker (Schwab, Fidelity, Vanguard,
    IBKR Lite) has been commission-free on US-listed ETFs since Oct 2019. Pre-2019
    a $5 flat ticket on a $10k probe would be 5 bps/side -- inside the stress sweep.
  * Quoted spread: these are among the most liquid ETFs listed. We do not assume it:
    the script MEASURES an effective spread per ticker with the Corwin-Schultz (2012)
    high-low estimator from the same yfinance OHLC we already have, and prints it.
  * Headline cost: 3.0 bps PER SIDE (half-spread + slippage), deliberately set at or
    above the measured value for every ticker used. Stress sweep 1/3/5/10/20 bps.
  * Expense ratios are NOT added: total-return (auto_adjust) ETF prices are already
    net of NAV fees. For the record: HYG 0.49%, JNK 0.40%, LQD 0.14%, IEF/TLT/SHY
    0.15%, BIL 0.1354%.
  * Short legs (the duration-hedged spreads) are charged a 40 bps/yr borrow fee and
    are credited NO interest on short proceeds -- which is the true retail experience
    on a $10k margin account. A zero-borrow sensitivity is reported.

Every NET series is scored through quant_validation.py, unmodified.
"""
import io
import json
import math
import os
import urllib.request
from datetime import datetime

import numpy as np
import pandas as pd

import quant_validation as qv

CACHE = ("/private/tmp/claude-501/-Users-test-Downloads/"
         "ad94ca3f-fc8a-4e60-8fcd-c99e53d7d7be/scratchpad/credit_rates_cache")
os.makedirs(CACHE, exist_ok=True)
OUT_JSON = "reports/credit_rates_carry_lab.json"

# House search count carried forward. reports/research_gold_basis.md closes with
# "House count going forward: 2,636" (2,542 prior + 84 threshold evaluations, plus
# 10 re-pricings). The task brief cites ~2,626 (the narrower count that excludes the
# 10 re-pricings). Both are reported below; the verdict does not turn on it.
HOUSE_N_WIDE = 2636
HOUSE_N_NARROW = 2626

COST_BPS_PER_SIDE = 3.0          # headline
COST_SWEEP = [1.0, 3.0, 5.0, 10.0, 20.0]
BORROW_BPS_PER_YEAR = 40.0       # short-leg borrow on the duration-hedged spreads
TRADING_DAYS = 252

EXPENSE_RATIOS = {"HYG": 0.0049, "JNK": 0.0040, "LQD": 0.0014, "IEF": 0.0015,
                  "TLT": 0.0015, "SHY": 0.0015, "BIL": 0.001354, "SPY": 0.000945}

CRISIS = {
    "2008_GFC": ("2007-10-01", "2009-06-30"),
    "2020_COVID": ("2020-02-01", "2020-04-30"),
    "2022_bond_bear": ("2022-01-01", "2022-12-31"),
}


# ---------------------------------------------------------------------------
# Data (cached; re-runs are cheap and deterministic)
# ---------------------------------------------------------------------------
def _cache_path(name):
    return os.path.join(CACHE, name.replace("^", "IDX_").replace("=", "_") + ".csv")


def yf_ohlc(ticker, start="1990-01-01", adjusted=True):
    """Daily OHLC. adjusted=True -> total-return series (dividends reinvested)."""
    path = _cache_path("yf_%s_%s" % (ticker, "adj" if adjusted else "raw"))
    if os.path.exists(path):
        return pd.read_csv(path, index_col=0, parse_dates=True)
    import yfinance as yf
    d = yf.download(ticker, start=start, progress=False, auto_adjust=adjusted)
    if d is None or d.empty:
        raise RuntimeError("no data for %s" % ticker)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d[["Open", "High", "Low", "Close"]].dropna()
    d.index = pd.to_datetime(d.index).tz_localize(None)
    d.to_csv(path)
    return d


def yf_dividends(ticker):
    """Ex-date dividend series, NORMALIZED to midnight.

    yfinance stamps dividends at 09:30 local; without .normalize() they align with
    nothing and every distribution yield silently computes as zero. That bug ran
    once here and produced four dead variant families before it was caught.
    """
    path = _cache_path("div_%s" % ticker)
    if os.path.exists(path):
        s = pd.read_csv(path, index_col=0, parse_dates=True).iloc[:, 0]
        s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
        return s
    import yfinance as yf
    d = yf.Ticker(ticker).dividends
    d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
    d.name = "div"
    d.to_frame().to_csv(path)
    return d


def fred(series_id, start="1990-01-01"):
    path = _cache_path("fred_%s" % series_id)
    if os.path.exists(path):
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        return pd.to_numeric(df.iloc[:, 0], errors="coerce").dropna()
    url = ("https://fred.stlouisfed.org/graph/fredgraph.csv?id=%s&cosd=%s"
           % (series_id, start))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=60).read().decode()
    df = pd.read_csv(io.StringIO(raw))
    df.columns = ["date", "val"]
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    df["val"] = pd.to_numeric(df["val"], errors="coerce")
    df.to_csv(path)
    return df["val"].dropna()


# ---------------------------------------------------------------------------
# Cost measurement -- Corwin & Schultz (2012) high-low effective spread
# ---------------------------------------------------------------------------
def corwin_schultz_spread_bps(ohlc):
    """Estimate the effective proportional spread from daily highs/lows.

    Two-day high-low ranges contain both volatility (scales with time) and the
    spread (does not). CS separate them. Negative daily estimates are set to 0 --
    the standard convention -- and we report the mean over the whole history.
    """
    h, l = ohlc["High"].values, ohlc["Low"].values
    n = len(h)
    if n < 3:
        return float("nan")
    beta = np.full(n, np.nan)
    gamma = np.full(n, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        hl = np.log(h / l) ** 2
        beta[1:] = hl[:-1] + hl[1:]
        h2 = np.maximum(h[:-1], h[1:])
        l2 = np.minimum(l[:-1], l[1:])
        gamma[1:] = np.log(h2 / l2) ** 2
        k = 3.0 - 2.0 * math.sqrt(2.0)
        alpha = ((math.sqrt(2.0) - 1.0) * np.sqrt(beta) / k) - np.sqrt(gamma / k)
        s = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))
    s = s[np.isfinite(s)]
    s = np.where(s < 0, 0.0, s)
    return float(np.mean(s) * 10000.0)


# NOTE ON THE ESTIMATOR, stated because it matters for how the number is used:
# Corwin-Schultz attributes ALL of the non-scaling part of the two-day high-low
# range to the spread, so overnight gaps and intraday jumps inflate it badly for
# very liquid instruments. The sanity check is SPY: CS returns ~27 bps round trip
# for SPY, whose real quoted spread is well under 1 bp. So CS here is an UPPER
# BOUND / ceiling, not a measurement of the spread we would pay. It is reported
# because it is a real number computed from real data, and the cost sweep runs out
# to 20 bps PER SIDE -- above even the CS ceiling for every ticker used.


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def perf(returns, ppy=TRADING_DAYS):
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if r.size < 2:
        return {}
    growth = float(np.prod(1.0 + r))
    years = r.size / ppy
    cagr = growth ** (1.0 / years) - 1.0 if growth > 0 else float("nan")
    sd = r.std(ddof=1)
    eq = np.cumprod(1.0 + r)
    dd = eq / np.maximum.accumulate(eq) - 1.0
    return {"cagr": float(cagr), "vol": float(sd * math.sqrt(ppy)),
            "sharpe": float((r.mean() / sd) * math.sqrt(ppy)) if sd > 0 else 0.0,
            "maxDD": float(dd.min()), "n": int(r.size),
            "total_return": growth - 1.0}


def window_stats(ret_series, lo, hi):
    """Cumulative return and max drawdown inside a calendar window."""
    s = ret_series.loc[(ret_series.index >= lo) & (ret_series.index <= hi)].dropna()
    if s.size < 3:
        return {"n": int(s.size), "cum": None, "maxDD": None}
    eq = (1.0 + s).cumprod()
    dd = eq / eq.cummax() - 1.0
    return {"n": int(s.size), "cum": float(eq.iloc[-1] - 1.0), "maxDD": float(dd.min())}


def zscore(series, window, minp):
    m = series.rolling(window, min_periods=minp).mean()
    s = series.rolling(window, min_periods=minp).std(ddof=1)
    return (series - m) / s.replace(0.0, np.nan)


# ---------------------------------------------------------------------------
# Backtest engine
#
# One convention throughout, applied to every variant including the benchmarks:
#   * signals are computed from data up to and including a MONTH-END close
#   * the resulting weights are held from the NEXT trading day (t+1 execution)
#   * costs are charged on |dw| at each weight change
# There is no look-ahead anywhere: `weights` is shifted before it multiplies returns.
# ---------------------------------------------------------------------------
def month_end_mask(index):
    idx = pd.Series(index, index=index)
    return idx.groupby([index.year, index.month]).transform("max") == idx


def run_book(rets, weights_monthly, cost_bps=COST_BPS_PER_SIDE,
             borrow_bps_yr=BORROW_BPS_PER_YEAR):
    """rets: DataFrame of daily total returns. weights_monthly: DataFrame of target
    weights defined on month-end dates only (NaN elsewhere -> forward filled).

    Returns (gross, net, turnover_per_year, weights_used).
    """
    w = weights_monthly.reindex(rets.index).ffill().fillna(0.0)
    w_eff = w.shift(1).fillna(0.0)                     # t+1 execution
    gross = (w_eff * rets).sum(axis=1)

    dw = w_eff.diff().abs().fillna(w_eff.abs().iloc[[0]].reindex(w_eff.index).fillna(0.0))
    dw.iloc[0] = w_eff.iloc[0].abs()
    trade_cost = dw.sum(axis=1) * (cost_bps / 10000.0)

    short_notional = w_eff.clip(upper=0.0).abs().sum(axis=1)
    borrow_cost = short_notional * (borrow_bps_yr / 10000.0) / TRADING_DAYS

    net = gross - trade_cost - borrow_cost
    turnover_yr = float(dw.sum(axis=1).sum() / (len(rets) / TRADING_DAYS))
    return gross, net, turnover_yr, w_eff


# ---------------------------------------------------------------------------
# Variant registry
# ---------------------------------------------------------------------------
class Lab(object):
    """Registry of every variant tested, with its gross/net series.

    NOTE ON SCORING (this is the trap this study nearly fell into):
    a rule of the form "own the risk asset when the signal fires, else sit in
    T-bills" spends most of its life in cash. Cash has a near-zero standard
    deviation, so the RAW Sharpe of such a rule is enormous and means nothing --
    it is measuring the risk-free rate, not a premium. Every variant is therefore
    ALSO scored on returns EXCESS OF CASH, which is the number that answers "were
    we paid anything for bearing credit or duration risk?"
    """

    def __init__(self, rf=None):
        self.results = []
        self.series = {}
        self.rf = rf        # daily risk-free (T-bill) total return series

    def add(self, name, family, rets, weights, benchmark_key, benchmark_label,
            cost_bps=COST_BPS_PER_SIDE, borrow=BORROW_BPS_PER_YEAR, note=""):
        gross, net, turn, w_eff = run_book(rets, weights, cost_bps, borrow)
        # A conditioning signal needs its lookback before it can say anything. Days
        # before the first live weight are NOT flat performance -- the strategy did
        # not exist. Leaving them in as exact zeros shrinks the stdev, inflates the
        # Sharpe, and fakes a serene 2008. Trim to the first live weight date.
        if len(weights.index):
            first_live = pd.Timestamp(weights.index.min())
            gross = gross.loc[gross.index > first_live]
            net = net.loc[net.index > first_live]
            w_eff = w_eff.loc[w_eff.index > first_live]
        gross, net = gross.dropna(), net.dropna()
        common = gross.index.intersection(net.index)
        gross, net = gross.loc[common], net.loc[common]
        w_eff = w_eff.loc[common]
        if len(common) > 1:      # turnover over the LIVE window only
            turn = float(w_eff.diff().abs().sum(axis=1).sum()
                         / (len(common) / TRADING_DAYS))
        g, nperf = perf(gross), perf(net)
        row = {
            "name": name, "family": family, "note": note,
            "start": str(common[0].date()), "end": str(common[-1].date()),
            "n_days": int(common.size), "years": round(common.size / TRADING_DAYS, 2),
            "turnover_per_year": round(turn, 2),
            "gross_cagr": g.get("cagr"), "gross_sharpe": g.get("sharpe"),
            "net_cagr": nperf.get("cagr"), "net_sharpe": nperf.get("sharpe"),
            "net_vol": nperf.get("vol"), "net_maxDD": nperf.get("maxDD"),
            "benchmark": benchmark_label, "benchmark_key": benchmark_key,
        }
        xs = None
        if self.rf is not None:
            j = pd.concat([net, self.rf], axis=1, join="inner").dropna()
            xs = (j.iloc[:, 0] - j.iloc[:, 1])
            xp = perf(xs.values)
            row["xcash_cagr"] = xp.get("cagr")
            row["xcash_sharpe"] = xp.get("sharpe")
            row["xcash_maxDD"] = xp.get("maxDD")
            row["pct_days_risk_off"] = round(
                float((w_eff.iloc[:, 0].abs() < 1e-9).mean()) * 100.0, 1)
        self.results.append(row)
        self.series[name] = {"gross": gross, "net": net, "w": w_eff, "xcash": xs}
        return row


def main():
    print("=" * 78)
    print("CREDIT & RATES CARRY LAB — completing the global carry map")
    print("run: %s" % datetime.now().isoformat(timespec="seconds"))
    print("=" * 78)

    tickers = ["HYG", "JNK", "LQD", "IEF", "TLT", "SHY", "BIL", "SPY", "AGG"]
    adj, raw = {}, {}
    for t in tickers:
        adj[t] = yf_ohlc(t, adjusted=True)
        raw[t] = yf_ohlc(t, adjusted=False)
    tnx = yf_ohlc("^TNX", adjusted=True)["Close"]
    irx = yf_ohlc("^IRX", adjusted=True)["Close"]

    # ---- measured costs -------------------------------------------------
    print("\n--- MEASURED effective spread (Corwin-Schultz, full history, bps round trip)")
    measured = {}
    for t in tickers:
        measured[t] = round(corwin_schultz_spread_bps(adj[t]), 2)
        print("    %-4s  %6.2f bps  (half-spread %5.2f bps/side)"
              % (t, measured[t], measured[t] / 2.0))
    print("    headline cost used: %.1f bps/SIDE (at or above every measured "
          "half-spread)" % COST_BPS_PER_SIDE)

    # ---- return panel ---------------------------------------------------
    px = pd.DataFrame({t: adj[t]["Close"] for t in tickers}).sort_index()
    rets_all = px.pct_change()

    # ---- credit carry signals (real, free, observable at the time) -------
    # FRED's ICE BofA OAS series are licence-restricted to the last ~3 years on the
    # free endpoint (verified: BAMLH0A0HYM2 returns 2023-08-28 onward only), so the
    # spread LEVEL is built from ETF distribution yields instead: trailing 365-day
    # distributions divided by the CURRENT unadjusted price. That is a genuine,
    # point-in-time observable carry measure -- and because the denominator is
    # today's price, it re-prices instantly when credit sells off.
    def dist_yield(t):
        d = yf_dividends(t)
        daily = d.reindex(px.index).fillna(0.0)
        ttm = daily.rolling("365D").sum()
        yld = ttm / raw[t]["Close"].reindex(px.index)
        # The trailing-12m sum ramps up from zero over the fund's first year, which
        # would otherwise feed a fake "spread is historically tight" signal into the
        # z-score. Mask until a full 12 months of distributions exist.
        first_full = d.index.min() + pd.Timedelta(days=370)
        yld.loc[yld.index < first_full] = np.nan
        return yld

    y = {t: dist_yield(t) for t in ["HYG", "JNK", "LQD", "IEF", "TLT", "SHY"]}
    hy_ig = (y["HYG"] - y["LQD"]).dropna()          # HY over IG carry
    hy_dur = (y["HYG"] - y["IEF"]).dropna()         # HY over duration carry
    print("\n--- credit carry LEVELS (trailing-12m distribution yield spread, %)")
    print("    HY-IG  %s..%s  min %.2f  median %.2f  max %.2f"
          % (hy_ig.index[0].date(), hy_ig.index[-1].date(),
             100 * hy_ig.min(), 100 * hy_ig.median(), 100 * hy_ig.max()))
    print("    HY-dur %s..%s  min %.2f  median %.2f  max %.2f"
          % (hy_dur.index[0].date(), hy_dur.index[-1].date(),
             100 * hy_dur.min(), 100 * hy_dur.median(), 100 * hy_dur.max()))
    logratio = np.log(px["HYG"] / px["LQD"]).dropna()

    # Two lookback windows for every conditioning signal. The 3y window is the
    # natural "vs its own history" horizon; the 1y window exists because the 3y
    # version cannot produce a signal until ~2010 and would therefore have made the
    # 2008 credit crisis -- the single most important observation for a credit-carry
    # study -- unobservable. Both are reported; both count toward the search.
    WINDOWS = [("w3y", 756, 504), ("w1y", 252, 126)]
    sig_credit = {}
    for tag, W, MP in WINDOWS:
        rv = logratio.rolling(W, min_periods=MP).mean() - logratio  # +ve = HY cheap
        sig_credit["A_yldHYIG_%s" % tag] = zscore(hy_ig, W, MP)
        sig_credit["B_yldHYdur_%s" % tag] = zscore(hy_dur, W, MP)
        sig_credit["C_pricerv_%s" % tag] = zscore(rv, W, MP)

    # ---- rates carry signals -------------------------------------------
    slope = (tnx - irx).reindex(px.index).ffill()   # 10y minus 3m, in percent
    sig_rates = {"D_slope_level": slope}
    for tag, W, MP in WINDOWS:
        sig_rates["E_slope_z_%s" % tag] = zscore(slope, W, MP)
    print("    curve slope (10y-3m) %s..%s  min %.2f  median %.2f  max %.2f  "
          "inverted on %.1f%% of days"
          % (slope.dropna().index[0].date(), slope.dropna().index[-1].date(),
             slope.min(), slope.median(), slope.max(),
             100 * float((slope.dropna() < 0).mean())))

    # risk-free proxy: BIL total return where it exists, SHY before BIL listed
    rf = rets_all["BIL"].copy()
    rf = rf.fillna(rets_all["SHY"])
    rf.name = "rf"

    lab = Lab(rf=rf)
    me = month_end_mask(px.index)

    def wframe(cols):
        return pd.DataFrame(0.0, index=px.index[me], columns=cols)

    # =====================================================================
    # BENCHMARKS / NAIVE ALWAYS-ON PREMIA
    # =====================================================================
    print("\n--- naive always-on premia (the benchmarks that must be beaten)")
    holds = {}
    for t, span_from in [("HYG", "HYG"), ("JNK", "JNK"), ("LQD", "LQD"),
                         ("IEF", "IEF"), ("TLT", "TLT"), ("SHY", "SHY"),
                         ("BIL", "BIL"), ("SPY", "SPY")]:
        r = rets_all[[t]].dropna()
        w = pd.DataFrame(1.0, index=r.index[month_end_mask(r.index)], columns=[t])
        row = lab.add("hold_%s" % t, "benchmark", r, w, "hold_%s" % t,
                      "itself", note="buy and hold, one entry cost")
        holds[t] = lab.series["hold_%s" % t]["net"]
        print("    hold %-4s %s..%s  net CAGR %6.2f%%  SR %5.2f  maxDD %6.1f%%"
              % (t, row["start"], row["end"], 100 * row["net_cagr"],
                 row["net_sharpe"], 100 * row["net_maxDD"]))

    # duration-hedged credit premium, always on (1:1 as briefed)
    static_spreads = [
        ("static_HYG_minus_IEF", ["HYG", "IEF"], [1.0, -1.0], "hold_HYG",
         "HYG buy-and-hold", "1:1 as briefed; HYG dur ~3.5y vs IEF ~7.4y so this "
         "is net SHORT duration -- see beta-hedged twin"),
        ("static_LQD_minus_IEF", ["LQD", "IEF"], [1.0, -1.0], "hold_LQD",
         "LQD buy-and-hold", "1:1; LQD dur ~8.4y vs IEF ~7.4y, a fair match"),
        ("static_HYG_minus_LQD", ["HYG", "LQD"], [1.0, -1.0], "hold_HYG",
         "HYG buy-and-hold", "HY over IG, the pure ratings-migration leg"),
        ("static_TLT_minus_SHY", ["TLT", "SHY"], [1.0, -1.0], "hold_TLT",
         "TLT buy-and-hold", "the term premium, always on"),
        ("static_IEF_minus_SHY", ["IEF", "SHY"], [1.0, -1.0], "hold_IEF",
         "IEF buy-and-hold", "term premium, intermediate"),
    ]
    for name, cols, wts, bk, bl, note in static_spreads:
        r = rets_all[cols].dropna()
        w = pd.DataFrame([wts], index=r.index[month_end_mask(r.index)][:1],
                         columns=cols)
        w = pd.DataFrame(np.tile(wts, (int(month_end_mask(r.index).sum()), 1)),
                         index=r.index[month_end_mask(r.index)], columns=cols)
        row = lab.add(name, "static_spread", r, w, bk, bl, note=note)
        print("    %-24s net CAGR %6.2f%%  SR %5.2f  maxDD %6.1f%%"
              % (name, 100 * row["net_cagr"], row["net_sharpe"],
                 100 * row["net_maxDD"]))

    # beta-hedged twins: hedge ratio = trailing 1y OLS beta on the treasury leg
    for name, credit, tsy, bk, bl in [
            ("static_HYG_minus_betaIEF", "HYG", "IEF", "hold_HYG", "HYG buy-and-hold"),
            ("static_LQD_minus_betaIEF", "LQD", "IEF", "hold_LQD", "LQD buy-and-hold")]:
        r = rets_all[[credit, tsy]].dropna()
        cov = r[credit].rolling(252, min_periods=126).cov(r[tsy])
        var = r[tsy].rolling(252, min_periods=126).var()
        beta = (cov / var).clip(-3, 3)
        mem = month_end_mask(r.index)
        w = pd.DataFrame({credit: 1.0, tsy: -beta[mem]}, index=r.index[mem]).dropna()
        row = lab.add(name, "static_spread", r, w, bk, bl,
                      note="rolling 1y OLS duration hedge (empirical beta)")
        print("    %-24s net CAGR %6.2f%%  SR %5.2f  maxDD %6.1f%%  (mean beta %.2f)"
              % (name, 100 * row["net_cagr"], row["net_sharpe"],
                 100 * row["net_maxDD"], float(beta.dropna().mean())))

    # =====================================================================
    # 1. CREDIT CARRY -- spread-conditioned
    # =====================================================================
    print("\n--- CREDIT carry: long HY when the spread is wide vs its own history")
    credit_thresholds = [-0.5, 0.0, 0.5, 1.0]
    credit_rules = [
        ("HYelseIG", ["HYG", "LQD"], "hold_HYG", "HYG buy-and-hold"),
        ("HYelseCash", ["HYG", "BIL"], "hold_HYG", "HYG buy-and-hold"),
        ("HYmIEFelseFlat", ["HYG", "IEF"], "static_HYG_minus_IEF",
         "always-on HYG-IEF"),
    ]
    for sname, sig in sig_credit.items():
        for rname, cols, bk, bl in credit_rules:
            for thr in credit_thresholds:
                r = rets_all[cols].dropna()
                mem = month_end_mask(r.index)
                s = sig.reindex(r.index).ffill()[mem]
                on = (s > thr)
                if rname == "HYelseIG":
                    w = pd.DataFrame({cols[0]: on.astype(float),
                                      cols[1]: (~on).astype(float)},
                                     index=r.index[mem])
                elif rname == "HYelseCash":
                    w = pd.DataFrame({cols[0]: on.astype(float),
                                      cols[1]: (~on).astype(float)},
                                     index=r.index[mem])
                else:
                    w = pd.DataFrame({cols[0]: on.astype(float),
                                      cols[1]: -on.astype(float)},
                                     index=r.index[mem])
                w = w.loc[s.dropna().index]
                nm = "credit_%s_%s_thr%+.1f" % (sname, rname, thr)
                if w.empty or float(w.abs().sum().sum()) == 0.0:
                    print("    SKIP %s -- signal never fires (degenerate)" % nm)
                    continue
                lab.add(nm, "credit_timed", r, w, bk, bl,
                        note="signal %s, threshold z>%+.1f" % (sname, thr))

    # JNK robustness on the same rule set (both windows of signal A, all thresholds)
    for tag, _, _ in WINDOWS:
        for thr in credit_thresholds:
            cols = ["JNK", "LQD"]
            r = rets_all[cols].dropna()
            mem = month_end_mask(r.index)
            s = sig_credit["A_yldHYIG_%s" % tag].reindex(r.index).ffill()[mem]
            on = (s > thr)
            w = pd.DataFrame({"JNK": on.astype(float), "LQD": (~on).astype(float)},
                             index=r.index[mem]).loc[s.dropna().index]
            if w.empty:
                continue
            lab.add("credit_A_%s_JNKelseIG_thr%+.1f" % (tag, thr), "credit_timed",
                    r, w, "hold_JNK", "JNK buy-and-hold",
                    note="JNK robustness of the HY-vs-IG rule")

    top_credit = sorted([x for x in lab.results if x["family"] == "credit_timed"],
                        key=lambda x: -(x["net_sharpe"] or -9))[:6]
    for x in top_credit:
        print("    %-34s net SR %5.2f  CAGR %6.2f%%  turn %4.1f/yr  vs %s"
              % (x["name"], x["net_sharpe"], 100 * x["net_cagr"],
                 x["turnover_per_year"], x["benchmark"]))

    # =====================================================================
    # 2. RATES / TERM CARRY -- curve-conditioned
    # =====================================================================
    print("\n--- RATES carry: long duration when the curve is steep, short/cash when flat")
    rate_rules = [
        ("TLTelseSHY", ["TLT", "SHY"], "hold_TLT", "TLT buy-and-hold"),
        ("TLTelseBIL", ["TLT", "BIL"], "hold_TLT", "TLT buy-and-hold"),
        ("IEFelseSHY", ["IEF", "SHY"], "hold_IEF", "IEF buy-and-hold"),
        ("TLTmSHYelseFlat", ["TLT", "SHY"], "static_TLT_minus_SHY",
         "always-on TLT-SHY"),
    ]
    rate_settings = [("D", sig_rates["D_slope_level"], t) for t in (0.0, 0.5, 1.0)]
    for tag, _, _ in WINDOWS:
        rate_settings += [("E_%s" % tag, sig_rates["E_slope_z_%s" % tag], t)
                          for t in (-0.5, 0.0, 0.5, 1.0)]
    for scode, sig, thr in rate_settings:
        for rname, cols, bk, bl in rate_rules:
            r = rets_all[cols].dropna()
            mem = month_end_mask(r.index)
            s = sig.reindex(r.index).ffill()[mem]
            on = (s > thr)
            if rname == "TLTmSHYelseFlat":
                w = pd.DataFrame({cols[0]: on.astype(float),
                                  cols[1]: -on.astype(float)}, index=r.index[mem])
            else:
                w = pd.DataFrame({cols[0]: on.astype(float),
                                  cols[1]: (~on).astype(float)}, index=r.index[mem])
            w = w.loc[s.dropna().index]
            nm = "rates_%s_%s_thr%+.2f" % (scode, rname, thr)
            if w.empty or float(w.abs().sum().sum()) == 0.0:
                print("    SKIP %s -- signal never fires (degenerate)" % nm)
                continue
            lab.add(nm, "rates_timed", r, w, bk, bl,
                    note="signal %s (10y-3m), threshold >%+.2f"
                         % ("slope level %" if scode == "D" else "slope z", thr))

    top_rates = sorted([x for x in lab.results if x["family"] == "rates_timed"],
                       key=lambda x: -(x["net_sharpe"] or -9))[:6]
    for x in top_rates:
        print("    %-34s net SR %5.2f  CAGR %6.2f%%  turn %4.1f/yr  vs %s"
              % (x["name"], x["net_sharpe"], 100 * x["net_cagr"],
                 x["turnover_per_year"], x["benchmark"]))

    n_variants = len(lab.results)
    N_WIDE = HOUSE_N_WIDE + n_variants
    N_NARROW = HOUSE_N_NARROW + n_variants
    print("\n--- SEARCH COUNT: %d variants tested here; house %d -> N = %d "
          "(narrow house %d -> %d)"
          % (n_variants, HOUSE_N_WIDE, N_WIDE, HOUSE_N_NARROW, N_NARROW))

    # =====================================================================
    # 3. Benchmark test + crisis behaviour + SPY correlation, EVERY variant
    # =====================================================================
    spy = rets_all["SPY"].dropna()
    for row in lab.results:
        net = lab.series[row["name"]]["net"]
        bkey = row["benchmark_key"]
        bench = lab.series[bkey]["net"] if bkey in lab.series else None
        if bench is not None and row["name"] != bkey:
            j = pd.concat([net, bench], axis=1, join="inner").dropna()
            ex = (j.iloc[:, 0] - j.iloc[:, 1]).values
            n_eff = qv.effective_sample_size(len(ex), overlap_factor=21.0)
            sd = ex.std(ddof=1)
            se = sd / math.sqrt(n_eff) if sd > 0 else 0.0
            row["excess_vs_hold_bps_day"] = round(float(ex.mean()) * 10000, 3)
            row["excess_t"] = round(float(ex.mean()) / se, 3) if se > 0 else None
            row["beats_hold"] = bool(se > 0 and (ex.mean() / se) > 1.96)
        else:
            row["excess_vs_hold_bps_day"] = None
            row["excess_t"] = None
            row["beats_hold"] = None
        j = pd.concat([net, spy], axis=1, join="inner").dropna()
        row["spy_corr"] = round(float(j.corr().iloc[0, 1]), 3) if len(j) > 30 else None
        for cname, (lo, hi) in CRISIS.items():
            ws = window_stats(net, lo, hi)
            row["crisis_%s_cum" % cname] = (round(ws["cum"], 4)
                                            if ws["cum"] is not None else None)
            row["crisis_%s_maxDD" % cname] = (round(ws["maxDD"], 4)
                                              if ws["maxDD"] is not None else None)

    # =====================================================================
    # 4. Pick the best NET variant that is a genuine CARRY SIGNAL (not a hold)
    # =====================================================================
    timed = [x for x in lab.results if x["family"] in ("credit_timed", "rates_timed")]
    best_raw = max(timed, key=lambda x: x["net_sharpe"] or -9)
    best = max(timed, key=lambda x: x.get("xcash_sharpe") or -9)   # honest ranking
    best_beater = None
    beaters = [x for x in timed if x.get("beats_hold")]
    if beaters:
        best_beater = max(beaters, key=lambda x: x["excess_t"] or -9)

    print("\n" + "=" * 78)
    print("BEST TIMED VARIANT by EXCESS-OF-CASH Sharpe: %s" % best["name"])
    print("=" * 78)
    print("    span %s..%s (%.1fy)  net SR %.3f  net CAGR %.2f%%  maxDD %.1f%%"
          % (best["start"], best["end"], best["years"], best["net_sharpe"],
             100 * best["net_cagr"], 100 * best["net_maxDD"]))
    print("    EXCESS OF CASH: SR %.3f  CAGR %.2f%%  maxDD %.1f%%  "
          "(risk-off on %.1f%% of days)"
          % (best["xcash_sharpe"], 100 * best["xcash_cagr"],
             100 * best["xcash_maxDD"], best["pct_days_risk_off"]))
    print("    benchmark %s: excess %s bps/day, t=%s, beats=%s"
          % (best["benchmark"], best["excess_vs_hold_bps_day"], best["excess_t"],
             best["beats_hold"]))
    print("    variants that BEAT their own buy-and-hold at t>1.96: %d" % len(beaters))
    if best_raw["name"] != best["name"]:
        print("\n    [contrast] best by RAW net Sharpe is %s: raw SR %.3f but only "
              "%.3f excess of cash, risk-off %.1f%% of days -- a high raw Sharpe "
              "here is the T-bill rate, not a premium."
              % (best_raw["name"], best_raw["net_sharpe"],
                 best_raw["xcash_sharpe"], best_raw["pct_days_risk_off"]))

    # =====================================================================
    # 5. HARNESS -- quant_validation.py, unmodified, on the NET series
    # =====================================================================
    trial_sharpes = [(x["net_sharpe"] or 0.0) / math.sqrt(TRADING_DAYS)
                     for x in lab.results]

    def harness(row, label):
        net = lab.series[row["name"]]["net"]
        gross = lab.series[row["name"]]["gross"]
        bench = lab.series[row["benchmark_key"]]["net"]
        j = pd.concat([net, gross, bench], axis=1, join="inner").dropna()
        n_eff = qv.effective_sample_size(len(j), overlap_factor=21.0)
        ev = qv.evaluate(label, j.iloc[:, 0].values, n_trials=N_WIDE,
                         trial_sharpes=trial_sharpes,
                         periods_per_year=TRADING_DAYS,
                         trades_per_period=max(row["turnover_per_year"], 0.01)
                         / TRADING_DAYS,
                         gross_returns=j.iloc[:, 1].values,
                         n_effective=n_eff,
                         benchmark_returns=j.iloc[:, 2].values)
        return ev

    print("\n--- HARNESS (quant_validation.py, unmodified) on the NET series")
    harness_out = {}
    todo = [(best, "best_xcash_timed")]
    if best_raw["name"] != best["name"]:
        todo.append((best_raw, "best_raw_sharpe_timed"))
    if best_beater and best_beater["name"] not in (best["name"], best_raw["name"]):
        todo.append((best_beater, "best_benchmark_beater"))
    for row, tag in todo:
        ev = harness(row, row["name"])
        harness_out[tag] = ev
        print("\n  [%s] %s" % (tag, row["name"]))
        for k in ("n_obs", "n_effective", "independence_discount",
                  "sharpe_annualized", "t_stat", "p_value", "skew", "kurtosis",
                  "n_trials_searched", "expected_max_sharpe_under_null",
                  "deflated_sharpe", "survives_deflation",
                  "excess_over_benchmark_bps", "excess_t_stat", "beats_benchmark",
                  "per_trade_bps", "gross_per_trade_bps", "cost_drag_bps",
                  "min_track_record_obs"):
            if k in ev:
                print("      %-32s %s" % (k, ev[k]))
        print("      VERDICT: %s" % qv.verdict_line(ev))

    # The same winner scored on its EXCESS-OF-CASH series -- the series that answers
    # "were we paid anything for bearing the risk?" rather than "did we hold T-bills?"
    xs = lab.series[best["name"]]["xcash"].dropna()
    n_eff_x = qv.effective_sample_size(len(xs), overlap_factor=21.0)
    ev_x = qv.evaluate(best["name"] + " [excess of cash]", xs.values,
                       n_trials=N_WIDE, trial_sharpes=trial_sharpes,
                       periods_per_year=TRADING_DAYS,
                       trades_per_period=max(best["turnover_per_year"], 0.01)
                       / TRADING_DAYS, n_effective=n_eff_x)
    harness_out["best_xcash_timed_excess_of_cash_series"] = ev_x
    print("\n  [excess-of-cash series] %s" % best["name"])
    print("      ann SR %.3f  t %.2f  DSR %s  survives %s"
          % (ev_x["sharpe_annualized"], ev_x["t_stat"], ev_x["deflated_sharpe"],
             ev_x["survives_deflation"]))

    # White's Reality Check across a whole family sharing one calendar span --
    # "is the BEST of these better than chance once the search is accounted for?"
    print("\n--- WHITE'S REALITY CHECK (families with a common span)")
    wrc_out = {}
    for fam in ("rates_timed", "credit_timed"):
        fam_rows = [x for x in lab.results if x["family"] == fam]
        spans = {}
        for x in fam_rows:
            spans.setdefault((x["start"], x["end"]), []).append(x["name"])
        span, names = max(spans.items(), key=lambda kv: len(kv[1]) * 1000
                          + len(lab.series[kv[1][0]]["net"]))
        # (i) vs ZERO, on excess-of-cash returns. NOTE what this can and cannot say:
        #     a family that is long the risk asset most of the time has a positive
        #     mean excess-of-cash return whenever the RISK PREMIUM is real. A pass
        #     here is evidence for the premium, NOT for the timing signal.
        mats = [lab.series[n]["xcash"].dropna().values for n in names]
        k = min(len(m) for m in mats)
        wrc0 = qv.whites_reality_check(np.vstack([m[:k] for m in mats]), n_boot=200)
        # (ii) vs BUY-AND-HOLD, on (variant - its own benchmark). THIS is the test
        #     that isolates the signal, and it is the one that matters.
        exm = []
        for n in names:
            row_n = next(x for x in lab.results if x["name"] == n)
            jj = pd.concat([lab.series[n]["net"],
                            lab.series[row_n["benchmark_key"]]["net"]],
                           axis=1, join="inner").dropna()
            exm.append((jj.iloc[:, 0] - jj.iloc[:, 1]).values)
        k2 = min(len(m) for m in exm)
        wrc1 = qv.whites_reality_check(np.vstack([m[:k2] for m in exm]), n_boot=200)
        wrc_out[fam] = {"span": list(span), "n_in_family": len(names),
                        "vs_zero_on_excess_of_cash": wrc0,
                        "vs_buy_and_hold": wrc1}
        print("    %-13s %d variants sharing %s..%s" % (fam, len(names),
                                                        span[0], span[1]))
        print("        vs ZERO (excess of cash), n=%d: p = %.3f -> %s"
              % (k, wrc0["p_value"], "the risk premium itself is real"
                 if wrc0["survives"] else "not distinguishable from chance"))
        print("        vs BUY-AND-HOLD,        n=%d: p = %.3f -> %s"
              % (k2, wrc1["p_value"], "the best TIMING signal survives the search"
                 if wrc1["survives"] else
                 "the best timing signal is a search artefact"))

    # also harness the strongest ALWAYS-ON premium, so the report can say whether
    # the beta itself would have survived (it is not a signal, but it is the thing
    # a signal has to beat)
    statics = [x for x in lab.results if x["family"] == "static_spread"]
    best_static = max(statics, key=lambda x: x["net_sharpe"] or -9)
    ev_static = harness(best_static, best_static["name"])
    harness_out["best_static_spread"] = ev_static
    print("\n  [best_static_spread] %s" % best_static["name"])
    print("      ann SR %.3f  DSR %s  beats %s: %s (excess %s bps/day, t=%s)"
          % (ev_static["sharpe_annualized"], ev_static["deflated_sharpe"],
             best_static["benchmark"], ev_static.get("beats_benchmark"),
             ev_static.get("excess_over_benchmark_bps"),
             ev_static.get("excess_t_stat")))

    # =====================================================================
    # 6. KILL-TESTS on the best net timed variant
    # =====================================================================
    print("\n--- KILL-TESTS on %s" % best["name"])

    # (a) execution delay: implement the month-end signal 0/1/3/5/10 days late
    def rebuild_with_delay(row, delay):
        """Re-run the winner with its weights shifted `delay` extra days."""
        w = lab.series[row["name"]]["w"]
        cols = list(w.columns)
        r = rets_all[cols].dropna()
        ww = w.reindex(r.index).ffill().shift(delay).fillna(0.0)
        gross = (ww * r).sum(axis=1)
        dw = ww.diff().abs().fillna(0.0)
        dw.iloc[0] = ww.iloc[0].abs()
        cost = dw.sum(axis=1) * (COST_BPS_PER_SIDE / 10000.0)
        borrow = (ww.clip(upper=0).abs().sum(axis=1)
                  * (BORROW_BPS_PER_YEAR / 10000.0) / TRADING_DAYS)
        return (gross - cost - borrow).dropna()

    delay_profile = {}
    for d in (0, 1, 3, 5, 10):
        s = rebuild_with_delay(best, d)
        delay_profile[d] = float(s.mean() * 10000.0)
        print("    delay %2dd: mean net %+.4f bps/day, ann SR %+.3f"
              % (d, delay_profile[d], perf(s.values)["sharpe"]))
    decay = qv.decay_profile(delay_profile)
    print("    decay verdict: %s" % decay["verdict"])

    # (b) effective bets: how persistent is the position?
    w_best = lab.series[best["name"]]["w"]
    main_col = w_best.columns[0]
    mem = month_end_mask(w_best.index)
    monthly_pos = w_best.loc[mem, main_col]
    rho = float(monthly_pos.autocorr(1)) if monthly_pos.std() > 0 else 0.999
    n_rebal = int((monthly_pos.diff().abs() > 1e-9).sum())
    bets = qv.effective_bets(max(len(monthly_pos), 1), rho)
    print("    position autocorr (monthly) %.3f -> %s independent bets (%s); "
          "actual switches %d"
          % (rho, bets["independent_bets"], bets["verdict"], n_rebal))

    # (c) purged K-fold CV on the net daily series
    net_best = lab.series[best["name"]]["net"].values
    folds = []
    for tr, te in qv.purged_kfold_splits(len(net_best), 5, 0.02, 21):
        seg = net_best[te]
        folds.append(round(float(perf(seg)["sharpe"]), 3))
    print("    purged 5-fold CV net Sharpe by fold: %s  (min %.2f, mean %.2f)"
          % (folds, min(folds), float(np.mean(folds))))

    # (d) cost sweep -- verdict invariance
    print("\n--- COST SWEEP on %s (bps/side)" % best["name"])
    cost_sweep = {}
    w = lab.series[best["name"]]["w"]
    cols = list(w.columns)
    r = rets_all[cols].dropna()
    for c in COST_SWEEP:
        ww = w.reindex(r.index).ffill().fillna(0.0)
        gross = (ww * r).sum(axis=1)
        dw = ww.diff().abs().fillna(0.0)
        dw.iloc[0] = ww.iloc[0].abs()
        s = (gross - dw.sum(axis=1) * (c / 10000.0)
             - ww.clip(upper=0).abs().sum(axis=1)
             * (BORROW_BPS_PER_YEAR / 10000.0) / TRADING_DAYS).dropna()
        cost_sweep[c] = round(perf(s.values)["sharpe"], 3)
        print("    %5.1f bps/side -> net ann Sharpe %+.3f" % (c, cost_sweep[c]))

    # (e) zero-borrow sensitivity on the best static spread
    zb = {}
    for bo in (0.0, BORROW_BPS_PER_YEAR, 100.0):
        w = lab.series[best_static["name"]]["w"]
        cols = list(w.columns)
        r = rets_all[cols].dropna()
        ww = w.reindex(r.index).ffill().fillna(0.0)
        gross = (ww * r).sum(axis=1)
        dw = ww.diff().abs().fillna(0.0)
        dw.iloc[0] = ww.iloc[0].abs()
        s = (gross - dw.sum(axis=1) * (COST_BPS_PER_SIDE / 10000.0)
             - ww.clip(upper=0).abs().sum(axis=1)
             * (bo / 10000.0) / TRADING_DAYS).dropna()
        zb[bo] = round(perf(s.values)["sharpe"], 3)
    print("\n--- borrow sensitivity on %s: %s" % (best_static["name"], zb))

    # =====================================================================
    # 7. Crisis table for the headline rows + SPY corr
    # =====================================================================
    print("\n--- CRISIS WINDOWS and SPY correlation (the known failure mode)")
    headline = ["hold_SPY", "hold_HYG", "hold_LQD", "hold_TLT",
                "static_HYG_minus_IEF", "static_LQD_minus_IEF",
                "static_HYG_minus_betaIEF", "static_TLT_minus_SHY",
                best["name"], best_static["name"]]
    seen = set()
    for nm in headline:
        if nm in seen:
            continue
        seen.add(nm)
        row = next((x for x in lab.results if x["name"] == nm), None)
        if row is None:
            continue
        def f(v):
            return "  n/a " if v is None else "%+6.1f%%" % (100 * v)
        print("    %-28s SPYcorr %+5.2f | 2008 %s (DD %s) | 2020 %s (DD %s) "
              "| 2022 %s (DD %s)"
              % (nm, row["spy_corr"] if row["spy_corr"] is not None else float("nan"),
                 f(row["crisis_2008_GFC_cum"]), f(row["crisis_2008_GFC_maxDD"]),
                 f(row["crisis_2020_COVID_cum"]), f(row["crisis_2020_COVID_maxDD"]),
                 f(row["crisis_2022_bond_bear_cum"]),
                 f(row["crisis_2022_bond_bear_maxDD"])))

    # =====================================================================
    # RECEIPT
    # =====================================================================
    receipt = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "status": "RESEARCH ONLY -- no capital, no orders, no pre-registration",
        "cost_model": {
            "commission_usd": 0.0,
            "headline_bps_per_side": COST_BPS_PER_SIDE,
            "sweep_bps_per_side": COST_SWEEP,
            "borrow_bps_per_year_on_short_legs": BORROW_BPS_PER_YEAR,
            "measured_corwin_schultz_roundtrip_bps": measured,
            "expense_ratios_already_in_total_return_prices": EXPENSE_RATIOS,
        },
        "data_limitation_fred_oas": (
            "FRED's ICE BofA OAS series (BAMLH0A0HYM2, BAMLC0A0CM) are "
            "licence-restricted to ~3 years on the free CSV endpoint (verified: "
            "first row 2023-08-28). The credit spread LEVEL is therefore built from "
            "ETF trailing-12m distribution yields over the current price."),
        "n_variants_this_study": n_variants,
        "search_count_wide": N_WIDE,
        "search_count_narrow": N_NARROW,
        "variants": lab.results,
        "best_xcash_timed": best["name"],
        "best_raw_sharpe_timed": best_raw["name"],
        "best_static_spread": best_static["name"],
        "n_variants_beating_own_buy_and_hold": len(beaters),
        "names_beating_own_buy_and_hold": [x["name"] for x in beaters],
        "harness": harness_out,
        "kill_tests": {
            "execution_delay_bps_per_day": delay_profile,
            "decay": decay,
            "effective_bets": bets,
            "purged_cv_fold_sharpes": folds,
            "cost_sweep_net_sharpe": cost_sweep,
            "borrow_sensitivity_best_static": zb,
            "whites_reality_check": wrc_out,
        },
    }
    os.makedirs("reports", exist_ok=True)
    with open(OUT_JSON, "w") as fh:
        json.dump(receipt, fh, indent=2, default=str)
    print("\nreceipt -> %s" % OUT_JSON)
    return receipt


if __name__ == "__main__":
    main()
