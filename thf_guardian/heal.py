"""Restart backoff. Never opens the book. At most heal_max tries per window, then escalate."""


def decide(attempts, now_ts, backoff=(60, 180, 600), max_tries=3, window=1800):
    """attempts are unix timestamps. Returns ('restart'|'wait'|'escalate', recent)."""
    recent = [a for a in attempts if now_ts - a < window]
    if len(recent) >= max_tries:
        return "escalate", recent
    if recent:
        wait = backoff[min(len(recent) - 1, len(backoff) - 1)]
        if now_ts - recent[-1] < wait:
            return "wait", recent
    return "restart", recent


def plan(state, unit, now_ts, backoff=(60, 180, 600), max_tries=3, window=1800, deadman_recent=False):
    """One unit. deadman_recent defers an engine restart the deadman just did."""
    if deadman_recent and unit == "tfo-thf-engine.service":
        return dict(unit=unit, action="wait", why="deadman restarted the engine recently")
    attempts = list((state.get("units") or {}).get(unit) or [])
    action, recent = decide(attempts, now_ts, backoff, max_tries, window)
    return dict(unit=unit, action=action, attempts=len(recent), why=action)


def record(state, unit, now_ts, window=1800):
    units = state.setdefault("units", {})
    prev = [a for a in units.get(unit) or [] if now_ts - a < window]
    prev.append(now_ts)
    units[unit] = prev
    return state
