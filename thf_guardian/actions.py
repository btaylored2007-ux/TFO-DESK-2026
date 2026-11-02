"""Whitelist of message-box actions. Anything else is a head-trader queue item.

Restart names map to a fixed unit list. There is no shell and no broker.
"""
import os, re

from thf_guardian.config import RESTARTABLE
from thf_guardian.seats import HUNT_SEATS, resolve_seats

_RESTART = re.compile(r"\brestart\b")
_HEALTH = re.compile(r"health check|re-?run health|rerun health")
_PAUSE_ENG = re.compile(r"\bpause (the )?engine\b|\bkill switch on\b")
_RESUME_ENG = re.compile(r"\bresume (the )?engine\b|\bunkill\b")
_FLATTEN = re.compile(r"\bflatten\b|\bkill and flatten\b|\bkill switch flat\b")
_FILL = re.compile(r"\bfill\b")
_PUSH = re.compile(r"\bpush\b")
_TAKE = re.compile(r"\btake over\b|\btakeover\b|\btake control\b")
_HAND = re.compile(r"\bhand back\b|\brelease control\b|\bend takeover\b")
_PAUSE_SEAT = re.compile(r"\bpause\b")
_RESUME_SEAT = re.compile(r"\bresume\b")
_ARM = re.compile(r"\barm\b")
_CLOSE = re.compile(r"\bclose\b")
_STOP = re.compile(r"\bmove (the )?stop\b|\btighten\b")


def parse(text):
    raw = text or ""
    low = raw.lower()
    if _FLATTEN.search(low):
        return dict(kind="flatten", needs_confirm=True)
    if _PAUSE_ENG.search(low):
        return dict(kind="pause_engine", needs_confirm=True)
    if _RESUME_ENG.search(low):
        return dict(kind="resume_engine", needs_confirm=True)
    if _HEALTH.search(low):
        return dict(kind="health", needs_confirm=True)
    if _RESTART.search(low):
        name = None
        for key in sorted(RESTARTABLE, key=len, reverse=True):
            if key in low:
                name = key
                break
        if name is None:
            return dict(kind="reject", why="restart target is not a THF unit")
        unit, how = RESTARTABLE[name]
        return dict(kind="restart", unit=unit, how=how, name=name, needs_confirm=True)
    if _FILL.search(low):
        m = re.search(r"\bfill\s+(\S+)", low)
        return dict(kind="fill", id=(m.group(1) if m else None), needs_confirm=True)
    if _PUSH.search(low) or "hunt" in low:
        seats, _ = resolve_seats(low)
        if not seats:
            seats = list(HUNT_SEATS)
        return dict(kind="push", seats=seats, needs_confirm=True, text=raw)
    if _TAKE.search(low):
        return dict(kind="takeover_on", needs_confirm=True)
    if _HAND.search(low):
        return dict(kind="takeover_off", needs_confirm=True)
    if _PAUSE_SEAT.search(low) or _RESUME_SEAT.search(low):
        seats, _ = resolve_seats(low)
        if not seats:
            return dict(kind="queue", why="name the seat to pause or resume")
        return dict(kind="resume_seat" if _RESUME_SEAT.search(low) and not _PAUSE_SEAT.search(low) else "pause_seat",
                    seats=seats, needs_confirm=True)
    if _ARM.search(low) or _CLOSE.search(low) or _STOP.search(low):
        return dict(kind="takeover_trade", needs_confirm=True, text=raw, why="trade change")
    return dict(kind="queue", why="not on the whitelist")


def pause_engine(cfg, now, reason):
    os.makedirs(os.path.dirname(cfg.engine_kill), exist_ok=True)
    with open(cfg.engine_kill, "w", encoding="utf-8") as fh:
        fh.write('{"by":"thf_guardian","utc":"%s","reason":"%s","paper_only":true}\n' % (now.isoformat(), reason.replace('"', "'")))
    return dict(ok=True, path=cfg.engine_kill)


def resume_engine(cfg):
    if os.path.exists(cfg.engine_kill):
        os.remove(cfg.engine_kill)
    return dict(ok=True, removed=True)


def set_seat_pause(cfg, seat, paused):
    path = cfg.seat_pause(seat)
    if paused:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("paused\n")
    elif os.path.exists(path):
        os.remove(path)
    return dict(ok=True, seat=seat, paused=paused)
