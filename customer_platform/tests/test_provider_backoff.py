"""Provider retries pause, a crashed pending attempt is abandoned, and fixture labels follow the transport."""

import json
import time

import pytest
from cryptography.fernet import Fernet

from customer_platform.files import write_json
from customer_platform.pages import desk as desk_page
from customer_platform.platform import Platform

SECRET = b"test-webhook-secret"


def test_provider_failures_back_off_and_then_pause(tmp_path, monkeypatch):
    monkeypatch.setenv("CUSTOMER_PLATFORM_DATA_KEY", Fernet.generate_key().decode())
    desk, token, tenant = _active(tmp_path, _claiming_real_opener())
    clock = {"t": time.time()}
    monkeypatch.setattr(time, "time", lambda: clock["t"])
    calls = {"n": 0}

    def opener(request, timeout):
        calls["n"] += 1
        payload = json.dumps({"choices": [{"message": {"content": "not-json"}}]}).encode("utf-8")

        class Reply:
            status = 200

            def read(self, limit=-1):
                return payload if limit < 0 else payload[:limit]

        return Reply()

    desk.install_scratch_opener(token, opener)
    for _ in range(8):
        desk.paper_tick_tenant(tenant)
    assert calls["n"] == 1
    clock["t"] += 1.3
    desk.paper_tick_tenant(tenant)
    clock["t"] += 2.5
    desk.paper_tick_tenant(tenant)
    clock["t"] += 5
    desk.paper_tick_tenant(tenant)
    assert calls["n"] == 4
    before = len(desk.decision_audit(tenant))
    clock["t"] += 100
    for _ in range(8):
        tick = desk.paper_tick_tenant(tenant)
    assert calls["n"] == 4
    assert tick["provider_status"] == "provider paused"
    assert len(desk.decision_audit(tenant)) == before
    assert any(row.get("status") == "provider paused" for row in desk.decision_audit(tenant))
    page = desk_page(desk.desk(token), "csrf")
    assert "Provider paused" in page
    assert not [row for row in desk._book(tenant).load()["orders"] if row.get("decision") == "provider_decision"]


def test_startup_abandons_a_pending_attempt_without_filling_it(tmp_path):
    desk = Platform(tmp_path / "customers", enabled=True, webhook_secret=SECRET)
    created = desk.provision_tenant("ada@example.com", "correct-horse-1", "Ada", starting_capital="1000.00")
    tenant = created["tenant_id"]
    abandoned = {
        "epoch": desk._epoch(tenant),
        "id": "dec_abandoned",
        "lease_until": time.time() + 100,
        "print": "100.00",
        "skill_id": "sk_old",
        "status": "pending",
        "version": "1",
    }
    write_json(desk._attempt_path(tenant), abandoned)
    restarted = Platform(tmp_path / "customers", enabled=True, webhook_secret=SECRET)
    current = json.loads(open(restarted._attempt_path(tenant), encoding="utf-8").read())
    assert current["status"] == "abandoned"
    assert current["reason"] == "pending attempt abandoned on startup"
    ready = restarted.prepare_provider_commit(
        tenant, abandoned, {"ok": True, "stop_percent": "5", "symbol": "AAPL"},
    )
    assert ready["status"] == "skipped"
    assert restarted._book(tenant).load()["orders"] == []
    nxt = restarted.open_provider_attempt(tenant, {"skill_id": "sk_new", "version": 2}, "100.00")
    assert nxt["id"] != "dec_abandoned" and nxt["status"] == "pending"
    assert any(row.get("status") == "abandoned" and row.get("id") == "dec_abandoned" for row in restarted.decision_audit(tenant))


def test_fixture_label_comes_from_the_transport_not_the_reply_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("CUSTOMER_PLATFORM_DATA_KEY", Fernet.generate_key().decode())
    desk, token, _tenant = _active(tmp_path, _claiming_real_opener(), interpret_only=True)
    shown = desk.teach_list(token)[0]
    assert shown["ai"] == "test fixture"


def _bad_opener():
    def opener(request, timeout):
        class Reply:
            status = 200

            def read(self, limit=-1):
                return b"{}"

        return Reply()

    return opener


def _claiming_real_opener():
    def opener(request, timeout):
        payload = json.dumps({
            "choices": [{"message": {"content": json.dumps({
                "market": "shares", "side": "buy", "stop_percent": "5", "symbol": "AAPL",
            })}}],
            "synthetic": False,
        }).encode("utf-8")

        class Reply:
            status = 200

            def read(self, limit=-1):
                return payload if limit < 0 else payload[:limit]

        return Reply()

    return opener


def _active(tmp_path, opener, interpret_only=False):
    desk = Platform(tmp_path / "customers", enabled=True, webhook_secret=SECRET)
    created = desk.provision_tenant("ada@example.com", "correct-horse-1", "Ada", starting_capital="10000.00")
    token = desk.login("ada@example.com", "correct-horse-1")
    tenant = created["tenant_id"]
    desk.key_save(token, "openai", "ada-secret-key-42")
    desk.enable_provider_send(token, "openai", "Send to my provider")
    saved = desk.teach_save(token, trader_ids=["desk"], text="I want one share of Apple and a five percent stop under the entry.")
    desk.arm_synthetic_replay(token)
    desk.set_replay_print(token, "100.00")
    desk.start_trading(token)
    desk.install_scratch_opener(token, opener)
    interpreted = desk.teach_interpret(token, saved["id"])
    assert interpreted["ai"] == "test fixture"
    if interpret_only:
        return desk, token, tenant
    desk.teach_run_test(token, saved["id"])
    desk.teach_status(token, saved["id"], "ready_for_review")
    desk.teach_status(token, saved["id"], "active")
    return desk, token, tenant
