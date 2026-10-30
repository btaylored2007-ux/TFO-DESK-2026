#!/usr/bin/env python3
"""rates_curve_lab.py — Honest COLD test of RATES CURVE TREND & CARRY:
time-series duration timing along the US Treasury curve.

RESEARCH ONLY. Paper desk. No orders, no capital, no pre-registration, no secrets.
Read-only w.r.t. the rest of the desk. Writes only its own JSON receipt + note.

WHY DISTINCT FROM WHAT WE ALREADY KILLED
----------------------------------------
The killed cross-asset carry lab was CROSS-SECTIONAL: rank many assets by carry
LEVEL, long the high tercile, short the low. This lab is TIME-SERIES positioning
along ONE curve — the classic duration-timing pair:
  CARRY : curve slope as the carry/roll-down signal. A steep curve pays a long-
          duration holder both term premium and roll-down; a flat/inverted curve
          pays nothing for the duration risk. Steep => long TLT, flat/inv => SHY.
  TREND : TSMOM on the bond ETFs' own total return (Moskowitz-Ooi-Pedersen family
          applied to a single asset, not a cross-section): positive trailing
          k-month return => long, negative => cash (and a shorts-allowed 12m
          variant with borrow swept 1%/3%).
The acid test is 2022: slope signals inverted late (curve stayed steep-ish while
yields exploded) and trend went to cash — the lab must show which variants dodge
the bond crash and which ride it down.

THE HONEST DATA NOTES (read before trusting any number)
-------------------------------------------------------
* Total return = raw close + cash distributions (yfinance auto_adjust=False),
  monthly. This is the same pattern as cross_asset_carry_lab and avoids Yahoo's
  adjusted-close splice artifacts.
* Panel starts ~2002-08 (SHY/IEF/TLT inception 2002-07) — covers 2008, 2020, 2022.
* Curve slopes are FRED daily series (T10Y2Y; and DGS10 minus DGS3MO), sampled at
  month-end, LAGGED one month before use — no look-ahead.
* Cash leg earns 0%. That is CONSERVATIVE (T-bills paid 4-5% in 2023-24); a
  surviving edge would be understated, a dead one is dead with a tailwind refused.
* Costs 5 bps per side on turnover — fair for SHY/IEF/TLT, among the most liquid
  ETFs alive. Shorts-allowed variants carry borrow at 1% and 3%/yr on the short
  notional.
* Benchmark for the gate: IEF buy-and-hold. Duration timing that cannot beat
  just HOLDING the belly of the curve is not a strategy.

SCORING: gross vs net, quant_validation on the best NET variant with the honest
search count (house prior 1179 + every variant formed here), effective-N from
MEASURED position persistence, crash windows 2008/2020/2022, beta vs SPY.

Report real output, win or lose. A mechanism that dies in the harness is the
process working.
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

CACHE = "/private/tmp/claude-501/-Users-test/326d8bdd-e63d-4126-a322-4582de2ba921/scratchpad/rates_curve_cache"
os.makedirs(CACHE, exist_ok=True)
RECEIPT = "reports/rates_curve_lab.json"
NOTE = "reports/research_ratescurve.md"

HOUSE_PRIOR_TRIALS = 1179          # cumulative search count before this lab
COST_BPS_SIDE = 5.0                # per side on turnover (very liquid UST ETFs)
ETFS = ["SHY", "IEF", "TLT"]


# ---------------------------------------------------------------------------
# Data (cached so re-runs are cheap and deterministic)
# ---------------------------------------------------------------------------
def yf_hist(ticker, start="2002-01-01"):
    """Daily raw close + cash dividends, cached. DataFrame[close, div]."""
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


def fred_series(series_id):
    """Daily FRED series, cached. Returns a float Series indexed by date."""
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


def monthly_total_returns(tickers):
    """DataFrame of monthly total returns (price move + dividends in month)."""
    rets = {}
    for t in tickers:
        d = yf_hist(t)
        me = d.resample("ME").agg({"close": "last", "div": "sum"})
        me = me.dropna(subset=["close"])
        rets[t] = (me["close"] + me["div"]) / me["close"].shift(1) - 1.0
    return pd.DataFrame(rets).dropna(how="any")


# ---------------------------------------------------------------------------
# Strategy engine — weights decided at month-end t, return realised month t+1
# ---------------------------------------------------------------------------
def run_weights(W, R, borrow_annual=0.0):
    """P&L of a monthly weight frame W (already lagged: row t = weights HELD
    during month t). Costs 5 bps/side on turnover; borrow on short notional.
    Returns (net, gross, turnover) Series and the measured lag-1 autocorrelation
    of the position (persistence -> effective-N discount)."""
    W = W.dropna(how="any")
    common = W.index.intersection(R.index)
    W = W.loc[common]
    dates, net, gross, turns = [], [], [], []
    prev = pd.Series(0.0, index=W.columns)
    bm = borrow_annual / 12.0
    for t in W.index:
        w = W.loc[t]
        r = R.loc[t].reindex(w.index).fillna(0.0)
        g = float((w * r).sum())
        turnover = float((w - prev).abs().sum())
        cost = turnover * (COST_BPS_SIDE / 10000.0)
        short_cost = float(w[w < 0].abs().sum()) * bm
        dates.append(t)
        gross.append(g)
        net.append(g - cost - short_cost)
        turns.append(turnover)
        prev = w
    di = pd.DatetimeIndex(dates)
    # position persistence: lag-1 autocorr of the net duration-risk exposure
    expo = W.sum(axis=1) if (W < 0).any().any() else W.max(axis=1)
    # use the signed weight on the variant's risk asset: sum works for 1-asset
    # long/short/cash; for carry (TLT vs SHY switch) use the TLT column.
    if "TLT" in W.columns and (W["TLT"].abs().sum() > 0) and W.shape[1] > 1:
        expo = W["TLT"]
    x = expo.values
    if len(x) > 2 and np.std(x[1:]) > 0 and np.std(x[:-1]) > 0:
        rho = float(np.corrcoef(x[1:], x[:-1])[0, 1])
    else:
        rho = 0.99   # constant position = one held bet; maximally discounted
    return (pd.Series(net, index=di), pd.Series(gross, index=di),
            pd.Series(turns, index=di), rho)


def carry_weights(R, slope_monthly):
    """Steep curve (slope > 0) => long TLT; flat/inverted => SHY. Lagged 1m."""
    sig = slope_monthly.reindex(R.index).shift(1)   # decide on last month's slope
    W = pd.DataFrame(np.nan, index=R.index, columns=["SHY", "TLT"])
    W.loc[sig.notna(), "TLT"] = (sig[sig.notna()] > 0).astype(float)
    W.loc[sig.notna(), "SHY"] = (sig[sig.notna()] <= 0).astype(float)
    return W


def trend_weights(R, etf, lookback, allow_short=False):
    """TSMOM: trailing `lookback`-month total return of the ETF, lagged 1m.
    Positive => long 1.0; negative => cash 0.0 (or short -1.0 if allowed)."""
    mom = (1.0 + R[etf]).rolling(lookback).apply(np.prod, raw=True) - 1.0
    sig = mom.shift(1)
    w = pd.Series(np.nan, index=R.index)
    w[sig > 0] = 1.0
    w[sig <= 0] = -1.0 if allow_short else 0.0
    return pd.DataFrame({etf: w})


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def maxdd(s):
    eq = (1 + s).cumprod()
    return float((eq / eq.cummax() - 1).min())


def crash(s, lo, hi):
    w = s[(s.index >= lo) & (s.index <= hi)]
    return round(float((1 + w).prod() - 1) * 100, 2) if len(w) else None


CRASHES = {
    "crash_2008_pct": (pd.Timestamp("2008-06-01"), pd.Timestamp("2009-03-31")),
    "crash_2020_pct": (pd.Timestamp("2020-02-01"), pd.Timestamp("2020-04-30")),
    "crash_2022_pct": (pd.Timestamp("2022-01-01"), pd.Timestamp("2022-10-31")),
}


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
    return round(float(np.polyfit(x, y, 1)[0]), 3)


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


# ---------------------------------------------------------------------------
def main():
    R = monthly_total_returns(ETFS)

    # curve slopes, month-end sampled
    t10y2y = fred_series("T10Y2Y").resample("ME").last()
    dgs10 = fred_series("DGS10").resample("ME").last()
    dgs3mo = fred_series("DGS3MO").resample("ME").last()
    slope_10_3m = (dgs10 - dgs3mo)

    ief_bh = R["IEF"]        # benchmark: buy-and-hold the belly

    specs = []
    specs.append(("carry_t10y2y", carry_weights(R, t10y2y), 0.0))
    specs.append(("carry_dgs10_3mo", carry_weights(R, slope_10_3m), 0.0))
    for etf in ["TLT", "IEF"]:
        for lb in [3, 6, 12]:
            specs.append((f"tsmom_{etf}_{lb}m",
                          trend_weights(R, etf, lb, allow_short=False), 0.0))
    for etf in ["TLT", "IEF"]:
        for borrow in [0.01, 0.03]:
            specs.append((f"tsmom_{etf}_12m_short_b{int(borrow*100)}",
                          trend_weights(R, etf, 12, allow_short=True), borrow))

    variants, trial_sharpes, series = [], [], {}
    n_here = 0
    for name, W, borrow in specs:
        net, gross, turn, rho = run_weights(W, R, borrow)
        n_here += 1
        m = qv.moments(net.values)
        trial_sharpes.append(m["sharpe"])
        series[name] = (net, gross, rho)
        row = {
            "name": name,
            "n_months": int(net.size),
            "borrow_annual": borrow,
            "net_sharpe_ann": round(m["sharpe"] * math.sqrt(12), 3),
            "net_cagr_pct": round(((1 + net).prod() ** (12 / max(1, net.size)) - 1) * 100, 2),
            "gross_cagr_pct": round(((1 + gross).prod() ** (12 / max(1, gross.size)) - 1) * 100, 2),
            "maxdd_pct": round(maxdd(net) * 100, 2),
            "avg_turnover": round(float(turn.mean()), 3),
            "position_autocorr": round(rho, 3),
        }
        for ck, (lo, hi) in CRASHES.items():
            row[ck] = crash(net, lo, hi)
        variants.append(row)

    # context rows: what the raw assets did (NOT variants, not searched)
    bench_rows = []
    for etf in ETFS:
        s = R[etf]
        m = qv.moments(s.values)
        br = {"name": f"buyhold_{etf}",
              "net_sharpe_ann": round(m["sharpe"] * math.sqrt(12), 3),
              "net_cagr_pct": round(((1 + s).prod() ** (12 / max(1, s.size)) - 1) * 100, 2),
              "maxdd_pct": round(maxdd(s) * 100, 2)}
        for ck, (lo, hi) in CRASHES.items():
            br[ck] = crash(s, lo, hi)
        bench_rows.append(br)

    total_trials = HOUSE_PRIOR_TRIALS + n_here
    scored = sorted(variants, key=lambda v: v["net_sharpe_ann"], reverse=True)
    best = scored[0]
    net, gross, rho = series[best["name"]]
    eff = qv.effective_bets(net.size, rho)
    n_eff = eff["independent_bets"]
    bench = ief_bh.reindex(net.index).values
    ev = qv.evaluate(f"rates_curve_{best['name']}", net.values,
                     n_trials=total_trials, trial_sharpes=trial_sharpes,
                     periods_per_year=12, trades_per_period=1.0,
                     gross_returns=gross.values, n_effective=n_eff,
                     benchmark_returns=bench)

    # White's reality check across all variants on the common index
    common = None
    for name in series:
        idx = series[name][0].index
        common = idx if common is None else common.intersection(idx)
    M = np.column_stack([series[k][0].reindex(common).fillna(0).values
                         for k in series])
    # qv.whites_reality_check expects (n_strategies, n_observations) — variants in
    # ROWS (verified against the function source; the transposed call yields
    # nonsense). Audit 2026-08-14 caught the original transposed primary.
    wrc = qv.whites_reality_check(M.T)

    b_spy = beta_to_spy(net)
    out = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "mechanism": ("rates curve trend & carry — TIME-SERIES duration timing on one curve "
                      "(slope carry TLT/SHY switch + TSMOM on TLT/IEF), distinct from the "
                      "killed cross-sectional carry"),
        "universe": ETFS,
        "panel_start": str(R.index[0].date()), "panel_end": str(R.index[-1].date()),
        "n_months_panel": int(len(R)),
        "cost_bps_side": COST_BPS_SIDE,
        "cash_assumption": "cash leg earns 0% (conservative; T-bills paid 4-5% in 2023-24)",
        "house_prior_trials": HOUSE_PRIOR_TRIALS,
        "lab_variants": n_here,
        "total_deflation_trials": total_trials,
        "variants": variants,
        "buyhold_context": bench_rows,
        "best_variant": best["name"],
        "best_position_autocorr": round(rho, 3),
        "best_effective_bets": eff,
        "beta_to_spy": b_spy,
        "best_crash_2008_pct": best["crash_2008_pct"],
        "best_crash_2020_pct": best["crash_2020_pct"],
        "best_crash_2022_pct": best["crash_2022_pct"],
        "whites_reality_check": wrc,
        "best_evaluate": ev,
        "verdict_line": qv.verdict_line(ev),
    }
    with open(RECEIPT, "w") as f:
        json.dump(_j(out), f, indent=2)

    print("=" * 80)
    print("RATES CURVE TREND & CARRY LAB — time-series duration timing (SHY/IEF/TLT)")
    print("=" * 80)
    print(f"  panel     : {out['panel_start']} .. {out['panel_end']}  ({len(R)} months)")
    print(f"  costs     : {COST_BPS_SIDE} bps/side on turnover; cash earns 0%")
    print(f"  deflation : {total_trials} trials (house {HOUSE_PRIOR_TRIALS} + {n_here} here)")
    print("  variants (annualised net Sharpe, best first):")
    for v in scored:
        print(f"    {v['name']:<24} netSh {v['net_sharpe_ann']:+.3f}  "
              f"CAGR {v['net_cagr_pct']:+.2f}%  maxDD {v['maxdd_pct']:.1f}%  "
              f"2022 {str(v['crash_2022_pct']):>7}%  turn {v['avg_turnover']:.2f}  "
              f"posAC {v['position_autocorr']:+.2f}  n={v['n_months']}")
    print("  buy-and-hold context:")
    for b in bench_rows:
        print(f"    {b['name']:<24} Sh {b['net_sharpe_ann']:+.3f}  "
              f"CAGR {b['net_cagr_pct']:+.2f}%  maxDD {b['maxdd_pct']:.1f}%  "
              f"2022 {str(b['crash_2022_pct']):>7}%")
    print(f"\n  best variant {best['name']}  position autocorr {rho:+.3f}  "
          f"n_eff {n_eff}  beta->SPY {b_spy}")
    print(f"  crash windows (best): 2008 {best['crash_2008_pct']}%  "
          f"2020 {best['crash_2020_pct']}%  2022 {best['crash_2022_pct']}%")
    print(f"  White's reality check p={wrc['p_value']} survives {wrc['survives']}")
    print("\n  HARNESS on best NET variant (vs IEF buy-and-hold):")
    print(f"    n_obs {ev['n_obs']}  n_effective {ev['n_effective']}  "
          f"t {ev['t_stat']}  p {ev['p_value']}")
    print(f"    gross/trade {ev.get('gross_per_trade_bps')}bps  "
          f"net/trade {ev['per_trade_bps']}bps  cost_drag {ev.get('cost_drag_bps')}bps")
    print(f"    excess vs IEF {ev.get('excess_over_benchmark_bps')}bps  "
          f"t {ev.get('excess_t_stat')}  beats_benchmark {ev.get('beats_benchmark')}")
    print(f"    deflated_sharpe {ev['deflated_sharpe']}  "
          f"survives_deflation {ev['survives_deflation']}")
    print(f"\n  VERDICT: {out['verdict_line']}")
    print(f"\n  receipt -> {RECEIPT}")
    return out


if __name__ == "__main__":
    main()
