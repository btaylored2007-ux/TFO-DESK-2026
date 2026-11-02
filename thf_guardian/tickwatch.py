"""Watch the book tick, and watch the guardian's own timers.

A tick restart is refused when the book fails an integrity check or a startup
lock is still held by a live tick. A stale mkdir lock is cleared first. The
guardian timer is a separate check so a stuck guardian loop is visible even
to the oneshot timer.
"""
OPENISH = {"open", "armed"}


def book_integrity(book):
    problems = []
    if not isinstance(book, dict) or not isinstance(book.get("seats"), dict) or not book["seats"]:
        return dict(ok=False, problems=["book missing seats"])
    if book.get("paper_only") is False or book.get("live") is True:
        problems.append("book is not marked paper")
    for seat, sb in book["seats"].items():
        if not isinstance(sb, dict):
            problems.append("%s seat is not an object" % seat)
            continue
        for pid, pos in (sb.get("positions") or {}).items():
            if not isinstance(pos, dict):
                problems.append("%s %s not an object" % (seat, pid))
                continue
            if pos.get("status") not in ("open", "armed", "closed", "expired", "cancelled"):
                problems.append("%s %s bad status %s" % (seat, pid, pos.get("status")))
            if pos.get("status") in OPENISH:
                if pos.get("stop") is None:
                    problems.append("%s %s open/armed without a stop" % (seat, pid))
                if pos.get("shares") in (None, ""):
                    problems.append("%s %s missing shares" % (seat, pid))
            if pos.get("status") == "open" and pos.get("fill") is None:
                problems.append("%s %s open without a fill" % (seat, pid))
    return dict(ok=not problems, problems=problems)


def prepare_tick_restart(integrity, locks, stale_sec=720):
    """integrity is book_integrity(). locks are diagnose_locks inputs ({kind, held, age_sec, path})."""
    if not integrity.get("ok"):
        return dict(restart=False, clear=[], why="book integrity failed; tick not restarted", problems=integrity.get("problems"))
    fresh = [lk for lk in locks or [] if lk.get("held") and lk.get("age_sec") is not None and lk["age_sec"] < stale_sec]
    if fresh:
        return dict(restart=False, clear=[], why="startup lock still held", locks=[lk.get("path") for lk in fresh])
    clear = [lk for lk in locks or [] if lk.get("kind") == "mkdir" and lk.get("held") and (lk.get("age_sec") or 0) >= stale_sec]
    return dict(restart=True, clear=clear, why="book ok; stale mkdir locks cleared; tick may start")


def watch_guardian(status_age_sec, interval_sec, timers):
    """timers: {unit: state}. The oneshot check timer is the watchdog over the daemon."""
    actions = []
    rows = []
    for unit in ("tfo-thf-guardian.timer", "tfo-thf-guardian-check.timer"):
        st = (timers or {}).get(unit)
        ok = st == "active"
        rows.append(dict(unit=unit, state=st or "unknown", ok=ok))
        if not ok:
            actions.append(dict(kind="start", unit=unit, why="guardian timer not active"))
    limit = 3 * int(interval_sec)
    stale = status_age_sec is None or status_age_sec > limit
    if stale:
        actions.append(dict(kind="escalate", why="guardian status is %s sec old (limit %s); daemon loop looks stuck" % (status_age_sec, limit)))
    return dict(ok=not actions, rows=rows, actions=actions, status_age_sec=status_age_sec, limit_sec=limit)
