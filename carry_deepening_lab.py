#!/usr/bin/env python3
"""Carry deepening lab — how much MORE of the one real edge can be captured.

WHY THIS EXISTS
---------------
The desk has exactly one mechanism that clears its own cost wall: delta-neutral
basis carry (long spot X / short perp X, harvest funding). `carry_monitor.py`
watches it and `carry_paper_desk.py` is the armed paper executor. Both are frozen
and correct in what they do. The honest highest-value question is NOT "find a new
edge" — it is "are we capturing as much of the real one as we could?"

This lab quantifies three levers, RESEARCH ONLY, against real public data:

  (a) ASSET SET      — does widening beyond BTC/ETH add real, persistent, capturable
                       carry, or does breadth destroy it (as research_basis_carry.md
                       found for the always-on top-5/top-10 baskets)?
  (b) REGIME TIMING  — how often has funding actually exceeded the 4% (and 2%, 6%)
                       harvest thresholds, and is a lower, cost-justified threshold
                       leaving money on the table right now?
  (c) EXECUTION COST — maker vs taker: how much does patient limit execution add on
                       a trade that holds ~1 year and never needs to hurry?

DATA
----
Public Kraken Futures v4 `historicalfundingrates` (hourly relativeFundingRate,
~366 days) — the SAME series research_basis_carry.md used, and the correctly
normalised one (the local funding_*.jsonl stores an ABSOLUTE rate that must be
divided by mark; see that report's data-schema note). Current 24h notional from
the public v3 `tickers` endpoint for the liquidity screen. No keys, no orders,
no local secrets. Everything is cached to reports/carry_funding_cache/.

METHOD (byte-consistent with research_basis_carry.md §2)
--------------------------------------------------------
Per asset, per UTC day: daily funding f_t = sum of hourly relativeFundingRate.
Short perp RECEIVES when f_t > 0. Annualised carry = mean(f_t) * 365. BTC
reproduces at +3.76%/yr and ETH at +3.37%/yr against the report's +3.78/+3.35
funding legs, which is the validation that the method is faithful.

Costs: round-trip BOTH legs — 90 bps taker / 32 bps maker (from the report §5.1).
Significance uses effective sample size (quant_validation.effective_sample_size)
because daily funding is heavily autocorrelated (BTC acf1 ~ 0.77); naive t-stats
overstate by ~2x.

PAPER / READ-ONLY. This writes one JSON research artifact. It never places an
order, never edits the carry registration or the executor, holds no credentials.

Usage:
    python3 carry_deepening_lab.py            # full analysis -> stdout + JSON
    python3 carry_deepening_lab.py --json
    python3 carry_deepening_lab.py --no-fetch # cache only, do not hit the network
"""
import argparse
import json
import math
import os
import statistics
import time
import urllib.request
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "reports", "carry_funding_cache")
OUT_JSON = os.path.join(BASE, "reports", "carry_deepening_lab.json")

HIST_URL = "https://futures.kraken.com/derivatives/api/v4/historicalfundingrates?symbol=%s"
TICK_URL = "https://futures.kraken.com/derivatives/api/v3/tickers"

# Round-trip cost of establishing AND unwinding BOTH legs, in bps (report §5.1).
COST_BPS_TAKER = 90.0
COST_BPS_MAKER = 32.0

# Executor's current gate and eligible set (carry_paper_desk.py) — the baseline we
# are trying to beat, quoted here READ-ONLY so the comparison is explicit.
EXECUTOR_THRESHOLD_PCT = 4.0
EXECUTOR_ELIGIBLE = ("BTC", "ETH")
EXECUTOR_COST_ASSUMPTION = "taker (45 bps/side)"

# Liquidity screen — the report's tail-risk law: basis dislocation in a cascade
# scales INVERSELY with liquidity (2025-10-11: ADA -2.89%, SOL -0.23%, ETH -0.19%,
# BTC +0.02%). Illiquid perps are NOT harvestable at size regardless of headline
# funding. Two floors: $1m/24h = tradeable at the A$33k paper size; $5m/24h = the
# bar to call a name "scalable".
LIQ_FLOOR_TRADEABLE_USD = 1_000_000.0
LIQ_FLOOR_SCALABLE_USD = 5_000_000.0

# Curated screening universe: the report's liquid names PLUS the two high-volume
# names that post-date the report's universe snapshot (HYPE, XMR). Gold tokens
# PAXG/XAUT included because the report flagged them as a decorrelated positive
# cluster. This is the honest candidate set for "wider than BTC/ETH".
UNIVERSE = {
    "BTC": "PF_XBTUSD", "ETH": "PF_ETHUSD", "SOL": "PF_SOLUSD", "XRP": "PF_XRPUSD",
    "ADA": "PF_ADAUSD", "DOGE": "PF_DOGEUSD", "BNB": "PF_BNBUSD", "SUI": "PF_SUIUSD",
    "AVAX": "PF_AVAXUSD", "LINK": "PF_LINKUSD", "HYPE": "PF_HYPEUSD", "ZEC": "PF_ZECUSD",
    "XMR": "PF_XMRUSD", "PAXG": "PF_PAXGUSD", "XAUT": "PF_XAUTUSD", "LTC": "PF_LTCUSD",
    "UNI": "PF_UNIUSD", "ONDO": "PF_ONDOUSD", "TAO": "PF_TAOUSD", "AAVE": "PF_AAVEUSD",
    "PEPE": "PF_PEPEUSD", "XLM": "PF_XLMUSD",
}

BASELINE = ["BTC", "ETH"]
NOTIONAL_AUD = 33000.0            # the executor's sized book (carry_paper_desk.py)
OOS_DAYS = 127                    # the report's out-of-sample window
TRAIL_WIN = 30                    # trailing window for regime detection (days)

try:
    from quant_validation import effective_sample_size as _ess
except Exception:                                    # keep the lab runnable standalone
    def _ess(n_total, n_series=1, mean_correlation=0.0, overlap_factor=1.0):
        n = float(n_total)
        if n_series > 1 and mean_correlation > 0:
            n = n / (1.0 + (n_series - 1) * float(mean_correlation))
        return max(2.0, n)


# --------------------------------------------------------------------------- data
def _fetch(url, path, allow_fetch):
    if os.path.exists(path) and os.path.getsize(path) > 500:
        with open(path) as f:
            return json.load(f)
    if not allow_fetch:
        return None
    with urllib.request.urlopen(url, timeout=30) as r:
        d = json.load(r)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(d, f)
    time.sleep(0.25)
    return d


def load_daily(sym, allow_fetch=True):
    """Daily funding f_t = sum of hourly relativeFundingRate (short perp receives >0)."""
    d = _fetch(HIST_URL % sym, os.path.join(CACHE, sym + ".json"), allow_fetch)
    if not d:
        return {}
    by, cnt = defaultdict(float), defaultdict(int)
    for r in d.get("rates", []):
        rr = r.get("relativeFundingRate")
        if rr is None:
            continue
        day = r["timestamp"][:10]
        by[day] += float(rr)
        cnt[day] += 1
    return {k: v for k, v in by.items() if cnt[k] >= 20}     # drop partial days


def load_liquidity(allow_fetch=True):
    d = _fetch(TICK_URL, os.path.join(CACHE, "tickers.json"), allow_fetch)
    out = {}
    inv = {v: k for k, v in UNIVERSE.items()}
    for t in (d or {}).get("tickers", []):
        s = t.get("symbol", "").upper()
        if s in inv:
            out[inv[s]] = (t.get("vol24h") or 0) * (t.get("markPrice") or t.get("last") or 0)
    return out


# ----------------------------------------------------------------------- analysis
def _acf1(v):
    if len(v) < 4:
        return 0.0
    m = statistics.mean(v)
    num = sum((v[i] - m) * (v[i + 1] - m) for i in range(len(v) - 1))
    den = sum((x - m) ** 2 for x in v)
    return num / den if den else 0.0


def per_asset(series, dates, liq):
    """Full-year carry + persistence (H1/H2/OOS) + autocorrelation-adjusted t."""
    mid = dates[len(dates) // 2]
    rows = {}
    for nm, s in series.items():
        v = [s[d] for d in dates if d in s]
        if len(v) < 60:
            continue
        h1 = [s[d] for d in dates if d in s and d < mid]
        h2 = [s[d] for d in dates if d in s and d >= mid]
        oos = [s[d] for d in dates[-OOS_DAYS:] if d in s]
        m, sd = statistics.mean(v), statistics.pstdev(v)
        acf1 = _acf1(v)
        # design-effect style deflation for serial correlation (see quant_validation)
        neff = _ess(len(v)) * (1 - acf1) / (1 + acf1) if -1 < acf1 < 1 else len(v)
        t_eff = (m / (sd / math.sqrt(max(neff, 2)))) if sd > 0 else 0.0
        rows[nm] = {
            "ann_pct": round(m * 365 * 100, 2),
            "sd_ann_pct": round(sd * math.sqrt(365) * 100, 2),
            "sharpe": round(m / sd * math.sqrt(365), 2) if sd > 0 else 0.0,
            "h1_pct": round(statistics.mean(h1) * 365 * 100, 2) if h1 else None,
            "h2_pct": round(statistics.mean(h2) * 365 * 100, 2) if h2 else None,
            "oos127_pct": round(statistics.mean(oos) * 365 * 100, 2) if oos else None,
            "acf1": round(acf1, 2),
            "n_eff": round(neff, 1),
            "t_eff": round(t_eff, 1),
            "vol24h_usd": round(liq.get(nm, 0), 0),
            "n_days": len(v),
        }
    return rows


def screen_expansion(rows):
    """A name is an expansion candidate only if it is (i) tradeable-liquid,
    (ii) genuinely persistent (both halves positive AND OOS positive), and
    (iii) statistically robust past a Bonferroni-ish |t_eff| >= 3.3 bar."""
    out = {}
    for nm, r in rows.items():
        if nm in BASELINE:
            continue
        liq = r["vol24h_usd"] >= LIQ_FLOOR_TRADEABLE_USD
        persist = (r["h1_pct"] or -1) > 0 and (r["h2_pct"] or -1) > 0 and (r["oos127_pct"] or -1) > 0
        robust = abs(r["t_eff"]) >= 3.3
        out[nm] = {
            "tradeable_liquid": liq, "scalable": r["vol24h_usd"] >= LIQ_FLOOR_SCALABLE_USD,
            "persistent": persist, "robust_t": robust,
            "expansion_candidate": bool(liq and persist and robust),
            **r,
        }
    return out


def basket_series(series, names, dates):
    return [(d, statistics.mean([series[n][d] for n in names if d in series[n]]))
            for d in dates if any(d in series[n] for n in names)]


def trailing_ann(seq, i, w=TRAIL_WIN):
    lo = max(0, i - w + 1)
    return statistics.mean([x for _, x in seq[lo:i + 1]]) * 365 * 100


def regime_frequency(seq, thresholds=(2, 4, 6)):
    n = len(seq)
    return {thr: round(sum(1 for i in range(n) if trailing_ann(seq, i) >= thr) / n * 100, 1)
            for thr in thresholds}


def policy_sim(seq, entry_thr, exit_thr=0.0, cost_rt_bps=COST_BPS_MAKER):
    """Book-level: enter when trailing-30d ann carry >= entry_thr, exit when < exit_thr.
    Accrue realised daily funding while in; charge one round trip per open/close cycle.
    Returns annualised numbers over the actual window (~1yr) so policies compare like-for-like."""
    n = len(seq)
    yrs = n / 365.0
    inpos = False
    funding = cost = 0.0
    cycles = days_in = 0
    for i, (_, x) in enumerate(seq):
        ta = trailing_ann(seq, i)
        if not inpos and ta >= entry_thr:
            inpos = True
            cycles += 1
            cost += cost_rt_bps / 10000.0
        if inpos:
            funding += x
            days_in += 1
            if ta < exit_thr:
                inpos = False
    net = funding - cost
    # cost margin = realised gross funding per cycle / round-trip cost per cycle
    be_margin = round((funding * 10000 / cycles) / cost_rt_bps, 1) if cycles else None
    return {
        "entry_thr_pct": entry_thr, "cost_rt_bps": cost_rt_bps,
        "cycles_per_yr": round(cycles / yrs, 2), "frac_in_market_pct": round(days_in / n * 100, 1),
        "gross_ann_pct": round(funding / yrs * 100, 2), "cost_ann_pct": round(cost / yrs * 100, 2),
        "net_ann_pct": round(net / yrs * 100, 2), "cost_margin_x": be_margin,
        "aud_per_yr": round(NOTIONAL_AUD * net / yrs, 0),
        "aud_per_day": round(NOTIONAL_AUD * net / yrs / 365, 2),
    }


# --------------------------------------------------------------------------- main
def run(allow_fetch=True):
    series = {nm: load_daily(sym, allow_fetch) for nm, sym in UNIVERSE.items()}
    series = {k: v for k, v in series.items() if v}
    if "BTC" not in series or "ETH" not in series:
        return {"error": "insufficient funding history (need at least BTC+ETH); "
                         "run once online to populate reports/carry_funding_cache/"}
    dates = sorted(set().union(*[set(v) for v in series.values()]))
    liq = load_liquidity(allow_fetch)

    rows = per_asset(series, dates, liq)
    screened = screen_expansion(rows)
    candidates = [nm for nm, r in screened.items() if r["expansion_candidate"]]
    # order candidates by robustness for a deterministic expanded basket
    candidates.sort(key=lambda nm: -screened[nm]["t_eff"])
    expanded = BASELINE + candidates

    base_seq = basket_series(series, BASELINE, dates)
    exp_seq = basket_series(series, expanded, dates)

    # (b) regime frequency
    regimes = {"BTC+ETH": regime_frequency(base_seq),
               "+".join(expanded): regime_frequency(exp_seq)}

    # (b)+(c) threshold sensitivity x maker/taker on the BASELINE (isolates the lever)
    threshold_grid = []
    for thr in (0, 1, 2, 4):
        for cost in (COST_BPS_MAKER, COST_BPS_TAKER):
            threshold_grid.append(policy_sim(base_seq, thr, cost_rt_bps=cost))

    # (a) asset expansion, marginal — always-on, maker
    base_net = policy_sim(base_seq, 0, cost_rt_bps=COST_BPS_MAKER)["net_ann_pct"]
    expansion = []
    acc = list(BASELINE)
    for nm in [None] + candidates:
        if nm:
            acc = acc + [nm]
        r = policy_sim(basket_series(series, acc, dates), 0, cost_rt_bps=COST_BPS_MAKER)
        expansion.append({"basket": "+".join(acc), "added": nm,
                          "net_ann_pct": r["net_ann_pct"],
                          "delta_vs_baseline_pct": round(r["net_ann_pct"] - base_net, 2)})

    # headline comparison: executor baseline vs the improved configs
    executor_like = policy_sim(base_seq, EXECUTOR_THRESHOLD_PCT, cost_rt_bps=COST_BPS_TAKER)
    baseline_alwayson_maker = policy_sim(base_seq, 0, cost_rt_bps=COST_BPS_MAKER)
    best = policy_sim(exp_seq, 0, cost_rt_bps=COST_BPS_MAKER)

    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "data": {"source": "kraken v4 historicalfundingrates (public, hourly relativeFundingRate)",
                 "window": [dates[0], dates[-1]], "n_days": len(dates),
                 "n_assets_screened": len(rows),
                 "validation": {"BTC_ann_pct": rows.get("BTC", {}).get("ann_pct"),
                                "ETH_ann_pct": rows.get("ETH", {}).get("ann_pct"),
                                "report_says": "BTC +3.78 / ETH +3.35 funding leg"}},
        "costs_bps": {"taker_rt": COST_BPS_TAKER, "maker_rt": COST_BPS_MAKER},
        "executor_baseline_config": {"eligible": list(EXECUTOR_ELIGIBLE),
                                     "entry_threshold_pct": EXECUTOR_THRESHOLD_PCT,
                                     "cost_assumption": EXECUTOR_COST_ASSUMPTION},
        "per_asset": rows,
        "expansion_screen": screened,
        "expansion_candidates": candidates,
        "expanded_basket": expanded,
        "regime_frequency_pct_of_days": regimes,
        "threshold_x_cost_grid_baseline": threshold_grid,
        "asset_expansion_marginal": expansion,
        "headline": {
            "executor_like_thr4_taker": executor_like,
            "baseline_alwayson_maker": baseline_alwayson_maker,
            "expanded_alwayson_maker": best,
            "note": "executor is presently FLAT: current carry ~1.5% has not reached the 4% gate, "
                    "so its realised harvest right now is A$0, not the figure above.",
        },
    }


def _p(res):
    if res.get("error"):
        print("carry_deepening_lab:", res["error"])
        return
    d = res["data"]
    print("=" * 78)
    print("CARRY DEEPENING LAB — capturing more of the one validated edge   [RESEARCH ONLY]")
    print("=" * 78)
    print("window %s -> %s (%d days) | BTC %.2f%% ETH %.2f%% (report: +3.78/+3.35 -> method OK)\n"
          % (d["window"][0], d["window"][1], d["n_days"],
             d["validation"]["BTC_ann_pct"], d["validation"]["ETH_ann_pct"]))

    print("(a) ASSET EXPANSION — screen: liquid>=$1m AND both-halves+OOS positive AND |t_eff|>=3.3")
    print("  %-6s %8s %8s %8s %8s %6s %14s  %s" % ("asset", "ann%", "H1%", "H2%", "OOS%", "t_eff", "vol24h$", "verdict"))
    for nm, r in sorted(res["expansion_screen"].items(), key=lambda kv: -kv[1]["ann_pct"]):
        print("  %-6s %8.2f %8.2f %8.2f %8.2f %6.1f %14s  %s"
              % (nm, r["ann_pct"], r["h1_pct"], r["h2_pct"], r["oos127_pct"], r["t_eff"],
                 "{:,.0f}".format(r["vol24h_usd"]),
                 "CANDIDATE" if r["expansion_candidate"] else ("thin" if not r["tradeable_liquid"] else "not persistent/robust")))
    print("  -> expansion candidates:", ", ".join(res["expansion_candidates"]) or "(none)")
    print()
    print("  marginal book-level net (always-on, maker 32bps):")
    for e in res["asset_expansion_marginal"]:
        print("    %-30s net %6.2f%%   (%+.2f vs BTC+ETH)"
              % (e["basket"], e["net_ann_pct"], e["delta_vs_baseline_pct"]))

    print("\n(b) REGIME FREQUENCY — %% of days trailing-30d annualised basket carry >= threshold")
    for bk, fr in res["regime_frequency_pct_of_days"].items():
        print("    %-28s " % bk + "  ".join("%d%%: %4.1f%%" % (k, v) for k, v in fr.items()))

    print("\n(b)+(c) THRESHOLD x COST (BTC+ETH baseline, book-level annualised):")
    print("  %-7s %-6s %8s %9s %8s %8s %8s %9s" % ("entry", "cost", "cyc/yr", "frac_in", "gross%", "cost%", "net%", "margin"))
    for g in res["threshold_x_cost_grid_baseline"]:
        lbl = "maker" if g["cost_rt_bps"] == COST_BPS_MAKER else "taker"
        print("  >=%-5.1f %-6s %8.2f %8.1f%% %8.2f %8.2f %8.2f %8sx"
              % (g["entry_thr_pct"], lbl, g["cycles_per_yr"], g["frac_in_market_pct"],
                 g["gross_ann_pct"], g["cost_ann_pct"], g["net_ann_pct"], g["cost_margin_x"]))

    h = res["headline"]
    print("\nHEADLINE (A$%.0f notional):" % NOTIONAL_AUD)
    print("  executor-like (thr4, taker) : %.2f%%  A$%.0f/yr  (and A$0 right now — gate not met)"
          % (h["executor_like_thr4_taker"]["net_ann_pct"], h["executor_like_thr4_taker"]["aud_per_yr"]))
    print("  BTC+ETH always-on, maker    : %.2f%%  A$%.0f/yr  A$%.2f/day"
          % (h["baseline_alwayson_maker"]["net_ann_pct"], h["baseline_alwayson_maker"]["aud_per_yr"],
             h["baseline_alwayson_maker"]["aud_per_day"]))
    print("  expanded always-on, maker   : %.2f%%  A$%.0f/yr  A$%.2f/day"
          % (h["expanded_alwayson_maker"]["net_ann_pct"], h["expanded_alwayson_maker"]["aud_per_yr"],
             h["expanded_alwayson_maker"]["aud_per_day"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-fetch", action="store_true", help="use cache only, never hit the network")
    args = ap.parse_args()
    res = run(allow_fetch=not args.no_fetch)
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w") as f:
        json.dump(res, f, indent=2)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        _p(res)
        print("\nwrote", OUT_JSON)


if __name__ == "__main__":
    main()
