#!/usr/bin/env python3
"""leadlag_lab.py — Honest COLD test of CROSS-ASSET LEAD-LAG: do credit, copper,
oil and the yield curve LEAD equity index returns?

RESEARCH ONLY. Paper desk. No orders, no capital, no pre-registration, no secrets.
Read-only w.r.t. the rest of the desk. Writes only its own JSON receipt + note.

WHY DISTINCT
------------
Every equity mechanism the house has killed is either price-history-of-the-asset
(trend, mean-rev, seasonality) or CROSS-SECTIONAL (value, carry, L/S factors).
This is TIME-SERIES CROSS-ASSET prediction: one asset class signalling ANOTHER.
Named priors: (1) credit leads equities — HY spreads widen before equity drawdowns
(HYG momentum as the tradable proxy); (2) "Dr. Copper" — industrial-metal demand
leads the cycle; (3) oil as a growth/inflation impulse; (4) the 10y-2y curve slope
as the classic recession lead. None of these signal families has been tested here
as a timing rule on SPY.

THE HONEST DATA PROBLEM (read before trusting any number)
---------------------------------------------------------
* HYG only exists since 2007 — the credit variants see one full cycle plus 2020/2022.
* Copper is HG=F front-month via yfinance (fallback COPX from 2010 if the future
  fails) — front-month splices embed roll noise; we state which source was used.
* Oil is CL=F front-month (fallback USO, which bleeds contango — stated if used).
* Curve slope is FRED T10Y2Y (daily, free, real). The curve's lead time is famously
  LONG and variable (12-24 months); a 1-3 month momentum rule is a crude harvest.
* SPY/HYG "total return" = yfinance auto-adjusted closes (dividends folded in).
* Cash leg earns 0 — no T-bill accrual — which UNDERSTATES long/cash returns a
  touch; conservative and stated.
* Long/cash only — NO shorts held, so the 1%/3% short-borrow sweep is vacuous
  here and recorded as such.

DESIGN
------
  signals  : HYG k-month total-return momentum; HG=F k-month change; CL=F k-month
             change; T10Y2Y k-month change (steepening = risk-on), k in {1m, 3m}
  rule     : signal > 0  =>  LONG SPY next period, else CASH earning 0
  rebalance: MONTHLY for all 8 signal/lookback combos, plus WEEKLY for the four
             1-month-lookback signals  =>  12 variants, every one counted
  costs    : 5 bps per side charged ONLY on position changes (actual turnover)
  extras   : raw predictive Spearman IC of each signal vs next-month SPY (the
             descriptive fact, separate from the strategies); curve-signal IC vs
             next-month IEF as context; beta to SPY; crash windows 2008/2020/2022
  scoring  : quant_validation on the best NET variant with n_trials = house prior
             (1179) + all 12 variants tried here, benchmark = SPY buy-and-hold
             over the same periods, n_effective from qv.effective_bets using the
             measured position-persistence (fraction of periods the position was
             unchanged, used as the AR(1) rho proxy — documented choice).

NON-NEGOTIABLE: report real output, win or lose. A mechanism that dies in the
harness is a SUCCESS of the process.
"""
import json
import math
import os
import urllib.request
import io
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy import stats as sstats

import quant_validation as qv

CACHE = "/private/tmp/claude-501/-Users-test/326d8bdd-e63d-4126-a322-4582de2ba921/scratchpad/leadlag_lab_cache"
os.makedirs(CACHE, exist_ok=True)
RECEIPT = "reports/leadlag_lab.json"
NOTE = "reports/research_leadlag.md"

HOUSE_PRIOR_TRIALS = 1179
COST_BPS_SIDE = 5.0          # per side, charged on position CHANGES only
START = "1999-01-01"


# ---------------------------------------------------------------------------
# Data helpers (cached so re-runs are cheap and deterministic)
# ---------------------------------------------------------------------------
def yf_daily(ticker, start=START):
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
    s.index = pd.DatetimeIndex(s.index).tz_localize(None)
    s.to_frame().to_csv(path)
    return s


def fred_series(series_id):
    """FRED fetcher (pattern copied from fx_carry_lab.fred_monthly). T10Y2Y is
    daily; caller resamples."""
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


def load_with_fallback(primary, fallback):
    """Try the primary ticker; fall back honestly, recording which was used."""
    try:
        s = yf_daily(primary)
        if s.dropna().size > 500:
            return s, primary
    except Exception:
        pass
    s = yf_daily(fallback)
    return s, fallback


# ---------------------------------------------------------------------------
# Strategy machinery
# ---------------------------------------------------------------------------
def timing_variant(signal_mom, asset_ret):
    """Long/cash rule: mom>0 at end of t => hold asset over t+1, else cash (0).
    Costs COST_BPS_SIDE per position CHANGE. Returns dict of aligned series."""
    df = pd.concat([signal_mom.rename("mom"), asset_ret.rename("ret")], axis=1).dropna()
    if len(df) < 24:
        return None
    ind = (df["mom"] > 0).astype(float)
    pos = ind.shift(1)                       # position HELD over period t
    valid = pos.notna()
    pos = pos[valid]
    ret = df["ret"][valid]
    turnover = pos.diff().abs().fillna(pos.iloc[0])   # first entry counts as a trade
    gross = pos * ret
    net = gross - turnover * (COST_BPS_SIDE / 10000.0)
    frac_unchanged = float((pos.diff().fillna(0.0) == 0).mean())
    return {"net": net, "gross": gross, "pos": pos, "turnover": turnover,
            "bench": ret, "frac_unchanged": frac_unchanged}


def spearman_ic(signal_mom, asset_ret):
    """Predictive Spearman IC: signal at t vs asset return over t+1, own t-stat."""
    df = pd.concat([signal_mom.rename("s"), asset_ret.shift(-1).rename("f")],
                   axis=1).dropna()
    n = len(df)
    if n < 24:
        return None
    ic, p = sstats.spearmanr(df["s"], df["f"])
    t = ic * math.sqrt((n - 2) / max(1e-12, 1.0 - ic * ic))
    return {"ic": round(float(ic), 4), "t_stat": round(float(t), 3),
            "p_value": round(float(p), 4), "n": int(n)}


def maxdd(s):
    eq = (1 + s).cumprod()
    return float((eq / eq.cummax() - 1).min())


def crash(s, lo, hi):
    w = s[(s.index >= lo) & (s.index <= hi)]
    return round(float((1 + w).prod() - 1) * 100, 2) if len(w) else None


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


def main():
    # ---------------- data ----------------
    spy_d = yf_daily("SPY")
    hyg_d = yf_daily("HYG")
    ief_d = yf_daily("IEF")
    cop_d, cop_src = load_with_fallback("HG=F", "COPX")
    oil_d, oil_src = load_with_fallback("CL=F", "USO")
    curve_d = fred_series("T10Y2Y")

    def to_freq(s, freq):
        return s.dropna().resample(freq).last().dropna()

    results = {}
    for freq, tag, ppy in [("ME", "monthly", 12.0), ("W-FRI", "weekly", 52.0)]:
        spy = to_freq(spy_d, freq)
        spy_ret = spy.pct_change().dropna()
        results[tag] = {
            "spy_ret": spy_ret,
            "hyg": to_freq(hyg_d, freq),
            "copper": to_freq(cop_d, freq),
            "oil": to_freq(oil_d, freq),
            "curve": to_freq(curve_d, freq),
            "ppy": ppy,
        }
    ief_m = to_freq(ief_d, "ME")
    ief_ret_m = ief_m.pct_change().dropna()

    def make_mom(level, k, kind):
        """kind 'pct' for prices (momentum = k-period % change);
        'diff' for the curve slope in %-points (change = steepening if > 0)."""
        return level.pct_change(k) if kind == "pct" else level.diff(k)

    SIGNALS = [("credit_hyg", "hyg", "pct"),
               ("copper", "copper", "pct"),
               ("oil", "oil", "pct"),
               ("curve_t10y2y", "curve", "diff")]

    # lookbacks in PERIODS of each frequency: monthly {1,3}; weekly 1-month = 4wk
    GRID = [("monthly", 1, "1m"), ("monthly", 3, "3m"), ("weekly", 4, "1m")]

    variants, trial_sharpes, series_store = [], [], {}
    n_here = 0
    for sname, skey, kind in SIGNALS:
        for freqtag, k, lktag in GRID:
            n_here += 1
            R = results[freqtag]
            mom = make_mom(R[skey], k, kind)
            tv = timing_variant(mom, R["spy_ret"])
            vname = "%s_%s_%s" % (sname, lktag, freqtag)
            if tv is None:
                variants.append({"name": vname, "note": "insufficient overlap"})
                continue
            ppy = R["ppy"]
            m = qv.moments(tv["net"].values)
            trial_sharpes.append(m["sharpe"])
            series_store[vname] = (tv, ppy, freqtag)
            bench = tv["bench"]
            variants.append({
                "name": vname, "signal": sname, "lookback": lktag,
                "rebalance": freqtag,
                "start": str(tv["net"].index[0].date()),
                "end": str(tv["net"].index[-1].date()),
                "n_periods": int(tv["net"].size),
                "pct_time_long": round(float(tv["pos"].mean()) * 100, 1),
                "trades_total": int(round(float(tv["turnover"].sum()))),
                "avg_turnover_per_period": round(float(tv["turnover"].mean()), 4),
                "frac_periods_unchanged": round(tv["frac_unchanged"], 4),
                "net_sharpe_ann": round(m["sharpe"] * math.sqrt(ppy), 3),
                "gross_sharpe_ann": round(
                    qv.moments(tv["gross"].values)["sharpe"] * math.sqrt(ppy), 3),
                "net_cagr_pct": round(((1 + tv["net"]).prod()
                                       ** (ppy / max(1, tv["net"].size)) - 1) * 100, 2),
                "bh_spy_sharpe_ann_same_window": round(
                    qv.moments(bench.values)["sharpe"] * math.sqrt(ppy), 3),
                "bh_spy_cagr_pct_same_window": round(((1 + bench).prod()
                                       ** (ppy / max(1, bench.size)) - 1) * 100, 2),
                "maxdd_pct": round(maxdd(tv["net"]) * 100, 2),
            })

    # ---------------- descriptive fact: predictive Spearman ICs (monthly) -----
    ics = {}
    Rm = results["monthly"]
    for sname, skey, kind in SIGNALS:
        for k, lktag in [(1, "1m"), (3, "3m")]:
            mom = make_mom(Rm[skey], k, kind)
            ics["%s_%s_vs_next_month_SPY" % (sname, lktag)] = spearman_ic(
                mom, Rm["spy_ret"])
    # context only: curve signal vs next-month IEF (secondary target)
    for k, lktag in [(1, "1m"), (3, "3m")]:
        mom = make_mom(Rm["curve"], k, "diff")
        ics["curve_t10y2y_%s_vs_next_month_IEF" % lktag] = spearman_ic(
            mom, ief_ret_m)

    # ---------------- pick best NET variant, run the harness ------------------
    scored = sorted([v for v in variants if "net_sharpe_ann" in v],
                    key=lambda v: v["net_sharpe_ann"], reverse=True)
    total_trials = HOUSE_PRIOR_TRIALS + n_here

    best = scored[0]
    tv, ppy, freqtag = series_store[best["name"]]
    net, gross, bench = tv["net"], tv["gross"], tv["bench"]
    # n_effective: monthly timing positions are AUTOCORRELATED. Proxy the AR(1)
    # rho of the position series with the measured fraction of periods the
    # position was UNCHANGED (documented choice; e.g. unchanged 90% of the time
    # -> rho 0.90 -> heavy independent-bet discount).
    rho_proxy = tv["frac_unchanged"]
    eff = qv.effective_bets(int(net.size), rho_proxy)
    n_eff = eff["independent_bets"]
    trades_per_period = max(float(tv["turnover"].mean()), 1e-6)
    best_eval = qv.evaluate(
        best["name"], net.values, n_trials=total_trials,
        trial_sharpes=trial_sharpes, periods_per_year=ppy,
        trades_per_period=trades_per_period, gross_returns=gross.values,
        n_effective=n_eff, benchmark_returns=bench.values)

    # White's Reality Check across the 8 MONTHLY variants on their common window
    # (weekly excluded: different grid). Harness expects rows = strategies.
    monthly_keys = [k for k in series_store if k.endswith("monthly")]
    wrc = None
    if len(monthly_keys) >= 2:
        common = None
        for k in monthly_keys:
            idx = series_store[k][0]["net"].index
            common = idx if common is None else common.intersection(idx)
        if common is not None and len(common) >= 24:
            Mrows = np.vstack([series_store[k][0]["net"].reindex(common).values
                               for k in monthly_keys])
            wrc = qv.whites_reality_check(Mrows)
            wrc["note"] = ("monthly variants only, common window %s..%s; "
                           "rows = strategies per the harness source"
                           % (common[0].date(), common[-1].date()))

    # ---------------- beta to SPY (monthly regression) ------------------------
    spy_m_ret = results["monthly"]["spy_ret"]
    if freqtag == "weekly":
        strat_m = (1 + net).resample("ME").prod() - 1.0
    else:
        strat_m = net
    j = pd.concat([strat_m.rename("s"), spy_m_ret.rename("m")], axis=1).dropna()
    beta_to_spy = (round(float(np.polyfit(j["m"].values, j["s"].values, 1)[0]), 3)
                   if len(j) >= 24 and float(np.nanstd(j["m"].values)) > 0 else None)

    # ---------------- crash windows for best variant --------------------------
    crashes = {
        "crash_2008_pct": crash(net, pd.Timestamp("2008-06-01"), pd.Timestamp("2009-03-31")),
        "crash_2020_pct": crash(net, pd.Timestamp("2020-02-01"), pd.Timestamp("2020-04-30")),
        "crash_2022_pct": crash(net, pd.Timestamp("2022-01-01"), pd.Timestamp("2022-10-31")),
        "spy_2008_pct": crash(bench, pd.Timestamp("2008-06-01"), pd.Timestamp("2009-03-31")),
        "spy_2020_pct": crash(bench, pd.Timestamp("2020-02-01"), pd.Timestamp("2020-04-30")),
        "spy_2022_pct": crash(bench, pd.Timestamp("2022-01-01"), pd.Timestamp("2022-10-31")),
    }

    out = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "mechanism": ("cross-asset lead-lag: credit (HYG), copper, oil, 10y-2y curve "
                      "momentum as long/cash timing on SPY — time-series cross-asset "
                      "prediction, distinct from killed cross-sectional factors"),
        "data_sources": {
            "spy": "SPY yfinance auto-adjusted (total-return proxy)",
            "credit": "HYG yfinance auto-adjusted (since 2007)",
            "copper": cop_src + (" (front-month future)" if cop_src == "HG=F"
                                 else " (FALLBACK — miners ETF, not the metal)"),
            "oil": oil_src + (" (front-month future)" if oil_src == "CL=F"
                              else " (FALLBACK — USO, contango bleed)"),
            "curve": "FRED T10Y2Y (daily, resampled)",
            "ief_context": "IEF yfinance auto-adjusted",
            "cash_leg": "earns 0 (no T-bill accrual — conservative, understates long/cash)",
        },
        "cost_bps_side": COST_BPS_SIDE,
        "short_borrow_sweep": "N/A — long/cash only, no shorts held in any variant",
        "house_prior_trials": HOUSE_PRIOR_TRIALS,
        "lab_variants": n_here,
        "total_deflation_trials": total_trials,
        "predictive_spearman_ics": ics,
        "variants": variants,
        "best_variant": best["name"],
        "best_position_persistence_rho_proxy": round(rho_proxy, 4),
        "best_effective_bets": eff,
        "best_trades_per_period": round(trades_per_period, 4),
        "beta_to_spy": beta_to_spy,
        "crash_windows_best": crashes,
        "whites_reality_check": wrc,
        "best_evaluate": best_eval,
        "verdict_line": qv.verdict_line(best_eval),
    }

    with open(RECEIPT, "w") as f:
        json.dump(_j(out), f, indent=2)

    print("=" * 80)
    print("CROSS-ASSET LEAD-LAG LAB — credit / copper / oil / curve  ->  SPY timing")
    print("=" * 80)
    print("  sources : copper=%s  oil=%s  curve=FRED T10Y2Y  credit=HYG" % (cop_src, oil_src))
    print("  costs   : %.0f bps/side on position changes only" % COST_BPS_SIDE)
    print("  deflation: %d trials (house %d + %d here)" % (total_trials,
                                                           HOUSE_PRIOR_TRIALS, n_here))
    print("\n  DESCRIPTIVE — predictive Spearman IC vs next-month SPY:")
    for k, v in ics.items():
        if v:
            print("    %-42s IC %+0.4f  t %+0.2f  p %.3f  n=%d"
                  % (k, v["ic"], v["t_stat"], v["p_value"], v["n"]))
    print("\n  VARIANTS (annualised net Sharpe, best first; b&h = SPY same window):")
    for v in scored:
        print("    %-28s netSh %+0.3f  grossSh %+0.3f  CAGR %+0.2f%%  "
              "b&hSh %+0.3f  long %5.1f%%  trades %3d  n=%d"
              % (v["name"], v["net_sharpe_ann"], v["gross_sharpe_ann"],
                 v["net_cagr_pct"], v["bh_spy_sharpe_ann_same_window"],
                 v["pct_time_long"], v["trades_total"], v["n_periods"]))
    e = best_eval
    print("\n  crash (best) 2008 %s%% (SPY %s%%)  2020 %s%% (SPY %s%%)  2022 %s%% (SPY %s%%)"
          % (crashes["crash_2008_pct"], crashes["spy_2008_pct"],
             crashes["crash_2020_pct"], crashes["spy_2020_pct"],
             crashes["crash_2022_pct"], crashes["spy_2022_pct"]))
    if wrc:
        print("  White's RC (8 monthly variants): p=%s survives %s"
              % (wrc["p_value"], wrc["survives"]))
    print("  beta_to_spy (best, monthly): %s" % beta_to_spy)
    print("\n  HARNESS on best NET variant (%s):" % best["name"])
    print("    n_obs %s  n_effective %s  t %s  p %s"
          % (e["n_obs"], e["n_effective"], e["t_stat"], e["p_value"]))
    print("    gross/trade %sbps  net/trade %sbps  cost_drag %sbps"
          % (e.get("gross_per_trade_bps"), e["per_trade_bps"], e.get("cost_drag_bps")))
    print("    excess_over_benchmark %sbps  excess_t %s  beats_benchmark %s"
          % (e.get("excess_over_benchmark_bps"), e.get("excess_t_stat"),
             e.get("beats_benchmark")))
    print("    deflated_sharpe %s  survives_deflation %s"
          % (e["deflated_sharpe"], e["survives_deflation"]))
    print("\n  VERDICT: %s" % out["verdict_line"])
    print("\n  receipt -> %s" % RECEIPT)
    return out


if __name__ == "__main__":
    main()
