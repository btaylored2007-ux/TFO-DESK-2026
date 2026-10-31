"""FMP estimates, surprises, grades, insider trades, index changes, earnings calendar.

Calls paid_feeds.fmp (key from ~/.tfo_secrets/fmp.env). A revision is the change in
consensus EPS for the same fiscal date versus the previous observation this store
made. Different fiscal periods are never subtracted, and a missing revision is not
stored as zero.
"""
import datetime as dt
from features.envelope import field, from_hit
from features.vendors.common import VendorError, aged, iso, is_us_equity, num, rows

ESTIMATES = "analyst-estimates"
EARNINGS = "earnings"
GRADES = "grades"
INSIDER = "insider-trading/search"
CALENDAR = "earnings-calendar"
INDEXES = (
    ("sp500", "historical-sp500-constituent"),
    ("nasdaq", "historical-nasdaq-constituent"),
    ("dow", "historical-dowjones-constituent"),
)
FIELDS = (
    "estimate_revisions",
    "earnings_surprises",
    "analyst_grades",
    "insider_trades",
    "index_changes",
    "earnings_calendar",
)


def fetch(ticker, now, client, gate):
    as_of = iso(now)
    sym = str(ticker).strip().upper()
    if not sym or ":" in sym or sym.startswith("^") or "=" in sym:
        return {name: field(None, as_of, source="fmp", applicable=False, reason="not_an_equity") for name in FIELDS}
    out = {
        "estimate_revisions": _estimates(sym, as_of, client, gate),
        "earnings_surprises": _surprises(sym, as_of, now, client, gate),
        "analyst_grades": _grades(sym, as_of, client, gate),
        "insider_trades": _insiders(sym, as_of, client, gate),
        "earnings_calendar": _calendar(sym, as_of, now, client, gate),
    }
    if is_us_equity(sym):
        out["index_changes"] = _index_changes(sym, as_of, client, gate)
    else:
        out["index_changes"] = field(None, as_of, source="fmp", applicable=False, reason="us_index_only")
    return out


def _call(gate, key, as_of, fn):
    return from_hit(gate.call(key, lambda: (fn(), as_of)), "fmp")


def _estimates(sym, as_of, client, gate):
    key = "estimates:" + sym
    prior = (gate.peek(key) or {}).get("value")

    def fn():
        js = client(ESTIMATES, symbol=sym, period="quarter", limit=8)
        payload = _revision(rows(js), prior)
        if payload is None:
            raise VendorError("no analyst estimate rows")
        return payload

    return _call(gate, key, as_of, fn)


def _revision(raw, prior):
    cur = {}
    for r in raw or []:
        if not isinstance(r, dict):
            continue
        d = str(r.get("date") or "")[:10]
        eps = num(r.get("estimatedEpsAvg") if r.get("estimatedEpsAvg") is not None else r.get("epsAvg"))
        if d and eps is not None:
            cur[d] = eps
    if not cur:
        return None
    prev = {}
    if isinstance(prior, dict):
        for d, v in (prior.get("by_fiscal_date") or {}).items():
            n = num(v)
            if n is not None:
                prev[str(d)[:10]] = n
    shared = sorted(set(cur) & set(prev))
    fiscal = shared[0] if shared else sorted(cur)[0]
    missing = fiscal not in prev
    return {
        "fiscal_date": fiscal,
        "estimated_eps": cur[fiscal],
        "prior_estimated_eps": None if missing else prev[fiscal],
        "eps_revision": None if missing else cur[fiscal] - prev[fiscal],
        "eps_revision_missing": missing,
        "by_fiscal_date": cur,
    }


def _surprises(sym, as_of, now, client, gate):
    def fn():
        js = client(EARNINGS, symbol=sym, limit=12)
        best = None
        for r in rows(js) or []:
            if not isinstance(r, dict):
                continue
            d = str(r.get("date") or "")[:10]
            actual = num(r.get("epsActual"))
            if actual is None and "epsActual" not in r:
                actual = num(r.get("eps"))
            est = num(r.get("epsEstimated"))
            if est is None:
                est = num(r.get("eps_estimated"))
            if not d or actual is None or est is None:
                continue
            pct = None if est == 0 else (actual - est) / abs(est) * 100.0
            row = {
                "date": d,
                "eps_actual": actual,
                "eps_estimated": est,
                "surprise_pct": pct,
                "surprise_missing": pct is None,
            }
            if best is None or d > best["date"]:
                best = row
        if best is None:
            raise VendorError("no earnings surprise row")
        return best

    hit = gate.call("surprises:" + sym, lambda: (fn(), as_of))
    doc = from_hit(hit, "fmp")
    obs = None if doc.get("missing") or not isinstance(doc.get("value"), dict) else doc["value"].get("date")
    return aged(doc, obs, now, 120)


def _grades(sym, as_of, client, gate):
    def fn():
        js = client(GRADES, symbol=sym, limit=15)
        out = []
        for r in (rows(js) or [])[:15]:
            if not isinstance(r, dict):
                continue
            out.append({
                "date": r.get("date"),
                "firm": r.get("gradingCompany"),
                "action": r.get("action"),
                "from_grade": r.get("previousGrade"),
                "to_grade": r.get("newGrade"),
            })
        if not out:
            raise VendorError("no analyst grades")
        return {"latest": out[0], "rows": out}

    return _call(gate, "grades:" + sym, as_of, fn)


def _insiders(sym, as_of, client, gate):
    def fn():
        js = client(INSIDER, symbol=sym, page=0, limit=10)
        raw = rows(js)
        if raw is None:
            raise VendorError("insider payload unusable")
        out = []
        for r in raw[:10]:
            if not isinstance(r, dict):
                continue
            out.append({
                "filing_date": r.get("filingDate"),
                "transaction_date": r.get("transactionDate"),
                "reporting_name": r.get("reportingName"),
                "transaction_type": r.get("transactionType"),
                "securities": num(r.get("securitiesTransacted")),
                "price": num(r.get("price")),
            })
        return {"rows": out}

    return _call(gate, "insider:" + sym, as_of, fn)


def _calendar(sym, as_of, now, client, gate):
    """One cached calendar for the window, then filtered to this ticker."""
    start = now.date()
    end = start + dt.timedelta(days=14)

    def fn():
        js = client(CALENDAR, **{"from": start.isoformat(), "to": end.isoformat()})
        raw = rows(js)
        if raw is None:
            raise VendorError("earnings calendar unusable")
        return raw

    hit = gate.call("calendar:" + start.isoformat(), lambda: (fn(), as_of))
    if hit.missing:
        return from_hit(hit, "fmp")
    matched = []
    for r in hit.value or []:
        if not isinstance(r, dict) or str(r.get("symbol") or "").upper() != sym:
            continue
        d = str(r.get("date") or "")[:10]
        if d:
            matched.append({"date": d, "time": r.get("time"), "eps_estimated": num(r.get("epsEstimated"))})
    return field(
        {"rows": matched, "window_from": start.isoformat(), "window_to": end.isoformat()},
        hit.as_of or as_of,
        source="fmp",
        fresh=bool(hit.fresh),
        reason=hit.reason,
    )


def _index_changes(sym, as_of, client, gate):
    rows_out = []
    saw = False
    fresh = True
    reasons = []
    for name, path in INDEXES:
        def fn(path=path):
            js = client(path)
            raw = rows(js)
            if raw is None:
                raise VendorError("index payload unusable")
            return raw

        hit = gate.call("index:" + name, lambda fn=fn: (fn(), as_of))
        if hit.missing:
            fresh = False
            reasons.append(name)
            continue
        saw = True
        if not hit.fresh:
            fresh = False
        for r in hit.value or []:
            if not isinstance(r, dict):
                continue
            added = str(r.get("symbol") or "").upper()
            removed = str(r.get("removedTicker") or "").upper()
            if added != sym and removed != sym:
                continue
            rows_out.append({
                "index": name,
                "date": r.get("date") or r.get("dateAdded"),
                "symbol": r.get("symbol"),
                "removed": r.get("removedTicker"),
                "reason": r.get("reason"),
            })
    if not saw:
        return field(None, as_of, source="fmp", missing=True, reason="index history unavailable")
    return field(
        {"rows": rows_out},
        as_of,
        source="fmp",
        fresh=fresh,
        reason=None if fresh else "partial index history: " + ",".join(reasons),
    )
