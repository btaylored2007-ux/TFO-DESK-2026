"""Assemble one advisory ticker file. Vendor failures become stale missing fields.

Nothing here arms a trade or edits a rail. Earnings inside the next two calendar
days is a flag on the file, computed only from an earnings calendar we actually
received. A missing calendar does not become 'no earnings'.
"""
import datetime as dt, json, os
from features.envelope import field
from features.paths import regime_path, ticker_path
from features.scrub import scrub
from features.vendors import coinalyze, eodhd, fmp, massive, unusual_whales
from features.vendors.common import iso

UTC = dt.timezone.utc
GROUPS = (
    ("fmp", fmp.FIELDS),
    ("unusual_whales", unusual_whales.FIELDS),
    ("massive", massive.FIELDS),
    ("eodhd", ("fundamentals",)),
    ("coinalyze", coinalyze.FIELDS),
)


def earnings_within_2d(calendar, today, as_of):
    if not calendar or calendar.get("applicable") is False:
        return field(None, as_of, source="fmp", applicable=False, reason="no equity calendar")
    if calendar.get("missing") or not isinstance(calendar.get("value"), dict):
        return field(None, calendar.get("as_of") if calendar else None, source="fmp", missing=True, reason="earnings calendar missing")
    dates = []
    for row in calendar["value"].get("rows") or []:
        try:
            dates.append(dt.date.fromisoformat(str(row.get("date"))[:10]))
        except Exception:
            continue
    flag = any(0 <= (d - today).days <= 2 for d in dates)
    fresh = calendar.get("freshness") == "fresh"
    return field(flag, calendar.get("as_of") or as_of, source="fmp", fresh=fresh, reason=None if fresh else "calendar stale")


def build_ticker(ticker, now, clients):
    as_of = iso(now)
    today = now.astimezone(UTC).date()
    fields = {}
    loaders = (
        ("fmp", lambda: fmp.fetch(ticker, now, clients.fmp, clients.gates["fmp"])),
        ("unusual_whales", lambda: unusual_whales.fetch(ticker, now, clients.uw, clients.gates["unusual_whales"])),
        ("massive", lambda: massive.fetch(ticker, now, clients.massive, clients.gates["massive"])),
        ("eodhd", lambda: eodhd.fetch(ticker, now, clients.eodhd, clients.gates["eodhd"], clients.eodhd_symbol)),
        ("coinalyze", lambda: coinalyze.fetch(ticker, now, clients.coinalyze, clients.gates["coinalyze"])),
    )
    for name, fn in loaders:
        try:
            got = fn()
        except Exception as e:
            got = {key: field(None, as_of, source=name, missing=True, reason=scrub(e)) for key in dict(GROUPS)[name]}
        fields[name] = got
    cal = (fields.get("fmp") or {}).get("earnings_calendar")
    try:
        fields["earnings_within_2d"] = earnings_within_2d(cal, today, as_of)
    except Exception as e:
        fields["earnings_within_2d"] = field(None, as_of, source="fmp", missing=True, reason=scrub(e))
    return _banner(as_of, ticker=str(ticker), fields=fields)


def _banner(as_of, **extra):
    doc = {"advisory": True, "gates": False, "rails": "unchanged", "paper_only": True, "built_utc": as_of}
    doc.update(extra)
    return doc


def write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=1, default=str)
        f.write("\n")
    os.replace(tmp, path)
    return path


def run(tickers, base=None, clients=None, now=None):
    """Write state/features/<ticker>.json and state/regime.json. Does not raise on vendor failure."""
    from features.clients import Clients
    from features.regime import build_regime
    now = now or dt.datetime.now(UTC)
    clients = clients or Clients(base=base)
    written, docs, errors = [], [], []
    for t in tickers:
        try:
            doc = build_ticker(t, now, clients)
            written.append(write_json(ticker_path(t, base), doc))
            docs.append(doc)
        except Exception as e:
            errors.append({"ticker": str(t), "reason": scrub(e)})
    try:
        reg = build_regime(now, clients)
    except Exception as e:
        reg = _banner(iso(now), fields={}, error=scrub(e))
    reg["earnings_within_2d"] = [_earn_row(d) for d in docs]
    try:
        regime_file = write_json(regime_path(base), reg)
    except Exception as e:
        regime_file = None
        errors.append({"ticker": "regime", "reason": scrub(e)})
    return {"tickers": written, "regime": regime_file, "errors": errors}


def _earn_row(doc):
    f = doc.get("fields", {}).get("earnings_within_2d") or {}
    return {
        "ticker": doc.get("ticker"),
        "value": f.get("value"),
        "as_of": f.get("as_of"),
        "freshness": f.get("freshness"),
        "missing": f.get("missing"),
        "applicable": f.get("applicable", True),
    }
