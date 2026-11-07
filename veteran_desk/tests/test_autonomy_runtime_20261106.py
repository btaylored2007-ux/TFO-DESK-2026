"""Autonomy observer integration tests: no providers, orders or service mutations."""
import datetime as dt
import fcntl
import importlib.util
import json
import pathlib

import pytest

BASE = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("autonomy_runtime_candidate", BASE / "veteran_desk/autonomy_runtime.py")
R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)
NOW = dt.datetime(2026, 11, 6, 4, 0, tzinfo=dt.timezone.utc)

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))

def evidence():
    return {"schema_version":1,"generated_utc":NOW.isoformat(),"status":"ready",
            "totals":{"completed_trades":4,"wins":2,"losses":2},"desks":{"veteran":{},"thf":{}}}

@pytest.fixture
def env(tmp_path):
    (tmp_path/"SGP_PRIMARY").touch()
    save(tmp_path/"state/vd_engine/hb/engine.json",{"utc":NOW.isoformat(),"pid":123,"fails":0})
    save(tmp_path/"state/thf/chat_retry.json",{})
    save(tmp_path/"state/thf/autonomy/learning_status.json",evidence())
    p=tmp_path/"logs/seat_brain.jsonl";p.parent.mkdir()
    p.write_text("\n".join(json.dumps({"utc":NOW.isoformat(),"ai":ai,"ok":True}) for ai in ("claude","chatgpt","deepseek_api"))+"\n")
    return tmp_path

def healthy():
    return {"status":"healthy","pending_units":[],"open_circuits":[],"attempts30m":0}

def test_status_reports_real_learning_and_distinct_observed_providers(env):
    report=R.build_status(env,recovery=healthy(),now=NOW)
    assert report["status"]=="green"
    assert report["metrics"]==dict(completed_trades=4,winning_trades=2,losing_trades=2,known_books=2,provider_count=3,parked_requests=0,open_circuits=0)
    assert "costs" in report["summary"]["learning"]
    assert report["paper_only"] is True

def test_absent_learning_is_unknown_not_zero(env):
    (env/"state/thf/autonomy/learning_status.json").unlink()
    report=R.build_status(env,recovery=healthy(),now=NOW)
    assert report["status"]!="green" and report["metrics"]["completed_trades"] is None

@pytest.mark.parametrize("data",[{"completed_trades":1,"wins":2,"losses":0},
                                {"completed_trades":True,"wins":0,"losses":0}])
def test_invalid_counts_cannot_become_apparent_success(env,data):
    item=evidence();item["totals"]=data
    report=R.build_status(env,learning=item,recovery=healthy(),now=NOW)
    assert report["status"]!="green" and report["metrics"]["completed_trades"] is None

def test_stale_and_future_evidence_are_not_live_readiness(env):
    for delta in (-181,61):
        item=evidence();item["generated_utc"]=(NOW+dt.timedelta(seconds=delta)).isoformat()
        report=R.build_status(env,learning=item,recovery=healthy(),now=NOW)
        assert report["status"]!="green" and "current" in report["summary"]["learning"]

def test_context_blocked_requests_remain_visible(env):
    save(env/"state/thf/chat_retry.json",{"a":{"status":"context_blocked"},"b":{"status":"pending"}})
    report=R.build_status(env,recovery=healthy(),now=NOW)
    assert report["metrics"]["parked_requests"]==1
    assert report["details"]["waiting_requests"]==1
    assert "held for context review" in report["summary"]["teamwork"]

def test_repair_request_is_not_reported_as_recovery(env):
    data=healthy();data["pending_units"]=["tfo-news.service"]
    report=R.build_status(env,recovery=data,now=NOW)
    assert "being verified" in report["summary"]["recovery"]
    data.update(status="blocked",open_circuits=["tfo-news.service"])
    assert R.build_status(env,recovery=data,now=NOW)["status"]=="red"

def test_owner_halts_are_preserved_and_not_cleared(env):
    stop=env/"state/thf/ENGINE_KILL";stop.touch()
    halt=env/"AUTONOMY_HALT";halt.touch()
    report=R.build_status(env,recovery=healthy(),now=NOW)
    assert "paused" in report["summary"]["trading"] and stop.exists() and halt.exists()
    assert report["details"]["new_strategy_probes_armed"] is False

def test_corrupt_or_missing_heartbeat_does_not_look_healthy(env):
    (env/"state/vd_engine/hb/engine.json").write_text("{bad")
    assert R.build_status(env,recovery=healthy(),now=NOW)["status"]=="red"

def test_build_progress_is_not_failed_and_creates_no_restart(env):
    save(env/"state/vd_engine/hb/engine.json",{"utc":NOW.isoformat(),"fails":0,"phase":"build_store"})
    report=R.build_status(env,recovery=healthy(),now=NOW)
    assert "updating" in report["summary"]["trading"] and report["status"]=="green"

def test_malformed_provider_rows_do_not_drop_later_good_results(env):
    p=env/"logs/seat_brain.jsonl"
    p.write_text('{bad\n'+json.dumps({"utc":NOW.isoformat(),"ai":{},"ok":True})+"\n"+json.dumps({"utc":NOW.isoformat(),"ai":"chatgpt_web","ok":True})+"\n")
    report=R.build_status(env,recovery=healthy(),now=NOW)
    assert report["metrics"]["provider_count"]==1
    assert "chatgpt" in report["details"]["providers"]

def test_periodic_index_is_invoked_without_changing_any_book(env):
    book=env/"reports/veteran_desk_book.json";save(book,{"sentinel":"do not change"})
    before=book.read_bytes();calls=[]
    def syncer(**kwargs):
        calls.append(kwargs)
        assert kwargs["root"]==env and kwargs["output_dir"]==env/"state/thf/autonomy"
        return evidence()
    report=R.run_once(env,now=NOW,syncer=syncer,recovery=healthy())
    assert len(calls)==1 and book.read_bytes()==before
    assert json.loads((env/"state/thf/autonomy/status.json").read_text())==report

def test_failed_index_retains_previous_counts_and_reports_problem(env):
    def fail(**kwargs):raise RuntimeError("synthetic store failure")
    report=R.run_once(env,now=NOW,syncer=fail,recovery=healthy())
    assert report["metrics"]["completed_trades"]==4 and report["status"]=="red"
    assert report["details"]["learning_age_s"]==0

def test_runtime_flock_prevents_duplicate_indexing(env):
    output=env/"state/thf/autonomy";called=[]
    with (output/".runtime.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        report=R.run_once(env,now=NOW,syncer=lambda **kw:called.append(1),recovery=healthy())
    assert report=={"status":"busy"} and called==[]

def test_standby_does_not_run_learning_jobs(env):
    (env/"SGP_PRIMARY").unlink();calls=[]
    report=R.run_once(env,now=NOW,syncer=lambda **kw:calls.append(1),recovery=healthy())
    assert calls==[] and "Standby" in report["summary"]["trading"]

def test_atomic_failure_keeps_last_published_status(env,monkeypatch):
    path=env/"state/thf/autonomy/status.json";path.write_text('{"old":true}')
    def fail(*args):raise OSError("synthetic rename failure")
    monkeypatch.setattr(R.os,"replace",fail)
    with pytest.raises(OSError):R._atomic(path,{"new":True})
    assert json.loads(path.read_text())=={"old":True}
    assert not list(path.parent.glob("status.json.*.tmp"))

def test_ordinary_and_learning_retries_are_counted_without_inventing_terminal_work(env):
    rows={"ordinary":{"running":False,"due":12,"tries":1},
          "provider":{"status":"waiting_provider"},"ready":{"status":"result_ready"},
          "saved":{"status":"review_saved"},"cancelled":{"status":"cancelled"},
          "paused":{"status":"paused"}}
    save(env/"state/thf/chat_retry.json",rows)
    report=R.build_status(env,recovery=healthy(),now=NOW)
    assert report["details"]["waiting_requests"]==3
    assert report["metrics"]["parked_requests"]==1

def test_unknown_retry_records_are_visible_instead_of_disappearing(env):
    save(env/"state/thf/chat_retry.json",{"new":{"status":"unrecognised"},"bad":"corrupt"})
    report=R.build_status(env,recovery=healthy(),now=NOW)
    assert report["metrics"]["parked_requests"]==2 and report["status"]!="green"

def test_unknown_learning_schema_never_becomes_valid_profit_evidence(env):
    item=evidence();item["schema_version"]=999
    report=R.build_status(env,learning=item,recovery=healthy(),now=NOW)
    assert report["metrics"]["completed_trades"] is None and report["status"]=="red"

def test_red_observer_exit_exposes_failure_to_existing_health_owners(env,monkeypatch,capsys):
    monkeypatch.setattr(R,"run_once",lambda *args,**kwargs:{"status":"red"})
    import sys
    monkeypatch.setattr(sys,"argv",["autonomy_runtime","once","--root",str(env)])
    assert R.main()==1

def test_pending_and_global_recovery_circuits_never_look_verified(env):
    for recovery in ({"status":"attention","pending_units":["x"],"open_circuits":[]},
                     {"status":"attention","global_circuit_open":True,"open_circuits":[]}):
        report=R.build_status(env,recovery=recovery,now=NOW)
        assert report["status"]=="amber"
    assert R.build_status(env,recovery={"status":"unknown"},now=NOW)["status"]=="red"

def test_maintenance_retained_work_is_held_not_waiting(env):
    save(env/"state/thf/chat_retry.json",{
        "held":{"running":0,"due":1,"tries":6,"maintenance_generation":"old","maintenance_state":"blocked"},
        "unknown_hold":{"due":1,"maintenance_generation":"old"},
        "running":{"running":1,"due":1,"maintenance_generation":"reviewed","maintenance_state":"running"},
        "complete":{"maintenance_generation":"old","maintenance_state":"completed"}})
    report=R.build_status(env,recovery=healthy(),now=NOW)
    assert report["metrics"]["parked_requests"]==2
    assert report["details"]["waiting_requests"]==1
    assert "2 held" in report["summary"]["teamwork"]

@pytest.mark.parametrize("stop_path",["ENGINE_KILL","state/thf/ENGINE_KILL","state/vd_engine/ENGINE_KILL"])
def test_each_registered_engine_stop_is_visible(env,stop_path):
    path=env/stop_path;path.parent.mkdir(parents=True,exist_ok=True);path.touch()
    report=R.build_status(env,recovery=healthy(),now=NOW)
    assert report["details"]["engine_stop"] is True
    assert "paused" in report["summary"]["trading"]
    assert report["status"]!="green"

def test_recovery_readiness_uses_its_current_snapshot_clock_not_learning_start(env,monkeypatch):
    import sys, types, veteran_desk
    calls=[]
    fake=types.ModuleType("veteran_desk.healing_guard")
    def status(*,state_path):
        calls.append(state_path)
        return healthy()
    fake.status=status
    monkeypatch.setitem(sys.modules,"veteran_desk.healing_guard",fake)
    monkeypatch.setattr(veteran_desk,"healing_guard",fake,raising=False)
    before={str(p.relative_to(env)):p.read_bytes() for p in env.rglob("*") if p.is_file()}
    report=R.build_status(env,now=NOW)
    assert calls==[env/"state/thf/recovery_guard.json"]
    assert report["details"]["recovery"]["status"]=="healthy"
    assert report["status"]=="green"
    assert before=={str(p.relative_to(env)):p.read_bytes() for p in env.rglob("*") if p.is_file()}
