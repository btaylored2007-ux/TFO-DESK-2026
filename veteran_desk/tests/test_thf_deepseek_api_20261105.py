"""DeepSeek's bounded API transport, truthful availability and shared routing."""
import collections, concurrent.futures as cf, json, multiprocessing as mp
import subprocess, threading, time
from pathlib import Path
import pytest
from veteran_desk import deepseek_bridge as D, provider_rotation as R

@pytest.fixture
def configured(tmp_path, monkeypatch):
    # This checkout has no staff persona files, so its imported roster is 16.
    # The live allow-list is the 18 lesson_trace seats; bind the gate to that set.
    from veteran_desk import lesson_trace as LT
    monkeypatch.setattr(D,'API_SEATS', frozenset(LT.SEATS))
    monkeypatch.setattr(D,'STATE',tmp_path/'api');D.STATE.mkdir()
    monkeypatch.setattr(D,'KEY',tmp_path/'private_key');D.KEY.write_text('synthetic-test-key');D.KEY.chmod(0o600)
    D._atomic(D.STATE/'config.json',dict(enabled=True))
    D.record_result(True,time.time(),http_status=200,probe=True)
    monkeypatch.setattr(D,'brain_readback',lambda p:[])
    from veteran_desk import bridge_maintenance as B
    monkeypatch.setattr(B,'client_guard',lambda:None)
    monkeypatch.setattr(B,'mark_submission',lambda:None)
    return tmp_path

def test_success_has_provider_identity_and_safe_receipt(configured,monkeypatch):
    monkeypatch.setattr(D,'_run',lambda p,b:dict(ok=True,text='{"reply":"technical response"}',model=D.MODEL,usage={'total_tokens':7}))
    out=D.ask('read-only',seat='software_engineer',request_id='one',workload='learning')
    assert out.model=='deepseek deepseek-flash API' and out.request_id=='one'
    rows=[json.loads(x) for x in (D.STATE/'requests.jsonl').read_text().splitlines()]
    assert [r['event'] for r in rows]==['accepted','finished']
    assert rows[-1]['ok'] and rows[-1]['workload']=='learning'
    assert 'synthetic-test-key' not in (D.STATE/'requests.jsonl').read_text()

@pytest.mark.parametrize('status,kind',[(401,'authentication refused'),(402,'insufficient balance'),(403,'access refused'),(429,'rate limit'),(503,'overloaded')])
def test_failures_are_skipped_and_only_actual_success_recovers(configured,monkeypatch,status,kind):
    monkeypatch.setattr(D,'_run',lambda p,b:dict(ok=False,failure=kind,http_status=status))
    with pytest.raises(RuntimeError,match=kind):D.ask('technical',seat='software_engineer')
    assert D.reason() and D.status()['blocked']==(status in (401,402,403))
    D.record_result(True,time.time(),http_status=200,probe=True)
    assert D.reason() is None

def test_old_success_cannot_clear_newer_quota_failure(configured):
    started=time.time();D.record_result(False,started,'insufficient balance',402)
    D.record_result(True,started-1,http_status=200)
    assert D.status()['blocked'] and 'insufficient balance' in D.reason()

def test_deadline_and_cancellation_never_publish_late_success(configured,monkeypatch):
    def slow(p,b):time.sleep(.03);return dict(ok=True,text='late',model=D.MODEL)
    monkeypatch.setattr(D,'_run',slow)
    # The caller deadline is already too short: nothing is submitted.
    with pytest.raises(RuntimeError,match='deadline'):D.ask('x',seat='software_engineer',deadline=time.time()+.01)
    cancelled=threading.Event();cancelled.set()
    with pytest.raises(RuntimeError,match='cancelled'):D.ask('x',seat='software_engineer',cancel_event=cancelled)
    assert not (D.STATE/'requests.jsonl').exists()
    def cancel_during(p,b):cancelled.set();return dict(ok=True,text='late',model=D.MODEL)
    cancelled.clear();monkeypatch.setattr(D,'_run',cancel_during)
    with pytest.raises(RuntimeError,match='discarded'):D.ask('x',seat='software_engineer',cancel_event=cancelled)
    assert not any(r.get('ok') for r in map(json.loads,(D.STATE/'requests.jsonl').read_text().splitlines()))

def test_duplicate_request_is_rejected_before_second_submission(configured,monkeypatch):
    entered=threading.Event();release=threading.Event();calls=[]
    def run(p,b):calls.append(1);entered.set();assert release.wait(3);return dict(ok=True,text='ok',model=D.MODEL)
    monkeypatch.setattr(D,'_run',run)
    with cf.ThreadPoolExecutor(2) as pool:
        first=pool.submit(D.ask,'x',seat='software_engineer',request_id='same')
        assert entered.wait(2)
        with pytest.raises(RuntimeError,match='duplicate'):D.ask('x',seat='software_engineer',request_id='same')
        release.set();assert first.result(2)=='ok'
    assert len(calls)==1

@pytest.mark.parametrize('seat',['software_engineer','fixer'])
def test_api_never_accepts_trader_workload_even_for_engineering_roles(configured,monkeypatch,seat):
    calls=[];monkeypatch.setattr(D,'_run',lambda *a:calls.append(1))
    with pytest.raises(PermissionError):D.ask('round',seat=seat,workload='trader')
    assert not calls

@pytest.mark.parametrize('seat',['banks','macro','head_trader','head_of_desk','cfo','news_filings','energy','crypto','healthcare','trend','essentials_utilities','fx_commodities','price_action','tech_ai_infra','blind_challenger','devils_advocate'])
def test_paid_api_rejects_all_other_roles_before_any_external_submission(configured,monkeypatch,seat):
    calls=[];monkeypatch.setattr(D,'_run',lambda *a:calls.append(1))
    with pytest.raises(PermissionError):D.ask('question',seat=seat)
    assert not calls and not (D.STATE/'requests.jsonl').exists()

def test_two_api_slots_do_not_change_single_browser_capacity(configured,monkeypatch):
    entered=threading.Barrier(3);release=threading.Event()
    def run(p,b):entered.wait(2);assert release.wait(3);return dict(ok=True,text='ok',model=D.MODEL)
    monkeypatch.setattr(D,'_run',run)
    with cf.ThreadPoolExecutor(2) as pool:
        a=pool.submit(D.ask,'x',seat='software_engineer');b=pool.submit(D.ask,'x',seat='fixer')
        entered.wait(2);assert D.reason()=='DeepSeek busy'
        with pytest.raises(RuntimeError,match='busy|already active'):D.ask('x',seat='software_engineer')
        release.set();assert a.result(2)==b.result(2)=='ok'

def _rotate(args):
    path,n=args
    return [R.choose(['claude','chatgpt','grok','seat','deepseek'],path=path) for _ in range(n)]

def test_four_provider_rotation_is_process_safe_and_migrates_existing_counts(tmp_path):
    path=str(tmp_path/'rotation.json')
    with mp.get_context('fork').Pool(4) as pool:out=pool.map(_rotate,[(path,25)]*4)
    assert collections.Counter(x for group in out for x in group)==dict(claude=25,chatgpt=25,grok=25,deepseek=25)
    before=R.snapshot(path)
    assert R.choose(['chatgpt','deepseek'],explicit='deepseek',path=path)=='deepseek'
    assert R.snapshot(path)['auto_selected']==before['auto_selected']
    assert R.reorder([('deepseek',None),('chatgpt',None)],lambda n:'quota' if n=='deepseek' else None,path=path)[0][0]=='chatgpt'

def test_explicit_deepseek_selection_and_truthful_unavailable_fallback(configured,monkeypatch):
    from veteran_desk import ai_router as A,ai_readiness as Ready,thf_msgdesk as M
    monkeypatch.setattr(A,'prefs',lambda:{})
    monkeypatch.setattr(Ready,'reason',lambda n,**k:None if n in ('deepseek_api','chatgpt') else 'quota')
    monkeypatch.setattr(M._CD,'cooling',lambda n:False);monkeypatch.setattr(M._CD,'mark_ok',lambda n:None)
    monkeypatch.setattr(M._CD,'mark_fail',lambda *a,**k:None);monkeypatch.setattr(M,'_tcall',lambda n,fn,*a:fn())
    monkeypatch.setattr(M,'_deepseek_api',lambda *a,**k:'{"reply":"DeepSeek test"}')
    monkeypatch.setattr(M,'_chatgpt',lambda *a,**k:'{"reply":"ChatGPT test"}')
    monkeypatch.setattr(A,'_bridge_ok',lambda n:True)
    assert A.chain('x',ai='deepseek_api',seat='software_engineer')['brain']=='deepseek_api'
    monkeypatch.setattr(Ready,'reason',lambda n,**k:None if n=='chatgpt' else 'quota')
    result=A.chain('x',ai='deepseek_api',seat='software_engineer')
    assert result['brain']=='chatgpt' and any(e.startswith('deepseek_api:quota') for e in result['errs'])

def test_engineering_plan_uses_unchanged_protected_fixer_guards(configured,monkeypatch,tmp_path):
    from veteran_desk import engineering_deepseek as E,fixer_work as F
    monkeypatch.setattr(F,'ROOT',tmp_path);monkeypatch.setattr(F,'WORK',tmp_path/'staging/fixer_work')
    monkeypatch.setattr(F,'_inventory',lambda r:[])
    monkeypatch.setattr(D,'ask',lambda *a,**k:json.dumps({'changes':[{'path':'veteran_desk/book.py','sha256':None,'content':'x=1'}]}))
    with pytest.raises(ValueError,match='guarded file'):E.run('Modify the protected paper book')
    assert not (tmp_path/'veteran_desk/book.py').exists()
    with pytest.raises(PermissionError):E.run('request',owner='head_trader')
    monkeypatch.setattr(D,'ask',lambda *a,**k:json.dumps({'no_change':'Read-only review found no required source changes.'}))
    result=E.run('Read-only technical routing review')
    assert result['outcome']=='reviewed' and result['applied'] is False


def test_code_pipeline_respects_explicit_api_pick_and_can_fall_back(configured,monkeypatch):
    from veteran_desk import thf_msgdesk as M,engineering_deepseek as E,ai_readiness as Ready,fixer_work as F
    called=[]
    monkeypatch.setattr(Ready,'reason',lambda n:None if n in ('deepseek_api','chatgpt_work') else 'quota')
    monkeypatch.setattr(M._CD,'cooling',lambda n:False)
    monkeypatch.setattr(E,'run',lambda *a,**k:(called.append(k.get('provider','deepseek_api')) or {'outcome':'reviewed','text':'Technical review complete.'}))
    monkeypatch.setattr(F,'run',lambda *a,**k:(called.append(a[1]) or {'outcome':'reviewed','text':'Technical review complete.'}))
    assert M._code_hands_run('Read-only planner review',preferred='deepseek_api')[1]=='fixer_reviewed'
    assert called==['deepseek_api']
    monkeypatch.setattr(Ready,'reason',lambda n:None if n=='chatgpt_work' else 'quota')
    assert M._code_hands_run('Read-only planner review',preferred='deepseek_api')[1]=='fixer_reviewed'
    assert called==['deepseek_api','chatgpt_work']

def test_brain_receipt_requires_literal_full_own_desk_lesson(tmp_path,monkeypatch):
    import hashlib
    monkeypatch.setattr(D,'ROOT',tmp_path)
    p=tmp_path/'state/thf/seat_brain/macro/lessons.jsonl';p.parent.mkdir(parents=True)
    lesson='Complete saved original lesson with a final caution.'
    p.write_text(json.dumps(dict(seat='macro',ref='original-review',kind='review',lesson=lesson))+'\n')
    block='MY BRAIN [THF seat=macro; advisory]\nLESSONS: [review; UNVERIFIED HYPOTHESIS; ref=original-review] '+lesson+' | next check: confirm independently\n'
    proof=D.brain_readback(block)[0]
    assert proof['full_saved_lesson_in_request'] and proof['saved_lesson_sha256']==hashlib.sha256(lesson.encode()).hexdigest()
    assert proof['request_lesson_sha256']!=proof['saved_lesson_sha256']
    assert not D.brain_readback(block.replace(lesson,lesson[:12]))[0]['full_saved_lesson_in_request']
    assert not D.brain_readback(block.replace('seat=macro','seat=banks'))[0]['full_saved_lesson_in_request']

def test_dashboard_status_and_message_keep_explicit_deepseek_identity(configured,monkeypatch,tmp_path):
    from veteran_desk import thf_chat_home as H,thf_funder as F
    monkeypatch.setattr(F,'SEAT_IDS', set(F.SEAT_IDS) | {'software_engineer','cfo'})
    rows=H.ai_status(cd={},gpt={},sgw={},hb={},probe={})
    ds=next(r for r in rows if r['id']=='deepseek_api')
    assert 'DeepSeek API' in ds['name'] and ds['state']=='up'
    monkeypatch.setattr(F,'F',str(tmp_path/'messages.jsonl'));monkeypatch.setattr(F,'LK',str(tmp_path/'messages.lock'))
    result=F.post('seat','Read-only technical question',seat='software_engineer',ai='deepseek_api',command={'mode':'ask'},dedupe_key='same-owner-question')
    again=F.post('seat','Read-only technical question',seat='software_engineer',ai='deepseek_api',command={'mode':'ask'},dedupe_key='same-owner-question')
    assert result['ai']=='deepseek_api' and again['id']==result['id']
    assert len(Path(F.F).read_text().splitlines())==1
    with pytest.raises(ValueError,match='restricted'):F.post('seat','Question',seat='head_trader',ai='deepseek_api')

def test_worker_is_killed_and_reaped_before_timeout_returns(monkeypatch):
    events=[]
    class Child:
        returncode=-9
        def communicate(self,*a,**k):
            events.append('communicate')
            if 'timeout' in k:raise subprocess.TimeoutExpired('synthetic',.01)
            return '',None
        def kill(self):events.append('kill')
    monkeypatch.setattr(D.subprocess,'Popen',lambda *a,**k:Child())
    with pytest.raises(subprocess.TimeoutExpired):D._run({'prompt':'synthetic'},.01)
    assert events==['communicate','kill','communicate']
