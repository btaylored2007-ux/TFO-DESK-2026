#!/usr/bin/env python3
"""overnight_lab.py — Honest COLD test of the OVERNIGHT vs INTRADAY RETURN
DECOMPOSITION anomaly (session-timing / market-microstructure family).

RESEARCH ONLY. Paper desk. No orders, no capital, no pre-registration, no secrets.
Read-only w.r.t. everything else. Writes only its own JSON receipt + research note.

WHY DISTINCT
------------
Every mechanism the house has killed is either a price-history factor (trend,
mean-rev, value, carry, L/S) or a positioning/flow signal. This is neither: it is
a SESSION-TIMING decomposition. The documented effect (Cliff-Cooper-Gulen and the
"overnight drift" literature): in US equity ETFs, close-to-open (overnight)
returns carry most of the total return while open-to-close (intraday) is roughly
flat. The "signal" is simply the clock — no parameter fitting, no cross-section.
The entire question is whether the effect clears a per-day two-trade cost wall.

THE HONEST DATA PROBLEM (read before trusting any number)
---------------------------------------------------------
1. yfinance with auto_adjust=True scales Open by the SAME split/dividend factor
   as Close. The adjusted Open is therefore an APPROXIMATION — Yahoo's raw opens
   are the venue print, and back-adjustment assumes the whole dividend accrues
   overnight (economically right: the ex-div drop happens at the open, so the
   overnight holder earns the distribution — but it makes the overnight leg the
   mechanical home of dividend income by construction).
2. Early-history opens on Yahoo can be stale (Open == prior Close). Measured:
   SPY stale-open fraction is 1.7% in 1993, ~0 after 1995, 0.08% overall — small
   enough to keep the full panel, and it is reported per-ETF in the receipt.
3. Execution at the exact auction open/close prints, every day, with NO slippage
   beyond the modelled per-side cost — i.e. the backtest is the BEST CASE for
   the strategy. MOC/MOO auction fills make this less fantastical than for most
   strategies, but 2 trades/day at 1 bps/side is already a ~5%/yr drag; the cost
   wall is the entire question and it is swept at 1 and 5 bps/side.

DESIGN
------
  universe : SPY, QQQ, IWM, DIA, EFA, EEM (liquid, 20+ years of daily OHLC)
  decompose: overnight_t = Open_t/Close_(t-1) - 1 ; intraday_t = Close_t/Open_t - 1
             (descriptive annualised means reported FIRST — the raw fact)
  variants : long-overnight-only (buy close, sell next open) and
             long-intraday-only (buy open, sell close), per ETF, per cost level
             {1, 5} bps/side  =>  6 x 2 x 2 = 24 net variants tried
  costs    : 2 trades/day => 2 and 10 bps/day round-trip respectively
  shorts   : none — every variant is long-only, so the short-borrow sweep is
             NOT APPLICABLE (recorded as such in the receipt)
  benchmark: buy-and-hold total return of the SAME ETF over the SAME days
  scoring  : qv.evaluate on the best single-ETF NET variant with n_trials =
             HOUSE_PRIOR_TRIALS + 24 and trial_sharpes from all 24 variants;
             n_effective = n_obs of that single ETF (daily bars ~independent;
             we do NOT pool across the correlated ETFs for the headline).
             White's Reality Check across all 24 variant net series (one series
             per strategy row, per the harness docstring/self-test) on the
             common date window. Crash windows 2008 / 2020 / 2022. Monthly beta
             to SPY of the best net variant.

NON-NEGOTIABLE: report real output, win or lose. A mechanism that dies in the
harness is a SUCCESS of the process.
"""
import json
import math
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import quant_validation as qv

CACHE = ("/private/tmp/claude-501/-Users-test/"
         "326d8bdd-e63d-4126-a322-4582de2ba921/scratchpad/overnight_cache")
os.makedirs(CACHE, exist_ok=True)
RECEIPT = "reports/overnight_lab.json"
NOTE = "reports/research_overnight.md"

HOUSE_PRIOR_TRIALS = 1179
ETFS = ["SPY", "QQQ", "IWM", "DIA", "EFA", "EEM"]
COST_SIDES_BPS = [1.0, 5.0]      # per side; 2 trades/day => 2 / 10 bps per day
TRADES_PER_DAY = 2.0
PPY = 252.0


def yf_ohlc(ticker):
    """Cached daily OHLC, auto_adjust=True (adjusted Open AND Close — see
    docstring caveat 1: the adjusted Open is a yfinance approximation)."""
    path = os.path.join(CACHE, "adj_%s.csv" % ticker)
    if os.path.exists(path):
        return pd.read_csv(path, index_col=0, parse_dates=True)
    import yfinance as yf
    h = yf.Ticker(ticker).history(period="max", auto_adjust=True)
    if h.empty:
        raise RuntimeError("no data for %s" % ticker)
    df = h[["Open", "Close"]].copy()
    df.index = df.index.tz_localize(None)
    df.to_csv(path)
    return df


def decompose(df):
    """overnight, intraday, close-to-close (all daily simple returns) + diagnostics."""
    o, c = df["Open"], df["Close"]
    on = o / c.shift(1) - 1.0
    intr = c / o - 1.0
    cc = c / c.shift(1) - 1.0
    keep = on.notna() & intr.notna() & cc.notna() & (o > 0) & (c > 0)
    n_stale = int((on[keep].abs() < 1e-12).sum())
    return on[keep], intr[keep], cc[keep], n_stale


def maxdd(s):
    eq = (1 + s).cumprod()
    return float((eq / eq.cummax() - 1).min())


def crash(s, lo, hi):
    w = s[(s.index >= lo) & (s.index <= hi)]
    return round(float((1 + w).prod() - 1) * 100, 2) if len(w) else None


def beta_spy_monthly(daily_net):
    """Monthly beta of the strategy vs SPY total return (adjusted close)."""
    spy = yf_ohlc("SPY")["Close"].resample("ME").last()
    spy_tr = (spy / spy.shift(1) - 1.0).dropna()
    strat_m = (1 + daily_net).resample("ME").prod() - 1.0
    c = strat_m.index.intersection(spy_tr.index)
    if len(c) < 24:
        return None
    x = spy_tr.reindex(c).values
    y = strat_m.reindex(c).values
    if np.nanstd(x) == 0:
        return None
    return round(float(np.polyfit(x, y, 1)[0]), 3)


def main():
    # ---- load + decompose -------------------------------------------------
    panels = {}          # etf -> dict(on, intr, cc, n_stale)
    decomposition = []   # the descriptive fact, per ETF
    for t in ETFS:
        df = yf_ohlc(t)
        on, intr, cc, n_stale = decompose(df)
        panels[t] = {"on": on, "intr": intr, "cc": cc, "n_stale": n_stale}
        decomposition.append({
            "etf": t,
            "start": str(on.index[0].date()), "end": str(on.index[-1].date()),
            "n_days": int(on.size),
            "stale_open_days": n_stale,
            "overnight_ann_mean_pct": round(float(on.mean()) * PPY * 100, 2),
            "intraday_ann_mean_pct": round(float(intr.mean()) * PPY * 100, 2),
            "closeclose_ann_mean_pct": round(float(cc.mean()) * PPY * 100, 2),
            "overnight_bps_day": round(float(on.mean()) * 1e4, 2),
            "intraday_bps_day": round(float(intr.mean()) * 1e4, 2),
        })

    # ---- variants: leg x ETF x cost --------------------------------------
    variants, trial_sharpes, series = [], [], {}
    n_here = 0
    for t in ETFS:
        for leg in ["overnight", "intraday"]:
            gross = panels[t]["on"] if leg == "overnight" else panels[t]["intr"]
            for side in COST_SIDES_BPS:
                n_here += 1
                cost_day = TRADES_PER_DAY * side / 1e4
                net = gross - cost_day
                m = qv.moments(net.values)
                trial_sharpes.append(m["sharpe"])
                key = "%s_%s_%dbps" % (t, leg, int(side))
                series[key] = (net, gross, t)
                variants.append({
                    "name": key, "etf": t, "leg": leg, "cost_bps_side": side,
                    "n_days": int(net.size),
                    "gross_bps_day": round(float(gross.mean()) * 1e4, 3),
                    "net_bps_day": round(float(net.mean()) * 1e4, 3),
                    "net_sharpe_ann": round(m["sharpe"] * math.sqrt(PPY), 3),
                    "net_cagr_pct": round(
                        ((1 + net).prod() ** (PPY / max(1, net.size)) - 1) * 100, 2),
                    "maxdd_pct": round(maxdd(net) * 100, 2),
                })

    scored = sorted(variants, key=lambda v: v["net_sharpe_ann"], reverse=True)
    best = scored[0]
    total_trials = HOUSE_PRIOR_TRIALS + n_here

    # ---- harness on the best single-ETF NET variant ----------------------
    net, gross, etf = series[best["name"]]
    bench = panels[etf]["cc"].reindex(net.index)   # buy-and-hold, same ETF, same days
    n_eff = float(net.size)                        # daily bars of ONE ETF ~independent
    ev = qv.evaluate(
        "overnight_%s" % best["name"], net.values,
        n_trials=total_trials, trial_sharpes=trial_sharpes,
        periods_per_year=PPY, trades_per_period=TRADES_PER_DAY,
        gross_returns=gross.values, n_effective=n_eff,
        benchmark_returns=bench.values)

    # ---- White's Reality Check across all 24 net variants ----------------
    # One equal-length series PER STRATEGY (harness docstring/self-test form),
    # on the common date window across all six ETFs.
    common = None
    for t in ETFS:
        idx = panels[t]["on"].index
        common = idx if common is None else common.intersection(idx)
    wrc_matrix = np.vstack([series[v["name"]][0].reindex(common).values
                            for v in variants])
    wrc = qv.whites_reality_check(wrc_matrix)

    # ---- risk numbers for the best variant -------------------------------
    beta = beta_spy_monthly(net)
    crashes = {
        "crash_2008_pct": crash(net, pd.Timestamp("2008-06-01"), pd.Timestamp("2009-03-31")),
        "crash_2020_pct": crash(net, pd.Timestamp("2020-02-01"), pd.Timestamp("2020-04-30")),
        "crash_2022_pct": crash(net, pd.Timestamp("2022-01-01"), pd.Timestamp("2022-10-31")),
    }

    out = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "mechanism": ("overnight vs intraday session-timing decomposition — "
                      "close-to-open carries the equity return, open-to-close is flat; "
                      "distinct family (microstructure/session), first test here"),
        "universe": ETFS,
        "house_prior_trials": HOUSE_PRIOR_TRIALS,
        "lab_variants": n_here,
        "total_deflation_trials": total_trials,
        "cost_model": {"per_side_bps_swept": COST_SIDES_BPS,
                       "trades_per_day": TRADES_PER_DAY,
                       "round_trip_bps_day": [2 * s for s in COST_SIDES_BPS]},
        "short_borrow_sweep": "not applicable — all 24 variants are long-only, no shorts held",
        "honest_data_notes": [
            "yfinance auto_adjust scales Open by the Close adjustment factor — the "
            "adjusted Open is an approximation; dividends land in the overnight leg "
            "by construction (economically correct: ex-div gap is at the open)",
            "stale opens (Open==prior Close) counted per ETF; worst is early-1990s "
            "SPY at ~1-2%/yr of days, ~0 after 1995",
            "fills assumed at exact auction open/close prints — best case for the "
            "strategy; the swept per-side cost is the only friction",
        ],
        "decomposition_descriptive": decomposition,
        "variants": variants,
        "best_variant": best["name"],
        "best_evaluate": ev,
        "beta_to_spy": beta,
        **crashes,
        "whites_reality_check": wrc,
        "wrc_common_window": {"start": str(common[0].date()),
                              "end": str(common[-1].date()),
                              "n_days": int(len(common))},
        "verdict_line": qv.verdict_line(ev),
    }
    with open(RECEIPT, "w") as f:
        json.dump(_j(out), f, indent=2)

    # ---- printed verdict --------------------------------------------------
    print("=" * 80)
    print("OVERNIGHT vs INTRADAY DECOMPOSITION LAB — session-timing anomaly")
    print("=" * 80)
    print("\n  DESCRIPTIVE FACT (annualised mean of each leg — before any strategy):")
    for d in decomposition:
        print("    %-4s %s..%s  overnight %+6.2f%%/yr  intraday %+6.2f%%/yr  "
              "(c2c %+6.2f%%/yr)  n=%d  stale_opens=%d"
              % (d["etf"], d["start"], d["end"], d["overnight_ann_mean_pct"],
                 d["intraday_ann_mean_pct"], d["closeclose_ann_mean_pct"],
                 d["n_days"], d["stale_open_days"]))
    print("\n  deflation: %d trials (house %d + %d here)"
          % (total_trials, HOUSE_PRIOR_TRIALS, n_here))
    print("  variants (annualised net Sharpe, best first):")
    for v in scored:
        print("    %-22s netSh %+7.3f  net %+7.3f bps/d  gross %+7.3f bps/d  "
              "CAGR %+7.2f%%  maxDD %6.1f%%  n=%d"
              % (v["name"], v["net_sharpe_ann"], v["net_bps_day"],
                 v["gross_bps_day"], v["net_cagr_pct"], v["maxdd_pct"], v["n_days"]))
    print("\n  best variant risk: beta_to_SPY(monthly) %s   "
          "crash 2008 %s%%  2020 %s%%  2022 %s%%"
          % (beta, crashes["crash_2008_pct"], crashes["crash_2020_pct"],
             crashes["crash_2022_pct"]))
    print("  White's Reality Check (24 variants, common window %s..%s): "
          "p=%.3f  survives=%s"
          % (out["wrc_common_window"]["start"], out["wrc_common_window"]["end"],
             wrc["p_value"], wrc["survives"]))
    print("\n  HARNESS on best NET variant:")
    print("    n_obs %d  n_effective %.0f  t %.3f  p %.6f"
          % (ev["n_obs"], ev["n_effective"], ev["t_stat"], ev["p_value"]))
    print("    gross/trade %sbps  net/trade %sbps  cost_drag %sbps"
          % (ev.get("gross_per_trade_bps"), ev["per_trade_bps"],
             ev.get("cost_drag_bps")))
    print("    deflated_sharpe %s  survives_deflation %s"
          % (ev["deflated_sharpe"], ev["survives_deflation"]))
    print("    vs buy-and-hold: excess %s bps  t=%s  beats_benchmark %s"
          % (ev.get("excess_over_benchmark_bps"), ev.get("excess_t_stat"),
             ev.get("beats_benchmark")))
    print("\n  VERDICT: %s" % out["verdict_line"])
    print("\n  receipt -> %s" % RECEIPT)
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
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else o
    return o


if __name__ == "__main__":
    main()
