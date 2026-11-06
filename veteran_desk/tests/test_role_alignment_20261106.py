"""The 18 paper roles stay distinct. Staff without a persona file are not dropped or renamed."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from veteran_desk import lesson_trace as LT
from veteran_desk import seat_registry as SR
from veteran_desk import thf_funder as F
from veteran_desk import thf_shadow_sampler as SAMPLER

EIGHTEEN = {
    "banks", "energy", "essentials_utilities", "healthcare", "news_filings", "price_action",
    "tech_ai_infra", "trend", "macro", "fx_commodities", "crypto",
    "head_of_desk", "head_trader", "cfo", "software_engineer", "fixer",
    "blind_challenger", "devils_advocate",
}


def test_named_staff_join_the_eighteen_and_risk_gate_stays_out():
    roles = {seat for seat, _label in SR.chat_seats()}
    assert roles == set(LT.SEATS) == EIGHTEEN
    assert "risk_gate" not in roles
    assert SR.resolve("root") is None
    assert SR.resolve("simon") == "software_engineer"
    assert SR.resolve("../../banks") is None
    assert "software_engineer.json" not in __import__("os").listdir(SR.SEATS_DIR)
    assert "cfo.json" not in __import__("os").listdir(SR.SEATS_DIR)


def test_unaddressed_posts_do_not_fan_out_to_staff_or_fixer():
    assert F.targets(dict(kind="all")) == [
        "banks", "blind_challenger", "crypto", "devils_advocate", "energy", "essentials_utilities",
        "fx_commodities", "head_of_desk", "head_trader", "healthcare", "macro", "news_filings",
        "price_action", "tech_ai_infra", "trend"]
    assert F.targets(dict(kind="seat", seat="software_engineer")) == ["software_engineer"]
    assert F.targets(dict(kind="seat", seat="cfo")) == ["cfo"]


def _ready(tmp_path, monkeypatch, config):
    from veteran_desk import bridge_maintenance as B
    from veteran_desk import deepseek_bridge as D
    monkeypatch.setattr(D, "STATE", tmp_path)
    monkeypatch.setattr(D, "KEY", tmp_path / "key")
    (tmp_path / "key").write_text("synthetic-test-key")
    (tmp_path / "key").chmod(0o600)
    D._atomic(tmp_path / "config.json", config)
    D.record_result(True, 1, http_status=200, probe=True)
    monkeypatch.setattr(B, "client_guard", lambda: None)
    monkeypatch.setattr(B, "mark_submission", lambda: None)
    monkeypatch.setattr(D, "brain_readback", lambda prompt: [])
    return D


def test_legacy_deepseek_accepts_only_its_two_roles(tmp_path, monkeypatch):
    D = _ready(tmp_path, monkeypatch, dict(enabled=True))
    monkeypatch.setattr(D, "_run", lambda payload, budget: {"ok": True, "text": "ok", "model": D.MODEL})
    assert D.ask("check", seat="software_engineer", workload="chat") == "ok"
    for seat in ("banks", "cfo", "head_trader", "root", "../software_engineer"):
        try:
            D.ask("check", seat=seat, workload="chat")
        except PermissionError:
            continue
        raise AssertionError(seat)


def test_configured_eighteen_reach_deepseek_as_themselves(tmp_path, monkeypatch):
    from veteran_desk import deepseek_bridge as D
    from veteran_desk import fixer_work as FW
    D = _ready(tmp_path, monkeypatch, dict(enabled=True, allowed_seats=sorted(D.API_SEATS), trader_enabled=True))
    seen = []
    monkeypatch.setattr(D, "_run", lambda payload, budget: seen.append(payload) or {"ok": True, "text": "ok", "model": D.MODEL})
    for seat in sorted(EIGHTEEN):
        D.ask("check", seat=seat, workload="other", request_id="role-" + seat)
    assert [row["seat"] for row in seen] == sorted(EIGHTEEN)
    assert FW.OWNERS == {"software_engineer", "fixer"}


def test_sampler_passes_each_role_to_all_four_clients_without_an_answer(tmp_path, monkeypatch):
    seen = []

    def make(name):
        def _call(prompt, seat, timeout):
            seen.append((name, seat))
            if seat in ("root", "desk", "all") or seat not in EIGHTEEN:
                raise AssertionError(seat)
            raise RuntimeError("provider not called for a live answer")
        return _call

    for name in SAMPLER.CALLS:
        monkeypatch.setitem(SAMPLER.CALLS, name, make(name))
    path = tmp_path / "state/thf"
    path.mkdir(parents=True)
    (path / "shadow_sampler.json").write_text(json.dumps(dict(
        enabled=True, paper_only=True, provider_timeout_s=5, max_jobs_per_run=18)))
    status = SAMPLER.once(str(tmp_path), now="2026-11-06T15:00:00+00:00")
    assert status["jobs"] == 18 and status["pending_jobs"] == 0
    assert status["window"]["started"] is False and status["seven_day"] == "not_started"
    assert status["recommendation"]["global_winner"] is None
    for seat in EIGHTEEN:
        assert sorted(name for name, got in seen if got == seat) == ["chatgpt", "claude", "deepseek_api", "grok"]
    assert len(seen) == 72


def test_role_installer_replaces_the_reviewed_bases(tmp_path):
    root = Path(__file__).resolve().parents[2]
    install = root / "deploy/role_align_20261106/install.py"
    book = tmp_path / "veteran_desk/book.py"
    book.parent.mkdir(parents=True)
    book.write_bytes((root / "veteran_desk/book.py").read_bytes())
    for rel in ("veteran_desk/seat_registry.py", "veteran_desk/thf_funder.py"):
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(subprocess.check_output(["git", "show", "b078846:" + rel], cwd=root))
    result = subprocess.run([sys.executable, str(install), "--root", str(tmp_path), "--apply"],
                            cwd=root, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout
    assert hashlib.sha256(book.read_bytes()).hexdigest() == "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
    assert (tmp_path / "veteran_desk/seat_registry.py").read_bytes() == (root / "veteran_desk/seat_registry.py").read_bytes()
    back = subprocess.run([sys.executable, str(install), "--root", str(tmp_path), "--rollback"],
                          cwd=root, text=True, capture_output=True, check=False)
    assert back.returncode == 0, back.stdout
    assert hashlib.sha256((tmp_path / "veteran_desk/seat_registry.py").read_bytes()).hexdigest() == "44455cc12c2d81df2b1aa176b8094ecfe1538744662b2057d64b71947bf83de9"
