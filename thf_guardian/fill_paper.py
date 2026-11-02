"""Paper fill of a stuck grade A/A+ at its armed entry.

Same shares, same stop, same grade. Cash moves by the existing book formula
(notional plus entry cost) when the caller supplies those amounts. If the seat
would go cash-negative the fill is refused and the order stays armed.

This is not a broker order. There is no live-order path in this module.
"""
import copy

from thf_guardian import assert_paper


class FillRefused(Exception):
    pass


def _book_is_paper(book):
    if book.get("paper_only") is False or book.get("live") is True:
        return False
    for sb in (book.get("seats") or {}).values():
        for pos in (sb.get("positions") or {}).values():
            for key in ("broker_order_id", "ib_order_id", "live_order_id"):
                if pos.get(key):
                    return False
    return True


def paper_fill_at_entry(book, seat, pid, now, cost_aud, notional_aud, is_fx=False):
    """Return a new book. Raises FillRefused. Does not touch other positions' stops, sizes or grades."""
    assert_paper()
    if not _book_is_paper(book):
        raise FillRefused("book is not a paper book")
    out = copy.deepcopy(book)
    sb = (out.get("seats") or {}).get(seat)
    if not sb or pid not in (sb.get("positions") or {}):
        raise FillRefused("no such position")
    pos = sb["positions"][pid]
    if pos.get("status") != "armed":
        raise FillRefused("not armed")
    if pos.get("grade") not in ("A", "A+"):
        raise FillRefused("grade %s is not A/A+; not filled" % pos.get("grade"))
    if pos.get("entry") is None or pos.get("stop") is None or pos.get("shares") in (None, 0):
        raise FillRefused("missing entry, stop or shares")
    stop, shares, grade, entry = pos["stop"], pos["shares"], pos["grade"], pos["entry"]
    try:
        cost = float(cost_aud)
        notional = float(notional_aud)
    except (TypeError, ValueError):
        raise FillRefused("cost and notional must be numbers; refusing to hand-edit cash")
    if cost < 0 or notional < 0:
        raise FillRefused("negative cost or notional")
    long_ = str(pos.get("side")).upper() == "LONG"
    sg = 1 if long_ else -1
    cash = float(sb.get("cash_aud") or 0)
    debit = cost if is_fx else (sg * notional + cost)
    if cash - debit < -1e-6:
        raise FillRefused("insufficient cash; order left armed")
    sb["cash_aud"] = round(cash - debit, 2)
    pos["status"] = "open"
    pos["fill"] = entry
    pos["filled_utc"] = now.isoformat()
    pos["cost_aud"] = round(cost, 2)
    pos["fill_notional_aud"] = notional
    pos["slippage_bps"] = 0
    pos["exec_note"] = "guardian fill-at-entry (Brett standing rule): armed entry, same size, same stop, PAPER"
    pos["guardian_fill"] = dict(utc=now.isoformat(), rule="stuck_grade_a_at_armed_entry", paper_only=True,
                                entry=entry, stop=stop, shares=shares, grade=grade)
    if pos["stop"] != stop or pos["shares"] != shares or pos["grade"] != grade or pos["fill"] != entry:
        raise FillRefused("internal check failed; fill aborted")
    return out
