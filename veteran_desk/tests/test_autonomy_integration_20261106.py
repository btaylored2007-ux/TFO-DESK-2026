"""Public autonomy API, live prompt evidence and status-only mirror contracts."""
import ast
import importlib.util
import json
import os
import pathlib
import subprocess
import types
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

def load(name, path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module);return module

def test_shared_evidence_does_not_become_own_thf_performance(tmp_path,monkeypatch):
    module=load("learning_prompt_candidate",ROOT/"veteran_desk/learning.py")
    monkeypatch.setattr(module,"BASE",str(tmp_path))
    monkeypatch.setenv("THF_DESK","1")
    module.BOOK=str(tmp_path/"state/thf/thf_book.json")
    book=pathlib.Path(module.BOOK);book.parent.mkdir(parents=True)
    book.write_text(json.dumps({"seats":{"crypto":{"positions":{},"closed":[]}}}))
    calls=[]
    fake=types.ModuleType("veteran_desk.learning_evidence")
    def context_for(seat,root,max_chars):
        calls.append((seat,root,max_chars))
        return "\nSHARED PEER EVIDENCE: Veteran example wins are historical peer data.\n"
    fake.context_for=context_for
    import veteran_desk
    monkeypatch.setitem(sys.modules,"veteran_desk.learning_evidence",fake)
    monkeypatch.setattr(veteran_desk,"learning_evidence",fake,raising=False)
    result=module.lessons_for_prompt("crypto")
    assert "own THF book" in result and "No realised closes" in result
    assert "historical peer data" in result
    assert calls==[("crypto",str(tmp_path),4000)]
    assert json.loads(book.read_text())["seats"]["crypto"]["closed"]==[]

@pytest.mark.parametrize("available,fresh,expected",[(True,True,200),(True,False,503),(False,False,503)])
def test_autonomy_api_never_serves_stale_readiness_as_success(monkeypatch,available,fresh,expected):
    source=ROOT/"veteran_desk/thf_dash.py";tree=ast.parse(source.read_text())
    klass=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="H")
    node=next(n for n in klass.body if isinstance(n,ast.FunctionDef) and n.name=="do_GET")
    scope={"json":json};exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),"exec"),scope)
    fake=types.ModuleType("veteran_desk.thf_home")
    fake.autonomy_status=lambda:dict(available=available,fresh=fresh,status="amber")
    import veteran_desk
    monkeypatch.setitem(sys.modules,"veteran_desk.thf_home",fake)
    monkeypatch.setattr(veteran_desk,"thf_home",fake,raising=False)
    sent=[];handler=types.SimpleNamespace(path="/api/autonomy",_send=lambda *args:sent.append(args))
    scope["do_GET"](handler)
    assert sent[0][0]==expected and json.loads(sent[0][1])["fresh"] is fresh

def test_dashboard_mirror_copies_status_but_not_active_learning_database(tmp_path):
    source=(ROOT/"tools/thf_dash_refresh.sh").read_text()
    live,base,merge,data=(tmp_path/n for n in ("live","base","merge","data"))
    for path in (live/"tools",live/"state/thf/autonomy",base/"state",merge,data/"state",data/"reports",data/"sqlite"):
        path.mkdir(parents=True,exist_ok=True)
    (live/"SGP_PRIMARY").touch()
    (live/"state/thf/autonomy/status.json").write_text('{"status":"amber"}')
    for name in ("learning.sqlite","learning.sqlite-wal","learning.sqlite-shm"):
        (live/"state/thf/autonomy"/name).write_bytes(b"not a transferable live database")
    source=source.replace("/opt/tfo/live/thf",str(live)).replace("/opt/tfo/dash/base",str(base)).replace("/opt/tfo/dash/codemerge",str(merge)).replace("/opt/tfo/data",str(data))
    script=tmp_path/"refresh.sh";script.write_text(source)
    result=subprocess.run(["bash",str(script)],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    assert json.loads((base/"state/thf/autonomy/status.json").read_text())["status"]=="amber"
    assert not list((base/"state/thf/autonomy").glob("learning.sqlite*"))

def test_chat_prompt_keeps_current_owner_request_and_intact_recent_peer_context(tmp_path,monkeypatch):
    source=ROOT/"veteran_desk/thf_msgdesk.py";tree=ast.parse(source.read_text())
    selected=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ("_shared_learning_evidence","prompt")]
    cp_source=pathlib.Path(os.environ.get("AUTONOMY_CP_SOURCE",str(ROOT/"veteran_desk/communication_protocol.py")))
    cp_tree=ast.parse(cp_source.read_text())
    cp_node=next(n for n in cp_tree.body if isinstance(n,ast.FunctionDef) and n.name=="thread_context")
    cp_scope={"json":json};exec(compile(ast.Module(body=[cp_node],type_ignores=[]),str(cp_source),"exec"),cp_scope)
    import veteran_desk
    cp=types.ModuleType("veteran_desk.communication_protocol");cp.status_prompt=lambda *a:"";cp.thread_context=cp_scope["thread_context"]
    tasks=types.ModuleType("veteran_desk.thf_chat_tasks");tasks.learning_prompt=lambda *a:""
    evidence=types.ModuleType("veteran_desk.learning_evidence");evidence.context_for=lambda *a,**k:"\nSHARED VERIFIED HISTORY: peer wins and losses; not own P&L.\n"
    mail=types.ModuleType("veteran_desk.seat_mail");mail.limits_text=lambda:"bounded"
    for name,module in (("communication_protocol",cp),("thf_chat_tasks",tasks),("learning_evidence",evidence),("seat_mail",mail)):
        monkeypatch.setitem(sys.modules,"veteran_desk."+name,module)
        monkeypatch.setattr(veteran_desk,name,module,raising=False)
    scope={"json":json,"L":str(tmp_path),"S":str(tmp_path/"state/thf"),"persona":lambda seat:({"role":"test"},"Test"),
           "memory":lambda seat:("{}",""),"_is_push":lambda *a:False,"STAFF":[],
           "SR":None,"ALL":["crypto"],"HT":"head_trader","TRADE":["crypto"],"SE":"software_engineer","CFO":"cfo",
           "SE_RULES":lambda:"","CFO_RULES":lambda:"","HT_RULES":"","SEAT_ACT_RULES":"","MARCUS_ACT_RULES":"",
           "SEAT_ACT_SCHEMA":"","HEAD_ACT_SCHEMA":"","market":lambda *a:{},
           "_mypos":lambda *a:"","_facts_for_prompt":lambda *a:{},
           "_money":lambda *a:"own book unchanged","_brain_block":lambda *a:"",
           "_dash_block":lambda *a:"","_mkt_json":lambda x:json.dumps(x),
           "_inbox":lambda *a:[],"_chain_line":lambda *a:"","_request_context":lambda *a:""}
    exec(compile(ast.Module(body=selected,type_ignores=[]),str(source),"exec"),scope)
    owner="CURRENT OWNER REQUEST "+"x"*2500
    rows=[{"from":"brett","to":"crypto","text":"old "+str(i)} for i in range(100)]
    rows += [{"id":"peer-123","reply_to":"request-123","root":"root-123","from":"banks","to":"crypto","text":"LATEST peer evidence"}]
    result=scope["prompt"]("crypto",{"id":"owner-123","text":owner,"ai":"chatgpt","command":{"mode":"ask"}},rows)
    assert owner in result and "SHARED VERIFIED HISTORY" in result
    body=result.split("Recent thread turns: ",1)[1].split("\nBrett (funder)",1)[0]
    context=json.loads(body)
    assert context[-1]["id"]=="peer-123" and context[-1]["reply_to"]=="request-123"
    assert len(body)<=1800

def test_one_corrupt_thread_row_cannot_hide_other_context_or_paid_receipts(tmp_path):
    source=ROOT/"veteran_desk/thf_msgdesk.py";tree=ast.parse(source.read_text())
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="tread")
    path=tmp_path/"thread.jsonl"
    rows=[{"id":"owner","from":"brett","text":"keep goal"},{"reply_to":"owner","from":"crypto","mode":"answer","text":"paid answer"}]
    raw=json.dumps(rows[0])+"\n{bad append\n[]\n"+json.dumps(rows[1])+"\n"
    path.write_text(raw);warnings=[]
    scope={"json":json,"os":os,"THREAD":str(path),"_THREAD_WARNED":None,"log":warnings.append}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),"exec"),scope)
    assert scope["tread"]()==rows and scope["tread"]()==rows
    assert path.read_text()==raw and len(warnings)==1


def _routing_harness(seat, source):
    root = ROOT if (ROOT/"veteran_desk/intent.py").exists() else ROOT.parents[2]
    module = load("autonomy_routing_harness", root/"veteran_desk/tests/test_intent_routing_20261104.py")
    module.SOURCE = source
    return module.harness(seat)


def test_handler_to_actual_prompt_preserves_latest_peer_and_route_ids(tmp_path, monkeypatch):
    # Execute the existing prompt regression setup, then exercise its actual handler caller.
    source=ROOT/"veteran_desk/thf_msgdesk.py"
    cp_source=pathlib.Path(os.environ.get("AUTONOMY_CP_SOURCE",str(ROOT/"veteran_desk/communication_protocol.py")))
    cp_tree=ast.parse(cp_source.read_text())
    cp_node=next(n for n in cp_tree.body if isinstance(n,ast.FunctionDef) and n.name=="thread_context")
    cp_scope={"json":json};exec(compile(ast.Module(body=[cp_node],type_ignores=[]),str(cp_source),"exec"),cp_scope)
    import veteran_desk
    cp=types.ModuleType("veteran_desk.communication_protocol")
    cp.status_prompt=lambda *a:"";cp.thread_context=cp_scope["thread_context"]
    cp.peer_engineering_reply=lambda seat,message,answer,state=None:answer
    tasks=types.ModuleType("veteran_desk.thf_chat_tasks");tasks.learning_prompt=lambda *a:""
    evidence=types.ModuleType("veteran_desk.learning_evidence");evidence.context_for=lambda *a,**k:"SHARED LEARNING PREFIX\n"
    mail=types.ModuleType("veteran_desk.seat_mail");mail.limits_text=lambda:"bounded";mail.ack=lambda *a:None
    for name,module in (("communication_protocol",cp),("thf_chat_tasks",tasks),("learning_evidence",evidence),("seat_mail",mail)):
        monkeypatch.setitem(sys.modules,"veteran_desk."+name,module)
        monkeypatch.setattr(veteran_desk,name,module,raising=False)
    prompt_scope={"json":json,"L":str(tmp_path),"S":str(tmp_path/"state/thf"),
        "persona":lambda seat:({},"Test"),"memory":lambda seat:("{}",""),"_is_push":lambda *a:False,
        "STAFF":[],"SR":None,"ALL":["banks"],"HT":"head_trader","TRADE":["banks"],"SE":"software_engineer","CFO":"cfo",
        "SE_RULES":lambda:"","CFO_RULES":lambda:"","HT_RULES":"","SEAT_ACT_RULES":"","MARCUS_ACT_RULES":"",
        "SEAT_ACT_SCHEMA":"","HEAD_ACT_SCHEMA":"","market":lambda *a:{},"_mypos":lambda *a:"",
        "_facts_for_prompt":lambda *a:{},"_money":lambda *a:"","_brain_block":lambda *a:"",
        "_dash_block":lambda *a:"","_mkt_json":json.dumps,"_inbox":lambda *a:[],
        "_chain_line":lambda *a:"","_request_context":lambda *a:""}
    tree=ast.parse(source.read_text())
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ("prompt","_shared_learning_evidence")]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),"exec"),prompt_scope)
    ns,rows,jobs,hunts,_=_routing_harness("banks",source)
    monkeypatch.setattr(ns["_BM"],"CONTROL",str(tmp_path/"no-maintenance.json"))
    rows.extend([dict(id="fm-prior",root="fm-prior",**{"from":"brett","to":"banks"},text="Retain my original objective"),
                 dict(id="peer-123",root="fm-prior",reply_to="fm-prior",**{"from":"energy","to":"banks"},text="LATEST relevant peer evidence")])
    observed=[]
    def ask(seat,message,context):
        observed.append(prompt_scope["prompt"](seat,message,context))
        return dict(reply="Reply",_brain="mock")
    ns["ask"]=ask
    try:
        ns["_handle_normal"](dict(id="fm-current",text="re:fm-prior Follow the objective",seat="banks",command=dict(mode="ask")))
        assert len(observed)==1 and "SHARED LEARNING PREFIX" in observed[0]
        body=observed[0].split("Recent thread turns: ",1)[1].split("\nBrett (funder)",1)[0]
        context=json.loads(body)
        assert any(row.get("id")=="peer-123" and row.get("reply_to")=="fm-prior" for row in context)
        assert "Retain my original objective" in body and len(body)<=1800
    finally:
        ns["_maintenance_temp"].cleanup()


def test_scheduled_head_trader_cancel_during_provider_has_no_inline_or_outer_actions(tmp_path, monkeypatch):
    import concurrent.futures as cf, re, time
    source=ROOT/"veteran_desk/thf_msgdesk.py"
    ns,rows,jobs,hunts,_=_routing_harness("head_trader",source)
    monkeypatch.setattr(ns["_BM"],"CONTROL",str(tmp_path/"no-maintenance.json"))
    tree=ast.parse(source.read_text())
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ("ht_review","_inflight","_retry_due")]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),"exec"),ns)
    state={"status":"active"};inline=[]
    def provider(*args):
        state["status"]="cancelled"
        return json.dumps(dict(reply="FULL late HT paid answer",actions=[dict(op="pause",seat="banks",why="proposal")]))
    ns.update(cf=cf,time=time,re=re,LASTM=types.SimpleNamespace(),_paid_context=lambda:"",prompt=lambda *a:"shared mock context",
              _claude=provider,_chatgpt=provider,_grok=provider,_seat_brain=provider,
              _tcall=lambda name,fn,budget:fn(),_budget=lambda *a:1,_interactive_wait=lambda msg:False,
              SKIP={},_CD=types.SimpleNamespace(cooling=lambda n:False,mark_ok=lambda n:None,mark_fail=lambda *a:None),
              QUOTA=re.compile("no-quota"),_model_label=lambda n:n,_ht_act=lambda action:inline.append(action) or dict(ok=True),
              HT_MAX_ACTS=3,_learn_control=lambda msg:state["status"],RETRY_MAX=6,STALE_S=1640)
    import veteran_desk
    ar=types.ModuleType("veteran_desk.ai_router");ar.resolve=lambda *a:"auto"
    ds=types.ModuleType("veteran_desk.deepseek_bridge");ds.fast_preferred=lambda:False;ds.enabled_for=lambda seat:False
    ready=types.ModuleType("veteran_desk.ai_readiness");ready.reason=lambda name,**k:None if name=="claude" else "mock-disabled"
    for name,module in (("ai_router",ar),("deepseek_bridge",ds),("ai_readiness",ready)):
        monkeypatch.setitem(sys.modules,"veteran_desk."+name,module)
        monkeypatch.setattr(veteran_desk,name,module,raising=False)
    try:
        ns["_handle_normal"](dict(id="fm-ht-owner",text="Review this desk",seat="head_trader",
                                  command=dict(mode="push",task_id="task-owner-original")))
        assert inline==[] and ns["_test_effects"]==[] and jobs==[] and hunts==set()
        record=ns["_test_learning"]["fm-ht-owner|head_trader"]
        assert record["status"]=="cancelled"
        assert record["ordinary_late_result"]["answer"]["reply"].startswith("FULL late HT paid answer")
        assert record["ordinary_late_result"]["answer"]["actions"][0]["op"]=="pause"
    finally:
        ns["_maintenance_temp"].cleanup()
