"""Separate feed facts. Keeper status 'ok' is not fresh usable data.

session_expected stays unknown unless a full venue calendar is available.
heuristic_session_expected is the weekday clock plus the small holiday lists already
in feed_health. It is not calendar proof. Crypto is continuous.
Freshness uses an event timestamp. File mtime is not freshness.
"""
import datetime as dt
from zoneinfo import ZoneInfo

CRYPTO = {"kraken_ticker", "kraken_tape", "thf_wstape", "coingecko", "kraken_sweep", "thf_newsbot"}
US = {"yahoo_quotes", "us_stream_fmp", "edgar_8k", "news_rss", "announcements", "intraday_pull", "earnings_cache", "news_desk"}
EU = {"eodhd_eu"}
ASIA = {"asia_intraday", "paid_bars"}
FX = {"audusd", "pit_fx_daily"}
ZONES = {"US": "America/New_York", "EU": "Europe/London", "ASIA": "Asia/Singapore", "FX": "America/New_York"}


def _zone(name):
    key = name.split(":", 1)[0]
    if key in CRYPTO:
        return "CRYPTO", None
    if key in US:
        return "US", ZoneInfo(ZONES["US"])
    if key in EU:
        return "EU", ZoneInfo(ZONES["EU"])
    if key in ASIA:
        return "ASIA", ZoneInfo(ZONES["ASIA"])
    if key in FX or key.startswith("oanda_practice"):
        return "FX", ZoneInfo(ZONES["FX"])
    return "unknown", None


def _in_hours(local, start, end):
    if local.weekday() >= 5:
        return False
    minutes = local.hour * 60 + local.minute
    return start <= minutes < end


def heuristic_session_expected(name, now):
    """Weekday hours, DST via zoneinfo, and the partial holiday lists in feed_health.

    This is not a venue calendar. Lunch breaks, half-days, and unlisted holidays are not covered.
    """
    venue, zone = _zone(name)
    if venue == "CRYPTO":
        return True
    if now is None:
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    if venue == "US":
        from veteran_desk.feed_health import us_rth_open
        return us_rth_open(now)
    if venue == "ASIA":
        from veteran_desk.feed_health import asia_session_open
        market = "JP" if name == "paid_bars" else "ASX"
        hours = ("Asia/Tokyo", "09:00", "15:00") if market == "JP" else ("Australia/Sydney", "10:00", "16:12")
        return asia_session_open(market, now, hours)
    if zone is None:
        return None
    local = now.astimezone(zone)
    if venue == "FX":
        if local.weekday() == 5 or (local.weekday() == 6 and local.hour < 17) or (local.weekday() == 4 and local.hour >= 17):
            return False
        return True
    if local.weekday() >= 5:
        return False
    hours = {"EU": (8 * 60, 16 * 60 + 30)}
    if venue not in hours:
        return None
    return _in_hours(local, *hours[venue])


def _event_time(row):
    coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    last = row.get("last_status") if isinstance(row.get("last_status"), dict) else {}
    for value in (row.get("event_utc"), row.get("last_msg_utc"), row.get("last_bar_utc"), coverage.get("last_bar"), last.get("utc")):
        if not value:
            continue
        try:
            stamp = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=dt.timezone.utc)
        return stamp.astimezone(dt.timezone.utc)
    return None


def _failure(row):
    last = row.get("last_status") if isinstance(row.get("last_status"), dict) else {}
    code = last.get("code") or row.get("last_error_code")
    text = " ".join(str(row.get(key) or "") for key in ("last_error", "detail"))
    if code:
        return dict(code=code, message=last.get("message") or row.get("last_error") or "")
    if "422" in text:
        return dict(code=422, message=text[:180])
    if row.get("last_error"):
        return dict(code=None, message=str(row.get("last_error"))[:180])
    return None


def classify(name, row, now=None):
    """One feed row. Does not remove the keeper status field."""
    row = dict(row or {})
    now = now or dt.datetime.now(dt.timezone.utc)
    venue, _zone_obj = _zone(name)
    heuristic = True if venue == "CRYPTO" else heuristic_session_expected(name, now)
    if row.get("process_state") in ("running", "active"):
        running = True
    elif row.get("process_state") in ("inactive", "failed", "dead"):
        running = False
    else:
        running = None
    if row.get("authorized") is True:
        entitlement = "authorized"
    elif row.get("authorized") is False:
        entitlement = "denied"
    else:
        entitlement = "unknown"
    failure = _failure(row)
    event = _event_time(row)
    limit = row.get("max_age_s")
    if heuristic is False:
        usable, usable_reason = False, "heuristic_session_closed"
    elif event is None or limit is None or now is None:
        usable, usable_reason = None, "unknown_no_event_timestamp"
    else:
        when = now if now.tzinfo else now.replace(tzinfo=dt.timezone.utc)
        age = (when - event).total_seconds()
        if age > limit or age < -120:
            usable, usable_reason = False, "stale_event"
        else:
            usable, usable_reason = True, "event_timestamp"
    current = row.get("healthy")
    if current is None:
        current = row.get("status")
    label = "usable" if usable is True else ("heuristic session closed, not a venue calendar" if usable_reason == "heuristic_session_closed" else "unknown" if usable is None else "not usable")
    facts = dict(process_running=running, session_expected=None if venue != "CRYPTO" else True,
                 heuristic_session_expected=heuristic, venue=venue,
                 entitlement=entitlement, fresh_usable=usable, usable_reason=usable_reason,
                 freshness_source="event_timestamp" if event is not None else "unknown_mtime_ignored",
                 last_failure=failure, current_health=current, label=label,
                 calendar="continuous_24_7" if venue == "CRYPTO" else "heuristic_not_venue_calendar")
    row["facts"] = facts
    return row


def annotate(feeds, now=None):
    for name, row in list((feeds or {}).items()):
        if isinstance(row, dict):
            feeds[name] = classify(name, row, now)
    return feeds


def summarize(feeds, now=None):
    rows = []
    for name, row in (feeds or {}).items():
        if isinstance(row, dict):
            rows.append(classify(name, row, now))
    usable = sum(1 for row in rows if (row.get("facts") or {}).get("fresh_usable") is True)
    return dict(keeper_status={status: sum(1 for row in rows if row.get("status") == status)
                               for status in {row.get("status") for row in rows}},
                usable=usable, not_usable=sum(1 for row in rows if (row.get("facts") or {}).get("fresh_usable") is False),
                usable_unknown=sum(1 for row in rows if (row.get("facts") or {}).get("fresh_usable") is None),
                ok_is_not_usable=True,
                note="A keeper status of ok is not a count of fresh usable data.")
