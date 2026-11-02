"""Direct kill-and-flatten. Does not wait for the next book tick.

Mirrors the book.py FLAT branch for a paper equity position: open trades close
at the mark, armed orders expire, kill_switch.state becomes FLAT. Stops and
grades on the closed record are left as they were. FX positions are refused
here so a bad conversion cannot invent a cash number; the caller can pass
notional_aud that it already computed with book.aud.
"""
import copy

from thf_guardian import assert_paper
from thf_guardian.fill_paper import _book_is_paper


class FlattenRefused(Exception):
    pass


def flatten_now(book, now, marks, notional_fn, cost_fn):
    """notional_fn(pos, px) -> aud notional. cost_fn(pos, px) -> aud cost.
    Both are required so this function never guesses an FX rate."""
    assert_paper()
    if not _book_is_paper(book):
        raise FlattenRefused("not a paper book")
    out = copy.deepcopy(book)
    closed = []
    for seat, sb in (out.get("seats") or {}).items():
        for pid, pos in list((sb.get("positions") or {}).items()):
            if pos.get("status") == "armed":
                pos["status"] = "expired"
                pos["why"] = "kill_flatten"
                pos["closed_utc"] = now.isoformat()
                sb.setdefault("closed", []).append(dict(pos))
                del sb["positions"][pid]
                closed.append(dict(seat=seat, id=pid, what="expired"))
                continue
            if pos.get("status") != "open":
                continue
            if (pos.get("venue") or "") == "FX" and notional_fn is None:
                raise FlattenRefused("fx flatten needs an explicit notional")
            mark = (marks or {}).get(pos.get("ticker")) or {}
            px = mark.get("last") if isinstance(mark, dict) else mark
            if px is None:
                px = pos.get("mark") or pos.get("fill")
            if px is None:
                raise FlattenRefused("no mark for %s" % pos.get("ticker"))
            px = float(px)
            notional = float(notional_fn(pos, px))
            cost = float(cost_fn(pos, px))
            long_ = str(pos.get("side")).upper() == "LONG"
            sg = 1 if long_ else -1
            # Equity cash: give back the mark notional (long) or take it (short), pay the exit cost.
            sb["cash_aud"] = round(float(sb.get("cash_aud") or 0) + sg * notional - cost, 2)
            gross = sg * (notional - float(pos.get("fill_notional_aud") or notional))
            pos.update(status="closed", exit=px, exit_reason="kill_switch_flat", closed_utc=now.isoformat(),
                       gross_aud=round(gross, 2), net_aud=round(gross - float(pos.get("cost_aud") or 0) - cost, 2))
            sb.setdefault("closed", []).append(dict(pos))
            del sb["positions"][pid]
            closed.append(dict(seat=seat, id=pid, what="flattened", exit=px))
    out["kill_switch"] = dict(state="FLAT", at_utc=now.isoformat(), by="thf_guardian",
                              why="funder kill-and-flatten, direct path, no wait for the tick", paper_only=True)
    out["guardian_flatten"] = dict(utc=now.isoformat(), n=len(closed), paper_only=True)
    return out, closed
