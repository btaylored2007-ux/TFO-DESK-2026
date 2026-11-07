"""Durable THF chat tasks. Dashboard writes only its existing message queue; the THF worker owns state."""
import datetime as dt,fcntl,hashlib,json,math,os,pathlib,re,secrets,time
from veteran_desk import thf_funder as TF,seat_registry as SR,seat_brain as SB,thf_prompt_catalog as PC,thf_competition as COMP
ROOT=pathlib.Path("/opt/tfo/live/thf");DIR=ROOT/"state/thf/chat_tasks";STATE=DIR/"tasks.json";LOCK=DIR/"tasks.lock";HEART=DIR/"worker.json";LIVE_DIR=DIR
VERSION="thf-commands-20261104-v2";WITA=dt.timezone(dt.timedelta(hours=8));_last_brain=0
class Unavailable(RuntimeError):pass
def load():
    if not STATE.exists():return dict(jobs={},requests={})
    try:
        data=json.loads(STATE.read_text())
        if not isinstance(data,dict) or not isinstance(data.get("jobs"),dict) or not isinstance(data.get("requests"),dict):raise ValueError()
        return data
    except (OSError,ValueError):raise RuntimeError("Task storage needs repair; the original file has been preserved")
def command_mode(msg):return (msg.get("command") or {}).get("mode") if isinstance(msg.get("command"),dict) else None
def traders():
    return {sid for sid,row in SR.load().items() if row.get("role")=="proposer"}
def targets(scope="current",seat=None):
    valid=set(TF.SEAT_IDS)-{"risk_gate"}
    if scope=="all" or (scope=="current" and seat in ("ALL",None,"")):return sorted(valid-{"fixer"})
    if scope=="traders":return sorted(valid & traders())
    if scope=="staff":return sorted(valid-traders()-{"fixer"})
    if scope!="current" or seat not in valid:raise ValueError("Choose a trader or staff member")
    return [seat]
def iso(epoch):return dt.datetime.fromtimestamp(float(epoch),dt.timezone.utc).isoformat()
def wita(epoch):return dt.datetime.fromtimestamp(float(epoch),WITA).strftime("%d %b %H:%M WITA") if epoch is not None else None
def parse_at(value):
    try:
        stamp=dt.datetime.fromisoformat(str(value).replace("Z","+00:00"))
        return stamp.replace(tzinfo=WITA).timestamp() if stamp.tzinfo is None else stamp.timestamp()
    except (TypeError,ValueError):raise ValueError("Choose a valid time (Singapore/Bali time, UTC+8)")
def validate(body,now=None):
    now=time.time() if now is None else float(now)
    if not isinstance(body,dict):raise ValueError("Expected a task object")
    op=str(body.get("op") or "create")
    if op not in ("create","pause","resume","cancel"):raise ValueError("Unknown task action")
    request=str(body.get("request_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{7,100}",request):raise ValueError("Missing request identity; retry from chat")
    if op!="create":
        task=str(body.get("task_id") or "")
        if not re.fullmatch(r"task-[a-z0-9_-]{4,80}",task):raise ValueError("Unknown task")
        return dict(op=op,request_id=request,task_id=task)
    text=str(body.get("text") or "").strip()
    if not text or len(text)>1800:raise ValueError("Write a prompt of 1-1800 characters")
    mode=str(body.get("mode") or "ask");scope=str(body.get("scope") or "current");seat=body.get("seat") or "ALL"
    if mode not in ("ask","push","learn"):raise ValueError("Choose Ask, Push or Learning review")
    to=targets(scope,seat)
    if not to:raise ValueError("No recipients in this group")
    ai=str(body.get("ai") or "auto")
    if ai not in ("auto","claude","chatgpt","grok","super"):raise ValueError("Unknown AI")
    schedule=body.get("schedule") or {};kind=str(schedule.get("kind") or "now")
    if kind not in ("now","once","repeat"):raise ValueError("Choose Now, Once or Repeat")
    start=now if not schedule.get("at") else parse_at(schedule["at"])
    if kind=="once" and (not schedule.get("at") or start<now+15):raise ValueError("A one-off task needs a future time")
    if start>now+366*86400:raise ValueError("Choose a start within the next year")
    every=None
    if kind=="repeat":
        try:every=int(schedule.get("every_minutes") or 1440)
        except (TypeError,ValueError):raise ValueError("Repeat interval must be a whole number of minutes")
        if not 15<=every<=10080:raise ValueError("Repeat every 15 minutes to 7 days")
        start=max(start,now)
    clean=dict(op=op,request_id=request,task_id="task-"+hashlib.sha256(request.encode()).hexdigest()[:20],
               text=text,label=str(body.get("label") or text.splitlines()[0])[:80],mode=mode,scope=scope,seat=seat,
               ai=ai,preset=str(body.get("preset") or "")[:80],schedule=dict(kind=kind,start=start,every_minutes=every),targets=to)
    clean["images"]=TF.img_validate(body.get("images"))
    clean["_body_hash"]=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
    return clean
def ready(now=None):
    d=COMP.read(HEART);now=time.time() if now is None else float(now)
    try:return d.get("version")==VERSION and 0<=now-float(d.get("epoch") or 0)<45
    except (TypeError,ValueError):return False
def submit(body):
    clean=validate(body)
    if not ready():raise Unavailable("Scheduled tasks are starting. Your text is kept; try again shortly.")
    summary=("Schedule: "+clean.get("label","")) if clean["op"]=="create" else clean["op"].capitalize()+" scheduled task"
    row=TF.post("task",summary,clean.get("seat") if clean.get("seat")!="ALL" else None,
                command=dict(mode="control",control=clean),dedupe_key="control:"+clean["request_id"])
    return dict(ok=True,id=row["id"],task_id=clean.get("task_id"),state="queued",note="Saved. The task worker applies it within a few seconds.")
class Lock:
    def __enter__(self):
        DIR.mkdir(parents=True,exist_ok=True);self.stream=LOCK.open("a");fcntl.flock(self.stream,fcntl.LOCK_EX);return self
    def __exit__(self,*args):fcntl.flock(self.stream,fcntl.LOCK_UN);self.stream.close()

def _check_control(control):
    if not isinstance(control,dict) or control.get("op") not in ("create","pause","resume","cancel"):raise ValueError("Invalid task control")
    if not re.fullmatch(r"task-[a-z0-9_-]{4,80}",str(control.get("task_id") or "")):raise ValueError("Invalid task identity")
    if control["op"]=="create":
        if control.get("mode") not in ("ask","push","learn") or control.get("ai") not in ("auto","chatgpt","claude","grok","super"):raise ValueError("Invalid task mode")
        if not isinstance(control.get("text"),str) or not 1<=len(control["text"])<=1800:raise ValueError("Invalid task prompt")
        targets(control.get("scope"),control.get("seat"))
        schedule=control.get("schedule") or {};kind=schedule.get("kind")
        if kind not in ("now","once","repeat"):raise ValueError("Invalid task schedule")
        start=COMP.number(schedule.get("start"))
        if start is None or start<0:raise ValueError("Invalid task time")
        if kind=="repeat" and not 15<=int(schedule.get("every_minutes") or 0)<=10080:raise ValueError("Invalid repeat interval")
        TF.img_validate(control.get("images"))
    return control

def consume(message,now=None):
    now=time.time() if now is None else float(now);control=(message.get("command") or {}).get("control") or {}
    control=_check_control(control)
    with Lock():
        state=load();state.setdefault("jobs",{});state.setdefault("requests",{})
        if message["id"] in state["requests"]:return state["requests"][message["id"]]
        op=control.get("op");tid=control.get("task_id");jobs=state["jobs"]
        if op=="create":
            if tid not in jobs and sum(j.get("status")=="active" for j in jobs.values())>=100:raise ValueError("100 active tasks already exist; pause or cancel one")
            if tid not in jobs:
                schedule=control.get("schedule") or {}
                targets(control.get("scope"),control.get("seat"))
                jobs[tid]=dict(control,id=tid,status="active",created_utc=iso(now),next_run=float(schedule.get("start") or now),runs=0,last_error=None)
            job=jobs[tid];text=("Queued now: " if job["schedule"]["kind"]=="now" else "Scheduled: ")+job["label"]+"; next "+wita(job["next_run"])
        elif op in ("pause","resume","cancel"):
            if tid not in jobs:raise ValueError("Task no longer exists")
            job=jobs[tid]
            if op=="resume" and job.get("status") in ("cancelled","complete","sent"):raise ValueError("Create a new task to run a completed or cancelled request")
            job["status"]={"pause":"paused","resume":"active","cancel":"cancelled"}[op]
            if op=="resume":job["next_run"]=max(now,float(job.get("next_run") or now))
            text=op.capitalize()+"d: "+job["label"]
        else:raise ValueError("Unknown task action")
        result=dict(ok=True,task_id=tid,text=text,status=job["status"])
        state["requests"][message["id"]]=result
        if len(state["requests"])>1000:state["requests"]=dict(list(state["requests"].items())[-1000:])
        COMP.atomic(STATE,state);return result
def _default_jobs(state,now):
    jobs=state.setdefault("jobs",{})
    next_day=dt.datetime.fromtimestamp(now,WITA).replace(hour=21,minute=30,second=0,microsecond=0)
    if next_day.timestamp()<=now:next_day+=dt.timedelta(days=1)
    fixer_day=dt.datetime.fromtimestamp(now,WITA).replace(hour=21,minute=45,second=0,microsecond=0)
    if fixer_day.timestamp()<=now:fixer_day+=dt.timedelta(days=1)
    defaults=[("task-daily-learning","Daily brain review","daily_learn","learn","all","ALL",1440,next_day.timestamp()),
              ("task-competition-coach","THF competition review","coach","ask","current","head_of_desk",240,now+900),
              ("task-fixer-learning","Fixer learning review","daily_learn","learn","current","fixer",1440,fixer_day.timestamp())]
    for tid,label,preset,mode,scope,seat,every,start in defaults:
        if tid in jobs:continue
        template=PC.get(preset)
        jobs[tid]=dict(id=tid,task_id=tid,label=label,preset=preset,mode=mode,scope=scope,seat=seat,ai="auto",text=template["text"],
                       schedule=dict(kind="repeat",start=start,every_minutes=every),next_run=start,status="active",
                       created_utc=iso(now),runs=0,last_error=None,configured_by="THF owner request, 4 Nov 2026")
def ensure_brains():
    roster=SR.load();roster=dict(roster)
    roster.setdefault("fixer",dict(trader_name="Fixer",role="executor",description="Guarded THF code executor; Simon owns planning, only verified deployment receipts prove changes.",can_arm=False,owns=["guarded deployments"]))
    errors={}
    for seat in sorted(set(TF.SEAT_IDS)-{"risk_gate"}):
        try:SB.ensure(seat,S=roster)
        except Exception as error:errors[seat]=str(error)[:160]
    SB.lessons_from_book()
    COMP.atomic(DIR/"brains_health.json",dict(utc=iso(time.time()),errors=errors,ready=len(TF.SEAT_IDS-{"risk_gate"})-len(errors)))
def tick(now=None):
    global _last_brain
    now=time.time() if now is None else float(now)
    if os.environ.get("PYTEST_CURRENT_TEST") and DIR==LIVE_DIR:return []
    heart=COMP.read(HEART)
    if now-float(heart.get("epoch") or 0)>10:COMP.atomic(HEART,dict(version=VERSION,epoch=now,utc=iso(now),learning_release="dot-learning-persistence-20261105"))
    try:COMP.tick(now)
    except Exception as error:COMP.atomic(DIR/"competition_error.json",dict(utc=iso(now),error=str(error)[:250]))
    if now-_last_brain>=300:
        try:ensure_brains()
        except Exception as error:COMP.atomic(DIR/"brain_error.json",dict(utc=iso(now),error=str(error)[:250]))
        _last_brain=now
    emitted=[]
    with Lock():
        state=load();state.setdefault("requests",{});before=set(state.get("jobs") or {});_default_jobs(state,now)
        if set(state["jobs"])!=before:COMP.atomic(STATE,state)
        due=[j for j in state["jobs"].values() if j.get("status")=="active" and float(j.get("next_run") or 0)<=now]
        for job in sorted(due,key=lambda item:float(item["next_run"]))[:5]:
            pending=job.get("inflight")
            if not pending:
                schedule=job["schedule"];start=float(schedule["start"]);interval=60*int(schedule.get("every_minutes") or 1)
                ordinal=max(0,int((now-start)//interval)) if schedule["kind"]=="repeat" else 0
                pending=dict(ordinal=ordinal,targets=targets(job["scope"],job["seat"]),messages={},started_utc=iso(now))
                job["inflight"]=pending;COMP.atomic(STATE,state)
            try:
                recipients=pending["targets"]
                # ALL is one broadcast; staff/trader groups use explicit seat deliveries.
                destinations=[None] if job["scope"]=="all" or (job["scope"]=="current" and job["seat"]=="ALL") else recipients
                for seat in destinations:
                    key=seat or "ALL"
                    if key in pending["messages"]:continue
                    prefix="PUSH: " if job["mode"]=="push" else "LEARNING REVIEW: " if job["mode"]=="learn" else ""
                    row=TF.post("all" if seat is None else "seat",prefix+job["text"],seat,ai=job["ai"],images=job.get("images"),
                                command=dict(mode=job["mode"],preset=job.get("preset"),task_id=job["id"],occurrence=pending["ordinal"]),
                                dedupe_key="%s:%s:%s"%(job["id"],pending["ordinal"],key))
                    pending["messages"][key]=row["id"];COMP.atomic(STATE,state)
                job["last_run"]=dict(utc=iso(now),messages=list(pending["messages"].values()),targets=len(recipients),occurrence=pending["ordinal"])
                job["runs"]=int(job.get("runs") or 0)+1;job["last_error"]=None;job.pop("inflight",None)
                if job["schedule"]["kind"]=="repeat":
                    interval=60*int(job["schedule"]["every_minutes"]);anchor=float(job["schedule"]["start"])
                    job["next_run"]=anchor+(int((now-anchor)//interval)+1)*interval
                else:job["status"]="sent";job["next_run"]=None
                emitted.append(job["id"])
            except Exception as error:
                job["last_error"]=str(error)[:250];job["next_run"]=now+30
            COMP.atomic(STATE,state)
    return emitted
def listing(seat="ALL"):
    state=load();jobs=list((state.get("jobs") or {}).values())
    if seat!="ALL":jobs=[job for job in jobs if seat in targets(job["scope"],job["seat"])]
    messages={row["id"]:row for row in TF.load(500)}
    retries=COMP.read(DIR.parent/"chat_retry.json")
    out=[]
    for job in sorted(jobs,key=lambda j:j.get("created_utc",""),reverse=True)[:30]:
        item={k:job.get(k) for k in ("id","label","status","mode","scope","seat","schedule","runs","last_error","last_run")}
        item["next_wita"]=wita(job.get("next_run"));item["delivery"]={}
        item["runs_meaning"]="emitted occurrences, not completed AI reviews"
        item["learning"]=[dict(message_id=(row.get("message") or {}).get("id"),seat=row.get("seat"),
                               status=row.get("status"),attempts=row.get("attempts",0),deadline=row.get("deadline"),
                               next_attempt=row.get("due"),result_available=isinstance(row.get("result"),dict),
                               error=row.get("err")) for row in retries.values() if isinstance(row,dict) and row.get("learning")
                          and ((row.get("message") or {}).get("command") or {}).get("task_id")==job["id"]]
        for mid in (job.get("last_run") or {}).get("messages") or []:
            for row in (messages.get(mid,{}).get("delivery") or {}).values():
                states={row.get("state") or "received",row.get("execution_state"),row.get("work_state")}
                for name in states-{None}:item["delivery"][name]=item["delivery"].get(name,0)+1
        out.append(item)
    return dict(tasks=out,ready=ready(),timezone="Asia/Makassar (UTC+8)",worker=COMP.read(HEART))
def tools(seat="ALL"):
    if seat!="ALL" and seat not in TF.SEAT_IDS:raise ValueError("Unknown trader or staff member")
    role="all" if seat=="ALL" else "trader" if seat in traders() else "admin"
    if seat=="ALL":
        brain=dict(separate=True,seats=len(TF.SEAT_IDS-{"risk_gate"}),initialised=sum((pathlib.Path(SB.DIR)/sid/"profile.json").is_file() for sid in TF.SEAT_IDS-{"risk_gate"}))
    else:
        card=SB.card(seat);brain=dict(seat=seat,name=card["name"],**card["brain"],separate=True)
    return dict(catalog=PC.catalog(role),brain=brain,**listing(seat))
def learning_prompt(seat,msg):
    mode=command_mode(msg);target=msg.get("_learning_for") or seat
    task=msg.get("id");task=str(task) if str(task or "").strip() else None
    try:peer=COMP.query_context(msg.get("text"))
    except Exception:peer="Peer history unavailable; do not invent evidence.\n"
    if mode=="ask":
        return peer+("\nASK: give a read-only answer. Return no trading actions, ht_actions, work_request or lab_idea. "
                "You may request information from colleagues; do not change code or orders.\n"+
                (SB.prompt_block(target,task_id=task) if target!=seat else ""))
    if mode!="learn":return peer
    return peer+("\nOWN-BRAIN REVIEW: review "+target+"'s own records. Produce observations and a testable hypothesis, not trading or code actions. "
            "Do not return actions, ht_actions or work_request. Include JSON key brain_review={lesson:<one short lesson>,hypothesis:<one testable hypothesis>,next_check:<specific check>,evidence:[source trade refs]}. "
            "Never change risk rules or treat an untested hypothesis as fact. "+SB.prompt_block(target,task_id=task)+"\n")
def learning_saved(seat,mid):
    """Exact durable identity, including records older than the prompt's recent tail."""
    ref="learning:"+mid+":"+seat
    path=pathlib.Path(SB._d(seat))/"lessons.jsonl"
    try:
        with path.open() as stream:
            for line in stream:
                try: row=json.loads(line)
                except (ValueError,TypeError): continue
                if isinstance(row,dict) and row.get("ref")==ref and row.get("kind")=="review":return row
    except FileNotFoundError: pass
    return None

def record_learning(seat,result,mid,model):
    existing=learning_saved(seat,mid)
    if existing:return existing
    review=result.get("brain_review") if isinstance(result,dict) else None
    if not isinstance(review,dict):return None
    lesson=str(review.get("lesson") or "").strip();hypothesis=str(review.get("hypothesis") or "").strip();check=str(review.get("next_check") or "").strip()
    if not lesson or not check:return None
    evidence=review.get("evidence") or []
    if not isinstance(evidence,list):evidence=[]
    known=set()
    book=SB._book()
    for item in ((book.get("seats") or {}).get(seat) or {}).get("closed") or []:
        if isinstance(item,dict) and item.get("id"):known.add(str(item["id"]))
    for group in (COMP.snapshot().get("trades") or {}).values():
        for item in (group.get("open") or [])+(group.get("closed") or []):known.add(str(item.get("ref")))
    matched=[str(x) for x in evidence if str(x) in known][:8]
    return SB.lesson(seat,"review",lesson[:300],
                     ref="learning:"+mid+":"+seat,model=model,status="hypothesis",evidence=matched,
                     hypothesis=hypothesis[:140],next_check=check[:150],source_message_id=mid,
                     evidence_note="AI review; recheck before treating as a standing fact")
