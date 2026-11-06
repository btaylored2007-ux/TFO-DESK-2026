"""Separate feed facts. Keeper status 'ok' is not fresh usable data.

Weekday sessions use the venue timezone. This is not an exchange-holiday calendar.
A missing fact stays unknown. Crypto is expected every day.
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


def session_expected(name, now):
    """True, False, or None. Outside the venue session is closed, not an outage. Crypto is always expected.

    Hours are weekday sessions in the venue timezone. Exchange holidays are not in this calendar,
    so a holiday afternoon stays unknown rather than a claimed open or a claimed outage. This
    function only returns False for the weekend and the known closed hours.
    """
    venue, zone = _zone(name)
    if venue == "CRYPTO":
        return True
    if zone is None or now is None:
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    local = now.astimezone(zone)
    if venue == "FX":
        if local.weekday() == 5:
            return False
        if local.weekday() == 6 and local.hour < 17:
            return False
        if local.weekday() == 4 and local.hour >= 17:
            return False
        return True
    hours = {"US": (9 * 60 + 30, 16 * 60), "EU": (8 * 60, 16 * 60 + 30), "ASIA": (9 * 60, 17 * 60)}
    if venue not in hours:
        return None
    if local.weekday() >= 5:
        return False
    if not _in_hours(local, *hours[venue]):
        return False
    return True


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
    expected = session_expected(name, now)
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
    age = row.get("age_s")
    limit = row.get("max_age_s")
    if expected is False:
        usable, usable_reason = False, "session_closed"
    elif expected is None or age is None or limit is None:
        usable, usable_reason = None, "unknown"
    elif age > limit:
        usable, usable_reason = False, "stale"
    else:
        usable, usable_reason = True, "fresh"
    label = {
        True: "usable",
        False: "session closed" if usable_reason == "session_closed" else "not usable",
        None: "unknown",
    }[usable]
    facts = dict(process_running=running, session_expected=expected, venue=venue,
                 entitlement=entitlement, fresh_usable=usable, usable_reason=usable_reason,
                 last_failure=failure, label=label, calendar="weekday_timezone_not_exchange_holiday")
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
