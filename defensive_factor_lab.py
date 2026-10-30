#!/usr/bin/env python3
"""Defensive / quality equity factor lab — the honest test of low-vol, low-beta and
quality, benchmarked against the passive index and scrubbed of repackaged beta.

WHY THIS EXISTS (AND WHY IT IS A DIFFERENT QUESTION)
----------------------------------------------------
The desk has already killed cross-sectional VALUE and MOMENTUM in equities (360
hypotheses; equity_lab's own winner, trend_50_200, beat the index in raw CAGR but
its excess-over-SPY t-stat was 1.29 — not significant). Those are OFFENSIVE factors.

Defensive factors are a genuinely different claim. The low-volatility "anomaly" is
that low-risk stocks have historically earned AS MUCH as high-risk ones — a Sharpe
story, not a return story. So the naive test ("does USMV beat SPY in CAGR?") is the
WRONG test: a low-beta sleeve is SUPPOSED to lag a rising index in total return while
delivering a smoother ride. The right questions are:

  1. Does it beat the index on a RISK-ADJUSTED basis (higher Sharpe)?
  2. Is there ALPHA once you strip out beta — i.e. a positive CAPM intercept with a
     real t-stat — or is the whole thing just "less than 100% SPY"?  A sleeve you can
     replicate with `beta * SPY + (1-beta) * cash` is not an edge; it is an asset
     allocation you could do for free.
  3. Does anything survive the desk's cumulative search deflation (~1,123 trials)?

If the only thing on offer is lower beta, the honest verdict is: buy less SPY. That
costs nothing and needs no lab.

WHAT IT MEASURES
----------------
Sleeve A — TRADEABLE ETF PROXIES: USMV, SPLV, QUAL, EFAV long-only vs SPY (and VT),
           over each ETF's full life. Real, investable, net of a holding cost.
Sleeve B — CROSS-SECTIONAL PRICE FACTORS: on ~50 liquid large caps + sector ETFs,
           rank by low realized vol and low beta, long top-quintile, monthly rebal,
           10 bps/side. Price-only, so strictly no fundamental look-ahead.

For every sleeve: gross/net Sharpe, DEFLATED Sharpe (against the house search count),
maxDD, excess-vs-SPY with t-stat, AND the CAPM alpha/beta decomposition. Plus the
effective_bets kill-test (a low-vol book barely turns over — 180 rebalances is not
180 independent bets).

HONEST LIMITS
-------------
* ETF proxies only exist from 2011-2013. That is ONE regime (post-GFC bull + COVID +
  AI melt-up) — the single most hostile backdrop possible for a defensive factor,
  which shines in drawdowns this window mostly lacks. Measured, not fixed.
* Quality is tested via the QUAL ETF, not a bottom-up fundamental screen. Point-in-
  time ROE/margin histories are not cleanly available through yfinance; using today's
  fundamentals on past dates would be look-ahead. The ETF proxy is the honest choice.
* Survivorship: the individual-name universe is today's survivors walked backwards.
* PAPER / RESEARCH ONLY. Places no orders, holds no credentials, creates no probe.

Usage:
    python3 defensive_factor_lab.py
    python3 defensive_factor_lab.py --years 20 --house-trials 1123
"""
import argparse
import json
import math
import os
import time
import warnings

import numpy as np

import quant_validation as qv

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
REPORT_JSON = os.path.join(BASE, "reports", "defensive_factor_lab.json")
REPORT_MD = os.path.join(BASE, "reports", "research_defensive_factors.md")

BENCHMARK = "SPY"
SECONDARY_BENCH = "VT"          # global cap benchmark, sanity cross-check

# Tradeable defensive/quality ETF proxies.
DEFENSIVE_ETFS = ["USMV", "SPLV", "QUAL", "EFAV"]

# Individual-name universe for the price-based cross-sectional sleeve. Same liquid,
# long-listed set equity_lab uses (reused deliberately) so results are comparable.
UNIVERSE = [
    "AAPL", "MSFT", "JNJ", "XOM", "JPM", "PG", "KO", "PEP", "WMT", "HD",
    "CVX", "MRK", "PFE", "ABT", "TMO", "COST", "MCD", "NKE", "ORCL", "CSCO",
    "INTC", "IBM", "GE", "CAT", "BA", "MMM", "HON", "UNH", "CVS", "T",
    "VZ", "DIS", "AMGN", "GILD", "LLY", "BMY", "SO", "DUK", "NEE", "LMT",
    "RTX", "UPS", "FDX", "LOW", "TGT", "SBUX", "GS", "MS", "BLK", "AXP",
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "IYR",
]

COST_BPS = 10.0                 # round-trip, per side of turnover; commission-free US
REBALANCE = {"M": 21, "Q": 63}


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def fetch(tickers, years, verbose=True):
    import yfinance as yf
    start = time.strftime("%Y-%m-%d",
                          time.gmtime(time.time() - years * 365.25 * 86400))
    if verbose:
        print("Fetching %d tickers from %s ..." % (len(tickers), start))
    raw = yf.download(tickers, start=start, interval="1d", auto_adjust=True,
                      progress=False, threads=True)
    px = raw["Close"].dropna(axis=1, how="all")
    return px


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------
def daily_rets(price_series):
    p = np.asarray(price_series, dtype=float)
    return p[1:] / p[:-1] - 1.0


def ann_sharpe(rets, ppy):
    r = np.asarray(rets, dtype=float)
    r = r[np.isfinite(r)]
    if r.size < 2:
        return 0.0
    sd = r.std(ddof=1)
    return float(r.mean() / sd * math.sqrt(ppy)) if sd > 0 else 0.0


def ann_return(rets, ppy):
    r = np.asarray(rets, dtype=float)
    r = r[np.isfinite(r)]
    if r.size == 0:
        return 0.0
    return float((1.0 + r.mean()) ** ppy - 1.0)


def max_drawdown(rets):
    r = np.asarray(rets, dtype=float)
    r = r[np.isfinite(r)]
    if r.size == 0:
        return 0.0
    eq = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(eq)
    return float((eq / peak - 1.0).min())


def capm_alpha_beta(port_rets, mkt_rets, ppy):
    """Regress portfolio returns on market returns: r_p = a + b*r_m + e.

    Returns annualized alpha, beta, and the t-stat of the intercept. This is THE
    test for 'repackaged beta': if alpha is ~0 or its t-stat is small, the sleeve is
    just a levered/de-levered slice of the index and carries no independent edge.
    """
    y = np.asarray(port_rets, dtype=float)
    x = np.asarray(mkt_rets, dtype=float)
    k = min(y.size, x.size)
    y, x = y[:k], x[:k]
    good = np.isfinite(y) & np.isfinite(x)
    y, x = y[good], x[good]
    n = y.size
    if n < 10:
        return {"alpha_ann": None, "beta": None, "alpha_t": None, "n": n}
    X = np.column_stack([np.ones(n), x])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    a, b = coef
    resid = y - X @ coef
    dof = n - 2
    s2 = (resid @ resid) / dof
    cov = s2 * np.linalg.inv(X.T @ X)
    se_a = math.sqrt(cov[0, 0])
    alpha_t = a / se_a if se_a > 0 else 0.0
    return {
        "alpha_per_period": float(a),
        "alpha_ann": float((1.0 + a) ** ppy - 1.0),
        "alpha_bps": float(a * 1e4),
        "beta": float(b),
        "alpha_t": float(alpha_t),
        "n": int(n),
    }


# ---------------------------------------------------------------------------
# SLEEVE A — tradeable defensive/quality ETF proxies vs the index
# ---------------------------------------------------------------------------
def sleeve_etf_proxies(years, house_trials, verbose=True):
    tickers = DEFENSIVE_ETFS + [BENCHMARK, SECONDARY_BENCH]
    px = fetch(tickers, years, verbose)
    ppy = 252.0
    # Hold-cost model: a defensive ETF is a near buy-and-hold. Charge a small annual
    # implementation drag (expense ratio + minimal rebalance): ~15 bps/yr, applied
    # daily. Conservative and does not flatter the factor.
    daily_cost = 0.0015 / 252.0

    results = []
    spy = px[BENCHMARK].dropna()
    for etf in DEFENSIVE_ETFS:
        if etf not in px.columns:
            continue
        pair = px[[etf, BENCHMARK, SECONDARY_BENCH]].dropna()
        er = daily_rets(pair[etf]) - daily_cost
        er_gross = daily_rets(pair[etf])
        mr = daily_rets(pair[BENCHMARK])
        vt = daily_rets(pair[SECONDARY_BENCH])
        k = min(er.size, mr.size)
        er, er_gross, mr, vt = er[:k], er_gross[:k], mr[:k], vt[:k]
        excess = er - mr
        capm = capm_alpha_beta(er, mr, ppy)
        # effective sample size: daily equity returns are close to independent, but
        # the excess series inherits strong autocorrelation-free structure; keep n.
        results.append({
            "etf": etf,
            "start": str(pair.index[0].date()),
            "end": str(pair.index[-1].date()),
            "n_days": int(k),
            "net_cagr_pct": round(ann_return(er, ppy) * 100, 2),
            "spy_cagr_pct": round(ann_return(mr, ppy) * 100, 2),
            "net_sharpe": round(ann_sharpe(er, ppy), 3),
            "gross_sharpe": round(ann_sharpe(er_gross, ppy), 3),
            "spy_sharpe": round(ann_sharpe(mr, ppy), 3),
            "vt_sharpe": round(ann_sharpe(vt, ppy), 3),
            "maxdd_pct": round(max_drawdown(er) * 100, 1),
            "spy_maxdd_pct": round(max_drawdown(mr) * 100, 1),
            "excess_vs_spy_bps_day": round(float(excess.mean()) * 1e4, 3),
            "excess_t": round(float(excess.mean() /
                              (excess.std(ddof=1) / math.sqrt(k)))
                              if excess.std(ddof=1) > 0 else 0.0, 3),
            "sharpe_beats_spy": bool(ann_sharpe(er, ppy) > ann_sharpe(mr, ppy)),
            "capm_beta": round(capm["beta"], 3),
            "capm_alpha_ann_pct": round(capm["alpha_ann"] * 100, 2),
            "capm_alpha_bps_day": round(capm["alpha_bps"], 3),
            "capm_alpha_t": round(capm["alpha_t"], 3),
            "_er": er, "_mr": mr,
        })

    # Deflated Sharpe on the best sleeve-A sharpe, deflated by the HOUSE search count.
    for r in results:
        ev = qv.evaluate(
            "ETF:%s" % r["etf"], r["_er"], n_trials=house_trials,
            periods_per_year=ppy, trades_per_period=1.0,
            benchmark_returns=r["_mr"])
        r["deflated_sharpe"] = ev["deflated_sharpe"]
        r["survives_deflation"] = ev["survives_deflation"]
        r["excess_t_stat_harness"] = ev.get("excess_t_stat")
        r["beats_benchmark_harness"] = ev.get("beats_benchmark")
    for r in results:
        r.pop("_er", None)
        r.pop("_mr", None)
    return {"benchmark": BENCHMARK, "results": results,
            "n_defensive_etfs": len(results)}


# ---------------------------------------------------------------------------
# SLEEVE B — cross-sectional price-based defensive factors, individual names
# ---------------------------------------------------------------------------
# Market series shared with the low-beta factor (set before backtest runs).
_MKT = None


def f_low_vol(col, i, w=252):
    if i < w + 1:
        return np.nan
    r = np.diff(col[i - w:i + 1]) / np.maximum(col[i - w:i], 1e-12)
    sd = r.std(ddof=1)
    return -sd if sd > 0 else np.nan          # low vol ranks high


def f_low_beta(col, i, w=252):
    """Negative trailing beta vs the market — low-beta names rank high."""
    if _MKT is None or i < w + 1:
        return np.nan
    a = np.diff(col[i - w:i + 1]) / np.maximum(col[i - w:i], 1e-12)
    m = np.diff(_MKT[i - w:i + 1]) / np.maximum(_MKT[i - w:i], 1e-12)
    k = min(a.size, m.size)
    a, m = a[:k], m[:k]
    vm = m.var(ddof=1)
    if vm <= 0:
        return np.nan
    beta = float(np.cov(a, m, ddof=1)[0, 1] / vm)
    return -beta


def f_low_vol_scaled(col, i, w=126):
    """Shorter-window inverse vol — robustness variant, not a new factor family."""
    return f_low_vol(col, i, w=126)


DEF_FACTORS = {
    "low_volatility_252": f_low_vol,
    "low_beta_252": f_low_beta,
    "low_volatility_126": f_low_vol_scaled,
}


def backtest_xs(P, mkt, factor_fn, hold_days, top_n, cost_bps):
    """Long top_n by the factor, equal weight, rebal every hold_days. Price-only.

    Returns (net_rets, gross_rets, turnover, n_rebalances, mean_rank_autocorr).
    Cost is charged on the traded fraction of the book.
    """
    n_dates, n_names = P.shape
    net, gross, turns = [], [], []
    prev = set()
    prev_order = None
    autocorrs = []
    start = 260
    for i in range(start, n_dates - hold_days, hold_days):
        scores = np.array([factor_fn(P[:, j], i) for j in range(n_names)])
        ok = np.isfinite(scores)
        if ok.sum() < top_n * 2:
            continue
        idx = np.where(ok)[0]
        order = idx[scores[idx].argsort()]
        longs = order[-top_n:]
        fwd = P[i + hold_days, :] / P[i, :] - 1.0
        g = float(np.mean(fwd[longs]))
        cur = set(longs.tolist())
        turn = 1.0 if not prev else len(cur - prev) / max(1, len(cur))
        prev = cur
        net.append(g - turn * (cost_bps / 1e4))
        gross.append(g)
        turns.append(turn)
        # rank autocorrelation of the full score vector across consecutive rebalances
        if prev_order is not None:
            common = np.isfinite(scores) & np.isfinite(prev_order)
            if common.sum() > 5:
                ac = np.corrcoef(scores[common], prev_order[common])[0, 1]
                if np.isfinite(ac):
                    autocorrs.append(ac)
        prev_order = scores
    return (np.array(net), np.array(gross),
            float(np.mean(turns)) if turns else 0.0, len(net),
            float(np.mean(autocorrs)) if autocorrs else 0.0)


def bench_period_rets(bpx, hold_days):
    b = np.asarray(bpx, dtype=float)
    out = []
    for i in range(260, len(b) - hold_days, hold_days):
        out.append(b[i + hold_days] / b[i] - 1.0)
    return np.array(out)


def sleeve_cross_sectional(years, house_trials, rebalance="M", verbose=True):
    global _MKT
    tickers = UNIVERSE + [BENCHMARK]
    px = fetch(tickers, years, verbose)
    px = px.dropna()
    names = [c for c in px.columns if c != BENCHMARK]
    P = px[names].to_numpy(float)
    _MKT = px[BENCHMARK].to_numpy(float)
    hold = REBALANCE[rebalance]
    ppy = 252.0 / hold
    bench = bench_period_rets(px[BENCHMARK].to_numpy(float), hold)
    bench_sharpe = ann_sharpe(bench, ppy)
    bench_cagr = ann_return(bench, ppy)

    quintile_n = max(3, len(names) // 5)      # top-quintile long
    results, trial_sharpes = [], []
    n_local_trials = 0
    for fname, fn in DEF_FACTORS.items():
        for tn in (quintile_n, 10, 5):
            n_local_trials += 1
            net, gross, turn, n, rac = backtest_xs(P, _MKT, fn, hold, tn, COST_BPS)
            if n < 24:
                continue
            k = min(len(net), len(bench))
            excess = net[:k] - bench[:k]
            # daily market series aligned to period returns for CAPM
            capm = capm_alpha_beta(net[:k], bench[:k], ppy)
            ebets = qv.effective_bets(n, rac)
            se = (excess.std(ddof=1) / math.sqrt(k)) if excess.std(ddof=1) > 0 else 0.0
            trial_sharpes.append(ann_sharpe(net, ppy) / math.sqrt(ppy))  # per-period
            results.append({
                "factor": fname,
                "top_n": tn,
                "n_periods": n,
                "net_cagr_pct": round(ann_return(net, ppy) * 100, 2),
                "gross_sharpe": round(ann_sharpe(gross, ppy), 3),
                "net_sharpe": round(ann_sharpe(net, ppy), 3),
                "spy_sharpe": round(bench_sharpe, 3),
                "maxdd_pct": round(max_drawdown(net) * 100, 1),
                "spy_maxdd_pct": round(max_drawdown(bench) * 100, 1),
                "turnover_pct": round(turn * 100, 1),
                "vs_spy_cagr_pct": round((ann_return(net, ppy) - bench_cagr) * 100, 2),
                "excess_bps": round(float(excess.mean()) * 1e4, 2),
                "excess_t": round(float(excess.mean() / se) if se > 0 else 0.0, 3),
                "sharpe_beats_spy": bool(ann_sharpe(net, ppy) > bench_sharpe),
                "capm_beta": round(capm["beta"], 3) if capm["beta"] is not None else None,
                "capm_alpha_ann_pct": round(capm["alpha_ann"] * 100, 2) if capm["alpha_ann"] is not None else None,
                "capm_alpha_t": round(capm["alpha_t"], 3) if capm["alpha_t"] is not None else None,
                "rank_autocorr": round(rac, 4),
                "independent_bets": ebets["independent_bets"],
                "bets_discount": ebets["discount"],
                "_net": net, "_gross": gross, "_bench": bench,
            })

    if not results:
        raise RuntimeError("no cross-sectional results")

    # Best by risk-adjusted improvement (Sharpe delta), which is the defensive claim,
    # not raw CAGR. Then deflate against the house search count.
    best = max(results, key=lambda r: r["net_sharpe"])
    k = min(len(best["_net"]), len(best["_bench"]))
    n_eff = qv.effective_sample_size(len(best["_net"]), n_series=1,
                                     mean_correlation=0.0)
    ev = qv.evaluate(
        "%s top%d" % (best["factor"], best["top_n"]),
        best["_net"], n_trials=house_trials, trial_sharpes=trial_sharpes,
        periods_per_year=ppy, trades_per_period=1.0,
        n_effective=n_eff, gross_returns=best["_gross"],
        benchmark_returns=best["_bench"][:k])

    for r in results:
        for kk in ("_net", "_gross", "_bench"):
            r.pop(kk, None)

    return {
        "rebalance": rebalance, "hold_days": hold,
        "sample_start": str(px.index[0].date()),
        "sample_end": str(px.index[-1].date()),
        "n_tickers": len(names),
        "benchmark_cagr_pct": round(bench_cagr * 100, 2),
        "benchmark_sharpe": round(bench_sharpe, 3),
        "benchmark_maxdd_pct": round(max_drawdown(bench) * 100, 1),
        "local_configs": n_local_trials,
        "results": results,
        "best": {kk: v for kk, v in best.items() if not kk.startswith("_")},
        "best_deflated": ev,
        "verdict": qv.verdict_line(ev),
    }


# ---------------------------------------------------------------------------
def run(years=20, house_trials=1123, verbose=True):
    if verbose:
        print("=" * 80)
        print("DEFENSIVE FACTOR LAB  (deflation against house search count = %d)"
              % house_trials)
        print("=" * 80)
    etf = sleeve_etf_proxies(min(years, 16), house_trials, verbose)
    xs = sleeve_cross_sectional(years, house_trials, rebalance="M", verbose=verbose)
    payload = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "house_trials_for_deflation": house_trials,
        "sleeve_A_etf_proxies": etf,
        "sleeve_B_cross_sectional": xs,
    }
    os.makedirs(os.path.dirname(REPORT_JSON), exist_ok=True)
    with open(REPORT_JSON, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    return payload


def report(p):
    a = p["sleeve_A_etf_proxies"]
    b = p["sleeve_B_cross_sectional"]
    print("\nSLEEVE A — TRADEABLE DEFENSIVE/QUALITY ETFs vs SPY")
    print("  %-6s %-21s %8s %8s %8s %8s %9s %8s %8s"
          % ("ETF", "window", "netCAGR", "netShrp", "spyShrp", "maxDD",
             "beta", "alpha%", "alpha_t"))
    for r in a["results"]:
        print("  %-6s %s..%s %7.2f%% %8.2f %8.2f %7.1f%% %9.2f %7.2f%% %8.2f"
              % (r["etf"], r["start"], r["end"], r["net_cagr_pct"],
                 r["net_sharpe"], r["spy_sharpe"], r["maxdd_pct"],
                 r["capm_beta"], r["capm_alpha_ann_pct"], r["capm_alpha_t"]))
    print("\nSLEEVE B — CROSS-SECTIONAL PRICE DEFENSIVE FACTORS (%s..%s, %s rebal)"
          % (b["sample_start"], b["sample_end"], b["rebalance"]))
    print("  SPY bench: %.2f%%/yr  Sharpe %.2f  maxDD %.1f%%"
          % (b["benchmark_cagr_pct"], b["benchmark_sharpe"], b["benchmark_maxdd_pct"]))
    print("  %-18s %5s %8s %8s %8s %8s %8s %8s %10s"
          % ("factor", "topN", "netShrp", "spyShrp", "maxDD", "beta",
             "alpha_t", "excess_t", "indep_bets"))
    for r in sorted(b["results"], key=lambda x: -x["net_sharpe"]):
        print("  %-18s %5d %8.2f %8.2f %7.1f%% %8.2f %8.2f %8.2f %10.1f"
              % (r["factor"], r["top_n"], r["net_sharpe"], r["spy_sharpe"],
                 r["maxdd_pct"], r["capm_beta"] or 0, r["capm_alpha_t"] or 0,
                 r["excess_t"], r["independent_bets"]))
    print("\n  BEST (by net Sharpe): %s" % b["best"]["factor"])
    ev = b["best_deflated"]
    print("    deflated Sharpe   : %s (survives: %s)"
          % (ev["deflated_sharpe"], ev["survives_deflation"]))
    print("    excess vs SPY     : %+.1f bps/rebal, t=%s, beats index: %s"
          % (ev.get("excess_over_benchmark_bps", 0), ev.get("excess_t_stat"),
             ev.get("beats_benchmark")))
    print("    -> %s" % b["verdict"])
    print("\nReport JSON -> %s" % REPORT_JSON)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=20)
    ap.add_argument("--house-trials", type=int, default=1123)
    args = ap.parse_args()
    report(run(years=args.years, house_trials=args.house_trials))


if __name__ == "__main__":
    main()
