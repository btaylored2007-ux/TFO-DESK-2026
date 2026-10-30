#!/usr/bin/env python3
"""futures_panel_coverage — collection-design audit of the futures_analytics panel.

READ-ONLY. SELECT-only against market_intelligence.sqlite; no network, no orders,
no engine touched, no registration created or edited.

This is NOT a new hypothesis search. reports/research_futures_panel.md (2026-08-24)
already ran the 144-cell grid and returned NO EDGE, and pre-specified that the panel's
binding constraint is COLLECTION DESIGN (breadth + liquid-name contiguity), not duration.
This script measures only the quantities that report named as the accrual gates, so the
house search count is UNCHANGED (no cell is tested here).

Gates carried over from research_futures_panel.md 8.1 / 10:
  4h TS  : 27 days of CONTIGUOUS coverage on liquid names (>=$5M/24h). Held 08-24: ~8d.
  12h XS : 36 days of >=30-symbol cross-sectional breadth.            Held 08-24: 11.5d.
  <=2h   : ANSWERED NO — powered, negative, below the wall. Never re-test.
"""
import sqlite3, json, collections, math, datetime as dt

DB = "/Users/test/Applications/KrakenQuantDesk/kraken_bot/market_intelligence.sqlite"
BUCKET = 1800
LIQUID_SEVEN = ["XBT/USD","XRP/USD","ETH/USD","SOL/USD","ADA/USD","LINK/USD","HYPE/USD"]

def f(x):
    try:
        v = float(x); return v if math.isfinite(v) else None
    except Exception: return None

# ---- STAGE 1: build panel (parser VERBATIM from futures_panel_lab.py; do not re-derive) ----
panel = collections.defaultdict(dict); spotmap = {}
c = sqlite3.connect(DB)
nrow = 0; nbad = 0
for spot, fsym, metric, pj in c.execute(
        "SELECT spot_symbol,futures_symbol,metric,payload_json FROM futures_analytics WHERE metric!='availability'"):
    nrow += 1; spotmap[fsym] = spot
    d = json.loads(pj); ts = d.get("timestamp") or []
    if not ts: continue
    ts = [int(t)//1000 if int(t) > 1e11 else int(t) for t in ts]
    dat = d.get("data")
    def put(field, vals, pick=None):
        global nbad
        if not vals or len(vals) != len(ts): return
        for t, v in zip(ts, vals):
            if pick is not None and isinstance(v, list): v = v[pick] if len(v) > pick else None
            x = f(v)
            if x is None: continue
            if not (1700000000 < t < 1800000000): nbad += 1; continue
            panel[(fsym, field)][t] = x
    if metric == "open-interest": put("oi", dat, pick=3)
    elif metric == "funding": put("funding", dat.get("relativeRate"), pick=3); put("funding_abs", dat.get("rate"), pick=3)
    elif metric == "long-short-ratio": put("lsr", dat)
    elif metric == "future-basis": put("basis", dat.get("basis"))
    elif metric == "cvd": put("cvd", dat.get("cvd")); put("buy_vol", dat.get("buy_volume")); put("sell_vol", dat.get("sell_volume"))
    elif metric == "liquidation-volume": put("liq", dat)
    elif metric == "aggressor-differential": put("aggr", dat)
panel = dict(panel)

U = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
DAY = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d")

out = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
       "rows_parsed": nrow, "out_of_range_cells": nbad}

print("="*78)
print("FUTURES PANEL — COLLECTION-DESIGN AUDIT   [%s]" % out["generated"])
print("READ-ONLY. No hypothesis tested; house search count unchanged.")
print("="*78)
print("rows parsed %d, out-of-range cells dropped %d" % (nrow, nbad))

# ---- field density ----
by = collections.defaultdict(lambda: [0, 0])
for (s, fd), ser in panel.items(): by[fd][0] += 1; by[fd][1] += len(ser)
print("\nFIELD DENSITY")
print("%-12s %7s %9s %8s" % ("field", "series", "cells", "medlen"))
fields = {}
for fd in sorted(by):
    lens = sorted(len(panel[(s, f2)]) for (s, f2) in panel if f2 == fd)
    med = lens[len(lens)//2]
    print("%-12s %7d %9d %8d" % (fd, by[fd][0], by[fd][1], med))
    fields[fd] = {"series": by[fd][0], "cells": by[fd][1], "median_len": med}
out["fields"] = fields

allb = sorted(set(t for s in panel.values() for t in s))
out["bucket_span"] = {"first": U(allb[0]), "last": U(allb[-1]), "n_union_buckets": len(allb),
                      "span_days": round((allb[-1]-allb[0])/86400.0, 2)}
print("\nbucket span %s -> %s   union %d buckets (%.2f days)" % (
    U(allb[0]), U(allb[-1]), len(allb), (allb[-1]-allb[0])/86400.0))

# ---- per-symbol coverage on OI ----
def longest_run(times):
    ts = sorted(times); best = run = 1
    for a, b in zip(ts, ts[1:]):
        run = run + 1 if b - a == BUCKET else 1
        best = max(best, run)
    return best

cov = {}
for (s, fd), ser in panel.items():
    if fd != "oi": continue
    t = sorted(ser)
    cov[s] = {"buckets": len(t), "first": U(t[0]), "last": U(t[-1]),
              "span_days": round((t[-1]-t[0])/86400.0, 2),
              "longest_run_bars": longest_run(t),
              "longest_run_days": round(longest_run(t)*BUCKET/86400.0, 2)}
out["per_symbol"] = cov
runs = sorted((v["longest_run_days"] for v in cov.values()), reverse=True)
print("\nPER-SYMBOL COVERAGE (open-interest): %d symbols with any cell" % len(cov))
if runs:
    print("  longest contiguous run: max %.2fd  median %.2fd  (>=21d: %d symbols, >=27d: %d)" % (
        runs[0], runs[len(runs)//2], sum(r >= 21 for r in runs), sum(r >= 27 for r in runs)))

# ---- daily cross-sectional breadth ----
print("\nDAILY BREADTH (symbols with OI cells that UTC day; 'full' = >=40 of 48 buckets)")
perday = collections.defaultdict(lambda: collections.Counter())
for (s, fd), ser in panel.items():
    if fd != "oi": continue
    for t in ser: perday[DAY(t)][s] += 1
days = {}
print("  %-12s %7s %7s" % ("day", "syms", "full"))
for d in sorted(perday):
    n = len(perday[d]); full = sum(1 for v in perday[d].values() if v >= 40)
    days[d] = {"symbols": n, "full_day_symbols": full}
    print("  %-12s %7d %7d" % (d, n, full))
out["daily_breadth"] = days
b30 = [d for d, v in days.items() if v["symbols"] >= 30]
b40 = [d for d, v in days.items() if v["symbols"] >= 40]
out["days_breadth_ge30"] = len(b30); out["days_breadth_ge40"] = len(b40)
print("  days with >=30 symbols: %d   >=40 symbols: %d" % (len(b30), len(b40)))

# ---- liquidity: 24h USD volume from the candles table ----
vol = collections.defaultdict(float)
lastpx = {}
cut = allb[-1] - 86400
for s, ts, cl, vw, v in c.execute(
        "SELECT symbol,ts,close,vwap,volume FROM candles WHERE interval_min=30 AND ts>=?", (cut,)):
    p = vw or cl
    if p and v: vol[s] += p*v
    lastpx[s] = cl
liq = {}
for fs in cov:
    sp = spotmap.get(fs)
    liq[fs] = {"spot": sp, "usd_24h": round(vol.get(sp, 0.0), 0)}
out["liquidity_24h"] = liq
clear5 = [k for k, v in liq.items() if v["usd_24h"] >= 5e6]
clear1 = [k for k, v in liq.items() if v["usd_24h"] >= 1e6]
vals = sorted(v["usd_24h"] for v in liq.values())
print("\nLIQUIDITY (last 24h of the panel window, Kraken spot USD volume from candles)")
print("  symbols clearing $5M/24h: %d   $1M/24h: %d   median symbol: $%,.0f".replace("%,", "%") % (
    len(clear5), len(clear1), vals[len(vals)//2] if vals else 0))
print("  $5M clearers: %s" % ", ".join(sorted(spotmap.get(k, k) for k in clear5)) if clear5 else "  none")
out["clear_5m"] = sorted(spotmap.get(k, k) for k in clear5)
out["clear_1m_count"] = len(clear1)

# ---- the liquid seven, named by the prior report ----
print("\nTHE LIQUID SEVEN (prior report's tradeable subset) — contiguity is the gate")
seven = {}
inv = {v: k for k, v in spotmap.items()}
for sp in LIQUID_SEVEN:
    fs = inv.get(sp)
    if fs is None or fs not in cov:
        seven[sp] = None
        print("  %-10s NOT COLLECTED in this window" % sp); continue
    v = cov[fs]; seven[sp] = v
    print("  %-10s buckets %5d  span %5.2fd  longest contiguous %5.2fd  last %s" % (
        sp, v["buckets"], v["span_days"], v["longest_run_days"], v["last"]))
out["liquid_seven"] = seven

# ---- gates ----
print("\n" + "="*78)
print("ACCRUAL GATES (from research_futures_panel.md, 2026-08-24)")
got = [v["longest_run_days"] for v in (seven[s] for s in LIQUID_SEVEN) if v]
best7 = max(got) if got else 0.0
med7 = sorted(got)[len(got)//2] if got else 0.0
print("  4h TS  needs 27d contiguous on liquid names : best %.2fd, median %.2fd  -> %s" % (
    best7, med7, "MET" if med7 >= 27 else "NOT MET"))
print("  12h XS needs 36 days of >=30-symbol breadth : have %d days -> %s" % (
    len(b30), "MET" if len(b30) >= 36 else "NOT MET"))
print("  <=2h horizons                               : ANSWERED NO — do not re-test")
out["gates"] = {"ts_4h_needs_days": 27, "ts_4h_best_contiguous_days": best7,
                "ts_4h_median_contiguous_days": med7, "ts_4h_met": med7 >= 27,
                "xs_12h_needs_breadth_days": 36, "xs_12h_have_days": len(b30),
                "xs_12h_met": len(b30) >= 36}
print("="*78)

json.dump(out, open("/Users/test/Applications/MultiVenueDesk/reports/futures_panel_coverage.json", "w"), indent=1)
print("receipt -> reports/futures_panel_coverage.json")
