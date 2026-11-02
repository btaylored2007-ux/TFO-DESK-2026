"""Per-seat AI heartbeat.

A seat degrades on the first real-call timeout of a degraded episode and
recovers only after two later real successes. A reachability ping updates the
dashboard and does not change the mode. Open trades and stops are untouched
here; new AI-only arms for a degraded seat are blocked by arm_guard.

The risk gate is code and is not a brain.
"""
import copy

from thf_guardian.seats import AI_SEATS, CODE_SEATS, HUNT_SEATS, SEAT_LABEL
from thf_guardian.util import atomic_write, read_json, wita_str

RECOVER_AFTER = 2


def blank_modes(now):
    modes = {}
    for sid in AI_SEATS:
        modes[sid] = dict(seat=sid, label=SEAT_LABEL[sid], mode="unknown", ok=None, reason="no real call yet",
                          since_utc=now.isoformat(), real_successes=0, rtt_ms=None)
    for sid, _short, _full in CODE_SEATS:
        modes[sid] = dict(seat=sid, label=SEAT_LABEL[sid], mode="code", ok=True,
                          reason="gate_v2 is deterministic code, not a model", since_utc=now.isoformat(), real_successes=0)
    return modes


def note_event(prev, seat, kind, ok, now, reason=""):
    """kind 'ping' never changes mode. kind 'call' is a real seat/brain call.

    A failed or timed-out call degrades the seat immediately and clears the
    recovery counter. A successful call while degraded counts toward recovery.
    The second one returns the seat to ai. A success while the seat is already
    up leaves it up.
    """
    if seat not in AI_SEATS:
        raise KeyError(seat)
    modes = copy.deepcopy(prev or blank_modes(now))
    for sid, _s, _f in CODE_SEATS:
        modes.setdefault(sid, dict(seat=sid, label=SEAT_LABEL[sid], mode="code", ok=True, real_successes=0))
    row = dict(modes.get(seat) or {})
    row.setdefault("seat", seat)
    row["label"] = SEAT_LABEL[seat]
    if kind == "ping":
        row["last_ping_ok"] = bool(ok)
        row["last_ping_utc"] = now.isoformat()
        row["ping_reason"] = reason or ("pong" if ok else "ping failed")
        modes[seat] = row
        return modes
    if kind != "call":
        raise ValueError("kind must be ping or call")
    row["checked_utc"] = now.isoformat()
    if not ok:
        already = row.get("mode") == "degraded"
        row.update(mode="degraded", ok=False, real_successes=0, recovered=False,
                   reason=reason or "timeout",
                   since_utc=row.get("since_utc") if already else now.isoformat())
        modes[seat] = row
        return modes
    if row.get("mode") == "degraded":
        n = int(row.get("real_successes") or 0) + 1
        row["real_successes"] = n
        row["reason"] = reason or "real call ok (%d/%d)" % (n, RECOVER_AFTER)
        if n >= RECOVER_AFTER:
            row.update(mode="ai", ok=True, real_successes=0, recovered=True, since_utc=now.isoformat(),
                       reason="recovered after %d real calls" % RECOVER_AFTER)
        else:
            row.update(mode="degraded", ok=False, recovered=False)
    else:
        row.update(mode="ai", ok=True, real_successes=0, recovered=False,
                   reason=reason or "real call ok",
                   since_utc=row.get("since_utc") or now.isoformat())
    modes[seat] = row
    return modes


def degraded_traders(modes):
    return [sid for sid in HUNT_SEATS if (modes or {}).get(sid, {}).get("mode") == "degraded"]


def ai_arm_blocked(seat, proposal=None, modes=None):
    """LLM-originated arms stop for a degraded seat. A funder takeover arm is not one of those."""
    proposal = proposal or {}
    if proposal.get("funder_takeover") or proposal.get("source") == "funder_takeover":
        return False
    info = (modes or {}).get(seat) or {}
    return info.get("mode") == "degraded"


def filter_ai_arms(recs, modes):
    kept, blocked = [], []
    for rec in recs or []:
        proposal = rec.get("proposal") or {}
        seat = rec.get("seat") or proposal.get("_seat")
        flag = dict(proposal)
        if rec.get("funder_takeover"):
            flag["funder_takeover"] = True
        if ai_arm_blocked(seat, flag, modes):
            blocked.append(dict(id=rec.get("id"), seat=seat, ticker=proposal.get("ticker"),
                                why="seat brain degraded; no new AI-only arm"))
        else:
            kept.append(rec)
    return kept, blocked


def load_modes(path, now):
    data = read_json(path, None)
    if not isinstance(data, dict) or not data.get("seats"):
        return blank_modes(now)
    seats = data["seats"]
    base = blank_modes(now)
    base.update(seats)
    return base


def save_modes(path, modes, now):
    atomic_write(path, dict(updated_utc=now.isoformat(), updated_wita=wita_str(now), seats=modes, paper_only=True,
                            rule="per seat; first real timeout degrades; two real calls recover; pings do not"))
    return modes
