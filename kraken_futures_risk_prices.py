"""Exact-contract public mark histories for two verified risk-data gaps.

PAPER analytics only. No orders, credentials, ledgers, proxies or risk constants.
The caller persists this cache. All dates are UTC; listing/current partial days
are excluded. The shared risk analyzer still owns freshness/history admission.
Source: Kraken public instruments and charts/v1/mark/{symbol}/1d endpoints.
"""
import hashlib
import json
import math
import time
import urllib.request
from datetime import datetime, timezone

INSTRUMENTS_URL = "https://futures.kraken.com/derivatives/api/v3/instruments"
CHARTS_URL = "https://futures.kraken.com/api/charts/v1/mark/"
# Intentionally bounded. Other mappings are NOT implicitly converted to perps.
VERIFIED_CONTRACTS = {"PF_GLDXUSD": "GLDx", "PF_SPCXXUSD": "SPCXx"}
CACHE_KEY = "exact_futures_mark_v1"
CACHE_SECONDS = 12 * 3600
DAY_MS = 86_400_000


def public_json(url):
    import kraken_dns; kraken_dns.install()   # futures.kraken.com is ISP-sinkholed on plain DNS
    with urllib.request.urlopen(url, timeout=20) as response:
        raw = response.read()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _identity(row, symbol):
    if (row.get("symbol") != symbol or row.get("base") != VERIFIED_CONTRACTS[symbol]
            or row.get("quote") != "USD" or row.get("type") != "flexible_futures"
            or row.get("contractSize") != 1):
        raise ValueError("contract identity/currency/size mismatch")
    opened = datetime.fromisoformat(row["openingDate"].replace("Z", "+00:00"))
    if opened.utcoffset() is None:
        raise ValueError("openingDate lacks timezone")
    return opened.timestamp() * 1000


def _series_digest(series):
    return hashlib.sha256(json.dumps(series, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _closes(payload, opened_ms, now):
    if payload.get("more_candles") is not False:
        raise ValueError("truncated/unknown candle coverage")
    rows = payload.get("candles")
    if not isinstance(rows, list) or not rows:
        raise ValueError("missing candles")
    today_ms = int(now // 86400) * DAY_MS
    series, seen = {}, set()
    for row in rows:
        stamp = row.get("time")
        if (type(stamp) is not int or stamp % DAY_MS or stamp in seen
                or stamp < int(opened_ms // DAY_MS) * DAY_MS or stamp > today_ms):
            raise ValueError("invalid/duplicate/non-daily candle timestamp")
        seen.add(stamp)
        o, h, low, close = (float(row[k]) for k in ("open", "high", "low", "close"))
        if (not all(math.isfinite(x) and x > 0 for x in (o, h, low, close))
                or not low <= min(o, close) <= max(o, close) <= h):
            raise ValueError("invalid OHLC prices")
        if stamp < opened_ms or stamp >= today_ms:
            continue  # partial listing day and today's forming bar are not history
        day = datetime.fromtimestamp(stamp / 1000, timezone.utc).date().isoformat()
        series[day] = close
    if not series:
        raise ValueError("no completed daily candles")
    # Kraken mark candles are daily even when market volume is zero. Refuse a
    # gap instead of letting downstream calendar ffill manufacture observations.
    dates = sorted(datetime.fromisoformat(day).toordinal() for day in series)
    if any(b - a != 1 for a, b in zip(dates, dates[1:])):
        raise ValueError("gap in exact-contract mark history")
    return dict(sorted(series.items()))


def closes(symbol, cache, *, now=None, fetch=None):
    """Verified exact-contract USD marks, or None with an explicit cache error."""
    now = time.time() if now is None else now
    fetch = public_json if fetch is None else fetch
    records = cache.setdefault(CACHE_KEY, {})
    url = CHARTS_URL + symbol + "/1d?count=1000"
    try:
        if symbol not in VERIFIED_CONTRACTS:
            raise ValueError("contract not verified for this loader")
        hit = records.get(symbol, {})
        if (hit.get("source_url") == url and hit.get("catalogue_url") == INSTRUMENTS_URL
                and hit.get("status") == "OK" and 0 <= now - hit.get("fetched", 0) < CACHE_SECONDS):
            _identity(hit["instrument"], symbol)
            series = hit["series"]
            if series and hit.get("series_sha256") == _series_digest(series):
                return series
        catalogue, catalogue_hash = fetch(INSTRUMENTS_URL)
        if catalogue.get("result") != "success":
            raise ValueError("instrument catalogue unavailable")
        matches = [r for r in catalogue.get("instruments", []) if r.get("symbol") == symbol]
        if len(matches) != 1:
            raise ValueError("exact contract absent or ambiguous")
        row = matches[0]
        opened_ms = _identity(row, symbol)
        payload, payload_hash = fetch(url)
        series = _closes(payload, opened_ms, now)
        records[symbol] = {
            "status": "OK", "fetched": now, "source_url": url,
            "catalogue_url": INSTRUMENTS_URL, "catalogue_sha256": catalogue_hash,
            "candles_sha256": payload_hash, "tick_type": "mark", "resolution": "1d",
            "instrument": {k: row[k] for k in ("symbol", "base", "quote", "type",
                                               "contractSize", "openingDate")},
            "series": series, "series_sha256": _series_digest(series),
            "completed_days": len(series), "first_day": min(series), "last_day": max(series),
            "limitations": "Mark-price risk, not executable bid/ask or total return; funding, fees and liquidity excluded.",
        }
        return series
    except Exception as exc:
        # A failed refresh never blesses an old or other-instrument cached series.
        records[symbol] = {"status": "UNAVAILABLE", "checked": now,
                           "source_url": url, "error": type(exc).__name__ + ": " + str(exc)}
        return None
