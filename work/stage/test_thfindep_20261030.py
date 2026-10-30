import os, json, types
from veteran_desk import vd_engine as E
def test_thf_wake_not_blocked_by_pipeline_lock(tmp_path, monkeypatch):
    lock = tmp_path / ".pipeline.lock"; lock.mkdir()
    q = tmp_path / "q.jsonl"; q.write_text(json.dumps(dict(wake_id="w1", status="queued", seats=["fx"])) + "\n")
    monkeypatch.setattr(E, "PIPE_LOCK", str(lock)); monkeypatch.setattr(E, "QUEUE_P", str(q))
    monkeypatch.setattr(E, "BASE", str(tmp_path)); (tmp_path / "logs").mkdir()
    started = []
    class P:
        pid = 1
        def poll(self): return None
    monkeypatch.setattr(E.subprocess, "Popen", lambda *a, **k: started.append(k["env"]["THF_DESK"]) or P())
    monkeypatch.setattr(E, "log", lambda *a: None)
    monkeypatch.delenv("THF_DEFER_PIPE", raising=False); monkeypatch.setenv("THF_DISPATCH", "1")
    E.RUNNER.update(proc=None, wid=None, t0=None)
    E.runner_tick()
    assert started == ["1"]
    E.RUNNER.update(proc=None, wid=None, t0=None)
def test_thf_defer_opt_in(tmp_path, monkeypatch):
    lock = tmp_path / ".pipeline.lock"; lock.mkdir()
    monkeypatch.setattr(E, "PIPE_LOCK", str(lock)); monkeypatch.setenv("THF_DEFER_PIPE", "1")
    E.RUNNER.update(proc=None, wid=None, t0=None)
    called = []; monkeypatch.setattr(E.subprocess, "Popen", lambda *a, **k: called.append(1))
    E.runner_tick(); assert called == []
def test_tsx_session_dst_aware():
    import datetime as dt
    Z = dt.timezone.utc
    assert E.market_open("CA", dt.datetime(2026, 10, 30, 14, 0, tzinfo=Z))          # 10:00 Toronto EDT
    assert not E.market_open("CA", dt.datetime(2026, 11, 2, 14, 0, tzinfo=Z))       # 09:00 EST (after DST) -> closed
    assert E.market_open("CA", dt.datetime(2026, 11, 2, 15, 0, tzinfo=Z))           # 10:00 EST
    assert not E.market_open("CA", dt.datetime(2026, 10, 31, 15, 0, tzinfo=Z))      # Saturday
