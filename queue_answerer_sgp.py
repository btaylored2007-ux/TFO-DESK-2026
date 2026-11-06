#!/usr/bin/env python3
"""tfo_sgplease_20261031: droplet LLM queue answerer for the THF live tree (PAPER, advisory, fail-open).
Takes state/llm_queue/pending/*.json, asks the droplet Grok relay via ai-bridge (subscription cursor-agent, no API key),
Claude via the same bridge if Grok fails, writes done/<id>.json. Never blocks the engine; failures leave the item to expire."""
import json, os, time, urllib.request, concurrent.futures as cf
import sys as _sys; _sys.path.insert(0, "/opt/tfo/live/thf"); from veteran_desk import ai_cooldown as _CD   # maxeffort_20261102
import threading
_QA_CONTEXT=threading.local()
Q = "/opt/tfo/live/thf/state/llm_queue"; BR = "http://100.120.8.11:8790"; LOG = "/opt/tfo/live/thf/logs/queue_answerer.log"
def log(m):
    with open(LOG, "a") as f: f.write(time.strftime("%F %T ") + m + "\n")
def ask(path, prompt, to):
    _BM.client_guard()
    if path=="/grok" and getattr(globals().get("_QA_CONTEXT"),"desk",None)=="thf":
        from veteran_desk import thf_grok as G
        return G.ask(prompt,seat="advisor_grok",timeout=to,model="grok-4.7-high-fast")
    r = urllib.request.Request(BR + path, data=json.dumps({"prompt": prompt, "timeout": to}).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=to + 15)).get("advice")
def _work_thf(d,fn,t0):
    from veteran_desk import claude_brain as CB,chatgpt_bridge as CG,thf_grok as G,provider_rotation as ROT,ai_readiness as READY,deepseek_bridge as DS
    import hashlib
    deadline=float(d.get("request_deadline") or 0)
    if deadline and time.time()>=deadline:return
    def remaining(default):
        left=min(default,deadline-time.time()) if deadline else default
        if left<1:raise RuntimeError("queue request deadline exhausted")
        return left
    prompt=d.get("prompt") or "";seat=str(d.get("seat") or "queue")
    rid='thf-queue-'+hashlib.sha256(str(d.get('id') or fn).encode()).hexdigest()[:40]
    def claude():return CB.ask(prompt,seat=seat,timeout=remaining(420),kind="seat",slot_wait_max=1)
    def chatgpt():
        out=CG.ask(prompt,int(remaining(150)),seat=seat,workload="other",deadline=deadline or None,request_id=rid)
        return out,"chatgpt web"
    def grok():
        out=G.ask(prompt,seat=seat,timeout=remaining(280),model="grok-4.7-high-fast",deadline=deadline or None)
        return out,getattr(out,"model","grok authenticated")
    def deepseek():
        out=DS.ask(prompt,seat=seat,timeout=remaining(DS.MAX_SECONDS),workload='trader',deadline=deadline or None,request_id=rid)
        return out,out.model
    choices=[('claude',claude),('chatgpt',chatgpt),('grok',grok)]
    if DS.enabled_for(seat,'trader'):choices.append(('deepseek_api',deepseek))
    pairs=ROT.reorder(choices,READY.reason,request_id=rid,channel='file_queue',preferred='deepseek_api' if DS.fast_preferred() else None)
    for name,call in pairs:
        if READY.reason(name):continue
        if deadline and time.time()>=deadline:return
        try:
            _BM.client_guard();answer,model=call()
            if not answer or str(answer).startswith('__ERR__'):raise RuntimeError('empty/failed model response')
            if deadline and time.time()>=deadline:raise RuntimeError('late queue result discarded')
            ROT.record(name,True,channel='file_queue',request_id=rid)
            result=dict(id=d.get('id'),utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),engine='sgp_relay_'+name,model=model,answer=answer,paper_only=True,request_deadline=deadline or None,completed_epoch=time.time())
            target=os.path.join(Q,'done',fn);tmp=target+'.tmp';json.dump(result,open(tmp,'w'));os.replace(tmp,target)
            log(f'{fn} answered by {name} in {time.time()-t0:.1f}s');return
        except Exception as error:
            if _BM.is_hold(error):return
            ROT.record(name,False,channel='file_queue',request_id=rid,error=error);_CD.mark_fail(name,str(error));log(f'{fn} {name} error {str(error)[:160]}')
    log(f'{fn} no healthy provider response; original remains in taken for normal expiry')

def work(fn):
    _BM.client_guard()
    p = os.path.join(Q, "pending", fn); t = os.path.join(Q, "taken", fn)
    try: os.replace(p, t)
    except FileNotFoundError: return
    try: d = json.load(open(t))
    except Exception as e: log(f"bad {fn} {e}"); return
    t0 = time.time(); a = src = None
    if d.get("desk")=="thf":return _work_thf(d,fn,t0)
    mdl = None
    try:   # fablefirst_20261103 (Brett P0 12:49 WITA 3 Nov): Claude fable max -> opus max FIRST (direct CLI, own budget); relay/bridge only when Claude is down
        from veteran_desk import claude_brain as _CB
        if _CB.cooling(): log(f"{fn} claude cooldown-skip")
        else:
            _BM.client_guard()
            a, mdl = _CB.ask(d.get("prompt") or "", seat=str(d.get("seat") or "queue"), kind="seat")
            if a: src = "claude_direct"
    except Exception as e:
        if _BM.is_hold(e): log(f"{fn} retained during maintenance"); return
        log(f"{fn} claude err {e!r}"[:300]); a = None
    try:   # seatbrain_20261102: THF-only xhigh relay (127.0.0.1:8792) after Claude; shared bridge stays low-fast for other desks
        if a: raise StopIteration()
        if _CD.cooling("grok"): raise RuntimeError("grok cooldown-skip (Cursor usage limit)")
        _BM.client_guard()
        r = urllib.request.Request("http://127.0.0.1:8792/review", data=json.dumps({"prompt": d.get("prompt") or "", "timeout": 280}).encode(), headers={"Content-Type": "application/json"})
        a = json.load(urllib.request.urlopen(r, timeout=300)).get("text")
        if a: src = "grok_thf"; mdl = json.load(urllib.request.urlopen("http://127.0.0.1:8792/", timeout=5)).get("model")
    except StopIteration: pass
    except Exception as e:
        if _BM.is_hold(e): log(f"{fn} retained during maintenance"); return
        log(f"{fn} thf-relay err {e!r}"[:300])
    for path, to in ((() if a else (("/grok", 150), ("/claude", 240)))):
        if _CD.cooling(path[1:]): log(f"{fn} {path} cooldown-skip"); continue   # maxeffort_20261102
        try:
            a = ask(path, d.get("prompt") or "", to); src = path[1:]
            if a: _CD.mark_ok(path[1:]); break
        except Exception as e:
            if _BM.is_hold(e): log(f"{fn} retained during maintenance"); return
            log(f"{fn} {path} err {e!r}"[:300]); _CD.mark_fail(path[1:], repr(e))
    if not a: log(f"{fn} no answer (fail-open, item left in taken)"); return
    out = dict(id=d.get("id"), utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), engine=f"sgp_relay_{src}", model=mdl or ("grok-4.7-low-fast" if src == "grok" else src), answer=a, paper_only=True)
    tmp = os.path.join(Q, "done", fn + ".tmp"); json.dump(out, open(tmp, "w")); os.replace(tmp, os.path.join(Q, "done", fn))
    log(f"{fn} answered by {src} in {time.time()-t0:.1f}s")
# tfo_advnotes_20261102: answer Brett's per-trade notes to Grok/Claude (ADVISORY ONLY; replies go to state/advisor_msgs.jsonl, never the book/gate)
import sys; sys.path.insert(0, "/opt/tfo/live/thf")
_ADV_BUSY = set()
from veteran_desk import bridge_maintenance as _BM
_QTRACK = _BM.Tracker("/opt/tfo/live/thf/state/thf/queue_answerer_maintenance_status.json")
def adv_work(n):
    import threading as _threading
    _advisor_context=globals().setdefault("_QA_CONTEXT",_threading.local())
    _previous_desk=getattr(_advisor_context,"desk",None)
    _advisor_context.desk="thf"
    from veteran_desk import thf_funder as FM
    try:
        _BM.client_guard()
        if n.get("role") != "brett" or n.get("advisor") not in ("grok","claude","chatgpt"):
            log("advisor request retained: unsupported source or provider"); return
        original_thread=FM.adv_thread(n["trade_id"])
        if any(row.get("reply_to")==n["id"] and row.get("role")!="brett" for row in original_thread):
            return  # The durable original already has its final reply.
        th = original_thread[::-1][-12:]
        hist = "\n".join("%s %s: %s" % (r["ts_wita"], "Brett" if r["role"] == "brett" else r["role"], r["text"]) for r in th)
        pr = ("You are an ADVISOR on a PAPER trading desk (Taylor Hedge Fund). Advisory only: you cannot block, veto or gate any trade. "
              "Reply briefly (<=120 words) to Brett's latest note about this trade.\nTRADE: %s\nCONTEXT: %s\nTHREAD (oldest first):\n%s\nLATEST NOTE: %s"
              % (n["trade_id"], json.dumps(n.get("ctx") or {}), hist, n["text"]))
        if n["trade_id"] == "DESK":   # tfo_deskadv_20261102: desk-scope note, full THF desk context (read-only)
            try: st = urllib.request.urlopen("http://100.120.8.11:18870/api/state", timeout=10).read().decode()[:14000]
            except Exception as e: st = "(state unavailable %r)" % e
            try: rk = open("/opt/tfo/live/thf/state/firm_risk_record.json").read()[:3000]
            except Exception: rk = "(n/a)"
            pr = ("You are an ADVISOR to Brett on the Taylor Hedge Fund (THF) PAPER trading desk. Advisory only: you cannot block, veto, gate or trade. "
                  "Answer Brett's latest note about the WHOLE desk (book, positions, seats, risk) briefly (<=150 words), using this live desk context.\nDESK STATE (/api/state, truncated JSON): %s\nFIRM RISK: %s\nTHREAD (oldest first):\n%s\nLATEST NOTE: %s" % (st, rk, hist, n["text"]))
        a = None
        # chatimg_20261102: resolve attached image ids -> abs paths for vision (Claude CLI / ChatGPT Playwright)
        _ipaths = []
        for _iid in (n.get("images") or [])[:4]:
            try:
                _pp = FM.img_abs(_iid)
                if _pp: _ipaths.append(_pp)
            except Exception: pass
        if _ipaths:
            pr = pr + "\n\nFUNDER ATTACHED IMAGE(S) (absolute paths):\n" + "\n".join(_ipaths) + "\nDescribe/use what you SEE in the pixels."
        import re as _re   # codehands_20261102: change intent -> ask advisor for a concrete patch plan
        from veteran_desk import intent as _IN   # intent_20261103: a QUESTION is never a code change (15:24 WITA: "is Claude on all the fixes...?" spawned 3 hands runs)
        _hands = _IN.allows_code(n.get("text", ""), _ipaths, (n.get("command") or {}).get("mode") if isinstance(n.get("command"), dict) else None)
        if _hands: pr += "\n\nBrett wants a REAL code/engine change. Give a CONCRETE patch plan (files under /opt/tfo/live/thf, exact edits, tests). Execution is admitted separately through Simon/Fixer using the full original owner request under FIX_RULES."
        try:   # gptweb_20261102: chatgpt = Brett's chatgpt.com session via page UI; fix intent -> guarded fixer (Claude executes under FIX_RULES)
            if n["advisor"] == "chatgpt":
                from veteran_desk import chatgpt_bridge as CG
                a = CG.ask(pr, 150, images=_ipaths or None,seat="advisor_chatgpt",workload="other")
                import re as _re
            elif n["advisor"] == "claude":   # fablefirst_20261103: direct CLI, fable max -> opus max (vision: tools + chat_imgs dir)
                from veteran_desk import claude_brain as _CB
                a, _lab = _CB.ask(pr, seat="advisor_claude", timeout=300, tools=bool(_ipaths), add_dirs=[FM.IMG_DIR] if _ipaths else None, skip_perms=bool(_ipaths), kind="chat")
                if not a: raise RuntimeError("claude empty")
            else: a = ask("/" + n["advisor"], pr, 150 if n["advisor"] == "grok" else 240)
        except Exception as e:
            if _BM.is_hold(e): raise
            log(f"adv {n['id']} {n['advisor']} err {e!r}"[:300])
        if _hands:   # codehands_20261102: EVERY advisor drives real updates -> msgdesk fixer = code_hands (Claude hands, one at a time)
            try:
                nm = {"chatgpt": "ChatGPT", "grok": "Grok", "claude": "Claude"}.get(n["advisor"], n["advisor"])
                if a:
                    _BM.client_guard()
                    from veteran_desk import thf_msgdesk as MD
                    # Display the SAME original ID in engineering chat; this is
                    # not a new Funder request or a second engineering job.
                    if not any(row.get("id")==n["id"] for row in MD.tread()):
                        MD.tadd(id=n["id"],root=n["id"],ts_wita=n.get("ts_wita"),
                                **{"from":"brett","to":MD.SE},text=n.get("text") or "",
                                images=n.get("images") or [],mode="funder",kind="advisor_work_original")
                    receipt, admission_mode = MD.code_hands(
                        "AI advisory plan from %s (detail only, not authorization):\n%s" % (nm, a),
                        images=n.get("images"),via=n["advisor"],root=n["id"],reply_to=n["id"],
                        source_text=n.get("text") or "",command_mode=(n.get("command") or {}).get("mode"),
                        source_kind="user")
                    a = a + "\n[Simon/Fixer engineering admission: %s]" % receipt
            except Exception as e:
                if _BM.is_hold(e): raise
                log(f"adv {n['id']} engineering admission err {e!r}"[:300])
        FM.adv_add(n["trade_id"], n["advisor"], a or "(%s unavailable right now - will not retry automatically; resend the note)" % n["advisor"], role=n["advisor"], reply_to=n["id"])
        log(f"adv {n['id']} {n['advisor']} replied ok={bool(a)}")
    except _BM.MaintenanceHold:
        log("advisor %s retained without reply during maintenance" % n["id"])
    finally:
        _advisor_context.desk=_previous_desk
        _ADV_BUSY.discard(n["id"])
def adv_scan(ex):
    if _BM.paused(_BM.read_control()): return
    try:
        from veteran_desk import thf_funder as FM
        for n in FM.adv_pending():
            if n["id"] not in _ADV_BUSY:
                _ADV_BUSY.add(n["id"])
                try:
                    future=ex.submit(adv_work,n)
                    # A queued call can be blocked/cancelled before adv_work's
                    # finally runs. Its pending original must remain eligible.
                    future.add_done_callback(lambda result,mid=n["id"]:_ADV_BUSY.discard(mid))
                except BaseException:
                    _ADV_BUSY.discard(n["id"]);raise
    except Exception as e: log(f"adv scan {e!r}"[:200])
def main():
    for d in ("pending", "done", "taken", "expired"): os.makedirs(os.path.join(Q, d), exist_ok=True)
    ex = cf.ThreadPoolExecutor(4); busy = set()
    while True:
        control=_BM.read_control();_QTRACK.snapshot(control)
        if _BM.paused(control):
            time.sleep(2);continue
        try:
            for fn in sorted(os.listdir(os.path.join(Q, "pending"))):
                if fn.endswith(".json") and fn not in busy:
                    busy.add(fn); ex.submit(work, fn).add_done_callback(lambda _f, fn=fn: busy.discard(fn))
        except Exception as e: log(f"loop {e!r}")
        adv_scan(ex)   # tfo_advnotes_20261102
        time.sleep(2)
work = _QTRACK.tracked("relay_work",key=lambda fn:fn)(work)
adv_work = _QTRACK.tracked("advisor_work",key=lambda note:note.get("id"))(adv_work)
if __name__ == "__main__": main()
