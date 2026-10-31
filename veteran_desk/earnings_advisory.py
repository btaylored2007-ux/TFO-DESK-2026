"""EODHD earnings blackout as an advisory flag on a grade. PAPER ONLY.

Fail open: no key, a stale cache, or any error yields no flag and a warning.
blocks_trade is always false. This does not change the existing earnings wake
block, stops, size, caps or the firm halt.
"""
import datetime as dt, json, os, time
UTC = dt.timezone.utc
BLACKOUT_DAYS = 2
STALE_H = 36.0
REV = {"AU": ".AX", "LSE": ".L", "TSE": ".T", "HK": ".HK", "TO": ".TO", "KO": ".KS", "TW": ".TW",
       "PA": ".PA", "XETRA": ".DE", "AS": ".AS", "SW": ".SW", "ST": ".ST", "CO": ".CO", "HE": ".HE",
       "OL": ".OL", "MC": ".MC", "MI": ".MI"}


def _root():
    return os.environ.get("DESK_BASE") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cache():
    return os.path.join(_root(), "state", "calendar", "earnings_advisory_cache.json")


def _has_key():
    if os.environ.get("EODHD_API_KEY") or os.environ.get("TFO_EODHD_KEY"):
        return True
    try:
        return os.path.exists(os.path.expanduser("~/.tfo_secrets/eodhd.env"))
    except Exception:
        return False


def to_desk(code):
    code = str(code or "").upper().strip()
    if not code or "." not in code:
        return None
    base, ex = code.rsplit(".", 1)
    if ex == "US":
        if "-" in base and len(base.split("-")[-1]) == 1:
            return base.replace("-", ".")
        return base
    suf = REV.get(ex)
    return (base + suf) if suf else None


def _when(row):
    raw = (row or {}).get("report_date") or (row or {}).get("date")
    try:
        return dt.date.fromisoformat(str(raw)[:10])
    except Exception:
        return None


def index_rows(rows):
    """{DESK_SYM: earliest report date}."""
    out = {}
    for row in rows or []:
        code = (row or {}).get("code") or (row or {}).get("symbol")
        sym = to_desk(code)
        d = _when(row)
        if not sym or not d:
            continue
        if sym not in out or d < out[sym]:
            out[sym] = d
    return out


def _read_cache(now_ts, cache_path):
    try:
        age_h = (now_ts - os.path.getmtime(cache_path)) / 3600.0
        if age_h > STALE_H:
            return None
        return json.load(open(cache_path))
    except Exception:
        return None


def _write_cache(rows, cache_path):
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        tmp = cache_path + ".tmp"
        json.dump(rows, open(tmp, "w"))
        os.replace(tmp, cache_path)
    except Exception:
        pass


def load_rows(today=None, fetch=None, now_ts=None, cache_path=None):
    """(rows or None, warning or None). None rows => do not flag."""
    today = today or dt.datetime.now(UTC).date()
    now_ts = time.time() if now_ts is None else now_ts
    cache_path = cache_path or _cache()
    cached = _read_cache(now_ts, cache_path)
    if fetch is None and not _has_key():
        if cached is not None:
            return cached, None
        return None, "eodhd key missing; earnings blackout advisory fail-open"
    try:
        if fetch is not None:
            rows = fetch(today)
        else:
            from veteran_desk import data_layer as DL
            rows = DL.earnings(str(today), str(today + dt.timedelta(days=BLACKOUT_DAYS)))
    except Exception as e:
        rows = None
        err = "eodhd earnings %s; advisory fail-open" % type(e).__name__
        if cached is not None:
            return cached, None
        return None, err
    if rows is None:
        if cached is not None:
            return cached, None
        return None, "eodhd earnings unavailable; advisory fail-open"
    if rows:
        _write_cache(rows, cache_path)
    return rows, None


def flags_for(syms, today=None, rows=None, fetch=None, now_ts=None, cache_path=None):
    """{SYM: flag}. Empty when the calendar is missing or stale. Never raises."""
    today = today or dt.datetime.now(UTC).date()
    warning = None
    try:
        if rows is None:
            rows, warning = load_rows(today, fetch=fetch, now_ts=now_ts, cache_path=cache_path)
        if rows is None:
            return {}
        want = {str(s).upper() for s in (syms or [])}
        idx = index_rows(rows)
        out = {}
        for sym, d in idx.items():
            if want and sym not in want:
                continue
            dd = (d - today).days
            if 0 <= dd <= BLACKOUT_DAYS:
                out[sym] = dict(flag="earnings_blackout", date=d.isoformat(), source="eodhd",
                                advisory=True, blocks_trade=False, days=dd)
        return out
    except Exception:
        return {}


def warning_for(today=None, fetch=None, now_ts=None, cache_path=None):
    try:
        _rows, warning = load_rows(today, fetch=fetch, now_ts=now_ts, cache_path=cache_path)
        return warning
    except Exception as e:
        return "earnings advisory %s; fail-open" % type(e).__name__
