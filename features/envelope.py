"""One observation. Missing data is flagged and left empty — never filled."""
FRESH = "fresh"
STALE = "stale"


def field(value, as_of, *, source, missing=False, fresh=True, reason=None, applicable=True):
    """Build a feature field.

    value of 0 or False is a real observation. missing=True forces value to None
    and freshness to stale. applicable=False is a known out-of-scope field, not a gap.
    """
    if not applicable:
        return {
            "value": None,
            "as_of": as_of,
            "freshness": FRESH,
            "missing": False,
            "applicable": False,
            "source": source,
            "reason": reason or "not_applicable",
        }
    if missing:
        return {
            "value": None,
            "as_of": as_of,
            "freshness": STALE,
            "missing": True,
            "applicable": True,
            "source": source,
            "reason": reason or "missing",
        }
    return {
        "value": value,
        "as_of": as_of,
        "freshness": FRESH if fresh else STALE,
        "missing": False,
        "applicable": True,
        "source": source,
        "reason": reason,
    }


def from_hit(hit, source):
    """Map a transport Hit onto a field. A cached value stays stale — it is not a fill."""
    if hit is None or hit.missing:
        return field(
            None,
            None if hit is None else hit.as_of,
            source=source,
            missing=True,
            reason=(None if hit is None else hit.reason) or "no response",
        )
    return field(hit.value, hit.as_of, source=source, missing=False, fresh=bool(hit.fresh), reason=hit.reason)
