"""Engineering routing regression tests. No service imports or live state writes.

The harness executes the actual routing/admission functions from source with
in-memory services. The guarded deployment still must run its full import suite.
"""
import ast
import builtins
import concurrent.futures as cf
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

from veteran_desk import intent as I
from veteran_desk import bridge_maintenance as BM

SOURCE = Path(__file__).resolve().parents[1] / "thf_msgdesk.py"
HUNT = """From Brett: THF PAPER trading only. For the London and US sessions, find your two valid A-grade setups and arm them through the existing guarded paper-trading path. Keep researching, screening and refining candidates until you find two that genuinely meet A-grade requirements. Do not stop the research just because the first candidates fail.

A setup may be armed only when every existing entry, risk, sizing, liquidity and stop check passes. Keep those rules and stops unchanged. Do not force a trade, lower the grade, bypass any gate, trade live, or treat this request as permission to change limits. If a candidate fails, keep looking for a valid one.

Avoid duplicating another seat's work or an already armed setup. Report back only after your two valid paper setups are armed, with symbols, direction, entries, stops, targets, sizing and verification. If an actionable data, access or safety blocker makes completion impossible, report that specific blocker instead of claiming completion."""
SEATS = dict(banks="Victor Crane", crypto="Kai Navarro", energy="Rex Calder",
             essentials_utilities="Nora Whitlock", fx_commodities="Lena Voss",
             healthcare="Iris Mendel", macro="Theo Ashby", news_filings="Quinn Mercer",
             tech_ai_infra="Nico Hart", price_action="Dex Romano (price action)", trend="Dex Romano (trend)")

def harness(seat="banks", reply=None):
    rows, jobs, hunts = [], [], set()
    learning, saved, model_calls, effects = {}, {}, [], []
    learning_lock = threading.Lock()
    def retry_rw(fn):
        with learning_lock: return fn(learning)
    def record_learning(seat, result, mid, model):
        if not result.get("brain_review"): return None
        saved[(mid,seat)] = json.loads(json.dumps(result))
        return saved[(mid,seat)]
    class ImmediateThread:
        def __init__(self, target, args=(), kwargs=None, **_):
            self.target, self.args, self.kwargs = target, args, kwargs or {}
            self.started,self.finished=False,False
        def start(self):
            if self.started:raise RuntimeError("Test thread already started")
            self.started=True
            try:self.target(*self.args, **self.kwargs)
            finally:self.finished=True
        def join(self,timeout=None):
            if not self.started:raise RuntimeError("Test thread was not started")
            assert self.finished,"Synchronous test work must finish before join returns"
    no = lambda *a, **k: None
    real_import=builtins.__import__
    isolated_modules={
        "seat_brain":types.SimpleNamespace(after_chat=no,journal=no),
        "seat_mail":types.SimpleNamespace(ack=no),
        "thf_chat_tasks":types.SimpleNamespace(record_learning=record_learning,
            learning_saved=lambda seat,mid:saved.get((mid,seat)),load=lambda:{"jobs":{}}),
        "ai_router":types.SimpleNamespace(resolve=lambda ai,seat:"mock",order=lambda *a:["mock"],FAN=["mock"]),
        "ai_readiness":types.SimpleNamespace(reason=lambda name:None),
        "ai_cooldown":types.SimpleNamespace(_load=lambda:{}),
    }
    def isolated_import(name,globals=None,locals=None,fromlist=(),level=0):
        if name=="veteran_desk" and fromlist and all(item in isolated_modules for item in fromlist):
            return types.SimpleNamespace(**{item:isolated_modules[item] for item in fromlist})
        return real_import(name,globals,locals,fromlist,level)
    test_builtins=dict(vars(builtins),__import__=isolated_import)
    ns = dict(__builtins__=test_builtins,re=re, time=time, os=os, json=json, hashlib=hashlib, cf=cf,
              threading=types.SimpleNamespace(Thread=ImmediateThread),
              SE="software_engineer", HT="head_trader", TRADE=set(SEATS),
              PUSH=re.compile(r"push|hunt|setup|profit|trade|market|theme|harder|find|arm|go\b", re.I),
              MARCUS=re.compile(r"(?!)"), LABRX=re.compile(r"^lab:(.+)", re.I),
              HANDLE_TO=3, CH_LOCK=threading.Lock(), CH_STATE={}, CH_KEYS={},
              now=lambda: "test-time", log=no, _ch_dup=lambda *a: None,
              _chq_read=lambda: list(jobs), _chq_write=lambda q: jobs.__setitem__(slice(None), q),
              _ch_ensure=no, _ch_stale=lambda: 3300, _retry_done=no,
              targets=lambda m: [m.get("seat") or seat], tadd=lambda **r: rows.append(r) or r,
              tread=lambda: list(rows), _brain_back=lambda s,d,t:t, _seat_mail=no,
              _ready_choice=no, _capacity=lambda e:False, _brain_down=no, _defer=lambda *a:None,
              code_reply=lambda *a:"fallback", fmt=lambda d:d.get("reply", "test"),
              TF=types.SimpleNamespace(delivery=no, ack=no, load=lambda n:[]),
              SETUPS="/not-used-by-tests")
    ns.update(_retry_rw=retry_rw, RETRY="/in-memory-routing-test", _LEARN_LIVE_RETRY="/not-a-test-path",
              _LEARN_ACTIVE=set(), _LEARN_ACTIVE_LOCK=threading.Lock(),
              _LEARN_SLOTS=threading.BoundedSemaphore(2), LEARN_MAX_ATTEMPTS=12, LEARN_DEADLINE_S=48*3600,
              _LEARN_TERMINAL={"review_saved","cancelled","expired","invalid_review"},
              _test_learning=learning, _test_saved=saved, _test_model_calls=model_calls, _test_effects=effects,
              _chat_actions=lambda *a,**k:effects.append(("actions",a)) or "")
    functions = {"_orig_text", "hands_intent", "hands_key", "_command_mode", "_is_push", "_push_targets",
                 "code_hands", "handle", "_handle_normal", "wants_hands", "_enqueue_hunt", "_ch_exec", "_rkey",
                 "_learn_register", "_learn_update", "_learn_control", "_learn_wait", "_learn_ready",
                 "_learn_one", "_learn_handle"}
    tree = ast.parse(SOURCE.read_text())
    module = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in functions], type_ignores=[])
    exec(compile(module, str(SOURCE), "exec"), ns)
    ns['_BM']=BM
    ns['_maintenance_temp']=tempfile.TemporaryDirectory()
    class ImmediateTracker(BM.Tracker):
        # Routing assertions observe the intended in-memory synchronous service.
        # Real tracker/thread concurrency is covered by the maintenance tests.
        def thread(self,target,args=(),kwargs=None,**options):
            context,budget=BM.permit(),BM.attempt()
            def run():
                token=self.begin("owned_thread",force=True)
                try:
                    with BM.use_permit(context),BM.use_attempt(budget):
                        return target(*args,**(kwargs or {}))
                finally:self.end(token)
            return ImmediateThread(run)
    ns['_MAINT']=ImmediateTracker(str(Path(ns['_maintenance_temp'].name)/'status.json'))
    ns['_MREC']=BM.Recovery(ns['_MAINT'],retry_rw,lambda:[],lambda:rows,lambda *a:None,lambda:{})
    actual_hunt = ns["_enqueue_hunt"]
    def queue_hunt(s,m):
        added=(s,m) not in hunts;hunts.add((s,m));return added
    ns["_enqueue_hunt"] = queue_hunt
    ns["ask"] = lambda *a:model_calls.append(a) or dict(reply="test answer", _brain="mock", **(reply or {}))
    ns["ht_review"] = ns["ask"]
    return ns, rows, jobs, hunts, actual_hunt

class IntentRegression(unittest.TestCase):
    def test_every_original_trader_request_is_not_engineering(self):
        for seat, name in SEATS.items():
            with self.subTest(seat=seat):
                raw=name+":\n"+HUNT
                self.assertFalse(I.wants_fix(raw))
                self.assertFalse(I.wants_fix("ACTING FOR SEAT "+seat+" (fix/update).\n"+raw))

    def test_legacy_questions_stay_nonmutating(self):
        for text in ("Update", "Team update me", "Marcus is any desk green", "Why is the brain down",
                     "Can you fix the engine?", "Is Claude on all the fixes and updates to the engine?",
                     "Brett said: Why is the feed down || Simon says: fix the engine"):
            with self.subTest(text=text):
                self.assertFalse(I.wants_fix(text)); self.assertEqual(I.kind(text), "question")

    def test_negations_trading_examples_and_status_cannot_grant_work(self):
        cases=("Find two setups. Do not treat this request as permission to change limits.",
               "Hunt hard; do not change code, risk limits or stops.", "Do not change the engine.",
               "Don't fix the feed.", "Never deploy the dashboard.", "No changes to the engine.",
               "Make a shortlist of two valid paper setups.", "The market is down; keep looking for setups.",
               "Create two valid setups.", "Change the stop on the paper trade.", "Set up two trades.",
               "Report the percentage change in price.", "Keep the engine unchanged.",
               "If the feed is broken, fix it.", 'The example says "Fix the engine".',
               '"Fix the engine"', "Fixing the engine was completed yesterday.",
               "The last code change was reviewed.", "Code is available.", "Run a fresh hunt.",
               "US IS OPEN", "Report stale trade prices; do not change feeds.",
               "No code changes. The feed is stale.", "Report missing feed data.",
               "Show me the broken dashboard.", "The trader said: 'the feed is broken'.",
               "Get the feed status.", "Get the dashboard URL.", "Make a diagram of the engine.",
               "Do not edit code. Fix the broken feed.", "Avoid code changes; the feed is down.",
               "The feed is stale.", "The dashboard is broken.",
               'The note says: "First observe.\nFix the engine."',
               "Example:\n```\nFix the engine.\n```", "Feed down, don’t fix it.",
               "I need you to get the feed status.", "We need to get the dashboard URL.",
               "Update me about the feed.", "Continue researching the feed issue.", "Continue researching the feed repair.",
               "Fix the feed. Do not deploy anything.", "Fix the feed, but just a plan.",
               "Fix the engine. Draft only.", "I need you to update me about the feed.", "No modifications to code. Fix the dashboard.")
        for text in cases:
            with self.subTest(text=text):self.assertFalse(I.wants_fix(text))

    def test_affirmative_engineering_and_limited_safety_constraints(self):
        cases=("Fix the broken feed.", "Please fix the feed; do not change risk limits.",
               "Fix the feed without changing risk limits.", "Fix the feed and do not change risk limits.",
               "Do not change limits, but fix the dashboard.", "NEWS FEED IS DOWN - PLEASE FIX",
               "Desk health needs fixing", "Get them all fixed up", "Dashboard and numbers not working on dashboard",
               "Add a CFO seat to THF", "Marcus get all the feeds fixed", "please fix the upload 403",
               "Redesign the THF dashboard CHAT section", "Patch veteran_desk/intent.py", "Fix the dashboard?",
               "Implement a CSV parser.", "Create a strategy backtester.",
               "Fix the feed, it does not update.", "Fix the OANDA feed, it has not updated since Friday", "Please update the feed collector.",
               "Update the dashboard.", "Simon, please continue the existing THF feed-health work.")
        for text in cases:
            with self.subTest(text=text):self.assertTrue(I.wants_fix(text, ["dashboard.png"] if text.startswith("Dashboard") else None))

    def test_images_do_not_grant_permission(self):
        self.assertFalse(I.wants_fix("Review this chart", ["chart.png"]))
        self.assertFalse(I.wants_fix("Hunt; do not change the dashboard", ["dashboard.png"]))
        self.assertTrue(I.wants_fix("Fix the broken dashboard button", ["dashboard.png"]))

    def test_ask_and_learn_override_text(self):
        for mode in ("ask", "learn"):
            self.assertFalse(I.allows_code("Fix the engine", command_mode=mode))

class RoutingIntegration(unittest.TestCase):
    def test_hunts_preserved_and_no_code_jobs_for_all_eleven_seats(self):
        for seat, name in SEATS.items():
            for mode in (None, "push"):
                with self.subTest(seat=seat, mode=mode):
                    ns, rows, jobs, hunts, _=harness(seat)
                    msg=dict(id="source-"+seat,text=name+":\n"+HUNT,seat=seat,kind="seat")
                    if mode:msg["command"]=dict(mode=mode)
                    ns["handle"](msg)
                    self.assertEqual(jobs, [])
                    self.assertEqual(hunts, {(seat,msg["id"])})
                    self.assertFalse(any(r.get("from")=="code_hands" for r in rows))

    def test_generated_work_request_cannot_escalate_a_hunt(self):
        ns, _, jobs, _, _=harness("software_engineer", dict(work_request="Fix the engine now"))
        ns["handle"](dict(id="source",text=HUNT,seat="software_engineer",kind="seat"))
        self.assertEqual(jobs, [])

    def test_admission_checks_original_not_ai_generated_plan(self):
        ns, _, jobs, _, _=harness()
        answer,mode=ns["code_hands"]("Fix the engine", source_text=HUNT, root="source")
        self.assertEqual(mode,"intent_rejected");self.assertEqual(jobs,[])
        self.assertIn("not queued", answer)

    def test_direct_admission_refuses_unsafe_legacy_wrappers(self):
        ns, _, jobs, _, _=harness()
        for wrapped in ("ACTING FOR SEAT banks (fix/update).\n"+HUNT,
                        "SOFTWARE ENGINEER (Simon) work order: Fix the engine\nOriginal request: "+HUNT,
                        "Claude plan -> Implement this. Brett: "+HUNT+" || Claude: Fix the engine",
                        "Claude plan -> Implement generated plan without original source"):
            with self.subTest(wrapped=wrapped[:50]):
                self.assertEqual(ns["code_hands"](wrapped)[1],"intent_rejected")
        self.assertEqual(jobs, [])

    def test_positive_fixer_and_engineer_requests_queue_once(self):
        for seat in ("fixer","software_engineer","banks"):
            for mode in (None,"push"):
                with self.subTest(seat=seat,mode=mode):
                    ns, _, jobs, _, _=harness(seat)
                    msg=dict(id="source",text="Please fix the broken feed; do not change risk limits.",seat=seat)
                    if mode:msg["command"]=dict(mode=mode)
                    ns["handle"](msg)
                    self.assertEqual(len(jobs),1)
                    self.assertEqual(jobs[0]["source_text"],msg["text"])
                    self.assertEqual(jobs[0]["command_mode"],mode)
                    self.assertIn("do not change risk limits", jobs[0]["text"])

    def test_ask_and_learning_never_queue_even_generated_work(self):
        for seat in ("fixer","software_engineer","banks"):
            for mode in ("ask","learn"):
                with self.subTest(seat=seat,mode=mode):
                    reply=dict(work_request="Fix the engine")
                    if mode=="learn":
                        reply.update(brain_review=dict(lesson="Review only",hypothesis="Unverified",next_check="Check evidence"),
                                     actions=["trade"],ht_actions=["trade"],lab_idea="build",to_seats=["macro"],
                                     to_marcus="relay",report_to_marcus=True,checked=[dict(grade="A")])
                    ns, rows, jobs, hunts, _=harness(seat,reply)
                    ns["handle"](dict(id="source",text="Fix the engine and hunt setups",seat=seat,command=dict(mode=mode)))
                    self.assertEqual(jobs,[]);self.assertEqual(hunts,set());self.assertEqual(ns["_test_effects"],[])
                    self.assertEqual(len(ns["_test_model_calls"]),1)
                    if mode=="learn":
                        self.assertEqual(ns["_test_learning"]["source|"+seat]["status"],"review_saved")
                        saved=ns["_test_saved"][("source",seat)]
                        self.assertEqual(saved["brain_review"],reply["brain_review"])
                        for field in ("actions","ht_actions","work_request","lab_idea","to_seats","to_marcus","report_to_marcus","checked"):
                            self.assertNotIn(field,saved)
                        self.assertEqual(len([r for r in rows if r.get("kind")=="learning_saved"]),1)

    def test_real_hunt_queue_deduplicates_original_message(self):
        ns, _, _, _, actual=harness()
        with tempfile.TemporaryDirectory() as d:
            ns["S"]=d
            actual("banks","source");actual("banks","source")
            rows=[json.loads(s) for s in (Path(d)/"hunt_requests.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows),1);self.assertTrue(rows[0]["push"])

    def test_classifier_failure_is_fail_closed(self):
        ns, _, _, _, _=harness()
        original=I.wants_fix
        try:
            def broken(*a,**k):raise RuntimeError("classifier unavailable")
            I.wants_fix=broken
            self.assertFalse(ns["wants_hands"]("Fix engine code",["screenshot.png"]))
        finally:I.wants_fix=original

    def test_system_health_requires_explicit_registered_origin(self):
        ns, _, jobs, _, _=harness()
        kwargs=dict(via="sgp_health",root="sgp-health-feed",key="sgphealth:feed")
        self.assertEqual(ns["code_hands"]("Find the cause",**kwargs)[1],"intent_rejected")
        self.assertEqual(ns["code_hands"]("Find the cause",source_kind="system_health",**kwargs)[1],"code")
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]["source_kind"],"system_health")
        for altered in (dict(via="fixer"),dict(root="user-message"),dict(key="unknown"),dict(command_mode="ask")):
            args=dict(kwargs,**altered)
            self.assertEqual(ns["code_hands"]("Find the cause",source_kind="system_health",**args)[1],"intent_rejected")

    def test_existing_health_producer_passes_registered_origin(self):
        path=SOURCE.with_name("sgp_health.py")
        tree=ast.parse(path.read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="queue_job")
        calls=[n for n in ast.walk(fn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=="code_hands"]
        self.assertEqual(len(calls),1)
        values={k.arg:ast.literal_eval(k.value) for k in calls[0].keywords if isinstance(k.value,ast.Constant)}
        self.assertEqual(values["source_kind"],"system_health")
        self.assertEqual(values["via"],"sgp_health")

    def test_external_message_fields_cannot_claim_system_origin(self):
        ns, _, jobs, _, _=harness()
        ns["handle"](dict(id="sgp-health-user",text=HUNT,seat="banks",
                          source_kind="system_health",via="sgp_health",key="sgphealth:user"))
        self.assertEqual(jobs,[])

    def test_full_original_restrictions_reach_execution(self):
        ns, _, _, _, _=harness()
        captured=[]
        with tempfile.TemporaryDirectory() as d:
            ns.update(CHRUN=str(Path(d)/"run.json"), LASTM=types.SimpleNamespace(fixer_receipt=None),
                      _ch_verify_rollback=lambda t:[], _ch_fixer_backups=lambda t:[],
                      _code_hands_run=lambda text,images:(captured.append(text) or "test result","mock"))
            original="Fix the feed. "+"Context. "*400+"Do not change risk limits or stops."
            ns["_ch_exec"](dict(id="test",text="Generated abbreviated plan",source_text=original,via="fixer"))
            self.assertIn(original,captured[0]);self.assertFalse((Path(d)/"run.json").exists())

    def test_advisor_uses_original_intent_and_respects_ask(self):
        path=SOURCE.parents[1]/"queue_answerer_sgp.py"
        tree=ast.parse(path.read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="adv_work")
        for text,mode,count in ((HUNT,None,0),("Fix the feed; do not change risk limits.",None,1),
                                ("Fix the feed","ask",0),("No code changes. The feed is stale.",None,0)):
            with self.subTest(text=text[:45],mode=mode):
                posts=[];answers=[];prompts=[]
                fm=types.ModuleType("veteran_desk.thf_funder")
                fm.adv_thread=lambda trade:[]
                fm.post=lambda *a,**k:posts.append((a,k))
                fm.adv_add=lambda *a,**k:answers.append((a,k))
                admissions=[]
                md=types.SimpleNamespace(SE="software_engineer",tread=lambda:[],tadd=lambda **kw:None,
                    code_hands=lambda *a,**k:(admissions.append((a,k)) or ("durable receipt","code")))
                real_import=builtins.__import__
                def advisor_import(name,globals=None,locals=None,fromlist=(),level=0):
                    if name=="veteran_desk" and "thf_funder" in fromlist:
                        return types.SimpleNamespace(thf_funder=fm)
                    if name=="veteran_desk" and "thf_msgdesk" in fromlist:
                        return types.SimpleNamespace(thf_msgdesk=md)
                    return real_import(name,globals,locals,fromlist,level)
                ns=dict(__builtins__=dict(vars(builtins),__import__=advisor_import),json=json,log=lambda *a:None,_ADV_BUSY=set(),
                        ask=lambda path,prompt,to:prompts.append(prompt) or "Plan details",
                        _CD=types.SimpleNamespace(cooling=lambda p:False),_BM=BM)
                exec(compile(ast.Module(body=[fn],type_ignores=[]),str(path),"exec"),ns)
                msg=dict(id="advisor-source",trade_id="trade1",advisor="grok",text=text,role="brett")
                if mode:msg["command"]=dict(mode=mode)
                ns["adv_work"](msg)
                self.assertEqual(posts,[]);self.assertEqual(len(admissions),count);self.assertEqual(len(answers),1)
                if count:
                    self.assertEqual(admissions[0][1]["source_text"],text)
                    self.assertEqual(admissions[0][1]["root"],msg["id"])
                    self.assertEqual(admissions[0][1]["reply_to"],msg["id"])
                    self.assertEqual(admissions[0][1]["command_mode"],mode)
                    self.assertEqual(admissions[0][1]["source_kind"],"user")
                self.assertEqual("Brett wants a REAL code/engine change" in prompts[0],bool(count))

if __name__=="__main__":unittest.main()

REVIEWED_LEARNING_TEST_REVISION = "dot-learning-v5"
