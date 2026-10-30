"""Funder override CLI (Brett 2026-10-28 10:37 WITA). PAPER only. Records a tick in state/funder_queue.json; the book's engine applies it
on its next tick (approve -> ARMED only if price is still before trigger / entry valid, else EXPIRED; reject -> dropped + missed_shadow).
  python3 funder_approve.py --list
  python3 funder_approve.py --approve ID [ID ...]
  python3 funder_approve.py --reject  ID [ID ...]"""
import datetime as dt, sys
import funder_gate as FG

def decide(ids, dec, who="funder"):
    out = {}
    def f(Q):
        for i in ids:
            e = Q["entries"].get(i)
            if not e: out[i] = "unknown id"; continue
            if e.get("state") != FG.WAIT: out[i] = f"not pending (state {e.get('state')})"; continue
            e.update(decision=dec, decided_utc=dt.datetime.now(dt.timezone.utc).isoformat(), decided_by=who); out[i] = f"{dec} recorded - engine applies on next tick"
    FG.update(f); return out

def main(a):
    if not a or a[0] == "--list":
        for e in FG.load().get("entries", {}).values():
            if e.get("state") == FG.WAIT: print(e["id"], e["side"], e["ticker"], "entry", e["entry"], "stop", e["stop"], "target", e["target"], "risk A$", e["risk_aud"], "|", e["eli10"])
        return 0
    if a[0] not in ("--approve", "--reject") or len(a) < 2: print(__doc__); return 2
    for i, r in decide(a[1:], "approve" if a[0] == "--approve" else "reject").items(): print(i, "->", r)
    return 0
if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
