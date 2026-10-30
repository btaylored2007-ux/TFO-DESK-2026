"""CAUSAL-FILL GUARD (PAPER ONLY, 2026-10-27, GPT review upgrade #1). A simulated fill/exit is causal only if it is timestamped
at/after the information+decision that triggered it (trade armed_utc = decision; fill bar END >= armed), not in the future
relative to the wall clock when it was recorded, and exits never precede the entry. check() returns violation strings."""
import datetime as dt
def _t(x):
    try: return dt.datetime.fromisoformat(x) if x else None
    except Exception: return None
def check(t, now=None):
    v = []; armed = _t(t.get("armed_utc")); fill = _t(t.get("entry_utc") or t.get("fill_utc")); now = now or dt.datetime.now(dt.timezone.utc)
    if fill and armed and fill < armed: v.append(f"fill {fill.isoformat()} before decision/arm {armed.isoformat()}")
    if fill and fill > now + dt.timedelta(minutes=1): v.append(f"fill {fill.isoformat()} after record time {now.isoformat()}")
    for x in t.get("exits", []) or []:
        xt = _t(x.get("utc"))
        if xt and fill and xt < fill: v.append(f"exit {x.get('why')} {xt.isoformat()} before entry {fill.isoformat()}")
    return v
def audit(trades, now=None):
    out = []
    for t in trades:
        for m in check(t, now): out.append(dict(ticker=t.get("ticker") or t.get("sym"), status=t.get("status"), violation=m))
    return out
