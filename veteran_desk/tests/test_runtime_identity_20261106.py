"""Service identity, adapter errors, and the two-job coverage cap."""
import hashlib
import os
import stat
import subprocess
import sys
from pathlib import Path

from veteran_desk import deepseek_bridge as DS
from veteran_desk import thf_grok
from veteran_desk import thf_shadow_sampler as SAMPLER

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "deploy/runtime_20261106/install.py"
MIGRATE = ROOT / "deploy/runtime_20261106/migrate_status_owner.py"
BASE = "08838f5"
BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"


def test_grok_cli_follows_home(monkeypatch):
    monkeypatch.setenv("HOME", "/home/tfo")
    assert thf_grok.cli_path() == "/home/tfo/.local/bin/cursor-agent"
    monkeypatch.setenv("HOME", "/root")
    assert thf_grok.cli_path() == "/root/.local/bin/cursor-agent"
    assert "expanduser" in open(thf_grok.__file__, encoding="utf-8").read()


def test_unit_runs_as_tfo_with_home():
    text = (ROOT / "deploy/incremental_20261106/tfo-thf-shadow.service").read_text()
    assert "User=tfo" in text and "Group=tfo" in text
    assert "Environment=HOME=/home/tfo" in text
    assert "TimeoutStartSec=900" in text
    assert "UMask=0077" in text
    assert "\nUser=\n" not in text


def test_root_sampler_does_not_write_status(tmp_path, monkeypatch):
    monkeypatch.setattr(SAMPLER, "refuses_root", lambda: True)
    status = SAMPLER.once(str(tmp_path))
    assert status["availability"] == "refused_root" and status["called"] is False
    assert not (tmp_path / "state/thf/shadow_sampler_status.json").exists()
    assert status["seven_day"] == "not_started"


def test_transport_valueerror_is_not_quota(tmp_path, monkeypatch):
    monkeypatch.setattr(DS, "STATE", tmp_path)
    DS._atomic(tmp_path / "config.json", dict(enabled=True))
    DS.record_result(True, 1, http_status=200, probe=True)
    DS.record_result(False, 2, "transport ValueError", None)
    assert DS.status()["until"] == 0
    assert "transport ValueError" in DS.reason(check_key=False)
    assert "cooling" not in DS.reason(check_key=False)
    assert SAMPLER.classify_error(DS.reason(check_key=False)) == "error"
    assert SAMPLER.classify_error("DeepSeek cooling: transport ValueError") == "error"
    assert SAMPLER.classify_error("DeepSeek cooling: rate limit") == "quota"
    assert SAMPLER.classify_error("insufficient balance") == "quota"
    DS.record_result(False, 3, "private key permissions", None)
    assert SAMPLER.classify_error(DS.reason(check_key=False)) == "unavailable"


def test_unreadable_status_is_not_called_unverified(tmp_path, monkeypatch):
    monkeypatch.setattr(DS, "STATE", tmp_path)
    DS._atomic(tmp_path / "config.json", dict(enabled=True))
    path = tmp_path / "status.json"
    path.write_text('{"last_success_at": 1}')
    os.chmod(path, 0)
    try:
        assert "unreadable" in DS.reason(check_key=False)
        assert "not verified" not in DS.reason(check_key=False)
    finally:
        os.chmod(path, 0o600)


def test_chatgpt_budget_covers_extra_high_and_skips_a_dead_bridge(monkeypatch):
    assert SAMPLER.chatgpt_budget(dict(provider_timeout_s=90)) == 280
    assert SAMPLER.chatgpt_budget(dict(provider_timeout_s=5)) == 5
    assert SAMPLER.chatgpt_budget(dict(provider_timeout_s=90, chatgpt_timeout_s=1000)) == 300
    from veteran_desk import chatgpt_bridge as bridge
    posts = []
    monkeypatch.setattr(bridge, "health", lambda: {})
    monkeypatch.setattr(bridge, "ask", lambda *args, **kwargs: posts.append(kwargs))
    try:
        SAMPLER.call_chatgpt("same", "banks", 280)
    except RuntimeError as error:
        assert "unreachable" in str(error)
    assert posts == []
    monkeypatch.setattr(bridge, "health", lambda: dict(state="restarting"))
    try:
        SAMPLER.call_chatgpt("same", "banks", 280)
    except RuntimeError as error:
        assert "restarting" in str(error)
    assert posts == []
    monkeypatch.setattr(bridge, "health", lambda: dict(state="logged_in", busy=True, inflight=1))
    try:
        SAMPLER.call_chatgpt("same", "banks", 280)
    except RuntimeError as error:
        assert "busy" in str(error)
    assert posts == []


def test_two_jobs_leave_sixteen_pending_and_do_not_start_seven_day(tmp_path, monkeypatch):
    calls = []

    def make(name):
        def _call(prompt, seat, timeout):
            calls.append((name, timeout))
            if name == "grok":
                raise RuntimeError("Existing Cursor CLI unavailable")
            return "answer", name
        return _call

    for name in SAMPLER.CALLS:
        monkeypatch.setitem(SAMPLER.CALLS, name, make(name))
    path = tmp_path / "state/thf"
    path.mkdir(parents=True)
    (path / "shadow_sampler.json").write_text(
        '{"enabled":true,"paper_only":true,"provider_timeout_s":90,"max_jobs_per_run":2}')
    status = SAMPLER.once(str(tmp_path), now="2026-11-06T14:45:46+00:00")
    assert status["seats_expected"] == 18
    assert status["jobs"] == 2
    assert status["pending_jobs"] == 16
    assert status["chatgpt_timeout_s"] == 280
    assert status["window"]["started"] is False
    assert status["seven_day"] == "not_started"
    assert status["recommendation"]["global_winner"] is None
    assert {timeout for name, timeout in calls if name == "chatgpt"} == {280}
    assert {timeout for name, timeout in calls if name == "claude"} == {90}
    first = SAMPLER.shadow_request_id("chatgpt", "banks", "same")
    assert first == SAMPLER.shadow_request_id("chatgpt", "banks", "same")
    assert len(first) <= 128 and ":" not in first


def _show(rel):
    return subprocess.check_output(["git", "show", "%s:%s" % (BASE, rel)], cwd=ROOT)


def test_runtime_installer_replaces_recorded_bases_and_rolls_back(tmp_path):
    book = tmp_path / "veteran_desk/book.py"
    book.parent.mkdir(parents=True)
    book.write_bytes((ROOT / "veteran_desk/book.py").read_bytes())
    for rel in (
        "veteran_desk/thf_shadow_sampler.py",
        "veteran_desk/thf_grok.py",
        "veteran_desk/deepseek_bridge.py",
        "deploy/incremental_20261106/tfo-thf-shadow.service",
    ):
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_show(rel))
    before = {rel: hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest() for rel in (
        "veteran_desk/thf_shadow_sampler.py",)}
    result = subprocess.run([sys.executable, str(INSTALL), "--root", str(tmp_path), "--apply"],
                            cwd=ROOT, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout
    assert "ALREADY_PROTECTED veteran_desk/book.py" in result.stdout
    assert hashlib.sha256(book.read_bytes()).hexdigest() == BOOK
    assert hashlib.sha256((tmp_path / "veteran_desk/thf_grok.py").read_bytes()).hexdigest() == hashlib.sha256((ROOT / "veteran_desk/thf_grok.py").read_bytes()).hexdigest()
    assert hashlib.sha256((tmp_path / "veteran_desk/thf_shadow_sampler.py").read_bytes()).hexdigest() != before["veteran_desk/thf_shadow_sampler.py"]
    back = subprocess.run([sys.executable, str(INSTALL), "--root", str(tmp_path), "--rollback"],
                          cwd=ROOT, text=True, capture_output=True, check=False)
    assert back.returncode == 0, back.stdout
    assert hashlib.sha256((tmp_path / "veteran_desk/thf_shadow_sampler.py").read_bytes()).hexdigest() == before["veteran_desk/thf_shadow_sampler.py"]
    assert hashlib.sha256(book.read_bytes()).hexdigest() == BOOK


def test_migration_chowns_only_root_owned_allowlist(tmp_path):
    folder = tmp_path / "state/thf/deepseek_api"
    folder.mkdir(parents=True)
    status = folder / "status.json"
    status.write_text("{}")
    os.chmod(status, 0o600)
    other = tmp_path / "veteran_desk"
    other.mkdir()
    (other / "book.py").write_text("leave")
    sys.path.insert(0, str(MIGRATE.parent))
    import migrate_status_owner as MIG
    planned = {row["path"]: row["action"] for row in MIG.plan(str(tmp_path))}
    assert planned["state/thf/deepseek_api/status.json"] == "skip_not_root"
    assert "veteran_desk/book.py" not in planned
    assert MIG.decide(0, False) == "chown"
    assert MIG.decide(1000, False) == "skip_not_root"
    assert MIG.decide(0, True) == "skip_symlink"
    mode = stat.S_IMODE(status.stat().st_mode)
    result = subprocess.run([sys.executable, str(MIGRATE), "--root", str(tmp_path), "--apply"],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout
    assert stat.S_IMODE(status.stat().st_mode) == mode
    assert "leave" == (other / "book.py").read_text()
