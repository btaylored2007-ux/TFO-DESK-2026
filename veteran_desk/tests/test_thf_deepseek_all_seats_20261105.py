"""Owner-authorised all-seat API mode; legacy restricted configuration stays fail-closed."""
import concurrent.futures as cf
import json
import threading
import time
from pathlib import Path
import pytest
from veteran_desk import deepseek_bridge as D,provider_rotation as R

@pytest.fixture
def wide_api(tmp_path,monkeypatch):
    # Live config.allowed_seats is these 18. Local persona files do not include staff.
    from veteran_desk import lesson_trace as LT
    from veteran_desk import thf_funder as F
    monkeypatch.setattr(D,'API_SEATS', frozenset(LT.SEATS))
    monkeypatch.setattr(F,'SEAT_IDS', set(F.SEAT_IDS) | set(LT.SEATS))
    monkeypatch.setattr(D,'STATE',tmp_path/'api');D.STATE.mkdir()
    monkeypatch.setattr(D,'KEY',tmp_path/'test-key');D.KEY.write_text('synthetic-private-test-key');D.KEY.chmod(0o600)
    D._atomic(D.STATE/'config.json',dict(enabled=True,allowed_seats=sorted(D.API_SEATS),trader_enabled=True,capacity=4,prefer_fast_api=True))
    D.record_result(True,time.time(),http_status=200,probe=True)
    from veteran_desk import bridge_maintenance as B
    monkeypatch.setattr(B,'client_guard',lambda:None);monkeypatch.setattr(B,'mark_submission',lambda:None)
    return tmp_path

@pytest.mark.parametrize('seat',['banks','blind_challenger','cfo','crypto','devils_advocate','energy','essentials_utilities','fixer','fx_commodities','head_of_desk','head_trader','healthcare','macro','news_filings','price_action','software_engineer','tech_ai_infra','trend'])
@pytest.mark.parametrize('workload',['chat','trader'])
def test_all_authorised_seats_have_advisory_access(wide_api,monkeypatch,seat,workload):
    seen=[]
    def run(payload,budget):seen.append(payload);return dict(ok=True,text='{"marker":"advisory"}',model=D.MODEL)
    monkeypatch.setattr(D,'_run',run)
    answer=D.ask('Technical check only; no orders',seat=seat,workload=workload,request_id=seat+'-'+workload)
    assert answer=='{"marker":"advisory"}' and seen[0]['seat']==seat and seen[0]['workload']==workload
    assert D.enabled_for(seat,workload)

@pytest.mark.parametrize('seat',['desk','unknown','advisor_chatgpt','../../banks'])
def test_unknown_roles_cannot_submit_paid_requests(wide_api,monkeypatch,seat):
    calls=[];monkeypatch.setattr(D,'_run',lambda *a:calls.append(1))
    with pytest.raises(PermissionError):D.ask('x',seat=seat)
    assert not calls and not (D.STATE/'requests.jsonl').exists()

def test_explicit_configuration_is_required_for_expansion(wide_api,monkeypatch):
    D._atomic(D.STATE/'config.json',dict(enabled=True))
    assert D.allowed_seats()==D.LEGACY_API_SEATS
    calls=[];monkeypatch.setattr(D,'_run',lambda *a:calls.append(1))
    with pytest.raises(PermissionError):D.ask('x',seat='banks')
    with pytest.raises(PermissionError):D.ask('x',seat='software_engineer',workload='trader')
    assert not calls

def test_four_slots_are_bounded_across_distinct_roles(wide_api,monkeypatch):
    entered=threading.Barrier(5);release=threading.Event()
    def run(payload,budget):entered.wait(3);assert release.wait(4);return dict(ok=True,text='ok',model=D.MODEL)
    monkeypatch.setattr(D,'_run',run)
    with cf.ThreadPoolExecutor(4) as pool:
        jobs=[pool.submit(D.ask,'technical',seat=seat,request_id=seat) for seat in ['banks','cfo','software_engineer','fixer']]
        entered.wait(3)
        assert D.reason()=='DeepSeek busy'
        with pytest.raises(RuntimeError,match='busy'):D.ask('technical',seat='crypto')
        release.set();assert [j.result(3) for j in jobs]==['ok']*4
    assert D.reason() is None

def test_fast_auto_preference_preserves_explicit_picks_and_unavailable_fallback(tmp_path):
    path=str(tmp_path/'rotation.json');pairs=[('chatgpt',None),('deepseek_api',None),('claude',None)]
    assert R.reorder(pairs,lambda n:None,path=path,preferred='deepseek_api')[0][0]=='deepseek_api'
    assert R.snapshot(path)['auto_selected']['deepseek']==1
    assert not R.snapshot(path)['explicit_selected'].get('deepseek')
    assert R.reorder(pairs,lambda n:None,path=path,explicit='chatgpt',preferred='deepseek_api')[0][0]=='chatgpt'
    assert R.reorder(pairs,lambda n:'busy' if n=='deepseek_api' else None,path=path,preferred='deepseek_api')[0][0]!='deepseek_api'

def test_alias_picker_and_broadcast_use_api_for_authorised_roles(wide_api,monkeypatch):
    from veteran_desk import ai_router as A,thf_funder as F,thf_chat_home as H
    assert A.norm('deepseek')=='deepseek_api'
    for seat in sorted(D.API_SEATS):assert 'deepseek_api' in A.seat_order('auto',None,seat)
    monkeypatch.setattr(F,'F',str(wide_api/'messages.jsonl'));monkeypatch.setattr(F,'LK',str(wide_api/'messages.lock'))
    for seat in sorted(D.API_SEATS):
        row=F.post('seat','Read-only check',seat=seat,ai='deepseek',command={'mode':'ask'})
        assert row['ai']=='deepseek_api'
    assert F.post('all','Read-only check',ai='deepseek_api',command={'mode':'ask'})['ai']=='deepseek_api'
    rows=H.ai_status(cd={},gpt={},sgw={},hb={},probe={})
    assert not any(r['id']=='deepseek' for r in rows)
    row=next(r for r in rows if r['id']=='deepseek_api')
    assert row['state']=='up' and '18 authorised seats' in row['note'] and '4 parallel slots' in row['note']

def test_native_trader_fast_path_uses_api_without_other_models(wide_api,monkeypatch):
    from veteran_desk import llm_backend as L,ai_readiness as Ready
    monkeypatch.setenv('VET_GROK_QUEUE_IN_TESTS','1');monkeypatch.setenv('VET_AI_CHAIN_IN_TESTS','1');monkeypatch.setenv('THF_DESK','1')
    monkeypatch.setattr(L,'Q',str(wide_api/'queue'));monkeypatch.setattr(L,'BRAIN_LOG',str(wide_api/'brain-log.jsonl'))
    monkeypatch.setattr(Ready,'reason',lambda *a,**k:None)
    def forbidden(*a,**k):raise AssertionError('A slower provider was called before the ready API')
    monkeypatch.setattr(L,'_ask_claude',forbidden);monkeypatch.setattr(L,'_ask_chatgpt',forbidden);monkeypatch.setattr(L,'_ask_grok_authenticated',forbidden)
    calls=[]
    def run(payload,budget):calls.append(payload);return dict(ok=True,text='{"marker":"native-trader"}',model=D.MODEL)
    monkeypatch.setattr(D,'_run',run)
    L.LAST.request_deadline=time.time()+20;L.LAST.cancel_event=None;L.LAST.paired_trader=True
    try:
        answer,rid,status=L.grok_call('technical','Technical transport test, no orders',seat='banks',desk='thf',timeout=10)
    finally:
        L.LAST.request_deadline=None;L.LAST.cancel_event=None;L.LAST.paired_trader=False
    assert status=='ok' and json.loads(answer)['marker']=='native-trader'
    assert len(calls)==1 and calls[0]['seat']=='banks' and calls[0]['workload']=='trader'
    done=json.loads((Path(L.Q)/'done'/(rid+'.json')).read_text())
    assert done['engine']=='deepseek_api' and done['paper_only'] is True

def test_all_role_api_does_not_expand_code_writer(wide_api):
    from veteran_desk import engineering_deepseek as E,fixer_work as F
    assert F.OWNERS=={'software_engineer','fixer'}
    for seat in D.API_SEATS-F.OWNERS:
        with pytest.raises(PermissionError):E.run('Technical read-only request',owner=seat)
