#!/usr/bin/env python3
"""Volatility universe lab — the last untouched asset class, tested honestly.

WHY THIS WAS EXCLUDED UNTIL NOW, AND WHAT CHANGED
-------------------------------------------------
Backtesting options needs historical chains (paid). But Cboe publishes its
option-strategy BENCHMARK INDICES — the official, audited track records of the
two most-documented options strategies on earth — and yfinance serves them:

    ^BXM  S&P 500 BuyWrite (covered calls)   history to 1988
    ^PUT  S&P 500 PutWrite (cash-secured)     history to 1996

Plus the VIX complex (^VIX 1990-, ^VIX3M 2006-) and the short-vol ETP SVXY with
the February 2018 "Volmageddon" in-sample — the single most instructive day in
retail volatility selling.

WHAT IS TESTED
--------------
1. The variance risk premium in its tradable forms: BXM and PUT vs SPY —
   return, Sharpe, drawdown, and the post-2012 decay the literature reports.
2. VIX term-structure regime switch: SPY when VIX < VIX3M (contango/calm),
   IEF when inverted (stress). The documented "vol curve as smoke alarm".
3. SVXY buy-and-hold — the cautionary exhibit, with 2018-02-05 called out.

Everything scored through the harness vs buy-and-hold. Caveats stated: BXM/PUT
are index track records (no fees/slippage of a real implementation); SPY here is
total-return via adjusted closes, the Cboe indices embed their own conventions.

PAPER ONLY. Research, not advice.
"""
import json
import os
import time
import warnings

import numpy as np

import quant_validation as qv

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(BASE, "reports", "vol_lab.json")


def fetch(t, start=None):
    import yfinance as yf
    h = yf.download(t, start=start, period=None if start else "max",
                    interval="1d", auto_adjust=True, progress=False)["Close"]
    if hasattr(h, "columns"):
        h = h.iloc[:, 0]
    return h.dropna()


def stats(r, name, hold_days=1):
    r = r.dropna()
    n = len(r)
    ann = float((1 + r.mean()) ** 252 - 1) * 100
    vol = float(r.std() * np.sqrt(252)) * 100
    eq = (1 + r).cumprod()
    dd = float((eq / eq.cummax() - 1).min()) * 100
    sharpe = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0
    return {"name": name, "n_days": n, "ann_pct": round(ann, 2),
            "vol_pct": round(vol, 1), "sharpe": round(sharpe, 3),
            "maxdd_pct": round(dd, 1)}


def run(verbose=True):
    if verbose:
        print("Fetching the volatility universe...")
    spy = fetch("SPY")
    bxm = fetch("^BXM")
    put = fetch("^PUT")
    vix = fetch("^VIX")
    v3m = fetch("^VIX3M")
    svxy = fetch("SVXY")
    ief = fetch("IEF")

    out = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "sections": {}}

    # ---- 1. VRP benchmarks vs SPY, common window ----
    import pandas as pd
    common = pd.concat({"SPY": spy, "BXM": bxm, "PUT": put}, axis=1).dropna()
    R = common.pct_change().dropna()
    rows = [stats(R[c], c) for c in ["SPY", "BXM", "PUT"]]
    # decay split at 2012 (literature's inflection)
    split = {}
    for c in ["SPY", "BXM", "PUT"]:
        pre = R[c][R.index < "2012-01-01"]
        post = R[c][R.index >= "2012-01-01"]
        split[c] = {"pre2012_ann": round(float((1 + pre.mean()) ** 252 - 1) * 100, 2),
                    "post2012_ann": round(float((1 + post.mean()) ** 252 - 1) * 100, 2),
                    "pre_sharpe": round(float(pre.mean() / pre.std() * np.sqrt(252)), 2),
                    "post_sharpe": round(float(post.mean() / post.std() * np.sqrt(252)), 2)}
    out["sections"]["vrp_benchmarks"] = {
        "window": [str(common.index[0].date()), str(common.index[-1].date())],
        "full": rows, "pre_post_2012": split,
        "caveat": "Cboe index track records — a real implementation adds fees, "
                  "spreads and assignment slippage on top."}

    # ---- 2. VIX term-structure regime switch ----
    tv = pd.concat({"SPY": spy, "IEF": ief, "VIX": vix, "V3M": v3m}, axis=1).dropna()
    rr = tv[["SPY", "IEF"]].pct_change()
    calm = (tv["VIX"] < tv["V3M"]).shift(1)          # decide on yesterday's close
    strat = rr["SPY"].where(calm, rr["IEF"]).dropna()
    flips = int((calm != calm.shift(1)).sum())
    cost = 0.0010
    strat_net = strat - (calm != calm.shift(1)).reindex(strat.index).fillna(False) * cost
    n_trials = 3   # this switch + the two benchmark comparisons constitute the family
    ev = qv.evaluate("vix_regime_switch", strat_net.to_numpy(), n_trials=n_trials,
                     periods_per_year=252, trades_per_period=1.0,
                     benchmark_returns=rr["SPY"].reindex(strat_net.index).to_numpy())
    out["sections"]["vix_regime"] = {
        "window": [str(tv.index[0].date()), str(tv.index[-1].date())],
        "strategy": stats(strat_net, "SPY-when-contango else IEF"),
        "spy_same_window": stats(rr["SPY"].dropna(), "SPY"),
        "pct_days_calm": round(float(calm.mean()) * 100, 1),
        "regime_flips": flips,
        "deflated": {"dsr": ev["deflated_sharpe"], "beats_spy": ev.get("beats_benchmark"),
                     "excess_t": ev.get("excess_t_stat")},
    }

    # ---- 3. SVXY — the cautionary exhibit ----
    sr = svxy.pct_change().dropna()
    worst_day = sr.idxmin()
    out["sections"]["short_vol_etp"] = {
        "stats": stats(sr, "SVXY buy-hold"),
        "worst_day": {"date": str(worst_day.date()),
                      "return_pct": round(float(sr.min()) * 100, 1)},
        "note": "The premium is real and the tail owns you: one session can erase "
                "years. This is why no vol-selling sleeve gets leverage, ever."}

    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w") as f:
        json.dump(out, f, indent=2)
    return out


def report(o):
    s1 = o["sections"]["vrp_benchmarks"]
    print("=" * 82)
    print("VOLATILITY UNIVERSE — variance risk premium, tested on Cboe's own records")
    print("=" * 82)
    print("1) BUYWRITE / PUTWRITE vs SPY  (%s -> %s)" % tuple(s1["window"]))
    print("   %-6s %8s %8s %8s %9s" % ("", "ann %", "vol %", "Sharpe", "maxDD %"))
    for r in s1["full"]:
        print("   %-6s %7.2f%% %7.1f%% %8.2f %8.1f%%"
              % (r["name"], r["ann_pct"], r["vol_pct"], r["sharpe"], r["maxdd_pct"]))
    print("   pre/post-2012 Sharpe: " + "  ".join(
        "%s %.2f->%.2f" % (c, s1["pre_post_2012"][c]["pre_sharpe"],
                           s1["pre_post_2012"][c]["post_sharpe"])
        for c in ["SPY", "BXM", "PUT"]))
    print("   caveat: %s" % s1["caveat"])

    s2 = o["sections"]["vix_regime"]
    print("\n2) VIX TERM-STRUCTURE SWITCH  (%s -> %s, %s%% of days calm, %d flips)"
          % (s2["window"][0], s2["window"][1], s2["pct_days_calm"], s2["regime_flips"]))
    a, b = s2["strategy"], s2["spy_same_window"]
    print("   switch: %.2f%% ann, Sharpe %.2f, maxDD %.1f%%   |   SPY: %.2f%%, %.2f, %.1f%%"
          % (a["ann_pct"], a["sharpe"], a["maxdd_pct"],
             b["ann_pct"], b["sharpe"], b["maxdd_pct"]))
    print("   deflated: DSR %s · beats SPY: %s (excess t=%s)"
          % (s2["deflated"]["dsr"], s2["deflated"]["beats_spy"], s2["deflated"]["excess_t"]))

    s3 = o["sections"]["short_vol_etp"]
    print("\n3) SHORT-VOL ETP (SVXY buy-hold): %.2f%% ann, maxDD %.1f%% — worst day %s: %s%%"
          % (s3["stats"]["ann_pct"], s3["stats"]["maxdd_pct"],
             s3["worst_day"]["date"], s3["worst_day"]["return_pct"]))
    print("   %s" % s3["note"])
    print("\nReport -> %s" % REPORT)


if __name__ == "__main__":
    report(run())
