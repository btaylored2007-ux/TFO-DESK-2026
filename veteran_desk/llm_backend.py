"""tfo_grokqueue_20261030 (Brett 22:10 WITA): LLM backend switch for VD + THF seats. PAPER ONLY.
Config: state/llm_backend.json {"backend": "codex"|"grok_queue", "revert_at": ISO, "revert_to": "codex"}.
grok_queue: write state/llm_queue/pending/<id>.json, poll state/llm_queue/done/<id>.json; Grok Bot answers via tools/llm_queue_cli.py.
No prompt cap (Brett 22:11). 22:29: Codex removed everywhere; backend() always grok_queue, no revert."""
import threading, datetime as dt, hashlib, inspect, json, os, time
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(BASE, "state", "llm_backend.json")
Q = os.path.join(BASE, "state", "llm_queue")
POLL_S = float(os.environ.get("VET_GROK_POLL_S", "2"))
WAIT_S = int(os.environ.get("VET_GROK_QUEUE_TIMEOUT", "420"))   # head trader 2026-10-31 06:12 WITA: 4 -> 7 min; seats timed out before the 5-min Grok answerer reached them   # fablefirst_20261103: the queue is now the LAST AI hop (Claude/ChatGPT/SuperGrok/Grok fast path come first in-process)
REUSE_S = 30 * 60   # tfo_reuse30_20261030: answer reuse capped 30 min (was 6h) - not every prompt embeds live levels/timestamps
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
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
    with open(tmp,"w") as stream:json.dump(obj,stream)
    os.replace(tmp,path)
def grok_call(engine, prompt, timeout=None, effort="high", seat=None, desk=None):
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("VET_GROK_QUEUE_IN_TESTS"): return "__ERR__ awaiting_grok test-mode", "test", "awaiting_grok"
    _sk0 = seat or _ctx(); _dk0 = desk or desk_id()
    h = hashlib.sha1((prompt or "").encode()).hexdigest()[:12]
    rid0 = _now().strftime("%Y%m%dT%H%M%S") + f"_{os.getpid() % 10000}_{h}"
    LAST.fair_desk=_dk0;LAST.fair_seat=_sk0;LAST.fair_request_id=rid0
    # Capture once: a newer round must not extend a timed-out worker's life.
    try: request_deadline=float(getattr(LAST,"request_deadline",None) or os.environ.get("THF_ROUND_DEADLINE") or 0) if _dk0=="thf" else 0
    except (TypeError,ValueError): request_deadline=0
    cancel_event=getattr(LAST,"cancel_event",None)
    def active():
        return not (cancel_event is not None and cancel_event.is_set()) and not (request_deadline and time.time()>=request_deadline)
    def discard_written(path):
        try:os.unlink(path)
        except FileNotFoundError:pass
    def expired():
        _brainlog(dict(seat=_sk0,desk=_dk0,ai="deadline",ok=False,err="round deadline/cancellation; late provider result discarded"))
        return "__ERR__ round_deadline "+rid0,rid0,"deadline_exhausted"
    if not active():return expired()
    for d in ("pending", "done", "taken", "expired"): os.makedirs(os.path.join(Q, d), exist_ok=True)
    # reuse a recent answer to the identical prompt (late answers are not wasted)
    for f in sorted(os.listdir(os.path.join(Q, "done")), reverse=True):
        if f.endswith(f"_{h}.json") and time.time() - os.path.getmtime(os.path.join(Q, "done", f)) < REUSE_S:
            try:
                with open(os.path.join(Q,"done",f)) as stream:cached=json.load(stream)
                if _dk0=="thf" and request_deadline:
                    original_deadline=float(cached.get("request_deadline") or 0)
                    completed=float(cached.get("completed_epoch") or 0)
                    if not original_deadline or not completed or completed>=original_deadline or time.time()>=original_deadline:continue
                if not active():return expired()
                if _dk0 == "thf" and 'Schema: {"action":' in (prompt or ""):
                    from veteran_desk.communication_protocol import trade_reply
                    trade_reply(_answer(cached))
                return _answer(cached), f[:-5], "reused"
            except Exception: pass
    # fablefirst_20261103 (Brett P0 12:49 WITA 3 Nov, Ultra plan; A52): Claude fable max -> opus max is the PRIMARY brain of every seat,
    # then ChatGPT web (Brett's own session), then the Grok relay fast path below, then the Grok file queue.
    # The code fallback (desk._code_fallback) is reached only when every AI is down. Which AI answered is written to done/<id>.json (model).
    _ai_ok = (not os.environ.get("PYTEST_CURRENT_TEST")) or os.environ.get("VET_AI_CHAIN_IN_TESTS")   # fablefirst_20261103: a test exercising the Grok file queue must NOT spawn a real Claude/ChatGPT CLI
    for _nm, _fn in (_ai_chain() if _ai_ok else []):
        if not active():return expired()
        try:
            out, mdl = _fn(prompt or "", _sk0, timeout)
            if not active():return expired()
            if out and not str(out).startswith("__ERR__"):
                if _dk0 == "thf" and 'Schema: {"action":' in (prompt or ""):
                    from veteran_desk.communication_protocol import trade_reply
                    trade_reply(out)
                LAST.model = mdl
                if _dk0=="thf":
                    from veteran_desk import provider_rotation as ROT
                    ROT.record(_nm,True,channel="trader",request_id=rid0)
                try: _write(os.path.join(Q, "done", rid0 + ".json"), dict(id=rid0, utc=_now().isoformat(), engine=_nm, model=mdl, seat=_sk0, desk=_dk0, answer=out, paper_only=True, request_deadline=request_deadline or None, completed_epoch=time.time()))
                except Exception: pass
                if not active():
                    discard_written(os.path.join(Q,"done",rid0+".json"))
                    return expired()
                _brainlog(dict(seat=_sk0, desk=_dk0, ai=_nm, model=mdl, ok=True, chars=len(out),
                               request_id=getattr(out,"request_id",None),
                               accepted_at=getattr(out,"accepted_at",None),
                               started_at=getattr(out,"started_at",None),
                               finished_at=getattr(out,"finished_at",None)))
                return out, rid0, "ok"
        except Exception as e:
            if _dk0=="thf":
                from veteran_desk import provider_rotation as ROT,bridge_maintenance as BM
                if not BM.is_hold(e):ROT.record(_nm,False,channel="trader",request_id=rid0,error=e)
            _brainlog(dict(seat=_sk0,desk=_dk0,ai=_nm,ok=False,err=str(e)[:160]))
    # tfo_fastllm_20261030: direct fast path (Grok via Cursor CLI, 1 retry) before the queue; falls back to the queue on any error
    if not active():return expired()
    fast_failed = False   # tfo_fastllm4: direct fast path already timed out -> daemon must not retry (box routine answers)
    if os.environ.get("TFO_FAST_LLM", "1") == "1":
        try:
            from veteran_desk import local_llm as _LL
            from veteran_desk import ai_readiness as READY
            if _LL.enabled() and not READY.reason("grok"):
                d_ = desk or desk_id(); rid0 = _now().strftime("%Y%m%dT%H%M%S") + f"_{os.getpid() % 10000}_{h}"
                # tfo_seatretry_20261101 (HT-approved): primary, then ONE retry of the primary, then the backup model, then the queue
                _sk = seat or _ctx(); _dk = d_ if d_ in ("vd", "thf") else "other"
                for _i, (_m, _to) in enumerate(((None, min(270, timeout or 270)), (getattr(_LL, "BACKUP_MODEL", None), min(120, timeout or 120)))):
                    if not active():return expired()
                    if _i == 1 and not _m: break
                    if _i and READY.reason("grok"):break
                    out, st = _LL.ask(prompt or "", desk=_dk, seat=_sk, timeout=_to, rid=rid0 + ("" if not _i else "_r%d" % _i), src="direct" if not _i else ("retry" if _i == 1 else "backup"), model=_m, retries=0 if (_i or _dk0=="thf") else 1)
                    if not active():return expired()
                    if st == "ok":
                        if _dk0 == "thf" and 'Schema: {"action":' in (prompt or ""):
                            from veteran_desk.communication_protocol import trade_reply
                            trade_reply(out)
                        try: _write(os.path.join(Q, "done", rid0 + ".json"), dict(id=rid0, utc=_now().isoformat(), engine="grok_bot_fast", model=_m or _LL.MODEL, attempt=_i, answer=out, request_deadline=request_deadline or None, completed_epoch=time.time()))
                        except Exception: pass
                        if not active():
                            discard_written(os.path.join(Q,"done",rid0+".json"))
                            return expired()
                        return out, rid0, "ok"
                fast_failed = True
        except Exception: pass
    if not active():return expired()
    if _dk0=="thf":
        _brainlog(dict(seat=_sk0,desk=_dk0,ai="code_fallback",ok=False,err="AI providers unavailable or busy; no THF queue wait"))
        return "__ERR__ no_ai_available "+rid0,rid0,"no_ai_available"
    try:   # brainsafe_20261103: the queue answerer (queue_answerer_sgp) answers only with Claude or the Grok relay; when both are cooling/down nobody
        from veteran_desk import ai_cooldown as _CDq   # will answer, so do not hold the seat WAIT_S (7 min) for nothing -> straight to the code fallback
        if _CDq.cooling("claude") and (_CDq.cooling("grok") or _CDq.cooling("seat")):
            _brainlog(dict(seat=_sk0, desk=_dk0, ai="grok_queue", ok=False, err="skipped: no answerer (claude cooling + grok relay down)"))
            return f"__ERR__ awaiting_grok {rid0} (no answerer)", rid0, "awaiting_grok"
    except Exception: pass
    for f in os.listdir(os.path.join(Q, "pending")):   # same prompt already queued -> wait on that one
        if f.endswith(f"_{h}.json"): rid = f[:-5]; break
    else:
        rid = _now().strftime("%Y%m%dT%H%M%S") + f"_{os.getpid() % 10000}_{h}"
        _write(os.path.join(Q, "pending", rid + ".json"), dict(id=rid, utc=_now().isoformat(), seat=seat or _ctx(),
               desk=desk or desk_id(), engine_requested=engine, effort=effort,
               prompt=prompt, prompt_chars=len(prompt or ""), schema=_schema(prompt), paper_only=True, fast_failed=fast_failed))
    t0 = time.time(); lim = min(WAIT_S, timeout or WAIT_S)
    try:   # rounddl_20261103: the queue wait also ends at the desk round's deadline
        _dlq = float(os.environ.get("THF_ROUND_DEADLINE") or 0)
        if _dlq: lim = max(0, min(lim, _dlq - t0))
    except Exception: pass
    dp = os.path.join(Q, "done", rid + ".json")
    while time.time() - t0 < lim:
        if os.path.exists(dp):
            try: return _answer(json.load(open(dp))), rid, "ok"
            except Exception: pass
        time.sleep(POLL_S)
    return f"__ERR__ awaiting_grok {rid}", rid, "awaiting_grok"
import threading as _thm; LAST = _thm.local()   # seatbrain_20261102: model the relay reported, per seat thread
# ---------------- fablefirst_20261103: Claude-first AI chain (shared by every seat, trader and admin) ----------------
BRAIN_LOG = os.path.join(BASE, "logs", "seat_brain.jsonl")
def _brainlog(rec):
    try:
        os.makedirs(os.path.dirname(BRAIN_LOG), exist_ok=True); rec = dict(utc=_now().isoformat(timespec="seconds"), **rec)
        with open(BRAIN_LOG, "a") as f: f.write(json.dumps(rec) + "\n")
    except Exception: pass
def _ask_claude(prompt, seat, timeout):
    from veteran_desk import claude_brain as CB,ai_readiness as READY
    why=READY.reason("claude")
    if why:raise RuntimeError("claude: "+why)
    return CB.ask(prompt, seat=seat, timeout=timeout, kind="seat",slot_wait_max=1)   # own budget (brain_cfg claude_timeout_s, default 420s), fable max -> opus max
def _ask_chatgpt(prompt, seat, timeout):
    from veteran_desk import chatgpt_bridge as CG, ai_cooldown as CD
    if not CG.enabled(): raise RuntimeError("chatgpt bridge off (%s)" % CG.status().get("state"))
    if CD.cooling("chatgpt"): raise RuntimeError("chatgpt cooling down")
    from veteran_desk import ai_readiness as READY
    # Only the paced proposer lane may use the one pending bridge slot.
    paired = False
    if getattr(LAST,"paired_trader",False):
        # Source swaps precede service activation. Never send a queue-capable
        # caller to an old server whose admission has no hard bound.
        live=CG.health()
        admission=(live.get("admission") or {}) if isinstance(live,dict) else {}
        paired=(isinstance(live,dict) and isinstance(admission,dict)
                and live.get("state")=="logged_in" and admission.get("capacity")==2
                and admission.get("protocol")=="bounded_seat_admission_v1")
    why=READY.reason("chatgpt", check_busy=not paired)
    if why:raise RuntimeError("chatgpt: "+why)
    if not paired and getattr(CG, "busy", lambda: False)(): raise RuntimeError("chatgpt bridge busy -> next AI")
    _to = int(os.environ.get("THF_CHATGPT_TIMEOUT_S", "600"))   # gptshare_20261103 (failover test 18:36): GPT-6 Extra High needs >280s on a 23KB round prompt (504 at 280s); the round deadline still caps it
    try: _dl = float(getattr(LAST,"request_deadline",None) or os.environ.get("THF_ROUND_DEADLINE") or 0)   # rounddl_20261103: never run past the desk round's deadline
    except Exception: _dl = 0
    if _dl:
        _to = min(_to, int(_dl - time.time()))
        if _to < 65: raise RuntimeError("chatgpt skipped: round deadline -> code fallback")
    cancel_event=getattr(LAST,"cancel_event",None)
    if cancel_event is not None and cancel_event.is_set():raise RuntimeError("chatgpt skipped: round cancelled")
    try:
        out = CG.ask(prompt, timeout=_to, skip_if_busy=not paired, seat=seat,
                     workload="trader" if paired else "other", deadline=_dl or None)
        CD.mark_ok("chatgpt")
    except Exception as e: CD.mark_fail("chatgpt", str(e)); raise
    return out, "chatgpt web (%s)" % (CG.status().get("model_pick") or "default")
def _ask_grok_authenticated(prompt,seat,timeout):
    from veteran_desk import thf_grok as G,local_llm as LL
    try:deadline=float(getattr(LAST,"request_deadline",None) or os.environ.get("THF_ROUND_DEADLINE") or 0)
    except (TypeError,ValueError):deadline=0
    out=G.ask(prompt,seat=seat,timeout=min(270,timeout or 270),model=LL.MODEL,deadline=deadline or None)
    return out,getattr(out,"model","grok authenticated")
def _ask_deepseek(prompt, seat, timeout):
    from veteran_desk import deepseek_bridge as D,ai_readiness as READY
    why=READY.reason('deepseek_api')
    if why:raise RuntimeError('deepseek_api: '+why)
    try:deadline=float(getattr(LAST,'request_deadline',None) or os.environ.get('THF_ROUND_DEADLINE') or 0)
    except (TypeError,ValueError):deadline=0
    budget=min(D.MAX_SECONDS,float(timeout or D.MAX_SECONDS))
    if deadline:budget=min(budget,deadline-time.time())
    out=D.ask(prompt,seat=seat,timeout=budget,deadline=deadline or None,
              request_id=getattr(LAST,'fair_request_id',None),
              workload='trader' if getattr(LAST,'paired_trader',False) else 'other',
              cancel_event=getattr(LAST,'cancel_event',None))
    return out,out.model

def _ai_chain():
    """Ordered (name, fn) pairs tried BEFORE the Grok relay/queue. Env switches: TFO_CLAUDE_FIRST=0 / TFO_CHATGPT_SEATS=0 turn one off."""
    ch = []
    if os.environ.get("TFO_CLAUDE_FIRST", "1") == "1": ch.append(("claude_direct", _ask_claude))
    if os.environ.get("TFO_CHATGPT_SEATS", "1") == "1": ch.append(("chatgpt_web", _ask_chatgpt))
    if getattr(LAST,"fair_desk",None)=="thf" or desk_id()=="thf":
        from veteran_desk import provider_rotation as ROT,ai_readiness as READY,deepseek_bridge as DS
        if DS.enabled_for(getattr(LAST,"fair_seat",None),"trader" if getattr(LAST,"paired_trader",False) else "other"):ch.append(("deepseek_api",_ask_deepseek))
        if os.environ.get("TFO_FAST_LLM","1")=="1":ch.append(("grok_authenticated",_ask_grok_authenticated))
        paired=bool(getattr(LAST,"paired_trader",False))
        ch=ROT.reorder(ch,lambda name:READY.reason(ROT.canonical(name),check_busy=False) if ROT.canonical(name)=="chatgpt" and paired else READY.reason(ROT.canonical(name)),request_id=getattr(LAST,"fair_request_id",None),channel="trader",preferred="deepseek_api" if DS.fast_preferred() else None)
        ch=_apply_claude_seat_policy(ch)   # claudeweek_20261106 (G4)
    return ch
def _apply_claude_seat_policy(ch):
    """claudeweek_20261106 (master fixer, checklist G4): seat work (everything that comes through grok_call: rounds, seat chatter, reviews)
    treats Claude as a BOUNDED FALLBACK so the 14 seats cannot burn Brett's weekly Claude limit again (3-4 Nov: dark for 44 h incl. the
    master fixer). brain_cfg claude_seat_policy: 'fallback' (default) = claude_direct moves behind every other AI; 'primary' = Claude first;
    'off' = Claude removed for seats; 'rotation' = leave the provider rotation's order alone (the pre-6 Nov behaviour). Brett's own chat and code_hands do not come through here."""
    try:
        from veteran_desk import claude_brain as CB
        pol = CB.seat_policy()
    except Exception: pol = "fallback"
    cl = [p for p in ch if p[0] == "claude_direct"]; rest = [p for p in ch if p[0] != "claude_direct"]
    if not cl: return ch
    if pol == "off": return rest
    if pol == "primary": return cl + rest
    if pol == "rotation": return ch
    return rest + cl
def _answer(d):
    LAST.model = d.get("model") or d.get("engine") if isinstance(d, dict) else None
    a = d.get("answer") if isinstance(d, dict) and "answer" in d else d
    return a if isinstance(a, str) else json.dumps(a)
