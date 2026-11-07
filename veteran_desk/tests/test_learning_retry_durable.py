"""AST-isolated learning lane tests. No application imports, providers or live writes."""
import ast
import concurrent.futures
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch
from veteran_desk import bridge_maintenance as BM

BASE=pathlib.Path(__file__).resolve().parents[1]
def functions(path,names,namespace):
    tree=ast.parse(path.read_text())
    nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),namespace)
    return namespace

class DurableLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=pathlib.Path(self.temp.name);self.rows=[];self.deliveries=[];self.calls=[];self.status='active';self.fail_save=False;self.saved={}
        self.msg=dict(id='fm-review-original',text='Original review request',seat='macro',command=dict(mode='learn',task_id='task-daily',occurrence=1))
        self.result=dict(reply='Review',_brain='chatgpt',_model='chatgpt web',brain_review=dict(lesson='Lesson',hypothesis='Hypothesis',next_check='Check',evidence=[]),actions=['bad'],work_request='bad')
        def ask(seat,msg,ctx):self.calls.append((seat,msg));return dict(self.result)
        def record(seat,result,mid,model):
            if self.fail_save:raise OSError('disk unavailable')
            if not result.get('brain_review'):return None
            self.saved[(mid,seat)]={'ref':'learning:'+mid+':'+seat,'kind':'review'}
            return self.saved[(mid,seat)]
        tasks=types.SimpleNamespace(load=lambda:{'jobs':{'task-daily':{'status':self.status}}},learning_saved=lambda seat,mid:self.saved.get((mid,seat)),record_learning=record)
        self.tasks=tasks
        self.readiness=types.SimpleNamespace(reason=lambda name:None)
        self.cd=types.SimpleNamespace(_load=lambda:{})
        package=types.ModuleType('veteran_desk');package.thf_chat_tasks=tasks;package.ai_router=types.SimpleNamespace(resolve=lambda ai,seat:ai or 'auto',order=lambda choice,images:['chatgpt'],FAN=['chatgpt']);package.ai_readiness=self.readiness;package.ai_cooldown=self.cd
        self.patcher=patch.dict(sys.modules,{'veteran_desk':package});self.patcher.start();self.addCleanup(self.patcher.stop)
        ns=dict(_BM=BM,json=json,os=os,time=time,threading=threading,RETRY=str(self.root/'retry.json'),_RLOCK=threading.Lock(),_LEARN_ACTIVE=set(),_LEARN_ACTIVE_LOCK=threading.Lock(),_LEARN_SLOTS=threading.BoundedSemaphore(2),LEARN_MAX_ATTEMPTS=12,LEARN_DEADLINE_S=48*3600,_LEARN_TERMINAL={'review_saved','cancelled','expired','invalid_review'},_LEARN_LIVE_RETRY='/no-live-test-path',SE='software_engineer',RETRY_MAX=6,STALE_S=1640,RETRY_GAP=240,RETRY_GAP_MAX=600,now=lambda:'NOW',log=lambda x:None,ask=ask,targets=lambda m:[m.get('seat') or 'macro'],_command_mode=lambda m:(m.get('command') or {}).get('mode'),tread=lambda:list(self.rows),tadd=lambda **row:self.rows.append(row),jl=lambda p,default:json.loads(pathlib.Path(p).read_text()) if pathlib.Path(p).exists() else default)
        ns['TF']=types.SimpleNamespace(delivery=lambda *a:self.deliveries.append(a),load=lambda n:[])
        names={'_rkey','_retry_rw','_defer','_retry_done','_inflight','_retry_due','_retry_boot'}|{n.name for n in ast.parse((BASE/'thf_msgdesk.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name.startswith('_learn_')}
        self.m=functions(BASE/'thf_msgdesk.py',names,ns)
        self.m['_MAINT']=BM.Tracker(str(self.root/'maintenance_status.json'))
        self.m['_MREC']=BM.Recovery(self.m['_MAINT'],self.m['_retry_rw'],lambda:[],lambda:self.rows,
                                    lambda *args:None,lambda:{})
        self.m['_capacity']=lambda error:'busy' in str(error)

    def state(self):return json.loads((self.root/'retry.json').read_text())
    def value(self):return self.state()['fm-review-original|macro']
    def update(self,**kw):self.m['_learn_update'](self.msg['id'],'macro',**kw)
    def run_one(self):self.m['_learn_one'](self.msg,'macro')
    def register(self):return self.m['_learn_register'](self.msg,'macro')
    def test_success_is_exact_ref_and_no_execution_fields(self):
        original=self.tasks.record_learning
        def record(seat,result,mid,model):
            self.assertNotIn('actions',result);self.assertNotIn('work_request',result)
            return original(seat,result,mid,model)
        self.tasks.record_learning=record
        self.run_one();self.run_one()
        self.assertEqual(len(self.calls),1);self.assertEqual(self.value()['status'],'review_saved');self.assertEqual(len(self.saved),1)
        self.assertEqual(len([r for r in self.rows if r.get('kind')=='learning_saved']),1)
    def test_save_failure_retries_saved_result_without_model(self):
        self.fail_save=True;self.run_one();self.assertEqual(self.value()['status'],'save_failed');self.assertIn('result',self.value())
        self.fail_save=False;self.update(due=0);self.run_one()
        self.assertEqual(len(self.calls),1);self.assertEqual(self.value()['status'],'review_saved')
    def test_exact_ref_recovery_skips_model(self):
        self.register();self.saved[(self.msg['id'],'macro')]={'kind':'review','ref':'existing'};self.run_one()
        self.assertEqual(self.calls,[]);self.assertEqual(self.value()['status'],'review_saved')
    def test_paused_resume_cancel_and_oneoff_sent(self):
        self.status='paused';self.run_one();self.assertEqual(self.calls,[]);self.assertEqual(self.value()['status'],'paused')
        self.status='sent';self.update(due=0);self.run_one();self.assertEqual(len(self.calls),1)
        self.msg['id']='fm-cancel';self.status='cancelled';self.run_one();self.assertEqual(len(self.calls),1)
    def test_missing_task_blocks(self):
        self.tasks.load=lambda:{'jobs':{}};self.run_one();self.assertEqual(self.calls,[]);self.assertEqual(self.value()['status'],'blocked')
    def test_deadline_and_attempt_limit_retain_request(self):
        self.register();self.update(deadline=time.time()-1);self.run_one();self.assertEqual(self.value()['status'],'expired');self.assertEqual(self.value()['message']['text'],'Original review request');self.assertEqual(self.calls,[])
        self.msg['id']='fm-limit';self.register();self.update(deadline=time.time()+999,attempts=12);self.run_one();self.assertEqual(self.calls,[])
    def test_busy_only_wait_is_short_without_spending_attempts(self):
        self.readiness.reason=lambda name:'bridge busy'
        self.run_one();value=self.value()
        self.assertEqual(value['attempts'],0)
        self.assertEqual(value['status'],'waiting_provider')
        self.assertGreater(value['due'],time.time()+140)
        self.assertLess(value['due'],time.time()+160)

    def test_provider_wait_does_not_spend_attempts_and_respects_cooldown(self):
        self.readiness.reason=lambda name:'cooldown';self.cd._load=lambda:{'chatgpt':{'until':time.time()+7200}}
        self.run_one();self.assertEqual(self.calls,[]);v=self.value();self.assertEqual(v['attempts'],0);self.assertGreater(v['due'],time.time()+7200)
    def test_model_failure_seventh_attempt_retains_original(self):
        self.register();self.update(attempts=6)
        self.m['ask']=lambda *a:(_ for _ in ()).throw(RuntimeError('provider unavailable'))
        self.run_one();v=self.value();self.assertEqual(v['attempts'],7);self.assertEqual(v['status'],'waiting_provider');self.assertEqual(v['message'],self.msg)
    def test_changed_incoming_message_cannot_replace_original(self):
        self.register();self.msg['text']='Mutated task';self.run_one();self.assertEqual(self.calls[0][1]['text'],'Original review request')
    def test_concurrent_attempt_is_not_duplicated(self):
        entered=threading.Event();release=threading.Event()
        def ask(*a):self.calls.append(a);entered.set();release.wait(3);return dict(self.result)
        self.m['ask']=ask
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            f=pool.submit(self.run_one);self.assertTrue(entered.wait(2));self.run_one();release.set();f.result(3)
        self.assertEqual(len(self.calls),1)
    def test_retry_uses_durable_source_not_recent_message_window(self):
        self.register();self.update(due=0);done=threading.Event();real=self.m['_learn_one']
        def worker(*args):
            try:real(*args)
            finally:done.set()
        self.m['_learn_one']=worker;self.m['_retry_due']();self.assertTrue(done.wait(3));self.assertEqual(len(self.calls),1)
    def test_result_ready_survives_deadline_and_reboot_without_model(self):
        self.register();self.update(result=dict(self.result),status='result_ready',deadline=0,running=time.time());self.m['_retry_boot']();self.update(due=0);self.run_one();self.assertEqual(self.calls,[]);self.assertEqual(self.value()['status'],'review_saved')
    def test_invalid_review_does_not_claim_learning_or_loop(self):
        self.result.pop('brain_review');self.run_one();self.run_one();self.assertEqual(len(self.calls),1);self.assertEqual(self.value()['status'],'invalid_review');self.assertEqual(self.saved,{})
    def test_backoff_cannot_be_bypassed_by_duplicate_delivery(self):
        self.register();self.update(due=time.time()+3600);self.run_one();self.assertEqual(self.calls,[])
    def test_pytest_live_retry_path_is_rejected_before_write(self):
        self.m['_LEARN_LIVE_RETRY']=self.m['RETRY']
        with patch.dict(os.environ,{'PYTEST_CURRENT_TEST':'isolated-test'}):
            with self.assertRaises(RuntimeError):self.register()
        self.assertFalse((self.root/'retry.json').exists())
    def test_cancel_during_model_call_retains_result_without_saving(self):
        def ask(*args):self.calls.append(args);self.status='cancelled';return dict(self.result)
        self.m['ask']=ask;self.run_one()
        self.assertEqual(self.value()['status'],'cancelled');self.assertIn('result',self.value());self.assertEqual(self.saved,{})
    def test_pause_during_model_call_then_resume_saves_without_ai(self):
        def ask(*args):self.calls.append(args);self.status='paused';return dict(self.result)
        self.m['ask']=ask;self.run_one();self.status='active';self.update(due=0);self.run_one()
        self.assertEqual(len(self.calls),1);self.assertEqual(self.value()['status'],'review_saved')
    def test_inflight_learning_preserves_request_without_marking_active(self):
        self.m['_inflight'](self.msg);self.assertEqual(self.value()['message'],self.msg);self.assertEqual(self.value()['running'],0)
    def test_active_learning_not_restarted_by_stale_recovery(self):
        self.register();self.update(running=time.time()-10000,due=0,attempts=7)
        self.m['_LEARN_ACTIVE'].add('fm-review-original|macro');self.m['_retry_due']()
        self.assertEqual(self.calls,[]);self.assertEqual(self.value()['attempts'],7)
    def test_retry_commit_syncs_file_before_replace_and_directory_after(self):
        events=[];real_sync=os.fsync;real_replace=os.replace
        def sync(fd):events.append('sync');return real_sync(fd)
        def replace(src,dst):events.append('replace');return real_replace(src,dst)
        with patch.object(os,'fsync',side_effect=sync),patch.object(os,'replace',side_effect=replace):self.register()
        self.assertEqual(events,['sync','replace','sync'])
        self.assertEqual(self.value()['message'],self.msg)
    def test_file_sync_failure_preserves_previous_queue(self):
        self.register();before=(self.root/'retry.json').read_bytes()
        with patch.object(os,'fsync',side_effect=OSError('sync failed')):
            with self.assertRaises(OSError):self.update(status='should-not-commit')
        self.assertEqual((self.root/'retry.json').read_bytes(),before)
    def test_directory_sync_failure_reports_uncertain_commit_without_losing_result(self):
        self.register();real_sync=os.fsync;calls=[]
        def sync(fd):
            calls.append(fd)
            if len(calls)==2:raise OSError('directory sync failed')
            return real_sync(fd)
        with patch.object(os,'fsync',side_effect=sync):
            with self.assertRaises(OSError):self.update(status='result_ready',result=dict(self.result))
        self.assertEqual(self.value()['result'],self.result)
        self.run_one();self.assertEqual(self.calls,[]);self.assertEqual(self.value()['status'],'review_saved')
    def test_readiness_to_call_contention_remains_retryable(self):
        for mode in ('code','deferred',None):
            with self.subTest(mode=mode):
                self.msg['id']='fm-race-'+str(mode)
                self.result['_brain']=mode
                self.run_one()
                key=self.msg['id']+'|macro';value=self.state()[key]
                self.assertEqual(value['status'],'waiting_provider');self.assertEqual(value['attempts'],1)
                self.assertNotIn('result',value);self.assertGreater(value['due'],time.time())
                self.result['_brain']='chatgpt';self.update(due=0);self.run_one()
                self.assertEqual(self.state()[key]['status'],'review_saved')
        self.assertEqual(len(self.calls),6)
    def test_ordinary_retry_budget_unchanged(self):
        msg=dict(id='plain',text='hello')
        for i in range(6):self.assertEqual(self.m['_defer'](msg,'macro','busy'),i+1)
        self.assertEqual(self.m['_defer'](msg,'macro','busy'),0);self.assertNotIn('plain|macro',self.state())

class RecordLearningTests(unittest.TestCase):
    def test_exact_ref_and_structured_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=pathlib.Path(tmp);seen=[]
            def lesson(seat,kind,text,ref,**extra):
                row=dict(kind=kind,lesson=text,ref=ref,**extra);seen.append(row)
                with (folder/'lessons.jsonl').open('a') as stream:stream.write(json.dumps(row)+'\n')
                return row
            sb=types.SimpleNamespace(_d=lambda seat:tmp,_book=lambda:{'seats':{'macro':{'closed':[{'id':'own'}]}}},lesson=lesson)
            ns=functions(BASE/'thf_chat_tasks.py',{'learning_saved','record_learning'},dict(json=json,pathlib=pathlib,SB=sb,COMP=types.SimpleNamespace(snapshot=lambda:{'trades':{}})))
            result={'brain_review':{'lesson':'L'*350,'hypothesis':'H'*500,'next_check':'C'*500,'evidence':['own','invented']}}
            one=ns['record_learning']('macro',result,'fm-1','chatgpt');two=ns['record_learning']('macro',{},'fm-1','chatgpt')
            self.assertEqual(one,two);self.assertEqual(len(seen),1);self.assertLessEqual(len(one['lesson']),600)
            seat='s'*41;ref='r'*100
            prompt='MY BRAIN [THF seat=%s; advisory; risk rules unchanged]'%seat
            prompt+='\nLESSONS: [review; UNVERIFIED HYPOTHESIS; ref=%s] '%ref
            prompt+=one['lesson']+' | Hypothesis: '+one['hypothesis']+' | Next check: '+one['next_check']+'\n'
            self.assertLessEqual(len(prompt),900)
            self.assertEqual(prompt.count('Hypothesis:'),1);self.assertEqual(prompt.count('Next check:'),1)
            self.assertEqual(one['lesson'],'L'*300);self.assertEqual(one['next_check'],'C'*150);self.assertEqual(one['hypothesis'],'H'*140);self.assertEqual(one['source_message_id'],'fm-1');self.assertEqual(one['evidence'],['own'])

if __name__=='__main__':unittest.main()
