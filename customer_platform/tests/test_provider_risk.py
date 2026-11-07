"""Provider jobs and entry stops. No network and no school file changes."""

import json
import os

import pytest
from cryptography.fernet import Fernet

from customer_platform.errors import Denied, ResetBlocked
from customer_platform.platform import Platform
from customer_platform.provider_jobs import ENDPOINTS, ProviderJobs

SECRET = b"test-webhook-secret"


def desk_for(tmp_path):
    desk = Platform(tmp_path / "customers", enabled=True, webhook_secret=SECRET)
    created = desk.provision_tenant("ada@example.com", "correct-horse-1", "Ada", starting_capital="10000.00")
    token = desk.login("ada@example.com", "correct-horse-1")
    return desk, token, created


def test_a_buy_without_a_stop_is_refused_and_a_saved_distance_survives_restart(tmp_path):
    desk, token, created = desk_for(tmp_path)
    desk.use_test_feed_probe(token)
    desk.start_trading(token)
    with pytest.raises(ResetBlocked):
        desk.place_order(token, symbol="AAPL", side="buy", qty="1", limit_price="100.00", idempotency_key="nostop")
    opened = desk.place_order(
        token, symbol="AAPL", side="buy", qty="1", limit_price="100.00", stop="90.00", idempotency_key="withstop",
    )
    filled = desk.fill_order(token, opened["order_id"], price="100.00")
    book = desk._book(created["tenant_id"])
    assert next(row["stop"] for row in book.load()["positions"] if row["status"] == "open") == "90.00"
    desk.stop_trading(token)
    assert next(row["stop"] for row in book.load()["positions"] if row["status"] == "open") == "90.00"
    updated = desk.set_position_stop(token, "AAPL", "95.00")
    assert any(row.get("stop") == "95.00" for row in updated["positions"])
    desk.set_stop_distance(token, "0.10")
    again = Platform(tmp_path / "customers", enabled=True, webhook_secret=SECRET)
    again.use_test_feed_probe(token)
    again.start_trading(token)
    implied = again.place_order(token, symbol="AAPL", side="buy", qty="1", limit_price="50.00", idempotency_key="policy")
    assert implied["stop"] == "45.00"
    assert filled["id"]


def test_provider_job_uses_only_this_tenant_key_and_does_not_invent_an_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("CUSTOMER_PLATFORM_DATA_KEY", Fernet.generate_key().decode())
    desk, token, created = desk_for(tmp_path)
    other = desk.provision_tenant("bea@example.com", "correct-horse-2", "Bea", starting_capital="1000.00")
    bea = desk.login("bea@example.com", "correct-horse-2")
    desk.key_save(bea, "openai", "bea-secret-key-99")
    pending = desk.ask(token, "What is my paper capital?", "ask-job-01")
    assert pending["job"] == "unconnected" and pending["answer"] == "No answer yet."
    desk.key_save(token, "openai", "ada-secret-key-42")
    still = desk.ask(token, "Again?", "ask-job-02")
    assert still["job"] == "unconnected" and still["answer"] == "No answer yet."
    job_path = os.path.join(desk._tenant_dir(created["tenant_id"]), "requests", "provider_jobs.json")
    stored = open(desk._key_path(created["tenant_id"]), encoding="utf-8").read() + open(job_path, encoding="utf-8").read()
    assert "ada-secret-key-42" not in stored and "bea-secret-key-99" not in stored

    seen = {"fail": 0}

    def transport(url, secret, timeout):
        seen["url"] = url
        seen["secret"] = secret
        seen["timeout"] = timeout
        if seen["fail"] < 1:
            seen["fail"] = 1
            raise RuntimeError("temporary")
        return {"synthetic": True, "text": "Synthetic fixture reply."}

    desk.use_mock_provider(token, transport)
    done = desk.ask(token, "Synthetic only", "ask-job-03")
    assert done["answer"] == "No answer yet."
    assert seen["url"] == ENDPOINTS["openai"] and seen["secret"] == "ada-secret-key-42" and seen["timeout"] == 5
    last = json.load(open(job_path))["jobs"][-1]
    assert last["status"] == "complete" and last["synthetic"] is True

    def leak(_url, secret, _timeout):
        return {"synthetic": True, "text": "echo " + secret}

    desk.use_mock_provider(token, leak)
    desk.ask(token, "Do not echo", "ask-job-04")
    assert "ada-secret-key-42" not in open(job_path, encoding="utf-8").read()

    def bad(_url, _secret, _timeout):
        raise RuntimeError("down")

    desk.use_mock_provider(token, bad)
    desk.ask(token, "Bound", "ask-job-05")
    failed = json.load(open(job_path))["jobs"][-1]
    assert failed["attempts"] == 3 and failed["status"] == "failed"
    with pytest.raises(Denied):
        ProviderJobs(str(tmp_path / "unused.json"), lambda: "t").open_job(
            request_id="x", provider="local", has_credential=False,
        )
    assert other["tenant_id"] != created["tenant_id"]
