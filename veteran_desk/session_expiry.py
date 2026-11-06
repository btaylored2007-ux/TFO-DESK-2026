"""tfo_sessexpiry_20261101 (Brett 2026-11-01 07:35 WITA, PAPER ONLY): setup expiry counts OPEN-MARKET time for session venues.
Before: approved funder_queue setups had a fixed 20h wall-clock expiry and book 'armed' orders a fixed 48h wall-clock expiry, so
weekend setups for Monday's opens (ASX/Euronext/LSE/US) died before their market ever opened.
Now:  * 24/7 venues (CRYPTO) and venues book.session_open does not gate (FX/FUT/IDX/...) keep the wall-clock window.
      * Session venues (US, ASX, INTL_SESSIONS) expire once the venue's regular session has been OPEN for the same number of hours
        since the setup was queued/armed (holidays as modelled by book.session_open).
Never re-grades, re-sizes or moves entry/stop/target. Gates still apply at fill (book.py pre-open re-check on fresh bars)."""
import datetime as dt
from functools import lru_cache
UTC = dt.timezone.utc
QUEUE_HOURS = 20          # funder_queue approval window (same length; open-market hours for session venues)
SESSION_ARM_HOURS = 20    # book 'armed' order: open-market hours for session venues (crypto keeps book.ARM_HOURS wall clock)
STEP_MIN = 3              # every modelled session boundary is a multiple of 3 local minutes
MAX_DAYS = 21             # safety cap
WALL_CLOCK_VENUES = {"CRYPTO"}
def _ts(x):
    t = x if isinstance(x, dt.datetime) else dt.datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)
def is_session_venue(venue):
    if not venue or venue in WALL_CLOCK_VENUES: return False
    from veteran_desk import book as BK
    return venue in ("US", "ASX") or venue in BK.INTL_SESSIONS
@lru_cache(maxsize=4096)
def _deadline(venue, t0_iso, hours):
    from veteran_desk import book as BK
    t0 = _ts(t0_iso); need = int(round(hours * 60)); got = 0
    t = t0.replace(second=0, microsecond=0); t -= dt.timedelta(minutes=t.minute % STEP_MIN)
    end = t0 + dt.timedelta(days=MAX_DAYS); step = dt.timedelta(minutes=STEP_MIN)
    while t < end:
        if BK.session_open(venue, t):
            got += STEP_MIN
            if got >= need: return max(t + step, t0)
        t += step
    return end
def deadline(venue, t0, hours):
    """UTC instant when `hours` have elapsed: open-market hours for session venues, wall clock otherwise."""
    t0 = _ts(t0)
    if not is_session_venue(venue): return t0 + dt.timedelta(hours=hours)
    return _deadline(venue, t0.astimezone(UTC).isoformat(), float(hours))
def next_open(venue, t0):
    """First instant at/after t0 when the venue session is open (None for wall-clock venues)."""
    if not is_session_venue(venue): return None
    from veteran_desk import book as BK
    t = _ts(t0).replace(second=0, microsecond=0); end = t + dt.timedelta(days=MAX_DAYS)
    t -= dt.timedelta(minutes=t.minute % STEP_MIN)
    while t < end:
        if BK.session_open(venue, t): return t
        t += dt.timedelta(minutes=STEP_MIN)
    return None
def queue_expiry(venue, t0, hours=QUEUE_HOURS): return deadline(venue, t0, hours).isoformat()
def arm_expired(p, t, arm_hours_wall):
    """book.py armed-order expiry. Session venues: SESSION_ARM_HOURS open-market since armed_utc; others: arm_hours_wall wall clock."""
    v = p.get("venue"); a = _ts(p["armed_utc"])
    if is_session_venue(v): return _ts(t) >= deadline(v, a, SESSION_ARM_HOURS)
    return (_ts(t) - a).total_seconds() > arm_hours_wall * 3600
def arm_expiry_utc(p, arm_hours_wall):
    v = p.get("venue"); a = _ts(p["armed_utc"])
    return (deadline(v, a, SESSION_ARM_HOURS) if is_session_venue(v) else a + dt.timedelta(hours=arm_hours_wall)).isoformat()
def needs_preopen_recheck(p): return bool(p.get("queued_for_open")) and not p.get("preopen_recheck_utc") and is_session_venue(p.get("venue"))
def preopen_recheck(p, m):
    """First fillable in-session tick of an order armed while its venue was closed: desk.live_check on the fresh bar.
    None = still valid; else the reason (caller cancels). Never resizes or moves levels. Fails open on errors (book pre-trade still applies)."""
    try:
        from veteran_desk import desk as DK
        return DK.live_check(p, lambda _s: m)
    except Exception: return None
