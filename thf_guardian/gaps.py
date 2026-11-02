"""Stale-price flags and the first fresh print after a stale stretch.

The flag is a report. It does not move a stop and it does not fill. A gap
through the stop on that first fresh print is logged so the book's own
gap-stop rule can run on the next tick.
"""
from thf_guardian.config import VENUE_FLOOR_MIN
from thf_guardian.feeds import venue_limit_min


def limit_min(venue):
    if venue in VENUE_FLOOR_MIN or venue:
        return venue_limit_min(venue or "US")
    return venue_limit_min("US")


def flag_stale(positions, now_unused=None):
    """positions: [{id, seat, ticker, venue, status, mark_age_min, stop, side}]."""
    out = []
    for p in positions or []:
        if p.get("status") not in ("open", "armed"):
            continue
        age = p.get("mark_age_min")
        limit = limit_min(p.get("venue") or "")
        if age is None or age > limit:
            out.append(dict(id=p.get("id"), seat=p.get("seat"), ticker=p.get("ticker"), venue=p.get("venue"),
                            status=p.get("status"), mark_age_min=age, limit_min=limit, flag="stale_price"))
    return out


def gap_on_first_fresh(pos, previous_stale, fresh_px):
    """previous_stale True means the last mark was past the venue limit.

    Returns a flag dict or None. Does not change pos.
    """
    if not previous_stale or fresh_px is None or pos.get("status") != "open":
        return None
    try:
        px = float(fresh_px)
        stop = float(pos["stop"])
    except (TypeError, ValueError, KeyError):
        return None
    long_ = str(pos.get("side")).upper() == "LONG"
    through = (long_ and px < stop) or ((not long_) and px > stop)
    return dict(id=pos.get("id"), seat=pos.get("seat"), ticker=pos.get("ticker"), flag="gap_on_first_fresh",
                fresh=px, stop=stop, through_stop=through, side=pos.get("side"),
                detail="first fresh print %s stop %s; stop left unchanged" % (px, stop))
