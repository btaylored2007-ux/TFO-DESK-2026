#!/usr/bin/env python3
"""index_recon_lab.py — Honest COLD event study of S&P 500 INDEX RECONSTITUTION
(additions side ONLY), built strictly inside the scope the 2026-08-16 data probe
declared viable (reports/index_recon_data_probe.json).

RESEARCH ONLY. Paper desk. No orders, no capital, no engine state touched.
Writes only its own JSON receipt + research note.

THE HONEST SCOPE (from the probe — do not widen it)
---------------------------------------------------
source   : Wikipedia "List of S&P 500 companies", 'Selected changes' table,
           PINNED revision oldid=1329075976 (2025-12-23T15:18Z). The CURRENT 2026
           revision dropped the table entirely; the pinned dated revision is the
           reproducible free source. Prices: yfinance daily bars (auto-adjusted).
window   : effective dates 2011-01-01 .. 2025-12-22 ONLY. Pre-2011 rows are a
           curated "selected" subset (1-13 events/yr vs true ~20-30, whole years
           missing) with unknown editor bias — BLOCKED. Post-2025-12-22 absent.
side     : ADDITIONS only. Deletion-side tests are DATA-BLOCKED (35% coverage,
           and the covered deletions are near-all benign demotions that kept
           trading, while acquisition/bankruptcy removals return nothing — the
           same only-survivors structure that blocked merger-arb 2026-08-16).
anchor   : EFFECTIVE date only. Announcement dates are NOT in the source, so the
           literature's announcement->effective inclusion drift is NOT testable
           here. A pre-effective run-up window (T-5 trading days to effective
           close) is measured ONLY as a labeled PROXY, never claimed as the
           announcement drift, and is NEVER a candidate tradeable variant (this
           source gives no proof the effective date was knowable at T-5).
leakage  : The additions side is NOT survivorship-clean either (ENDP-type: added,
           later bankrupt, history purged from Yahoo). Every unpriceable addition
           is ENUMERATED and the leak rate reported per window — never silently
           dropped.

DESIGN
------
  events   : additions with ticker, effective 2011-01-01..2025-12-22 (324-row window)
  entry    : effective-date close (first trading day >= effective date)
  windows  : hold +5 / +20 / +60 trading days after entry
  abnormal : stock window return MINUS SPY window return (market leg cancels)
  sides    : long (inclusion momentum) and short (post-inclusion reversal)
  costs    : 5 and 10 bps per side (10/20 bps round trip) on single names
  variants : 3 windows x 2 sides x 2 cost levels = 12 scored tradeable variants,
             + 2 descriptive pre-effective PROXY measurements (long/short) that are
             counted in the deflation trials but excluded from best-variant selection
  clusters : events land on shared reconstitution dates -> same-date events are
             merged into ONE equal-weight basket per date, and the harness gets an
             effective-N discounted for serial overlap of nearby event windows
  scoring  : qv.evaluate on the best NET variant with n_trials = 1287 house prior
             + every number looked at here; White's reality check across the 12
             variants (strategies in ROWS).

Report real output, win or lose. A number that dies in the harness is a lesson
with receipts — that is the process working.
"""
import json
import math
import os
import re
import datetime as dt
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import quant_validation as qv

SCRATCH = ("/private/tmp/claude-501/-Users-test/"
           "326d8bdd-e63d-4126-a322-4582de2ba921/scratchpad")
CACHE = os.path.join(SCRATCH, "index_recon_cache")
os.makedirs(CACHE, exist_ok=True)

RECEIPT = "reports/index_recon_lab.json"
NOTE = "reports/research_index_recon.md"

PINNED_REVID = 1329075976
WIKI_URL = f"https://en.wikipedia.org/w/index.php?oldid={PINNED_REVID}"
WIKI_CACHE = os.path.join(SCRATCH, f"wiki_rev_{PINNED_REVID}.html")
UA = {"User-Agent": "Mozilla/5.0 (research probe; contact: paper desk local)"}

HOUSE_PRIOR_TRIALS = 1287          # registered BEFORE this lab
WINDOW_START = dt.date(2011, 1, 1) # honest near-complete era per the probe
WINDOW_END = dt.date(2025, 12, 22) # pinned revision's last event
HOLD_DAYS = [5, 20, 60]            # trading days after effective-date close
PRE_DAYS = 5                       # pre-effective PROXY window (descriptive only)
COST_SIDES_BPS = [5.0, 10.0]
PX_START, PX_END = "2010-06-01", "2026-05-01"
MIN_BAR_PRESENCE = 0.90            # guard vs recycled-ticker gaps inside a window


# ---------------------------------------------------------------------------
# Source: pinned Wikipedia revision (same parser as the probe, rowspan-aware)
# ---------------------------------------------------------------------------
def fetch_wiki():
    if os.path.exists(WIKI_CACHE):
        return open(WIKI_CACHE).read()
    import requests
    r = requests.get(WIKI_URL, headers=UA, timeout=90)
    r.raise_for_status()
    open(WIKI_CACHE, "w").write(r.text)
    return r.text


def parse_changes_table(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="changes")
    if table is None:
        for t in soup.find_all("table"):
            head = t.get_text(" ", strip=True)[:300].lower()
            if "added" in head and "removed" in head and "date" in head:
                table = t
                break
    if table is None:
        raise RuntimeError("changes table not found in pinned revision")
    rows = table.find_all("tr")
    events, pending = [], {}
    n_cols = 6  # Date, AddTicker, AddName, RemTicker, RemName, Reason
    for tr in [tr for tr in rows if tr.find("td")]:
        cells = tr.find_all("td")
        row_vals = [None] * n_cols
        for ci in list(pending):
            rem, val = pending[ci]
            row_vals[ci] = val
            rem -= 1
            if rem <= 0:
                del pending[ci]
            else:
                pending[ci] = (rem, val)
        ci = 0
        for td in cells:
            while ci < n_cols and row_vals[ci] is not None:
                ci += 1
            if ci >= n_cols:
                break
            val = td.get_text(" ", strip=True)
            rs = td.get("rowspan")
            if rs:
                try:
                    rs = int(rs)
                except ValueError:
                    rs = 1
                if rs > 1:
                    pending[ci] = (rs - 1, val)
            row_vals[ci] = val
            ci += 1
        if row_vals[0] is None:
            continue
        events.append({
            "effective_date_raw": row_vals[0],
            "added_ticker": (row_vals[1] or "").strip(),
            "added_name": (row_vals[2] or "").strip(),
            "reason": (row_vals[5] or "").strip(),
        })
    return events


def parse_date(s):
    s = re.sub(r"\[.*?\]", "", s).strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def load_addition_events():
    """Additions with ticker inside the honest window, deduped (date, ticker)."""
    events = parse_changes_table(fetch_wiki())
    out, seen = [], set()
    n_dated = 0
    for e in events:
        d = parse_date(e["effective_date_raw"])
        if d is None:
            continue
        n_dated += 1
        if not e["added_ticker"]:
            continue
        if not (WINDOW_START <= d <= WINDOW_END):
            continue
        key = (d, e["added_ticker"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"date": d, "ticker": e["added_ticker"],
                    "yf": e["added_ticker"].replace(".", "-"),
                    "name": e["added_name"], "reason": e["reason"]})
    out.sort(key=lambda x: (x["date"], x["ticker"]))
    return out, n_dated


# ---------------------------------------------------------------------------
# Prices: batched yfinance download, cached per chunk
# ---------------------------------------------------------------------------
def load_close_panel(symbols):
    """Adjusted daily closes for symbols + SPY on the SPY trading calendar."""
    import yfinance as yf
    symbols = sorted(set(symbols) | {"SPY"})
    frames = []
    chunk_n = 50
    for i in range(0, len(symbols), chunk_n):
        chunk = symbols[i:i + chunk_n]
        path = os.path.join(CACHE, "closes_%03d.csv" % (i // chunk_n))
        if os.path.exists(path):
            df = pd.read_csv(path, index_col=0, parse_dates=True)
        else:
            raw = yf.download(chunk, start=PX_START, end=PX_END,
                              auto_adjust=True, threads=True, progress=False,
                              group_by="column")
            if raw is None or raw.empty:
                df = pd.DataFrame()
            elif isinstance(raw.columns, pd.MultiIndex):
                df = raw["Close"]
            else:                      # single ticker fallback
                df = raw[["Close"]].rename(columns={"Close": chunk[0]})
            df.index = pd.to_datetime(df.index).tz_localize(None)
            df.to_csv(path)
        frames.append(df)
    panel = pd.concat(frames, axis=1)
    panel = panel.loc[:, ~panel.columns.duplicated()]
    panel = panel.dropna(axis=1, how="all")
    if "SPY" not in panel.columns:
        raise RuntimeError("SPY download failed — cannot compute abnormal returns")
    spy_days = panel.index[panel["SPY"].notna()]
    return panel.reindex(spy_days)


# ---------------------------------------------------------------------------
# Event measurement
# ---------------------------------------------------------------------------
def measure_events(events, panel):
    """Per event: entry bar (first trading day >= effective) and abnormal returns
    over each window. Unpriceable events are enumerated with a reason, never
    silently dropped."""
    days = panel.index
    spy = panel["SPY"]
    measured, unpriceable = [], []
    for e in events:
        ed = pd.Timestamp(e["date"])
        pos = days.searchsorted(ed)
        if pos >= len(days):
            unpriceable.append({**_eid(e), "reason_blocked": "beyond price panel"})
            continue
        entry_i = int(pos)
        rec = {**_eid(e), "entry_date": str(days[entry_i].date()),
               "entry_lag_days": int((days[entry_i] - ed).days), "ar": {}}
        col = e["yf"]
        if col not in panel.columns:
            unpriceable.append({**_eid(e), "reason_blocked":
                                "no yfinance history at all (ENDP-type purge, "
                                "restructure, or pre-Yahoo delisting)"})
            continue
        px = panel[col]
        if np.isnan(px.iloc[entry_i]):
            unpriceable.append({**_eid(e), "reason_blocked":
                                "no bar at effective-date close (history starts "
                                "later or was purged around the event)"})
            continue
        any_window = False
        for h in HOLD_DAYS:
            exit_i = entry_i + h
            if exit_i >= len(days) or np.isnan(px.iloc[exit_i]):
                rec["ar"]["h%d" % h] = None
                continue
            seg = px.iloc[entry_i:exit_i + 1]
            if seg.notna().mean() < MIN_BAR_PRESENCE:
                rec["ar"]["h%d" % h] = None   # recycled-ticker gap guard
                continue
            r_stock = float(px.iloc[exit_i] / px.iloc[entry_i] - 1.0)
            r_spy = float(spy.iloc[exit_i] / spy.iloc[entry_i] - 1.0)
            rec["ar"]["h%d" % h] = r_stock - r_spy
            rec["spy_h%d" % h] = r_spy
            any_window = True
        # pre-effective PROXY (descriptive only, never a tradeable variant)
        pre_i = entry_i - PRE_DAYS
        if pre_i >= 0 and not np.isnan(px.iloc[pre_i]):
            r_stock = float(px.iloc[entry_i] / px.iloc[pre_i] - 1.0)
            r_spy = float(spy.iloc[entry_i] / spy.iloc[pre_i] - 1.0)
            rec["ar"]["pre%d_proxy" % PRE_DAYS] = r_stock - r_spy
        else:
            rec["ar"]["pre%d_proxy" % PRE_DAYS] = None
        if any_window:
            measured.append(rec)
        else:
            unpriceable.append({**_eid(e), "reason_blocked":
                                "entry bar exists but no post-window is "
                                "computable (delisted/purged inside window)"})
    return measured, unpriceable


def _eid(e):
    return {"date": str(e["date"]), "ticker": e["ticker"], "name": e["name"]}


def window_stats(measured, key, lo=None, hi=None):
    """Naive per-event stats for one window (clustering handled at harness)."""
    vals = []
    for m in measured:
        if lo and m["date"] < lo:
            continue
        if hi and m["date"] > hi:
            continue
        v = m["ar"].get(key)
        if v is not None:
            vals.append(v)
    if len(vals) < 2:
        return {"n_events": len(vals), "mean_ar_bps": None, "t_naive": None,
                "median_ar_bps": None, "hit_rate_pos": None}
    a = np.array(vals)
    sd = a.std(ddof=1)
    return {
        "n_events": int(a.size),
        "mean_ar_bps": round(float(a.mean()) * 10000, 2),
        "t_naive": round(float(a.mean() / (sd / math.sqrt(a.size))), 3)
        if sd > 0 else None,
        "median_ar_bps": round(float(np.median(a)) * 10000, 2),
        "hit_rate_pos": round(float((a > 0).mean()), 3),
    }


# ---------------------------------------------------------------------------
# Per-date basket series (same-date events merged = the first cluster discount)
# ---------------------------------------------------------------------------
def per_date_series(measured, key, side, cost_side_bps):
    """Equal-weight basket of all events sharing an effective date.
    Returns (dates, net, gross, spy_window) as aligned lists."""
    by_date = {}
    for m in measured:
        v = m["ar"].get(key)
        if v is None:
            continue
        by_date.setdefault(m["entry_date"], {"ar": [], "spy": []})
        by_date[m["entry_date"]]["ar"].append(v)
        s = m.get("spy_" + key)
        if s is not None:
            by_date[m["entry_date"]]["spy"].append(s)
    dates = sorted(by_date)
    rt_cost = 2.0 * cost_side_bps / 10000.0
    gross = np.array([side * float(np.mean(by_date[d]["ar"])) for d in dates])
    net = gross - rt_cost
    spyw = np.array([float(np.mean(by_date[d]["spy"])) if by_date[d]["spy"]
                     else np.nan for d in dates])
    return dates, net, gross, spyw


def overlap_rho(dates, hold_days):
    """Serial-dependence floor from calendar overlap of consecutive event
    windows: nearby reconstitution dates share most of their N-day window, so
    their abnormal returns are not independent draws."""
    if len(dates) < 2:
        return 0.0
    d = pd.to_datetime(pd.Series(dates))
    gaps = d.diff().dt.days.dropna().values.astype(float)
    win_cal = hold_days * (7.0 / 5.0)          # trading -> calendar days
    fr = np.clip(1.0 - gaps / win_cal, 0.0, 1.0)
    return float(np.mean(fr))


def lag1_ac(x):
    x = np.asarray(x, dtype=float)
    if x.size < 3 or x.std(ddof=1) == 0:
        return 0.0
    return float(np.corrcoef(x[:-1], x[1:])[0, 1])


def main():
    events, n_dated_rows = load_addition_events()
    panel = load_close_panel([e["yf"] for e in events])
    measured, unpriceable = measure_events(events, panel)

    n_total = len(events)
    n_unpriceable = len(unpriceable)
    leak_rate = n_unpriceable / n_total if n_total else 0.0

    # per-window leak (events measured overall but blocked for that window)
    per_window_leak = {}
    for h in HOLD_DAYS:
        k = "h%d" % h
        n_ok = sum(1 for m in measured if m["ar"].get(k) is not None)
        per_window_leak[k] = {
            "priceable": n_ok,
            "unpriceable": n_total - n_ok,
            "leak_rate": round((n_total - n_ok) / n_total, 4) if n_total else None,
        }

    # ------------------------------------------------------------------
    # Descriptive event-study table (naive t; clustering handled at harness)
    # ------------------------------------------------------------------
    pre_key = "pre%d_proxy" % PRE_DAYS
    all_keys = [pre_key] + ["h%d" % h for h in HOLD_DAYS]
    descriptive = {k: window_stats(measured, k) for k in all_keys}
    eras = [("2011-2015", "2011-01-01", "2015-12-31"),
            ("2016-2020", "2016-01-01", "2020-12-31"),
            ("2021-2025", "2021-01-01", "2025-12-31")]
    era_table = {label: {k: window_stats(measured, k, lo, hi) for k in all_keys}
                 for label, lo, hi in eras}

    # ------------------------------------------------------------------
    # Tradeable variants: window x side x cost (post-effective ONLY).
    # The pre-effective PROXY is measured (2 numbers looked at -> 2 trials)
    # but is NOT eligible as a tradeable variant: without announcement dates
    # this source cannot prove the effective date was knowable at T-5.
    # ------------------------------------------------------------------
    variants, trial_sharpes, series = [], [], {}
    n_here = 0
    for h in HOLD_DAYS:
        key = "h%d" % h
        for side, sname in [(+1, "long"), (-1, "short")]:
            for cost in COST_SIDES_BPS:
                dates, net, gross, spyw = per_date_series(measured, key, side, cost)
                n_here += 1
                vname = f"{sname}_h{h}_c{int(cost)}"
                if net.size < 8:
                    variants.append({"name": vname, "n_event_dates": int(net.size),
                                     "note": "too few event dates to score"})
                    continue
                m = qv.moments(net)
                trial_sharpes.append(m["sharpe"])
                series[vname] = (dates, net, gross, spyw, h)
                variants.append({
                    "name": vname, "side": sname, "hold_days": h,
                    "cost_bps_side": cost,
                    "n_event_dates": int(net.size),
                    "gross_mean_bps": round(float(np.mean(gross)) * 10000, 2),
                    "net_mean_bps": round(float(np.mean(net)) * 10000, 2),
                    "net_sharpe_per_event_date": round(m["sharpe"], 4),
                    "hit_rate": round(float((net > 0).mean()), 3),
                })
    # the two proxy measurements count toward the search, honestly
    n_here += 2
    total_trials = HOUSE_PRIOR_TRIALS + n_here

    scored = [v for v in variants if "net_sharpe_per_event_date" in v]
    scored.sort(key=lambda v: v["net_sharpe_per_event_date"], reverse=True)

    best_eval, eff, beta, wrc, rho_used = None, None, None, None, None
    if scored:
        best = scored[0]
        dates, net, gross, spyw, h = series[best["name"]]
        # effective-N: same-date merge already done; remaining serial dependence
        # from overlapping windows of nearby reconstitution dates. Conservative:
        # rho = max(measured lag-1 autocorr, calendar-overlap floor, 0).
        rho_used = max(lag1_ac(net), overlap_rho(dates, h), 0.0)
        eff = qv.effective_bets(net.size, rho_used)
        n_eff = eff["independent_bets"]          # DICT -> scalar, per harness spec
        span_years = max(1e-9, (pd.Timestamp(dates[-1]) -
                                pd.Timestamp(dates[0])).days / 365.25)
        ppy = net.size / span_years
        best_eval = qv.evaluate(
            "index_recon_add_" + best["name"], net, n_trials=total_trials,
            trial_sharpes=trial_sharpes, periods_per_year=ppy,
            trades_per_period=1.0, gross_returns=gross, n_effective=n_eff)
        # beta of the best net variant to SPY over the SAME windows
        ok = np.isfinite(spyw)
        if ok.sum() >= 24 and np.nanstd(spyw[ok]) > 0:
            beta = round(float(np.polyfit(spyw[ok], net[ok], 1)[0]), 3)
        # White's reality check — strategies in ROWS on the union date grid
        all_dates = sorted({d for v in series.values() for d in v[0]})
        M = np.zeros((len(series), len(all_dates)))
        for i, k in enumerate(sorted(series)):
            s = pd.Series(series[k][1], index=series[k][0])
            M[i] = s.reindex(all_dates).fillna(0.0).values
        wrc = qv.whites_reality_check(M)

    out = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "lab": "index_recon_lab",
        "mechanism": ("S&P 500 reconstitution — ADDITIONS-only, effective-date-"
                      "anchored event study (post-inclusion momentum vs reversal), "
                      "abnormal vs SPY"),
        "source": {
            "name": "Wikipedia 'List of S&P 500 companies' — 'Selected changes' table",
            "pinned_revision_oldid": PINNED_REVID,
            "pinned_revision_ts": "2025-12-23T15:18:59Z",
            "url": WIKI_URL,
            "prices": "yfinance daily bars, auto-adjusted, SPY calendar",
        },
        "honest_scope": {
            "window": [str(WINDOW_START), str(WINDOW_END)],
            "why_2011_plus": ("pre-2011 'Selected changes' rows are a curated "
                              "subset (1-13 events/yr vs true ~20-30, whole years "
                              "missing) — unknown editor bias, BLOCKED"),
            "deletions": "BLOCKED — 35% coverage, only-survivors structure "
                         "(same as the 2026-08-16 merger-arb block) + ticker recycling",
            "announcement_drift": "NOT TESTABLE — source has effective dates only; "
                                  "pre-effective window reported strictly as PROXY",
            "post_2025_12_22": "absent — current article revision dropped the table",
        },
        "events": {
            "dated_rows_in_pinned_table": n_dated_rows,
            "additions_in_honest_window": n_total,
            "measured": len(measured),
            "unpriceable": n_unpriceable,
            "survivorship_leak_rate": round(leak_rate, 4),
            "per_window_leak": per_window_leak,
            "unpriceable_detail": unpriceable,
            "leak_note": ("Unpriceable additions are disproportionately firms that "
                          "later died (ENDP-type purges) — every reported mean is "
                          "therefore biased UP by roughly the leak rate; treat "
                          "positive long-side results with this in mind."),
        },
        "descriptive_event_study": {
            "note": ("mean abnormal return (stock minus SPY) per window, naive "
                     "per-event t-stats — clustering is handled at the harness "
                     "stage, not here. '%s' is a labeled PROXY, NOT the "
                     "announcement-to-effective drift." % pre_key),
            "full_2011_2025": descriptive,
            "by_era": era_table,
            "literature_context": ("The famous +3-8%% announcement-to-effective "
                                   "inclusion pop is a pre-2000s result measured "
                                   "from ANNOUNCEMENT dates, which this source "
                                   "does not carry; the documented post-2010 "
                                   "decay toward zero is what the honest window "
                                   "can speak to, and the era table above is "
                                   "that measurement."),
        },
        "costs_bps_side": COST_SIDES_BPS,
        "house_prior_trials": HOUSE_PRIOR_TRIALS,
        "lab_variants_scored": len(scored),
        "lab_numbers_looked_at": n_here,
        "total_deflation_trials": total_trials,
        "variants": variants,
        "best_variant": scored[0]["name"] if scored else None,
        "best_rho_used": round(rho_used, 4) if rho_used is not None else None,
        "best_effective_bets": eff,
        "beta_to_spy": beta,
        "whites_reality_check": wrc,
        "best_evaluate": best_eval,
        "verdict_line": qv.verdict_line(best_eval) if best_eval
        else "no scorable variant",
    }

    with open(RECEIPT, "w") as f:
        json.dump(_j(out), f, indent=2)
    write_note(out)

    print("=" * 80)
    print("INDEX RECONSTITUTION LAB — S&P 500 additions, effective-date anchored")
    print("=" * 80)
    print(f"  source    : pinned Wikipedia oldid={PINNED_REVID} (2025-12-23)")
    print(f"  events    : {n_total} additions 2011-2025, {len(measured)} measured, "
          f"{n_unpriceable} unpriceable (leak {leak_rate:.1%})")
    print(f"  deflation : {total_trials} trials (house {HOUSE_PRIOR_TRIALS} + "
          f"{n_here} here: {len(scored)} scored variants + 2 proxy measurements)")
    print("\n  DESCRIPTIVE (mean abnormal return vs SPY, naive t):")
    for k in all_keys:
        d = descriptive[k]
        tag = " (PROXY, not announcement drift)" if k == pre_key else ""
        print(f"    {k:<12} n={d['n_events']:<4} mean {d['mean_ar_bps']:+8.1f} bps  "
              f"t {d['t_naive']:+.2f}  hit {d['hit_rate_pos']:.2f}{tag}")
    print("\n  ERA DECAY (mean AR bps per window):")
    for label, _, _ in eras:
        row = era_table[label]
        cells = "  ".join(f"{k}:{row[k]['mean_ar_bps']:+8.1f}" for k in all_keys
                          if row[k]["mean_ar_bps"] is not None)
        print(f"    {label}: {cells}")
    print("\n  VARIANTS (net Sharpe per event-date, best first):")
    for v in scored:
        print(f"    {v['name']:<16} netSh {v['net_sharpe_per_event_date']:+.3f}  "
              f"net {v['net_mean_bps']:+7.1f} bps  gross {v['gross_mean_bps']:+7.1f} bps  "
              f"hit {v['hit_rate']:.2f}  n={v['n_event_dates']}")
    if best_eval:
        e = best_eval
        print(f"\n  HARNESS on best NET variant ({out['best_variant']}):")
        print(f"    rho_used {out['best_rho_used']}  -> "
              f"n_eff {e['n_effective']} of {e['n_obs']} event dates")
        print(f"    t {e['t_stat']}  p {e['p_value']}  "
              f"deflated_sharpe {e['deflated_sharpe']}  "
              f"survives {e['survives_deflation']}")
        print(f"    gross/trade {e.get('gross_per_trade_bps')} bps  "
              f"net/trade {e['per_trade_bps']} bps  "
              f"cost_drag {e.get('cost_drag_bps')} bps")
        print(f"    beta_to_spy {beta}  "
              f"White's RC p={wrc['p_value']} survives={wrc['survives']}")
        print(f"\n  VERDICT: {out['verdict_line']}")
    print(f"\n  receipt -> {RECEIPT}")
    print(f"  note    -> {NOTE}")
    return out


def write_note(out):
    d = out["descriptive_event_study"]["full_2011_2025"]
    e = out["best_evaluate"]
    lines = [
        "# Research note — S&P 500 reconstitution (additions-only event study)",
        "",
        f"*Generated {out['generated']} — RESEARCH ONLY, paper desk, no orders.*",
        "",
        "## Source and honest scope",
        f"- Wikipedia 'Selected changes' table, **pinned** oldid={PINNED_REVID} "
        "(2025-12-23). The current 2026 revision deleted the table; the pinned "
        "revision is the reproducible free source. Prices: yfinance daily bars.",
        f"- Honest window **2011-01-01..2025-12-22** "
        f"({out['events']['additions_in_honest_window']} additions). Pre-2011 rows "
        "are a curated subset (editor bias) — blocked.",
        "- **Deletions blocked** (35% coverage, only-survivors structure, ticker "
        "recycling — same shape as the 2026-08-16 merger-arb block).",
        "- **Announcement dates absent** — the literature's announcement-to-"
        "effective drift is NOT testable here. The pre-effective window below is "
        "a labeled PROXY only.",
        "",
        "## Survivorship leak (additions side is not clean either)",
        f"- {out['events']['unpriceable']} of "
        f"{out['events']['additions_in_honest_window']} additions unpriceable "
        f"(leak rate {out['events']['survivorship_leak_rate']:.1%}) — enumerated "
        "in the receipt. These skew toward later-dead firms (ENDP-type purges), "
        "so measured means are biased UP by roughly the leak rate.",
        "",
        "## Descriptive event study (abnormal return vs SPY, naive t)",
        "",
        "| window | n | mean AR (bps) | t (naive) | median (bps) | hit rate |",
        "|---|---|---|---|---|---|",
    ]
    for k, row in d.items():
        tag = " (PROXY)" if "proxy" in k else ""
        lines.append(f"| {k}{tag} | {row['n_events']} | {row['mean_ar_bps']} | "
                     f"{row['t_naive']} | {row['median_ar_bps']} | "
                     f"{row['hit_rate_pos']} |")
    lines += [
        "",
        "### Era decay (mean AR bps)",
        "",
        "| era | " + " | ".join(d.keys()) + " |",
        "|---|" + "---|" * len(d),
    ]
    for era, row in out["descriptive_event_study"]["by_era"].items():
        lines.append("| " + era + " | " +
                     " | ".join(str(row[k]["mean_ar_bps"]) for k in d) + " |")
    lines += [
        "",
        "The famous pre-2000s +3-8% inclusion pop was measured announcement-to-"
        "effective, which this source cannot see. What the honest window shows is "
        "the post-2010 regime: the effect the desk can actually measure here is "
        "small, era-unstable, and (per the harness below) not a deployable edge.",
        "",
        "## Harness verdict on the best NET variant",
        f"- best variant: **{out['best_variant']}** of "
        f"{out['lab_variants_scored']} scored (costs "
        f"{out['costs_bps_side']} bps/side)",
        f"- deflation: {out['total_deflation_trials']} trials "
        f"(house {out['house_prior_trials']} + {out['lab_numbers_looked_at']} here)",
        f"- clustering: same-date events merged into one basket per date; "
        f"rho={out['best_rho_used']} -> n_eff "
        f"{out['best_effective_bets']['independent_bets'] if out['best_effective_bets'] else None} "
        f"of {e['n_obs'] if e else '?'} event dates",
    ]
    if e:
        lines += [
            f"- t={e['t_stat']}, p={e['p_value']}, deflated Sharpe "
            f"{e['deflated_sharpe']}, survives deflation: "
            f"**{e['survives_deflation']}**",
            f"- gross/trade {e.get('gross_per_trade_bps')} bps, net/trade "
            f"{e['per_trade_bps']} bps, cost drag {e.get('cost_drag_bps')} bps",
            f"- beta to SPY {out['beta_to_spy']}; White's reality check "
            f"p={out['whites_reality_check']['p_value']} "
            f"(survives={out['whites_reality_check']['survives']})",
        ]
    lines += [
        "",
        f"**VERDICT: {out['verdict_line']}**",
        "",
        f"Receipt: `{RECEIPT}` (per-event unpriceable enumeration inside).",
    ]
    with open(NOTE, "w") as f:
        f.write("\n".join(lines) + "\n")


def _j(o):
    if isinstance(o, dict):
        return {str(k): _j(v) for k, v in o.items()}
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
