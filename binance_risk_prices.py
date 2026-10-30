"""Bounded Binance spot histories translated to USD, for PAPER risk only.

No orders, keys or financial ledger writes. Exact verified source gaps only.
USD = same-UTC-day Binance USDT close * Kraken USDT/USD close, never a $1 peg.
This is a cross-venue valuation conversion, not an executable paired fill.
"""
import hashlib
import json
import math
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
try:
    import desk_doh; desk_doh.install(ibkr=False, oanda=False, extra=True)  # 2026-10-27 audit
except Exception as _doh_e:
    print("desk_doh unavailable: %r" % _doh_e, file=__import__("sys").stderr)

from kraken_futures_risk_prices import public_json

PREFIX = "BINANCE:"
VERIFIED_PAIRS = {
    "CATIUSDT": "CATI", "MANTAUSDT": "MANTA", "CRWVBUSDT": "CRWVB",
    # Exact Binance spot/USDT identities reverified 2026-09-12. These are not
    # ordinary-equity proxies; short histories still fail the existing risk gate.
    "IQUSDT": "IQ", "GMEBUSDT": "GMEB", "LITEBUSDT": "LITEB",
    # Exact held Binance spot/USDT price sources verified 2026-09-13;
    # this mapping is not proof of an executable Kraken pair or paper fill.
    "QKCUSDT": "QKC", "STEEMUSDT": "STEEM",
    # Exact held Binance spot/USDT risk sources verified 2026-09-14;
    # this does not validate Kraken execution or the underlying strategy.
    "CKBUSDT": "CKB", "TFUELUSDT": "TFUEL",
    # Exact held Binance spot/USDT risk sources verified 2026-09-18 FIX ALL;
    # rumour lowvol band names that were falling through to KRK: and failing OHLC.
    "AWEUSDT": "AWE", "GLMUSDT": "GLM", "MBLUSDT": "MBL",
    "QTUMUSDT": "QTUM", "CELOUSDT": "CELO",
}
CACHE_KEY = "binance_spot_usd_v1"
CACHE_SECONDS = 12 * 3600
BINANCE = "https://api.binance.com/api/v3/"
FX_INFO = "https://api.kraken.com/0/public/AssetPairs?pair=USDTUSD"
FX_PRICES = "https://api.kraken.com/0/public/OHLC?pair=USDTUSD&interval=1440"
DAY_MS = 86_400_000
LIMITATION = ("Binance spot USDT histories use same-date Kraken USDT/USD conversion, not a fixed peg or executable FX fill. "
              "Authoritative paper-book USD-equivalent marks/equity retain their existing accounting convention.")


def digest(series):
    return hashlib.sha256(json.dumps(series, sort_keys=True, allow_nan=False).encode()).hexdigest()


def validate_ohlc(values):
    if any(isinstance(x, bool) for x in values):
        raise ValueError("boolean OHLC price")
    o, h, low, close = map(float, values)
    if (not all(math.isfinite(x) and x > 0 for x in (o, h, low, close))
            or not low <= min(o, close) <= max(o, close) <= h):
        raise ValueError("invalid OHLC prices")
    return close


def validate_days(series):
    if not series:
        raise ValueError("no completed daily observations")
    days = sorted(datetime.fromisoformat(d).toordinal() for d in series)
    if any(b - a != 1 for a, b in zip(days, days[1:])):
        raise ValueError("gap in daily observations")


def parse_klines(rows, now):
    if not isinstance(rows, list) or not rows:
        raise ValueError("missing Binance klines")
    today = int(now // 86400) * DAY_MS
    series, seen = {}, set()
    for row in rows:
        if not isinstance(row, list) or len(row) != 12:
            raise ValueError("wrong Binance kline shape")
        stamp, end = row[0], row[6]
        if (type(stamp) is not int or type(end) is not int or stamp < 0
                or stamp % DAY_MS or stamp in seen or stamp > today
                or end != stamp + DAY_MS - 1):
            raise ValueError("invalid/duplicate/non-UTC-daily Binance timestamp")
        seen.add(stamp)
        close = validate_ohlc(row[1:5])
        if stamp < today:
            series[datetime.fromtimestamp(stamp / 1000, timezone.utc).date().isoformat()] = close
    validate_days(series)
    # A first listing candle can begin intraday despite a midnight open key.
    # Conservatively drop the oldest returned day even for a rolling window.
    series.pop(min(series))
    validate_days(series)
    return dict(sorted(series.items()))


def parse_fx(info, payload, now):
    if info.get("error") != [] or payload.get("error") != []:
        raise ValueError("Kraken USDT/USD source error")
    result = info.get("result", {})
    if set(result) != {"USDTZUSD"}:
        raise ValueError("ambiguous USDT/USD identity")
    identity = result["USDTZUSD"]
    if any(identity.get(k) != v for k, v in {"altname": "USDTUSD", "wsname": "USDT/USD",
                                              "base": "USDT", "quote": "ZUSD"}.items()):
        raise ValueError("wrong USDT/USD currency identity")
    result = payload.get("result", {})
    if set(result) != {"USDTZUSD", "last"} or not isinstance(result["USDTZUSD"], list):
        raise ValueError("wrong USDT/USD OHLC identity")
    today, seen, series = int(now // 86400) * 86400, set(), {}
    for row in result["USDTZUSD"]:
        if not isinstance(row, list) or len(row) != 8:
            raise ValueError("wrong FX OHLC shape")
        stamp = row[0]
        if type(stamp) is not int or stamp < 0 or stamp % 86400 or stamp in seen or stamp > today:
            raise ValueError("invalid/duplicate/non-daily FX timestamp")
        seen.add(stamp)
        close = validate_ohlc(row[1:5])
        if stamp < today:
            series[datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()] = close
    validate_days(series)
    return dict(sorted(series.items()))


def usd_series(coin, fx):
    validate_days(coin)
    validate_days(fx)
    if max(coin) != max(fx):
        raise ValueError("coin/FX completed endpoints differ")
    shared = sorted(set(coin) & set(fx))
    series = {day: coin[day] * fx[day] for day in shared}
    validate_days(series)
    if not all(math.isfinite(x) and x > 0 for x in series.values()):
        raise ValueError("invalid converted USD close")
    return series


def load(symbols, cache, *, now=None, fetch=None):
    """Return {exact_symbol: USD_daily_series}; absent keys remain unknown risk."""
    now, fetch = time.time() if now is None else now, fetch or public_json
    records = cache.get(CACHE_KEY)
    if not isinstance(records, dict):
        records = cache[CACHE_KEY] = {}  # data cache only; never a financial ledger
    out, pending = {}, []
    for symbol in sorted(set(symbols)):
        if symbol not in VERIFIED_PAIRS:
            records[symbol] = {"status": "UNAVAILABLE", "checked": now, "error": "unverified Binance symbol"}
            continue
        hit = records.get(symbol, {})
        if not isinstance(hit, dict):
            hit = {}
        try:
            valid = (hit.get("status") == "OK" and hit.get("symbol") == symbol
                     and hit.get("base") == VERIFIED_PAIRS[symbol] and hit.get("quote") == "USDT"
                     and hit.get("output_currency") == "USD" and hit.get("fx_source") == FX_PRICES
                     and hit.get("source_url") == BINANCE + "klines?" + urlencode({
                         "symbol": symbol, "interval": "1d", "timeZone": "0", "limit": 1000})
                     and 0 <= now - hit.get("fetched", 0) < CACHE_SECONDS
                     and isinstance(hit.get("series"), dict) and bool(hit["series"])
                     and hit.get("series_sha256") == digest(hit["series"]))
        except (TypeError, ValueError):
            valid = False
        if valid:
            out[symbol] = hit["series"]
        else:
            pending.append(symbol)
    if not pending:
        return out
    info_url = BINANCE + "exchangeInfo?" + urlencode({"symbols": json.dumps(pending, separators=(",", ":"))})
    try:
        info, info_hash = fetch(info_url)
        fx_info, fx_info_hash = fetch(FX_INFO)
        fx_payload, fx_hash = fetch(FX_PRICES)
        fx = parse_fx(fx_info, fx_payload, now)
        identities = info.get("symbols")
        if not isinstance(identities, list):
            raise ValueError("missing Binance exchangeInfo")
    except Exception as exc:
        for symbol in pending:
            records[symbol] = {"status": "UNAVAILABLE", "checked": now, "error": str(exc)}
        return out
    for symbol in pending:
        url = BINANCE + "klines?" + urlencode({"symbol": symbol, "interval": "1d", "timeZone": "0", "limit": 1000})
        try:
            rows = [r for r in identities if r.get("symbol") == symbol]
            if (len(rows) != 1 or rows[0].get("baseAsset") != VERIFIED_PAIRS[symbol]
                    or rows[0].get("quoteAsset") != "USDT" or rows[0].get("isSpotTradingAllowed") is not True):
                raise ValueError("wrong Binance spot identity/currency")
            payload, payload_hash = fetch(url)
            coin = parse_klines(payload, now)
            series = usd_series(coin, fx)
            records[symbol] = {
                "status": "OK", "fetched": now, "symbol": symbol, "base": VERIFIED_PAIRS[symbol],
                "quote": "USDT", "output_currency": "USD", "source_url": url,
                "catalogue_url": info_url, "catalogue_sha256": info_hash, "klines_sha256": payload_hash,
                "fx_source": FX_PRICES, "fx_sha256": fx_hash, "fx_catalogue_url": FX_INFO,
                "fx_catalogue_sha256": fx_info_hash, "fx_last_day": max(fx), "fx_last_close": fx[max(fx)],
                "series": series, "series_sha256": digest(series), "completed_days": len(series),
                "first_day": min(series), "last_day": max(series), "limitation": LIMITATION,
            }
            out[symbol] = series
        except Exception as exc:
            records[symbol] = {"status": "UNAVAILABLE", "checked": now, "source_url": url, "error": str(exc)}
    return out
