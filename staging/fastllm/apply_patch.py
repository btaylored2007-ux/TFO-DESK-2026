"""Rebuild staged files from CURRENT live files (never clobber other workers' edits). tfo_fastllm2."""
import re, shutil
S = "staging/fastllm/"
# desk.py: tighter compact pack + keep live_quotes for kept syms
s = open("veteran_desk/desk.py").read()
assert "def compact_pk" in s
s = s.replace('len(json.dumps(v, default=str)) > 2500: q[k] = json.dumps(v, default=str)[:1200]', 'len(json.dumps(v, default=str)) > 1500: q[k] = json.dumps(v, default=str)[:700]')
s = s.replace('if len(lj) > 4000: q["live_feeds"] = lj[:4000]', 'if len(lj) > 2000: q["live_feeds"] = lj[:2000]')
a = '        for k, v in list(q.items()):\n            if k not in ("instruments", "live_feeds")'
if "live_quotes" not in s[s.find("def compact_pk"):s.find("def prop_prompt")]:
    s = s.replace(a, '        if isinstance(pk.get("live_quotes"), dict): q["live_quotes"] = {k: v for k, v in pk["live_quotes"].items() if k in syms or k in [str(r)[:20] for r in rk[:COMPACT_N]]} or dict(list(pk["live_quotes"].items())[:COMPACT_N])\n' + a.replace('("instruments", "live_feeds")', '("instruments", "live_feeds", "live_quotes")'))
open(S + "desk.py", "w").write(s)
# llm_backend.py: unique tmp names + 120s fast path
s = open("veteran_desk/llm_backend.py").read()
s = s.replace('tmp = path + ".tmp"; json.dump', 'tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"; json.dump')   # tfo_fastllm2: unique tmp per call
if "import threading" not in s: s = s.replace("import datetime as dt, hashlib,", "import threading, datetime as dt, hashlib,", 1)
s = s.replace("timeout=min(90, timeout or 90)", "timeout=min(120, timeout or 120)")
open(S + "llm_backend.py", "w").write(s)
# daemon unique tmp
s = open(S + "queue_answer_daemon.py").read()
s = s.replace('tmp = path + ".tmp"; json.dump', 'tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"; json.dump')
open(S + "queue_answer_daemon.py", "w").write(s)
print("patched")
s = open("tools/llm_queue_cli.py").read()
s = s.replace('tmp = P("done", rid + ".json.tmp")', 'tmp = P("done", f"{rid}.json.{os.getpid()}.{time.time_ns()}.tmp")')   # tfo_fastllm2 unique tmp
open(S + "llm_queue_cli.py", "w").write(s); print("cli patched")
