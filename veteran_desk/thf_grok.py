"""THF Grok through the existing authenticated Singapore Cursor CLI. No new credentials or relay services."""
import contextlib,datetime as dt,fcntl,json,os,re,signal,subprocess,time
from veteran_desk import bridge_maintenance as B
STATE='/opt/tfo/live/thf/state/thf/grok_provider_status.json'
CLI=os.path.expanduser('~/.local/bin/cursor-agent')
QUOTA=re.compile(r'usage limit|limit reached|ActionRequiredError|monthly .{0,30}limit|out of credits|quota',re.I)
AUTH=re.compile(r'authentication required|not logged|unauthori[sz]ed|sign.?in required|login required',re.I)
class Reply(str):
 def __new__(cls,text,model):
  obj=str.__new__(cls,text);obj.model='grok '+model+' (authenticated Singapore Cursor)';return obj
def state():
 try:
  with open(STATE) as f:return json.load(f)
 except FileNotFoundError:return {}
def monthly_reset_secs(detail,epoch=None):
 if not (re.search('monthly',detail,re.I) and re.search(r'1st.*month.*UTC',detail,re.I)):return None
 now=dt.datetime.fromtimestamp(time.time() if epoch is None else epoch,dt.timezone.utc)
 year=now.year+(now.month==12);month=1 if now.month==12 else now.month+1
 return max(60,int(dt.datetime(year,month,1,tzinfo=dt.timezone.utc).timestamp()-now.timestamp()))
def _save_result(ok,detail,started,model,probe=False,observed=None):
 from veteran_desk import ai_cooldown as CD
 epoch=time.time() if observed is None else observed
 os.makedirs(os.path.dirname(STATE),exist_ok=True)
 with open(STATE+'.lock','a+') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX);old=state()
  if ok and float(old.get('failure_epoch') or 0)>started:return dict(old,stale_success_ignored=True)
  if observed is not None and float(old.get('checked_epoch') or 0)>epoch:return old
  result=dict(old,route='sgp_authenticated_cursor',model=model,checked_epoch=epoch,checked_utc=dt.datetime.fromtimestamp(epoch,dt.timezone.utc).isoformat(),detail=str(detail)[-800:],probe=probe)
  if ok:
   result.update(ok=True,last_success_epoch=epoch);CD.mark_ok('grok')
  elif QUOTA.search(str(detail)) or AUTH.search(str(detail)):
   result.update(ok=False,failure_epoch=epoch,quota=bool(QUOTA.search(str(detail))),auth=bool(AUTH.search(str(detail))))
   CD.mark_fail('grok',str(detail),secs=monthly_reset_secs(str(detail),epoch),kind='quota' if result['quota'] else 'auth')
  else:
   result['last_transient_error']=str(detail)[-300:]
   if not old:result['ok']=False
   CD.mark_fail('grok',str(detail))
  B.atomic_json(STATE,result);return result
def reason():
 from veteran_desk import ai_cooldown as CD
 if CD.cooling('grok'):return 'Grok account paused; awaiting a genuine successful health response'
 if not os.path.isfile(CLI):return 'Existing Cursor CLI unavailable'
 current=state()
 if not current.get('ok'):return 'Authenticated Grok route not successfully verified'+(': '+str(current.get('detail',''))[-180:] if current else '')
 return None
def _run(prompt,timeout,model,deadline=None):
 from veteran_desk import local_llm as LL
 end=min(time.time()+max(1,float(timeout)),float(deadline)) if deadline else time.time()+max(1,float(timeout))
 if end-time.time()<1:raise RuntimeError('grok deadline exhausted')
 os.makedirs(LL.WS,exist_ok=True)
 with LL._global_slot(end-1) as got:
  if not got or end-time.time()<1:raise RuntimeError('grok capacity wait exceeded deadline')
  p=subprocess.Popen([CLI,'-p','--output-format','text','--model',model,'--mode','ask','--trust','--workspace',LL.WS],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,cwd=LL.WS,start_new_session=True)
  try:out,err=p.communicate(prompt or '',timeout=max(.1,end-time.time()))
  except subprocess.TimeoutExpired:
   try:os.killpg(p.pid,signal.SIGKILL)
   except ProcessLookupError:pass
   p.communicate(timeout=5);raise RuntimeError('grok timeout; process group stopped; no late result')
  if p.returncode!=0 or not out.strip():raise RuntimeError(('grok rc='+str(p.returncode)+' '+(err or out)[-700:]).strip())
  if time.time()>=end:raise RuntimeError('grok late result discarded')
  return Reply(out.strip(),model)
def ask(prompt,seat='?',timeout=120,model=None,deadline=None):
 B.client_guard()
 why=reason()
 if why:raise RuntimeError(why)
 from veteran_desk import local_llm as LL
 model=model or LL.MODEL;started=time.time()
 try:
  out=_run(prompt,timeout,model,deadline);_save_result(True,'Model response succeeded',started,model);return out
 except Exception as error:
  _save_result(False,error,started,model);raise

def probe(timeout=45):
 started=time.time();model='grok-4.7-low-fast'
 try:
  out=_run('Read-only provider health check. No trading, code, messages or account changes. Reply exactly GROK_READY.',timeout,model)
  if 'GROK_READY' not in out:raise RuntimeError('Grok probe did not return its readiness marker')
  return _save_result(True,'GROK_READY',started,model,probe=True)
 except Exception as error:return _save_result(False,error,started,model,probe=True)
def probe_due(interval=1800,force=False):
 if os.environ.get('PYTEST_CURRENT_TEST') and not force:return None
 if B.paused(B.read_control()):return None
 os.makedirs(os.path.dirname(STATE),exist_ok=True)
 with open(STATE+'.probe.lock','a+') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return None
  if not force and time.time()-float(state().get('checked_epoch') or 0)<interval:return state()
  return probe()
if __name__=='__main__':print(json.dumps(probe_due(force=True),indent=2))
