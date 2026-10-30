#!/usr/bin/env python3
"""Volatility TERM-STRUCTURE ROLL lab — the short-vol carry, tested honestly.

WHAT THIS MEASURES
------------------
The VIX-futures term structure spends most of its life in CONTANGO: front-month
VIX futures trade above spot VIX and roll DOWN toward it as they approach expiry.
Being short that front month harvests the roll-down — the "short-vol carry" or
"volatility risk premium in futures form". Three tradable expressions are tested:

  A. NAKED SHORT FRONT  — short the short-term VIX-futures ETP (VIXY 2011-, VXX
     2018-). The purest, most ruinous form. Equivalent long: SVXY / -1x short vol.
  B. TERM-STRUCTURE ROLL SPREAD — short front (VXX/VIXY) + long back (VXZ/VIXM),
     dollar-neutral. Harvests the STEEPER front-month roll-down while the long
     back leg hedges part of the vega/level. The "relative-value" short-vol carry.
  C. CONTANGO-CONDITIONAL SHORT FRONT — short front ONLY while VIX < VIX3M
     (curve in contango); flat when inverted. The "smart" version that tries to
     step aside before the tail. Tests whether the roll-yield signal dodges ruin.

Plus a VIX-futures CONTANGO CARRY PROXY built from ^VIX / ^VIX3M: the roll yield
(VIX3M - VIX)/VIX, its sign distribution, and its link to realised short-vol P&L.

Everything scored through quant_validation.py: gross & net Sharpe, DEFLATED
Sharpe against the house search count (~1,123 hypotheses), and — the real point —
the TAIL: worst day, worst month, maxDD, skew, kurtosis, and the exact behaviour
on 2018-02-05/06 (Volmageddon, incl. the single-session loss) and 2020-03 (COVID).

DATA / CAVEATS
--------------
* VXX/VXZ on yfinance are the 2018 Series-B ETNs (history from 2018-01-25). The
  long, continuous front/mid proxies are VIXY / VIXM (2011-), which is what the
  15-year numbers use; VXX/VXZ corroborate on the 2018-2026 overlap.
* SVXY changed from -1x to -0.5x short-vol on 2018-02-27; its buy-hold record
  therefore mixes two exposures and is used only as the cautionary exhibit.
* Costs are modelled, not observed: transaction 10 bps/side, plus a borrow fee on
  the SHORT leg (base 5%/yr, with 15% and 50% stress cases — VIX ETP borrow spikes
  hard in exactly the states that hurt). Expense ratios are already in ETP prices.
* Short-vol return series are violently non-normal; the DSR denominator inflates
  with negative skew + fat tails on purpose. That crushing IS the finding.

PAPER / RESEARCH ONLY. No capital, no orders. Short-vol ETPs are PERMANENTLY
BARRED from deployment on this desk (TOURNAMENT_PREREGISTRATION.md) regardless of
what this measures — this is a knowledge exercise, not a trade proposal.
"""
import json
import os
import time
import warnings

import numpy as np
import pandas as pd

import quant_validation as qv

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(BASE, "reports", "vol_termstructure_lab.json")
CACHE = os.path.join(BASE, "reports", "_vol_ts_cache.parquet")

# House search count. The firm has hunted ~1,123 hypotheses and only basis carry
# survived. Deflate any "discovered" short-vol edge against that whole search.
HOUSE_SEARCH = 1123

TXN_BPS = 10.0          # per side, in bps, on turnover
BORROW_BASE = 0.05      # annualised borrow fee on short-leg notional
BORROW_STRESS = [0.05, 0.15, 0.50]
TRADING_DAYS = 252


def fetch(t, start=None):
    import yfinance as yf
    h = yf.download(t, start=start, period=None if start else "max",
                    interval="1d", auto_adjust=True, progress=False)["Close"]
    if hasattr(h, "columns"):
        h = h.iloc[:, 0]
    return h.dropna().rename(t)


def load_prices():
    tickers = ["VIXY", "VIXM", "VXX", "VXZ", "SVXY", "^VIX", "^VIX3M", "SPY"]
    if os.path.exists(CACHE):
        try:
            df = pd.read_parquet(CACHE)
            if all(c in df.columns for c in tickers):
                return df
        except Exception:
            pass
    series = {t: fetch(t) for t in tickers}
    df = pd.concat(series, axis=1)
    df.columns = tickers
    try:
        df.to_parquet(CACHE)
    except Exception:
        pass
    return df


# ---------------------------------------------------------------------------
# Tail characterisation
# ---------------------------------------------------------------------------
def tail_table(r, name):
    r = pd.Series(r).dropna()
    eq = (1 + r).cumprod()
    dd = eq / eq.cummax() - 1.0
    monthly = (1 + r).resample("ME").prod() - 1.0
    ann = float((1 + r.mean()) ** TRADING_DAYS - 1) * 100
    vol = float(r.std() * np.sqrt(TRADING_DAYS)) * 100
    sharpe = float(r.mean() / r.std() * np.sqrt(TRADING_DAYS)) if r.std() > 0 else 0.0
    m = qv.moments(r.to_numpy())
    return {
        "name": name,
        "n_days": int(len(r)),
        "window": [str(r.index[0].date()), str(r.index[-1].date())],
        "ann_pct": round(ann, 2),
        "vol_pct": round(vol, 1),
        "sharpe_gross": round(sharpe, 3),
        "maxdd_pct": round(float(dd.min()) * 100, 1),
        "worst_day_pct": round(float(r.min()) * 100, 1),
        "worst_day_date": str(r.idxmin().date()),
        "worst_month_pct": round(float(monthly.min()) * 100, 1),
        "worst_month": str(monthly.idxmin().date())[:7] if len(monthly) else None,
        "skew": round(m["skew"], 2),
        "kurtosis_nonexcess": round(m["kurtosis"], 1),
        "pct_up_days": round(float((r > 0).mean()) * 100, 1),
    }


def event_window(r, lo, hi, label):
    r = pd.Series(r).dropna()
    w = r[(r.index >= lo) & (r.index <= hi)]
    if not len(w):
        return {"event": label, "note": "no data in window"}
    cum = float((1 + w).prod() - 1) * 100
    return {
        "event": label,
        "window": [lo, hi],
        "cum_return_pct": round(cum, 1),
        "worst_session_pct": round(float(w.min()) * 100, 1),
        "worst_session_date": str(w.idxmin().date()),
        "n_sessions": int(len(w)),
    }


def deflated(r, name, n_trials, n_eff=None):
    r = pd.Series(r).dropna().to_numpy()
    d = qv.deflated_sharpe_ratio(r, n_trials=n_trials, n_effective=n_eff)
    mtrl = qv.min_track_record_length(r)
    return {
        "name": name,
        "observed_sharpe_per_obs": round(d["observed_sharpe"], 4),
        "sharpe_annualized": round(d["observed_sharpe"] * np.sqrt(TRADING_DAYS), 3),
        "n_trials": n_trials,
        "expected_max_sharpe_under_null": round(d["expected_max_sharpe_under_null"], 4),
        "deflated_sharpe": None if np.isnan(d["deflated_sharpe"]) else round(d["deflated_sharpe"], 4),
        "psr_vs_zero": None if np.isnan(d["psr_vs_zero"]) else round(d["psr_vs_zero"], 4),
        "survives_deflation": bool(d["survives"]),
        "min_track_record_obs": None if not np.isfinite(mtrl) else round(mtrl, 0),
        "min_track_record_years": None if not np.isfinite(mtrl) else round(mtrl / TRADING_DAYS, 1),
        "n_effective": d["n_effective"],
    }


def ar1_neff(r):
    r = pd.Series(r).dropna()
    if len(r) < 10:
        return float(len(r))
    rho = float(r.autocorr(lag=1) or 0.0)
    rho = max(0.0, rho)
    return qv.effective_sample_size(len(r), n_series=1, mean_correlation=0.0,
                                    overlap_factor=(1 + rho) / (1 - rho) if rho < 1 else 5.0)


def cost_short_leg(gross, short_notional=1.0, borrow=BORROW_BASE, turnover_per_day=0.0):
    """Net a short-carry return series: subtract daily borrow on the short notional
    and a transaction drag from turnover. turnover_per_day is fraction of notional
    traded per day (both sides charged at TXN_BPS)."""
    daily_borrow = short_notional * borrow / TRADING_DAYS
    daily_txn = turnover_per_day * 2 * (TXN_BPS / 1e4)
    return pd.Series(gross).dropna() - daily_borrow - daily_txn


# ---------------------------------------------------------------------------
def run(verbose=True):
    if verbose:
        print("Loading volatility term-structure universe...")
    px = load_prices()
    rets = px.pct_change()

    out = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "house_search_count": HOUSE_SEARCH,
           "data_note": "VIXY/VIXM = continuous front/mid VIX-futures ETPs (2011-); "
                        "VXX/VXZ = 2018 Series-B ETNs (2018-); SVXY cautionary only.",
           "cost_model": {"txn_bps_per_side": TXN_BPS, "borrow_base": BORROW_BASE,
                          "borrow_stress": BORROW_STRESS},
           "strategies": {}, "events": {}, "contango_proxy": {}}

    # ---- Front / back building blocks ----
    r_vixy, r_vixm = rets["VIXY"].dropna(), rets["VIXM"].dropna()
    r_vxx, r_vxz = rets["VXX"].dropna(), rets["VXZ"].dropna()
    r_svxy = rets["SVXY"].dropna()
    vix, v3m = px["^VIX"].dropna(), px["^VIX3M"].dropna()

    # =====================================================================
    # A. NAKED SHORT FRONT (short VIXY; short VXX corroboration)
    #    gross short-vol return = -1 * front-ETP return
    # =====================================================================
    for tag, r_front in [("VIXY_2011", r_vixy), ("VXX_2018", r_vxx)]:
        gross = -r_front
        neff = ar1_neff(gross)
        block = {"gross": tail_table(gross, "short %s (gross)" % tag)}
        block["net_by_borrow"] = {}
        for bcost in BORROW_STRESS:
            net = cost_short_leg(gross, 1.0, bcost, turnover_per_day=0.0)
            tt = tail_table(net, "short %s net@borrow%d%%" % (tag, int(bcost * 100)))
            defl = deflated(net, "short_front_%s_b%d" % (tag, int(bcost * 100)),
                            HOUSE_SEARCH, n_eff=neff)
            block["net_by_borrow"]["borrow_%d%%" % int(bcost * 100)] = {
                "sharpe_net": tt["sharpe_gross"], "ann_pct": tt["ann_pct"],
                "maxdd_pct": tt["maxdd_pct"], "deflated_sharpe": defl["deflated_sharpe"],
                "survives": defl["survives_deflation"],
                "min_track_record_years": defl["min_track_record_years"]}
        block["deflation_base_borrow"] = deflated(
            cost_short_leg(gross, 1.0, BORROW_BASE), "short_front_%s" % tag,
            HOUSE_SEARCH, n_eff=neff)
        out["strategies"]["A_naked_short_front_%s" % tag] = block

    # =====================================================================
    # B. TERM-STRUCTURE ROLL SPREAD: short front + long back, dollar-neutral
    #    return = (-r_front) + (r_back)   [short $1 front, long $1 back]
    #    borrow charged on the $1 short front leg only
    # =====================================================================
    for tag, rf, rb in [("VIXY_VIXM_2011", r_vixy, r_vixm),
                        ("VXX_VXZ_2018", r_vxx, r_vxz)]:
        pair = pd.concat([rf, rb], axis=1).dropna()
        gross = (-pair.iloc[:, 0] + pair.iloc[:, 1])
        neff = ar1_neff(gross)
        # dollar-neutral daily re-neutralisation turnover ~ |rf - rb| per day
        turn = (pair.iloc[:, 0] - pair.iloc[:, 1]).abs()
        block = {"gross": tail_table(gross, "roll spread %s (gross)" % tag),
                 "net_by_borrow": {}}
        for bcost in BORROW_STRESS:
            net = gross - 1.0 * bcost / TRADING_DAYS - turn * 2 * (TXN_BPS / 1e4)
            tt = tail_table(net, "spread %s net@borrow%d%%" % (tag, int(bcost * 100)))
            defl = deflated(net, "roll_spread_%s_b%d" % (tag, int(bcost * 100)),
                            HOUSE_SEARCH, n_eff=neff)
            block["net_by_borrow"]["borrow_%d%%" % int(bcost * 100)] = {
                "sharpe_net": tt["sharpe_gross"], "ann_pct": tt["ann_pct"],
                "maxdd_pct": tt["maxdd_pct"], "worst_day_pct": tt["worst_day_pct"],
                "skew": tt["skew"], "kurtosis": tt["kurtosis_nonexcess"],
                "deflated_sharpe": defl["deflated_sharpe"],
                "survives": defl["survives_deflation"],
                "min_track_record_years": defl["min_track_record_years"]}
        net_base = gross - 1.0 * BORROW_BASE / TRADING_DAYS - turn * 2 * (TXN_BPS / 1e4)
        block["net_base_tail"] = tail_table(net_base, "roll spread %s net (base)" % tag)
        block["deflation_base_borrow"] = deflated(net_base, "roll_spread_%s" % tag,
                                                  HOUSE_SEARCH, n_eff=neff)
        block["_net_base_series"] = net_base  # kept for event windows
        out["strategies"]["B_roll_spread_%s" % tag] = block

    # =====================================================================
    # C. CONTANGO-CONDITIONAL SHORT FRONT (short VIXY only when VIX<VIX3M)
    # =====================================================================
    tv = pd.concat({"VIXY": r_vixy, "VIX": vix, "V3M": v3m}, axis=1).dropna()
    contango = (tv["VIX"] < tv["V3M"]).shift(1).fillna(False)  # act on yesterday
    gross_c = (-tv["VIXY"]).where(contango, 0.0)
    flips = (contango != contango.shift(1))
    neff = ar1_neff(gross_c[contango])
    net_c = gross_c - 1.0 * BORROW_BASE / TRADING_DAYS * contango.astype(float) \
        - flips.astype(float) * 2 * (TXN_BPS / 1e4)
    out["strategies"]["C_contango_conditional_short_front"] = {
        "pct_days_short": round(float(contango.mean()) * 100, 1),
        "flips": int(flips.sum()),
        "gross": tail_table(gross_c, "contango-only short front (gross)"),
        "net_base_tail": tail_table(net_c, "contango-only short front (net)"),
        "deflation_base_borrow": deflated(net_c, "contango_conditional",
                                          HOUSE_SEARCH, n_eff=neff),
        "_net_base_series": net_c,
    }

    # =====================================================================
    # VIX-futures contango carry proxy (roll yield from VIX / VIX3M)
    # =====================================================================
    cp = pd.concat({"VIX": vix, "V3M": v3m}, axis=1).dropna()
    roll_yield = (cp["V3M"] - cp["VIX"]) / cp["VIX"]  # >0 = contango
    out["contango_proxy"] = {
        "window": [str(cp.index[0].date()), str(cp.index[-1].date())],
        "pct_days_contango": round(float((roll_yield > 0).mean()) * 100, 1),
        "mean_roll_yield_pct": round(float(roll_yield.mean()) * 100, 2),
        "median_roll_yield_pct": round(float(roll_yield.median()) * 100, 2),
        "worst_inversion_pct": round(float(roll_yield.min()) * 100, 1),
        "worst_inversion_date": str(roll_yield.idxmin().date()),
        "note": "Roll yield = (VIX3M-VIX)/VIX. Positive ~85-90% of days (contango) "
                "is the carry; the rare deep inversions are where short-vol dies.",
    }

    # =====================================================================
    # Event studies: Volmageddon 2018-02 and COVID 2020-03
    # =====================================================================
    events = {}
    named = {
        "A_short_VIXY_gross": -r_vixy,
        "A_short_VIXY_net_base": cost_short_leg(-r_vixy, 1.0, BORROW_BASE),
        "B_roll_spread_VIXY_VIXM_net": out["strategies"]
            ["B_roll_spread_VIXY_VIXM_2011"]["_net_base_series"],
        "C_contango_conditional_net": net_c,
        "SVXY_buyhold": r_svxy,
    }
    for k, series in named.items():
        events[k] = {
            "volmageddon_2018": event_window(series, "2018-02-01", "2018-02-28",
                                             "Volmageddon (Feb 2018)"),
            "feb5_2018_single_session": _single_day(series, "2018-02-05"),
            "feb6_2018_single_session": _single_day(series, "2018-02-06"),
            "covid_2020_03": event_window(series, "2020-02-20", "2020-03-31",
                                          "COVID crash (Feb-Mar 2020)"),
        }
    out["events"] = events

    # SVXY cautionary exhibit
    out["svxy_cautionary"] = tail_table(r_svxy, "SVXY buy-hold (mixed -1x/-0.5x)")

    # strip private series before dump
    for s in out["strategies"].values():
        s.pop("_net_base_series", None)

    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w") as f:
        json.dump(out, f, indent=2, default=str)
    return out


def _single_day(series, date):
    s = pd.Series(series).dropna()
    hit = s[s.index == date]
    if not len(hit):
        return {"date": date, "note": "no session"}
    return {"date": date, "return_pct": round(float(hit.iloc[0]) * 100, 1)}


def report(o):
    print("=" * 84)
    print("VOLATILITY TERM-STRUCTURE ROLL — short-vol carry, after-cost & tail-honest")
    print("=" * 84)
    print("House search count deflated against: %d hypotheses\n" % o["house_search_count"])

    cp = o["contango_proxy"]
    print("CONTANGO CARRY PROXY (VIX/VIX3M, %s->%s):" % tuple(cp["window"]))
    print("  contango %.1f%% of days · mean roll yield %.2f%% · worst inversion %.1f%% (%s)\n"
          % (cp["pct_days_contango"], cp["mean_roll_yield_pct"],
             cp["worst_inversion_pct"], cp["worst_inversion_date"]))

    for key, s in o["strategies"].items():
        print("-" * 84)
        print(key)
        if "gross" in s:
            g = s["gross"]
            print("  gross: ann %.1f%% · Sharpe %.2f · maxDD %.1f%% · worst day %.1f%% (%s) "
                  "· skew %.2f · kurt %.0f"
                  % (g["ann_pct"], g["sharpe_gross"], g["maxdd_pct"], g["worst_day_pct"],
                     g["worst_day_date"], g["skew"], g["kurtosis_nonexcess"]))
        if "net_by_borrow" in s:
            for b, v in s["net_by_borrow"].items():
                print("  net @ %-9s Sharpe %5.2f · ann %6.1f%% · maxDD %6.1f%% · "
                      "DSR %s · survives=%s"
                      % (b, v["sharpe_net"], v["ann_pct"], v["maxdd_pct"],
                         v["deflated_sharpe"], v["survives"]))
        d = s.get("deflation_base_borrow", {})
        if d:
            print("  deflation(base borrow): DSR=%s survives=%s · minTrackRecord=%s yrs"
                  % (d["deflated_sharpe"], d["survives_deflation"],
                     d["min_track_record_years"]))

    print("\n" + "=" * 84)
    print("EVENT STUDIES (net where noted)")
    print("=" * 84)
    for k, ev in o["events"].items():
        vm = ev["volmageddon_2018"]
        cov = ev["covid_2020_03"]
        f5, f6 = ev["feb5_2018_single_session"], ev["feb6_2018_single_session"]
        print("%s:" % k)
        print("  Volmageddon Feb18: cum %s%% · worst session %s%% (%s)"
              % (vm.get("cum_return_pct"), vm.get("worst_session_pct"),
                 vm.get("worst_session_date")))
        print("  5-Feb-2018: %s%% · 6-Feb-2018: %s%%"
              % (f5.get("return_pct"), f6.get("return_pct")))
        print("  COVID Feb-Mar20: cum %s%% · worst session %s%% (%s)"
              % (cov.get("cum_return_pct"), cov.get("worst_session_pct"),
                 cov.get("worst_session_date")))
    sv = o["svxy_cautionary"]
    print("\nSVXY buy-hold (cautionary): ann %.1f%% · maxDD %.1f%% · worst day %.1f%% (%s)"
          % (sv["ann_pct"], sv["maxdd_pct"], sv["worst_day_pct"], sv["worst_day_date"]))
    print("\nReport -> %s" % REPORT)


if __name__ == "__main__":
    report(run())
