"""THF-only process-safe provider rotation. No trading state, provider calls or credentials."""
import contextlib,fcntl,json,os,threading,time,uuid
STATE='/opt/tfo/live/thf/state/thf/provider_rotation.json'
PROVIDERS=('claude','chatgpt','grok','deepseek')
ALIASES={'deepseek_api':'deepseek','claude_direct':'claude','chatgpt_web':'chatgpt','chatgpt_work':'chatgpt','seat':'grok','grok_authenticated':'grok','grok_thf':'grok','grok_bot_fast':'grok'}
_TEST={};_LOCK=threading.RLock()
def canonical(name):return ALIASES.get(str(name),str(name))
def fresh():return dict(version=1,cursor=0,auto_selected={p:0 for p in PROVIDERS if p!='deepseek'},explicit_selected={p:0 for p in PROVIDERS if p!='deepseek'},successes={p:0 for p in PROVIDERS if p!='deepseek'},failures={p:0 for p in PROVIDERS if p!='deepseek'},events=[])
@contextlib.contextmanager
def locked(path=None):
 target=path or STATE
 if path is None and os.environ.get('PYTEST_CURRENT_TEST'):
  with _LOCK:
   value=_TEST.setdefault(os.environ['PYTEST_CURRENT_TEST'],fresh());yield value
  return
 os.makedirs(os.path.dirname(os.path.abspath(target)),exist_ok=True)
 with open(target+'.lock','a+') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  try:
   try:
    with open(target) as f:value=json.load(f)
   except FileNotFoundError:value=fresh()
   if value.get('version')!=1:raise RuntimeError('Provider rotation state needs review; unsupported version')
   yield value
   tmp=target+'.'+str(os.getpid())+'.'+uuid.uuid4().hex+'.tmp'
   try:
    with open(tmp,'x') as f:json.dump(value,f);f.flush();os.fsync(f.fileno())
    os.replace(tmp,target)
   finally:
    if os.path.exists(tmp):os.unlink(tmp)
  finally:fcntl.flock(lock,fcntl.LOCK_UN)
def _event(state,event):
 state['events']=(state.get('events',[])+[dict(epoch=time.time(),**event)])[-160:]
def choose(eligible,explicit=None,path=None,request_id=None,channel='auto',preferred=None):
 names={canonical(p) for p in eligible}&set(PROVIDERS)
 if not names:return None
 picked=canonical(explicit) if explicit else None
 with locked(path) as state:
  if picked in names:
   selected=picked;kind='explicit_selected'
  elif preferred and canonical(preferred) in names:
   selected=canonical(preferred);kind='auto_selected'
   state['cursor']=(PROVIDERS.index(selected)+1)%len(PROVIDERS)
  else:
   cursor=int(state.get('cursor',0))%len(PROVIDERS)
   selected=next(PROVIDERS[(cursor+i)%len(PROVIDERS)] for i in range(len(PROVIDERS)) if PROVIDERS[(cursor+i)%len(PROVIDERS)] in names)
   state['cursor']=(PROVIDERS.index(selected)+1)%len(PROVIDERS);kind='auto_selected'
  state.setdefault(kind,{})[selected]=state.get(kind,{}).get(selected,0)+1
  _event(state,dict(event='selected',provider=selected,selection=kind,channel=channel,request_id=request_id))
 return selected
def reorder(pairs,reason,explicit=None,path=None,request_id=None,channel='auto',preferred=None):
 # One logical Grok entry regardless of route aliases; first occurrence preserves explicit model choice.
 unique=[];seen=set()
 for name,fn in pairs:
  logical=canonical(name)
  if logical not in seen:unique.append((name,fn));seen.add(logical)
 available=[]
 for name,fn in unique:
  if not reason(name):available.append(canonical(name))
 selected=choose(available,explicit,path,request_id,channel,preferred)
 if selected is None:return unique
 i=PROVIDERS.index(selected);rank={PROVIDERS[(i+k)%len(PROVIDERS)]:k for k in range(len(PROVIDERS))}
 return sorted(unique,key=lambda item:(canonical(item[0]) not in available,rank[canonical(item[0])]))
def record(name,ok,channel='auto',request_id=None,error=None,path=None):
 name=canonical(name)
 if name not in PROVIDERS:return
 with locked(path) as state:
  kind='successes' if ok else 'failures';state.setdefault(kind,{})[name]=state.get(kind,{}).get(name,0)+1
  _event(state,dict(event='finished',provider=name,ok=bool(ok),channel=channel,request_id=request_id,error=str(error)[:160] if error else None))
def snapshot(path=None):
 with locked(path) as state:return json.loads(json.dumps(state))
