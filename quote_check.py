#!/usr/bin/env python3
"""Shared quote-consistency check for the V1 spread gates (Head Trader 2026-09-29 23:59 AEST). PAPER ONLY.

Why: at the 29 Sep US open Yahoo `.info` bid/ask was junk while last trades were current: TMO/SHEL/PG/JPM 0/0, ISRG 350/408,
MSFT 501/509, and AAPL 339.74/339.78 vs last 332.89 (2% off the tape) -- AAPL "passed" a 1.2bp spread on a quote that was
not the live book. A bid/ask is accepted only when
  * both sides are non-zero and bid <= ask (crossed bid > ask = not accepted), and
  * the last trade sits inside [bid, ask] or within 0.5% of mid.
Locked quotes (bid == ask, both non-zero; HT 2026-09-30 00:09 AEST) are accepted as a 0.0bp spread when the last trade is
within 0.5% of the locked price. Callers must still not treat a locked 0bp as proof of a tight book for size tiers.
Otherwise fail closed with a reason starting `quote_inconsistent` (callers re-read, and the fire routine's retries try again).
"""
LAST_TOL_PCT = 0.5
CODE = "quote_inconsistent"


def check(bid, ask, last):
    """-> (ok, reason|None, spread_bp|None)."""
    try:
        bid, ask = float(bid or 0), float(ask or 0)
        last = float(last) if last is not None else None
    except (TypeError, ValueError):
        return False, "%s: unreadable bid/ask/last" % CODE, None
    if not (bid > 0 and ask > 0):
        return False, "%s: missing side (bid %g, ask %g)" % (CODE, bid, ask), None
    if bid > ask:
        return False, "%s: crossed bid %g > ask %g" % (CODE, bid, ask), None
    if bid == ask:  # locked: accept as 0bp only if the tape agrees (HT 2026-09-30)
        if last is None or not last > 0:
            return False, "%s: locked %g with no last trade to validate" % (CODE, bid), 0.0
        off = abs(last / bid - 1) * 100
        if off > LAST_TOL_PCT:
            return False, "%s: locked %g but last %g is %.2f%% away (> %.1f%%)" % (CODE, bid, last, off, LAST_TOL_PCT), 0.0
        return True, None, 0.0
    mid = (bid + ask) / 2.0
    spread_bp = (ask - bid) / mid * 1e4
    if last is None or not last > 0:
        return False, "%s: no last trade to validate bid %g / ask %g" % (CODE, bid, ask), spread_bp
    if not (bid <= last <= ask) and abs(last / mid - 1) * 100 > LAST_TOL_PCT:
        return False, ("%s: last %g outside bid %g / ask %g and %.2f%% from mid (> %.1f%%)"
                       % (CODE, last, bid, ask, abs(last / mid - 1) * 100, LAST_TOL_PCT)), spread_bp
    return True, None, spread_bp


# ---------------------------------------------------------------------------------------------------------------------
# Primary US bid/ask source (HT 2026-09-30 00:09 AEST). The desk had no equity bid/ask feed of its own (ws_tape.sqlite is
# Kraken crypto only; the rail runner uses Yahoo chart prices). Nasdaq's public quote API (no key, no signup) returns the
# real-time Nasdaq Basic top-of-book with a trade timestamp; at the 29 Sep open it agreed with the tape where Yahoo .info
# was 0/0 or stale. Used only when fresh; otherwise callers fall back to Yahoo. BOTH paths go through check() and fail
# closed. A fresh-but-inconsistent Nasdaq quote is NOT replaced by Yahoo (no shopping for a passing quote).
# Disable with env QUOTE_PRIMARY=yahoo.
import datetime as _dt, json as _json, os as _os, re as _re, urllib.request as _ur
NASDAQ_URL = "https://api.nasdaq.com/api/quote/%s/info?assetclass=stocks"
NASDAQ_MAX_AGE_MIN = 5          # last-trade timestamp (minute resolution) must be this recent
NASDAQ_TIMEOUT_S = 4


def _money(s):
    try:
        return float(str(s).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def parse_nasdaq(payload, now=None):
    """Nasdaq quote JSON -> {"bid","ask","last","ts","source"} if real-time, market open and fresh; else None."""
    try:
        d = payload["data"]; p = d["primaryData"]
    except (TypeError, KeyError):
        return None
    if not p or p.get("isRealTime") is not True or str(d.get("marketStatus", "")).lower() != "open":
        return None
    try:
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        ts = _dt.datetime.strptime(str(p.get("lastTradeTimestamp", "")).replace(" ET", "").strip(), "%b %d, %Y %I:%M %p").replace(tzinfo=et)
    except (ValueError, TypeError):
        return None
    now = now or _dt.datetime.now(_dt.timezone.utc)
    age_min = (now - ts).total_seconds() / 60.0
    if age_min > NASDAQ_MAX_AGE_MIN + 1 or age_min < -2:   # +1: timestamp is floored to the minute
        return None
    return {"bid": _money(p.get("bidPrice")), "ask": _money(p.get("askPrice")), "last": _money(p.get("lastSalePrice")),
            "ts": ts.isoformat(), "source": "nasdaq"}


def nasdaq_quote(sym, now=None):
    """Fresh Nasdaq top-of-book for a plain US ticker, or None (not US-plain, disabled, unreachable, stale, closed)."""
    if _os.environ.get("QUOTE_PRIMARY", "").lower() == "yahoo" or not _re.fullmatch(r"[A-Z]{1,5}", str(sym or "")):
        return None
    try:
        req = _ur.Request(NASDAQ_URL % sym, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with _ur.urlopen(req, timeout=NASDAQ_TIMEOUT_S) as r:
            return parse_nasdaq(_json.load(r), now)
    except Exception:
        return None
