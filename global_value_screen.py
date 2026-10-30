#!/usr/bin/env python3
"""Global value/quality screen — every market, current fundamentals, honest framing.

WHAT THIS IS AND IS NOT
-----------------------
A disciplined CURRENT-STATE screen of ~130 large liquid names across the US,
Europe, UK, Japan, Australia, Asia and emerging markets, ranked on the published
factor definitions (value: earnings/book/FCF yield, EV/EBITDA; quality: ROE,
margins, low leverage; growth-at-reasonable-price: revenue growth vs PEG).

It is NOT a backtested edge. Clean fundamental backtests need point-in-time data
(paid); ranking today's ratios against past returns is look-ahead bias. The desk
refuses to fake that. Instead the screen feeds a PRE-REGISTERED forward test (the
Value Book) whose live results become the evidence.

What the published record honestly says about this tilt: the value premium is
real in long history (~2-4%/yr), suffered a brutal 2010s drought, and post-2005
anomaly returns decayed hard (Chen & Welch: median 7 bps/mo non-micro). Expect a
TILT with droughts, not a money machine.

Value-trap guard: cheap + shrinking revenue + heavy debt = flagged and excluded
from the book. Cheapness alone is how screens buy failing companies.

PAPER ONLY. Research, not advice.

Usage:  python3 global_value_screen.py            # run + print top of screen
"""
import json
import os
import time
import warnings

import numpy as np

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(BASE, "reports", "value_screen.json")

# ~130 large, liquid names spanning markets. ADRs used for non-US where cleaner.
UNIVERSE = [
    # US mega/large across sectors
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "BRK-B", "JPM", "V", "MA",
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "XOM", "CVX", "COP",
    "WMT", "COST", "PG", "KO", "PEP", "MCD", "HD", "LOW", "NKE", "SBUX",
    "CAT", "DE", "HON", "GE", "LMT", "RTX", "UPS", "UNP", "BA", "MMM",
    "GS", "MS", "BAC", "WFC", "C", "BLK", "AXP", "T", "VZ", "CMCSA",
    "ORCL", "CRM", "ADBE", "INTC", "AMD", "QCOM", "TXN", "IBM", "CSCO", "MU",
    "DIS", "NFLX", "PYPL", "SQ", "F", "GM", "TSLA", "SO", "DUK", "NEE",
    # Europe (ADR / US-listed)
    "ASML", "SAP", "NVO", "AZN", "GSK", "SNY", "TTE", "SHEL", "BP", "UL",
    "DEO", "BUD", "SAN", "ING", "DB", "UBS", "NVS", "RHHBY", "SIEGY", "ALIZY",
    "MC.PA", "OR.PA", "AIR.PA", "BNP.PA", "SIE.DE", "ALV.DE", "BAS.DE", "BMW.DE",
    # UK
    "HSBC", "BARC.L", "LLOY.L", "RIO", "BHP", "GLEN.L", "TSCO.L", "VOD",
    # Japan
    "TM", "SONY", "MUFG", "SMFG", "HMC", "7203.T", "6758.T", "9984.T", "8306.T",
    # Australia
    "CBA.AX", "WBC.AX", "NAB.AX", "WES.AX", "WOW.AX",
    # Asia / EM
    "TSM", "BABA", "JD", "PDD", "BIDU", "TCEHY", "0700.HK", "005930.KS",
    "INFY", "HDB", "IBN", "VALE", "PBR", "ITUB",
]

# Composite weights — frozen here, cited by the Value Book registration.
W_VALUE, W_QUALITY, W_GROWTH = 0.5, 0.3, 0.2
MIN_METRICS = 4          # a name needs at least this many usable metrics to rank


def fetch_fundamentals(tickers=UNIVERSE, verbose=True):
    import yfinance as yf
    rows = {}
    for i, t in enumerate(tickers):
        try:
            info = yf.Ticker(t).info or {}
        except Exception:
            info = {}
        mc = info.get("marketCap")
        fcf = info.get("freeCashflow")
        rows[t] = {
            "name": (info.get("shortName") or t)[:34],
            "country": info.get("country"),
            "sector": info.get("sector"),
            "market_cap_b": round(mc / 1e9, 1) if mc else None,
            "trailing_pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "p_b": info.get("priceToBook"),
            "ev_ebitda": info.get("enterpriseToEbitda"),
            "fcf_yield": (fcf / mc) if (fcf and mc) else None,
            "roe": info.get("returnOnEquity"),
            "margin": info.get("profitMargins"),
            "debt_to_equity": info.get("debtToEquity"),
            "rev_growth": info.get("revenueGrowth"),
            "peg": info.get("pegRatio"),
        }
        if verbose and (i + 1) % 25 == 0:
            print("  ...%d/%d fetched" % (i + 1, len(tickers)))
        time.sleep(0.25)
    return rows


def _pct_rank(values):
    """Percentile ranks in [0,1], NaN-safe (NaN stays NaN)."""
    v = np.array(values, float)
    out = np.full(len(v), np.nan)
    ok = np.isfinite(v)
    if ok.sum() >= 2:
        order = v[ok].argsort().argsort().astype(float)
        out[ok] = order / (ok.sum() - 1)
    return out


def screen(fund=None, verbose=True):
    if fund is None:
        if verbose:
            print("Fetching fundamentals for %d global names..." % len(UNIVERSE))
        fund = fetch_fundamentals(verbose=verbose)

    ticks = list(fund)
    g = lambda k: [fund[t].get(k) for t in ticks]

    def clean_pos(xs):
        return [x if (x is not None and np.isfinite(x) and x > 0) else np.nan
                for x in xs]

    # value: higher = cheaper
    ey = _pct_rank([1.0 / x if np.isfinite(x) else np.nan
                    for x in clean_pos(g("trailing_pe"))])
    fey = _pct_rank([1.0 / x if np.isfinite(x) else np.nan
                     for x in clean_pos(g("forward_pe"))])
    by = _pct_rank([1.0 / x if np.isfinite(x) else np.nan
                    for x in clean_pos(g("p_b"))])
    evy = _pct_rank([1.0 / x if np.isfinite(x) else np.nan
                     for x in clean_pos(g("ev_ebitda"))])
    fcfy = _pct_rank([x if (x is not None and np.isfinite(x)) else np.nan
                      for x in g("fcf_yield")])
    # quality: higher = better
    roe = _pct_rank([x if (x is not None and np.isfinite(x)) else np.nan
                     for x in g("roe")])
    mar = _pct_rank([x if (x is not None and np.isfinite(x)) else np.nan
                     for x in g("margin")])
    lowdebt = _pct_rank([-x if (x is not None and np.isfinite(x)) else np.nan
                         for x in g("debt_to_equity")])
    # growth at reasonable price
    grw = _pct_rank([x if (x is not None and np.isfinite(x)) else np.nan
                     for x in g("rev_growth")])
    lowpeg = _pct_rank([-x if (x is not None and np.isfinite(x) and x > 0) else np.nan
                        for x in g("peg")])

    results = []
    for i, t in enumerate(ticks):
        parts = {
            "value": np.nanmean([ey[i], fey[i], by[i], evy[i], fcfy[i]]),
            "quality": np.nanmean([roe[i], mar[i], lowdebt[i]]),
            "growth": np.nanmean([grw[i], lowpeg[i]]),
        }
        n_metrics = int(np.isfinite([ey[i], fey[i], by[i], evy[i], fcfy[i],
                                     roe[i], mar[i], lowdebt[i], grw[i],
                                     lowpeg[i]]).sum())
        if n_metrics < MIN_METRICS or not np.isfinite(parts["value"]):
            continue
        comp = (W_VALUE * parts["value"]
                + W_QUALITY * (parts["quality"] if np.isfinite(parts["quality"]) else 0.5)
                + W_GROWTH * (parts["growth"] if np.isfinite(parts["growth"]) else 0.5))
        f = fund[t]
        trap = bool(
            parts["value"] > 0.7
            and f.get("rev_growth") is not None and f["rev_growth"] < -0.05
            and f.get("debt_to_equity") is not None and f["debt_to_equity"] > 150)
        results.append({
            "ticker": t, "name": f["name"], "country": f.get("country"),
            "sector": f.get("sector"), "market_cap_b": f.get("market_cap_b"),
            "composite": round(float(comp), 4),
            "value_rank": round(float(parts["value"]), 3),
            "quality_rank": round(float(parts["quality"]), 3)
            if np.isfinite(parts["quality"]) else None,
            "growth_rank": round(float(parts["growth"]), 3)
            if np.isfinite(parts["growth"]) else None,
            "trailing_pe": f.get("trailing_pe"), "forward_pe": f.get("forward_pe"),
            "p_b": f.get("p_b"), "fcf_yield": round(f["fcf_yield"], 4)
            if f.get("fcf_yield") else None,
            "rev_growth": f.get("rev_growth"),
            "n_metrics": n_metrics,
            "value_trap_flag": trap,
        })

    results.sort(key=lambda r: -r["composite"])
    payload = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "universe_size": len(UNIVERSE),
        "ranked": len(results),
        "weights": {"value": W_VALUE, "quality": W_QUALITY, "growth": W_GROWTH},
        "results": results,
    }
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w") as f:
        json.dump(payload, f, indent=2)
    return payload


def top_picks(payload, n=10):
    """Top-N by composite, excluding value traps — what the Value Book holds."""
    return [r for r in payload["results"] if not r["value_trap_flag"]][:n]


def report(p, n=15):
    print("=" * 100)
    print("GLOBAL VALUE/QUALITY SCREEN — %d of %d names ranked (%s)"
          % (p["ranked"], p["universe_size"], p["generated"]))
    print("=" * 100)
    print("  %-8s %-30s %-14s %6s %6s %6s %7s %6s %5s"
          % ("ticker", "name", "country", "comp", "val", "qual", "PE", "fPE", "trap"))
    for r in p["results"][:n]:
        print("  %-8s %-30s %-14s %6.3f %6.3f %6s %7s %6s %5s"
              % (r["ticker"], r["name"][:30], (r["country"] or "?")[:14],
                 r["composite"], r["value_rank"],
                 r["quality_rank"] if r["quality_rank"] is not None else "—",
                 round(r["trailing_pe"], 1) if r["trailing_pe"] else "—",
                 round(r["forward_pe"], 1) if r["forward_pe"] else "—",
                 "TRAP" if r["value_trap_flag"] else ""))
    print("\n  Composite = %.0f%% value + %.0f%% quality + %.0f%% GARP. Value traps"
          % (100 * p["weights"]["value"], 100 * p["weights"]["quality"],
             100 * p["weights"]["growth"]))
    print("  (cheap + shrinking revenue + heavy debt) are flagged and excluded from the book.")
    print("\nReport -> %s" % REPORT)


if __name__ == "__main__":
    report(screen())
