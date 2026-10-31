#!/usr/bin/env python3
"""pead_lab.py — Honest COLD test of POST-EARNINGS-ANNOUNCEMENT DRIFT (PEAD),
queue item #3 of HUNT_BACKLOG.md (data-gated: "needs earnings dates + surprise").

RESEARCH ONLY. Paper desk. No orders, no capital, no pre-registration, no secrets.
Read-only w.r.t. the engine. Writes only its own JSON receipt + note.

WHY DISTINCT
------------
PEAD (Ball & Brown 1968; Bernard & Thomas 1989; Foster-Olsen-Shevlin 1984) says
prices UNDER-react to an earnings surprise and keep drifting in the surprise's
direction for weeks. The signal is an ACCOUNTING event plus its announcement date
— not a price-history factor. Distinct from every mechanism this house has killed
(momentum, mean-rev, breakout, value, sentiment, carry, trend, positioning).

DATA PATH (probed before building — free + honest, no fabrication)
------------------------------------------------------------------
* Announcement dates : SEC EDGAR submissions API, 8-K filings carrying ITEM 2.02
  ("Results of Operations"). Structured, free, ~90 per large-cap back to 2004
  (item numbering became reliable Aug-2004). This is the REAL announcement date,
  not the 10-Q filing date.
* Earnings           : SEC EDGAR XBRL companyconcept, EarningsPerShareDiluted /
  Basic. Quarterly facts identified by 80-100 day durations. POINT-IN-TIME: for
  each period we keep the EARLIEST-FILED instance, i.e. the originally-reported
  figure, never a later restatement. (The xbrl/frames endpoint is cheaper —
  one request per calendar quarter — but was rejected here: its records carry
  accession numbers filed YEARS after the period, i.e. restated values. Probed
  and refused rather than used.)
* Surprise           : SUE, the seasonal-random-walk standardised unexpected
  earnings — (EPS_q - EPS_q-4) / stdev of the trailing seasonal differences. It
  needs NO analyst estimates, so it needs no paid feed. Plus a purely price-based
  cousin: CAR3, the announcement-window abnormal return (the market's own read of
  the surprise), which needs only the free announcement date.
* Prices             : yfinance, split/dividend adjusted.

KNOWN BIASES (stated, not hidden)
---------------------------------
1. SURVIVORSHIP: the universe is built from companies LISTED TODAY that were also
   filing in 2010. Firms that were acquired or died are absent. PEAD estimated on
   survivors is biased UPWARD. Quantified as best we can: the count of names lost
   at each screen is reported.
2. The XBRL era starts ~2009, so the panel cannot reach the 1970s-80s samples
   where PEAD was largest.
3. EPS from XBRL is the filed figure; a press-release EPS (often "adjusted") can
   differ. SUE here is a GAAP surprise. CAR3 is immune to this and is tested
   alongside precisely as the cross-check.

Report real output, win or lose.
"""
import json
import math
import os
import random
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

import quant_validation as qv

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = ("/private/tmp/claude-501/-Users-test/"
         "4c4f7229-ae34-4794-9872-47924f47fa70/scratchpad/pead_cache")
os.makedirs(CACHE, exist_ok=True)
RECEIPT = os.path.join(BASE, "reports", "pead_lab.json")
NOTE = os.path.join(BASE, "reports", "research_pead.md")

HOUSE_PRIOR_TRIALS = 1227          # HUNT_BACKLOG.md, 2026-08-14
UA = __import__("sec_contact").user_agent()
N_UNIVERSE = 400
SEED = 20260816
VOL_TARGET = 0.10
TERCILE = 1.0 / 3.0

# Sweeps (every combination is a trial and is counted into the deflation bar)
COST_BPS_SIDE = [5.0, 10.0, 20.0]
BORROW_ANNUAL = [0.0025, 0.01]
STALE_DAYS = [30, 60, 90]          # how long after the announcement a signal is used

_rate_lock = threading.Lock()
_last_call = [0.0]


# ----------------------------------------------------------------- SEC plumbing
def sec_get(url, tag):
    """Throttled, cached, retried SEC fetch. Returns parsed JSON or None."""
    path = os.path.join(CACHE, re.sub(r"[^A-Za-z0-9_.-]", "_", tag) + ".json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    for attempt in range(3):
        with _rate_lock:                       # global <= ~8 req/s (SEC allows 10)
            gap = time.time() - _last_call[0]
            if gap < 0.125:
                time.sleep(0.125 - gap)
            _last_call[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            with open(path, "w") as f:
                json.dump(data, f)
            return data
        except Exception:
            if attempt == 2:
                with open(path, "w") as f:
                    json.dump(None, f)         # negative-cache: don't re-hammer
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def ticker_cik_map():
    d = sec_get("https://www.sec.gov/files/company_tickers.json", "company_tickers")
    if not d:
        raise RuntimeError("cannot reach SEC ticker map")
    return {v["ticker"]: str(v["cik_str"]).zfill(10) for v in d.values()}


def frame_ciks(frame):
    """CIK set present in an EPS frame — used ONLY to pick the universe (never for
    the signal itself, so its restatement problem cannot leak into a return)."""
    d = sec_get("https://data.sec.gov/api/xbrl/frames/us-gaap/"
                "EarningsPerShareDiluted/USD-per-shares/%s.json" % frame,
                "frame_%s" % frame)
    return {int(x["cik"]) for x in d["data"]} if d else set()


def fetch_eps(cik):
    """POINT-IN-TIME quarterly EPS: {period_end(date): (value, first_filed(date))}."""
    out = {}
    for concept in ("EarningsPerShareDiluted", "EarningsPerShareBasic"):
        d = sec_get("https://data.sec.gov/api/xbrl/companyconcept/CIK%s/us-gaap/%s.json"
                    % (cik, concept), "cc_%s_%s" % (cik, concept))
        if not d:
            continue
        for arr in d.get("units", {}).values():       # KO-style 'pure' unit quirk
            for x in arr:
                if not x.get("start") or x.get("val") is None:
                    continue
                try:
                    s = date.fromisoformat(x["start"])
                    e = date.fromisoformat(x["end"])
                    fl = date.fromisoformat(x["filed"])
                except Exception:
                    continue
                if not (80 <= (e - s).days <= 100):
                    continue
                cur = out.get(e)
                if cur is None or fl < cur[1]:        # earliest filed = as-reported
                    out[e] = (float(x["val"]), fl)
        if len(out) >= 20:
            break
    return out


def fetch_announcements(cik):
    """8-K item 2.02 filing dates (the earnings releases), ascending."""
    d = sec_get("https://data.sec.gov/submissions/CIK%s.json" % cik, "sub_%s" % cik)
    if not d:
        return []
    dates = []

    def scan(rec):
        for form, fd, items in zip(rec.get("form", []), rec.get("filingDate", []),
                                   rec.get("items", [])):
            if form == "8-K" and "2.02" in (items or ""):
                dates.append(fd)

    scan(d.get("filings", {}).get("recent", {}))
    for f in d.get("filings", {}).get("files", []):
        o = sec_get("https://data.sec.gov/submissions/" + f["name"], "sub_%s" % f["name"])
        if o:
            scan(o)
    return sorted({date.fromisoformat(x) for x in dates})


# ----------------------------------------------------------------- universe
def build_universe():
    """Long-listed US filers: in the EPS frame in 2010 AND 2025, still tickered
    today. Deterministic random sample of N_UNIVERSE — sampled, not hand-picked,
    so the head of desk cannot cherry-pick names that worked."""
    tmap = ticker_cik_map()
    old, new = frame_ciks("CY2010Q1"), frame_ciks("CY2025Q1")
    survivors = old & new
    by_cik = {}
    for tkr, cik in tmap.items():
        c = int(cik)
        if c in survivors and re.fullmatch(r"[A-Z]{1,5}", tkr):
            by_cik.setdefault(c, tkr)          # one ticker per filer
    pairs = sorted(by_cik.items())
    rng = random.Random(SEED)
    sample = pairs if len(pairs) <= N_UNIVERSE else rng.sample(pairs, N_UNIVERSE)
    return {t: str(c).zfill(10) for c, t in sorted(sample, key=lambda p: p[1])}, {
        "filers_2010": len(old), "filers_2025": len(new),
        "survivors_both": len(survivors), "tickered_today": len(by_cik),
        "sampled": len(sample),
    }


# ----------------------------------------------------------------- prices
def daily_prices(tickers):
    path = os.path.join(CACHE, "px_daily.csv")
    if os.path.exists(path):
        return pd.read_csv(path, index_col=0, parse_dates=True)
    import yfinance as yf
    frames = []
    todo = list(tickers) + ["SPY"]
    for i in range(0, len(todo), 60):
        chunk = todo[i:i + 60]
        raw = yf.download(chunk, start="2006-01-01", auto_adjust=True,
                          progress=False, threads=True)
        cl = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
        if not isinstance(raw.columns, pd.MultiIndex):
            cl.columns = chunk
        frames.append(cl)
    px = pd.concat(frames, axis=1)
    px = px.loc[:, ~px.columns.duplicated()]
    px.index = pd.DatetimeIndex(px.index).tz_localize(None)
    px.to_csv(path)
    return px


# ----------------------------------------------------------------- events
def build_events(universe, px):
    """One row per (ticker, earnings announcement): SUE, CAR3, availability date."""
    rows, stats = [], {"no_eps": 0, "no_ann": 0, "no_px": 0, "ok": 0}
    spy = px["SPY"].dropna() if "SPY" in px.columns else pd.Series(dtype=float)
    spy_r = spy.pct_change()

    def work(item):
        tkr, cik = item
        eps = fetch_eps(cik)
        ann = fetch_announcements(cik)
        return tkr, eps, ann

    with ThreadPoolExecutor(max_workers=4) as ex:
        fetched = list(ex.map(work, universe.items()))

    for tkr, eps, ann in fetched:
        if len(eps) < 12:
            stats["no_eps"] += 1
            continue
        if len(ann) < 8:
            stats["no_ann"] += 1
            continue
        if tkr not in px.columns or px[tkr].dropna().size < 500:
            stats["no_px"] += 1
            continue
        ser = px[tkr].dropna()
        ret = ser.pct_change()
        ends = sorted(eps)
        vals = {e: eps[e][0] for e in ends}
        for i, e in enumerate(ends):
            # seasonal partner ~1 year earlier
            prior = [p for p in ends[:i] if 350 <= (e - p).days <= 380]
            if not prior:
                continue
            diffs = []
            for j, ee in enumerate(ends[:i + 1]):
                pp = [p for p in ends[:j] if 350 <= (ee - p).days <= 380]
                if pp:
                    diffs.append(vals[ee] - vals[pp[-1]])
            if len(diffs) < 7:                      # need a real dispersion estimate
                continue
            hist = diffs[-9:-1]                     # trailing 8, EXCLUDING current
            sd = float(np.std(hist, ddof=1))
            if not sd > 0:
                continue
            sue = (vals[e] - vals[prior[-1]]) / sd
            if not np.isfinite(sue):
                continue
            # announcement = first 8-K/2.02 on or after period end, within 90 days
            cand = [a for a in ann if 0 <= (a - e).days <= 90]
            if not cand:
                continue
            a0 = cand[0]
            ts = pd.Timestamp(a0)
            loc = ser.index.searchsorted(ts)
            if loc < 2 or loc + 2 >= ser.size:
                continue
            # CAR3: day -1..+1 around the release, abnormal vs SPY
            win = ret.iloc[loc - 1:loc + 2]
            bwin = spy_r.reindex(win.index).fillna(0.0)
            car3 = float((win.fillna(0.0) - bwin).sum())
            if not np.isfinite(car3):
                continue
            # availability: 2 trading days after the release (releases are often
            # after the close) — nothing is tradeable before this date
            avail = ser.index[min(loc + 2, ser.size - 1)]
            rows.append({"ticker": tkr, "period_end": e, "ann": ts,
                         "avail": avail, "sue": sue, "car3": car3})
        stats["ok"] += 1
    ev = pd.DataFrame(rows)
    if not ev.empty:
        # winsorise SUE tails (a tiny denominator makes absurd values)
        lo, hi = ev["sue"].quantile([0.01, 0.99])
        ev["sue"] = ev["sue"].clip(lo, hi)
    return ev, stats


# ----------------------------------------------------------------- backtest
def monthly_returns(px, cols):
    m = px[cols].resample("ME").last()
    return m.pct_change()


def signal_panel(ev, month_index, field, stale_days):
    """For each month end, the most recent per-ticker signal still inside the
    drift window. Uses only information available on or before that month end."""
    out = pd.DataFrame(index=month_index, columns=sorted(ev["ticker"].unique()),
                       dtype=float)
    for tkr, g in ev.groupby("ticker"):
        g = g.sort_values("avail")
        av = g["avail"].values
        sv = g[field].values
        for m in month_index:
            k = np.searchsorted(av, np.datetime64(m), side="right") - 1
            if k < 0:
                continue
            age = (m - pd.Timestamp(av[k])).days
            if 0 <= age <= stale_days:
                out.at[m, tkr] = sv[k]
    return out.dropna(axis=1, how="all")


def factor(R, SIG, cost_bps, borrow_annual):
    """Dollar-neutral tercile factor, long high signal / short low signal."""
    idx = R.index
    dates, net, gross, turns = [], [], [], []
    prev_w = pd.Series(0.0, index=R.columns)
    rank_prev, autocorrs, breadth = None, [], []
    bm = borrow_annual / 12.0
    for i in range(1, len(idx)):
        t0, t1 = idx[i - 1], idx[i]
        if t0 not in SIG.index:
            continue
        sig = SIG.loc[t0].dropna()
        fr = R.loc[t1]
        names = [n for n in sig.index if n in fr.index and not np.isnan(fr[n])]
        sig = sig[names]
        if len(sig) < 12:
            continue
        ranks = sig.rank()
        if rank_prev is not None:
            c = ranks.index.intersection(rank_prev.index)
            if len(c) >= 6:
                autocorrs.append(np.corrcoef(ranks[c], rank_prev[c])[0, 1])
        rank_prev = ranks
        k = max(1, int(len(sig) * TERCILE))
        order = sig.sort_values()
        low, high = order.index[:k], order.index[-k:]
        w = pd.Series(0.0, index=R.columns)
        w[high] = 0.5 / k
        w[low] = -0.5 / k
        g = float((w * R.loc[t1].reindex(w.index).fillna(0.0)).sum())
        turnover = float((w - prev_w).abs().sum())
        cost = turnover * (cost_bps / 10000.0)
        short_cost = float(w[w < 0].abs().sum()) * bm
        dates.append(t1)
        gross.append(g)
        net.append(g - cost - short_cost)
        turns.append(turnover)
        breadth.append(len(sig))
        prev_w = w
    ac = float(np.nanmean(autocorrs)) if autocorrs else 0.5
    di = pd.DatetimeIndex(dates)
    return (pd.Series(net, index=di), pd.Series(gross, index=di),
            pd.Series(turns, index=di), ac, float(np.mean(breadth)) if breadth else 0.0)


def vol_scale(s, target=VOL_TARGET):
    v = s.std(ddof=1) * math.sqrt(12)
    return s * (target / v) if v > 0 else s


def maxdd(s):
    eq = (1 + s).cumprod()
    return float((eq / eq.cummax() - 1).min())


def crash(s, lo, hi):
    w = s[(s.index >= lo) & (s.index <= hi)]
    return round(float((1 + w).prod() - 1) * 100, 2) if len(w) else None


def beta_spy(s, px):
    m = px["SPY"].resample("ME").last().pct_change()
    c = s.index.intersection(m.index)
    if len(c) < 24:
        return None
    x, y = m.reindex(c).values, s.reindex(c).values
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 24 or np.nanstd(x[ok]) <= 0:
        return None
    return round(float(np.polyfit(x[ok], y[ok], 1)[0]), 3)


# ----------------------------------------------------------------- main
def main():
    universe, uni_stats = build_universe()
    px = daily_prices(list(universe))
    ev, stats = build_events(universe, px)
    if ev.empty:
        raise RuntimeError("no events built — refusing to report anything")

    cols = [c for c in sorted(ev["ticker"].unique()) if c in px.columns]
    R = monthly_returns(px, cols)
    R = R[R.index >= pd.Timestamp("2010-01-31")]

    variants, trial_sharpes, series = [], [], {}
    n_here = 0
    for field in ("sue", "car3"):
        for stale in STALE_DAYS:
            SIG = signal_panel(ev, R.index, field, stale)
            for cost in COST_BPS_SIDE:
                for borrow in BORROW_ANNUAL:
                    net, gross, turn, ac, brd = factor(R[SIG.columns], SIG, cost, borrow)
                    n_here += 1
                    if net.size < 24:
                        continue
                    key = "%s_s%d_c%d_b%d" % (field, stale, int(cost), int(borrow * 10000))
                    m = qv.moments(net.values)
                    trial_sharpes.append(m["sharpe"])
                    series[key] = (net, gross, ac)
                    variants.append({
                        "name": key, "signal": field, "stale_days": stale,
                        "cost_bps_side": cost, "borrow_annual": borrow,
                        "n_months": int(net.size), "avg_names": round(brd, 1),
                        "net_sharpe_ann": round(m["sharpe"] * math.sqrt(12), 3),
                        "net_cagr_pct": round(((1 + net).prod() ** (12 / max(1, net.size)) - 1) * 100, 2),
                        "gross_sharpe_ann": round(qv.moments(gross.values)["sharpe"] * math.sqrt(12), 3),
                        "maxdd_pct": round(maxdd(net) * 100, 2),
                        "avg_turnover": round(float(turn.mean()), 3),
                        "rank_autocorr": round(ac, 3),
                    })

    if not variants:
        raise RuntimeError("no variant produced a usable series")
    scored = sorted(variants, key=lambda v: v["net_sharpe_ann"], reverse=True)
    best = scored[0]
    net, gross, ac = series[best["name"]]
    net_s = vol_scale(net)
    # The vol target is a LEVERAGE choice; it must be applied to the gross series
    # with the SAME factor, or evaluate() compares a levered net against an
    # unlevered gross and reports a NEGATIVE cost drag (costs appearing to add
    # return). Scale both by one factor so gross-vs-net stays apples-to-apples.
    lev = float(net_s.iloc[0] / net.iloc[0]) if net.iloc[0] not in (0.0,) else (
        (VOL_TARGET / (net.std(ddof=1) * math.sqrt(12))) if net.std(ddof=1) > 0 else 1.0)
    gross_s = gross.reindex(net_s.index) * lev
    eff = qv.effective_bets(net_s.size, ac)
    ev_res = qv.evaluate("pead_" + best["name"], net_s.values,
                         n_trials=HOUSE_PRIOR_TRIALS + n_here,
                         trial_sharpes=trial_sharpes, periods_per_year=12,
                         trades_per_period=1.0,
                         gross_returns=gross_s.values,
                         n_effective=eff["independent_bets"])
    # strategies in ROWS (2026-08-14 audit lesson)
    wrc = qv.whites_reality_check(
        np.vstack([series[k][0].reindex(net_s.index).fillna(0).values for k in series]))

    # honest cross-check: does the raw event even show a drift? (no portfolio, no costs)
    fwd = []
    for _, r in ev.iterrows():
        s = px[r["ticker"]].dropna()
        loc = s.index.searchsorted(r["avail"])
        if loc + 21 < s.size:
            fwd.append((r["sue"], float(s.iloc[loc + 21] / s.iloc[loc] - 1)))
    fwd = pd.DataFrame(fwd, columns=["sue", "r21"]).dropna()
    ic = float(fwd["sue"].corr(fwd["r21"], method="spearman")) if len(fwd) > 100 else None
    ic_t = (ic * math.sqrt(len(fwd) - 2) / math.sqrt(1 - ic ** 2)) if ic not in (None, 1.0) else None

    out = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "mechanism": "post-earnings-announcement drift (PEAD) — SEC 8-K item 2.02 dates "
                     "+ point-in-time XBRL EPS surprise (SUE) and announcement CAR3",
        "queue_item": "HUNT_BACKLOG #3 (data-gated) — data path PROBED and viable, free",
        "data_path": {
            "announcement_dates": "SEC EDGAR submissions API, 8-K item 2.02",
            "earnings": "SEC EDGAR XBRL companyconcept, earliest-filed = as-reported (point-in-time)",
            "frames_endpoint": "REJECTED — carries restated values (accession filed years later)",
            "prices": "yfinance adjusted close",
            "cost": "free, no credentials",
        },
        "universe_build": uni_stats,
        "event_stats": stats,
        "n_events": int(len(ev)),
        "n_names_with_events": int(ev["ticker"].nunique()),
        "event_span": [str(ev["ann"].min().date()), str(ev["ann"].max().date())],
        "panel_start": str(R.index[0].date()), "panel_end": str(R.index[-1].date()),
        "house_prior_trials": HOUSE_PRIOR_TRIALS, "lab_variants": n_here,
        "total_deflation_trials": HOUSE_PRIOR_TRIALS + n_here,
        "raw_event_drift": {
            "n_events_scored": int(len(fwd)),
            "spearman_ic_sue_vs_next21d": round(ic, 4) if ic is not None else None,
            "ic_t_stat": round(ic_t, 2) if ic_t is not None else None,
            "note": "gross, no costs, no portfolio — is the underlying effect even there?",
        },
        "best_variant": best["name"],
        "vol_target_leverage": round(lev, 3),
        "best_rank_autocorr": round(ac, 3),
        "best_effective_bets": eff,
        "beta_to_SPY": beta_spy(net_s, px),
        "crash_2020_pct": crash(net_s, pd.Timestamp("2020-02-01"), pd.Timestamp("2020-04-30")),
        "crash_2022_pct": crash(net_s, pd.Timestamp("2022-01-01"), pd.Timestamp("2022-10-31")),
        "biases_disclosed": [
            "survivorship: universe = filers alive in 2010 AND 2025, tickered today",
            "XBRL era only (panel starts 2010) — misses the large 1970s-80s PEAD sample",
            "SUE is a GAAP surprise, not an analyst-estimate surprise; CAR3 tested as cross-check",
        ],
        "variants": variants,
        "whites_reality_check": wrc,
        "best_evaluate": ev_res,
        "verdict_line": qv.verdict_line(ev_res),
    }
    with open(RECEIPT, "w") as f:
        json.dump(_j(out), f, indent=2)

    print("=" * 82)
    print("PEAD LAB — post-earnings-announcement drift (EDGAR 8-K 2.02 + PIT XBRL EPS)")
    print("=" * 82)
    print("  universe   : %d sampled from %d long-listed filers (survivors 2010&2025)"
          % (uni_stats["sampled"], uni_stats["tickered_today"]))
    print("  usable     : %d names, %d events, %s .. %s"
          % (out["n_names_with_events"], out["n_events"], *out["event_span"]))
    print("  dropped    : no_eps %d  no_ann %d  no_px %d" %
          (stats["no_eps"], stats["no_ann"], stats["no_px"]))
    print("  deflation  : %d trials (house %d + %d here)"
          % (out["total_deflation_trials"], HOUSE_PRIOR_TRIALS, n_here))
    print("  raw drift  : SUE vs next-21d IC %s (t=%s) on %s events"
          % (out["raw_event_drift"]["spearman_ic_sue_vs_next21d"],
             out["raw_event_drift"]["ic_t_stat"], out["raw_event_drift"]["n_events_scored"]))
    print("  top variants (annualised NET Sharpe):")
    for v in scored[:8]:
        print("    %-24s netSh %+.3f  grossSh %+.3f  CAGR %+.2f%%  maxDD %.1f%%  "
              "turn %.2f  names %.0f  n=%d"
              % (v["name"], v["net_sharpe_ann"], v["gross_sharpe_ann"], v["net_cagr_pct"],
                 v["maxdd_pct"], v["avg_turnover"], v["avg_names"], v["n_months"]))
    print("\n  beta->SPY %s   crash 2020 %s%%  2022 %s%%"
          % (out["beta_to_SPY"], out["crash_2020_pct"], out["crash_2022_pct"]))
    print("  White's reality check p=%s survives %s" % (wrc["p_value"], wrc["survives"]))
    print("\n  HARNESS on best NET variant (vol-targeted to 10%):")
    print("    n_obs %s  n_effective %s  t %s  p %s"
          % (ev_res["n_obs"], ev_res["n_effective"], ev_res["t_stat"], ev_res["p_value"]))
    print("    gross/trade %sbps  net/trade %sbps  cost drag %sbps"
          % (ev_res.get("gross_per_trade_bps"), ev_res["per_trade_bps"],
             ev_res.get("cost_drag_bps")))
    print("    deflated_sharpe %s  survives_deflation %s"
          % (ev_res["deflated_sharpe"], ev_res["survives_deflation"]))
    print("\n  VERDICT: %s" % out["verdict_line"])
    print("\n  receipt -> %s" % RECEIPT)
    return out


def _j(o):
    if isinstance(o, dict):
        return {k: _j(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_j(x) for x in o]
    if isinstance(o, (np.floating, float)):
        return None if (math.isnan(o) or math.isinf(o)) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, date)):
        return str(o)
    return o


if __name__ == "__main__":
    main()
