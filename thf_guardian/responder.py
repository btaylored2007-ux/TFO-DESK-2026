"""Message-box answers.

Facts are computed in code first. The Grok/Claude bridge is asked only to phrase
them, and only when the caller supplies a bridge function that returns in time.
If the bridge is down the same facts are returned with bridge=down. No trade
action waits on a model.
"""
from thf_guardian.actions import parse
from thf_guardian.seats import SEAT_LABEL


def _armed(book):
    rows = []
    for seat, sb in (book.get("seats") or {}).items():
        for pid, pos in (sb.get("positions") or {}).items():
            if pos.get("status") == "armed":
                rows.append(dict(seat=seat, id=pid, ticker=pos.get("ticker"), side=pos.get("side"),
                                 entry=pos.get("entry"), stop=pos.get("stop"), shares=pos.get("shares"),
                                 grade=pos.get("grade"), exec_note=pos.get("exec_note")))
    return rows


def _fills(book):
    rows = []
    for seat, sb in (book.get("seats") or {}).items():
        for pos in (sb.get("closed") or []):
            if pos.get("filled_utc") or pos.get("status") == "closed":
                rows.append(dict(seat=seat, id=pos.get("id"), ticker=pos.get("ticker"), exit=pos.get("exit"),
                                 reason=pos.get("exit_reason"), net=pos.get("net_aud")))
        for pid, pos in (sb.get("positions") or {}).items():
            if pos.get("status") == "open":
                rows.append(dict(seat=seat, id=pid, ticker=pos.get("ticker"), fill=pos.get("fill"), status="open"))
    return rows


def facts(text, snap):
    low = (text or "").lower()
    book = snap.get("book") or {}
    brains = snap.get("brains") or {}
    lines = []
    if any(k in low for k in ("ok", "status", "health", "everything")):
        bad = [c for c in (snap.get("checks") or []) if not c.get("ok")]
        lines.append("THF is PAPER. %s check(s) failing." % len(bad) if bad else "THF is PAPER. Checks are green.")
        for c in bad[:8]:
            lines.append("- %s: %s" % (c.get("name"), c.get("detail")))
    if "arm" in low and "alarm" not in low:
        rows = _armed(book)
        lines.append("Armed: " + (", ".join("%s %s %s @ %s" % (r["seat"], r["ticker"], r["side"], r["entry"]) for r in rows) or "none"))
    if "fill" in low:
        lines.append("Fills/open: " + (", ".join("%s %s" % (r["ticker"], r.get("reason") or r.get("status") or r.get("fill")) for r in _fills(book)[:12]) or "none"))
    if "why" in low:
        needle = low.split("why")[-1]
        for row in _armed(book):
            if row["ticker"] and row["ticker"].lower() in needle:
                lines.append("%s still armed: %s" % (row["ticker"], row.get("exec_note") or "no exec note"))
        if not any(row["ticker"] and row["ticker"].lower() in needle for row in _armed(book)):
            lines.append("No armed order matched that name.")
    down = ["%s %s" % (SEAT_LABEL.get(s, s), b.get("mode")) for s, b in brains.items() if b.get("mode") == "degraded"]
    if down:
        lines.append("Brains down: " + ", ".join(down))
    if snap.get("firm_paused"):
        lines.append("New arms are paused firm-wide (%s trader seats degraded)." % snap.get("degraded_n"))
    if not lines:
        lines.append("I can answer status, armed orders, fills and why an armed order has not filled. Anything else is queued for Marcus.")
    return "\n".join(lines)


def answer(text, snap, bridge=None, timeout_sec=25):
    """bridge(prompt, timeout) -> str or raises. Called at most once, after the facts exist."""
    action = parse(text)
    body = facts(text, snap)
    used_bridge = None
    advice = None
    if bridge is not None and action["kind"] == "queue":
        try:
            advice = bridge(body + "\n\nBrett asked: " + text, timeout_sec)
            used_bridge = "up" if advice else "down"
        except Exception:
            used_bridge = "down"
            advice = None
    elif bridge is None and action["kind"] == "queue":
        used_bridge = "down"
    else:
        # Status and whitelist actions do not need a model. A bridge may phrase a status.
        if bridge is not None and action["kind"] == "queue":
            pass
        if bridge is not None and any(k in (text or "").lower() for k in ("ok", "status", "health", "armed", "fill", "why")):
            try:
                advice = bridge(body, timeout_sec)
                used_bridge = "up" if advice else "down"
            except Exception:
                used_bridge = "down"
        elif used_bridge is None:
            used_bridge = "not_needed" if action["kind"] != "queue" else "down"
    if used_bridge == "down" or advice is None:
        text_out = body if action["kind"] != "queue" else body + "\nBridge down: status only. Queued for the head trader."
    else:
        text_out = body + "\n" + str(advice)
    queued = action["kind"] in ("queue", "reject")
    return dict(answer=text_out, bridge=used_bridge, action=action, queued_for_head=queued,
                needs_confirm=bool(action.get("needs_confirm")), paper_only=True,
                executed=None)
