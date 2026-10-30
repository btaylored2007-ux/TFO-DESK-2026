#!/usr/bin/env python3
"""gold_basis_lab.py — Honest COLD test of the PAXG (Kraken) vs GLD (NYSE Arca)
gold basis as a tradeable, after-cost, mean-reverting relationship.

RESEARCH ONLY. Paper. No capital, no orders, no pre-registration, no engine touched.
Funder-directed 2026-08-27.

THE IDEA
--------
PAXG is a tokenised fine troy ounce of LBMA good-delivery gold (Paxos, redeemable).
GLD is a physically-backed gold ETF (SPDR, ~0.09 oz gold per share, redeemable in
baskets by APs). Both are claims on the same metal, so log(PAXG) - log(GLD * oz_per_share)
is ECONOMICALLY ANCHORED and must mean-revert. That is a genuine structural
relationship, not a mined pattern -- and it sits in the BASIS family, the only
family that has ever survived on this desk.

The question is not "does it mean-revert" (it must). The question is whether it ever
diverges by MORE THAN IT COSTS TO CLOSE, and often enough to matter.

THE FOUR TRAPS, HANDLED EXPLICITLY
----------------------------------
1. TIMESTAMP MISMATCH. GLD's official close is 16:00 ET (20:00/21:00 UTC depending
   on DST). Kraken's PAXG daily bar closes 00:00 UTC -- 3-4h later. Differencing
   those two closes measures GOLD'S OWN OVERNIGHT MOVE, not a basis, and it is not
   tradeable because the two prints never coexisted.
   FIX: we reconstruct PAXG's price AT THE INSTANT OF GLD'S CLOSE from Kraken's
   public *Trades* feed (which, unlike OHLC, paginates arbitrarily far back), and
   quote the mismatch-induced fake divergence alongside so the size of the trap is
   on the record.

2. GLD's GOLD-PER-SHARE DECAYS. GLD's 0.40%/yr sponsor fee is paid in metal, so
   each share represents monotonically less gold and the raw PAXG/GLD ratio TRENDS
   UP mechanically. That trend is an accounting fact, not an edge.
   FIX: fit the log-linear drift, report it, and de-trend before measuring any
   deviation. The backtest uses a CAUSAL expanding-window de-trend (no look-ahead).
   IAU (0.25%/yr fee) is the falsification cross-check: if we are really measuring
   sponsor-fee decay, GLD's fitted drift must exceed IAU's by ~15 bps/yr.

3. REAL COSTS ON BOTH LEGS, INCLUDING THE SHORT. Measured, not assumed:
   Kraken's own AssetPairs endpoint gives the PAXG fee ladder (40 bps taker /
   25 bps maker at the base tier -- NOT the ~10 bps a desk might assume), the live
   Depth endpoint gives the book spread, and leverage_sell tells us whether the
   short is executable at all. Margin financing is MODELLED and flagged as such.

4. SEARCH DEFLATION. Every variant tested here is counted and added to the house
   cumulative count of 2,542 (reports/research_liquidity_floor.md), and the
   survivor -- if any -- goes through quant_validation.py unmodified.

Usage:
    python3 gold_basis_lab.py --stage fetch     # populate the cache (slow, ~5 min)
    python3 gold_basis_lab.py --stage analyze   # analysis only, from cache
    python3 gold_basis_lab.py                   # both
"""
import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
import quant_validation as qv  # noqa: E402

CACHE = os.path.join(BASE, "reports", "gold_basis_cache")
RECEIPT = os.path.join(BASE, "reports", "gold_basis_lab.json")
REPORT = os.path.join(BASE, "reports", "research_gold_basis.md")

# House cumulative search count carried forward (reports/research_liquidity_floor.md)
HOUSE_PRIOR_TRIALS = 2542

KRAKEN_PAIR = "PAXGUSD"
# Sustainable public-counter rate for /Trades (cost 2, cap ~20, decay ~0.5/s).
TRADES_CALL_INTERVAL_S = 1.2
GLD_OZ_PER_SHARE_TODAY = None   # discovered from the data, not assumed

# ---------------------------------------------------------------------------
# Measured cost inputs (see fetch_cost_inputs) -- these are pulled live, not typed.
# ---------------------------------------------------------------------------
DEFAULT_COSTS = {
    # PAXG / Kraken
    "kraken_taker_bps": 40.0,       # base tier, from AssetPairs 'fees'
    "kraken_maker_bps": 25.0,       # base tier, from AssetPairs 'fees_maker'
    "paxg_half_spread_bps": 0.22,   # measured from Depth
    # GLD / US equity
    "gld_half_spread_bps": 0.5,     # ~1 bp quoted spread on a $300+ ETF
    "gld_commission_bps": 0.2,      # IBKR-style $0.005/share on a ~$300 share
    # financing on the short leg (MODELLED -- see report)
    "kraken_margin_open_bps": 2.0,       # 0.02% opening fee
    "kraken_margin_rollover_bps_4h": 2.0,  # 0.02% per 4h -> 12 bps/day
    "gld_borrow_bps_per_year": 40.0,     # GLD is general collateral / easy to borrow
}


# ===========================================================================
# STAGE 1 -- FETCH
# ===========================================================================
def _kraken(endpoint, params, tries=6):
    """Kraken public GET with visible, bounded backoff.

    Kraken's public counter is NOT 1 req/sec for every endpoint: /Trades costs 2
    counter units against a ~20 cap that decays at ~0.5/sec, so the sustainable
    rate is roughly one call per 4 seconds. Bursting faster works for ~50 calls
    and then every subsequent call errors -- which is exactly how the first
    attempt at this fetch silently wedged.
    """
    import requests
    for i in range(tries):
        try:
            r = requests.get("https://api.kraken.com/0/public/" + endpoint,
                             params=params, timeout=30)
            j = r.json()
            if j.get("error"):
                wait = 5 * (i + 1)
                print("      kraken %s error %s -- backoff %ds"
                      % (endpoint, j["error"], wait), flush=True)
                time.sleep(wait)
                continue
            return j["result"]
        except Exception as e:
            wait = 5 * (i + 1)
            print("      kraken %s exception %r -- backoff %ds"
                  % (endpoint, e, wait), flush=True)
            time.sleep(wait)
    raise RuntimeError("kraken %s failed after %d tries: %s"
                       % (endpoint, tries, params))


def fetch_cost_inputs():
    """Pull the REAL fee ladder, book spread and short-availability from Kraken."""
    ap = _kraken("AssetPairs", {"pair": KRAKEN_PAIR})[KRAKEN_PAIR]
    dep = _kraken("Depth", {"pair": KRAKEN_PAIR, "count": 20})[KRAKEN_PAIR]
    tic = _kraken("Ticker", {"pair": KRAKEN_PAIR})[KRAKEN_PAIR]

    bid = float(dep["bids"][0][0])
    ask = float(dep["asks"][0][0])
    mid = 0.5 * (bid + ask)
    out = {
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "fee_ladder_taker_pct": ap["fees"],
        "fee_ladder_maker_pct": ap["fees_maker"],
        "base_tier_taker_bps": float(ap["fees"][0][1]) * 100.0,
        "base_tier_maker_bps": float(ap["fees_maker"][0][1]) * 100.0,
        "leverage_sell": ap.get("leverage_sell"),
        "leverage_buy": ap.get("leverage_buy"),
        "short_executable_on_kraken": bool(ap.get("leverage_sell")),
        "ordermin_paxg": ap.get("ordermin"),
        "book_spread_bps": (ask - bid) / mid * 1e4,
        "mid": mid,
        "depth_top5_bid_usd": sum(float(p) * float(q) for p, q, _ in dep["bids"][:5]),
        "depth_top5_ask_usd": sum(float(p) * float(q) for p, q, _ in dep["asks"][:5]),
        "depth_top20_bid_usd": sum(float(p) * float(q) for p, q, _ in dep["bids"]),
        "depth_top20_ask_usd": sum(float(p) * float(q) for p, q, _ in dep["asks"]),
        "vol_24h_paxg": float(tic["v"][1]),
        "vol_24h_usd": float(tic["v"][1]) * mid,
    }
    _dump(os.path.join(CACHE, "cost_inputs.json"), out)
    return out


def fetch_kraken_ohlc(interval):
    """Kraken OHLC. HARD CAP: 720 bars per interval, whatever `since` you pass."""
    res = _kraken("OHLC", {"pair": KRAKEN_PAIR, "interval": interval})
    rows = [r for k, v in res.items() if k != "last" for r in v]
    rows.sort(key=lambda r: r[0])
    out = [{"ts": int(r[0]), "open": float(r[1]), "high": float(r[2]),
            "low": float(r[3]), "close": float(r[4]), "vwap": float(r[5]),
            "volume": float(r[6]), "count": int(r[7])} for r in rows]
    _dump(os.path.join(CACHE, "paxg_ohlc_%d.json" % interval), out)
    print("  kraken OHLC interval=%-5d bars=%d  %s -> %s  (720-bar cap %s)"
          % (interval, len(out), _iso(out[0]["ts"]), _iso(out[-1]["ts"]),
             "BINDS" if len(out) >= 720 else "does not bind"))
    return out


def fetch_kraken_trades(start_ts, max_calls=900):
    """Paginate Kraken's public Trades feed back to `start_ts`.

    This is the ONLY public Kraken route to intraday history older than 30 days:
    OHLC caps at 720 bars (= 30 days at hourly), but Trades accepts an arbitrary
    nanosecond `since` cursor and walks forward indefinitely. We collapse the raw
    prints into 1-minute last-trade bars, which is all the alignment needs.
    """
    path = os.path.join(CACHE, "paxg_trades_1min.json")
    bars = {}
    since = int(start_ts) * 10**9
    if os.path.exists(path):
        prev = json.load(open(path))
        bars = {int(k): v for k, v in prev["bars"].items()}
        since = int(prev["cursor"])
        print("  resuming trade fetch from %s (%d 1-min bars cached)"
              % (_iso(since // 10**9), len(bars)))

    calls = 0
    t0 = time.time()
    while calls < max_calls:
        res = _kraken("Trades", {"pair": KRAKEN_PAIR, "since": str(since),
                                 "count": 1000})
        trades = [t for k, v in res.items() if k != "last" for t in v]
        if not trades:
            break
        for tr in trades:
            price, vol, ts = float(tr[0]), float(tr[1]), float(tr[2])
            minute = int(ts // 60) * 60
            b = bars.get(minute)
            if b is None:
                bars[minute] = [price, price, vol, 1]   # last, first, vol, n
            else:
                b[0] = price
                b[2] += vol
                b[3] += 1
        new_since = int(res["last"])
        calls += 1
        if new_since <= since:
            break
        since = new_since
        if calls % 25 == 0:
            print("    ...%4d calls, %6d 1-min bars, at %s (%.0fs)"
                  % (calls, len(bars), _iso(since // 10**9), time.time() - t0))
            _dump(path, {"cursor": since, "bars": {str(k): v for k, v in bars.items()}})
        if since // 10**9 > time.time() - 120:
            break
        time.sleep(1.1)   # public rate-limit courtesy

    _dump(path, {"cursor": since, "bars": {str(k): v for k, v in bars.items()}})
    ks = sorted(bars)
    print("  kraken Trades -> %d 1-min bars, %s -> %s (%d calls, %.0fs)"
          % (len(bars), _iso(ks[0]), _iso(ks[-1]), calls, time.time() - t0))
    return bars


def fetch_kraken_trades_targeted(dates, max_calls=1200):
    """One Trades page per US trading date, anchored at the session open.

    The sequential walk in fetch_kraken_trades() reconstructs PAXG 24/7 but costs
    ~1 call per 2 days of tape. We only need PAXG around the US session (the only
    window where GLD also trades), and a single 1000-print page spans 1-3 days of
    PAXG tape -- comfortably covering one 6.5h session plus the close. So we anchor
    one page per trading date at (close - 7h) and merge. Same information for the
    alignment, roughly 6x fewer calls.
    """
    path = os.path.join(CACHE, "paxg_trades_1min.json")
    bars = {}
    cursor = 0
    if os.path.exists(path):
        prev = json.load(open(path))
        bars = {int(k): v for k, v in prev["bars"].items()}
        cursor = int(prev.get("cursor", 0))
        print("  merging into %d existing 1-min bars" % len(bars))

    calls = 0
    t0 = time.time()
    todo = []
    for dstr in dates:
        ts, _ = gld_close_utc(dstr)
        # skip dates already fully covered: need a print within 30min each side
        have_before = any(k for k in (ts - 1800, ts) if k in bars)
        todo.append((dstr, ts))

    for idx, (dstr, ts) in enumerate(todo):
        if calls >= max_calls:
            print("  WARNING: hit max_calls=%d, stopping at %s" % (max_calls, dstr))
            break
        # already covered? require a print in [ts-45min, ts+45min]
        window = [k for k in bars if ts - 2700 <= k <= ts + 2700]
        if window:
            continue
        since = (ts - 7 * 3600) * 10**9
        res = _kraken("Trades", {"pair": KRAKEN_PAIR, "since": str(since),
                                 "count": 1000})
        trades = [t for k, v in res.items() if k != "last" for t in v]
        calls += 1
        for tr in trades:
            price, vol, tstamp = float(tr[0]), float(tr[1]), float(tr[2])
            minute = int(tstamp // 60) * 60
            b = bars.get(minute)
            if b is None:
                bars[minute] = [price, price, vol, 1]
            else:
                b[0] = price
                b[2] += vol
                b[3] += 1
        if calls % 10 == 0:
            print("    ...%4d calls, %6d 1-min bars, at %s (%.0fs)"
                  % (calls, len(bars), dstr, time.time() - t0), flush=True)
            _dump(path, {"cursor": cursor,
                         "bars": {str(k): v for k, v in bars.items()}})
        time.sleep(TRADES_CALL_INTERVAL_S)

    _dump(path, {"cursor": cursor, "bars": {str(k): v for k, v in bars.items()}})
    ks = sorted(bars)
    print("  kraken Trades (targeted) -> %d 1-min bars, %s -> %s (%d calls, %.0fs)"
          % (len(bars), _iso(ks[0]), _iso(ks[-1]), calls, time.time() - t0))
    return bars


def fetch_equities_hourly():
    """GLD hourly bars. yfinance caps 1h history at 730 days -- state it."""
    import yfinance as yf
    d = yf.download("GLD", period="730d", interval="1h",
                    auto_adjust=False, progress=False)
    cl = d["Close"]
    if hasattr(cl, "columns"):
        cl = cl.iloc[:, 0]
    rows = [{"ts": int(ix.timestamp()), "close": float(v)}
            for ix, v in cl.items() if np.isfinite(v)]
    _dump(os.path.join(CACHE, "gld_hourly.json"), rows)
    print("  yfinance GLD hourly bars=%d  %s -> %s  (730d 1h cap BINDS)"
          % (len(rows), _iso(rows[0]["ts"]), _iso(rows[-1]["ts"])))
    return rows


def fetch_equities():
    import yfinance as yf
    out = {}
    for tkr in ("GLD", "IAU"):
        d = yf.download(tkr, period="5y", interval="1d",
                        auto_adjust=False, progress=False)
        # GLD and IAU pay no distributions, so Close == Adj Close; use Close.
        cl = d["Close"]
        if hasattr(cl, "columns"):
            cl = cl.iloc[:, 0]
        rows = [{"date": str(ix.date()), "close": float(v)}
                for ix, v in cl.items() if np.isfinite(v)]
        _dump(os.path.join(CACHE, "%s_daily.json" % tkr.lower()), rows)
        out[tkr] = rows
        print("  yfinance %s daily bars=%d  %s -> %s"
              % (tkr, len(rows), rows[0]["date"], rows[-1]["date"]))
    return out


def stage_fetch():
    os.makedirs(CACHE, exist_ok=True)
    print("STAGE 1 -- FETCH")
    ci = fetch_cost_inputs()
    print("  measured PAXG book: spread %.3f bps, 24h vol $%.2fM, top-20 depth $%.0f/$%.0f"
          % (ci["book_spread_bps"], ci["vol_24h_usd"] / 1e6,
             ci["depth_top20_bid_usd"], ci["depth_top20_ask_usd"]))
    print("  measured Kraken fees: taker %.0f bps, maker %.0f bps (base tier)"
          % (ci["base_tier_taker_bps"], ci["base_tier_maker_bps"]))
    print("  PAXG short executable on Kraken margin: %s (leverage_sell=%s)"
          % (ci["short_executable_on_kraken"], ci["leverage_sell"]))
    daily = fetch_kraken_ohlc(1440)
    fetch_kraken_ohlc(60)
    eq = fetch_equities()
    fetch_equities_hourly()
    first_date = datetime.fromtimestamp(daily[0]["ts"], timezone.utc).date()
    dates = [r["date"] for r in eq["GLD"]
             if datetime.strptime(r["date"], "%Y-%m-%d").date() >= first_date]
    fetch_kraken_trades_targeted(dates)
    print("STAGE 1 complete.\n")


# ===========================================================================
# helpers
# ===========================================================================
def _dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def _iso(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d %H:%M")


def _load(name):
    return json.load(open(os.path.join(CACHE, name)))


def ols(x, y):
    """Slope, intercept, R^2, Newey-West-free t on slope."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    n = x.size
    sx, sy = x.mean(), y.mean()
    sxx = ((x - sx) ** 2).sum()
    b = ((x - sx) * (y - sy)).sum() / sxx
    a = sy - b * sx
    resid = y - (a + b * x)
    ss_res = (resid ** 2).sum()
    ss_tot = ((y - sy) ** 2).sum()
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    se = math.sqrt(ss_res / (n - 2) / sxx) if n > 2 and sxx > 0 else float("nan")
    return b, a, r2, (b / se if se and np.isfinite(se) and se > 0 else float("nan"))


def ou_half_life(series, dt_units=1.0):
    """AR(1) / discrete OU half-life. Returns (half_life, phi, t_on_phi_minus_1)."""
    s = np.asarray(series, float)
    s = s[np.isfinite(s)]
    y = s[1:]
    x = s[:-1]
    b, a, r2, _ = ols(x, y)
    # regression of delta on level: dS = k*(mu - S) -> phi = 1 + slope
    d = y - x
    bd, ad, r2d, td = ols(x, d)
    if bd >= 0:
        return float("inf"), b, td
    hl = -math.log(2.0) / math.log(1.0 + bd) * dt_units
    return hl, b, td


def pct(a, q):
    return float(np.percentile(np.asarray(a, float), q))


# ===========================================================================
# STAGE 2 -- ANALYSIS
# ===========================================================================
US_CLOSE_HOUR_ET = 16

def gld_close_utc(date_str):
    """UTC timestamp of the 16:00 ET official close for a US trading date.

    US eastern DST: second Sunday of March -> first Sunday of November.
    16:00 EDT = 20:00 UTC ; 16:00 EST = 21:00 UTC.
    """
    y, m, d = (int(v) for v in date_str.split("-"))
    dt = datetime(y, m, d, tzinfo=timezone.utc)

    def nth_sunday(year, month, n):
        first = datetime(year, month, 1, tzinfo=timezone.utc)
        offset = (6 - first.weekday()) % 7          # weekday: Mon=0 .. Sun=6
        return first.day + offset + 7 * (n - 1)

    dst_start = datetime(y, 3, nth_sunday(y, 3, 2), tzinfo=timezone.utc)
    dst_end = datetime(y, 11, nth_sunday(y, 11, 1), tzinfo=timezone.utc)
    is_edt = dst_start <= dt < dst_end
    hour_utc = 20 if is_edt else 21
    return int(datetime(y, m, d, hour_utc, 0, tzinfo=timezone.utc).timestamp()), is_edt


def paxg_next(minute_bars, target_ts, max_wait_s=3600):
    """FIRST PAXG print at or after target_ts -- the price you could actually get.

    This is the execution counterpart to paxg_at(). PAXG on Kraken is thin
    (~350 prints/day), so the last print BEFORE GLD's close can be many minutes
    stale. Signalling off that stale price and then assuming you transacted at it
    is a look-ahead: part of the apparent divergence is simply gold having moved
    since PAXG last traded, and it 'converges' the moment the next print lands --
    a convergence you could never have captured. Signal on paxg_at, fill on
    paxg_next.
    """
    keys = minute_bars["keys"]
    lo, hi = 0, len(keys) - 1
    if hi < 0 or keys[hi] < target_ts:
        return None, None
    while lo < hi:
        mid = (lo + hi) // 2
        if keys[mid] >= target_ts:
            hi = mid
        else:
            lo = mid + 1
    k = keys[lo]
    wait = k - target_ts
    if wait > max_wait_s:
        return None, wait
    return minute_bars["bars"][k][0], wait


def paxg_at(minute_bars, target_ts, max_staleness_s=3600):
    """Last PAXG trade price at or before target_ts, plus how stale it was."""
    lo, hi = 0, len(minute_bars["keys"]) - 1
    keys = minute_bars["keys"]
    if hi < 0 or keys[0] > target_ts:
        return None, None
    # binary search for rightmost key <= target
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if keys[mid] <= target_ts:
            lo = mid
        else:
            hi = mid - 1
    k = keys[lo]
    stale = target_ts - k
    if stale > max_staleness_s:
        return None, stale
    return minute_bars["bars"][k][0], stale


def stage_analyze():
    print("STAGE 2 -- ANALYSIS")
    R = {"generated_utc": datetime.now(timezone.utc).isoformat(),
         "house_prior_trials": HOUSE_PRIOR_TRIALS}
    trials = 0   # every variant we test, counted honestly

    ci = _load("cost_inputs.json")
    R["cost_inputs_measured"] = ci

    d1440 = _load("paxg_ohlc_1440.json")
    d60 = _load("paxg_ohlc_60.json")
    gld = _load("gld_daily.json")
    iau = _load("iau_daily.json")
    tr = _load("paxg_trades_1min.json")
    bars = {int(k): v for k, v in tr["bars"].items()}
    mb = {"keys": sorted(bars), "bars": bars}

    R["data_span"] = {
        "kraken_daily_bars": len(d1440),
        "kraken_daily_first": _iso(d1440[0]["ts"]),
        "kraken_daily_last": _iso(d1440[-1]["ts"]),
        "kraken_daily_cap_binds": len(d1440) >= 720,
        "kraken_hourly_bars": len(d60),
        "kraken_hourly_first": _iso(d60[0]["ts"]),
        "kraken_hourly_last": _iso(d60[-1]["ts"]),
        "kraken_hourly_cap_binds": len(d60) >= 720,
        "kraken_hourly_days_covered": round((d60[-1]["ts"] - d60[0]["ts"]) / 86400, 1),
        "paxg_1min_bars_from_trades": len(bars),
        "paxg_trades_first": _iso(mb["keys"][0]),
        "paxg_trades_last": _iso(mb["keys"][-1]),
        "gld_daily_bars": len(gld),
        "iau_daily_bars": len(iau),
    }
    print("  PAXG 1-min bars reconstructed from Trades: %d  (%s -> %s)"
          % (len(bars), _iso(mb["keys"][0]), _iso(mb["keys"][-1])))

    # -- INTEGRITY CHECK -------------------------------------------------
    # Our 1-min bars are rebuilt from raw prints; Kraken's OHLC is authoritative.
    # A Kraken daily bar is labelled by its START (00:00 UTC) and closes 24h later,
    # so bar[D].close must equal our last print at (D+1) 00:00 UTC. If this fails,
    # either the reconstruction or the bar-labelling assumption is wrong, and every
    # alignment downstream is worthless.
    errs = []
    for b in d1440[:-1]:
        px, stale = paxg_at(mb, b["ts"] + 86400, max_staleness_s=7200)
        if px is not None and b["close"] > 0:
            errs.append(abs(px / b["close"] - 1.0) * 1e4)
    R["integrity_trades_vs_ohlc"] = {
        "n_compared": len(errs),
        "median_abs_err_bps": float(np.median(errs)) if errs else None,
        "p99_abs_err_bps": pct(errs, 99) if errs else None,
        "max_abs_err_bps": float(np.max(errs)) if errs else None,
        "note": ("Reconstructed last-print at the daily bar boundary vs Kraken's own "
                 "OHLC close. Near-zero confirms both the reconstruction and the "
                 "bar-labelling (bars are stamped by START, close 24h later)."),
    }
    print("  INTEGRITY reconstruction vs Kraken OHLC close: median %.3f bps, max %.2f bps (n=%d)"
          % (np.median(errs), np.max(errs), len(errs)) if errs else "  INTEGRITY: no overlap")

    gld_by_date = {r["date"]: r["close"] for r in gld}
    iau_by_date = {r["date"]: r["close"] for r in iau}
    paxg_daily_close = {datetime.fromtimestamp(r["ts"], timezone.utc).strftime("%Y-%m-%d"):
                        r["close"] for r in d1440}

    # -------------------------------------------------------------------
    # TRAP 1 -- build BOTH series: the naive (wrong) one and the aligned one
    # -------------------------------------------------------------------
    trades_first_date = datetime.fromtimestamp(mb["keys"][0], timezone.utc).date()
    rows = []
    staleness = []
    waits = []
    drops = {"before_trade_cache": 0, "no_print_before": 0, "no_print_after": 0,
             "no_kraken_daily_bar": 0, "kept": 0}
    for r in gld:
        dstr = r["date"]
        if datetime.strptime(dstr, "%Y-%m-%d").date() < trades_first_date:
            drops["before_trade_cache"] += 1
            continue
        ts, is_edt = gld_close_utc(dstr)
        px_aligned, stale = paxg_at(mb, ts)
        px_exec, wait = paxg_next(mb, ts)
        if px_aligned is None:
            drops["no_print_before"] += 1
            continue
        if px_exec is None:
            drops["no_print_after"] += 1
            continue
        # naive comparator: Kraken's own 00:00 UTC daily close for the SAME date,
        # i.e. the bar that closes 3-4h AFTER GLD printed.
        px_naive = paxg_daily_close.get(dstr)
        if px_naive is None:
            drops["no_kraken_daily_bar"] += 1
            continue
        drops["kept"] += 1
        staleness.append(stale)
        waits.append(wait)
        rows.append({
            "date": dstr, "ts": ts, "is_edt": is_edt,
            "gld": r["close"], "iau": iau_by_date.get(dstr),
            "paxg_aligned": px_aligned, "paxg_exec": px_exec,
            "paxg_naive_daily_close": px_naive,
            "staleness_s": stale, "wait_s": wait,
        })

    R["alignment"] = {
        "n_days": len(rows),
        "first": rows[0]["date"], "last": rows[-1]["date"],
        "paxg_print_staleness_s_median": float(np.median(staleness)),
        "paxg_print_staleness_s_p90": pct(staleness, 90),
        "paxg_print_staleness_s_max": float(np.max(staleness)),
        "paxg_fill_wait_s_median": float(np.median(waits)),
        "paxg_fill_wait_s_p90": pct(waits, 90),
        "paxg_fill_wait_s_max": float(np.max(waits)),
        "day_accounting": drops,
        "gld_days_in_kraken_era": drops["kept"] + drops["no_print_before"]
                                  + drops["no_print_after"] + drops["no_kraken_daily_bar"],
        "note": ("staleness = age of the last PAXG print at GLD's close (the signal "
                 "price). wait = delay to the next PAXG print after it (the fill "
                 "price). PAXG prints ~350x/day, so neither is instantaneous. Days "
                 "are dropped when no PAXG print exists within 1h either side of "
                 "GLD's close in the targeted trade cache -- an artefact of fetching "
                 "one Trades page per session rather than the whole tape."),
    }
    print("  day accounting: kept %d, dropped %d (no print before %d / after %d), "
          "pre-cache %d" % (drops["kept"],
                            drops["no_print_before"] + drops["no_print_after"],
                            drops["no_print_before"], drops["no_print_after"],
                            drops["before_trade_cache"]))
    print("  aligned trading days: %d  (%s -> %s)"
          % (len(rows), rows[0]["date"], rows[-1]["date"]))
    print("  PAXG print staleness at GLD close: median %.0fs, p90 %.0fs, max %.0fs"
          % (np.median(staleness), pct(staleness, 90), np.max(staleness)))

    # log ratios
    t_years = np.array([(r["ts"] - rows[0]["ts"]) / (365.25 * 86400) for r in rows])
    lr_aligned = np.array([math.log(r["paxg_aligned"] / r["gld"]) for r in rows])
    lr_exec = np.array([math.log(r["paxg_exec"] / r["gld"]) for r in rows])
    lr_naive = np.array([math.log(r["paxg_naive_daily_close"] / r["gld"]) for r in rows])
    have_iau = [r for r in rows if r["iau"]]
    t_iau = np.array([(r["ts"] - rows[0]["ts"]) / (365.25 * 86400) for r in have_iau])
    lr_iau = np.array([math.log(r["paxg_aligned"] / r["iau"]) for r in have_iau])

    # -------------------------------------------------------------------
    # TRAP 2 -- fit and remove the sponsor-fee decay
    # -------------------------------------------------------------------
    b_g, a_g, r2_g, t_g = ols(t_years, lr_aligned)
    b_i, a_i, r2_i, t_i = ols(t_iau, lr_iau)
    b_n, a_n, r2_n, t_n = ols(t_years, lr_naive)
    R["trap2_detrend"] = {
        "gld_fitted_drift_bps_per_year": b_g * 1e4,
        "gld_drift_r2": r2_g, "gld_drift_t": t_g,
        "iau_fitted_drift_bps_per_year": b_i * 1e4,
        "iau_drift_r2": r2_i, "iau_drift_t": t_i,
        "gld_minus_iau_drift_bps_per_year": (b_g - b_i) * 1e4,
        "expected_gld_minus_iau_from_prospectus_bps": 15.0,  # 0.40% - 0.25%
        "gld_prospectus_expense_bps": 40.0,
        "iau_prospectus_expense_bps": 25.0,
        "implied_oz_per_gld_share_start": math.exp(-a_g),
        "implied_oz_per_gld_share_end": math.exp(-(a_g + b_g * t_years[-1])),
    }
    print("  TRAP 2  fitted PAXG/GLD drift  = %+.1f bps/yr (R2 %.3f, t %.1f)  [prospectus 40]"
          % (b_g * 1e4, r2_g, t_g))
    print("          fitted PAXG/IAU drift  = %+.1f bps/yr (R2 %.3f, t %.1f)  [prospectus 25]"
          % (b_i * 1e4, r2_i, t_i))
    print("          GLD - IAU              = %+.1f bps/yr  [expected +15 if this is fee decay]"
          % ((b_g - b_i) * 1e4))

    # de-trended spreads, in bps
    sp_aligned = (lr_aligned - (a_g + b_g * t_years)) * 1e4
    sp_exec = (lr_exec - (a_g + b_g * t_years)) * 1e4
    sp_naive = (lr_naive - (a_n + b_n * t_years)) * 1e4

    # How much of the "divergence" is just PAXG not having printed yet?
    gap = sp_aligned - sp_exec
    R["signal_vs_execution_gap"] = {
        "mean_abs_gap_bps": float(np.abs(gap).mean()),
        "p95_abs_gap_bps": pct(np.abs(gap), 95),
        "stdev_gap_bps": float(np.std(gap, ddof=1)),
        "corr_gap_with_signal": float(np.corrcoef(gap, sp_aligned)[0, 1]),
        "note": ("Difference between the spread computed on the last print before "
                 "GLD's close (signal) and on the first print after it (fill). A "
                 "positive correlation with the signal means the apparent divergence "
                 "partly evaporates on the very next print -- stale-quote illusion, "
                 "not capturable basis."),
    }
    print("  STALE-PRINT GAP signal vs fill: mean |gap| %.1f bps, corr with signal %+.3f"
          % (np.abs(gap).mean(), np.corrcoef(gap, sp_aligned)[0, 1]))

    # -------------------------------------------------------------------
    # TRAP 1 -- how much divergence does the mismatch MANUFACTURE?
    # -------------------------------------------------------------------
    R["trap1_mismatch"] = {
        "naive_spread_stdev_bps": float(np.std(sp_naive, ddof=1)),
        "aligned_spread_stdev_bps": float(np.std(sp_aligned, ddof=1)),
        "inflation_factor": float(np.std(sp_naive, ddof=1) / np.std(sp_aligned, ddof=1)),
        "naive_abs_p95_bps": pct(np.abs(sp_naive), 95),
        "aligned_abs_p95_bps": pct(np.abs(sp_aligned), 95),
        "note": ("The naive series differences GLD's 16:00 ET print against Kraken's "
                 "00:00 UTC print 3-4h later. The excess width is gold's overnight "
                 "move, not a basis, and it cannot be traded because the two prices "
                 "never coexisted."),
    }
    print("  TRAP 1  spread stdev: naive(mismatched) %.1f bps vs aligned %.1f bps -> %.1fx inflation"
          % (np.std(sp_naive, ddof=1), np.std(sp_aligned, ddof=1),
             np.std(sp_naive, ddof=1) / np.std(sp_aligned, ddof=1)))

    # -------------------------------------------------------------------
    # SPREAD DISTRIBUTION + HALF-LIFE
    # -------------------------------------------------------------------
    absa = np.abs(sp_aligned)
    hl_d, phi_d, t_phi = ou_half_life(sp_aligned, 1.0)
    R["spread_distribution_daily"] = {
        "n": int(sp_aligned.size),
        "stdev_bps": float(np.std(sp_aligned, ddof=1)),
        "mean_abs_bps": float(absa.mean()),
        "median_abs_bps": float(np.median(absa)),
        "abs_p75_bps": pct(absa, 75), "abs_p90_bps": pct(absa, 90),
        "abs_p95_bps": pct(absa, 95), "abs_p99_bps": pct(absa, 99),
        "abs_max_bps": float(absa.max()),
        "p01_bps": pct(sp_aligned, 1), "p99_bps": pct(sp_aligned, 99),
        "half_life_days": hl_d, "ar1_phi": phi_d, "t_on_mean_reversion": t_phi,
    }
    print("  spread |bps|: median %.1f  p75 %.1f  p90 %.1f  p95 %.1f  p99 %.1f  max %.1f  (sd %.1f)"
          % (np.median(absa), pct(absa, 75), pct(absa, 90), pct(absa, 95),
             pct(absa, 99), absa.max(), np.std(sp_aligned, ddof=1)))
    print("  mean-reversion half-life: %.2f days (AR1 phi %.3f, t %.1f)" % (hl_d, phi_d, t_phi))

    # -------------------------------------------------------------------
    # HOURLY-RESOLUTION SPREAD -- is convergence a 6-hour or a 30-day event?
    # GLD hourly closes (US session only) vs PAXG's last print in that same minute.
    # -------------------------------------------------------------------
    try:
        gld_h = _load("gld_hourly.json")
        hrows = []
        for r in gld_h:
            px, stale = paxg_at(mb, r["ts"], max_staleness_s=1800)
            if px is None:
                continue
            hrows.append((r["ts"], math.log(px / r["close"])))
        if len(hrows) > 200:
            th = np.array([(t - hrows[0][0]) / (365.25 * 86400) for t, _ in hrows])
            lh = np.array([v for _, v in hrows])
            bh, ah, r2h, _ = ols(th, lh)
            sp_h = (lh - (ah + bh * th)) * 1e4
            # consecutive-bar pairs only (skip overnight/weekend gaps > 2h)
            ts_h = np.array([t for t, _ in hrows])
            gaps = np.diff(ts_h)
            ok = gaps <= 2 * 3600
            x_h, y_h = sp_h[:-1][ok], sp_h[1:][ok]
            bd, _, _, td = ols(x_h, y_h - x_h)
            hl_h = -math.log(2.0) / math.log(1.0 + bd) if bd < 0 else float("inf")
            absh = np.abs(sp_h)
            R["spread_distribution_hourly"] = {
                "n_bars": len(hrows),
                "n_consecutive_pairs": int(ok.sum()),
                "first": _iso(ts_h[0]), "last": _iso(ts_h[-1]),
                "stdev_bps": float(np.std(sp_h, ddof=1)),
                "median_abs_bps": float(np.median(absh)),
                "abs_p95_bps": pct(absh, 95), "abs_p99_bps": pct(absh, 99),
                "abs_max_bps": float(absh.max()),
                "half_life_hours": hl_h,
                "half_life_us_session_days": hl_h / 6.5,
                "t_on_mean_reversion": td,
                "note": ("US-session bars only; the half-life is in TRADING hours, "
                         "so ~6.5 of these hours make one session."),
            }
            print("  HOURLY spread |bps|: median %.1f  p95 %.1f  max %.1f  (sd %.1f, n=%d)"
                  % (np.median(absh), pct(absh, 95), absh.max(),
                     np.std(sp_h, ddof=1), len(hrows)))
            print("  HOURLY half-life: %.2f trading hours (%.2f US sessions, t %.1f)"
                  % (hl_h, hl_h / 6.5, td))
    except Exception as e:
        R["spread_distribution_hourly"] = {"error": repr(e)}
        print("  HOURLY analysis unavailable: %r" % (e,))

    # -------------------------------------------------------------------
    # COST STACK (trap 3)
    # -------------------------------------------------------------------
    c = dict(DEFAULT_COSTS)
    c["kraken_taker_bps"] = ci["base_tier_taker_bps"]
    c["kraken_maker_bps"] = ci["base_tier_maker_bps"]
    c["paxg_half_spread_bps"] = ci["book_spread_bps"] / 2.0

    def round_trip(mode, hold_days, short_leg):
        """Full 4-execution round trip in bps of notional PER LEG-PAIR."""
        paxg_fee = c["kraken_taker_bps"] if mode == "taker" else c["kraken_maker_bps"]
        paxg = 2 * paxg_fee + (2 * c["paxg_half_spread_bps"] if mode == "taker" else 0.0)
        gld = 2 * (c["gld_half_spread_bps"] + c["gld_commission_bps"])
        if short_leg == "paxg":
            fin = c["kraken_margin_open_bps"] + \
                  c["kraken_margin_rollover_bps_4h"] * 6.0 * max(hold_days, 0.0)
        else:
            fin = c["gld_borrow_bps_per_year"] * max(hold_days, 0.0) / 365.25
        return paxg + gld + fin, {"paxg_legs_bps": paxg, "gld_legs_bps": gld,
                                  "financing_bps": fin}

    rt_taker_0d, br_t = round_trip("taker", 0.0, "gld")
    rt_maker_0d, br_m = round_trip("maker", 0.0, "gld")
    rt_taker_short_paxg_1d, _ = round_trip("taker", 1.0, "paxg")
    R["trap3_cost_stack"] = {
        "components_bps": c,
        "round_trip_taker_zero_hold_bps": rt_taker_0d,
        "round_trip_taker_breakdown": br_t,
        "round_trip_maker_zero_hold_bps": rt_maker_0d,
        "round_trip_maker_breakdown": br_m,
        "round_trip_taker_short_paxg_1day_bps": rt_taker_short_paxg_1d,
        "financing_per_day_short_paxg_bps": c["kraken_margin_rollover_bps_4h"] * 6.0,
        "financing_per_day_short_gld_bps": c["gld_borrow_bps_per_year"] / 365.25,
        "short_paxg_executable": ci["short_executable_on_kraken"],
        "short_paxg_leverage_sell": ci["leverage_sell"],
    }
    # --- Is either leg actually executable FROM THIS DESK? ---------------
    try:
        import venue_costs as vc
        eq_api = [n for n, v in vc.VENUES.items() if v.get("equities_api")]
        eq_class = [n for n, v in vc.VENUES.items()
                    if any(k in ("stock", "etf") for k in v["classes"])
                    and v["status"] != "retired"]
        crypto_live = [n for n, v in vc.VENUES.items()
                       if "crypto" in v["classes"] and v["status"] != "retired"]
        R["executability"] = {
            "venues_with_equities_api": eq_api,
            "venues_with_live_equity_class": eq_class,
            "venues_with_live_crypto_class": crypto_live,
            "paxg_leg_executable": True,
            "paxg_short_executable": ci["short_executable_on_kraken"],
            "gld_leg_executable": bool(eq_api),
            "both_legs_executable": bool(eq_api) and True,
            "note": ("The PAXG leg is real: Kraken serves AU and UK, has a full "
                     "authenticated API, and PAXG/USD carries leverage_sell so the "
                     "short exists. The GLD leg has NO execution path on this desk "
                     "at all -- no configured venue exposes an equities API, and the "
                     "only venue that ever modelled a 'stock' class (eToro) is "
                     "retired and never had a retail order API. A convergence trade "
                     "with one unexecutable leg is not a trade."),
        }
        print("  EXECUTABILITY: PAXG leg yes (short: %s) | GLD leg NO -- "
              "venues with an equities API: %s"
              % (ci["short_executable_on_kraken"], eq_api or "NONE"))
    except Exception as e:
        R["executability"] = {"error": repr(e)}

    print("  TRAP 3  round trip (taker, short-GLD leg, 0d hold) = %.1f bps" % rt_taker_0d)
    print("          round trip (maker, short-GLD leg, 0d hold) = %.1f bps" % rt_maker_0d)
    print("          short-PAXG financing = %.0f bps/DAY (Kraken margin rollover, modelled)"
          % (c["kraken_margin_rollover_bps_4h"] * 6.0))

    # -------------------------------------------------------------------
    # THE DECISIVE QUESTION
    # -------------------------------------------------------------------
    for label, wall in (("taker", rt_taker_0d), ("maker", rt_maker_0d)):
        frac = float((absa > wall).mean())
        R.setdefault("decisive", {})["frac_days_gt_%s_wall" % label] = frac
        R["decisive"]["days_per_year_gt_%s_wall" % label] = frac * 252
        print("  DECISIVE: |spread| exceeds the %s round trip (%.1f bps) on %.2f%% of days"
              " (%.1f days/yr)" % (label, wall, frac * 100, frac * 252))
    R["decisive"]["max_spread_bps"] = float(absa.max())
    R["decisive"]["taker_wall_bps"] = rt_taker_0d
    R["decisive"]["maker_wall_bps"] = rt_maker_0d

    # -------------------------------------------------------------------
    # THRESHOLD SWEEP -- causal (expanding-window) de-trend, no look-ahead
    # -------------------------------------------------------------------
    MIN_TRAIN = 120
    ts_arr = np.array([r["ts"] for r in rows], float)
    causal = np.full(sp_aligned.size, np.nan)
    causal_x = np.full(sp_aligned.size, np.nan)
    for i in range(MIN_TRAIN, lr_aligned.size):
        bb, aa, _, _ = ols(t_years[:i], lr_aligned[:i])
        causal[i] = (lr_aligned[i] - (aa + bb * t_years[i])) * 1e4
        causal_x[i] = (lr_exec[i] - (aa + bb * t_years[i])) * 1e4
    hl_c, phi_c, t_c = ou_half_life(causal[np.isfinite(causal)], 1.0)
    R["causal_detrend"] = {
        "min_train_obs": MIN_TRAIN,
        "n_tradeable_obs": int(np.isfinite(causal).sum()),
        "half_life_days_causal_series": hl_c,
        "ar1_phi_causal": phi_c,
        "note": ("De-trend refit on an expanding window using only data up to bar i, "
                 "so the sponsor-fee slope removed at bar i uses no future data."),
    }

    sweep = []
    thresholds = [5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100, 125, 150, 200]
    exits = [0.0, 0.25, 0.5]
    for X in thresholds:
        for ex in exits:
            # (a) executable: signal on the pre-close print, fill on the next print
            trials += 1
            res = backtest(causal, X, ex * X, ts_arr, c, round_trip, exec_bps=causal_x)
            # (b) look-ahead comparator: fill AT the stale signal price
            trials += 1
            la = backtest(causal, X, ex * X, ts_arr, c, round_trip, exec_bps=None)
            res["threshold_bps"] = X
            res["exit_frac"] = ex
            res["lookahead_net_bps_per_trade"] = la["net_bps_per_trade"]
            res["lookahead_gross_bps_per_trade"] = la["gross_bps_per_trade"]
            res["lookahead_n_trades"] = la["n_trades"]
            sweep.append(res)
    R["sweep"] = sweep
    R["n_variants_tested_sweep"] = trials

    tradable = [s for s in sweep if s["n_trades"] >= 5]
    print("\n  THRESHOLD SWEEP -- daily grid, causal de-trend, %d usable obs, %d variants"
          % (int(np.isfinite(causal).sum()), trials))
    print("  causal-series half-life: %.2f days (AR1 phi %.3f)" % (hl_c, phi_c))
    print("  %5s %5s %6s %9s %9s %9s %8s %8s %9s %11s" %
          ("thr", "exit", "trades", "gross_bps", "cost_bps", "net_bps",
           "hold_d", "shrtPAXG", "net/yr%", "lookahd_net"))
    for s in sweep:
        if s["n_trades"] == 0:
            continue
        print("  %5d %5.2f %6d %9.2f %9.2f %9.2f %8.2f %8.2f %9.2f %11.2f"
              % (s["threshold_bps"], s["exit_frac"], s["n_trades"],
                 s["gross_bps_per_trade"], s["avg_cost_bps"], s["net_bps_per_trade"],
                 s["avg_hold_days"], s["frac_short_paxg"], s["net_annual_pct"],
                 s["lookahead_net_bps_per_trade"]))

    best = None
    if tradable:
        best = max(tradable, key=lambda s: s["net_sharpe"])
    R["best_variant"] = best
    if best:
        print("  BEST by net Sharpe: thr %d exit %.2f -> net %.2f bps/trade, "
              "%d trades, ann.SR %.2f"
              % (best["threshold_bps"], best["exit_frac"],
                 best["net_bps_per_trade"], best["n_trades"], best["net_sharpe"]))

    # -------------------------------------------------------------------
    # HARNESS -- quant_validation, unmodified
    # -------------------------------------------------------------------
    n_total = HOUSE_PRIOR_TRIALS + trials
    R["n_trials_total"] = n_total
    print("\n  SEARCH COUNT: house %d + this lab %d = N = %d"
          % (HOUSE_PRIOR_TRIALS, trials, n_total))

    if best and len(best["net_returns"]) >= 5:
        nr = np.array(best["net_returns"])
        gr = np.array(best["gross_returns"])
        trial_sharpes = [s["net_sharpe_per_trade"] for s in sweep if s["n_trades"] >= 5]
        ev = qv.evaluate(
            "paxg_gld_convergence_thr%d_exit%.2f" % (best["threshold_bps"], best["exit_frac"]),
            nr, n_trials=n_total, trial_sharpes=trial_sharpes,
            periods_per_year=best["trades_per_year"] or 1.0,
            trades_per_period=1.0, gross_returns=gr,
        )
        R["harness"] = ev
        R["harness_verdict"] = qv.verdict_line(ev)
        print("  HARNESS: " + qv.verdict_line(ev))

        # decay / execution-delay kill-test on the best variant
        decay = {}
        for delay in (0, 1, 2, 3, 5):
            trials += 1
            d = backtest(causal, best["threshold_bps"],
                         best["exit_frac"] * best["threshold_bps"],
                         ts_arr, c, round_trip, entry_delay=delay, exec_bps=causal_x)
            decay[delay] = d["net_bps_per_trade"]
        R["execution_delay_kill_test_net_bps"] = decay
        R["decay_profile"] = qv.decay_profile(decay)
        print("  DELAY KILL-TEST net bps/trade by days of delay: %s"
              % {k: round(v, 2) for k, v in decay.items()})

        # financing sensitivity -- the Kraken margin rollover rate is MODELLED,
        # not measured, and it is the largest single unknown in the stack.
        fin_sens = {}
        for roll in (0.0, 0.5, 1.0, 2.0):
            trials += 1
            c2 = dict(c)
            c2["kraken_margin_rollover_bps_4h"] = roll

            def rt2(mode, hold_days, short_leg, _c=c2):
                paxg_fee = _c["kraken_taker_bps"] if mode == "taker" else _c["kraken_maker_bps"]
                paxg = 2 * paxg_fee + (2 * _c["paxg_half_spread_bps"] if mode == "taker" else 0.0)
                gldc = 2 * (_c["gld_half_spread_bps"] + _c["gld_commission_bps"])
                if short_leg == "paxg":
                    fin = _c["kraken_margin_open_bps"] + \
                        _c["kraken_margin_rollover_bps_4h"] * 6.0 * max(hold_days, 0.0)
                else:
                    fin = _c["gld_borrow_bps_per_year"] * max(hold_days, 0.0) / 365.25
                return paxg + gldc + fin, {}

            d = backtest(causal, best["threshold_bps"],
                         best["exit_frac"] * best["threshold_bps"],
                         ts_arr, c2, rt2, exec_bps=causal_x)
            fin_sens["%.1f_bps_per_4h" % roll] = d["net_bps_per_trade"]
        R["financing_sensitivity_net_bps"] = fin_sens
        print("  FINANCING SENSITIVITY (net bps/trade by margin rollover): %s"
              % {k: round(v, 2) for k, v in fin_sens.items()})

        # maker-only variant: does patient execution rescue it?
        trials += 1
        mk = backtest(causal, best["threshold_bps"],
                      best["exit_frac"] * best["threshold_bps"],
                      ts_arr, c, round_trip, exec_bps=causal_x, mode="maker")
        R["maker_variant"] = {"net_bps_per_trade": mk["net_bps_per_trade"],
                              "n_trades": mk["n_trades"],
                              "net_sharpe": mk["net_sharpe"]}
        print("  MAKER-ONLY variant: net %.2f bps/trade over %d trades (ann.SR %.2f)"
              % (mk["net_bps_per_trade"], mk["n_trades"], mk["net_sharpe"]))

        # purged CV on the best variant's trade returns
        if nr.size >= 20:
            fold_sharpes = []
            for tr_ix, te_ix in qv.purged_kfold_splits(nr.size, 5, 0.02, 1):
                if te_ix.size >= 3:
                    fold_sharpes.append(float(qv.sharpe_ratio(nr[te_ix])))
            R["purged_cv_fold_sharpes"] = fold_sharpes
            R["purged_cv_folds_positive"] = int(sum(1 for s in fold_sharpes if s > 0))
            print("  PURGED CV fold Sharpes: %s" % [round(s, 3) for s in fold_sharpes])
    else:
        R["harness"] = None
        R["harness_verdict"] = ("No variant produced >= 5 trades that clear the "
                                "cost wall -- there is nothing to deflate.")
        print("  HARNESS: " + R["harness_verdict"])

    R["n_trials_total"] = HOUSE_PRIOR_TRIALS + trials
    R["n_variants_this_lab"] = trials

    _dump(RECEIPT, _jsonable(R))
    print("\n  receipt -> %s" % RECEIPT)
    return R


def backtest(signal_bps, entry_thr, exit_thr, ts_arr, costs, round_trip,
             entry_delay=0, exec_bps=None, mode="taker"):
    """Enter when |signal| > entry_thr, exit when it converges to <= exit_thr.

    Signs: spread > 0 means PAXG RICH vs GLD -> short PAXG / long GLD.
           spread < 0 means PAXG CHEAP        -> long PAXG / short GLD.

    `signal_bps` is the information set at the decision instant (PAXG's last print
    at or before GLD's close). `exec_bps` is the spread at the price you could
    actually transact (PAXG's first print at or after it). PnL is booked on
    exec_bps; only the decision uses signal_bps. Passing exec_bps=None collapses
    the two and reproduces the optimistic look-ahead version, which is why the
    sweep reports both.

    `entry_delay` shifts the entry by N bars -- the execution-delay kill test.
    Holding periods are measured in CALENDAR days from ts_arr, because financing
    accrues on the calendar, not on the trading grid.
    """
    if exec_bps is None:
        exec_bps = signal_bps
    n = signal_bps.size
    pos = 0           # 0 flat, +1 long-spread(PAXG cheap), -1 short-spread(PAXG rich)
    entry_i = None
    entry_s = None
    trades = []
    i = 0
    while i < n:
        s = signal_bps[i]
        if not np.isfinite(s):
            i += 1
            continue
        if pos == 0:
            if abs(s) > entry_thr:
                j = i + entry_delay
                if j >= n or not np.isfinite(exec_bps[j]):
                    i += 1
                    continue
                pos = -1 if s > 0 else 1
                entry_i, entry_s = j, exec_bps[j]
                i = j + 1
                continue
        else:
            if abs(s) <= exit_thr or (pos == 1 and s > 0) or (pos == -1 and s < 0):
                if not np.isfinite(exec_bps[i]):
                    i += 1
                    continue
                hold = (ts_arr[i] - ts_arr[entry_i]) / 86400.0
                x = exec_bps[i]
                # Gross convergence captured, in bps of one leg's notional.
                # short-spread (pos=-1, entered rich): pnl = s_entry - s_exit
                # long-spread  (pos=+1, entered cheap): pnl = s_exit - s_entry
                gross = (entry_s - x) if pos == -1 else (x - entry_s)
                short_leg = "paxg" if pos == -1 else "gld"
                cost, _ = round_trip(mode, hold, short_leg)
                trades.append({"entry_i": entry_i, "hold_days": hold,
                               "gross_bps": gross, "cost_bps": cost,
                               "net_bps": gross - cost, "short_leg": short_leg})
                pos = 0
                entry_i = entry_s = None
        i += 1

    if not trades:
        return {"n_trades": 0, "gross_bps_per_trade": 0.0, "net_bps_per_trade": 0.0,
                "net_annual_pct": 0.0, "avg_hold_days": 0.0, "max_hold_days": 0.0,
                "net_sharpe": 0.0, "net_sharpe_per_trade": 0.0, "trades_per_year": 0.0,
                "net_returns": [], "gross_returns": [], "win_rate": 0.0,
                "frac_short_paxg": 0.0, "avg_cost_bps": 0.0}

    g = np.array([t["gross_bps"] for t in trades])
    nt = np.array([t["net_bps"] for t in trades])
    holds = np.array([t["hold_days"] for t in trades], float)
    # Annualize off the TRADEABLE window (post-burn-in), on the calendar.
    finite = np.isfinite(signal_bps)
    span = ts_arr[finite]
    years = max((span[-1] - span[0]) / (365.25 * 86400), 1e-9)
    tpy = len(trades) / years if years > 0 else 0.0
    sd = nt.std(ddof=1) if nt.size > 1 else 0.0
    spt = float(nt.mean() / sd) if sd > 0 else 0.0
    return {
        "n_trades": len(trades),
        "gross_bps_per_trade": float(g.mean()),
        "net_bps_per_trade": float(nt.mean()),
        "avg_cost_bps": float(np.mean([t["cost_bps"] for t in trades])),
        "avg_hold_days": float(holds.mean()),
        "max_hold_days": float(holds.max()),
        "trades_per_year": tpy,
        "net_annual_pct": float(nt.mean() * tpy / 100.0),
        "win_rate": float((nt > 0).mean()),
        "frac_short_paxg": float(np.mean([t["short_leg"] == "paxg" for t in trades])),
        "net_sharpe_per_trade": spt,
        "net_sharpe": spt * math.sqrt(tpy) if tpy > 0 else 0.0,
        "net_returns": (nt / 1e4).tolist(),
        "gross_returns": (g / 1e4).tolist(),
    }


def _jsonable(o):
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        v = float(o)
        return v if math.isfinite(v) else None
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all", choices=["fetch", "analyze", "all"])
    a = ap.parse_args()
    if a.stage in ("fetch", "all"):
        stage_fetch()
    if a.stage in ("analyze", "all"):
        stage_analyze()
