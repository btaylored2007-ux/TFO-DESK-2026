"""Synthetic owner-chat clarity cases. No private logs and no live network."""
import hashlib
from pathlib import Path

from veteran_desk import ai_router as AR
from veteran_desk import supergrok_bridge as SG
from veteran_desk import thf_chat_clarity as CL
from veteran_desk import thf_chat_home as CH
from veteran_desk import thf_funder as TF

ROOT = Path(__file__).resolve().parents[2]
KIT = ROOT / "deploy/display-epoch-ui-kit-20261107.tar.gz"
KIT_SHA = "abfc2af4bdeae0959727afb14f308b0f99ebab06eb7e87bce583e872a5f955e4"


def _row(**kwargs):
    base = dict(from_="desk", to="brett", text="alpha answer", kind="reply", root="req-1", ts=1)
    base.update(kwargs)
    if "from_" in base:
        base["from"] = base.pop("from_")
    return base


def test_chat_01_one_lead_for_reply_and_report():
    view = CL.present([
        _row(id="a", kind="reply", text="alpha answer"),
        _row(id="b", kind="report", text="alpha answer again"),
    ])
    shown = [row for row in view["owner"] if row.get("kind") != "request"]
    assert len(shown) == 1 and shown[0]["lead"] is True
    assert view["rewritten"] is False
    assert any(row.get("folded") for row in view["audit"])


def test_chat_02_routine_ack_is_hidden_and_retained():
    view = CL.present([
        _row(id="q", from_="brett", to="ALL", kind="request", text="question"),
        _row(id="k", kind="ack", text="ack only"),
    ])
    assert [row["event_id"] for row in view["owner"]] == ["q"]
    assert any(row["event_id"] == "k" and row["clarity_hide"] for row in view["audit"])


def test_chat_03_halt_stays_visible():
    view = CL.present([_row(id="h", kind="ack", text="feed down halt", critical=True)])
    assert view["owner"][0]["critical"] is True
    assert not view["owner"][0].get("clarity_hide")


def test_chat_04_independent_answers_are_not_consensus():
    view = CL.present([
        _row(id="c", provider="claude", from_="seat", to="head_trader", text="one view"),
        _row(id="g", provider="chatgpt", from_="seat", to="head_trader", text="other view"),
    ])
    assert all(row["independent"] and row["consensus"] is False for row in view["owner"])
    assert all(row["audience"] == "head_trader" for row in view["owner"])
    assert "All reviewed" not in " ".join(row["prov"] for row in view["owner"])


def test_chat_05_snapshot_disagreement_keeps_both_texts():
    original = ["scope A says 3", "scope B says 4"]
    view = CL.present([
        _row(id="a", snapshot_id="s1", scope="open", text=original[0]),
        _row(id="b", provider="other", snapshot_id="s2", scope="closed", text=original[1]),
    ])
    assert [row["text"] for row in view["owner"]] == original
    assert all(row["unresolved"] and row["consensus"] is False for row in view["owner"])


def test_chat_06_missing_ids_retries_and_order():
    view = CL.present([
        _row(text="first", event_time=2, ts=2),
        _row(text="second", event_time=1, ts=1),
        _row(id="same", text="first", event_time=3, ts=3),
    ])
    ids = [row["event_id"] for row in view["audit"]]
    assert ids[0] != ids[1]
    assert all(not row["migrated_id"] or row["event_id"].startswith("mig-") for row in view["audit"])
    owner_ids = [row["event_id"] for row in view["owner"]]
    assert owner_ids[0] == ids[1]
    assert any(row.get("duplicate_ids") for row in view["owner"])


def test_chat_07_late_answer_is_not_an_action():
    view = CL.present([_row(id="late", status="expired", text="old answer")])
    row = view["owner"][0]
    assert row["stale"] and row["current"] is False and row["action"] is False
    assert row["text"] == "old answer"


def test_chat_08_hop_and_task_budgets():
    assert CL.relay_allowed(2) is True
    assert CL.relay_allowed(3) is False
    assert CL.task_allowed({"from": "brett", "kind": "request"}) is True
    assert CL.task_allowed({"from": "seat", "kind": "reply"}) is False
    assert CL.task_allowed({"from": "brett", "kind": "ack"}) is False


def test_chat_09_other_tenant_cannot_read_operator_records():
    record = {"visibility": "operator", "tenant": "owner", "kind": "internal_mail"}
    assert CL.tenant_allows({"tenant": "customer"}, record) is False
    assert CL.tenant_allows({"tenant": "owner"}, record) is True


def test_chat_10_instruction_tokens_are_not_silently_dropped():
    note = CL.wording_note("Each desk keeps one written plan for the open.", "The quote says plan and drops the count.")
    assert note["ambiguous"] is True and note["policy_changed"] is False
    assert CL.wording_note("keep the plan", "keep the plan")["preserved"] is True


def test_selector_is_honest_and_history_does_not_connect(monkeypatch):
    monkeypatch.setattr(SG, "snapshot", lambda path=None: ("unavailable", "SuperGrok has no fresh composer check"))
    rows = [{"from": "supergrok", "to": "brett", "text": "old", "k": 1}]
    assert "supergrok" not in CH.seats(rows)
    home = CH.home(rows=rows, adv=[dict(chat="adv:supergrok", k=1, text="old", from_="supergrok")], reg=CH.seats(rows), hb={})
    card = next(item for item in home["chats"] if item["id"] == "adv:supergrok")
    assert card["available"] is False and card["can_send"] is False
    assert AR.norm("grok") == "grok" and AR.norm("super") == "super" and AR.norm("supergrok") == "supergrok"
    assert AR.norm("sg") == "auto"
    assert AR.order("supergrok") == ["supergrok"]
    assert "supergrok" not in AR.FAN and "supergrok" not in AR.order("auto") and "supergrok" not in AR.order("super")
    try:
        TF.post("seat", "question", "banks", ai="supergrok")
    except ValueError as error:
        assert "not connected" in str(error)
    else:
        raise AssertionError("a disconnected provider accepted a message")


def test_page_health_is_required_and_worker_has_no_bypass():
    assert SG.classify({"state": "logged_in"})[0] == "blocked"
    assert SG.classify({"state": "captcha", "checked_epoch": 0}, fetched_at=10)[0] in ("blocked", "stale")
    assert SG.classify({"state": "logged_in", "composer": "ready", "checked_epoch": 100}, fetched_at=100)[0] == "ready"
    import time
    now = time.time()
    try:
        SG.ask("ping", health_body={"state": "needs_signin", "checked_epoch": now}, url="http://127.0.0.1:18892")
    except RuntimeError as error:
        assert "sign-in" in str(error)
    else:
        raise AssertionError("ask proceeded without a composer")
    assert SG.classify({"state": "logged_in", "composer": "ready", "checked_epoch": 1}, fetched_at=now)[0] == "stale"
    worker = (ROOT / "tools/grok_web.py").read_text(encoding="utf-8")
    assert "disable-blink-features" not in worker
    assert "vnc.html" not in worker
    assert "adv_add" not in worker
    assert "cookie" not in worker.lower() or "never" in worker.lower()


def test_owner_chat_package_fixture():
    import subprocess
    import sys
    script = ROOT / "deploy/owner_chat_20261107/tests/fixture_matrix.py"
    run = subprocess.run([sys.executable, str(script)], text=True, capture_output=True, check=False)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "FIXTURE_OK"


def test_archived_display_kit_bytes_are_unchanged():
    assert hashlib.sha256(KIT.read_bytes()).hexdigest() == KIT_SHA


def test_owner_chat_preflight_refuses_production_and_activation(tmp_path):
    import importlib.util
    import shutil
    spec = importlib.util.spec_from_file_location("owner_chat_install", ROOT / "deploy/owner_chat_20261107/install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.preflight("/opt/tfo/live/thf") == "REFUSED_ROOT"
    assert module.preflight(tmp_path, activate=True) == "REFUSED_ACTIVATION"
    payload = ROOT / "deploy/owner_chat_20261107/payload"
    for path in payload.rglob("*"):
        if path.is_file():
            dest = tmp_path / path.relative_to(payload)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
    report = module.preflight(tmp_path)
    assert "NOT_ACTIVATED" in report and "REFUSED" not in report
    assert "systemctl" not in (ROOT / "deploy/owner_chat_20261107/install.py").read_text(encoding="utf-8")
