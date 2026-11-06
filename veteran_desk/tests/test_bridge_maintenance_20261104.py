"""Maintenance invariants with real parser/tracker/recovery and isolated state.
No provider, service, book, or external write is used.
"""
import ast
import concurrent.futures as cf
import copy
import importlib.util
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
from unittest.mock import patch

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from veteran_desk import bridge_maintenance as B
BASE=Path(B.__file__).resolve().parents[1]
from veteran_desk import chatgpt_bridge as CG

class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'control.json'
        self.patcher=patch.object(B,'CONTROL',str(self.path));self.patcher.start();self.addCleanup(self.patcher.stop)
        self.tracker=B.Tracker(str(self.root/'status.json'))
        self.msg=dict(id='fm-original',text='Read-only question',seat='macro',command={'mode':'ask'})
        self.key='fm-original|macro';self.state={self.key:dict(tries=6,first='original-first',due=0,running=0)}
        self.rows=[];self.originals=[self.msg];self.launched=[]
        self.health=dict(state='logged_in',busy=False,inflight=0,queue=0,admitted=0,admission={'capacity':2,'protocol':'bounded_seat_admission_v1'})
        self.lock=threading.RLock()
        def rw(fn):
            with self.lock:return fn(self.state)
        self.recovery=B.Recovery(self.tracker,rw,lambda:self.originals,lambda:self.rows,
            lambda message,seat,permit:self.launched.append((message,seat,permit)),lambda:self.health)
    def control(self,phase='hold',**kw):
        value=dict(version=B.VERSION,generation='generation-1',phase=phase,created_at=time.time()-1,deadline=time.time()+599,
                   protected=[dict(key=self.key,original_sha256=B.original_hash(self.msg),replay='safe',review_id='audit-1')])
        value.update(kw);self.path.write_text(json.dumps(value));return value
    def test_absent_control_keeps_ordinary_clients(self):
        self.assertIsNone(B.read_control());B.client_guard()
    def test_invalid_and_expired_controls_block_explicitly(self):
        for value in ([],{}, {'phase':'hold'}):
            self.path.write_text(json.dumps(value));self.assertEqual(B.read_control()['phase'],'blocked')
            with self.assertRaises(B.MaintenanceHold):B.client_guard()
        self.control(created_at=time.time()-100,deadline=time.time()-1)
        self.assertEqual(B.read_control()['phase'],'blocked')
        self.recovery.tick();self.assertEqual(self.launched,[])
        self.assertIn('expired',self.state[self.key]['maintenance_error'])
    def test_window_max_is_finite(self):
        self.control(deadline=time.time()+1801)
        self.assertEqual(B.read_control()['phase'],'blocked')
    def test_hash_excludes_only_mutable_delivery_metadata(self):
        original=B.original_hash(self.msg)
        self.assertEqual(original,B.original_hash(dict(self.msg,acked=True,delivery={'macro':'busy'})))
        self.assertNotEqual(original,B.original_hash(dict(self.msg,text='different request')))
    def test_hold_deferral_retains_exact_original_budget(self):
        self.control();self.recovery.defer(self.msg,'macro',B.MaintenanceHold('bridge maintenance hold'))
        value=self.state[self.key]
        self.assertEqual(value['tries'],6);self.assertEqual(value['first'],'original-first')
        self.assertEqual(value['maintenance_original'],self.msg)
    def test_protected_capacity_at_limit_does_not_spend_or_drop(self):
        self.control('recover');self.recovery.defer(self.msg,'macro','chatgpt busy',capacity=True)
        self.assertEqual(self.state[self.key]['tries'],6)
        self.assertEqual(self.state[self.key]['maintenance_state'],'waiting_capacity')
    def test_unrelated_capacity_path_is_unchanged(self):
        self.control();before=copy.deepcopy(self.state)
        self.assertIsNone(self.recovery.defer(dict(self.msg,id='other'),'macro','busy',capacity=True))
        self.assertEqual(self.state,before)
    def test_maintenance_interrupted_unlisted_original_is_retained_blocked(self):
        self.control();other=dict(self.msg,id='fm-other')
        self.recovery.preserve(other,['macro']);self.recovery.tick()
        self.assertEqual(self.state['fm-other|macro']['tries'],0)
        status=json.loads((self.root/'status.json').read_text())
        self.assertEqual(status['keys']['fm-other|macro']['state'],'blocked')
    def test_quiescence_counts_queued_and_timed_out_provider(self):
        started=threading.Event();done=threading.Event()
        def work():started.set();done.wait(2)
        with cf.ThreadPoolExecutor(1) as pool:
            future=self.tracker.submit(pool,work);self.assertTrue(started.wait(1))
            self.control();snap=self.tracker.snapshot(B.read_control())
            self.assertFalse(snap['tracked_quiesced'])
            with self.assertRaises(cf.TimeoutError):future.result(.01)
            self.assertEqual(self.tracker.count(),1)
            done.set();future.result(1)
        self.assertEqual(self.tracker.count(),0)
    def test_owned_thread_counted_before_run_and_permit_propagated(self):
        seen=[];ctx={'generation':'g','key':'k'}
        with B.use_permit(ctx):thread=self.tracker.thread(target=lambda:seen.append(B.permit()))
        thread.start();thread.join(1)
        self.assertEqual(seen,[ctx]);self.assertEqual(self.tracker.count(),0)
    def test_all_new_entry_kinds_are_fenced(self):
        self.control();ran=[]
        for kind in ('handle','learning_review','head_trader_digest','code_hands_worker','dispatcher'):
            self.tracker.tracked(kind)(lambda:ran.append(kind))()
        self.assertEqual(ran,[])
    def test_recovery_requires_new_server_healthy_idle(self):
        self.control('recover')
        for changes in ({'busy':True},{'queue':1},{'inflight':1},{'admitted':1},{'state':'down'},{'admission':{}}):
            old=self.health;self.health=dict(old,**changes);self.recovery.tick();self.assertEqual(self.launched,[]);self.health=old
        self.recovery.tick();self.assertEqual(len(self.launched),1)
        self.assertEqual(self.state[self.key]['tries'],6)
    def test_one_protected_release_not_due_burst(self):
        control=self.control('recover')
        second=dict(self.msg,id='fm-second',seat='banks');secondkey='fm-second|banks'
        self.originals.append(second);self.state[secondkey]=dict(tries=6,due=0,running=0)
        control['protected'].append(dict(key=secondkey,original_sha256=B.original_hash(second),replay='safe',review_id='audit-2'))
        self.path.write_text(json.dumps(control))
        self.recovery.tick();self.recovery.tick()
        self.assertEqual(len(self.launched),1)
        self.assertEqual(self.launched[0][0]['id'],'fm-original')
    def test_any_tracked_old_work_blocks_recovery(self):
        token=self.tracker.begin('old handler');self.control('recover')
        self.recovery.tick();self.assertEqual(self.launched,[])
        self.tracker.end(token);self.recovery.tick();self.assertEqual(len(self.launched),1)
    def test_missing_retry_or_original_is_blocked_not_reconstructed(self):
        self.control('recover');self.state.clear();self.recovery.tick()
        self.assertEqual(self.state,{});self.assertEqual(self.launched,[])
        self.state[self.key]=dict(tries=6,due=0,running=0);self.originals=[];self.recovery.tick()
        self.assertIn('Original request missing',self.state[self.key]['maintenance_error'])
    def test_hash_mismatch_and_ambiguous_replay_block(self):
        control=self.control('recover');self.msg['text']='changed';self.recovery.tick()
        self.assertEqual(self.launched,[])
        control=self.control('recover');control['protected'][0]['replay']='blocked';self.path.write_text(json.dumps(control));self.recovery.tick()
        self.assertEqual(self.launched,[])
    def test_completed_code_reply_never_reenrolled(self):
        self.control('recover');self.state.clear()
        self.rows.append(dict(reply_to=self.msg['id'],**{'from':'macro'},mode='code',text='Final fallback'))
        self.recovery.tick();self.assertEqual(self.launched,[]);self.assertEqual(self.state,{})
        status=json.loads((self.root/'status.json').read_text())
        self.assertEqual(status['keys'][self.key]['state'],'completed')
    def test_instant_status_does_not_count_as_final_reply(self):
        self.control('recover');self.rows.append(dict(reply_to=self.msg['id'],**{'from':'macro'},mode='code',kind='instant_status'))
        self.recovery.tick();self.assertEqual(len(self.launched),1)
    def test_pre_action_done_retained_until_reply_commit(self):
        self.control('recover');self.assertTrue(self.recovery.done(self.msg['id'],'macro'))
        self.assertIn(self.key,self.state)
        self.recovery.finish(self.msg,['macro']);self.recovery.tick()
        self.assertEqual(self.launched,[])
        self.assertEqual(self.state[self.key]['maintenance_state'],'blocked')
    def test_reply_commit_then_explicit_release_cleanup(self):
        self.control('recover');self.recovery.done(self.msg['id'],'macro')
        self.rows.append(dict(reply_to=self.msg['id'],**{'from':'macro'},mode='chatgpt'))
        self.recovery.finish(self.msg,['macro'])
        self.assertEqual(self.state[self.key]['maintenance_state'],'completed')
        self.control('released');self.recovery.tick();self.assertNotIn(self.key,self.state)
    def test_interrupted_handler_requires_new_audit_receipt(self):
        control=self.control();self.state[self.key]['running']=time.time()
        self.recovery.boot(self.state);self.assertEqual(self.state[self.key]['tries'],6)
        control['phase']='recover';self.path.write_text(json.dumps(control));self.recovery.tick();self.assertEqual(self.launched,[])
        control['protected'][0]['review_id']='post-stop-audit-2';self.path.write_text(json.dumps(control));self.recovery.tick()
        self.assertEqual(len(self.launched),1)
    def test_recovery_permit_exact_generation_key_hash_and_audit(self):
        self.control('recover');self.recovery.tick();permit=self.launched[0][2]
        with B.use_permit(permit):B.client_guard()
        for field in ('generation','key','original_sha256','review_id'):
            with B.use_permit(dict(permit,**{field:'wrong'})):
                with self.assertRaises(B.MaintenanceHold):B.client_guard()
    def test_pending_commit_needs_explicit_new_nonexecution_audit(self):
        control=self.control('recover');self.recovery.done(self.msg['id'],'macro');self.recovery.finish(self.msg,['macro'])
        control['protected'][0]['review_id']='new-audit';self.path.write_text(json.dumps(control))
        self.recovery.tick();self.assertEqual(self.launched,[])
        control['protected'][0]['interrupted_outcome']='not_executed';self.path.write_text(json.dumps(control))
        self.recovery.tick();self.assertEqual(len(self.launched),1)
        self.assertNotIn('maintenance_commit_pending',self.state[self.key])
    def test_health_race_generation_change_prevents_launch(self):
        self.control('recover')
        def health():self.control('hold',generation='changed');return self.health
        self.recovery.health=health;self.recovery.tick();self.assertEqual(self.launched,[])
    def test_client_hold_before_quota_and_post(self):
        self.control();CG._T[:]=[]
        with patch.object(CG,'enabled',return_value=True),patch.object(CG,'busy',return_value=False),patch.object(CG.urllib.request,'urlopen') as opened:
            with self.assertRaises(B.MaintenanceHold):CG.ask('prompt')
            self.assertEqual(CG._T,[]);opened.assert_not_called()
    def test_client_second_guard_rolls_back_reserved_quota(self):
        CG._T[:]=[];real=B.client_guard;calls=[]
        def guard():
            calls.append(1)
            if len(calls)==2:self.control()
            return real()
        with patch.object(B,'client_guard',side_effect=guard),patch.object(CG,'enabled',return_value=True),patch.object(CG,'busy',return_value=False),patch.object(CG.urllib.request,'urlopen') as opened:
            with self.assertRaises(B.MaintenanceHold):CG.ask('prompt')
            self.assertEqual(CG._T,[]);opened.assert_not_called()
    def test_atomic_status_syncs_file_rename_directory_and_closes_fd(self):
        events=[];closed=[];real_sync=os.fsync;real_replace=os.replace;real_close=os.close
        def sync(fd):events.append('sync');return real_sync(fd)
        def replace(src,dst):events.append('replace');return real_replace(src,dst)
        def close(fd):events.append('close');closed.append(fd);return real_close(fd)
        with patch.object(os,'fsync',side_effect=sync),patch.object(os,'replace',side_effect=replace),patch.object(os,'close',side_effect=close):
            B.atomic_json(str(self.root/'durable.json'),{'ok':True})
        self.assertEqual(events,['sync','replace','sync','close'])
        with self.assertRaises(OSError):os.fstat(closed[0])
        self.assertEqual(json.loads((self.root/'durable.json').read_text()),{'ok':True})
    def test_ack_identifies_fresh_process_instance_and_generation(self):
        self.control();self.recovery.tick();status=json.loads((self.root/'status.json').read_text())
        self.assertEqual(status['version'],B.VERSION);self.assertEqual(status['pid'],os.getpid())
        self.assertEqual(status['generation'],'generation-1');self.assertEqual(status['instance'],self.tracker.instance)
        self.assertTrue(status['tracked_quiesced']);self.assertGreater(status['checked_at'],time.time()-2)

class DispatchIntegrationTests(unittest.TestCase):
    """Execute the actual dispatch/handle definitions with real maintenance objects."""
    setUp=MaintenanceTests.setUp
    control=MaintenanceTests.control
    def harness(self):
        source=(BASE/'veteran_desk/thf_msgdesk.py').read_text();tree=ast.parse(source)
        selected={'handle','_handle_normal','_command_mode','_is_push','once','_retry_done','_defer'}
        ns=dict(_BM=B,_MAINT=self.tracker,_MREC=self.recovery,cf=cf,time=time,threading=threading,re=re,json=json,os=os,
            targets=lambda m:[m['seat']],_learn_handle=lambda *args:'learning-path',
            _learn_register=lambda *args:None,_learn_wait=lambda *args:None,
            _once_normal=lambda:'ordinary-dispatch',_ch_ensure=lambda:None,_retry_rw=self.recovery.retry_rw,
            _capacity=lambda e:'busy' in str(e),_rkey=lambda m,s:m+'|'+s,
            RETRY_MAX=6,RETRY_GAP=240,RETRY_GAP_MAX=600,now=lambda:'now',log=lambda text:None)
        exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in selected],type_ignores=[]),'<real-msgdesk-dispatch>','exec'),ns)
        return ns
    def test_direct_learning_handle_uses_actual_wrapper_and_lane(self):
        ns=self.harness();message=dict(self.msg,command={'mode':'learn'})
        self.assertEqual(ns['handle'](message),'learning-path')
        self.assertEqual(self.tracker.count(),0)
    def test_direct_handle_held_before_any_actions_or_model(self):
        ns=self.harness();self.control();ns['handle'](self.msg)
        self.assertEqual(self.state[self.key]['tries'],6)
        self.assertEqual(self.state[self.key]['maintenance_state'],'held')
    def test_once_hold_returns_so_loop_can_write_heartbeat(self):
        ns=self.harness();self.control();self.assertIsNone(ns['once']())
        self.assertTrue((self.root/'status.json').exists())
        # Actual loop retains its heartbeat update after once(), no early return.
        tree=ast.parse((BASE/'veteran_desk/thf_msgdesk.py').read_text())
        loop=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='loop')
        text=ast.unparse(loop)
        self.assertLess(text.index('once()'),text.index('msgdesk_heartbeat.tmp'))
    def test_ordinary_dispatch_and_unprotected_budget_unchanged(self):
        ns=self.harness();self.assertEqual(ns['once'](),'ordinary-dispatch')
        other=dict(self.msg,id='fm-other');self.state['fm-other|macro']=dict(first='x',tries=6)
        self.assertEqual(ns['_defer'](other,'macro','busy'),0)
        self.assertNotIn('fm-other|macro',self.state)
    def test_held_boot_then_release_starts_existing_code_worker_once(self):
        ns=self.harness();starts=[];ns['_ch_ensure']=lambda:starts.append('existing queue')
        self.control();ns['once']();self.assertEqual(starts,[])
        self.control('released');ns['once']();ns['once']()
        self.assertEqual(starts,['existing queue'])
    def test_protected_retry_done_is_two_phase(self):
        ns=self.harness();self.control();ns['_retry_done'](self.msg['id'],'macro')
        self.assertTrue(self.state[self.key]['maintenance_commit_pending'])



class ReviewRegressionTests(unittest.TestCase):
    setUp=MaintenanceTests.setUp
    control=MaintenanceTests.control
    harness=DispatchIntegrationTests.harness
    def test_released_or_missing_control_cannot_replay_managed_original(self):
        ns=self.harness();calls=[];ns['_handle_normal']=lambda *a:calls.append(a)
        self.state[self.key].update(maintenance_generation='generation-1',maintenance_state='blocked',maintenance_commit_pending=True)
        self.control('released');ns['handle'](self.msg)
        self.path.unlink();ns['handle'](self.msg)
        self.assertEqual(calls,[]);self.assertTrue(self.state[self.key]['maintenance_commit_pending'])
    def test_unacked_original_cannot_bypass_managed_fence_after_release(self):
        ns=self.harness();self.control('released')
        self.state[self.key].update(maintenance_generation='generation-1',maintenance_state='blocked')
        ack=[];started=[]
        ns.update(pack_check=lambda:None,_route_adv=lambda:None,_ht_digest_bg=lambda:None,_retry_due=lambda:None,
                  tread=lambda:[],_inflight=lambda m:started.append(m),log=lambda x:None,
                  TF=types.SimpleNamespace(unacked=lambda:[self.msg],ack=lambda *a:ack.append(a),tick=lambda:None))
        tree=ast.parse((BASE/'veteran_desk/thf_msgdesk.py').read_text())
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_once_normal')
        exec(compile(ast.Module(body=[node],type_ignores=[]),'<real-pickup>','exec'),ns)
        ns['_once_normal']();self.assertEqual(ack,[]);self.assertEqual(started,[])
    def test_actual_cooldown_classifier_ignores_hold_strings(self):
        path=BASE/'veteran_desk/ai_cooldown.py'
        if not path.exists():path=BASE.parent/'test_support/ai_cooldown.py'
        spec=importlib.util.spec_from_file_location('test_actual_cooldown',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        errors=['bridge maintenance hold: hold','bridge maintenance hold: recover',
                'bridge maintenance hold: Maintenance window expired or clock predates control; explicit review/release required',
                'bridge maintenance hold: Invalid maintenance control: JSONDecodeError']
        with patch.object(module,'_save') as saved,patch.object(module,'_load',return_value={}):
            for error in errors:self.assertIsNone(module.mark_fail('chatgpt',error))
            saved.assert_not_called()
    def learning_namespace(self,ask):
        path=BASE/'veteran_desk/thf_msgdesk.py';tree=ast.parse(path.read_text())
        names={'_rkey','_retry_rw','_learn_register','_learn_update','_learn_control','_learn_wait','_learn_one'}
        ns=dict(json=json,os=os,time=time,_BM=B,threading=threading,
                RETRY=str(self.root/'learning_retry.json'),_RLOCK=threading.Lock(),_LEARN_ACTIVE=set(),
                _LEARN_ACTIVE_LOCK=threading.Lock(),_LEARN_SLOTS=threading.BoundedSemaphore(2),
                _LEARN_LIVE_RETRY='/not-live',_LEARN_TERMINAL={'review_saved','cancelled','expired','invalid_review'},
                LEARN_MAX_ATTEMPTS=12,LEARN_DEADLINE_S=999,SE='software_engineer',
                jl=lambda path,default:json.loads(Path(path).read_text()) if Path(path).exists() else default,
                TF=types.SimpleNamespace(delivery=lambda *a:None),log=lambda x:None,ask=ask,tread=lambda:[],tadd=lambda **k:None)
        exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),str(path),'exec'),ns)
        return ns
    def test_learning_hold_after_readiness_refunds_only_unsubmitted_reservation(self):
        tasks=types.SimpleNamespace(load=lambda:{'jobs':{}},learning_saved=lambda *a:None)
        import veteran_desk
        with patch.object(veteran_desk,'thf_chat_tasks',tasks,create=True):
            for submitted in (False,True):
                self.path.unlink(missing_ok=True)
                message=dict(self.msg,id='fm-learning-'+str(submitted),command={'mode':'learn'})
                def ask(*args):
                    if submitted:B.mark_submission();self.control()
                    B.client_guard()
                ns=self.learning_namespace(ask)
                ns['_learn_register'](message,'macro');ns['_learn_update'](message['id'],'macro',attempts=11)
                def ready(*a):
                    if not submitted:self.control()
                    return None,0
                ns['_learn_ready']=ready;ns['_learn_one'](message,'macro')
                value=json.loads(Path(ns['RETRY']).read_text())[message['id']+'|macro']
                self.assertEqual(value['attempts'],12 if submitted else 11)
                self.assertEqual(value['status'],'maintenance_held')
    def test_super_canonical_provider_fallback_propagates_permit_and_attempt(self):
        from veteran_desk import ai_router as router
        self.control('recover');self.recovery.tick();permit=self.launched[0][2];budget={'submitted':False};seen=[]
        md=types.SimpleNamespace(_MAINT=self.tracker,_BM=B,_CD=types.SimpleNamespace(mark_ok=lambda name:None))
        def call(name,fn,seat,prompt,images):
            B.client_guard();seen.append((name,B.permit(),B.attempt()));B.mark_submission();return 'answer'
        for fallback in (False,True):
            seen.clear();budget['submitted']=False
            with patch.object(router,'_md',return_value=md),patch.object(router,'_call',side_effect=call),patch.object(router,'providers',return_value={n:None for n in router.STRONG}),patch.object(router,'skip_reason',side_effect=lambda n:'paused' if fallback and n!='grok' else None):
                with B.use_permit(permit),B.use_attempt(budget):result=router.super_power('prompt',synth=False)
            self.assertTrue(seen);self.assertTrue(all(p==permit and a is budget for _,p,a in seen));self.assertTrue(budget['submitted'])
            self.assertEqual({n for n,_,_ in seen},{'grok'} if fallback else set(router.FAN))
            self.assertEqual(self.tracker.count(),0)
        # An unavailable Grok account cannot be retried as a fourth provider via its old seat alias.
        seen.clear();budget['submitted']=False
        self.assertNotIn('seat',router.STRONG);self.assertNotIn('seat',router.FAN)
        with patch.object(router,'_md',return_value=md),patch.object(router,'_call',side_effect=call),patch.object(router,'providers',return_value={n:None for n in router.STRONG}),patch.object(router,'skip_reason',return_value='paused'):
            with B.use_permit(permit),B.use_attempt(budget):
                with self.assertRaisesRegex(RuntimeError,'no AI answered'):router.super_power('prompt',synth=False)
        self.assertFalse(seen);self.assertFalse(budget['submitted']);self.assertEqual(self.tracker.count(),0)
    def test_router_propagates_maintenance_without_cooldown(self):
        from veteran_desk import ai_router as router
        cd=types.SimpleNamespace(mark_fail=lambda *a:self.fail('maintenance reached cooldown'))
        md=types.SimpleNamespace(_BM=B,_CD=cd,SKIP={},QUOTA=re.compile('quota'))
        with patch.object(router,'_md',return_value=md):
            with self.assertRaises(B.MaintenanceHold):router._fail('chatgpt',B.MaintenanceHold('bridge maintenance hold'))

if __name__=='__main__':unittest.main()

REVIEWED_LEARNING_TEST_REVISION='dot-learning-v5'
