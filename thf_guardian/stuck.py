"""Stuck armed orders. Deterministic. Does not edit the book.

An order is stuck when it is still armed after stuck_after seconds and either
the trigger has traded through, or the book left a stale-price block on a
delayed venue (ASX and the other feed_delay floors). Session-closed waits are
not stuck.
"""
from thf_guardian.config import DELAYED_VENUES
from thf_guardian.util import parse_dt


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def trigger_traded_through(pos, mark):
    """True when the bar traded through the armed trigger. Needs a mark."""
    if not mark or mark.get("last") is None:
        return False
    entry = _f(pos.get("entry"))
    if entry is None:
        return False
    last = _f(mark.get("last"))
    hi = _f(mark.get("hi"))
    lo = _f(mark.get("lo"))
    if hi is None:
        hi = last
    if lo is None:
        lo = last
    long_ = str(pos.get("side")).upper() == "LONG"
    ot = (pos.get("order_type") or "limit").lower()
    if ot == "stop_limit":
        return (long_ and hi >= entry) or ((not long_) and lo <= entry)
    return (long_ and lo <= entry) or ((not long_) and hi >= entry)


def _stale_note(pos):
    note = str(pos.get("exec_note") or "").lower()
    return "stale" in note and "price" in note


def _session_closed(pos, mark):
    note = str(pos.get("exec_note") or "").lower()
    if "session closed" in note:
        return True
    if mark and mark.get("session_open") is False:
        return True
    return False


def assess(pos, mark, now, stuck_after_sec=300):
    if (pos or {}).get("status") != "armed":
        return dict(stuck=False, reason="not_armed")
    armed = parse_dt(pos.get("armed_utc"))
    if armed is None:
        return dict(stuck=False, reason="no_armed_utc")
    age = (now - armed).total_seconds()
    if age < stuck_after_sec:
        return dict(stuck=False, reason="young", age_sec=age)
    through = trigger_traded_through(pos, mark)
    stale = _stale_note(pos)
    venue = pos.get("venue") or ""
    delayed = venue in DELAYED_VENUES
    if _session_closed(pos, mark) and not (stale and delayed):
        return dict(stuck=False, reason="session_closed", age_sec=age)
    if through:
        why = "stale_price_delayed_venue" if (stale and delayed) else "trigger_through"
        return dict(stuck=True, reason=why, age_sec=age, venue=venue, grade=pos.get("grade"),
                    ticker=pos.get("ticker"), side=pos.get("side"), entry=pos.get("entry"),
                    stop=pos.get("stop"), shares=pos.get("shares"))
    if stale and delayed:
        return dict(stuck=True, reason="stale_price_delayed_venue", age_sec=age, venue=venue,
                    grade=pos.get("grade"), ticker=pos.get("ticker"), side=pos.get("side"),
                    entry=pos.get("entry"), stop=pos.get("stop"), shares=pos.get("shares"))
    return dict(stuck=False, reason="working", age_sec=age)


def scan_book(book, marks, now, stuck_after_sec=300):
    """marks: {ticker: {last, hi, lo, session_open}}. Missing mark still catches a stale-price note."""
    out = []
    for seat, sb in (book.get("seats") or {}).items():
        for pid, pos in (sb.get("positions") or {}).items():
            mark = (marks or {}).get(pos.get("ticker")) or {}
            if pos.get("mark") is not None and mark.get("last") is None:
                mark = dict(mark, last=pos.get("mark"), hi=pos.get("hi"), lo=pos.get("lo"))
            hit = assess(pos, mark, now, stuck_after_sec)
            if hit.get("stuck"):
                hit = dict(hit, seat=seat, id=pid)
                out.append(hit)
    return out
