"""tfo_grokqueue_20261030 (Brett 22:10 WITA): LLM backend switch for VD + THF seats. PAPER ONLY.
Config: state/llm_backend.json {"backend": "codex"|"grok_queue", "revert_at": ISO, "revert_to": "codex"}.
grok_queue: write state/llm_queue/pending/<id>.json, poll state/llm_queue/done/<id>.json; Grok Bot answers via tools/llm_queue_cli.py.
No prompt cap (Brett 22:11). 22:29: Codex removed everywhere; backend() always grok_queue, no revert."""
import threading, datetime as dt, hashlib, inspect, json, os, time
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(BASE, "state", "llm_backend.json")
Q = os.path.join(BASE, "state", "llm_queue")
POLL_S = float(os.environ.get("VET_GROK_POLL_S", "2"))
WAIT_S = int(os.environ.get("VET_GROK_QUEUE_TIMEOUT", "240"))
REUSE_S = 30 * 60   # tfo_reuse30_20261030: answer reuse capped 30 min (was 6h) - not every prompt embeds live levels/timestamps
def _now(): return dt.datetime.now(dt.timezone.utc)
def config():
    try: return json.load(open(CFG))
    except Exception: return {}
def desk_id(): return "thf" if os.environ.get("THF_DESK") == "1" else "vd"
def backend(desk=None):
    """Every desk is Grok. Brett 22:29 WITA 30 Oct: Codex removed, no revert."""
    return "grok_queue"
def _ctx():
    """Best-effort seat id from the caller stack (a local 'seat' dict or 'sk'/'seat_key' str)."""
    for fr in inspect.stack()[2:16]:
        L = fr.frame.f_locals
        for v in (L.get("seat"), L.get("s"), L.get("p")):   # tfo_seatlabel_20261030: seat dicts carry their id under "seat"
            if isinstance(v, dict) and isinstance(v.get("seat"), str): return v["seat"]
            if isinstance(v, dict) and (v.get("key") or v.get("id")): return v.get("key") or v.get("id")
        if isinstance(L.get("seat"), str) and len(L["seat"]) < 40: return L["seat"]
        for k in ("sk", "seat_key", "role", "name"):
            if isinstance(L.get(k), str) and len(L[k]) < 40: return L[k]
    return inspect.stack()[3].function if len(inspect.stack()) > 3 else "?"
def _schema(prompt):
    p = prompt or ""; i = max(p.rfind("Reply"), p.rfind("Return"), p.rfind("JSON"))
    return p[i:i + 600] if i >= 0 else None
def _write(path, obj):
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"; json.dump(obj, open(tmp, "w")); os.replace(tmp, path)
def grok_call(engine, prompt, timeout=None, effort="high", seat=None, desk=None):
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("VET_GROK_QUEUE_IN_TESTS"): return "__ERR__ awaiting_grok test-mode", "test", "awaiting_grok"
    for d in ("pending", "done", "taken", "expired"): os.makedirs(os.path.join(Q, d), exist_ok=True)
    h = hashlib.sha1((prompt or "").encode()).hexdigest()[:12]
    # reuse a recent answer to the identical prompt (late answers are not wasted)
    for f in sorted(os.listdir(os.path.join(Q, "done")), reverse=True):
        if f.endswith(f"_{h}.json") and time.time() - os.path.getmtime(os.path.join(Q, "done", f)) < REUSE_S:
            try: return _answer(json.load(open(os.path.join(Q, "done", f)))), f[:-5], "reused"
            except Exception: pass
    # tfo_fastllm_20261030: direct fast path (Grok via Cursor CLI, 1 retry) before the queue; falls back to the queue on any error
    fast_failed = False   # tfo_fastllm4: direct fast path already timed out -> daemon must not retry (box routine answers)
    if os.environ.get("TFO_FAST_LLM", "1") == "1":
        try:
            from veteran_desk import local_llm as _LL
            if _LL.enabled():
                d_ = desk or desk_id(); rid0 = _now().strftime("%Y%m%dT%H%M%S") + f"_{os.getpid() % 10000}_{h}"
                out, st = _LL.ask(prompt or "", desk=d_ if d_ in ("vd", "thf") else "other", seat=seat or _ctx(), timeout=min(120, timeout or 120), rid=rid0, src="direct")
                if st == "ok":
                    try: _write(os.path.join(Q, "done", rid0 + ".json"), dict(id=rid0, utc=_now().isoformat(), engine="grok_bot_fast", model=_LL.MODEL, answer=out))
                    except Exception: pass
                    return out, rid0, "ok"
                fast_failed = True
        except Exception: pass
    for f in os.listdir(os.path.join(Q, "pending")):   # same prompt already queued -> wait on that one
        if f.endswith(f"_{h}.json"): rid = f[:-5]; break
    else:
        rid = _now().strftime("%Y%m%dT%H%M%S") + f"_{os.getpid() % 10000}_{h}"
        _write(os.path.join(Q, "pending", rid + ".json"), dict(id=rid, utc=_now().isoformat(), seat=seat or _ctx(),
               desk=desk or desk_id(), engine_requested=engine, effort=effort,
               prompt=prompt, prompt_chars=len(prompt or ""), schema=_schema(prompt), paper_only=True, fast_failed=fast_failed))
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
