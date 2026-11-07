"""Single-ownership check for the paper-policy engine. No signal is emitted."""
import hashlib
import json
from pathlib import Path

import pytest

from deploy.paper_rules_engine_activation_t2824 import attest, capture
from deploy.paper_rules_engine_activation_t2824.ownership import (
    BOOK, GATE, LIVE, OwnershipError, allow_env, classify, refuse_second_daemon, write_takeover,
)

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "deploy" / "paper_rules_engine_activation_t2824"
UNIT_CG = "/system.slice/tfo-thf-engine.service"


def _files(source=False):
    body = {
        "veteran_desk/book.py": {"sha256": BOOK, "mtime_epoch": 100},
        "veteran_desk/gate_v2.py": {"sha256": GATE, "mtime_epoch": 100},
        "veteran_desk/engine_supervisor.py": {"present": source, "sha256": "abc" if source else None},
    }
    return body


def _proc(pid, ppid, kind, cgroup, start=200, pythonpath=""):
    cmd = {
        "supervisor": ["python", "-m", "veteran_desk.engine_supervisor"],
        "vd_engine": ["python", "-m", "veteran_desk.vd_engine", "daemon"],
        "firm_risk": ["python", "-m", "veteran_desk.exec_speed", "risk-serve"],
    }[kind]
    return {
        "pid": pid, "ppid": ppid, "alive": True, "cmdline": cmd, "cgroup": cgroup,
        "cwd": LIVE, "exe": "/opt/tfo/venv/bin/python", "start_epoch": start,
        "environ_allowlisted": {"PYTHONPATH": pythonpath, "PYTHONDONTWRITEBYTECODE": "1"},
    }


def _watched(extra):
    present = {row["pid"] for row in extra}
    rows = list(extra)
    for pid in (372060, 366917, 833092, 1940860, 1941386, 337359):
        if pid not in present:
            rows.append({"pid": pid, "alive": False})
    return rows


def _capture(processes, source=False, killmode="control-group"):
    return {
        "processes": processes,
        "service": {"MainPID": 1940860, "ControlGroup": UNIT_CG, "KillMode": killmode, "ActiveState": "active"},
        "files": _files(source),
        "tick": {"timer_active": True, "service_active": False},
    }


def test_reported_split_does_not_treat_the_official_pid_as_loaded_code():
    body = classify(_capture(_watched([
        _proc(1940860, 1, "supervisor", UNIT_CG),
        _proc(833092, 1, "supervisor", "0::/user.slice/user-1000.slice/session-56389.scope"),
        _proc(1941386, 833092, "vd_engine", "0::/user.slice/user-1000.slice/session-56389.scope"),
        _proc(337359, 1, "firm_risk", "/system.slice/tfo-thf-firm-risk.service"),
    ])))
    assert body["verdict"] == "SPLIT_OWNERSHIP"
    assert body["status"] == "blocked"
    assert body["retire_command"] is None
    assert body["official_supervisor_pids"] == [1940860]
    assert body["leftover_supervisor_pids"] == [833092]
    assert body["vd_engine_pids"] == [1941386]
    assert body["loaded_modules_proven"] is False
    assert body["daemon_evidence"][0]["official_cgroup_load"] is False
    assert body["daemon_evidence"][0]["evidence_level"] == "path_attested"
    assert body["daemon_evidence"][0]["daemon_in_official_cgroup"] is False
    assert body["pid_372060_alive"] is False
    assert "kill" in body["forbidden"]
    assert body["capture_gap"]


def test_main_pid_in_the_user_session_is_not_the_official_supervisor():
    body = classify(_capture(_watched([
        _proc(1940860, 1, "supervisor", "0::/user.slice/session-56389.scope"),
        _proc(1941386, 1940860, "vd_engine", UNIT_CG),
        _proc(337359, 1, "firm_risk", "/system.slice/other.service"),
    ]), source=True))
    assert 1940860 not in body["official_supervisor_pids"]
    assert body["verdict"] == "SPLIT_OWNERSHIP"
    assert body["daemon_evidence"][0]["evidence_level"] == "path_attested"
    assert body["loaded_modules_proven"] is False


def test_start_after_mtime_is_not_enough_when_pythonpath_leaves_the_live_tree():
    daemon = _proc(1941386, 1940860, "vd_engine", UNIT_CG, pythonpath="/opt/tfo/dash/base")
    body = classify(_capture(_watched([
        _proc(1940860, 1, "supervisor", UNIT_CG),
        daemon,
        _proc(337359, 1, "firm_risk", "/system.slice/other.service"),
    ]), source=True))
    assert body["daemon_evidence"][0]["evidence_level"] == "start_after_mtime"
    assert body["status"] == "blocked"
    assert body["verdict"] == "SINGLE_OWNER"


def test_one_official_child_is_observed_and_still_has_no_retire_command():
    body = classify(_capture(_watched([
        _proc(1940860, 1, "supervisor", UNIT_CG),
        _proc(1941386, 1940860, "vd_engine", UNIT_CG),
        _proc(337359, 1, "firm_risk", "/system.slice/other.service"),
    ]), source=True))
    assert body["verdict"] == "SINGLE_OWNER"
    assert body["status"] == "observed_single_owner"
    assert body["retire_command"] is None
    assert body["firm_risk_untouched"] is True
    assert body["loaded_modules_proven"] is False
    assert body["capture_gap"] == []


def test_two_daemons_and_a_missing_daemon_stay_blocked():
    two = classify(_capture(_watched([
        _proc(1940860, 1, "supervisor", UNIT_CG),
        _proc(1941386, 1940860, "vd_engine", UNIT_CG),
        _proc(1942000, 833092, "vd_engine", "0::/user.slice/session-56389.scope"),
        _proc(833092, 1, "supervisor", "0::/user.slice/session-56389.scope"),
        _proc(337359, 1, "firm_risk", "/system.slice/other.service"),
    ]), source=True))
    assert two["verdict"] == "DUPLICATE_DAEMON"
    assert refuse_second_daemon(len(two["vd_engine_pids"])) is True
    none = classify(_capture(_watched([
        _proc(1940860, 1, "supervisor", UNIT_CG),
        _proc(337359, 1, "firm_risk", "/system.slice/other.service"),
    ]), source=True))
    assert none["verdict"] == "MISSING_DAEMON"
    assert refuse_second_daemon(0) is False
    with pytest.raises(OwnershipError, match="REFUSED_DAEMON_COUNT"):
        refuse_second_daemon(True)


def test_persist_writes_takeover_only_under_the_handover_directory(tmp_path):
    cap = _capture(_watched([
        _proc(1940860, 1, "supervisor", UNIT_CG),
        _proc(833092, 1, "supervisor", "0::/user.slice/session-56389.scope"),
        _proc(1941386, 833092, "vd_engine", "0::/user.slice/session-56389.scope"),
        _proc(337359, 1, "firm_risk", "/system.slice/other.service"),
    ]))
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(cap))
    assert attest.main(["--capture", str(path)]) == 0
    with pytest.raises(OwnershipError, match="REFUSED_ROOT"):
        attest.main(["--capture", str(path), "--persist", "--root", LIVE])
    wrote, body = write_takeover(tmp_path, cap)
    assert wrote.name == "TAKEOVER"
    assert "build_handover" in str(wrote)
    assert json.loads(wrote.read_text())["verdict"] == body["verdict"]
    with pytest.raises(OwnershipError, match="REFUSED_TAKEOVER_PATH"):
        write_takeover(tmp_path / "ledger", cap)


def test_capture_keeps_allowlisted_env_and_marks_absent_pids(tmp_path):
    raw = b"PYTHONPATH=\0SECRET_TOKEN=nope\0THF_DESK=1\0PASSWORD=nope\0"
    assert allow_env(raw) == {"PYTHONPATH": "", "THF_DESK": "1"}
    proc = tmp_path / "proc"
    for pid, cmd in ((1941386, "python\0-m\0veteran_desk.vd_engine\0daemon\0"), (833092, "python\0-m\0veteran_desk.engine_supervisor\0")):
        base = proc / str(pid)
        base.mkdir(parents=True)
        (base / "cmdline").write_bytes(cmd.encode())
        (base / "status").write_text("Name:\tpython\nPPid:\t1\n")
        (base / "cgroup").write_text("0::/user.slice/session-56389.scope\n")
        (base / "environ").write_bytes(b"PYTHONPATH=\0AWS_SECRET_ACCESS_KEY=hidden\0")
        (base / "cwd").symlink_to(tmp_path)
        (base / "exe").symlink_to(tmp_path)
    out = tmp_path / "CAPTURE.json"
    service = tmp_path / "service.json"
    files = tmp_path / "files.json"
    tick = tmp_path / "tick.json"
    service.write_text(json.dumps({"MainPID": 1940860, "ControlGroup": UNIT_CG, "KillMode": "control-group"}))
    files.write_text("{}")
    tick.write_text(json.dumps({"timer_active": True}))
    assert capture.main(["--proc", str(proc), "--service-json", str(service), "--files-json", str(files), "--tick-json", str(tick), "--out", str(out)]) == 0
    saved = json.loads(out.read_text())
    by_pid = {row["pid"]: row for row in saved["processes"]}
    assert by_pid[372060]["alive"] is False
    assert by_pid[1941386]["environ_allowlisted"] == {"PYTHONPATH": ""}
    assert "AWS_SECRET_ACCESS_KEY" not in json.dumps(saved)
    with pytest.raises(OwnershipError, match="REFUSED_HOST"):
        capture.main(["--out", str(tmp_path / "nope.json")])


def test_plan_has_no_retire_command():
    plan = json.loads((PKG / "PLAN.json").read_text())
    assert plan["retire_command"] is None
    assert plan["authorization_verbatim"] == "Finish it up"
    for name in ("attest.py", "capture.py", "handover.py", "ownership.py"):
        text = (PKG / name).read_text()
        assert "os.kill" not in text
        assert "subprocess" not in text


def test_captured_supervisor_will_not_spawn_while_any_daemon_exists(tmp_path):
    import importlib.util
    from veteran_desk import healing_guard
    path = PKG / "captured" / "engine_supervisor.py"
    spec = importlib.util.spec_from_file_location("captured_engine_supervisor", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "0d0b7f640a7c9644a56f0bbe8c91b640976c1db7ecf968c328112e542de96e0e"

    class Child:
        def __init__(self):
            self.pid = 1941386
            self.signals = []
            self.dead = False
        def poll(self):
            return 0 if self.dead else None
        def terminate(self):
            self.signals.append("TERM")
            self.dead = True
        def wait(self, timeout=None):
            return 0
        def kill(self):
            self.signals.append("KILL")

    class Guard:
        def __init__(self):
            self.calls = []
        def observe(self, *args, **kwargs):
            self.calls.append("observe")
            return {"accepted": False, "action": "observing"}
        def guarded_attempt(self, *args, **kwargs):
            self.calls.append("spawn")
            raise AssertionError("spawned")

    guard = Guard()
    supervisor = module.Supervisor(base=tmp_path, guard=guard, scanner=lambda base, now: {
        "lease_valid": True, "engine_kill": False, "engine_child_alive": True, "engine_pids": [1941386],
        "busy": False, "maintenance": False,
    }, starter=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("starter")))
    status = supervisor.step()
    assert status["action"] == "observing"
    assert guard.calls == ["observe"]
    child = Child()
    supervisor.child = child
    assert supervisor.stop_owned() is True
    assert child.signals == ["TERM"]
    blocked = {"lease_valid": True, "engine_kill": False, "engine_child_alive": True, "busy": False, "maintenance": False, "active_state": "active"}
    assert "existing-child" in healing_guard._eligibility("tfo-thf-engine.service", "spawn", "engine_supervisor", blocked)
    blocked["engine_child_alive"] = False
    blocked["busy"] = True
    assert "idle" in healing_guard._eligibility("tfo-thf-engine.service", "spawn", "engine_supervisor", blocked)


def test_census_handover_is_one_term_and_not_module_proof():
    from deploy.paper_rules_engine_activation_t2824.handover import ARCHIVE_SHA, build_handover, sha256, CAPTURED
    snapshot = json.loads((PKG / "captured" / "PROCESS_SNAPSHOT.json").read_text())
    body = build_handover(snapshot, sha256(CAPTURED))
    recorded = json.loads((PKG / "HANDOVER.json").read_text())
    assert body == recorded
    assert body["archive_sha256"] == ARCHIVE_SHA
    assert body["fully_clean"] is False
    assert body["loaded_modules_proven"] is False
    assert body["handover"]["command"] == "kill -s TERM 833092"
    assert 1941386 in body["handover"]["does_not_signal"]
    assert 1940860 in body["handover"]["does_not_signal"]
    assert "systemctl restart tfo-thf-engine.service" in body["handover"]["does_not_run"]
    assert "833089" in " ".join(body["handover"]["preconditions"])
    facts = body["lazy_imports"]["path_facts_for_1941386"]
    assert facts["exe"] == "/usr/bin/python3.12"
    assert facts["cwd"] == "/opt/tfo/live/thf"
    assert facts["PYTHONPATH"] == "unset"
