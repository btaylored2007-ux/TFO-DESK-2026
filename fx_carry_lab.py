#!/usr/bin/env python3
"""fx_carry_lab.py — Honest after-cost test of the G10 FX carry mechanism.

RESEARCH ONLY. Paper desk. No orders, no capital, no pre-registration, no secrets.

Two independent routes:
  (A) DIRECT PROXY  — DBV (Invesco DB G10 Currency Harvest). Its NAV total return
      IS the tradable G10 carry factor, already net of a real fund's expense ratio
      (~0.75%/yr) and trading spread. UUP/SPY pulled for context/benchmark.
  (B) BUILT BASKET  — 9 G10 crosses vs USD (yfinance), ranked monthly by carry.
      Carry is proxied by each currency's 3-month interbank rate (FRED, free,
      real). Long top-3 carry, short bottom-3, vol-targeted, monthly rebalance.
      Total return of a leg = spot return + local short rate accrual.

Everything NET is scored through quant_validation.py (deflated/search-deflated
Sharpe, purged CV, effective-N, kill-tests). Crash windows (2008 unwind, 2015 CHF
de-peg) are measured explicitly because crash risk is FX carry's known failure mode.
"""
import io
import json
import math
import os
import urllib.request

import numpy as np
import pandas as pd

import quant_validation as qv

CACHE = "/private/tmp/claude-501/-Users-test-Downloads/ad94ca3f-fc8a-4e60-8fcd-c99e53d7d7be/scratchpad/fx_cache"
os.makedirs(CACHE, exist_ok=True)
OUT_JSON = "reports/fx_carry_lab.json"

# ---------------------------------------------------------------------------
# Data helpers (cached so re-runs are cheap and deterministic)
# ---------------------------------------------------------------------------
def yf_daily(ticker, start="2003-01-01"):
    path = os.path.join(CACHE, "yf_%s.csv" % ticker.replace("=", "_").replace("^", ""))
    if os.path.exists(path):
        return pd.read_csv(path, index_col=0, parse_dates=True)["close"]
    import yfinance as yf
    d = yf.download(ticker, start=start, progress=False, auto_adjust=True)
    if d.empty:
        raise RuntimeError("no data for %s" % ticker)
    s = d["Close"]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    s.name = "close"
    s.to_frame().to_csv(path)
    return s


def fred_monthly(series_id):
    path = os.path.join(CACHE, "fred_%s.csv" % series_id)
    if os.path.exists(path):
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        return pd.to_numeric(df.iloc[:, 0], errors="coerce")
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=" + series_id
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=45).read().decode()
    df = pd.read_csv(io.StringIO(raw))
    df.columns = ["date", "val"]
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    df["val"] = pd.to_numeric(df["val"], errors="coerce")
    df.to_csv(path)
    return df["val"]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def perf(returns, ppy):
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if r.size < 2:
        return {}
    growth = np.prod(1 + r)
    years = r.size / ppy
    cagr = growth ** (1 / years) - 1 if growth > 0 else float("nan")
    vol = r.std(ddof=1) * math.sqrt(ppy)
    sharpe = (r.mean() / r.std(ddof=1)) * math.sqrt(ppy) if r.std(ddof=1) > 0 else 0.0
    eq = np.cumprod(1 + r)
    dd = eq / np.maximum.accumulate(eq) - 1
    return {"cagr": float(cagr), "vol": float(vol), "sharpe": float(sharpe),
            "maxDD": float(dd.min()), "n": int(r.size)}


def window_dd(returns_series, lo, hi):
    """Peak-to-trough return over [lo,hi] on a monthly/daily return series."""
    s = returns_series.loc[(returns_series.index >= lo) & (returns_series.index <= hi)]
    if s.empty:
        return None
    eq = (1 + s).cumprod()
    dd = eq / eq.cummax() - 1
    return {"start": str(s.index[0].date()), "end": str(s.index[-1].date()),
            "cum_return": float(eq.iloc[-1] - 1), "maxDD": float(dd.min()),
            "n_obs": int(s.size)}


def spy_corr(strat_ret, spy_ret):
    j = pd.concat([strat_ret.rename("s"), spy_ret.rename("m")], axis=1).dropna()
    if len(j) < 3:
        return float("nan")
    return float(j["s"].corr(j["m"]))


# ---------------------------------------------------------------------------
# ROUTE A — DBV direct proxy
# ---------------------------------------------------------------------------
def route_a():
    print("\n=== ROUTE A: DBV (Invesco DB G10 Currency Harvest) direct proxy ===")
    dbv = yf_daily("DBV")
    spy = yf_daily("SPY")
    # DBV total return is already NET of the fund's expense ratio + spread.
    dbv_m = dbv.resample("ME").last()
    spy_m = spy.resample("ME").last()
    r = dbv_m.pct_change().dropna()
    spy_r = spy_m.pct_change().dropna()
    r = r.loc[r.index <= dbv_m.index[-1]]

    ppy = 12
    p = perf(r.values, ppy)
    daily_r = dbv.pct_change().dropna()
    crashes = {
        "2008_carry_unwind": window_dd(daily_r, "2008-07-01", "2009-03-31"),
        "2015_CHF_depeg": window_dd(daily_r, "2015-01-01", "2015-02-28"),
        "2020_covid": window_dd(daily_r, "2020-02-15", "2020-04-15"),
    }
    corr = spy_corr(r, spy_r)

    # DBV net IS the tradable factor -> score the net monthly series directly.
    # Search count: house ~1,120 + this study's variants (see route_b grid ~ +6) + DBV as 1.
    n_trials = 1122
    ev = qv.evaluate("DBV_G10_carry_net", r.values, n_trials=n_trials,
                     periods_per_year=12, trades_per_period=1.0,
                     benchmark_returns=spy_r.reindex(r.index).dropna().values)
    print("  span %s -> %s  months=%d" % (r.index[0].date(), r.index[-1].date(), len(r)))
    print("  CAGR %.2f%%  vol %.1f%%  Sharpe %.2f  maxDD %.1f%%  SPYcorr %.2f"
          % (p["cagr"] * 100, p["vol"] * 100, p["sharpe"], p["maxDD"] * 100, corr))
    print("  deflated Sharpe %.4f (survives=%s)  |  " % (ev["deflated_sharpe"], ev["survives_deflation"])
          + qv.verdict_line(ev))
    for k, v in crashes.items():
        if v:
            print("  crash %-18s %s..%s  cum %.1f%%  DD %.1f%%"
                  % (k, v["start"], v["end"], v["cum_return"] * 100, v["maxDD"] * 100))
    return {"perf": p, "spy_corr": corr, "crashes": crashes, "evaluate": ev,
            "span": [str(r.index[0].date()), str(r.index[-1].date())],
            "monthly_returns": r, "note": "DBV NAV is net of ~0.75%/yr expense + spread; "
            "fund LIQUIDATED 2023-03 (data ends there) — discontinuation/survivorship caveat."}


# ---------------------------------------------------------------------------
# ROUTE B — built G10 carry basket
# ---------------------------------------------------------------------------
# yfinance ticker -> (is_inverted so we build USD-per-foreign, FRED short-rate id)
G10 = {
    "AUD": ("AUDUSD=X", False, "IR3TIB01AUM156N"),
    "NZD": ("NZDUSD=X", False, "IR3TIB01NZM156N"),
    "EUR": ("EURUSD=X", False, "IR3TIB01EZM156N"),
    "GBP": ("GBPUSD=X", False, "IR3TIB01GBM156N"),
    "JPY": ("JPY=X",    True,  "IR3TIB01JPM156N"),
    "CHF": ("CHF=X",    True,  "IR3TIB01CHM156N"),
    "CAD": ("CAD=X",    True,  "IR3TIB01CAM156N"),
    "NOK": ("NOK=X",    True,  "IR3TIB01NOM156N"),
    "SEK": ("SEK=X",    True,  "IR3TIB01SEM156N"),
}
USD_RATE = "IR3TIB01USM156N"


def build_panels():
    usd_per_fx = {}
    rates = {}
    for ccy, (tk, inv, rid) in G10.items():
        s = yf_daily(tk)
        s = s[s > 0]
        usd_per_fx[ccy] = (1.0 / s) if inv else s
        rates[ccy] = fred_monthly(rid)
    px = pd.DataFrame(usd_per_fx).resample("ME").last()
    rate_df = pd.DataFrame(rates)
    rate_df.index = rate_df.index + pd.offsets.MonthEnd(0)
    usd = fred_monthly(USD_RATE)
    usd.index = usd.index + pd.offsets.MonthEnd(0)
    rate_df = rate_df.reindex(px.index).ffill()
    usd = usd.reindex(px.index).ffill()
    return px, rate_df, usd


def basket_backtest(px, rate_df, usd, n_long=3, n_short=3, vol_target=0.10,
                    cost_bps=7.5, vol_lb=12):
    """Monthly long high-carry / short low-carry, vol-targeted. Returns dict."""
    spot = px.pct_change()
    # carry accrual earned over the month (annual rate / 12), lagged onto the holding month
    accr = rate_df / 100.0 / 12.0
    total = spot + accr.shift(1)  # earn last-known rate over the month held
    carry_sig = (rate_df.sub(usd, axis=0)).shift(1)  # rank on prior month-end info

    idx = px.index
    raw = pd.Series(0.0, index=idx)
    turnover = pd.Series(0.0, index=idx)
    prev_w = pd.Series(0.0, index=px.columns)
    for i in range(1, len(idx)):
        t = idx[i]
        sig = carry_sig.loc[t].dropna()
        avail = sig.index[total.loc[t, sig.index].notna()]
        sig = sig.loc[avail]
        if len(sig) < n_long + n_short:
            prev_w = pd.Series(0.0, index=px.columns)
            continue
        ranked = sig.sort_values(ascending=False)
        longs = ranked.index[:n_long]
        shorts = ranked.index[-n_short:]
        w = pd.Series(0.0, index=px.columns)
        w[longs] = 1.0 / n_long
        w[shorts] = -1.0 / n_short
        raw.loc[t] = float((w * total.loc[t]).sum())
        turnover.loc[t] = float((w - prev_w).abs().sum())
        prev_w = w
    raw = raw.iloc[1:]
    turnover = turnover.iloc[1:]

    # vol-target using trailing realized vol of the raw (unlevered) basket
    rvol = raw.rolling(vol_lb).std() * math.sqrt(12)
    lev = (vol_target / rvol).shift(1).clip(upper=3.0)
    gross = (raw * lev).dropna()
    tno = turnover.reindex(gross.index).fillna(0.0) * lev.reindex(gross.index).fillna(0.0)
    cost = tno * (cost_bps / 10000.0)
    net = (gross - cost).dropna()
    gross = gross.reindex(net.index)
    return {"gross": gross, "net": net, "turnover": tno.reindex(net.index),
            "ann_turnover": float(tno.reindex(net.index).mean() * 12)}


def route_b():
    print("\n=== ROUTE B: built G10 carry basket (yfinance FX + FRED short rates) ===")
    px, rate_df, usd = build_panels()
    print("  FX months %d (%s -> %s)  currencies=%d"
          % (len(px), px.index[0].date(), px.index[-1].date(), px.shape[1]))
    spy = yf_daily("SPY").resample("ME").last().pct_change().dropna()

    base = basket_backtest(px, rate_df, usd, cost_bps=7.5)
    net, gross = base["net"], base["gross"]
    ppy = 12
    pg, pn = perf(gross.values, ppy), perf(net.values, ppy)
    corr = spy_corr(net, spy)

    crashes = {
        "2008_carry_unwind": window_dd(net, "2008-07-01", "2009-03-31"),
        "2015_CHF_depeg": window_dd(net, "2014-12-01", "2015-03-31"),
        "2020_covid": window_dd(net, "2020-02-01", "2020-05-31"),
    }

    # cost stress
    stress = {}
    for c in (5.0, 10.0, 20.0):
        bt = basket_backtest(px, rate_df, usd, cost_bps=c)
        stress[c] = perf(bt["net"].values, ppy)["sharpe"]

    # honest small variant grid (search count contribution) + trial sharpes
    trial_sharpes = []
    for nl in (2, 3):
        for vt in (0.08, 0.10, 0.12):
            for cb in (5.0, 10.0):
                bt = basket_backtest(px, rate_df, usd, n_long=nl, n_short=nl,
                                     vol_target=vt, cost_bps=cb)
                pp = perf(bt["net"].values, ppy)
                if pp:
                    trial_sharpes.append(pp["sharpe"] / math.sqrt(ppy))  # per-period
    n_variants = len(trial_sharpes)

    n_trials = 1122 + n_variants
    ev = qv.evaluate("G10_built_carry_net", net.values, n_trials=n_trials,
                     periods_per_year=12, trades_per_period=1.0,
                     gross_returns=gross.values,
                     benchmark_returns=spy.reindex(net.index).dropna().values)

    # purged CV: sign-stable mean across folds?
    fold_means = []
    arr = net.values
    for tr, te in qv.purged_kfold_splits(len(arr), n_splits=5, embargo_pct=0.02, label_horizon=1):
        fold_means.append(float(arr[te].mean()))

    # kill-test: is monthly rebalance really N independent bets?
    lw = base["net"]  # proxy rank persistence via basket-return autocorr
    ac = float(pd.Series(arr).autocorr(lag=1)) if len(arr) > 3 else 0.0
    ebets = qv.effective_bets(len(arr), max(0.0, ac))

    print("  gross Sharpe %.2f | NET Sharpe %.2f | CAGR(net) %.2f%% vol %.1f%% maxDD %.1f%%"
          % (pg["sharpe"], pn["sharpe"], pn["cagr"] * 100, pn["vol"] * 100, pn["maxDD"] * 100))
    print("  cost-stress NET Sharpe  5bps=%.2f 10bps=%.2f 20bps=%.2f  (ann turnover %.1fx)"
          % (stress[5.0], stress[10.0], stress[20.0], base["ann_turnover"]))
    print("  SPY corr %.2f | deflated Sharpe %.4f (survives=%s)" % (corr, ev["deflated_sharpe"], ev["survives_deflation"]))
    print("  " + qv.verdict_line(ev))
    print("  purged-CV fold means (x1e4): %s" % [round(x * 1e4, 1) for x in fold_means])
    for k, v in crashes.items():
        if v:
            print("  crash %-18s %s..%s  cum %.1f%%  DD %.1f%%"
                  % (k, v["start"], v["end"], v["cum_return"] * 100, v["maxDD"] * 100))

    return {"perf_gross": pg, "perf_net": pn, "spy_corr": corr, "crashes": crashes,
            "cost_stress_net_sharpe": stress, "ann_turnover": base["ann_turnover"],
            "evaluate": ev, "n_variants": n_variants,
            "purged_cv_fold_means": fold_means, "effective_bets": ebets,
            "span": [str(net.index[0].date()), str(net.index[-1].date())],
            "monthly_net": net, "monthly_gross": gross}


def _clean_for_json(o):
    if isinstance(o, dict):
        return {k: _clean_for_json(v) for k, v in o.items()
                if not isinstance(v, (pd.Series, pd.DataFrame))}
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    return o


if __name__ == "__main__":
    os.chdir("/Users/test/Applications/MultiVenueDesk")
    a = route_a()
    b = route_b()
    dump = {"route_a_DBV": _clean_for_json(a), "route_b_built": _clean_for_json(b),
            "generated": "2026-08-10", "status": "READ-ONLY research, paper desk, no orders"}
    with open(OUT_JSON, "w") as f:
        json.dump(dump, f, indent=2, default=str)
    print("\nwrote", OUT_JSON)
