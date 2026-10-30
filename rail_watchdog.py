#!/usr/bin/env python3
"""Blocked-rail watchdog (funder directive 2026-09-28: 'never miss a bank turn'). PAPER, report-only.

Each rail runner (equity: tfo_ticket.py --execute every 300s; crypto: tfo_crypto.py rails --execute every 60s)
calls update(runner, rows) once per cycle. A row is a position whose rail is DUE (HALF/STOP/FLAT/TRAIL) but whose
exit did not execute this cycle (status BLOCKED, or READY without a ticket), or a held position with NO_QUOTE
(its rail cannot be evaluated). After more than ALERT_AFTER_CYCLES (2) consecutive cycles the entry becomes an
ALERT in reports/rail_watchdog.json and a 'RAIL-WATCHDOG ALERT' line is printed to the runner's log.
Due rails blocked only because the venue's market is shut are listed under pending_market_shut (no alert).
Due rails blocked only because the name has no current-session print yet (thin/no tape) are listed under
pending_no_tape (no alert); once a print exists and the exit still fails, the normal blocked alert applies.
Never places, blocks or edits a trade. Never raises into the runner.
"""
import datetime as dt, fcntl, json, os, tempfile
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__)); R = os.path.join(BASE, "reports")
PATH = os.path.join(R, "rail_watchdog.json"); EVENTS = os.path.join(R, "rail_watchdog_events.jsonl")
LOCK = os.path.join(R, ".rail_watchdog.lock")
SCHEMA = "rail_watchdog v1 2026-09-28"
ALERT_AFTER_CYCLES = 2            # alert when blocked for MORE than 2 consecutive cycles
INTERVALS = {"equity": 300, "crypto": 60}
SYD = ZoneInfo("Australia/Sydney"); UTC = dt.timezone.utc
SHUT_SESSIONS = ("closed", "pre", "post", "prepost")


def _iso(t): return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
def _syd(t): return t.astimezone(SYD).strftime("%Y-%m-%d %H:%M AEST")
def _parse(s): return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def classify(row):
    """-> None (not a watchdog case) | 'blocked' | 'market_shut' | 'no_tape' | 'no_quote'."""
    st = str(row.get("status") or "").upper()
    if st.startswith("NO_QUOTE"):
        return "no_quote"
    if not row.get("rail"):
        return None
    if st.startswith("BLOCKED") or (st.startswith("READY") and not row.get("executed")):
        why = str(row.get("why") or "").lower()
        if "no tape yet" in why or "awaiting first regular-session print" in why:
            return "no_tape"
        sess = str(row.get("session") or "").lower()
        return "market_shut" if sess in SHUT_SESSIONS else "blocked"
    return None


def _load(path):
    try:
        with open(path) as f: return json.load(f)
    except Exception:
        return None


def _write(path, obj):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".rail_watchdog", suffix=".tmp")
    with os.fdopen(fd, "w") as f: json.dump(obj, f, indent=1, default=str)
    os.replace(tmp, path)


def update(runner, rows, now=None, path=None, events_path=None, log=print):
    """Record one runner cycle. Returns the list of current alerts. Never raises."""
    try:
        return _update(runner, rows, now or dt.datetime.now(UTC), path or PATH, events_path or EVENTS, log)
    except Exception as e:  # the watchdog must never break a rail runner
        try: log("RAIL-WATCHDOG error (ignored): %s: %s" % (type(e).__name__, e))
        except Exception: pass
        return []


def _update(runner, rows, now, path, events_path, log):
    lockp = os.path.join(os.path.dirname(path), ".rail_watchdog.lock")
    with open(lockp, "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        doc = _load(path) or {}
        track = doc.get("tracking") or {}
        seen, events = set(), []
        for r in rows:
            kind = classify(r)
            if not kind: continue
            sym = r.get("sym") or r.get("symbol"); book = r.get("book") or ""
            rule = r.get("exit_rule") or r.get("rail") or ("UNKNOWN (no quote)" if kind == "no_quote" else None)
            key = "%s|%s|%s" % (runner, book, sym)
            seen.add(key)
            prev = track.get(key)
            if prev and prev.get("kind") == kind and prev.get("rule") == rule:
                rec = prev; rec["count"] = int(rec.get("count") or 0) + 1
            else:
                rec = {"runner": runner, "book": book, "symbol": sym, "kind": kind, "rule": rule,
                       "first_blocked_utc": _iso(now), "first_blocked_sydney": _syd(now), "count": 1}
            lvl = r.get("rail_level_usd")
            rec.update({"action": r.get("rail"), "level_usd": None if lvl is None else round(float(lvl), 8),
                        "px_usd": r.get("px_usd"), "ret_orig_pct": r.get("ret_orig_pct"), "session": r.get("session"),
                        "reason": r.get("why") or r.get("status"), "last_blocked_utc": _iso(now)})
            rec["minutes_blocked"] = round((now - _parse(rec["first_blocked_utc"])).total_seconds() / 60, 1)
            was_alert = bool(prev and prev.get("alert"))
            rec["alert"] = kind not in ("market_shut", "no_tape") and rec["count"] > ALERT_AFTER_CYCLES
            if rec["alert"] and not was_alert:
                events.append({"utc": _iso(now), "event": "RAISED", **{k: rec.get(k) for k in ("runner", "book", "symbol", "rule", "level_usd", "reason", "count", "first_blocked_utc")}})
            track[key] = rec
        for key in [k for k in track if k.startswith(runner + "|") and k not in seen]:
            old = track.pop(key)
            if old.get("alert"):
                events.append({"utc": _iso(now), "event": "CLEARED", **{k: old.get(k) for k in ("runner", "book", "symbol", "rule", "count", "first_blocked_utc")}})
        runners = doc.get("runners") or {}
        runners[runner] = {"last_cycle_utc": _iso(now), "interval_sec": INTERVALS.get(runner),
                           "tracked": sum(1 for k in track if k.startswith(runner + "|"))}
        alerts = sorted((v for v in track.values() if v.get("alert")), key=lambda v: v["first_blocked_utc"])
        shut = sorted((v for v in track.values() if v.get("kind") == "market_shut"), key=lambda v: v["first_blocked_utc"])
        no_tape = sorted((v for v in track.values() if v.get("kind") == "no_tape"), key=lambda v: v["first_blocked_utc"])
        hist = (doc.get("history") or []) + events
        out = {"schema": SCHEMA, "paper_only": True, "report_only": True,
               "updated_utc": _iso(now), "updated_sydney": _syd(now),
               "rule": "alert when a DUE rail exit (HALF/STOP/FLAT/TRAIL) is not executed for more than %d consecutive runner cycles "
                       "while its market is open and a print exists, or a held position has no quote for more than %d cycles; "
                       "no-tape-yet and shut markets stay pending" % (ALERT_AFTER_CYCLES, ALERT_AFTER_CYCLES),
               "status": "ALERT" if alerts else "OK", "alert_count": len(alerts),
               "alerts": [{k: v.get(k) for k in ("runner", "book", "symbol", "kind", "rule", "action", "level_usd", "px_usd", "ret_orig_pct",
                                                  "reason", "session", "first_blocked_utc", "first_blocked_sydney", "last_blocked_utc", "count", "minutes_blocked")} for v in alerts],
               "pending_market_shut": [{k: v.get(k) for k in ("runner", "book", "symbol", "rule", "level_usd", "reason", "first_blocked_utc", "count")} for v in shut],
               "pending_no_tape": [{k: v.get(k) for k in ("runner", "book", "symbol", "rule", "level_usd", "reason", "first_blocked_utc", "count")} for v in no_tape],
               "runners": runners, "tracking": track, "history": hist[-100:]}
        _write(path, out)
    if events:
        with open(events_path, "a") as f:
            for e in events: f.write(json.dumps(e) + "\n")
    for a in alerts:
        if a["runner"] == runner:
            log("RAIL-WATCHDOG ALERT %s %s %s rule=%s level=%s blocked x%d since %s: %s" % (runner, a["book"], a["symbol"], a["rule"], a.get("level_usd"),
                a["count"], a["first_blocked_sydney"], a.get("reason")))
    for e in events:
        if e["event"] == "CLEARED" and e["runner"] == runner:
            log("RAIL-WATCHDOG CLEARED %s %s %s" % (runner, e["book"], e["symbol"]))
    return alerts


def summary(path=None, now=None):
    """Small read-only view for the War Room header. Adds runner staleness."""
    doc = _load(path or PATH)
    now = now or dt.datetime.now(UTC)
    if not doc:
        return {"status": "UNKNOWN", "alert_count": 0, "alerts": [], "note": "rail_watchdog.json not written yet"}
    stale = []
    for name, r in (doc.get("runners") or {}).items():
        try:
            age = (now - _parse(r["last_cycle_utc"])).total_seconds()
            if r.get("interval_sec") and age > 3 * r["interval_sec"]: stale.append({"runner": name, "age_sec": round(age)})
        except Exception:
            stale.append({"runner": name, "age_sec": None})
    return {"status": doc.get("status"), "updated_utc": doc.get("updated_utc"), "alert_count": doc.get("alert_count", 0),
            "alerts": doc.get("alerts", []), "pending_market_shut": doc.get("pending_market_shut", []),
            "pending_no_tape": doc.get("pending_no_tape", []),
            "runners": doc.get("runners", {}), "stale_runners": stale}


if __name__ == "__main__":
    print(json.dumps(summary(), indent=1))
