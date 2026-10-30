#!/usr/bin/env python3
"""Grok Bot side of the LLM file queue (tfo_grokqueue_20261030). PAPER ONLY.
  python3 tools/llm_queue_cli.py list [--full] [--max N]   pending ids + compact prompts
  python3 tools/llm_queue_cli.py show <id>                  full request JSON
  python3 tools/llm_queue_cli.py answer <id> <json-file>    write done/<id>.json (file = the answer JSON; '-' = stdin)
  python3 tools/llm_queue_cli.py status                     backend + counts
  python3 tools/llm_queue_cli.py expire --older-min N       move stale pending to expired/"""
import json, os, sys, time, datetime as dt
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); Q = os.path.join(BASE, "state", "llm_queue")
sys.path.insert(0, BASE)
def P(*a): return os.path.join(Q, *a)
def pend(): return sorted(f[:-5] for f in os.listdir(P("pending")) if f.endswith(".json")) if os.path.isdir(P("pending")) else []
def main(a):
    cmd = a[0] if a else "list"
    if cmd == "list":
        full = "--full" in a; mx = int(a[a.index("--max") + 1]) if "--max" in a else 400
        ids = pend(); print(f"{len(ids)} pending")
        for i in ids:
            r = json.load(open(P("pending", i + ".json"))); p = r.get("prompt", "")
            print(f"\n== {i} | desk={r.get('desk')} seat={r.get('seat')} chars={r.get('prompt_chars')} utc={r.get('utc')}")
            print(p if full else (p[:mx] + (" ... " + p[-mx:] if len(p) > 2 * mx else p[mx:])).replace("\n", " "))
    elif cmd == "show": print(open(P("pending", a[1] + ".json")).read() if os.path.exists(P("pending", a[1] + ".json")) else open(P("done", a[1] + ".json")).read())
    elif cmd == "answer":
        rid, src = a[1], a[2]; raw = sys.stdin.read() if src == "-" else open(src).read()
        try: ans = json.loads(raw)
        except Exception: ans = raw   # plain-text answers (desk notes) allowed
        os.makedirs(P("done"), exist_ok=True)
        out = dict(id=rid, utc=dt.datetime.now(dt.timezone.utc).isoformat(), engine="grok_bot", answer=ans)
        tmp = P("done", f"{rid}.json.{os.getpid()}.{time.time_ns()}.tmp"); json.dump(out, open(tmp, "w")); os.replace(tmp, P("done", rid + ".json"))
        if os.path.exists(P("pending", rid + ".json")): os.makedirs(P("taken"), exist_ok=True); os.replace(P("pending", rid + ".json"), P("taken", rid + ".json"))
        print("answered", rid)
    elif cmd == "status":
        from veteran_desk import llm_backend as LB
        print(json.dumps(dict(backend=LB.backend(), config=LB.config(), pending=len(pend()), done=len(os.listdir(P("done"))) if os.path.isdir(P("done")) else 0)))
    elif cmd == "expire":
        n = float(a[a.index("--older-min") + 1]); os.makedirs(P("expired"), exist_ok=True); k = 0
        for i in pend():
            f = P("pending", i + ".json")
            if time.time() - os.path.getmtime(f) > n * 60: os.replace(f, P("expired", i + ".json")); k += 1
        print("expired", k)
    else: print(__doc__); return 2
if __name__ == "__main__": sys.exit(main(sys.argv[1:]) or 0)
