#!/usr/bin/env python3
"""tfo_fastllm_20261030: 24/7 answerer for state/llm_queue (all desks: VD, THF, hunt_clearance, reviews). PAPER ONLY.
Claims pending/<id>.json by atomic rename to taken/, answers via veteran_desk.local_llm.ask (Grok via Cursor CLI, ask mode,
empty workspace -> no file edits, never touches ledgers), writes done/<id>.json in llm_queue_cli format.
On failure the item goes back to pending/ so the box Grok routine (fallback) answers it. Stats -> reports/queue_answerer.json."""
import json, os, sys, time, threading, datetime as dt
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, BASE)
from veteran_desk import local_llm as L
Q = os.path.join(BASE, "state", "llm_queue"); REP = os.path.join(BASE, "reports", "queue_answerer.json")
MAX_AGE_S = 230   # callers give up at 240s; older items are left to the box routine
FAILED = {}; ST = dict(answered=0, failed=0, lat=[]); LK = threading.Lock(); INF = set()
def P(*a): return os.path.join(Q, *a)
def _w(path, obj):
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"; json.dump(obj, open(tmp, "w")); os.replace(tmp, path)
def report():
    with LK:
        l = sorted(ST["lat"][-200:]); n = len(l)
        _w(REP, dict(utc=dt.datetime.now(dt.timezone.utc).isoformat(), model=L.MODEL, answered=ST["answered"], failed=ST["failed"], inflight=len(INF),
                     lat_p50=l[n // 2] if n else None, lat_p90=l[int(n * .9)] if n else None, lat_last=ST["lat"][-10:], lanes=L._LANES))
def work(rid):
    src = P("taken", rid + ".json"); t0 = time.time()
    try:
        r = json.load(open(src)); age = t0 - os.path.getmtime(src)
        desk = r.get("desk") or "other"; desk = desk if desk in ("vd", "thf") else "other"
        out, st = L.ask(r.get("prompt") or "", desk=desk, seat=r.get("seat"), rid=rid, src="daemon")
        if st == "ok":
            try: ans = json.loads(out)
            except Exception: ans = out
            _w(P("done", rid + ".json"), dict(id=rid, utc=dt.datetime.now(dt.timezone.utc).isoformat(), engine="grok_bot_fast", model=L.MODEL, answer=ans))
            with LK: ST["answered"] += 1; ST["lat"].append(round(time.time() - t0 + age, 1))
        else:
            FAILED[rid] = time.time(); os.replace(src, P("pending", rid + ".json"))   # hand back to box routine
            with LK: ST["failed"] += 1
    except Exception as e:
        FAILED[rid] = time.time()
        try: os.replace(src, P("pending", rid + ".json"))
        except Exception: pass
    finally:
        with LK: INF.discard(rid)
        report()
def main():
    for d in ("pending", "done", "taken", "expired"): os.makedirs(P(d), exist_ok=True)
    report()
    while True:
        try:
            for f in sorted(os.listdir(P("pending"))):
                if not f.endswith(".json"): continue
                rid = f[:-5]
                if rid in INF or rid in FAILED or os.path.exists(P("done", f)): continue
                if time.time() - os.path.getmtime(P("pending", f)) > MAX_AGE_S: continue
                try:   # tfo_fastllm4: skip items whose direct fast-path call already failed -> straight to box routine
                    if json.load(open(P("pending", f))).get("fast_failed"): FAILED[rid] = time.time(); continue
                except Exception: continue
                try: os.replace(P("pending", f), P("taken", f))   # atomic claim
                except Exception: continue
                with LK: INF.add(rid)
                threading.Thread(target=work, args=(rid,), daemon=True).start()
        except Exception: pass
        time.sleep(0.5)
if __name__ == "__main__": main()
