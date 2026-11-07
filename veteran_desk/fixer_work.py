"""THF model plans execute only through Simon/Fixer's deterministic deployment gate."""
import ast,hashlib,json,os,pathlib,re,subprocess,time
ROOT=pathlib.Path(__file__).resolve().parents[1]
WORK=ROOT/"staging/fixer_work"
OWNERS={"software_engineer","fixer"}
DENY={"veteran_desk/book.py","veteran_desk/gate_v2.py","funder_gate.py","risk_caps.py","firm_risk.py","tools/safe_deploy.sh","veteran_desk/fixer_work.py"}
def sha(data):return hashlib.sha256(data).hexdigest()
def code_path(name,write=False):
    rel=pathlib.PurePosixPath(str(name))
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:raise ValueError("invalid code path")
    path=ROOT.joinpath(*rel.parts)
    if ROOT.resolve() not in path.resolve().parents:raise ValueError("outside THF")
    if path.resolve().relative_to(ROOT.resolve()).as_posix()!=rel.as_posix():raise ValueError("symlink alias refused")
    if any(p.startswith(".") for p in rel.parts):raise ValueError("hidden path refused")
    if rel.parts[0] in ("state","reports","logs","data","backups","staging"):raise ValueError("runtime data refused")
    if path.suffix not in (".py",".js",".html",".css",".sh"):raise ValueError("not source")
    if write and (str(rel) in DENY or "tests" in rel.parts or path.suffix==".sh"):raise ValueError("guarded file needs maintainer review")
    return path
def parse(text):
    text=str(text or "").strip();fence=chr(96)*3
    if text.startswith(fence):text=re.sub("^"+fence+r"(?:json)?\s*|\s*"+fence+"$","",text)
    obj=json.loads(text)
    if not isinstance(obj,dict):raise ValueError("expected plan object")
    return obj
def stage_plan(plan,folder,owner):
    if owner not in OWNERS:raise ValueError("only Simon/Fixer may execute code changes")
    changes=plan.get("changes")
    if not isinstance(changes,list) or not 1<=len(changes)<=8:raise ValueError("expected 1-8 changes")
    files=[];seen=set();total=0
    for item in changes:
        path=code_path(item.get("path"),True);rel=path.relative_to(ROOT).as_posix()
        if rel in seen:raise ValueError("duplicate target")
        seen.add(rel);old=path.read_bytes() if path.is_file() else None
        if item.get("sha256")!=(sha(old) if old is not None else None):raise ValueError("source changed: "+rel)
        if old is None:
            text=item.get("content")
            if not isinstance(text,str):raise ValueError("new file needs content")
        else:
            text=old.decode();edits=item.get("edits")
            if not isinstance(edits,list) or not 1<=len(edits)<=12:raise ValueError("exact edits required")
            for edit in edits:
                find=edit.get("old");replace=edit.get("new")
                if not isinstance(find,str) or not find or not isinstance(replace,str) or text.count(find)!=1:raise ValueError("edit must match once")
                text=text.replace(find,replace,1)
        total+=len(text.encode())
        if total>300000:raise ValueError("change set too large")
        if path.suffix==".py":ast.parse(text)
        target=folder/"files"/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text)
        files.append(dict(path=rel,before_sha256=sha(old) if old is not None else None,after_sha256=sha(text.encode()),staged=str(target)))
    return files
def verify(receipt):
    if not isinstance(receipt,dict) or receipt.get("owner") not in OWNERS or receipt.get("returncode")!=0 or not receipt.get("files"):return False
    try:
        path=pathlib.Path(receipt["receipt_path"])
        if WORK.resolve() not in path.resolve().parents or json.loads(path.read_text())!=receipt:return False
        return all(sha(code_path(r["path"]).read_bytes())==r["after_sha256"] for r in receipt["files"])
    except Exception:return False
def apply_plan(plan,folder,owner,provider):
    files=stage_plan(plan,folder,owner)
    cmd=["bash","tools/safe_deploy.sh","--tag","fixer-"+folder.name[-20:],"--note","Simon/Fixer execution; planner="+provider]
    cmd += [r["staged"]+":"+r["path"] for r in files];log=folder/"deploy.log"
    with log.open("w") as stream:
        # Let the deployment gate finish its checks and rollback; an outer timeout could kill it after the copy.
        run=subprocess.run(cmd,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
    receipt=dict(owner=owner,provider=provider,returncode=run.returncode,files=files,log=str(log),completed_epoch=time.time(),receipt_path=str(folder/"receipt.json"))
    (folder/"receipt.json").write_text(json.dumps(receipt,indent=2))
    return dict(applied=verify(receipt),pending=run.returncode in (3,4),receipt=receipt,text="Applied and tested" if run.returncode==0 else "Waiting for deploy window/lock" if run.returncode in (3,4) else "Deployment refused; originals restored")
def _ask(provider,prompt,images):
    if provider=="chatgpt_work":
        from veteran_desk import chatgpt_bridge as CG
        return CG.ask(prompt,timeout=600,images=images,skip_if_busy=True)
    if provider=="claude":
        if images:raise RuntimeError("attached-image proposals need ChatGPT Work")
        from veteran_desk import claude_brain as CB
        return CB.ask(prompt,seat="software_engineer",timeout=900,kind="hands",tools=False)[0]
    if provider=="grok":
        from veteran_desk import local_llm as LL
        text,status=LL.ask(prompt,desk="thf",seat="software_engineer",timeout=600,retries=0,src="fixer_plan")
        if status!="ok":raise RuntimeError("Grok planner unavailable: "+str(status))
        return text
    raise ValueError("unknown code planner")
class PlannerError(ValueError):
    """A plan cannot be executed; capacity retries cannot repair its contents."""

def _inventory(request):
    words=set(re.findall(r"[a-z]{3,}",request.lower()))
    paths=[]
    for parent in (ROOT,ROOT/"veteran_desk",ROOT/"tools"):
        if not parent.is_dir():continue
        for p in parent.iterdir():
            if not p.is_file() or p.suffix not in (".py",".js",".html",".css",".sh"):continue
            try:code_path(p.relative_to(ROOT).as_posix())
            except ValueError:continue
            paths.append(p)
    paths=sorted(paths,key=lambda p:(-sum(w in p.stem.lower() for w in words),p.name))[:80]
    return [dict(path=p.relative_to(ROOT).as_posix(),sha256=sha(p.read_bytes())) for p in paths]

def run(request,provider,owner="software_engineer",images=None):
    if owner not in OWNERS:raise ValueError("only Simon/Fixer may execute code changes")
    request=str(request)
    if len(request)>16000:raise PlannerError("request exceeds 16000 characters; split into explicit work orders")
    WORK.mkdir(parents=True,exist_ok=True);key=sha((provider+"\n"+request).encode())[:20]
    folder=WORK/key;folder.mkdir(exist_ok=True);cache=folder/"plan.json"
    if (folder/"receipt.json").is_file():
        previous=json.loads((folder/"receipt.json").read_text())
        if verify(previous):return dict(applied=True,pending=False,receipt=previous,text="Already applied; verified live hashes")
    if cache.is_file():
        cached=json.loads(cache.read_text())
        try:stage_plan(cached,folder,owner)
        except ValueError as error:
            if not str(error).startswith("source changed:"):raise
            # Preserve the old proposal for audit, then inspect current source. Never replay stale edits.
            cache.rename(folder/("plan.stale-%d.json"%time.time_ns()))
        else:return apply_plan(cached,folder,owner,provider)
    inventory=_inventory(request)
    rules=("You are Simon's THF PAPER code planner. Fixer is the ONLY writer. Never claim applied changes. No secrets, ledgers, runtime data, git, live orders, risk-limit changes, deploy-gate changes or test edits. "
           'Reply ONE JSON object: {"inspect":[{"path":"relative.py","start":1,"end":120}]} OR '
           '{"summary":"reason","changes":[{"path":"relative.py","sha256":"provided hash","edits":[{"old":"exact unique block","new":"replacement"}]}]}. '
           'New files use sha256:null and content. No shell commands. At most 8 source files. {"no_change":"evidence"} is allowed. '
           "All writes require backups, compile, imports and full guarded tests through safe_deploy. "
           "Old request observations may be stale. Inspection context is cumulative: use the supplied source, do not ask for the same range again. "
           "A no_change result is a review only, never a claim of deployment or of completed trades.\n")
    base=rules+"\nREQUEST:\n"+request+"\nINVENTORY:\n"+json.dumps(inventory)
    context={};chars=0;prompt=base
    for attempt in range(5):
        plan=parse(_ask(provider,prompt,images or []))
        if plan.get("changes"):
            stage_plan(plan,folder,owner);cache.write_text(json.dumps(plan));return apply_plan(plan,folder,owner,provider)
        if plan.get("no_change"):
            evidence=str(plan["no_change"]).strip()
            if len(evidence)<12:raise PlannerError("no_change requires concrete review evidence")
            return dict(applied=False,pending=False,outcome="reviewed",text="Reviewed; no code change proposed: "+evidence[:4000])
        inspection=plan.get("inspect")
        if not isinstance(inspection,list) or not 1<=len(inspection)<=6:raise PlannerError("inspect or propose exact changes")
        added=False
        for item in inspection:
            path=code_path(item.get("path"));raw=path.read_bytes();lines=raw.decode().splitlines()
            start=max(1,int(item.get("start") or 1));end=min(len(lines),int(item.get("end") or start+119),start+199)
            if start>end:raise PlannerError("inspection range is outside source")
            rel=path.relative_to(ROOT).as_posix();digest=sha(raw);key=(rel,start,end,digest)
            if key in context:continue
            # Invalidate only obsolete snapshots of this file, keeping other inspected source.
            context={k:v for k,v in context.items() if k[0]!=rel or k[3]==digest}
            source="\n".join("%d:%s"%(n,lines[n-1]) for n in range(start,end+1))
            context[key]=dict(path=rel,sha256=digest,source=source);added=True
        chars=sum(len(v["source"]) for v in context.values())
        if chars>60000:raise PlannerError("cumulative inspection exceeds 60000 characters; narrow the work order")
        if not added:raise PlannerError("repeated inspection without new source; maintainer review required")
        prompt=base+"\nINSPECTED SOURCE (all previous ranges):\n"+json.dumps(list(context.values()))
    raise PlannerError("inspection limit reached; maintainer review required")
