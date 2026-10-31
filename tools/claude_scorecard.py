#!/usr/bin/env python3
"""Claude advice scorecard. PAPER ONLY. Observational: Claude never changes a fill.

Compares logged verdicts with realised paper outcomes over a window (default 14 days).
Outcomes come from later JSONL join lines (kind=outcome) and from closed paper-book rows.

  python3 tools/claude_scorecard.py [--days 14] [--calls PATH] [--book PATH] [--json]
"""
import datetime as dt, json, os, sys
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path: sys.path.insert(0, BASE)
from veteran_desk import claude_review as CR
def _utc(s):
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except Exception: return None
def outcomes_from_book(path):
    out = {}
    if not path or not os.path.exists(path): return out
    try: book = json.load(open(path))
    except Exception: return out
    seats = book.get("seats") or {}
    if not isinstance(seats, dict): return out
    for seat, B in seats.items():
        for p in (B or {}).get("closed") or []:
            tid = p.get("id")
            if not tid: continue
            net = p.get("net_aud")
            try: win = float(net) > 0 if net is not None else None
            except (TypeError, ValueError): win = None
            out[tid] = dict(trade_id=tid, seat=seat, ticker=p.get("ticker"), net_aud=net, win=win, status=p.get("status"))
    return out
def score(calls, outcomes=None, now=None, days=14):
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None: now = now.replace(tzinfo=dt.timezone.utc)
    cut = now - dt.timedelta(days=days)
    outcomes = dict(outcomes or {})
    for row in calls or []:
        if isinstance(row, dict) and row.get("kind") == "outcome" and row.get("trade_id"):
            win = row.get("win")
            if win is None and row.get("net_aud") is not None:
                try: win = float(row.get("net_aud")) > 0
                except (TypeError, ValueError): win = None
            outcomes[row["trade_id"]] = dict(trade_id=row["trade_id"], net_aud=row.get("net_aud"), win=win, ticker=row.get("ticker"), seat=row.get("seat"))
    best = {}
    for row in calls or []:
        if not isinstance(row, dict) or row.get("kind") == "outcome": continue
        ts = _utc(row.get("utc"))
        if ts is not None and ts < cut: continue
        if row.get("cached"): continue
        tid = row.get("trade_id") or row.get("request_id")
        if not tid: continue
        prev = best.get(tid)
        if prev and prev.get("verdict") in ("agree", "disagree") and row.get("verdict") == "abstain": continue
        best[tid] = row
    tallies = dict(agree_and_won=0, agree_and_lost=0, disagree_and_won=0, disagree_and_lost=0, abstain=0, unjoined=0)
    by_desk = {}
    for tid, row in best.items():
        desk = row.get("desk") or "other"
        slot = by_desk.setdefault(desk, dict(n=0, joined=0))
        slot["n"] += 1
        v = row.get("verdict")
        if v == "abstain" or v not in ("agree", "disagree"):
            tallies["abstain"] += 1; continue
        oc = outcomes.get(row.get("trade_id")) or outcomes.get(tid)
        if not oc or oc.get("win") is None:
            tallies["unjoined"] += 1; continue
        slot["joined"] += 1
        key = ("agree" if v == "agree" else "disagree") + ("_and_won" if oc.get("win") else "_and_lost")
        tallies[key] += 1
    decided = tallies["agree_and_won"] + tallies["agree_and_lost"]
    return dict(paper_only=True, advisory_only=True, window_days=days, calls=len(best), joined=sum(tallies[k] for k in ("agree_and_won", "agree_and_lost", "disagree_and_won", "disagree_and_lost")),
                agree_hit_rate=(round(tallies["agree_and_won"] / decided, 4) if decided else None),
                by_desk=by_desk, **tallies,
                note="Claude is advice only. This scorecard does not change a trade, arm, stop or exit.")
def main(argv):
    days, calls, book, as_json = 14, None, None, "--json" in argv
    if "--days" in argv: days = int(argv[argv.index("--days") + 1])
    if "--calls" in argv: calls = argv[argv.index("--calls") + 1]
    if "--book" in argv: book = argv[argv.index("--book") + 1]
    rows = CR.read_calls(calls) if calls else CR.read_calls()
    outcomes = outcomes_from_book(book) if book else {}
    if book is None:
        for rel in ("reports/veteran_desk_book.json", "state/thf/thf_book.json"):
            outcomes.update(outcomes_from_book(os.path.join(BASE, rel)))
    rep = score(rows, outcomes, days=days)
    print(json.dumps(rep, indent=2, default=str) if as_json else json.dumps(rep, default=str))
    return 0
if __name__ == "__main__": sys.exit(main(sys.argv[1:]) or 0)
