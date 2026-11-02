"""Funder takeover. Brett can arm, close, tighten a stop, pause a seat, or flatten.

Every trade action is refused unless takeover is on, the signed token matches
the screen on his desk, and the caps (including the kill switch) pass. Flatten
writes the book immediately. Widening a stop is refused. No LLM.
"""
import os

from thf_guardian.caps import firm_new_arms_paused, screen_arm
from thf_guardian.fill_paper import _book_is_paper
from thf_guardian.flatten import flatten_now
from thf_guardian.signing import verify
from thf_guardian.util import atomic_write, read_json


def takeover_on(cfg, now, who="brett"):
    atomic_write(cfg.takeover, dict(on=True, utc=now.isoformat(), who=who, paper_only=True))
    return dict(on=True)


def takeover_off(cfg):
    if os.path.exists(cfg.takeover):
        os.remove(cfg.takeover)
    return dict(on=False)


def is_on(cfg):
    data = read_json(cfg.takeover, None)
    return bool(isinstance(data, dict) and data.get("on"))


def _require(cfg, token, now, screen_rev, secret, used):
    if not is_on(cfg):
        return False, "takeover is off"
    ok, why = verify(secret, token, now, screen_rev, used)
    return ok, why


def move_stop(book, seat, pid, new_stop):
    """Tighten only, same rule as seat_cli.amend. Returns a new book or raises ValueError."""
    import copy
    out = copy.deepcopy(book)
    pos = ((out.get("seats") or {}).get(seat) or {}).get("positions", {}).get(pid)
    if not pos:
        raise ValueError("no such position")
    if pos.get("status") not in ("open", "armed"):
        raise ValueError("not open or armed")
    new_stop = float(new_stop)
    long_ = str(pos.get("side")).upper() == "LONG"
    old = float(pos["stop"])
    if (long_ and new_stop < old) or ((not long_) and new_stop > old):
        raise ValueError("stop may only tighten: %s -> %s widens risk" % (old, new_stop))
    ref = float(pos.get("mark") or pos.get("fill") or pos.get("entry"))
    if pos.get("status") == "open" and ((long_ and new_stop >= ref) or ((not long_) and new_stop <= ref)):
        raise ValueError("stop is through the mark")
    if pos.get("status") == "armed" and ((long_ and new_stop >= float(pos["entry"])) or ((not long_) and new_stop <= float(pos["entry"]))):
        raise ValueError("stop must stay on the loss side of entry")
    pos["stop"] = new_stop
    pos.setdefault("amends", []).append(dict(old_stop=old, stop=new_stop, by="funder_takeover"))
    if pos["stop"] != new_stop:
        raise ValueError("stop was not stored")
    return out


def close_at(book, seat, pid, px, now, cost_aud):
    """Paper close at a price the caller already has. Armed orders are cancelled, not filled."""
    import copy
    if not _book_is_paper(book):
        raise ValueError("not a paper book")
    out = copy.deepcopy(book)
    sb = out["seats"][seat]
    pos = sb["positions"][pid]
    if pos.get("status") == "armed":
        pos.update(status="cancelled", why="funder_takeover", closed_utc=now.isoformat())
        sb.setdefault("closed", []).append(dict(pos))
        del sb["positions"][pid]
        return out, dict(cancelled=pid)
    if pos.get("status") != "open":
        raise ValueError("not open")
    long_ = str(pos.get("side")).upper() == "LONG"
    sg = 1 if long_ else -1
    px = float(px)
    cost = float(cost_aud)
    # Shares and stop are not resized. Cash pays the exit cost only in this paper close;
    # the mark notional is the caller's problem when they pass cost. We also book the
    # signed mark P&L against fill using the same price unit as the mark (AUD tests).
    fill = float(pos.get("fill") or pos["entry"])
    shares = pos["shares"]
    gross = sg * (px - fill) * float(shares)
    sb["cash_aud"] = round(float(sb.get("cash_aud") or 0) + gross - cost, 2)
    pos.update(status="closed", exit=px, exit_reason="funder_takeover", closed_utc=now.isoformat(),
               gross_aud=round(gross, 2), net_aud=round(gross - float(pos.get("cost_aud") or 0) - cost, 2))
    if pos.get("stop") != book["seats"][seat]["positions"][pid]["stop"]:
        raise ValueError("close moved the stop")
    if pos.get("shares") != shares:
        raise ValueError("close resized")
    sb.setdefault("closed", []).append(dict(pos))
    del sb["positions"][pid]
    return out, dict(closed=pid, exit=px)


def act(cfg, book, modes, token, now, screen_rev, secret, used, marks=None, notional_fn=None, cost_fn=None):
    """Dispatch one signed takeover action. `used` is a set of nonces; the nonce is added on success."""
    ok, why = _require(cfg, token, now, screen_rev, secret, used)
    if not ok:
        return dict(ok=False, error=why)
    body = token["body"]
    kind = body.get("kind")
    args = body.get("args") or {}
    ks = (book or {}).get("kill_switch") or {}
    if kind == "flatten":
        try:
            out, rows = flatten_now(book, now, marks or {}, notional_fn, cost_fn)
        except Exception as exc:
            return dict(ok=False, error=str(exc))
        used.add(body["nonce"])
        return dict(ok=True, kind=kind, book=out, rows=rows)
    if kind == "close":
        out, info = close_at(book, args["seat"], args["id"], args["px"], now, args.get("cost_aud") or 0)
        used.add(body["nonce"])
        return dict(ok=True, kind=kind, book=out, **info)
    if kind == "move_stop":
        try:
            out = move_stop(book, args["seat"], args["id"], args["stop"])
        except ValueError as exc:
            return dict(ok=False, error=str(exc))
        used.add(body["nonce"])
        return dict(ok=True, kind=kind, book=out)
    if kind == "arm":
        if ks.get("state") == "FLAT":
            return dict(ok=False, error="kill switch FLAT; no new arms")
        proposal = dict(args.get("proposal") or {}, funder_takeover=True, source="funder_takeover")
        decision = screen_arm(proposal, book, modes, base_pct=args.get("base_pct") or 0.01)
        if not decision["ok"]:
            return dict(ok=False, error="; ".join(decision["reasons"]), decision=decision)
        if os.path.exists(cfg.seat_pause(proposal.get("seat") or "")):
            return dict(ok=False, error="seat is paused")
        used.add(body["nonce"])
        return dict(ok=True, kind=kind, queued=True, proposal=proposal, decision=decision,
                    note="passes the caps; the existing gate still has to pass before book.tick arms it")
    if kind in ("pause_seat", "resume_seat"):
        from thf_guardian.actions import set_seat_pause
        for seat in args.get("seats") or []:
            set_seat_pause(cfg, seat, kind == "pause_seat")
        used.add(body["nonce"])
        return dict(ok=True, kind=kind, seats=args.get("seats"))
    paused, n = firm_new_arms_paused(modes)
    return dict(ok=False, error="unknown takeover kind %s" % kind, firm_paused=paused, degraded_n=n)
