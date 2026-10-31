"""Shared pre-trade call. PAPER ONLY.

Every desk's new-entry path calls ``pretrade`` (or ``blocked``) before it opens
risk. The firm-risk service is the one implementation: heat 15%, US beta 60%
of book equity, single-name 40%, A 1% (1.5% when the 40% cap binds), non-A 2%
floor, OANDA 2% and at most 4 open, a stop on every new trade. A seat's own
lower rail is not lifted. A down or stale service refuses new risk. Exits and
stops always pass. Decisions append to state/firm_risk_decisions.jsonl.

This module does not write a ledger and does not change a size.
"""
from veteran_desk.exec_speed import firm_risk_service as _R


def pretrade(proposal, **kw):
    """Return the service decision. Never raises: a failure refuses new risk."""
    try:
        return _R.pretrade(proposal, **kw)
    except Exception as exc:
        decision = {
            "ok": False, "exits_allowed": True, "paper_only": True,
            "reason": "firm_risk_service_unreachable", "detail": type(exc).__name__,
        }
        try:
            _R._log_decision(proposal if isinstance(proposal, dict) else {}, decision)
        except Exception:
            pass
        return decision


def blocked(proposal, **kw):
    """None when the new trade may open. Otherwise the refuse reason."""
    decision = pretrade(proposal, **kw)
    if decision.get("ok"):
        return None
    return str(decision.get("reason") or "firm_risk")


def hunt_proposal(trade, book, venue="US", equity_aud=1_000_000.0, risk_aud=2500.0):
    """Proposal for a hunt arm or fill. The book's own A$2,500 rail is the ceiling."""
    px = trade.get("max_fill", trade.get("entry", trade.get("trigger")))
    try:
        px_f = float(px or 0)
    except (TypeError, ValueError):
        px_f = 0.0
    try:
        shares = float(trade.get("shares") or 0)
    except (TypeError, ValueError):
        shares = 0.0
    side = "LONG" if str(trade.get("direction") or "short").lower() == "long" else "SHORT"
    try:
        ra = float(trade.get("risk_aud") or trade.get("risk_aud_actual") or trade.get("risk_cap_aud") or risk_aud)
    except (TypeError, ValueError):
        ra = float(risk_aud)
    try:
        eq = float(equity_aud)
    except (TypeError, ValueError):
        eq = 0.0
    pct = (ra / eq) if eq else 0.0
    prop = {
        "desk": "hunt", "seat": book, "ticker": trade.get("ticker") or trade.get("sym"),
        "side": side, "venue": venue, "grade": "B", "action": "entry",
        "risk_pct": pct, "risk_aud": ra, "notional_aud": abs(px_f * shares),
        "seat_equity_aud": eq, "stop": trade.get("stop"), "entry": px_f or None,
        "seat_risk_min": 0.0, "seat_risk_max": pct,
    }
    if str(venue).upper() == "US":
        sign = 1.0 if side == "LONG" else -1.0
        prop["us_beta_usd"] = sign * px_f * shares
    return prop
