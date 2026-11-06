"""THF PAPER round: completed seats reach unchanged gates while other AI calls continue.
The main thread alone evaluates and submits proposals. Advisory models run after submission
in a separate bounded process. No ledger edits, new risk limits or alternative order writer.
"""
import concurrent.futures as cf,datetime as dt,hashlib,json,os,re,subprocess,sys,time
import queue as _queue, threading
from pathlib import Path

def decision_stream(fn, seats, workers, *, interval_s=0, deadline=None,
                    deferred=None, dispatch_state=None, on_batch=None,
                    clock=None, sleep=None):
    """Two paced, read-only model workers; all durable side effects stay in admit.

    Explicit daemon workers avoid ThreadPoolExecutor's unbounded shutdown join.
    A deadline fences this stream and its second look: no additional pair starts
    while an abandoned provider may still be running. The shared bridge retains
    its admission slot until that request finishes/expires, across processes.
    """
    clock=clock or time.time;sleep=sleep or time.sleep
    seats=list(seats);state=dispatch_state if dispatch_state is not None else {}
    def fallback(remaining,why):
        if deferred is None:raise TimeoutError(why)
        for seat in remaining:yield deferred(seat,why)
    if state.get("blocked"):
        yield from fallback(seats,"earlier pair still outstanding; no duplicate model submission")
        return
    pair_size=max(1,min(2,int(workers)))
    for offset in range(0,len(seats),pair_size):
        wait=max(0,float(state.get("next_start") or 0)-clock())
        if deadline and clock()+wait+65>=deadline:
            yield from fallback(seats[offset:],"staged pair deadline exhausted")
            return
        if wait:sleep(wait)
        if deadline and clock()+65>=deadline:
            yield from fallback(seats[offset:],"staged pair deadline exhausted")
            return
        started=clock();pair=seats[offset:offset+pair_size]
        state["next_start"]=started+max(0,interval_s)
        if on_batch:on_batch(pair,started)
        results=_queue.Queue();pending=dict(enumerate(pair));cancelled=state.setdefault("cancel_event",threading.Event())
        def work(index,seat):
            # The deadline/cancel fence also covers a thread delayed at startup.
            if cancelled.is_set() or (deadline and clock()+65>=deadline):
                results.put((index,None,TimeoutError("model submission deadline exhausted")))
                return
            try:results.put((index,fn(seat),None))
            except BaseException as error:results.put((index,None,error))
        for index,seat in pending.items():
            threading.Thread(target=work,args=(index,seat),daemon=True,
                             name="thf-paired-model").start()
        while pending:
            remaining=deadline-clock() if deadline else None
            if remaining is not None and remaining<=0:
                cancelled.set();state["blocked"]=True
                state["timed_out_seats"]=[s.get("seat") if isinstance(s,dict) else s for s in pending.values()]
                yield from fallback(list(pending.values())+seats[offset+pair_size:],
                                    "round deadline; late model results discarded")
                return
            try:index,result,error=results.get(timeout=min(.1,remaining) if remaining is not None else None)
            except _queue.Empty:continue
            seat=pending.pop(index)
            if deadline and clock()>=deadline:
                cancelled.set();state["blocked"]=True
                yield from fallback([seat]+list(pending.values())+seats[offset+pair_size:],
                                    "round deadline; late model results discarded")
                return
            if error:
                if isinstance(error,TimeoutError):yield from fallback([seat],str(error))
                else:raise error
            else:yield result

def candidate_options(D,seat,proposal):
    if not proposal or proposal.get("action")!="propose" or not proposal.get("ticker"):return
    yield proposal
    if not proposal.get("_brain_down"):return
    seen={proposal["ticker"]}
    for _ in range(9):
        p=D._code_fallback(seat,exclude=seen)
        if p.get("action")!="propose" or not p.get("ticker") or p["ticker"] in seen:return
        seen.add(p["ticker"]);yield p

def accept_round_ticket(seat, runid, reply, decision_id, trace):
    """Record a queued proposal id only when that reply cites a ref from this round.

    The queued id is the decision task. It is not assumed to be every closed row.
    A reply with no ref= does not attach any other trade.
    """
    from veteran_desk import lesson_trace as LT
    return LT.note_accepted_decision(seat, trace, decision_id, "%s-%s" % (runid, seat), reply)


def submit_candidate(D,records,record,committed,au,queue,queue_path):
    D.apply_marcus_limits(None,records,committed=committed)
    if not record["gate"]["passed"]:return []
    from veteran_desk import research_mandate as RM
    why=RM.arm_withheld(record.get("proposal") or {})
    if why:
        record["research_shadow"]=why
        return []
    if not queue:return [record["id"]]
    ids=D.enqueue([record],au,queue_path,ack_required=not os.environ.get("PYTEST_CURRENT_TEST"))
    if not ids and record.get("live_reject"):
        record["gate"]["passed"]=False
        record["gate"].setdefault("reasons",[]).append("live check: "+str(record["live_reject"]))
    elif not ids:
        import funder_gate as FG
        existing=(FG.load(queue_path).get("entries") or {}).get(record["id"]) or {}
        if existing and existing.get("hash")==record.get("hash"):
            record["already_queued"]=True
            return [record["id"]]
        # A duplicate identifier with different content is not permission for an alternative order.
        if existing:
            record["gate"]["passed"]=False
            record["gate"].setdefault("reasons",[]).append("queue identifier already used for another proposal")
            record["queue_identity_conflict"]=True
    return ids

def run(D,use_llm=True,queue=True,asof=None,queue_path=None,only=None):
    t0=time.time();asof=asof or D.now();c=D.P.con();S=D.seats();au=D.audusd(c,asof);runid=asof.strftime("%Y%m%d-%H%M")
    os.environ["THF_ROUND_DEADLINE"]=str(t0+D.ROUND_BUDGET_S)
    props=[s for s in S.values() if s["role"]=="proposer" and (not only or s["seat"] in only)]
    batch_gap=max(120,min(180,float(os.environ.get("THF_TRADER_BATCH_GAP_S","150"))))
    dispatch_state={"cancel_event":threading.Event()}
    state_lock=threading.Lock();run_closed=threading.Event()
    log=dict(run_id=runid,asof_utc=asof.isoformat(),paper_only=True,seats={},proposals=[],queued=[],
             execution_mode="staged_pairs_completed_seats",batch_size=2,batch_gap_s=batch_gap,
             batches=[],status="running")
    from veteran_desk import feeds
    def pack_one(s):
        c2=D.P.con()
        try:return s["seat"],D.pack(c2,s,asof)
        finally:c2.close()
    packs={s["seat"]:{} for s in props}
    prepared_seats=set()
    def go(s,second=False):
        seat=s["seat"];deadline=t0+D.ROUND_BUDGET_S
        cancel=dispatch_state["cancel_event"]
        def alive():return not run_closed.is_set() and not cancel.is_set() and time.time()<deadline
        if not alive():return seat,None,"round cancelled before preparation"
        if seat not in prepared_seats:
            _,prepared=pack_one(s)
            with state_lock:
                if not alive():return seat,None,"round cancelled after preparation"
                packs[seat]=prepared;prepared_seats.add(seat)
        if not use_llm:return seat,None,"llm disabled"
        pk=dict(packs[seat])
        if second:
            ranked=pk.get("candidates_ranked") or []
            if len(ranked)>10:pk["candidates_ranked"]=ranked[10:40]
            pk["rescan_note"]="SECOND LOOK at market open. Recheck unseen candidates under the same rules; standing aside is allowed."
        from veteran_desk import llm_backend as LB
        previous=(getattr(LB.LAST,"paired_trader",False),getattr(LB.LAST,"request_deadline",None),getattr(LB.LAST,"cancel_event",None))
        try:
            prompt=D.prop_prompt(s,pk)
            shadow_root=os.environ.get("THF_SHADOW_ROOT")
            if shadow_root or not os.environ.get("PYTEST_CURRENT_TEST"):
                try:
                    from veteran_desk import thf_shadow_sampler as SH
                    SH.enqueue_production_prompt(shadow_root or D.BASE, seat, prompt, asof.isoformat(), runid+"-"+seat, role=s.get("role") or "trader")
                except Exception:
                    pass
            if not alive() or time.time()+65>=deadline:return seat,None,"round deadline before model submission"
            LB.LAST.paired_trader=True;LB.LAST.request_deadline=deadline;LB.LAST.cancel_event=cancel
            txt=D.llm(s["engine"],prompt)
            if not alive():return seat,None,"late model result discarded"
            j=D.parse_json(txt)
            if not alive():return seat,None,"late parsed result discarded"
        except Exception as error:txt,j="llm_err "+type(error).__name__,None
        finally:LB.LAST.paired_trader,LB.LAST.request_deadline,LB.LAST.cancel_event=previous
        return seat,j,(txt or "")[:400],getattr(LB.LAST,"model",None)
    used=0;out=log["proposals"];seen=set();committed=set();flat=[]
    def persist():
        if D.ENGINE_WAKE:
            log["engine_wake_id"]=D.ENGINE_WAKE
            D._wj(os.path.join(D.BASE,"state","vd_engine","runs",D.ENGINE_WAKE+".json"),log)
        D._wj(os.path.join(D.SD,"runs",runid+("-eng" if D.ENGINE_WAKE else "")+".json"),log)
    def admit(result,second=False):
        nonlocal used
        seat,j,raw=result[:3];model=result[3] if len(result)>3 else None
        entry=log["seats"].setdefault(seat,{})
        from veteran_desk import research_mandate as RM
        original=j if isinstance(j, dict) else {"empty_output": True, "action": None}
        entry["research"]=RM.public_view(RM.record_round(seat, original, asof, "thf_round.admit", os.environ.get("THF_RESEARCH_LOG")))
        if not os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("THF_LESSON_TRACE"):
            try:
                from veteran_desk import seat_brain as brain
                task = runid + "-" + seat
                brain.prompt_block(seat, 1100, task_id=task)
                if raw: brain.after_chat(seat, "round", raw, brain=model, task_id=task)
            except Exception:
                pass
        # Only the main-thread admitted result may update memory, heartbeat or
        # mail. A timed-out daemon worker cannot do any of these after fallback.
        if use_llm:
            if j is None:
                j=D._code_fallback(seat);raw=(raw or "")+" [brain_down -> code fallback]"
            from veteran_desk import llm_backend as LB
            previous_model=getattr(LB.LAST,"model",None);LB.LAST.model=model
            try:D._seat_heartbeat(seat,j);D._after_round(seat,j)
            finally:LB.LAST.model=previous_model
            if j and not j.get("_brain_down") and not os.environ.get("PYTEST_CURRENT_TEST"):
                try:
                    from veteran_desk import seat_mail
                    seat_mail.ack(seat,[row.get("id") for row in packs[seat].get("seat_inbox",[])[:2]],"desk")
                except Exception:pass

        entry.update(trader=D.name(S,seat),engine=D.ran_engine(S[seat]["engine"]),reason=(j or {}).get("thesis") or (j or {}).get("reason") or (j or {}).get("why_not_choppy"),raw_ok=bool(j),action=(j or {}).get("action"),raw_head=None if j else raw,scan=packs[seat].get("scan"),graded=(j or {}).get("graded"),ranked=(j or {}).get("ranked"),best_long=(j or {}).get("best_long"),best_short=(j or {}).get("best_short"))
        if second:entry["rescan"]=dict(action=(j or {}).get("action"),ticker=(j or {}).get("ticker"))
        try:
            from veteran_desk import thf_review as review
            if review.enabled():review.record_plan(seat,D.ENGINE_WAKE or runid,j,D.name(S,seat))
        except Exception:pass
        accepted=False
        for p in candidate_options(D,seat,j):
            if isinstance(p,dict) and not os.environ.get("PYTEST_CURRENT_TEST"):p["_ll_required"]=True
            cap=D._engine_cap(seat,p)
            if cap:entry["engine_grade_cap"]=cap
            record,used=gate_candidate(D,c,seat,p,asof,used,out,au,packs,S,seen)
            ids=submit_candidate(D,out,record,committed,au,queue,queue_path)
            entry.setdefault("attempts",[]).append(dict(ticker=p["ticker"],passed=record["gate"]["passed"],reasons=record["gate"].get("reasons") or [],live_reject=record.get("live_reject"),queued=bool(ids)))
            if ids:
                committed.update(ids)
                if queue:log["queued"].extend(ids)
                entry["selected"]=p["ticker"];accepted=True
                try:
                    trace = os.environ.get("THF_LESSON_TRACE")
                    if trace or not os.environ.get("PYTEST_CURRENT_TEST"):
                        dest = trace or os.path.join(D.BASE, "state", "thf", "lesson_trace.jsonl")
                        for decision_id in ids:
                            accept_round_ticket(seat, runid, raw or "", decision_id, dest)
                except Exception:
                    pass
                persist() # A later model stall or process timeout cannot lose submitted work.
                break
            persist()
            if record.get("queue_identity_conflict"):break
        if not accepted and not second:flat.append(S[seat])
        persist()
    try:
        props=sorted(props,key=lambda s:-len(json.dumps(packs.get(s["seat"]) or {},default=str)))
        with D.P.bar_cache(),feeds.round_cache():
            def dispatched(pair,started,second=False):
                log["batches"].append(dict(seats=[s["seat"] for s in pair],
                                           started_utc=dt.datetime.fromtimestamp(started,dt.timezone.utc).isoformat(),
                                           started_epoch=started,second_look=second))
                persist()
            gap=batch_gap if use_llm else 0
            for result in decision_stream(go,props,D.SEAT_PARALLEL,interval_s=gap,
                                          deadline=t0+D.ROUND_BUDGET_S,dispatch_state=dispatch_state,
                                          deferred=lambda s,why:(s["seat"],None,why),on_batch=dispatched):admit(result)
            if use_llm and D._open_run(asof):
                for result in decision_stream(lambda s:go(s,True),flat,D.SEAT_PARALLEL,
                                              interval_s=gap,deadline=t0+D.ROUND_BUDGET_S,
                                              dispatch_state=dispatch_state,
                                              deferred=lambda s,why:(s["seat"],None,why),
                                              on_batch=lambda pair,started:dispatched(pair,started,True)):admit(result,True)
        log["head_of_desk"]=D.head_of_desk(S,out,False,committed=committed)
        log["advisory"]=start_advisory(D,log)
        if use_llm and os.environ.get("VET_SOUNDING_BOARD","1")=="1":
            try:
                from veteran_desk import learning
                log["sounding_board"]=learning.critique_async([r for r in out if r["id"] in committed],runid)
            except Exception as error:log["sounding_board_error"]=type(error).__name__
        try:
            vq=feeds.vix_live() or {}
            log.update(vix_last=vq.get("last"),vix_asof=vq.get("asof"),vix_src=vq.get("src"),vix_stale=bool(vq.get("delayed")) if vq else True,vix_stale_why=vq.get("stale_why"))
        except Exception as error:log.update(vix_last=None,vix_asof=None,vix_error=type(error).__name__)
        log["status"]="finished"
        persist()
        if not D.ENGINE_WAKE:D._wj(os.path.join(D.SD,"latest.json"),log)
        try:D.drought(D.SD)
        except Exception:pass
        D.track(out);sc=D.score();D.sheet(log)
        usage=list(D.USAGE)
        D.health(dict(model_usage=dict(calls=len(usage),ok=sum(u["ok"] for u in usage),timeouts=sum(u["timeout"] for u in usage),rate_limited=sum(u["rate_limited"] for u in usage),by_engine={e:sum(u["engine"]==e for u in usage) for e in ("codex",)},avg_secs=round(sum(u["secs"] for u in usage)/len(usage),1) if usage else None),ok=True,last_run_utc=D.now().isoformat(),run_id=runid,secs=round(time.time()-t0),llm=use_llm,seats_ok=sum(v["raw_ok"] for v in log["seats"].values()),seats_total=len(props),proposals=len(out),passed=sum(r["id"] in committed for r in out)))
        if not D.ENGINE_WAKE:D.card(log,sc)
        return log
    finally:
        with state_lock:
            run_closed.set();dispatch_state["cancel_event"].set()
        c.close()

def start_advisory(D,log):
    if os.environ.get("PYTEST_CURRENT_TEST"):return dict(status="test_disabled",blocks_trade=False)
    directory=Path(D.BASE)/"state/thf/advisory_requests";directory.mkdir(parents=True,exist_ok=True)
    path=directory/(log["run_id"]+".json")
    selected=[r for r in log["proposals"] if r.get("id") in set(log.get("queued") or [])]
    D._wj(str(path),dict(run_id=log["run_id"],asof_utc=log["asof_utc"],advisory_only=True,records=selected,paper_only=True))
    env=dict(os.environ,THF_ROUND_DEADLINE=str(time.time()+240))
    with open(os.devnull,"w") as sink:
        child=subprocess.Popen([sys.executable,"-m","veteran_desk.thf_round","advisory",str(path)],cwd=D.BASE,env=env,stdout=sink,stderr=sink,start_new_session=True)
    return dict(status="detached",pid=child.pid,blocks_trade=False,path=str(path))

def advisory(path):
    from veteran_desk import desk as D
    root=(Path(D.BASE)/"state/thf/advisory_requests").resolve();target=Path(path).resolve()
    if target.parent!=root or not re.fullmatch(r"\d{8}-\d{4}\.json",target.name):raise ValueError("Invalid advisory request")
    payload=json.loads(target.read_text());records=payload.get("records") or [];out=[];budget=D.SUB_AGENTS_PER_RUN
    for rec in records:
        if time.time()>float(os.environ.get("THF_ROUND_DEADLINE") or 0):break
        p=rec["proposal"];f=rec.get("features") or {}
        def ask(prompt):
            try:return D.parse_json(D.llm("codex",prompt, min(60,D.SUB_TIMEOUT)))
            except Exception:return None
        chal=ask(D.chal_prompt(f));da=ask(D.da_prompt(p,f,[r["proposal"] for r in records if r is not rec]))
        tasks=[("research","Research analyst: list the three important facts for and against."),
               ("chart_read","Chart reader: describe trend, support/resistance and chase risk."),
               ("filings_dig","Filings analyst: identify event risk, or say none in data."),
               ("second_opinion","Second-opinion trader: would you take this exact trade and why?"),
               ("news_check","News checker: do supplied headlines threaten this trade?")]
        notes=[]
        for role,prompt in tasks[:min(D.SUB_AGENTS_PER_TRADE,budget)]:
            if time.time()>float(os.environ.get("THF_ROUND_DEADLINE") or 0):break
            try:note=D.llm("codex",prompt+" ADVISORY only. Cannot place, arm or change a trade. Use point-in-time DATA only; under 80 words.\nPROPOSAL:"+json.dumps(p)+"\nDATA:"+json.dumps(f),min(60,D.SUB_TIMEOUT))
            except Exception as error:note="unavailable: "+type(error).__name__
            notes.append(dict(agent=role,note=(note or "")[:600]));budget-=1
        out.append(dict(id=rec["id"],seat=rec["seat"],challenger=chal,devils_advocate=da,sub_agents=notes))
    result=dict(run_id=payload["run_id"],asof_utc=payload["asof_utc"],completed_utc=D.now().isoformat(),advisory_only=True,blocks_trade=False,notes=out)
    D._wj(str(target.with_name(target.stem+"-results.json")),result)
    return result

def gate_candidate(D,c,seat,p,asof,used,out,au,packs,S,seen):
    risk_gate=D.risk_gate;_shadow_gate=D._shadow_gate;size=D.size;name=D.name
    ran_engine=D.ran_engine;now=D.now;P=D.P
    runid=asof.strftime("%Y%m%d-%H%M")
    f=D.feats(c,p["ticker"],asof) if not P.banned(p["ticker"]) else None
    chal=da=None;other="advisory_pending";subs=[]
    g = risk_gate(dict(p, _seat=seat), f, chal, da, asof, used, others=[dict(ticker=r["proposal"]["ticker"], side=r["proposal"].get("side"), risk_aud=r["gate"].get("risk_aud") or 0, us_beta_usd=r["gate"].get("us_beta_usd") or 0) for r in out if r["gate"]["passed"]], au=au)
    _shadow_gate(runid, seat, p, g)   # tfo_seatpipe_8_20261030: SHADOW ONLY - logs what a softer history rule / 1.5% floor would pass; g is never changed
    if p["ticker"] in seen: g["passed"] = False; g["reasons"].append("duplicate ticker this run - one owner per trade")
    if f and f["venue"] == "CRYPTO" and g["passed"]:   # gate v2 law: missing data cuts size, never blocks; data showing a thin coin still blocks
        liq = ((packs.get(seat, {}).get("live_feeds", {}).get("kraken_spot") or {}).get("data") or {})
        v = [x for k, x in liq.items() if p["ticker"].split(":")[-1][:3] in k.replace("XXBT", "XBT").replace("XETH", "ETH")]; thin = False
        if v:   # Brett 2026-10-29 13:02 WITA: floor A$500k 24h traded value (was US$20m), converted via book.aud
            from veteran_desk import book as BK, feeds as FDQ
            try: v24 = BK.aud(p["ticker"], float(v[0].get("vol24h_usd") or 0), au)
            except Exception: v24 = None
            thin = (v24 < FDQ.MIN_24H_AUD) if v24 is not None else not v[0].get("liquid")
        if not v:   # 2026-10-29 kailiq: feeds row missing -> use the kraken_sweep 24h figure (same A$500k floor)
            sr = D.sweep_row_for(p["ticker"])
            if sr and sr.get("vol24h_aud") is not None:
                from veteran_desk import feeds as FDQ
                v = [sr]; thin = float(sr["vol24h_aud"]) < FDQ.MIN_24H_AUD
        try:
            from veteran_desk import small_coin as _SC0; _sct = _SC0.is_small(p["ticker"])
        except Exception: _sct = False
        if v and thin and not _sct: g["passed"] = False; g["reasons"].append("thin coin (Kraken 24h < A$500k) - pass")   # tfo_smallcoin_20261101: tier coins use the 20d A$100k-500k band
        elif not v:
            g.setdefault("warnings", []).append("no Kraken liquidity data this run - size x0.5"); g["size_mult"] = round(max((g.get("size_mult") or 1.0) * .5, .25), 3)
            from veteran_desk import book as BK
            rp = BK.risk_plan(g.get("seat_equity_aud") or BK.seat_equity(seat), p.get("grade"), g.get("chal_agree"), g["size_mult"], floor=BK.RISK_CRYPTO_MIN)   # railfix 2026-10-29: crypto seat floor 0.5%; below floor -> reject, never lift
            g["risk_aud"] = round(rp["risk_aud"]); g["risk_pct"] = rp["pct"]
            if rp.get("reject"): g["passed"] = False; g["reasons"].append(rp["reject"])   # kairails: below 0.5% after the x0.5 cut -> below_crypto_floor
            elif f and float(p.get("entry") or 0) > 0:   # kairails: re-apply the A$50k/A$100k A+ clip cap after the resize
                try:
                    k2 = BK.aud_per_unit(p["ticker"], au); r2, _q, rj = BK.crypto_clip(g["risk_aud"], abs(float(p["entry"]) - float(p["stop"])) * k2, float(p["entry"]) * k2, g.get("seat_equity_aud") or BK.seat_equity(seat), p.get("grade"))
                    g["risk_aud"] = round(r2)
                    if rj: g["passed"] = False; g["reasons"].append(rj)
                except Exception: pass
            if rp.get("reject"): g["passed"] = False; g["reasons"].append(rp["reject"])
            if rp["warning"]: g["warnings"].append(rp["warning"])
    if p.get("test"): p = dict(p, test=None)   # TEST tag is operator-only (seat_cli); never honoured from an engine round
    try:   # tfo_smallcoin_20261101: final small-coin pass after every resize (1/4 risk, 2% ADV cap, spread, limit-only, PF_ shorts, cost_R)
        from veteran_desk import small_coin as _SC1, gate_v2 as _G2s; _SC1.apply_gate(p, g, au, rr_min=_G2s.TH["rr_min"])
    except Exception as _x: g["passed"] = False; g.setdefault("reasons", []).append("small_coin check error %s - fail closed" % type(_x).__name__)
    seen.add(p["ticker"]); sz = size(p, f, g.get("risk_aud", 500), au) if g["passed"] else None
    if sz and not sz["passes_doubled_cost"]: g["passed"] = False; g["reasons"].append("fails doubled-cost hurdle")
    if g["passed"]: used += sz["risk_aud"]
    if g.get("quote_asof") and not p.get("quote_asof"):
        p = dict(p, quote_asof=g.get("quote_asof"), quote_last=g.get("quote_last"), quote_src=g.get("quote_src"))
    rec = dict(id=f"VET-{re.sub(r'[^A-Z0-9]', '', p['ticker'].upper())}-{runid}-{seat}", seat=seat, trader=name(S, seat), sub_agents=subs, engine=ran_engine(S[seat]["engine"]), proposal=p,
               challenger=dict(engine=other, trader=name(S, "blind_challenger"), view=chal), devils_advocate=da, gate=g, sizing=sz, features=f or dict(sym=p["ticker"], venue=P.venue_of(p["ticker"]), pit_missing=True),
               universe_bias=P.VENUE_BIAS.get(P.venue_of(p["ticker"]), ("?",))[0], created_utc=now().isoformat(), info_only=True, seat_reply_ok=True)
    rec["hash"] = hashlib.sha256(json.dumps([p, sz], sort_keys=True, default=str).encode()).hexdigest()[:16]; out.append(rec)
    return rec,used

if __name__=="__main__":
    if len(sys.argv)==3 and sys.argv[1]=="advisory":advisory(sys.argv[2])
    else:raise SystemExit("advisory REQUEST_FILE")
