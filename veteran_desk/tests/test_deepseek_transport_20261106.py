"""DeepSeek transport failures stay specific, secret-free, and retryable."""
import errno
import hashlib
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

from veteran_desk import deepseek_bridge as D
from veteran_desk import lesson_trace as LT
from veteran_desk import thf_shadow_sampler as SAMPLER

LIVE = Path("/tmp/seatlive/thf-upgrade-seat-registry-20261106")
EIGHTEEN = set(LT.SEATS)


def test_live_allow_list_already_matches_the_eighteen_sampler_roles():
    config = json.loads((LIVE / "evidence/deepseek_config_safe.json").read_text())
    registry = (LIVE / "evidence/registry_gap.json").read_text()
    assert set(config["allowed_seats"]) == EIGHTEEN
    assert json.loads(registry)["missing_from_deepseek_vs_LT"] == []
    assert "software_engineer" in config["allowed_seats"] and "cfo" in config["allowed_seats"]
    assert "risk_gate" not in config["allowed_seats"]


def test_exception_class_is_kept_and_exception_text_is_not(tmp_path, monkeypatch):
    secret = "sk-live-secret-value-should-not-appear"
    assert D.failure_for(json.JSONDecodeError("bad " + secret, "doc", 0)) == "response_not_json"
    assert D.failure_for(ValueError("private key permissions")) == "private key permissions"
    assert D.failure_for(ValueError("token " + secret)) == "adapter_value"
    refused = urllib.error.URLError(OSError(errno.ECONNREFUSED, secret))
    assert D.failure_for(refused) == "transport_ECONNREFUSED"
    assert secret not in D.failure_for(ValueError(secret))
    assert SAMPLER.classify_error("DeepSeek transport ValueError") == "error"
    assert SAMPLER.classify_error("DeepSeek response_not_json") == "error"
    assert SAMPLER.classify_error("DeepSeek transport_ECONNREFUSED") == "error"
    assert SAMPLER.classify_error("insufficient balance") == "quota"
    monkeypatch.setattr(D, "STATE", tmp_path)
    monkeypatch.setattr(D, "KEY", tmp_path / "key")
    (tmp_path / "key").write_text("synthetic-test-key")
    (tmp_path / "key").chmod(0o600)
    D._atomic(tmp_path / "config.json", dict(enabled=True, allowed_seats=sorted(EIGHTEEN), trader_enabled=True))
    D.record_result(True, 1, http_status=200, probe=True)
    D.record_result(False, 2, "transport ValueError", None)
    assert D.status()["until"] == 0
    assert "transport ValueError" in D.reason(check_key=False)
    assert "quota" not in D.reason(check_key=False).lower()
    calls = []
    monkeypatch.setattr(D, "_run", lambda payload, budget: calls.append(payload["seat"]) or dict(ok=True, text="ok", model=D.MODEL))
    from veteran_desk import bridge_maintenance as B
    monkeypatch.setattr(B, "client_guard", lambda: None)
    monkeypatch.setattr(B, "mark_submission", lambda: None)
    monkeypatch.setattr(D, "brain_readback", lambda prompt: [])
    monkeypatch.setattr(D, "API_SEATS", frozenset(EIGHTEEN))
    assert D.ask("check", seat="essentials_utilities", workload="other") == "ok"
    assert D.ask("check", seat="healthcare", workload="other") == "ok"
    assert calls == ["essentials_utilities", "healthcare"]


def test_worker_json_error_and_bad_worker_output_hide_the_body(monkeypatch):
    try: json.loads(b"<html>secret-body</html>")
    except json.JSONDecodeError as error:
        assert D.failure_for(error) == "response_not_json"
        assert "secret-body" not in D.failure_for(error)
    monkeypatch.setattr(D.subprocess, "Popen", lambda *a, **k: _Child())
    try: D._run({"prompt": "x"}, 1)
    except RuntimeError as error:
        assert str(error) == "DeepSeek worker output was not JSON"
        assert "secret" not in str(error)


class _Child:
    returncode = 0
    def communicate(self, *a, **k): return "not-json secret-output", ""
    def kill(self): pass


def test_transport_installer_replaces_only_the_live_bridge(tmp_path):
    root = Path(__file__).resolve().parents[2]
    install = root / "deploy/deepseek_transport_20261106/install.py"
    book = tmp_path / "veteran_desk/book.py"
    book.parent.mkdir(parents=True)
    book.write_bytes((root / "veteran_desk/book.py").read_bytes())
    dest = tmp_path / "veteran_desk/deepseek_bridge.py"
    dest.write_bytes((LIVE / "live/veteran_desk/deepseek_bridge.py").read_bytes())
    base = hashlib.sha256(dest.read_bytes()).hexdigest()
    result = subprocess.run([sys.executable, str(install), "--root", str(tmp_path), "--apply"],
                            cwd=root, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout
    assert "ALREADY_PROTECTED veteran_desk/book.py" in result.stdout
    assert hashlib.sha256(book.read_bytes()).hexdigest() == "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == hashlib.sha256((root / "veteran_desk/deepseek_bridge.py").read_bytes()).hexdigest()
    back = subprocess.run([sys.executable, str(install), "--root", str(tmp_path), "--rollback"],
                          cwd=root, text=True, capture_output=True, check=False)
    assert back.returncode == 0, back.stdout
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == base
