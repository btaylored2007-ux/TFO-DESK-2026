"""Feed status that follows the bars and the entitlement, not the age of a JSON file.

Asia: a rewritten status file with last_bar null is not fresh while a market is open.
Europe: research names rotate inside a symbol cap. eu-quote stays off unless asked.
A 422 means the request was refused and nothing was added. No new data purchase.
"""
import datetime as dt

ASIA_HOLIDAYS = {
    "ASX": {"2026-12-25", "2026-12-28", "2027-01-01", "2027-01-26"},
    "JP": {"2026-12-31", "2027-01-01"},
    "HK": {"2026-12-25"},
}


def asia_session_open(market, now, hours):
    """hours: (tz name, open 'HH:MM', close 'HH:MM'). Weekend and listed holidays are closed."""
    tz_name, open_hm, close_hm = hours
    from zoneinfo import ZoneInfo
    local = now.astimezone(ZoneInfo(tz_name))
    if local.weekday() >= 5 or local.date().isoformat() in ASIA_HOLIDAYS.get(market, ()):
        return False
    start = local.replace(hour=int(open_hm[:2]), minute=int(open_hm[3:]), second=0, microsecond=0)
    end = local.replace(hour=int(close_hm[:2]), minute=int(close_hm[3:]), second=0, microsecond=0)
    return start - dt.timedelta(minutes=5) <= local <= end + dt.timedelta(minutes=20)


def us_rth_open(now):
    """Regular US cash session, America/New_York, weekdays. Premarket is not RTH. Holidays in this set are closed."""
    from zoneinfo import ZoneInfo
    holidays = {"2026-11-26", "2026-12-25", "2027-01-01"}
    local = now.astimezone(ZoneInfo("America/New_York"))
    if local.weekday() >= 5 or local.date().isoformat() in holidays:
        return False
    minute = local.hour * 60 + local.minute
    return 570 <= minute < 960


def asia_data_status(record, now, max_age_s=8 * 3600):
    """session_closed | fresh | stale_underlying. File mtime is ignored."""
    live = list(record.get("markets_in_session") or [])
    if not live:
        return "session_closed"
    last = record.get("last_bar_utc")
    try:
        count = int(record.get("syms_with_bars_8h") or 0)
    except (TypeError, ValueError):
        count = 0
    if not last or count <= 0:
        return "stale_underlying"
    try:
        seen = dt.datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=dt.timezone.utc)
        age = (now - seen.astimezone(dt.timezone.utc)).total_seconds()
    except (TypeError, ValueError):
        return "stale_underlying"
    if age > max_age_s or age < -120:
        return "stale_underlying"
    return "fresh"


def allocate_rest(candidates, held, cap, rotate=0, entitled=None):
    """Pick non-US names to request. Held entitled names first, then a rotation.

    entitled=None means the subscription list was not supplied: the cap still
    applies and entitlement is 'unknown'. Names outside a supplied list are
    not_entitled and are not requested. quote_stream is always false here.
    """
    cap = max(0, int(cap))
    rotate = int(rotate or 0)
    def keep(name):
        name = str(name or "").upper().strip()
        return name or None
    held_names = []
    for name in held or []:
        name = keep(name)
        if name and name not in held_names:
            held_names.append(name)
    research = []
    for name in candidates or []:
        name = keep(name)
        if name and name not in held_names and name not in research:
            research.append(name)
    if entitled is None:
        allowed = None
        not_entitled = []
        entitlement = "unknown"
    else:
        allowed = {str(name).upper() for name in entitled}
        not_entitled = [name for name in held_names + research if name not in allowed]
        held_names = [name for name in held_names if name in allowed]
        research = [name for name in research if name in allowed]
        entitlement = "listed"
    if research:
        shift = rotate % len(research)
        research = research[shift:] + research[:shift]
    chosen = []
    for name in held_names + research:
        if name not in chosen:
            chosen.append(name)
        if len(chosen) >= cap:
            break
    return dict(subscribe=chosen, rotated_out=[name for name in research if name not in chosen],
                not_entitled=not_entitled, entitlement=entitlement, quote_stream=False, cap=cap,
                refused_retry=False)


def eu_quote_word(enabled, quota_room, accepted):
    """eu-quote is off unless explicitly enabled. Off is not 'up'."""
    if not enabled:
        return "off"
    if accepted:
        return "up"
    if quota_room <= 0:
        return "off: quota"
    return "waiting"
