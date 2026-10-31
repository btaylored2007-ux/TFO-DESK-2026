"""Massive (formerly Polygon) unusual volume, gaps and pre-market volume.

Uses veteran_desk.massive._call. The 20-day average is the twenty completed
daily bars before the latest bar. Fewer than twenty prior bars leaves the
ratio missing. It is never filled from a shorter window.
"""
import datetime as dt, urllib.parse
from zoneinfo import ZoneInfo
from features.envelope import field, from_hit
from features.vendors.common import VendorError, iso, is_us_equity, num

NY = ZoneInfo("America/New_York")
FIELDS = ("unusual_volume", "gap", "premarket_volume")


def fetch(ticker, now, client, gate):
    as_of = iso(now)
    if not is_us_equity(ticker):
        return {name: field(None, as_of, source="massive", applicable=False, reason="us_listed_only") for name in FIELDS}
    sym = str(ticker).strip().upper().replace("-", ".")
    daily = _daily_hit(sym, now, as_of, client, gate)
    pre = _premarket(sym, now, as_of, client, gate)
    if daily.missing:
        miss = from_hit(daily, "massive")
        return {"unusual_volume": miss, "gap": miss, "premarket_volume": pre}
    bars = [r for r in (daily.value or []) if isinstance(r, dict)]
    vol = _unusual(bars)
    gap = _gap(bars)
    fresh = bool(daily.fresh)
    return {
        "unusual_volume": _derived(vol, as_of, fresh, "under 20 prior sessions"),
        "gap": _derived(gap, as_of, fresh, "need two daily bars"),
        "premarket_volume": pre,
    }


def _derived(value, as_of, fresh, why):
    if value is None:
        return field(None, as_of, source="massive", missing=True, reason=why)
    return field(value, as_of, source="massive", fresh=fresh, reason=None if fresh else "served cache after failure")


def _daily_hit(sym, now, as_of, client, gate):
    end = now.date()
    start = end - dt.timedelta(days=50)
    path = "/v2/aggs/ticker/%s/range/1/day/%s/%s" % (urllib.parse.quote(sym), start.isoformat(), end.isoformat())

    def fn():
        js = client(path, {"adjusted": "true", "sort": "asc", "limit": 60})
        if not isinstance(js, dict) or not isinstance(js.get("results"), list):
            raise VendorError("no daily bars")
        return js["results"]

    return gate.call("daily:" + sym, lambda: (fn(), as_of))


def _unusual(bars):
    clean = []
    for r in bars:
        v = num(r.get("v"))
        c = num(r.get("c"))
        if v is None or c is None:
            continue
        clean.append((r, v))
    if len(clean) < 21:
        return None
    prior = [v for _, v in clean[-21:-1]]
    if len(prior) != 20:
        return None
    last_r, last_v = clean[-1]
    avg = sum(prior) / 20.0
    ratio = None if avg == 0 else last_v / avg
    return {
        "volume": last_v,
        "avg20": avg,
        "ratio": ratio,
        "ratio_missing": ratio is None,
        "bar_ms": last_r.get("t"),
    }


def _gap(bars):
    clean = [r for r in bars if num(r.get("o")) is not None and num(r.get("c")) is not None]
    if len(clean) < 2:
        return None
    prev, last = clean[-2], clean[-1]
    pc, o = num(prev["c"]), num(last["o"])
    if not pc:
        return None
    return {"gap_pct": (o - pc) / pc * 100.0, "open": o, "prior_close": pc, "bar_ms": last.get("t")}


def _premarket(sym, now, as_of, client, gate):
    day = now.astimezone(NY).date()
    path = "/v2/aggs/ticker/%s/range/1/minute/%s/%s" % (urllib.parse.quote(sym), day.isoformat(), day.isoformat())

    def fn():
        js = client(path, {"adjusted": "true", "sort": "asc", "limit": 50000})
        if not isinstance(js, dict) or not isinstance(js.get("results"), list):
            raise VendorError("no minute bars")
        total, n = 0.0, 0
        for r in js["results"]:
            if not isinstance(r, dict) or r.get("t") is None:
                continue
            ts = dt.datetime.fromtimestamp(float(r["t"]) / 1000.0, NY)
            if ts.date() != day:
                continue
            minutes = ts.hour * 60 + ts.minute
            if 4 * 60 <= minutes < 9 * 60 + 30:
                v = num(r.get("v"))
                if v is None:
                    continue
                total += v
                n += 1
        return {"volume": total, "bars": n, "session_date": day.isoformat()}

    return from_hit(gate.call("pre:" + sym + ":" + day.isoformat(), lambda: (fn(), as_of)), "massive")
