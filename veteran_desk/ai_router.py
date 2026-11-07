"""aipick_20261102 (Brett 20:40 WITA, PAPER, advisory only): one AI helper for chat + every trader/admin seat.
ai choices: auto (fallback chain, default) | claude | chatgpt | grok | super (SUPER POWER).
 - explicit pick: that AI first; if it is cooling down / fails, falls back down the auto chain (reply says so).
 - super: fan out the same prompt to every available AI in parallel at max effort (Claude opus max, ChatGPT Pro web,
   Grok high via relay), each with its own hard timeout; skip any AI on ai_cooldown, whose bridge
   is logged out, or whose concurrency slot is busy (never wait); then ONE synth pass by the strongest AI that answered
   merges + cherry-picks, flags disagreements and lists contributors. No AI available -> RuntimeError.
Seats: per-seat default in state/thf/seat_ai_pref.json (set: python -m veteran_desk.ai_router pref <seat> <ai>);
any seat/tool can call: python -m veteran_desk.ai_router ask --seat <seat> --ai super "question".
Never writes books/ledgers/gates. Provider functions live in thf_msgdesk (resolved lazily so tests can monkeypatch)."""
import concurrent.futures as cf, json, os, re, sys, threading, time
CHOICES = ("auto", "claude", "chatgpt", "grok", "deepseek", "deepseek_api", "super", "supergrok")
ALIASES = {"super_power": "super", "superpower": "super", "gpt": "chatgpt", "openai": "chatgpt", "": "auto", "none": "auto", "deepseek": "deepseek_api"}
LABEL = dict(auto="Auto", claude="Claude", chatgpt="ChatGPT", grok="Grok", deepseek="DeepSeek API", deepseek_api="DeepSeek API", super="SUPER POWER", supergrok="SuperGrok")
PREF = "/opt/tfo/live/thf/state/thf/seat_ai_pref.json"
FAN = ("claude", "chatgpt", "grok")          # super fan-out (distinct AIs)
STRONG = ("claude", "chatgpt", "grok")  # synth preference (strongest first)
VISION = ("claude", "chatgpt")
CAP = dict(claude=16, chatgpt=1, grok=6, seat=3, deepseek_api=4, supergrok=1)   # web bridges are serial: busy -> skip, never queue   # fablefirst_20261103: Ultra plan, Claude never throttled
_SEM = {k: threading.BoundedSemaphore(v) for k, v in CAP.items()}
GROK_MAX = os.environ.get("SUPER_GROK_MODEL", "grok-4.7-high-fast")

def norm(ai):
    a = str(ai or "").strip().lower(); a = ALIASES.get(a, a)
    return a if a in CHOICES else "auto"
def prefs():
    try: return json.load(open(PREF))
    except Exception: return {}
def set_pref(seat, ai):
    d = prefs(); a = norm(ai)
    if a == "auto": d.pop(seat, None)
    else: d[seat] = a
    t = PREF + ".tmp"; json.dump(d, open(t, "w"), indent=1); os.replace(t, PREF); return d
def resolve(ai, seat=None):
    """Explicit pick wins; else the seat's own default; else auto."""
    a = norm(ai)
    return a if a != "auto" else norm(prefs().get(seat or ""))

def _md():
    from veteran_desk import thf_msgdesk as MD
    return MD
def _grok_max(seat,p):
    from veteran_desk import thf_grok as G
    return G.ask(p,seat=seat,timeout=95,model=GROK_MAX)

def _supergrok_call(seat, prompt, images=None, **_kwargs):
    from veteran_desk import supergrok_bridge as SG
    if images:
        raise RuntimeError("SuperGrok page route has no image attach in this package")
    return SG.ask(prompt, seat=seat)


def providers(max_effort=False):
    MD = _md()
    return dict(claude=MD._claude, chatgpt=MD._chatgpt, grok=_grok_max if max_effort else MD._grok, seat=MD._seat_brain, deepseek=MD._deepseek_chat, deepseek_api=MD._deepseek_api, supergrok=_supergrok_call)
def fan_names(seat, images=None):
    from veteran_desk import deepseek_bridge as DS
    return FAN + (('deepseek_api',) if DS.enabled_for(seat) and not images else ())

def _bridge_ok(name):
    try:
        if name == "chatgpt":
            from veteran_desk import chatgpt_bridge as B; return B.enabled()
    except Exception: return False
    return True
def skip_reason(name):
    MD = _md()
    if MD.SKIP.get(name, 0) > time.time() or MD._CD.cooling(name): return "cooldown"
    if not _bridge_ok(name): return "signed-out"
    return None
def _call(name, fn, seat, p, imgs, wait_for_slot=False, request_id=None, workload="chat"):
    MD = _md()
    from veteran_desk import ai_readiness as READY
    why=READY.reason(name,check_busy=False) if name=="chatgpt" and wait_for_slot else READY.reason(name)
    if why:raise RuntimeError(name+": "+why)
    if name=="deepseek_api":
        if imgs:raise RuntimeError("DeepSeek API text route does not support images")
        return MD._tcall(name,lambda:fn(seat,p,request_id=request_id,workload=workload))
    if name=="chatgpt" and (wait_for_slot or workload=="learning"):
        options=dict(wait_for_slot=True) if wait_for_slot else {}
        if request_id:options['request_id']=request_id
        if workload=="learning":options['workload']=workload
        return MD._tcall(name, lambda:fn(seat,p,imgs,**options))
    return MD._tcall(name, (lambda: fn(seat, p, imgs)) if imgs and name in VISION else (lambda: fn(seat, p)))
def _fail(name, e):
    MD = _md()
    if MD._BM.is_hold(e): raise e if isinstance(e,BaseException) else MD._BM.MaintenanceHold(str(e))
    e = str(e)[:100]
    if MD.QUOTA.search(e): MD.SKIP[name] = time.time() + 900
    if "hard timeout" not in e: MD._CD.mark_fail(name, e)   # our own per-call timeout under fan-out load is not an AI outage

def order(ai, imgs=None):
    """Names to try, in order. auto = msgdesk's existing chain; explicit = picked AI first, then the auto chain.
    SuperGrok is explicit only and does not fall back to Grok or the fan-out."""
    auto = ("claude", "chatgpt", "seat", "grok")   # fablefirst_20261103: Claude first for every seat, vision or not (Brett P0 12:49 WITA 3 Nov)
    a = norm(ai)
    if a == "supergrok":
        return ["supergrok"]
    return list(auto) if a in ("auto", "super") else [a] + [n for n in auto if n != a]

def seat_order(ai, imgs=None, seat=None):
    """Extend the stable base-provider interface for authorised THF seats."""
    names = order(ai, imgs)
    if names == ["supergrok"]:
        return names
    from veteran_desk import deepseek_bridge as DS
    if DS.enabled_for(seat) and not imgs and 'deepseek_api' not in names:
        names = names + ['deepseek_api']
    return names

def chain(prompt, ai="auto", seat="desk", images=None, wait_for_slot=False, request_id=None, workload="chat"):
    """Single-AI ask with fallback. Returns dict(text, brain, errs, picked)."""
    if workload not in ("chat","learning"):raise ValueError("invalid chat workload")
    a = norm(ai)
    from veteran_desk import deepseek_bridge as DS
    if a=='deepseek_api' and seat not in DS.allowed_seats():raise PermissionError('DeepSeek API restricted by authorised seat policy')
    imgs = list(images or [])[:4]; errs = []; P = providers()
    from veteran_desk import provider_rotation as ROT,ai_readiness as READY
    pairs=[(name,_grok_max if name=="grok" and a=="grok" else P[name]) for name in seat_order(a,imgs,seat)]
    def unavailable(name):
        if name=="deepseek_api" and imgs:return "DeepSeek API text route does not support images"
        return ("cooldown" if _md()._CD.cooling(name) else None) or (READY.reason(name,check_busy=False) if name=="chatgpt" and wait_for_slot else READY.reason(name))
    # Explain unavailable explicit picks even when fair fallback succeeds before visiting the paused provider.
    if a not in ("auto","super"):
        why=unavailable(a)
        if why:errs.append(a+":"+("cooldown-skip" if why=="cooldown" else str(why)))
    pairs=ROT.reorder(pairs,unavailable,explicit=a if a!="auto" else None,request_id=request_id,channel="chat_router",preferred="deepseek_api" if a=="auto" and DS.fast_preferred() and DS.enabled_for(seat) and not imgs else None)
    for name,fn in pairs:
        if name=="deepseek_api" and imgs:
            errs.append("deepseek_api:image attachments require a vision provider");continue
        if name == a and name in ("chatgpt") and not _bridge_ok(name): errs.append(name + ":signed-out"); continue
        if _md()._CD.cooling(name): errs.append(name + ":cooldown-skip"); continue
        try:
            if name=="deepseek_api":
                out=_call(name,fn,seat,prompt,imgs,request_id=request_id,workload=workload)
            elif name=="chatgpt" and (wait_for_slot or workload=="learning"):
                options=dict(wait_for_slot=True) if wait_for_slot else {}
                if request_id:options['request_id']=request_id
                if workload=="learning":options['workload']=workload
                out=_call(name,fn,seat,prompt,imgs,**options)
            else:out=_call(name,fn,seat,prompt,imgs)
            ROT.record(name,True,channel="chat_router",request_id=request_id); _md()._CD.mark_ok(name)
            try: mdl = _md()._model_label(name)   # fablefirst_20261103: which model answered (heartbeat / proof)
            except Exception: mdl = name
            return dict(text=out, brain=name, errs=errs, picked=a, model=mdl)
        except Exception as x:
            if _md()._BM.is_hold(x):raise
            ROT.record(name,False,channel="chat_router",request_id=request_id,error=x)
            errs.append("%s:%s" % (name, str(x)[:50])); _fail(name, x)
    raise RuntimeError("; ".join(errs) or "no AI available")

SYNTH = ("SUPER POWER SYNTHESIS. {n} different AIs answered the SAME request independently (below). Merge them into ONE best answer: "
         "cherry-pick the strongest, best-evidenced points; drop weak/unsupported ones; never invent facts, prices or fills that none of them gave; "
         "PAPER only, keep every rail/gate exactly as stated in the request. {fmt}\n"
         "Explicitly flag DISAGREEMENTS between the AIs (who said what) and end with 'Contributors: {who}'.\n\n=== ORIGINAL REQUEST ===\n{req}\n\n{ans}")
FMT_JSON = "Reply with ONE JSON object in exactly the schema the original request asks for; put the merged answer, a 'Disagreements:' line and the 'Contributors:' line inside its \"reply\" field."
FMT_TEXT = "Reply in plain text: merged answer, then 'Disagreements:' (or 'Disagreements: none'), then the Contributors line."

def super_power(prompt, seat="desk", images=None, json_reply=False, synth=True, wait_for_slot=False, request_id=None, workload="chat"):
    """Fan out to every available AI in parallel, each with its own timeout; skip cooled/signed-out/busy ones; one synth pass.
    Returns dict(text, brain, contributors, skipped, answers, synth_by, errs)."""
    if workload not in ("chat","learning"):raise ValueError("invalid chat workload")
    imgs = list(images or [])[:4]; P = providers(max_effort=True); got = {}; skipped = {}; errs = []
    names=fan_names(seat,imgs)
    strength=list(STRONG)+[n for n in names if n not in STRONG]
    held = []; fx = cf.ThreadPoolExecutor(max_workers=len(names)); futs = {}
    for name in names:
        why = skip_reason(name)
        if why: skipped[name] = why; continue
        if not _SEM[name].acquire(blocking=False): skipped[name] = "busy"; continue
        options=dict(wait_for_slot=True) if wait_for_slot and name=="chatgpt" else {}
        if name=="chatgpt" and workload=="learning":
            options['workload']=workload
            if request_id:options['request_id']=request_id
        if name=='deepseek_api':
            options['workload']=workload
            if request_id:options['request_id']=str(request_id)+'-deepseek'
        held.append(name); futs[_md()._MAINT.submit(fx, _call, name, P[name], seat, prompt, imgs, **options)] = name
    try:
        for f in cf.as_completed(futs):   # each future is bounded by its own per-AI hard timeout in _tcall
            name = futs[f]
            try:
                out = f.result()
                if not str(out or "").strip(): raise RuntimeError("empty answer")
                got[name] = str(out); _md()._CD.mark_ok(name)
            except Exception as x:
                skipped[name] = "timeout" if "timeout" in str(x) else "error"; errs.append("%s:%s" % (name, str(x)[:50])); _fail(name, x)
    finally:
        for n in held: _SEM[n].release()
        fx.shutdown(wait=False)
    if not got: raise RuntimeError("super power: no AI answered (%s)" % ", ".join("%s=%s" % kv for kv in skipped.items()))
    who = [n for n in strength if n in got]
    res = dict(contributors=who, skipped=skipped, answers=got, errs=errs, synth_by=None)
    if len(got) == 1 or not synth:
        res.update(text=got[who[0]], brain="super:" + who[0], synth_by=who[0] if len(got) == 1 else None); return res
    ans = "\n\n".join("=== ANSWER FROM %s ===\n%s" % (n.upper(), got[n][:6000]) for n in who)
    sp = SYNTH.format(n=len(got), fmt=FMT_JSON if json_reply else FMT_TEXT, who=", ".join(LABEL.get(n, n) for n in who), req=prompt[:12000], ans=ans)
    for name in [n for n in strength if n in got] + [n for n in strength if n not in got]:   # strongest that answered first
        if skip_reason(name) or not _SEM[name].acquire(blocking=False): continue
        try:
            out = _call(name, P[name], seat, sp, None, **(dict(workload=workload,request_id=(str(request_id)+"-synth") if request_id else None) if name=="deepseek_api" or name=="chatgpt" and workload=="learning" else {}))
            if str(out or "").strip():
                res.update(text=str(out), brain="super:%s<-%s" % (name, "+".join(who)), synth_by=name); return res
        except Exception as x: errs.append("synth %s:%s" % (name, str(x)[:50])); _fail(name, x)
        finally: _SEM[name].release()
    # no synth AI free: code merge (labelled, never invented)
    res.update(text="\n\n".join("[%s] %s" % (LABEL.get(n, n), got[n][:1500]) for n in who), brain="super:nosynth<-" + "+".join(who)); return res

def footer(r):
    sk = ", ".join("%s (%s)" % (LABEL.get(k, k), v) for k, v in (r.get("skipped") or {}).items())
    return "\n⚡ SUPER POWER · contributors: %s%s%s" % (", ".join(LABEL.get(n, n) for n in r.get("contributors") or []),
        " · synth: %s" % LABEL.get(r["synth_by"], r["synth_by"]) if r.get("synth_by") else "", " · skipped: " + sk if sk else "")

def ask(prompt, ai="auto", seat="desk", images=None, json_reply=False):
    """Public helper for any seat/tool. Returns dict(text, brain, ...)."""
    a = resolve(ai, seat)
    if a == "super":
        r = super_power(prompt, seat, images, json_reply=json_reply)
        if not json_reply: r["text"] = r["text"].rstrip() + footer(r)
        return r
    return chain(prompt, a, seat, images)

def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="ai_router"); sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("ask"); a.add_argument("--seat", default="desk"); a.add_argument("--ai", default="auto"); a.add_argument("question")
    p = sp.add_parser("pref"); p.add_argument("seat", nargs="?"); p.add_argument("ai", nargs="?")
    sp.add_parser("status")
    o = ap.parse_args(argv)
    if o.cmd == "ask":
        r = ask(o.question, o.ai, o.seat); print(json.dumps(dict(brain=r.get("brain"), text=r.get("text"), skipped=r.get("skipped"), errs=r.get("errs")), indent=1))
    elif o.cmd == "pref":
        if o.seat and o.ai:
            MD = _md()
            if o.seat not in MD.ALL + [MD.HT]: raise SystemExit("unknown seat %s" % o.seat)
            print(json.dumps(set_pref(o.seat, o.ai), indent=1))
        else: print(json.dumps(prefs(), indent=1))
    else:
        print(json.dumps({n: skip_reason(n) or "available" for n in STRONG}, indent=1))
if __name__ == "__main__": main(sys.argv[1:])
