#!/usr/bin/env python3
"""Long-history regime test — does buy-and-hold's dominance survive real crashes?

WHY THIS EXISTS
---------------
The multi-asset lab found buy-and-hold SPY beating every allocation strategy over
2010-2026 — and flagged its own biggest weakness: that window is ONE regime, a
near-uninterrupted US equity melt-up. Trend-following and dual momentum exist to
earn their keep in CRASHES, and 2010-2026 contains no crash longer than a month.

That caveat is a testable claim. Long-listed mutual funds give daily history back
to ~1990, a sample containing:

    dot-com bust      2000-03   S&P -49% peak-to-trough
    GFC               2007-09   S&P -57%
    COVID             2020      S&P -34% in a month
    inflation bear    2022      S&P -25%, bonds down simultaneously

If trend/dual-momentum's crash protection pays for its bull-market lag, THIS is the
sample that shows it. If buy-and-hold still wins here, the 2010-2026 conclusion is
not a regime artefact and the question is settled at retail evidence standards.

PROXIES (chosen for listing age, not perfection)
------------------------------------------------
Mutual funds, adjusted for distributions by yfinance auto_adjust:
    VFINX  S&P 500 index fund       (1980)
    VUSTX  long US treasuries       (1986)
    VWEHX  high-yield credit        (1978)
    FSAGX  gold/precious metals eq  (1985)   — miners, not bullion; imperfect
    VEURX  European equity          (1990)
    VPACX  Pacific equity           (1990)

HONEST LIMITS
-------------
* The trend/momentum fallback asset is CASH AT 0%, which is conservative — T-bills
  paid 3-5% for much of this sample, so trend strategies are UNDERSTATED here.
  Stated rather than modelled to avoid inventing a rate series.
* FSAGX is gold miners (equity beta ~0.3-0.5), not bullion. Understates gold's
  diversification.
* Monthly-return drawdowns understate true daily drawdowns for every row equally.
* Survivorship is minimal here (these funds all survived, but they track broad
  asset classes, not picked stocks).
* PAPER ONLY. Research, not advice.

Usage:  python3 long_history_lab.py
"""
import json
import os
import time
import warnings

import numpy as np

import quant_validation as qv

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(BASE, "reports", "long_history_lab.json")

FUNDS = {
    "VFINX": "US equity (S&P 500)",
    "VUSTX": "Long US treasuries",
    "VWEHX": "High-yield credit",
    "FSAGX": "Gold (miners proxy)",
    "VEURX": "European equity",
    "VPACX": "Pacific equity",
}
EQUITY, BONDS = "VFINX", "VUSTX"
HOLD = 21          # monthly
COST_BPS = 10.0

CRASH_WINDOWS = {
    "dot-com 2000-03": ("2000-03-01", "2003-03-31"),
    "GFC 2007-09": ("2007-10-01", "2009-03-31"),
    "COVID 2020": ("2020-02-01", "2020-04-30"),
    "inflation 2022": ("2022-01-01", "2022-12-31"),
}


def fetch(verbose=True):
    import yfinance as yf
    raw = yf.download(list(FUNDS), start="1990-01-01", interval="1d",
                      auto_adjust=True, progress=False, threads=True)
    px = raw["Close"].dropna(axis=1, how="all").dropna()
    if verbose:
        print("  %d dates x %d funds, %s -> %s  (%.1f years)\n"
              % (px.shape[0], px.shape[1], px.index[0].date(), px.index[-1].date(),
                 (px.index[-1] - px.index[0]).days / 365.25))
    return px


# ---------------------------------------------------------------------------
# Strategies. Long-only; fallback is cash at 0% (conservative for trend/momo).
# ---------------------------------------------------------------------------
def w_equity(P, i, names):
    w = np.zeros(len(names)); w[names.index(EQUITY)] = 1.0
    return w, 0.0


def w_6040(P, i, names):
    w = np.zeros(len(names))
    w[names.index(EQUITY)] = 0.6; w[names.index(BONDS)] = 0.4
    return w, 0.0


def w_equal(P, i, names):
    return np.ones(len(names)) / len(names), 0.0


def w_trend(P, i, names, lb=252):
    """Each asset held only when above its 12m trend; the rest sits in cash at 0%."""
    if i < lb + 1:
        return np.ones(len(names)) / len(names), 0.0
    above = (P[i, :] / P[i - lb, :] - 1.0) > 0.0
    w = np.zeros(len(names))
    if above.sum():
        w[above] = 1.0 / len(names)          # fixed slice per asset; rest to cash
    cash = 1.0 - w.sum()
    return w, cash


def w_dual_momentum(P, i, names, lb=252, top=2):
    """Top-2 by trailing return, only if positive; otherwise cash at 0%."""
    if i < lb + 1:
        return np.ones(len(names)) / len(names), 0.0
    trail = P[i, :] / P[i - lb, :] - 1.0
    order = np.argsort(trail)[::-1]
    picks = [j for j in order[:top] if trail[j] > 0]
    w = np.zeros(len(names))
    if picks:
        w[picks] = 1.0 / len(picks)
        return w, 0.0
    return w, 1.0


STRATEGIES = {
    "buy_hold_equity": w_equity,
    "sixty_forty": w_6040,
    "equal_weight": w_equal,
    "trend_overlay_12m": w_trend,
    "dual_momentum_top2": w_dual_momentum,
}


def backtest(px, fn):
    names = list(px.columns)
    P = px.to_numpy(float)
    dates = px.index
    rets, when = [], []
    prev_w = None
    for i in range(260, len(P) - HOLD, HOLD):
        w, cash = fn(P, i, names)
        tot = w.sum() + cash
        if tot <= 0:
            continue
        fwd = P[i + HOLD, :] / P[i, :] - 1.0
        r = float(np.dot(w, fwd))                 # cash leg contributes 0
        turn = 1.0 if prev_w is None else float(np.abs(w - prev_w).sum() / 2.0)
        prev_w = w
        r -= turn * (COST_BPS / 10000.0)
        rets.append(r)
        when.append(dates[i])
    return np.array(rets), when


def ann(r):
    return (1.0 + np.mean(r)) ** (252.0 / HOLD) - 1.0 if len(r) else 0.0


def maxdd(r):
    eq = np.cumprod(1.0 + r)
    return float((eq / np.maximum.accumulate(eq) - 1.0).min()) if len(r) else 0.0


def window_return(r, when, start, end):
    import datetime as dt
    s = dt.datetime.fromisoformat(start); e = dt.datetime.fromisoformat(end)
    sel = [x for x, t in zip(r, when) if s <= t.to_pydatetime().replace(tzinfo=None) <= e]
    return (np.prod([1 + x for x in sel]) - 1.0) * 100 if sel else None


def run(verbose=True):
    print("Fetching %d fund proxies from 1990..." % len(FUNDS))
    px = fetch(verbose)

    rows, series, whens, trial_sharpes = [], {}, {}, []
    for name, fn in STRATEGIES.items():
        r, when = backtest(px, fn)
        if len(r) < 24:
            continue
        sd = r.std(ddof=1)
        series[name], whens[name] = r, when
        trial_sharpes.append(float(r.mean() / sd) if sd > 0 else 0.0)
        row = {
            "strategy": name,
            "annualised_pct": round(ann(r) * 100, 2),
            "sharpe": round(float(r.mean() / sd * np.sqrt(252.0 / HOLD)), 3)
            if sd > 0 else 0.0,
            "max_drawdown_pct": round(maxdd(r) * 100, 1),
            "n_periods": len(r),
            "crashes": {},
        }
        for cname, (s, e) in CRASH_WINDOWS.items():
            row["crashes"][cname] = round(window_return(r, when, s, e), 1)
        rows.append(row)

    eq = series["buy_hold_equity"]
    eq_ann = ann(eq)
    for row in rows:
        row["vs_equity_pct"] = round((ann(series[row["strategy"]]) - eq_ann) * 100, 2)

    # deflation + benchmark test on the best non-benchmark by Sharpe
    cands = [x for x in rows if x["strategy"] not in ("buy_hold_equity", "sixty_forty")]
    best = max(cands, key=lambda x: x["sharpe"])
    r = series[best["strategy"]]
    k = min(len(r), len(eq))
    ev = qv.evaluate(best["strategy"], r, n_trials=len(rows),
                     trial_sharpes=trial_sharpes, periods_per_year=252.0 / HOLD,
                     trades_per_period=1.0, benchmark_returns=eq[:k])

    payload = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sample_start": str(px.index[0].date()), "sample_end": str(px.index[-1].date()),
        "years": round((px.index[-1] - px.index[0]).days / 365.25, 1),
        "cost_bps": COST_BPS, "funds": FUNDS,
        "results": rows,
        "best_non_benchmark": best["strategy"],
        "best_deflated": ev,
        "verdict": qv.verdict_line(ev),
    }
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w") as f:
        json.dump(payload, f, indent=2)
    return payload


def report(p):
    print("=" * 96)
    print("LONG-HISTORY REGIME TEST — %s to %s (%.1f years, THREE major crashes in sample)"
          % (p["sample_start"], p["sample_end"], p["years"]))
    print("=" * 96)
    print("  %-20s %9s %8s %9s %10s | %10s %9s %7s %8s"
          % ("strategy", "ann %", "Sharpe", "maxDD %", "vs equity",
             "dot-com", "GFC", "COVID", "2022"))
    for r in sorted(p["results"], key=lambda x: -x["sharpe"]):
        c = r["crashes"]
        print("  %-20s %8.2f%% %8.2f %8.1f%% %+9.2f%% | %9s%% %8s%% %6s%% %7s%%"
              % (r["strategy"], r["annualised_pct"], r["sharpe"],
                 r["max_drawdown_pct"], r["vs_equity_pct"],
                 c.get("dot-com 2000-03"), c.get("GFC 2007-09"),
                 c.get("COVID 2020"), c.get("inflation 2022")))

    ev = p["best_deflated"]
    print("\nBest non-benchmark: %s" % p["best_non_benchmark"])
    print("  t-stat %+.2f   DSR %s   excess vs equity %+.1f bps/период t=%s beats: %s"
          % (ev["t_stat"], ev["deflated_sharpe"],
             ev.get("excess_over_benchmark_bps", 0), ev.get("excess_t_stat"),
             ev.get("beats_benchmark")))
    print("  -> %s" % p["verdict"])
    print("\n  NOTE: trend/dual-momentum fallback is cash at 0%% — T-bills paid 3-5%% for")
    print("  much of this sample, so those two rows are UNDERSTATED by roughly 1-2%%/yr.")
    print("\nReport -> %s" % REPORT)


if __name__ == "__main__":
    report(run())
