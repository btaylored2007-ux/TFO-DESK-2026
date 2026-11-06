"""THF advisory DeepSeek API client. No trading, code execution or credential creation.

The owner enables this provider only after a gated release and a real model
response. API work uses independent process locks; the ChatGPT browser stays
single-worker. A killed/timed-out API child is reaped before releasing a slot.
"""
import contextlib, datetime, fcntl, hashlib, json, math, os, pathlib, re
import subprocess, sys, time, uuid

def _runtime_root(source):
    root = pathlib.Path(source).resolve().parents[1]
    # Dashboard code is mirrored, while provider admission and health remain
    # authoritative in the live tree. Never create a separate mirror lock pool.
    return pathlib.Path('/opt/tfo/live/thf') if str(root) in ('/opt/tfo/dash/base','/opt/tfo/dash/codemerge') else root

ROOT = _runtime_root(__file__)
STATE = ROOT / 'state/thf/deepseek_api'
KEY = pathlib.Path('/home/tfo/.tfo_secrets/deepseek_api_key')
LIVE_STATE = STATE
MODEL = 'deepseek-flash'
MAX_SECONDS = 90
from veteran_desk.thf_funder import SEAT_IDS as _KNOWN_SEATS
API_SEATS = frozenset(_KNOWN_SEATS)
LEGACY_API_SEATS = frozenset(('software_engineer','fixer'))
MAX_CAPACITY = 4

class Reply(str):
    pass

def _read(path, default=None):
    try: return json.loads(path.read_text())
    except (OSError, ValueError): return default

def _atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with open(tmp, 'x', encoding='utf8') as f:
        os.chmod(tmp, 0o600); json.dump(value, f); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)

def config():
    # Tests must opt into a temporary state; never read the live key/config.
    if os.environ.get('PYTEST_CURRENT_TEST') and STATE == LIVE_STATE: return {}
    return _read(STATE/'config.json', {})

def enabled(): return config().get('enabled') is True

def allowed_seats():
    configured = config().get('allowed_seats')
    if configured is None: return LEGACY_API_SEATS
    if not isinstance(configured, (list, tuple)): return frozenset()
    return API_SEATS.intersection(str(seat) for seat in configured)

def enabled_for(seat, workload='chat'):
    return enabled() and seat in allowed_seats() and (workload!='trader' or config().get('trader_enabled') is True)

def fast_preferred(): return enabled() and config().get('prefer_fast_api') is True

def capacity():
    try: return min(MAX_CAPACITY, max(1, int(config().get('capacity', 2))))
    except (TypeError, ValueError): return 2

def status(): return _read(STATE/'status.json', {}) if config() else {}

def reason(check_busy=True, check_key=True):
    if not enabled(): return 'DeepSeek API not enabled'
    if check_key and not KEY.is_file(): return 'DeepSeek private API key unavailable'
    s = status()
    if s.get('blocked'): return 'DeepSeek '+s.get('failure', 'account unavailable')+'; successful health probe required'
    if float(s.get('until') or 0) > time.time(): return 'DeepSeek cooling: '+str(s.get('failure') or 'temporarily unavailable')
    if not s.get('last_success_at'): return 'DeepSeek model response not verified'
    if check_busy:
        slots = []
        try:
            for n in range(capacity()):
                try: f = open(STATE/('slot%d.lock'%n), 'r')
                except FileNotFoundError: return None  # an unused slot is free
                except OSError: return 'DeepSeek capacity observation unavailable'
                try: fcntl.flock(f, fcntl.LOCK_EX|fcntl.LOCK_NB); slots.append(f)
                except BlockingIOError: f.close()
            if not slots: return 'DeepSeek busy'
        finally:
            for f in slots: f.close()
    return None

def record_result(ok, started, failure=None, http_status=None, probe=False):
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE/'status.lock', 'a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        s = _read(STATE/'status.json', {})
        # An older in-flight success must not clear a newer quota/auth refusal.
        if ok and not probe and float(s.get('failure_observed_at') or 0) > started: return
        now = time.time()
        if ok:
            s.update(ok=True, last_success_at=now, model=MODEL, blocked=False, until=0, failure=None)
        else:
            s.update(ok=False, failure=str(failure or 'API failure')[:150], failure_observed_at=now,
                     blocked=http_status in (401,402,403), until=now+(300 if http_status==429 else 60))
        s.update(checked_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), http_status=http_status)
        _atomic(STATE/'status.json', s)

def brain_readback(prompt):
    evidence=[]
    for block in re.finditer(r'MY BRAIN \[THF seat=([a-z0-9_]{1,40});[^\n]*\n((?:LESSONS:[^\n]*\n?)+)', str(prompt)):
        seat=block.group(1); saved={}
        path=ROOT/'state/thf/seat_brain'/seat/'lessons.jsonl'
        try:
            if path.stat().st_size>2_000_000: continue
            for ln in path.read_text().splitlines()[-1000:]:
                try:
                    row=json.loads(ln)
                    if row.get('seat')==seat and row.get('ref'): saved[str(row['ref'])]=row
                except (ValueError,AttributeError): pass
        except OSError: pass
        for ln in block.group(2).splitlines():
            m=re.search(r'\bref=([^\]\r\n]+)\]\s*(.*)',ln)
            if not m: continue
            ref,received=m.groups(); row=saved.get(ref,{}); lesson=str(row.get('lesson') or '')
            evidence.append(dict(brain_seat=seat,ref=ref,saved_kind=row.get('kind'),source='own THF brain',saved_lesson_sha256=hashlib.sha256(lesson.encode()).hexdigest() if lesson else None,full_saved_lesson_in_request=bool(lesson and lesson in received),request_lesson_sha256=hashlib.sha256(received.encode()).hexdigest()))
            if len(evidence)>=8:return evidence
    return evidence

def receipt(event, job, **fields):
    row=dict(event=event,utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),provider='deepseek_api',**job,**fields)
    with open(STATE/'requests.jsonl','a') as f:
        fcntl.flock(f,fcntl.LOCK_EX);f.write(json.dumps(row)+'\n');f.flush()

@contextlib.contextmanager
def _locks(rid, seat, workload, deadline, cancel_event):
    # A stable request ID is shared by queue/chat/learning callers. Reject an
    # overlapping duplicate before any external API request can be submitted.
    held=[];seat_file=None
    try:
        for tag in ['request-'+hashlib.sha256(rid.encode()).hexdigest()]:
            f=open(STATE/(tag+'.lock'),'a+');held.append(f)
            try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('DeepSeek duplicate request already active')
        seat_file=open(STATE/('seat-'+hashlib.sha256(str(seat).encode()).hexdigest()+'.lock'),'a+')
        end=min(deadline-5,time.time()+300)
        while True:
            if cancel_event is not None and cancel_event.is_set():raise RuntimeError('DeepSeek request cancelled before submission')
            try:fcntl.flock(seat_file,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                seat_file.seek(0)
                try: prior=json.load(seat_file)
                except ValueError: prior={}
                if {prior.get('workload'),workload}!={'trader','learning'}:raise RuntimeError('DeepSeek seat already active')
                if time.time()>=end:raise RuntimeError('DeepSeek bounded same-seat wait expired')
                time.sleep(min(.1,max(.001,end-time.time())))
        seat_file.seek(0);seat_file.truncate();json.dump(dict(workload=workload,request_id=rid),seat_file);seat_file.flush()
        for n in range(capacity()):
            f=open(STATE/('slot%d.lock'%n),'a+')
            try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:f.close();continue
            held.append(f);break
        else:raise RuntimeError('DeepSeek busy')
        yield
    finally:
        if seat_file:seat_file.close()
        for f in held:f.close()

def _run(payload, budget):
    p=subprocess.Popen([sys.executable,'-m','veteran_desk.deepseek_bridge','--worker'],cwd=str(ROOT),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    try:
        out,_=p.communicate(json.dumps(payload),timeout=budget)
    except BaseException:
        p.kill();p.communicate();raise
    if p.returncode:raise RuntimeError('DeepSeek API worker failed')
    return json.loads(out)

def ask(prompt, seat='desk', timeout=90, request_id=None, workload='chat', deadline=None, cancel_event=None, probe=False):
    if seat not in API_SEATS or seat not in allowed_seats() or (workload=='trader' and config().get('trader_enabled') is not True):
        raise PermissionError('DeepSeek API is restricted by the current authorised seat/workload policy')
    from veteran_desk import bridge_maintenance as BM
    BM.client_guard()
    if workload not in ('chat','trader','learning','other'):raise ValueError('invalid DeepSeek workload')
    started=time.time();budget=float(timeout)
    if not math.isfinite(budget) or budget<=0:raise ValueError('invalid DeepSeek timeout')
    end=min(started+MAX_SECONDS,started+budget,float(deadline) if deadline else started+MAX_SECONDS)
    if not math.isfinite(end) or end<=started+1:raise RuntimeError('DeepSeek deadline exhausted')
    if os.environ.get('PYTEST_CURRENT_TEST') and STATE==LIVE_STATE:raise RuntimeError('DeepSeek live API disabled in tests')
    why=reason(check_busy=False)
    if why and not probe:raise RuntimeError(why)
    STATE.mkdir(parents=True,exist_ok=True)
    rid=str(request_id or uuid.uuid4().hex)
    job=dict(request_id=rid,seat=str(seat),workload=workload,deadline=end,accepted_at=started,prompt_sha256=hashlib.sha256(str(prompt).encode()).hexdigest(),brain_readback=brain_readback(prompt))
    with _locks(rid,seat,workload,end,cancel_event):
        if cancel_event is not None and cancel_event.is_set():raise RuntimeError('DeepSeek request cancelled')
        BM.client_guard();BM.mark_submission();job['started_at']=time.time();receipt('accepted',job)
        try:
            result=_run(dict(prompt=str(prompt),seat=seat,workload=workload),max(.01,end-time.time()))
            if time.time()>=end or cancel_event is not None and cancel_event.is_set():raise RuntimeError('DeepSeek late/cancelled response discarded')
            if not result.get('ok'):
                record_result(False,started,result.get('failure'),result.get('http_status'))
                raise RuntimeError('DeepSeek '+str(result.get('failure') or 'API failure'))
            text=result.get('text')
            if not isinstance(text,str) or not text.strip():raise RuntimeError('DeepSeek empty answer')
            record_result(True,started,http_status=200,probe=probe)
            receipt('finished',job,ok=True,model=result.get('model'),finished_at=time.time(),usage=result.get('usage'))
            answer=Reply(text);answer.model='deepseek '+str(result.get('model') or MODEL)+' API';answer.request_id=rid;answer.accepted_at=started;answer.started_at=job['started_at'];answer.finished_at=time.time()
            return answer
        except Exception as e:
            # Log only locally generated/classified failures, never raw headers or an API key.
            receipt('finished',job,ok=False,reason=type(e).__name__,finished_at=time.time())
            raise

def _worker():
    import urllib.request,urllib.error
    if os.environ.get('PYTEST_CURRENT_TEST'):raise RuntimeError('DeepSeek live worker disabled in tests')
    payload=json.load(sys.stdin)
    try:
        # No redirects: the bearer credential can only reach the fixed official endpoint.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*a,**kw):return None
        st=KEY.stat()
        if st.st_uid!=os.getuid() or st.st_mode&0o077:raise ValueError('private key permissions')
        key=KEY.read_text().strip()
        if not 16<=len(key)<=512 or '\n' in key or '\r' in key:raise ValueError('private key format')
        body=dict(model=MODEL,messages=[dict(role='system',content='Provider: DeepSeek API; model: '+MODEL+'; THF seat: '+str(payload.get('seat') or 'unspecified')+'. The owner permits this API for all configured THF trader/admin seats. Follow the supplied schema and current own-desk context. You are advisory: code writes stay with the guarded Simon/Fixer pipeline; you cannot execute code, alter risk rules or place orders.'),dict(role='user',content=payload['prompt'])],max_tokens=4096,thinking=dict(type='disabled'),stream=False)
        from veteran_desk.communication_protocol import wants_json, object_reply
        structured = wants_json(payload['prompt'])
        if structured: body['response_format'] = dict(type='json_object')
        req=urllib.request.Request('https://api.deepseek.com/chat/completions',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
        with urllib.request.build_opener(NoRedirect).open(req,timeout=MAX_SECONDS) as r:data=json.loads(r.read(2_000_000))
        choice=(data.get('choices') or [{}])[0];text=(choice.get('message') or {}).get('content')
        good=isinstance(text,str) and bool(text.strip()) and choice.get('finish_reason')=='stop'
        failure = None if good else 'empty_or_truncated_answer'
        if good and structured:
            try: object_reply(text)
            except ValueError: good=False; failure='invalid_json_object'
        result=dict(ok=good,text=text if good else None,failure=failure,model=data.get('model'),usage=data.get('usage'),http_status=200)
    except urllib.error.HTTPError as e:
        result=dict(ok=False,http_status=e.code,failure={401:'authentication refused',402:'insufficient balance',403:'access refused',429:'rate limit',500:'server error',503:'overloaded'}.get(e.code,'HTTP failure'))
    except Exception as e:result=dict(ok=False,failure='transport '+type(e).__name__)
    print(json.dumps(result))

if __name__=='__main__' and sys.argv[1:]==['--worker']:_worker()
