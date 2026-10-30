"""tfo_grokqueue_20261030 (Brett 22:10 WITA): LLM backend switch for VD + THF seats. PAPER ONLY.
Config: state/llm_backend.json {"backend": "codex"|"grok_queue", "revert_at": ISO, "revert_to": "codex"}.
grok_queue: write state/llm_queue/pending/<id>.json, poll state/llm_queue/done/<id>.json; Grok Bot answers via tools/llm_queue_cli.py.
No prompt cap (Brett 22:11). 22:29: Codex removed everywhere; backend() always grok_queue, no revert."""
import datetime as dt, hashlib, inspect, json, os, time
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(BASE, "state", "llm_backend.json")
Q = os.path.join(BASE, "state", "llm_queue")
POLL_S = float(os.environ.get("VET_GROK_POLL_S", "2"))
WAIT_S = int(os.environ.get("VET_GROK_QUEUE_TIMEOUT", "240"))
REUSE_S = 6 * 3600   # a late Grok answer to the same prompt is reused for 6h
def _now(): return dt.datetime.now(dt.timezone.utc)
def config():
    try: return json.load(open(CFG))
    except Exception: return {}
def desk_id(): return "thf" if os.environ.get("THF_DESK") == "1" else "vd"
def backend(desk=None):
    """Per-desk: config {"vd": {"backend","revert_at","revert_to"}, "thf": {"backend": "grok_queue"}} (THF Grok-only permanently, Brett 22:15)."""
    return "grok_queue"   # Brett 22:29 WITA 30 Oct: Codex removed entirely, every desk Grok permanently
    c = config(); c = c.get(desk or desk_id(), c) if isinstance(c, dict) else {}
    b = c.get("backend", "codex")
    try:
        if c.get("revert_at") and _now() >= dt.datetime.fromisoformat(c["revert_at"]): return c.get("revert_to", "codex")
    except Exception: pass
    return b if b in ("codex", "grok_queue") else "codex"
def _ctx():
    """Best-effort seat id from the caller stack (a local 'seat' dict or 'sk'/'seat_key' str)."""
    for fr in inspect.stack()[2:12]:
        L = fr.frame.f_locals
        s = L.get("seat")
        if isinstance(s, dict): return s.get("key") or s.get("id") or s.get("name") or fr.function
        for k in ("sk", "seat_key", "role", "name"):
            if isinstance(L.get(k), str) and len(L[k]) < 40: return L[k]
    return inspect.stack()[3].function if len(inspect.stack()) > 3 else "?"
def _schema(prompt):
    p = prompt or ""; i = max(p.rfind("Reply"), p.rfind("Return"), p.rfind("JSON"))
    return p[i:i + 600] if i >= 0 else None
def _write(path, obj):
    tmp = path + ".tmp"; json.dump(obj, open(tmp, "w")); os.replace(tmp, path)
def grok_call(engine, prompt, timeout=None, effort="high", seat=None, desk=None):
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("VET_GROK_QUEUE_IN_TESTS"): return "__ERR__ awaiting_grok test-mode", "test", "awaiting_grok"
    for d in ("pending", "done", "taken", "expired"): os.makedirs(os.path.join(Q, d), exist_ok=True)
    h = hashlib.sha1((prompt or "").encode()).hexdigest()[:12]
    # reuse a recent answer to the identical prompt (late answers are not wasted)
    for f in sorted(os.listdir(os.path.join(Q, "done")), reverse=True):
        if f.endswith(f"_{h}.json") and time.time() - os.path.getmtime(os.path.join(Q, "done", f)) < REUSE_S:
            try: return _answer(json.load(open(os.path.join(Q, "done", f)))), f[:-5], "reused"
            except Exception: pass
    for f in os.listdir(os.path.join(Q, "pending")):   # same prompt already queued -> wait on that one
        if f.endswith(f"_{h}.json"): rid = f[:-5]; break
    else:
        rid = _now().strftime("%Y%m%dT%H%M%S") + f"_{os.getpid() % 10000}_{h}"
        _write(os.path.join(Q, "pending", rid + ".json"), dict(id=rid, utc=_now().isoformat(), seat=seat or _ctx(),
               desk=desk or desk_id(), engine_requested=engine, effort=effort,
               prompt=prompt, prompt_chars=len(prompt or ""), schema=_schema(prompt), paper_only=True))
    t0 = time.time(); lim = min(WAIT_S, timeout or WAIT_S)
    dp = os.path.join(Q, "done", rid + ".json")
    while time.time() - t0 < lim:
        if os.path.exists(dp):
            try: return _answer(json.load(open(dp))), rid, "ok"
            except Exception: pass
        time.sleep(POLL_S)
    return f"__ERR__ awaiting_grok {rid}", rid, "awaiting_grok"
def _answer(d):
    a = d.get("answer") if isinstance(d, dict) and "answer" in d else d
    return a if isinstance(a, str) else json.dumps(a)
