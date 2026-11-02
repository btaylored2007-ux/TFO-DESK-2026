"""Tick lag and the cleared-but-not-armed queue.

Auto-clear does two things only:
  * re-rank today's cleared names ahead of stale rows (order only; no field that
    sizes, stops or grades a trade is changed, and nothing is deleted);
  * fill-at-entry for an armed grade A/A+ whose trigger has traded through.

A gate-threshold mismatch is logged. The gate file is not rewritten.
"""
from thf_guardian.config import APPROVED_GATE
from thf_guardian.util import parse_dt, wita_day

CLEARED_STATES = {"awaiting_funder", "cleared", "approved", "approve"}


def _is_cleared(entry):
    state = str(entry.get("state") or "")
    decision = str(entry.get("decision") or "")
    if entry.get("gate_passed") is True or (entry.get("gate") or {}).get("passed") is True:
        return True
    if decision == "approve" and state in CLEARED_STATES | {"armed"}:
        return state != "armed"
    return state in {"cleared", "approved"}


def _day(entry, now):
    for key in ("queued_utc", "decided_utc", "utc"):
        t = parse_dt(entry.get(key))
        if t:
            return wita_day(t)
    return None


def compare_gate(observed, approved=None):
    """Return a list of mismatches. Empty if observed is missing (unreadable, not a false pass)."""
    approved = approved or APPROVED_GATE
    if not observed:
        return [dict(key="*", approved=None, observed=None, why="gate thresholds unreadable")]
    bad = []
    for key, want in approved.items():
        got = observed.get(key)
        if isinstance(want, float):
            try:
                if abs(float(got) - want) > 1e-9:
                    bad.append(dict(key=key, approved=want, observed=got))
            except (TypeError, ValueError):
                bad.append(dict(key=key, approved=want, observed=got))
        elif list(got or []) != list(want) if isinstance(want, list) else got != want:
            bad.append(dict(key=key, approved=want, observed=got))
    return bad


def rerank(entries, now):
    """Return (new_entries_dict, moved_ids). Today's cleared rows first. Values unchanged."""
    today = wita_day(now)
    items = list(entries.items()) if isinstance(entries, dict) else list(enumerate(entries))
    today_cleared, rest = [], []
    for key, entry in items:
        day = _day(entry, now)
        if day == today and _is_cleared(entry):
            today_cleared.append((key, entry))
        else:
            rest.append((key, entry))
    # Only an actual reorder counts. If today-cleared were already a prefix, do nothing.
    moved = []
    if today_cleared and rest:
        first_rest = len(today_cleared)
        # Were any today-cleared items sitting after a stale row?
        positions = {id(entry): i for i, (_k, entry) in enumerate(items)}
        stale_before = False
        seen_rest = False
        for _k, entry in items:
            if (_k, entry) in today_cleared or any(entry is e for _kk, e in today_cleared):
                if seen_rest:
                    stale_before = True
                    moved.append(entry.get("id") or _k)
            else:
                seen_rest = True
        if not stale_before:
            return (entries if isinstance(entries, dict) else entries), []
    ordered = today_cleared + rest
    if isinstance(entries, dict):
        return {k: e for k, e in ordered}, moved
    return [e for _k, e in ordered], moved


def diagnose(snap, now):
    """snap: book_age_sec, tick_paused, queue (dict of entries), gate (observed TH), stuck (list)."""
    incidents = []
    actions = []
    if not snap.get("tick_paused"):
        age = snap.get("book_age_sec")
        limit = snap.get("tick_lag_sec", 420)
        if age is not None and age > limit:
            incidents.append(dict(kind="tick_lag", age_sec=age, limit=limit,
                                  detail="book tick older than %ss" % limit))
            actions.append(dict(kind="start_tick", unit="tfo-thf-tick.service"))
    queue = snap.get("queue") or {}
    entries = queue.get("entries") if isinstance(queue, dict) and "entries" in queue else (queue or {})
    if not isinstance(entries, dict):
        entries = {str(i): e for i, e in enumerate(entries)}
    cleared = []
    for key, entry in entries.items():
        if not _is_cleared(entry):
            continue
        t = parse_dt(entry.get("queued_utc") or entry.get("decided_utc"))
        age = (now - t).total_seconds() if t else None
        cleared.append(dict(id=entry.get("id") or key, ticker=entry.get("ticker"), seat=entry.get("seat") or (entry.get("tags") or {}).get("seat"),
                            age_sec=age, day=_day(entry, now), grade=entry.get("grade")))
    waiting = [c for c in cleared if c["age_sec"] is None or c["age_sec"] > snap.get("cleared_wait_sec", 600)]
    if waiting:
        incidents.append(dict(kind="cleared_never_armed", n=len(waiting), ids=[c["id"] for c in waiting[:20]],
                              detail="%d cleared setup(s) not armed" % len(waiting)))
    if len(entries) > snap.get("queue_overflow", 40):
        incidents.append(dict(kind="waiting_list_overflow", n=len(entries), cap=snap.get("queue_overflow", 40),
                              detail="queue holds %d rows" % len(entries)))
    new_entries, moved = rerank(entries, now)
    if moved:
        incidents.append(dict(kind="stale_rows_crowding", moved=moved,
                              detail="re-ranked %d today's cleared name(s) ahead of stale rows" % len(moved)))
        actions.append(dict(kind="rerank_queue", entries=new_entries, moved=moved))
    mismatches = compare_gate(snap.get("gate"))
    if mismatches and not (len(mismatches) == 1 and mismatches[0]["key"] == "*" and snap.get("gate") is None):
        if snap.get("gate"):
            incidents.append(dict(kind="wrong_gate_thresholds", mismatches=mismatches,
                                  detail="gate thresholds differ from approved; not rewritten",
                                  action="alert_only"))
    stuck = snap.get("stuck") or []
    fills = []
    for s in stuck:
        if s.get("grade") in ("A", "A+") and s.get("reason") in ("trigger_through", "stale_price_delayed_venue"):
            fills.append(dict(kind="fill_at_entry", seat=s["seat"], id=s["id"], ticker=s.get("ticker"),
                              entry=s.get("entry"), stop=s.get("stop"), shares=s.get("shares"), grade=s.get("grade"),
                              why=s.get("reason")))
    if fills:
        incidents.append(dict(kind="armed_through_entry", n=len(fills),
                              ids=[f["id"] for f in fills],
                              detail="armed A through entry; fill-at-entry fallback (same size and stop)"))
        actions.extend(fills)
    return dict(incidents=incidents, actions=actions, cleared=cleared,
                rule="reorder and fill-at-entry only; never loosen risk rails; never rewrite gate thresholds")
