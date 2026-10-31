"""Shared parsing for the advisory adapters."""
import datetime as dt


class VendorError(Exception):
    """A vendor call failed. The message must not contain a key or a request URL."""


def num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or abs(f) == float("inf"):
        return None
    return f


def rows(js):
    """A list payload, or None when the shape is not a list we can trust."""
    if isinstance(js, list):
        return js
    if isinstance(js, dict):
        for k in ("data", "results", "historical", "earnings"):
            v = js.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict) and isinstance(v.get("data"), list):
                return v["data"]
    return None


def iso(now):
    if isinstance(now, dt.datetime):
        if now.tzinfo is None:
            now = now.replace(tzinfo=dt.timezone.utc)
        return now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(now)


def is_us_equity(ticker):
    s = str(ticker).strip().upper()
    if not s or ":" in s or "=" in s or s.startswith("^"):
        return False
    return "." not in s


def aged(field_doc, obs_date, now, max_days):
    """A successful read of an old print is stale. The value is kept; it is not replaced."""
    if not field_doc or field_doc.get("missing") or not obs_date:
        return field_doc
    try:
        age = (now.date() - dt.date.fromisoformat(str(obs_date)[:10])).days
    except Exception:
        return field_doc
    if age > max_days:
        out = dict(field_doc)
        out["freshness"] = "stale"
        out["reason"] = "observation older than %sd" % max_days
        return out
    return field_doc
