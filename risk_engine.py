#!/usr/bin/env python3
"""Firm-wide risk engine — the one report every real family office runs first.

WHAT THIS ANSWERS
-----------------
The dashboards show what every position is WORTH. This answers what the whole
firm could LOSE, held together:

  * portfolio volatility and 1-day VaR (95/99%), historical simulation
  * the hidden factor bets: beta to equities (SPY), bitcoin, oil, and AUD/USD
  * concentration: effective holdings, covariance risk dimensions and overlap
  * stress replays: today's EXACT weights pushed through COVID (Feb-Mar 2020),
    the 2022 inflation bear, and the worst 5-day window in the sample
  * USD price-risk proxies, with the unmodelled listing-FX and basis risks
    stated explicitly alongside every report.

STRESS REPLAYS — PROMISE KEPT, 2026-08-27
-----------------------------------------
Until today the docstring above promised COVID and 2022 replays that the code
did not run: the headline sample is 2 years, so neither window is even IN it,
and `stress_replays_pct` carried only the worst 5-day window and the 2025-10-11
crypto cascade. That is now implemented rather than deleted: a SEPARATE
long-history panel is fetched (cached ~7 days) and today's weights are replayed
through 2020-02-19 -> 2020-03-23 and 2022-01-03 -> 2022-10-14 over the names
that actually existed then. Since the 2026-09-10 correction, current firm equity
remains the denominator; missing historical names are explicitly unknown risk.
Every replay publishes `covered_pct` — the share of today's gross the window
could price. A replay covering 40% of the book is reported as covering 40% of
the book; it is never presented as the whole firm.

METHOD, HONESTLY
----------------
Historical simulation with TODAY'S signed exposures / canonical firm equity
held static over the common window within a ~2-year daily panel (at least 63
returns for each measured name). That assumes the future rhymes with that sample — the standard caveat
of every VaR number ever printed, stated here rather than hidden.

COVERAGE — REBUILT AGAIN 2026-08-27 (funder-directed, DEFECT FIX)
-----------------------------------------------------------------
This report called itself FIRM-WIDE while `gather_positions()` read exactly
FOUR ledgers: growth_book, value_book, kraken_floor, replica_floor. Eighteen
tournament sleeves, the CoinSpot and cross-asset floors, both carry books, the
24h punt and the OANDA book were not in it at all — they contributed ZERO to
the firm's only risk number, silently. Coverage now comes from
`book_coverage.py`, the single enumeration of every book with live capital
(sleeves are GLOBBED, so a new sleeve is covered the moment it writes a
ledger). Two further defects fixed in the same pass:

  * MARK, NOT COST. Floor positions were valued at `p["cost"]` — the entry
    notional — while a live `mark` sat in the same record. Positions are now
    valued at units*mark wherever a mark exists; entry cost is used only as a
    DECLARED fallback, tagged per position and totalled in the payload as
    `cost_basis_usd`, so the size of that assumption is published.
  * RE-MARK FROM THE PANEL. Books that store no per-name mark (the weight books
    and every tournament sleeve store only `entry_px`) are re-marked from the
    price history this report already downloads — no extra fetch, no guess. The
    number of positions re-marked, and the dollar change, are both published.

COVERAGE (rebuilt 2026-08-11, operator-authorised)
--------------------------------------------------
Until 2026-08-11 the alt legs on the Kraken and Replica floors had no yfinance
ticker, so ~30% of the gross book was UNMEASURED — carried as an unknown rather
than a risk number. Kraken's own public OHLC endpoint prices every one of them,
free and unauthenticated, so they are now measured from source. Names split
three ways and each is reported separately, because a short series and a long
series are not the same evidence:

  * full-sample names   — span the whole ~2y window; they set the headline VaR
  * short-history names — listed part-way through (PUMP, CC, GWEI); folding them
    into the headline would truncate EVERY name to the shortest series, so they
    get their own all-positions run over the common short window instead
  * uncovered names     — no series at all; still reported loudly, because an
    uncovered position is unmeasured risk, not zero risk

No threshold moved. The book is no safer than it was this morning; it is only
honestly measured now.

PAPER ONLY. Reads ledgers, writes a report. Touches nothing.

Usage:  python3 risk_engine.py
"""
try:  # 2026-10-27 WITA Brett: ISP hijacks port-53 DNS for Kraken -> scoped DoH for Kraken public hosts only (kill: state/entry_engine/KRAKEN_DOH_OFF)
    import os as _kd_os, sys as _kd_sys
    _kd_sys.path.append(_kd_os.path.normpath(_kd_os.path.join(_kd_os.path.dirname(_kd_os.path.abspath(__file__)), '.')))
    import kraken_dns as _kraken_dns; _kraken_dns.install()
except Exception as _kd_e:  # pragma: no cover
    print("kraken_dns unavailable: %r" % _kd_e, file=__import__("sys").stderr)
import json
import os
import time
import warnings

import numpy as np

import book_coverage
import risk_analytics
import kraken_futures_risk_prices
import binance_risk_prices

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
KRAKEN_DESK = "/Users/test/Applications/KrakenQuantDesk"
REPORT = os.path.join(BASE, "reports", "risk_report.json")
COVERAGE_ERRORS = os.path.join(BASE, "reports", "risk_coverage_errors.json")

# Long-history panel for the COVID / 2022 replays (2026-08-27). Separate from the
# 2y headline sample on purpose: folding 2020 into the headline would truncate
# every crypto name that did not exist then.
STRESS_CACHE = os.path.join(BASE, "reports", "stress_history_cache.csv")
STRESS_CACHE_META = os.path.join(BASE, "reports", "stress_history_cache.json")
STRESS_CACHE_MAX_AGE = 7 * 86400
STRESS_WINDOWS = {
    "COVID crash 2020-02-19..03-23": ("2020-02-19", "2020-03-23"),
    "2022 inflation bear 01-03..10-14": ("2022-01-03", "2022-10-14"),
}

# Full coverage payload from the last gather_positions() call — run() reads it to
# re-mark positions and to publish per-book coverage.
_COVERAGE = None

FACTORS = {"SPY": "US equities", "BTC-USD": "Bitcoin", "USO": "Oil",
           "AUDUSD=X": "AUD/USD"}

CRYPTO_MAP = {  # floor symbol -> yfinance ticker
    "XLM/USD": "XLM-USD", "ADA/USD": "ADA-USD", "BNB/USD": "BNB-USD",
    "AAVE/USD": "AAVE-USD", "XBT/USD": "BTC-USD", "ETH/USD": "ETH-USD",
    "SOL/USD": "SOL-USD", "XRP/USD": "XRP-USD", "LTC/USD": "LTC-USD",
    "DOGE/USD": "DOGE-USD", "DOT/USD": "DOT-USD", "TRX/USD": "TRX-USD",
}

# Anything without a yfinance ticker is priced from Kraken's own public OHLC
# (read-only, unauthenticated, no key — this desk holds no broker credentials).
KRK = "KRK:"                       # pseudo-ticker prefix for Kraken-sourced series
PRICE_CACHE = os.path.join(BASE, "reports", "kraken_price_cache.json")
CACHE_MAX_AGE_SEC = 12 * 3600      # refetch twice a day; ticks run hourly
FULL_SAMPLE_MIN = 0.95             # share of the window a name must span for headline
SHORT_SAMPLE_MIN = 120             # below this many days a name stays UNCOVERED
# A re-mark this far from the book's own stored USD value is a unit/currency/
# scale error, never a price move — see remark(). Set well above any believable
# move on a position carried at ENTRY COST (the 24h punt ran 20x levered and
# still only moved 1x) so a real gain is never suppressed.
SANITY_MULT = 25.0


def kraken_closes(base, cache):
    """Daily closes for one Kraken base symbol: {'YYYY-MM-DD': close} or None."""
    import urllib.request

    pair = base + "USD"
    hit = (cache.get("series") or {}).get(pair)
    fetched = (cache.get("fetched") or {}).get(pair, 0)
    if hit and (time.time() - fetched) < CACHE_MAX_AGE_SEC:
        return hit
    try:
        url = ("https://api.kraken.com/0/public/OHLC?pair=%s&interval=1440" % pair)
        with urllib.request.urlopen(url, timeout=20) as r:
            d = json.loads(r.read().decode())
        if d.get("error"):
            return hit
        key = [k for k in d.get("result", {}) if k != "last"]
        if not key:
            return hit
        rows = d["result"][key[0]]
        ser = {time.strftime("%Y-%m-%d", time.gmtime(int(x[0]))): float(x[4]) for x in rows}
        cache.setdefault("series", {})[pair] = ser
        cache.setdefault("fetched", {})[pair] = time.time()
        return ser
    except Exception:
        return hit                 # stale cache beats no measurement


def jf(path, fb):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return fb


def fetch_price_panel(positions):
    """Shared public-data panel for canonical positions; no trading paths.

Do not forward-fill here: original last-valid dates are needed to detect a
stalled feed. The shared analyzer excludes today's still-forming daily bars.
"""
    import pandas as pd
    import yfinance as yf
    tickers = {p.get("ticker") for p in positions if p.get("ticker")}
    yf_tickers = sorted({t for t in tickers if not t.startswith((KRK, binance_risk_prices.PREFIX))} | set(FACTORS))
    binance_symbols = sorted(t[len(binance_risk_prices.PREFIX):] for t in tickers
                             if t.startswith(binance_risk_prices.PREFIX))
    krk_bases = sorted(t[len(KRK):] for t in tickers if t.startswith(KRK) and t != KRK)
    try:
        raw = yf.download(yf_tickers, period="2y", interval="1d", auto_adjust=True,
                          progress=False, threads=True)["Close"]
        if isinstance(raw, pd.Series):
            raw = raw.to_frame(yf_tickers[0])
    except Exception as exc:
        print("!! risk price download unavailable: %s" % type(exc).__name__)
        raw = pd.DataFrame()
    raw.index = pd.to_datetime(raw.index, utc=True).tz_convert(None).normalize()
    cache = jf(PRICE_CACHE, {})
    for base in krk_bases:
        ser = (kraken_futures_risk_prices.closes(base, cache) if base.startswith("PF_")
               else kraken_closes(base, cache))
        if ser:
            s = pd.Series({pd.Timestamp(d): v for d, v in ser.items()}).sort_index()
            raw = raw.reindex(raw.index.union(s.index))
            raw[KRK + base] = s
    binance_series = binance_risk_prices.load(binance_symbols, cache)
    for symbol, ser in binance_series.items():
        s = pd.Series({pd.Timestamp(d): v for d, v in ser.items()}).sort_index()
        raw = raw.reindex(raw.index.union(s.index))
        raw[binance_risk_prices.PREFIX + symbol] = s
    os.makedirs(os.path.dirname(PRICE_CACHE), exist_ok=True)
    tmp = "%s.tmp.%d" % (PRICE_CACHE, os.getpid())
    with open(tmp, "w") as f:
        json.dump(cache, f)
    os.replace(tmp, PRICE_CACHE)
    return raw.sort_index(), {"yfinance": len(yf_tickers),
                             "binance_spot_usd_requested": len(binance_symbols),
                             "binance_spot_usd_loaded": len(binance_series),
                             "kraken_public_ohlc": sum(not b.startswith("PF_") for b in krk_bases),
                             "kraken_exact_futures_mark_requested": sum(b.startswith("PF_") for b in krk_bases),
                             "kraken_exact_futures_mark": sum(b.startswith("PF_") and KRK + b in raw
                                                               for b in krk_bases)}


def gather_positions():
    """EVERY open position across EVERY book, as (label, yf_ticker, signed_usd).

    Rebuilt 2026-08-27 (funder-directed). Coverage is no longer a hand-written
    list of four ledgers in this function -- it is `book_coverage.registry()`,
    which enumerates every book with live capital and globs the tournament
    sleeves so a new sleeve cannot be missed.

    Values here are each book's STORED basis (mark where the book records one,
    entry cost where it does not, tagged either way). `run()` then re-marks
    everything it can price from the panel it downloads anyway. A position with
    no price series at all is returned UNCOVERED -- reported loudly, never
    silently valued at zero.

    The full coverage payload is kept in `_COVERAGE` for run() to use; the
    (label, ticker, value) triple shape is unchanged so every consumer below
    still works.
    """
    global _COVERAGE
    g = book_coverage.gather(verbose=True)
    _COVERAGE = g
    out, uncovered = [], []
    for p in g["positions"]:
        if not p["ticker"] or p["ticker"] == KRK:
            uncovered.append((p["label"], abs(p["fallback_usd"])))
            continue
        out.append((p["label"], p["ticker"], p["fallback_usd"]))
    if g["errors"]:
        # LOUD + PERSISTED: an unreadable book is unmeasured risk, and a risk
        # report that quietly shrinks its own denominator is the defect this
        # rebuild exists to remove.
        try:
            os.makedirs(os.path.dirname(COVERAGE_ERRORS), exist_ok=True)
            with open(COVERAGE_ERRORS, "w") as f:
                json.dump({"utc": g["generated_utc"], "errors": g["errors"],
                           "source": "risk_engine.gather_positions"}, f, indent=2)
        except Exception as e:                      # noqa: BLE001
            print("!! could not persist coverage errors: %s: %s" % (type(e).__name__, e))
    elif os.path.exists(COVERAGE_ERRORS):
        try:
            os.remove(COVERAGE_ERRORS)
        except Exception:                           # noqa: BLE001
            pass
    return out, uncovered


def remark(raw):
    """Re-value every position at the LAST CLOSE in the downloaded panel.

    Fixes the entry-cost valuation: `units * close * mark_fx_usd` where a close
    exists, the stored basis otherwise. `mark_fx_usd` is the ticker's own
    local-price->USD factor (KRW for 005930.KS, pence for BARC.L, 1.0 for the
    USD-quoted majority), so a foreign listing is never re-marked at the wrong
    scale. Returns (triples, stats) -- stats quantify exactly how much of the
    book moved and how much is still carried at cost.

    TWO GUARDS, added 2026-08-28 after book_coverage hardcoded `mark_fx_usd=1.0`
    for the fundamental desk and inflated the firm's gross book to $92.35M
    against $1.59M of firm equity for at least twelve consecutive ticks:

      1. A position whose FX is missing/non-numeric is NOT re-marked at 1.0. The
         loader now passes None when it cannot establish the factor, and None
         means "refuse" -- the stored USD value is kept and the name is named.
      2. A re-mark that lands more than SANITY_MULT away from the book's own
         stored USD value is refused and reported. A book that publishes a mark
         cannot legitimately be 25x wrong about its own position; that ratio is
         a unit/currency/scale error every time, and the whole point of this
         module is that it is the layer which must never quietly report one.
    """
    out, n_marked, n_fallback, before, after = [], 0, 0, 0.0, 0.0
    still_cost = 0.0
    refused_fx, refused_sanity = [], []
    for p in (_COVERAGE or {}).get("positions", []):
        t, stored = p["ticker"], p["fallback_usd"]
        px = None
        if t and t in getattr(raw, "columns", []):
            try:
                s = raw[t].dropna()
                if len(s):
                    px = float(s.iloc[-1])
            except Exception:                       # noqa: BLE001
                px = None
        fx = p.get("mark_fx_usd")
        if px and px > 0 and p["units"] and not isinstance(fx, (int, float)):
            refused_fx.append(p["label"])
            px = None                               # fall through to the stored value
        cand = None
        if px and px > 0 and p["units"]:
            cand = p["sign"] * p["units"] * px * float(fx)
            if stored and abs(cand) > SANITY_MULT * abs(stored):
                refused_sanity.append((p["label"], abs(stored), abs(cand)))
                cand = None
        if cand is not None:
            val = cand
            n_marked += 1
        else:
            val = stored
            n_fallback += 1
            if p["fallback_basis"] != "mark":
                still_cost += abs(stored)
        before += abs(stored)
        after += abs(val)
        if not t or t == KRK:
            continue
        out.append((p["label"], t, val))
    if refused_fx:
        print("   re-mark REFUSED for %d position(s) with no usable FX factor "
              "(kept at the book's stored USD value): %s"
              % (len(refused_fx), ", ".join(refused_fx[:8])))
    for lab, sto, got in refused_sanity:
        print("!! re-mark REFUSED (sanity {:g}x): {} stored ${:,.0f} -> re-marked "
              "${:,.0f}. Kept the stored value; this is a unit/currency/scale "
              "error upstream, not a price move."
              .format(SANITY_MULT, lab, sto, got))
    return out, {"positions_remarked": n_marked, "positions_not_remarked": n_fallback,
                 "gross_stored_usd": round(before, 0), "gross_marked_usd": round(after, 0),
                 "mark_vs_cost_delta_usd": round(after - before, 0),
                 "mark_vs_cost_delta_pct": round((after - before) / before * 100, 2) if before else 0.0,
                 "still_at_cost_usd": round(still_cost, 0),
                 "remark_refused_no_fx": refused_fx,
                 "remark_refused_sanity": [{"label": l, "stored_usd": round(s, 2),
                                            "remarked_usd": round(g, 2)}
                                           for l, s, g in refused_sanity],
                 "sanity_mult": SANITY_MULT}


def stress_panel(tickers):
    """Long-history closes for the named windows. Cached ~7 days: this is the
    ONLY extra download the replays cost, and the replays run hourly."""
    import pandas as pd
    cache = jf(STRESS_CACHE_META, {})
    if os.path.exists(STRESS_CACHE) and (time.time() - cache.get("fetched", 0)) < STRESS_CACHE_MAX_AGE:
        try:
            df = pd.read_csv(STRESS_CACHE, index_col=0, parse_dates=True)
            if set(tickers) - set(df.columns):
                pass                                # stale ticker set -> refetch
            else:
                return df
        except Exception:                           # noqa: BLE001
            pass
    try:
        import yfinance as yf
        df = yf.download(sorted(tickers), period="max", interval="1d",
                         auto_adjust=True, progress=False, threads=True)["Close"]
        df.to_csv(STRESS_CACHE)
        with open(STRESS_CACHE_META, "w") as f:
            json.dump({"fetched": time.time(), "tickers": sorted(tickers)}, f)
        return df
    except Exception as e:                          # noqa: BLE001 - reported
        print("WARNING risk_engine: long-history panel unavailable (%s: %s); "
              "the COVID and 2022 replays will be reported UNAVAILABLE, not omitted."
              % (type(e).__name__, e))
        return None


def replay_window(df, vals, a, b, min_days=15, denominator=None):
    """Today's weights through a named historical window.

    The current report passes canonical firm equity as denominator and labels
    missing names as unknown exposure. Omitting denominator retains the legacy
    renormalised-subset view for callers that explicitly want that diagnostic.
    """
    import pandas as pd
    if df is None or not len(vals):
        return {"available": False, "reason": "long-history panel unavailable"}
    try:
        seg = df[(df.index >= pd.Timestamp(a)) & (df.index <= pd.Timestamp(b))]
    except Exception:                               # noqa: BLE001
        return {"available": False, "reason": "window not indexable"}
    gross = sum(abs(v) for v in vals.values()) or 1.0
    have = {}
    for t, v in vals.items():
        if t not in seg.columns:
            continue
        s = seg[t].dropna()
        if (len(s) >= min_days and float(s.iloc[0]) > 0
                and s.index[0] <= pd.Timestamp(a) + pd.Timedelta(days=7)
                and s.index[-1] >= pd.Timestamp(b) - pd.Timedelta(days=7)):
            have[t] = v
    if not have:
        return {"available": False, "reason": "no position in this book existed in the window",
                "covered_pct": 0.0}
    sub = sum(abs(v) for v in have.values())
    if denominator is not None and (not risk_analytics.finite(denominator) or denominator <= 0):
        return {"available": False, "reason": "invalid firm equity denominator"}
    base = denominator if denominator is not None else sub
    if base <= 0:
        return {"available": False, "reason": "zero covered exposure"}
    w = {t: v / base for t, v in have.items()}
    R = seg[sorted(have)].pct_change(fill_method=None).dropna()
    if len(R) < min_days:
        return {"available": False, "reason": "fewer than %d usable days" % min_days,
                "covered_pct": round(sub / gross * 100, 1)}
    port = sum(R[t] * x for t, x in w.items()).dropna()
    eq = (1 + port).cumprod()
    return {"available": True,
            "return_pct": round(float(eq.iloc[-1] - 1) * 100, 1),
            "maxdd_pct": round(float((eq / eq.cummax() - 1).min()) * 100, 1),
            "covered_pct": round(sub / gross * 100, 1),
            "names": len(have), "days": len(port),
            "denominator_usd": base,
            "partial": len(have) != len(vals),
            "note": ("firm-equity denominator retained; missing historical names are unknown risk"
                     if denominator is not None else
                     "legacy subset view: weights renormalised over the %d of %d names that existed then"
                     % (len(have), len(vals)))}


def run(verbose=True, coverage=None, raw=None, *, refresh_stress=True,
        check_files=True, unclaimed=None, now=None, write_report=True):
    """Canonical all-books, equity-normalized risk; partial estimates are labelled."""
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc)
    g = coverage if coverage is not None else book_coverage.gather(verbose=verbose)
    if unclaimed is None:
        unclaimed, _ = book_coverage.coverage_audit(g["books"], verbose=verbose)
    if raw is None:
        raw, price_sources = fetch_price_panel(g["positions"])
    else:
        price_sources = {"provided_panel": True}
    a = risk_analytics.analyze(g, raw, min_return_days=63, factors=FACTORS,
                               now=now, unclaimed=unclaimed, check_files=check_files)
    stats, equity, firm = a["statistics"], a["equity_usd"], a["return_series"]
    measured_netted_gross = sum(abs(v) for t, v in a["net_exposures_usd"].items()
                                if t not in a["missing_instruments"])
    gross_view = {}
    if stats.get("available") and measured_netted_gross > 0:
        gross_view = risk_analytics.risk_statistics(
            firm * equity / measured_netted_gross, measured_netted_gross)
        gross_view.update({"denominator_usd": measured_netted_gross,
                           "denominator": "absolute net-by-ticker covered exposure, excluding cash; NOT firm equity"})
    stresses = {}
    if stats.get("available"):
        stresses["worst 5-day in sample"] = stats.get("worst5_pct")
        seg = firm.loc["2025-10-06":"2025-10-18"]
        stresses["2025-10-11 crypto cascade wk"] = float(((1 + seg).prod() - 1) * 100) if len(seg) > 5 else None
    stress_vals = a["net_exposures_usd"]
    long_replays = {}
    if refresh_stress and equity and equity > 0:
        sdf = stress_panel([t for t in stress_vals if not t.startswith((KRK, binance_risk_prices.PREFIX, "UNPRICED:"))])
        long_replays = {label: replay_window(sdf, stress_vals, start, end,
                                             denominator=equity)
                        for label, (start, end) in STRESS_WINDOWS.items()}
    aw = sorted((abs(x) for x in a["weights"].values()), reverse=True)
    concentration = a["concentration"]
    payload = {
        "generated": now.isoformat(), "generated_utc": now.isoformat(),
        "schema_version": 2, "status": "OK" if a["complete"] else "INCOMPLETE",
        "risk_complete": a["complete"], "partial_risk_estimate": not a["complete"],
        "risk_errors": a["errors"],
        "denominator": "canonical current firm_equity_usd, including cash",
        "firm_equity_usd": equity, "risk_denominator_usd": equity,
        "sample_days": stats.get("sample_days", 0),
        "sample_start": a["sample_start"], "sample_end": a["sample_end"],
        "gross_book_usd": round(a["gross_usd"], 2),
        "net_exposure_usd": round(a["net_usd"], 2),
        "netted_gross_exposure_usd": round(a["netted_gross_usd"], 2),
        "covered_usd": round(a["covered_gross_usd"], 2),
        "uncovered_usd": round(a["unpriced_gross_usd"], 2),
        "uncovered_pct": round(100 - a["position_history_coverage_pct"], 2),
        "uncovered_names": a["missing_instruments"],
        "short_history_usd": 0.0, "short_history_pct": 0.0, "short_history_names": [],
        "history_policy": "all sufficiently fresh positions with >=63 returns share their common window; shorter histories are explicitly unmeasured",
        "price_sources": price_sources, "all_positions_view": {},
        "ann_vol_pct": stats.get("ann_vol_pct"),
        "daily_sigma_usd": stats.get("daily_sigma_usd"),
        "var95_1d_pct": stats.get("var95_1d_pct"), "var95_1d_usd": stats.get("var95_1d_usd"),
        "var99_1d_pct": stats.get("var99_1d_pct"), "var99_1d_usd": stats.get("var99_1d_usd"),
        "hist_maxdd_pct": stats.get("hist_maxdd_pct"),
        "factor_betas": a["factor_betas"],
        "common_factor_model": a["common_factor_model"],
        "top_position_pct": aw[0] * 100 if aw else 0.0,
        "effective_holdings": concentration["effective_holdings"],
        "effective_bets": None,
        "effective_bets_deprecated": "The old value was inverse HHI, not independent bets. Use effective_holdings; covariance risk dimensions are separately defined.",
        "concentration": concentration,
        "book_risk_contributions": a["book_risk_contributions"],
        "book_correlations": a["book_correlations"],
        "gross_normalized_view": gross_view,
        "stress_replays_pct": stresses, "long_history_replays": long_replays,
        "coverage": {
            "n_books": g.get("n_books"), "n_books_readable": g.get("n_books_ok"),
            "n_positions": g.get("n_positions"), "unreadable_books": g.get("errors") or [],
            "warnings": g.get("warnings") or [],
            "audit_clean": not unclaimed, "unclaimed_ledgers": a["inventory"]["unclaimed_ledgers"],
            "position_history_coverage_pct": a["position_history_coverage_pct"],
            "mapped_capital_pct": a["mapped_capital_pct"],
            "inventory": a["inventory"],
            "price_freshness": a["price_freshness"],
            "books": [{"book": b["label"], "key": b["key"], "kind": b["kind"],
                       "equity_usd": b.get("equity_usd"),
                       "gross_usd": b.get("gross_usd", 0),
                       "positions": len(b.get("positions") or []),
                       "stale": b.get("stale", False)} for b in g["books"]],
            "note": "book_coverage canonical registry plus unclaimed-ledger audit; errors never shrink into a complete risk number"},
        "valuation": a["valuation"],
        "aud_view": {},
        "weights": a["weights"], "method": a["method"],
        "limitations": ["Unpriced exposure is unknown, not zero; partial estimates cannot authorize new risk.",
                        binance_risk_prices.LIMITATION,
                        "Current listing FX conversions are held fixed in historical local-price returns.",
                        "Same-ticker offsetting spot/perpetual proxies omit basis, margin, liquidity and counterparty risk.",
                        "Correlations and factor regressions describe this sample; they do not establish an edge."],
    }
    if not stats.get("available"):
        payload["error"] = stats.get("reason")
    if write_report:
        os.makedirs(os.path.dirname(REPORT), exist_ok=True)
        tmp = "%s.tmp.%d" % (REPORT, os.getpid())
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2, allow_nan=False)
        os.replace(tmp, REPORT)
    return payload


def report(p):
    if p.get("error"):
        print("risk engine:", p["error"])
        return
    print("=" * 74)
    print("TAYLOR FAMILY OFFICE — FIRM-WIDE RISK REPORT   [%s]" % p["generated"])
    print("=" * 74)
    print("  status / denominator: %s · current firm equity $%s"
          % (p.get("status", "UNKNOWN"), "{:,.2f}".format(p.get("firm_equity_usd") or 0)))
    for error in p.get("risk_errors", []):
        print("    !! INCOMPLETE RISK: %s" % error)
    print("  gross book         : $%s  (measured $%s · UNMEASURED $%s = %.1f%%)"
          % ("{:,.0f}".format(p["gross_book_usd"]), "{:,.0f}".format(p["covered_usd"]),
             "{:,.0f}".format(p["uncovered_usd"]), p["uncovered_pct"]))
    if p["uncovered_names"]:
        print("    no price history : %s" % ", ".join(p["uncovered_names"]))
    if p.get("short_history_names"):
        print("    short history    : $%s = %.1f%% — %s"
              % ("{:,.0f}".format(p["short_history_usd"]), p["short_history_pct"],
                 ", ".join(p["short_history_names"])))
        print("                       (measured, but listed part-way through the window —")
        print("                        excluded from the headline, see ALL-POSITIONS below)")
    src = p.get("price_sources") or {}
    if src:
        print("    price sources    : yfinance %d · Kraken public OHLC %d"
              % (src.get("yfinance", 0), src.get("kraken_public_ohlc", 0)))
    print("  annualised vol     : %.1f%%   (~$%s one-sigma day)"
          % (p["ann_vol_pct"], "{:,.0f}".format(p["daily_sigma_usd"])))
    print("  1-day VaR 95%%      : %.2f%%  (~$%s)" % (p["var95_1d_pct"],
          "{:,.0f}".format(p["var95_1d_usd"])))
    print("  1-day VaR 99%%      : %.2f%%  (~$%s)" % (p["var99_1d_pct"],
          "{:,.0f}".format(p["var99_1d_usd"])))
    print("  hist max drawdown  : %.1f%%  (%s..%s, %d common return days)"
          % (p["hist_maxdd_pct"], p.get("sample_start"), p.get("sample_end"), p["sample_days"]))
    print("\n  FACTOR BETAS (the firm's hidden bets)")
    for k, v in p["factor_betas"].items():
        print("    %-12s %+0.2f" % (k, v))
    print("\n  CONCENTRATION      : top net position %.1f%% of equity · effective holdings %s"
          % (p["top_position_pct"], p.get("effective_holdings")))
    print("  RISK DIMENSIONS    : %s (covariance participation; not independent bets)"
          % ((p.get("concentration") or {}).get("effective_risk_dimensions")))
    print("  STRESS REPLAYS (today's weights)")
    for k, v in p["stress_replays_pct"].items():
        print("    %-28s %s%%" % (k, v))
    for k, v in (p.get("long_history_replays") or {}).items():
        if v.get("available"):
            print("    %-34s %+.1f%% (maxdd %.1f%%) — covers %.1f%% of gross, %d names"
                  % (k, v["return_pct"], v["maxdd_pct"], v["covered_pct"], v["names"]))
        else:
            print("    %-34s UNAVAILABLE — %s" % (k, v.get("reason")))
    c = p.get("coverage") or {}
    if c:
        print("\n  COVERAGE           : %s of %s books readable · %s positions"
              % (c.get("n_books_readable"), c.get("n_books"), c.get("n_positions")))
        for e in (c.get("unreadable_books") or []):
            print("    !! UNREADABLE BOOK : %s" % e)
    v = p.get("valuation") or {}
    if v:
        print("  VALUATION          : %d entry-cost fallbacks revalued / %d book bases retained · mark-vs-cost "
              "%+.2f%% ($%s) · $%s still at cost"
              % (v.get("positions_remarked", 0), v.get("positions_not_remarked", 0),
                 v.get("mark_vs_cost_delta_pct", 0),
                 "{:,.0f}".format(v.get("mark_vs_cost_delta_usd", 0)),
                 "{:,.0f}".format(v.get("still_at_cost_usd", 0))))
    if p.get("all_positions_view"):
        a = p["all_positions_view"]
        print("\n  ALL-POSITIONS CHECK (incl. %s over their common %d-day window)"
              % (", ".join(a["late_listings"]), a["window_days"]))
        print("    gross $%s · vol %.1f%% · 1-day VaR 99%% %.2f%% (~$%s)"
              % ("{:,.0f}".format(a["gross_usd"]), a["ann_vol_pct"],
                 a["var99_1d_pct"], "{:,.0f}".format(a["var99_1d_usd"])))
    if p.get("aud_view"):
        a = p["aud_view"]
        print("\n  AUD INVESTOR VIEW  : vol %.1f%% in USD -> %.1f%% in AUD terms"
              % (a["ann_vol_usd_pct"], a["ann_vol_aud_pct"]))
    print("\n  method: %s" % p["method"])
    print("\nReport -> %s" % REPORT)


if __name__ == "__main__":
    report(run())
