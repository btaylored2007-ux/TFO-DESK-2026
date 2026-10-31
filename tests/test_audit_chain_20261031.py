"""Hash-chained paper audit trail. No network, no live state/audit writes."""
import datetime as dt
import fcntl
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import audit


def _use(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DIR", str(tmp_path))
    return str(tmp_path)


def _lines(tmp_path):
    files = sorted(tmp_path.glob("audit_*.jsonl"))
    out = []
    for f in files:
        out.extend(json.loads(line) for line in f.read_text().splitlines() if line.strip())
    return out


def test_chain_links_and_verifier(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch)
    h1 = audit.log("paper_order", "veteran", {"id": "a", "sym": "SPY"})
    h2 = audit.log("fill", "veteran", {"id": "a", "px": 10})
    assert h1 and h2 and h1 != h2
    rows = _lines(tmp_path)
    assert rows[0]["prev_hash"] == audit.GENESIS
    assert rows[0]["hash"] == h1
    assert rows[1]["prev_hash"] == h1
    assert rows[1]["hash"] == audit._hash_of(rows[1])
    result = audit.verify(str(tmp_path))
    assert result["ok"] is True
    assert result["records"] == 2
    assert result["tip"] == h2


def test_cross_month_prev_hash(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch)
    oct_ = dt.datetime(2026, 10, 31, 12, tzinfo=dt.timezone.utc)
    nov = dt.datetime(2026, 11, 1, 0, 1, tzinfo=dt.timezone.utc)
    h1 = audit.log("deploy", "tfo", {"tag": "oct"}, ts=oct_)
    h2 = audit.log("deploy", "tfo", {"tag": "nov"}, ts=nov)
    files = sorted(p.name for p in tmp_path.glob("audit_*.jsonl"))
    assert files == ["audit_202610.jsonl", "audit_202611.jsonl"]
    nov_row = json.loads((tmp_path / "audit_202611.jsonl").read_text().splitlines()[0])
    assert nov_row["prev_hash"] == h1
    assert nov_row["hash"] == h2
    assert audit.verify(str(tmp_path))["ok"] is True


def test_tamper_and_truncation_are_breaks(tmp_path, monkeypatch):
    root = _use(tmp_path, monkeypatch)
    audit.log("stop", "tfo", {"sym": "XOM", "px": 100})
    audit.log("exit", "tfo", {"sym": "XOM", "px": 90})
    first = audit.verify(root)
    assert first["ok"]
    audit.write_status(first, root)
    path = next(tmp_path.glob("audit_*.jsonl"))
    text = path.read_text().replace("XOM", "XYZ", 1)
    path.write_text(text)
    broken = audit.verify(root)
    assert broken["ok"] is False
    assert any(b["error"] == "hash mismatch" for b in broken["breaks"])
    path.unlink()
    audit.log("fill", "tfo", {"sym": "NEW"})
    gone = audit.verify(root)
    assert gone["ok"] is False
    assert any("no longer in the chain" in b["error"] for b in gone["breaks"])


def test_redacts_secrets(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch)
    audit.log("config_change", "tfo", {
        "api_key": "sk-live-should-not-appear",
        "nested": {"access_token": "tok-abc", "px": 1},
        "note": "Authorization: Bearer supersecretvalue token=also-secret",
        "authorised": "Brett",
    })
    raw = next(tmp_path.glob("audit_*.jsonl")).read_text()
    assert "sk-live-should-not-appear" not in raw
    assert "supersecretvalue" not in raw
    assert "also-secret" not in raw
    assert "tok-abc" not in raw
    assert "[REDACTED]" in raw
    assert "Brett" in raw
    row = json.loads(raw)
    assert row["payload"]["authorised"] == "Brett"
    assert row["payload"]["nested"]["px"] == 1


def test_failure_does_not_block_stop(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    monkeypatch.setenv("AUDIT_DIR", str(blocker / "audit"))

    def stop_out():
        booked = {"status": "closed", "reason": "stop"}
        audit.log("stop", "kraken", {"pair": "XBT/USD", "px": 1})
        return booked

    assert stop_out()["status"] == "closed"

    monkeypatch.setenv("AUDIT_DIR", str(tmp_path / "ok"))
    audit.log("fill", "tfo", {"n": 1})
    lock = open(tmp_path / "ok" / ".lock", "a+")
    fcntl.flock(lock, fcntl.LOCK_EX)
    t0 = time.monotonic()

    def held_stop():
        audit.log("stop", "oanda", {"instrument": "EUR_USD"})
        return "flat"

    assert held_stop() == "flat"
    assert time.monotonic() - t0 < 1.0
    fcntl.flock(lock, fcntl.LOCK_UN)
    lock.close()
    result = audit.verify(str(tmp_path / "ok"))
    assert result["ok"] is True
    assert result["records"] == 1


def test_concurrent_appends_stay_chained(tmp_path, monkeypatch):
    root = _use(tmp_path, monkeypatch)
    got = []
    gate = threading.Lock()

    def worker(n):
        for i in range(4):
            h = audit.log("fill", "hunts", {"n": n, "i": i})
            if h:
                with gate:
                    got.append(h)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    result = audit.verify(root)
    assert result["ok"] is True
    assert result["records"] == len(got)
    assert len(got) >= 1


def test_classify_and_book_events(tmp_path, monkeypatch):
    root = _use(tmp_path, monkeypatch)
    assert audit.classify("armed") == "armed_setup"
    assert audit.classify("filled") == "fill"
    assert audit.classify("stop_gap_through") == "stop"
    assert audit.classify("full_flat_5pct") == "exit"
    assert audit.classify("rail_giveback_protect") == "exit"
    assert audit.classify("rail_2R_quarter_out") == "exit"
    assert audit.classify("stop_update_refused") == "risk_decision"
    assert audit.classify("entry_order") == "paper_order"
    assert audit.classify("closed_disabled") == "exit"
    assert audit.classify("entry_dry") is None
    audit.book_events("thf", [("id1", "armed", 10), ("id1", "filled", 11), ("id1", "stop", 9)])
    kinds = [r["event_type"] for r in _lines(tmp_path)]
    assert kinds == ["paper_order", "armed_setup", "fill", "stop"]
    assert audit.verify(root)["ok"] is True


def test_deploy_emits_rule_and_config(tmp_path, monkeypatch):
    root = _use(tmp_path, monkeypatch)
    audit.deploy("safe_deploy", tag="cap", files=["RULE_LOG.md", "state/desk_config.json"], note="paper")
    kinds = [r["event_type"] for r in _lines(tmp_path)]
    assert kinds[0] == "deploy"
    assert "rule_change" in kinds
    assert "config_change" in kinds
    assert audit.verify(root)["ok"] is True


def test_pytest_does_not_write_live_audit(monkeypatch):
    monkeypatch.delenv("AUDIT_DIR", raising=False)
    assert os.environ.get("PYTEST_CURRENT_TEST")
    assert audit.log("exit", "veteran", {"sym": "TEST"}) is None
    live = ROOT / "state" / "audit"
    assert not list(live.glob("audit_*.jsonl")) if live.exists() else True


def test_cli_verify_and_emit(tmp_path):
    env = os.environ.copy()
    env["AUDIT_DIR"] = str(tmp_path)
    env["AUDIT_EVENT"] = "deploy"
    env["AUDIT_SOURCE"] = "safe_deploy"
    env["AUDIT_DESK"] = "tfo"
    env["AUDIT_TAG"] = "rules"
    env["AUDIT_FILES"] = "RULE_LOG.md"
    env["AUDIT_NOTE"] = "token=supersecretvalue"
    proc = subprocess.run([sys.executable, "-m", "audit", "emit"], cwd=str(ROOT), env=env, capture_output=True, text=True)
    assert proc.returncode == 0
    raw = next(tmp_path.glob("audit_*.jsonl")).read_text()
    assert "supersecretvalue" not in raw
    assert "rule_change" in raw
    ok = subprocess.run([sys.executable, "-m", "audit", "verify", "--dir", str(tmp_path), "--write-status"],
                        cwd=str(ROOT), env=env, capture_output=True, text=True)
    assert ok.returncode == 0
    assert ok.stdout.startswith("OK ")
    status = json.loads((tmp_path / "verify_status.json").read_text())
    assert status["ok"] is True
    path = next(tmp_path.glob("audit_*.jsonl"))
    path.write_text(path.read_text().replace("safe_deploy", "tampered", 1))
    bad = subprocess.run([sys.executable, "-m", "audit", "verify", "--dir", str(tmp_path)],
                         cwd=str(ROOT), env=env, capture_output=True, text=True)
    assert bad.returncode == 1
    assert bad.stdout.startswith("BREAK")


def test_card_reports_nightly_status(tmp_path, monkeypatch):
    root = _use(tmp_path, monkeypatch)
    audit.log("fill", "v2", {"sym": "AAPL"})
    card = audit.card()
    assert card["status"] == "UNVERIFIED"
    assert card["paper_only"] is True
    audit.write_status(audit.verify(root), root)
    assert audit.card()["status"] == "INTACT"
    audit.log("exit", "v2", {"sym": "AAPL"})
    moved = audit.card()
    assert moved["status"] == "TIP_MOVED"
    assert moved["since_verify"] is True


def test_safe_deploy_and_plist_hooked():
    sh = (ROOT / "tools" / "safe_deploy.sh").read_text()
    assert "audit emit" in sh
    assert "|| true" in sh
    plist = (ROOT / "launchd" / "com.tfo.audit-verify.plist").read_text()
    assert "audit" in plist and "verify" in plist and "--write-status" in plist
    html = (ROOT / "command_dashboard.html").read_text()
    assert 'id="auditcard"' in html and "/api/audit" in html
