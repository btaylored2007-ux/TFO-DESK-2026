"""Hunt research is complete only with a checked candidate. It does not arm by itself.

A bare stand_aside does not finish the hunt. Grade A is rejected when net R is
below 1.5 or the quote is missing or stale. Legacy proposals with no
research_candidate still follow the existing risk gate. No ledger write.
"""
import datetime as dt
import json
import math
import os

VERSION = "research_mandate_20261106"
TRADERS = ("banks", "energy", "essentials_utilities", "healthcare", "news_filings", "price_action",
           "tech_ai_infra", "trend", "macro", "fx_commodities", "crypto")
MIN_NET_R_A = 1.5
MAX_AGE_S = 20 * 60


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _net_r(side, entry, stop, target, fee_bps, slip_bps):
    risk = abs(entry - stop)
    if risk <= 0 or entry <= 0:
        return None
    if side == "LONG" and not (stop < entry < target):
        return None
    if side == "SHORT" and not (target < entry < stop):
        return None
    gross = abs(target - entry) / risk
    cost = ((fee_bps + slip_bps) / 10000.0) * entry * 2.0 / risk
    return gross - cost


def assess(reply, now=None, seat=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    if isinstance(now, str):
        now = dt.datetime.fromisoformat(now.replace("Z", "+00:00"))
    reply = reply if isinstance(reply, dict) else {}
    action = reply.get("action")
    cand = reply.get("research_candidate")
    out = dict(version=VERSION, seat=seat, action=action, research_complete=False,
               hunt_satisfied=False, execution_eligible=False, shadow_only=False,
               legitimate_no_trade=action == "stand_aside", grade_claimed=None,
               grade_accepted=None, net_r=None, blocked_by=None, next_action=None)
    if not isinstance(cand, dict):
        out.update(blocked_by="bare_stand_aside" if action == "stand_aside" else "no_candidate",
                   next_action="return one research_candidate or a no_quote record; do not invent prices")
        return out
    out["grade_claimed"] = cand.get("grade")
    if cand.get("blocked_by") == "no_quote" or any(cand.get(k) is None for k in ("entry", "stop", "target")):
        out.update(blocked_by="no_quote", next_action="retry when a sourced quote exists; do not fabricate",
                   research_complete=False)
        return out
    side = cand.get("side")
    entry, stop, target = _num(cand.get("entry")), _num(cand.get("stop")), _num(cand.get("target"))
    fee, slip = _num(cand.get("fee_bps")), _num(cand.get("slippage_bps"))
    if side not in ("LONG", "SHORT") or None in (entry, stop, target, fee, slip):
        out.update(blocked_by="invalid_candidate", next_action="supply side, prices, fee_bps and slippage_bps")
        return out
    net = _net_r(side, entry, stop, target, fee, slip)
    out["net_r"] = None if net is None else round(net, 4)
    asof = cand.get("asof")
    fresh = False
    if asof:
        try:
            seen = dt.datetime.fromisoformat(str(asof).replace("Z", "+00:00"))
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=dt.timezone.utc)
            age = (now - seen).total_seconds() if hasattr(now, "tzinfo") and now.tzinfo else None
            fresh = age is not None and -120 <= age <= MAX_AGE_S
        except (TypeError, ValueError):
            fresh = False
    structural = bool(cand.get("ticker") and cand.get("source") and cand.get("catalyst")
                      and cand.get("disconfirming") and cand.get("test_evidence") is not None
                      and cand.get("duplicate_exposure") is not None and net is not None)
    out["research_complete"] = structural
    claimed = cand.get("grade")
    accepted = None
    if structural and fresh and net >= MIN_NET_R_A and claimed in ("A", "A+"):
        if claimed == "A+" and net < 2.0:
            accepted = "A"
        else:
            accepted = claimed
    elif structural and net is not None and net < MIN_NET_R_A and claimed in ("A", "A+"):
        out["mislabel"] = True
        out["blocked_by"] = "net_r_below_quality"
    elif not fresh and claimed in ("A", "A+"):
        out["mislabel"] = True
        out["blocked_by"] = "stale_or_missing_asof"
    out["grade_accepted"] = accepted
    out["hunt_satisfied"] = accepted in ("A", "A+")
    out["execution_eligible"] = False  # the existing risk gate still has to pass; this module does not arm
    out["shadow_only"] = structural and accepted not in ("A", "A+")
    if out["blocked_by"] is None and not out["hunt_satisfied"]:
        out["blocked_by"] = "not_quality_a"
        out["next_action"] = "report the blocker; do not relabel a B or a 0.19R idea as A"
    return out


def public_view(result):
    return {k: v for k, v in result.items() if k != "raw"}


def arm_withheld(proposal, now=None):
    """None keeps the existing gate. A bad research_candidate withholds the arm."""
    if not isinstance(proposal, dict) or not isinstance(proposal.get("research_candidate"), dict):
        return None
    view = assess(proposal, now=now)
    if view.get("grade_accepted") in ("A", "A+") and view.get("research_complete"):
        return None
    return view.get("blocked_by") or "research_not_executable"


def record_round(seat, reply, asof=None, source="thf_round.admit", path=None):
    """Persist one production proposal check. Empty output is not a trade and is not a finished hunt."""
    payload = reply if isinstance(reply, dict) else {"empty_output": True, "action": None}
    view = assess(payload, now=asof, seat=seat)
    if payload.get("empty_output") or payload.get("action") is None and not payload.get("research_candidate"):
        view["empty_output"] = True
        view["hunt_satisfied"] = False
        view["execution_eligible"] = False
        view["forced_trade"] = False
        if view.get("blocked_by") in (None, "no_candidate"):
            view["blocked_by"] = "empty_output"
            view["next_action"] = "zero model output is valid; do not invent a trade"
    view["forced_trade"] = False
    if path is None and os.environ.get("PYTEST_CURRENT_TEST"):
        return view
    if path is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "state", "thf", "research_mandate.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    row = dict(view, source=source, asof=asof if isinstance(asof, str) else getattr(asof, "isoformat", lambda: None)())
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return view


def observe(seat, reply, asof=None, source="desk.run", path=None):
    if os.environ.get("PYTEST_CURRENT_TEST") and path is None:
        return None
    view = assess(reply, now=asof, seat=seat)
    if path is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "state", "thf", "research_mandate.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    row = dict(view, source=source, asof=asof)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return row
