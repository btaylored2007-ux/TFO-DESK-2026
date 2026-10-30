#!/usr/bin/env python3
"""cef_probe.py — LIVE forward paper probe of the CEF discount-to-NAV reversion
mechanism. Spec sealed in CEF_DISCOUNT_PROBE_PREREGISTRATION.md (hash 6d13387831117064).

PAPER ONLY. Read-only w.r.t. everything except its own sleeve ledger. Reuses the
VALIDATED lab (cef_discount_lab) so the deployed signal == the registered signal.

Sizing: dollar-neutral, 1.0x gross (0.5 long / 0.5 short) — the conservative
unlevered default. disc_z tercile long widest-discount / short richest, monthly.
All network is wrapped; on any data failure the probe degrades to mark-only and
NEVER raises into the desk tick.
"""
import os
import time

import cef_discount_lab as lab

TERCILE = 1.0 / 3.0
GROSS = 1.0                 # 0.5 long + 0.5 short
COST_BPS = 25.0
REFRESH_DAYS = 20
# Non-negotiable #7 (funder directive 2026-08-22): FILLS never price from the
# research cache — fresh fetch at decision, refuse names staler than this.
MAX_FILL_AGE_TDAYS = 2


def _tdays_since(ts):
    """Trading days (Mon-Fri, UTC dates) strictly after the bar's date."""
    import datetime as dt
    d = ts.date() if hasattr(ts, "date") else ts
    today = dt.datetime.now(dt.timezone.utc).date()
    n = 0
    while d < today:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def _fresh_fill_prices(tickers):
    """Fill prices fetched FRESH at decision time. Signals may read the cache;
    fills never do (funder directive 2026-08-22: "no stale prices to trade
    against"). A name whose latest fresh bar is older than MAX_FILL_AGE_TDAYS
    trading days is REFUSED loudly, skipped this cycle, retried next cycle."""
    px = {}
    if not tickers:
        return px
    try:
        import yfinance as yf
        raw = yf.download(list(tickers), period="5d", interval="1d",
                          auto_adjust=False, progress=False, threads=True)["Close"]
    except Exception as e:
        print("cef_probe: REFUSED all fills — fresh decision-time fetch failed"
              " (%s); retry next cycle" % str(e)[:60])
        return px
    for t in tickers:
        try:
            col = raw[t].dropna() if hasattr(raw, "columns") else raw.dropna()
            if not len(col):
                print("cef_probe: REFUSED %s — no fresh bars; skipped this"
                      " cycle" % t)
                continue
            age = _tdays_since(col.index[-1])
            if age > MAX_FILL_AGE_TDAYS:
                print("cef_probe: REFUSED %s — last bar %s is %d trading days"
                      " old (limit %d); skipped this cycle"
                      % (t, str(col.index[-1])[:10], age, MAX_FILL_AGE_TDAYS))
                continue
            v = float(col.iloc[-1])
            if v == v and v > 0:
                px[t] = v
        except Exception as e:
            print("cef_probe: REFUSED %s — %s; skipped this cycle"
                  % (t, str(e)[:40]))
    return px


def _refresh_if_stale():
    """Force fresh history at (monthly) rebalance so discounts are current."""
    try:
        files = [os.path.join(lab.CACHE, f) for f in os.listdir(lab.CACHE)
                 if f.endswith(".csv")]
        if not files:
            return
        newest = max(os.path.getmtime(f) for f in files)
        if (time.time() - newest) > REFRESH_DAYS * 86400:
            for f in files:
                try:
                    os.remove(f)
                except OSError:
                    pass
    except Exception:
        pass


def current_targets():
    """Returns ({sym: weight}, {sym: last_price}) for the monthly rebalance, or
    ({}, {}) if data is unavailable."""
    _refresh_if_stale()
    try:
        R, D, ok = lab.build_panels()
        Z = lab.zscore(D)
        z_last = Z.iloc[-1].dropna()
    except Exception:
        return {}, {}
    if len(z_last) < 6:
        return {}, {}
    sig = -z_last                       # higher = wider discount = LONG
    order = sig.sort_values()
    k = max(1, int(len(sig) * TERCILE))
    shorts, longs = order.index[:k], order.index[-k:]
    # Fix 2026-08-22 (funder directive, Non-negotiable #7): fills used to price
    # from lab.yf_hist's cache — closes up to 20 days old. The z-score SIGNAL
    # above still reads the cache; FILL prices now come only from a fresh
    # decision-time fetch with a per-name recency refusal.
    px = _fresh_fill_prices(list(longs) + list(shorts))
    longs = [t for t in longs if t in px]
    shorts = [t for t in shorts if t in px]
    if not longs or not shorts:
        return {}, {}
    w = {}
    for t in longs:
        w[t] = (GROSS / 2.0) / len(longs)
    for t in shorts:
        w[t] = -(GROSS / 2.0) / len(shorts)
    return w, px


def spot_prices(tickers):
    """Light last-price fetch for marking held names between rebalances."""
    out = {}
    if not tickers:
        return out
    try:
        import yfinance as yf
        raw = yf.download(list(tickers), period="5d", interval="1d",
                          auto_adjust=False, progress=False, threads=True)["Close"]
        last = raw.ffill().iloc[-1]
        for t in tickers:
            try:
                v = float(last[t]) if hasattr(last, "__getitem__") else float(last)
                if v == v:
                    out[t] = v
            except Exception:
                pass
    except Exception:
        pass
    return out
