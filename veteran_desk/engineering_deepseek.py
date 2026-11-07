"""Engineering-only DeepSeek planner; the existing protected Fixer remains writer.

This adds a model transport, not another executor. All path validation, exact
source hashes, staging, deployment checks, rollback and receipt verification
are delegated to the unchanged fixer_work functions.
"""
import json,time
from veteran_desk import fixer_work as F,deepseek_bridge as D

def run(request,owner='software_engineer',images=None,provider='deepseek_api',deadline=None):
    if owner not in F.OWNERS or owner not in D.API_SEATS:raise PermissionError('Only Simon/Fixer may use the paid engineering planner')
    if provider not in ('deepseek_api','chatgpt_work','grok','claude'):raise ValueError('Unknown engineering planner')
    if images and provider!='chatgpt_work':raise RuntimeError('Attached-image planning requires ChatGPT Work')
    request=str(request)
    if len(request)>16000:raise F.PlannerError('request exceeds 16000 characters; split into explicit work orders')
    F.WORK.mkdir(parents=True,exist_ok=True)
    folder=F.WORK/F.sha((provider+'\n'+request).encode())[:20];folder.mkdir(exist_ok=True)
    cache=folder/'plan.json'
    if (folder/'receipt.json').is_file():
        receipt=json.loads((folder/'receipt.json').read_text())
        if F.verify(receipt):return dict(applied=True,pending=False,receipt=receipt,text='Already applied; verified live hashes')
    if cache.is_file():
        plan=json.loads(cache.read_text())
        try:F.stage_plan(plan,folder,owner)
        except ValueError as error:
            if not str(error).startswith('source changed:'):raise
            cache.rename(folder/('plan.stale-%d.json'%time.time_ns()))
        else:return F.apply_plan(plan,folder,owner,provider)
    rules=('You are Simon/Fixer\'s THF PAPER code planner. Fixer is the ONLY writer. Never claim applied changes. '
           'No secrets, ledgers, runtime data, git, orders, stops, risk-limit changes, deploy-gate changes or test edits. '
           'Reply one JSON object: {"inspect":[{"path":"relative.py","start":1,"end":120}]} OR '
           '{"summary":"reason","changes":[{"path":"relative.py","sha256":"provided hash","edits":[{"old":"exact unique block","new":"replacement"}]}]}. '
           'New files use sha256:null and content. No shell commands. At most8 source files. '
           '{"no_change":"concrete review evidence"} is allowed and means a review, never deployment. '
           'All writes require backups, compilation, imports and full guarded tests through safe_deploy. '
           'Use cumulative inspected source; do not repeat the same range.\n')
    base=rules+'\nREQUEST:\n'+request+'\nINVENTORY:\n'+json.dumps(F._inventory(request))
    context={};prompt=base;deadline=min(time.time()+180,float(deadline)) if deadline else time.time()+180
    for attempt in range(5):
        left=deadline-time.time()
        if left<2:raise RuntimeError('DeepSeek engineering planning deadline exhausted; no new deployment submitted')
        rid='engineering-'+folder.name+'-'+str(attempt)+'-'+F.sha(prompt.encode())[:12]
        if provider=='deepseek_api':
            answer=D.ask(prompt,seat=owner,timeout=min(45,left),deadline=deadline,request_id=rid,workload='other')
        elif provider=='chatgpt_work':
            from veteran_desk import chatgpt_bridge as CG
            answer=CG.ask(prompt,timeout=min(120,left),images=images,seat=owner,workload='other',skip_if_busy=True,deadline=deadline,request_id=rid)
        elif provider=='claude':
            from veteran_desk import claude_brain as CB
            answer=CB.ask(prompt,seat=owner,timeout=min(90,left),kind='hands',tools=False,slot_wait_max=1)[0]
        else:
            from veteran_desk import thf_grok as G,local_llm as LL
            answer=G.ask(prompt,seat=owner,timeout=min(60,left),deadline=deadline,model=LL.MODEL)
        plan=F.parse(answer)
        if plan.get('changes'):
            F.stage_plan(plan,folder,owner);cache.write_text(json.dumps(plan))
            return F.apply_plan(plan,folder,owner,provider)
        if plan.get('no_change'):
            evidence=str(plan['no_change']).strip()
            if len(evidence)<12:raise F.PlannerError('no_change requires concrete review evidence')
            return dict(applied=False,pending=False,outcome='reviewed',text='Reviewed; no code change proposed: '+evidence[:4000])
        inspection=plan.get('inspect')
        if not isinstance(inspection,list) or not 1<=len(inspection)<=6:raise F.PlannerError('inspect or propose exact changes')
        added=False
        for item in inspection:
            path=F.code_path(item.get('path'));raw=path.read_bytes();lines=raw.decode().splitlines()
            start=max(1,int(item.get('start') or 1));end=min(len(lines),int(item.get('end') or start+119),start+199)
            if start>end:raise F.PlannerError('inspection range is outside source')
            rel=path.relative_to(F.ROOT).as_posix();digest=F.sha(raw);key=(rel,start,end,digest)
            if key in context:continue
            context={k:v for k,v in context.items() if k[0]!=rel or k[3]==digest}
            context[key]=dict(path=rel,sha256=digest,source='\n'.join('%d:%s'%(n,lines[n-1]) for n in range(start,end+1)))
            added=True
        if sum(len(v['source']) for v in context.values())>60000:raise F.PlannerError('cumulative inspection exceeds60000 characters; narrow the work order')
        if not added:raise F.PlannerError('repeated inspection without new source; maintainer review required')
        prompt=base+'\nINSPECTED SOURCE (all previous ranges):\n'+json.dumps(list(context.values()))
    raise F.PlannerError('inspection limit reached; maintainer review required')
