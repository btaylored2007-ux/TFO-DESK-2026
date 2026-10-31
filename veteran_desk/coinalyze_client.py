"""Coinalyze funding, open interest and liquidations for the THF F pillar. PAPER ONLY.

Free tier is 40 calls/minute. Responses are cached. A missing key, a rate-limit
miss, an HTTP error or a stale cache scores the pillar neutral (None) and the
caller logs a warning. This module never places an order and never prints a key.
"""
import json, os, time, urllib.request
CZ = "https://api.coinalyze.net/v1"
MAX_PER_MIN = 40
CACHE_TTL_S = 900
STALE_NEUTRAL = True
_CALLS = []


def _key():
    k = os.environ.get("COINALYZE_API_KEY")
    if k:
        return k.strip()
    try:
        for ln in open(os.path.expanduser("~/.tfo_secrets/coinalyze.env")):
            if ln.strip().startswith("COINALYZE_API_KEY="):
                return ln.split("=", 1)[1].strip().strip("\"'")
    except Exception:
        return None
    return None


def allow(now=None, n=MAX_PER_MIN, window=60.0, bucket=None):
    """True if this call is inside the 40/min budget. Records the call when allowed."""
    bucket = _CALLS if bucket is None else bucket
    now = time.time() if now is None else now
    bucket[:] = [t for t in bucket if now - t < window]
    if len(bucket) >= n:
        return False
    bucket.append(now)
    return True


def base_of(sym):
    from veteran_desk.crypto_universe import split_alt, ALIAS
    alt = str(sym or "").upper().split(":")[-1]
    base, _q = split_alt(alt)
    return ALIAS.get(base, base)


def cz_symbol(base):
    return str(base).upper() + "USDT_PERP.A"


def f_score(row, direction):
    """Directional F input in about [-1, 1]. None when the row has no usable fields.

    Positive funding (longs pay) is a headwind for a long. Short liquidations
    support a long. OI change confirms the side. Each missing field is skipped;
    if nothing is present the pillar stays neutral.
    """
    if not isinstance(row, dict):
        return None
    d = 1.0 if direction > 0 else -1.0
    parts = []
    if row.get("funding_rate") is not None:
        try:
            parts.append(max(-1.0, min(1.0, -d * float(row["funding_rate"]) * 1000.0)))
        except (TypeError, ValueError):
            pass
    try:
        lo, sh = float(row.get("liq_long") or 0), float(row.get("liq_short") or 0)
    except (TypeError, ValueError):
        lo = sh = 0.0
    if lo + sh > 0:
        parts.append(max(-1.0, min(1.0, d * (sh - lo) / (lo + sh))))
    if row.get("oi_usd") is not None and row.get("oi_prev"):
        try:
            chg = float(row["oi_usd"]) / float(row["oi_prev"]) - 1.0
            parts.append(max(-1.0, min(1.0, d * chg * 5.0)))
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    if not parts:
        return None
    return sum(parts) / len(parts)


def _cache_path(path):
    if path:
        return path
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "state", "vd_engine", "coinalyze_cache.json")


def _read(path):
    try:
        return json.load(open(path))
    except Exception:
        return None


def _write(path, obj):
    if os.environ.get("PYTEST_CURRENT_TEST") and path.endswith(os.path.join("state", "vd_engine", "coinalyze_cache.json")):
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        json.dump(obj, open(tmp, "w"), default=str)
        os.replace(tmp, path)
    except Exception:
        pass


def _http(url, headers, timeout=10):
    req = urllib.request.Request(url, headers=dict({"User-Agent": "TFO paper desk", "Accept": "application/json"}, **(headers or {})))
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return json.loads(f.read())


def snapshot(syms, get=None, now=None, cache_path=None, bucket=None):
    """{ok, stale, warning, by_sym}. Fail open: problems return ok False and by_sym {}.

    by_sym maps KRAKEN-style symbols the caller passed in to raw funding/OI/liq
    rows. Stale cache is NOT used as a score (stale -> neutral).
    """
    now = time.time() if now is None else now
    path = _cache_path(cache_path)
    cached = _read(path) or {}
    fresh = cached.get("fetched") and (now - float(cached["fetched"])) <= CACHE_TTL_S and cached.get("by_base")
    if fresh and get is None:
        return _project(syms, cached["by_base"], ok=True, stale=False, warning=None, fetched=cached.get("fetched"))
    if os.environ.get("PYTEST_CURRENT_TEST") and get is None:
        return dict(ok=False, stale=False, warning="coinalyze skipped under pytest; F pillar neutral", by_sym={}, blocks_trade=False)
    key = _key()
    if not key and get is None:
        return dict(ok=False, stale=False, warning="coinalyze key missing; F pillar neutral", by_sym={}, blocks_trade=False)
    bases = []
    for s in syms or []:
        b = base_of(s)
        if b and b not in bases:
            bases.append(b)
    if not bases:
        return dict(ok=True, stale=False, warning=None, by_sym={}, blocks_trade=False)
    getter = get or (lambda url, headers: _http(url, headers))
    headers = {"api_key": key or "test"}
    # Stale cache is not a score. Only rows fetched this call are used.
    by_base = {}
    calls = 0
    warning = None
    # One call per endpoint, chunked. Stop at the rate limit; the rest stay absent (neutral).
    chunk = 20
    try:
        for i in range(0, len(bases), chunk):
            part = bases[i:i + chunk]
            symbols = ",".join(cz_symbol(b) for b in part)
            if not allow(now + calls * 0.001, bucket=bucket):
                warning = "coinalyze rate limit; unscored names stay F-neutral"
                break
            oi = getter(CZ + "/open-interest?convert_to_usd=true&symbols=" + symbols, headers)
            calls += 1
            if not allow(now + calls * 0.001, bucket=bucket):
                warning = "coinalyze rate limit; unscored names stay F-neutral"
                break
            fr = getter(CZ + "/funding-rate?symbols=" + symbols, headers)
            calls += 1
            if not allow(now + calls * 0.001, bucket=bucket):
                warning = "coinalyze rate limit; unscored names stay F-neutral"
                break
            lq = getter(CZ + "/liquidation-history?interval=1hour&convert_to_usd=true&from=%d&to=%d&symbols=%s" % (int(now) - 86400, int(now), symbols), headers)
            calls += 1
            _merge(by_base, part, oi, fr, lq)
    except Exception:
        warning = "coinalyze fetch failed; F pillar neutral"
        by_base = {}
    if not by_base:
        return dict(ok=False, stale=False, warning=warning or "coinalyze empty; F pillar neutral", by_sym={}, blocks_trade=False)
    _write(path, dict(fetched=now, by_base=by_base))
    return _project(syms, by_base, ok=True, stale=False, warning=warning, fetched=now)


def _merge(by_base, bases, oi, fr, lq):
    want = {cz_symbol(b): b for b in bases}
    for b in bases:
        by_base.setdefault(b, {})
    for x in oi or []:
        b = want.get(x.get("symbol"))
        if b and x.get("value") is not None:
            prev = by_base[b].get("oi_usd")
            by_base[b]["oi_prev"] = prev
            by_base[b]["oi_usd"] = x.get("value")
    for x in fr or []:
        b = want.get(x.get("symbol"))
        if b and x.get("value") is not None:
            by_base[b]["funding_rate"] = x.get("value")
    for x in lq or []:
        b = want.get(x.get("symbol"))
        if not b:
            continue
        hist = x.get("history") or []
        by_base[b]["liq_long"] = sum(float(z.get("l") or 0) for z in hist)
        by_base[b]["liq_short"] = sum(float(z.get("s") or 0) for z in hist)


def _project(syms, by_base, ok, stale, warning, fetched):
    out = {}
    for s in syms or []:
        b = base_of(s)
        row = by_base.get(b)
        if row:
            out[s] = dict(row)
    return dict(ok=ok, stale=stale, warning=warning, by_sym=out, fetched=fetched, blocks_trade=False)
