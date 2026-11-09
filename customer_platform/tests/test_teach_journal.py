"""Approved scope: teach traders, and a private journal that is not scored."""

import os

import pytest

from customer_platform.errors import Denied, ResetBlocked
from customer_platform.files import hash_tree
from customer_platform.platform import Platform

SECRET = b"test-webhook-secret"


def desk_for(tmp_path):
    desk = Platform(tmp_path / "customers", enabled=True, webhook_secret=SECRET)
    created = desk.provision_tenant("ada@example.com", "correct-horse-1", "Ada", starting_capital="10000.00")
    token = desk.login("ada@example.com", "correct-horse-1")
    return desk, token, created


def test_described_skill_is_saved_without_an_ai_reading(tmp_path):
    desk, token, _created = desk_for(tmp_path)
    saved = desk.teach_save(token, trader_ids=["desk"], rules={
        "entry": "Buy only after a paper plan is written",
        "exit": "Sell if the reason is gone",
        "risk": "One small practice position",
        "market": "Listed shares",
    })
    assert saved["ai"] == "disabled"
    assert saved["status"] == "draft"
    assert saved["rules_source"] == "your words"
    assert saved["promise"] is False
    with pytest.raises(ResetBlocked):
        desk.teach_status(token, saved["id"], "active")


def test_upload_is_data_and_another_tenant_cannot_read_it(tmp_path):
    desk, token, created = desk_for(tmp_path)
    other = desk.provision_tenant("bea@example.com", "correct-horse-2", "Bea", starting_capital="1000.00")
    other_token = desk.login("bea@example.com", "correct-horse-2")
    saved = desk.teach_upload(
        token, trader_ids=["desk"], filename="plan.txt",
        data=b"Entry: wait\nExit: flat\nRisk: small\nMarket: shares\n",
    )
    assert saved["status"] == "draft"
    with pytest.raises(ResetBlocked):
        desk.teach_upload(token, trader_ids=["desk"], filename="run.sh", data=b"#!/bin/sh\necho hi\n")
    with pytest.raises(Denied):
        desk.teach_list(other_token, claimed_tenant=created["tenant_id"])
    assert desk.teach_list(other_token) == []
    assert saved["id"] not in [row["id"] for row in desk.teach_list(other_token)]
    assert other["tenant_id"] != created["tenant_id"]


def test_rails_and_reset_keep_the_skill_and_journal(tmp_path):
    desk, token, created = desk_for(tmp_path)
    desk.use_test_feed_probe(token)
    desk.start_trading(token)
    opened = desk.place_order(token, symbol="AAPL", side="buy", qty="1", limit_price="10", stop="9", idempotency_key="skill-buy-01")
    filled = desk.fill_order(token, opened["order_id"], price="10")
    closed = desk.place_order(token, symbol="AAPL", side="sell", qty="1", limit_price="9", idempotency_key="skill-sell-01")
    sold = desk.fill_order(token, closed["order_id"], price="9")
    saved = desk.teach_save(token, trader_ids=["desk"], text="Entry: plan\nExit: flat\nRisk: small\nMarket: shares\nDisable stops")
    assert any("cannot change stops" in item for item in saved["clarifications"])
    blocked = desk.teach_run_test(token, saved["id"])
    assert blocked["last_test"]["risk_ok"] is False and blocked["last_test"]["validated_skill"] is False
    with pytest.raises(ResetBlocked):
        desk.teach_status(token, saved["id"], "paper_testing", evidence_ids=[sold["id"]])
    clean = desk.teach_save(token, trader_ids=["desk"], text="Entry: plan\nExit: flat\nRisk: stop 5 percent below entry\nMarket: shares\n")
    tested = desk.teach_run_test(token, clean["id"])
    assert tested["last_test"]["synthetic"] is True and tested["status"] == "paper_testing"
    reviewed = desk.teach_status(token, tested["id"], "ready_for_review")
    active = desk.teach_status(token, reviewed["id"], "active")
    assert active["status"] == "active"
    rails = (tmp_path / "customers" / "tenants" / created["tenant_id"] / "brain" / "rails.json").read_bytes()
    rolled = desk.teach_rollback(token, saved["id"], 1)
    assert rolled["status"] == "draft"
    note = desk.journal_add(token, kind="mistake_review", text="I entered without a written reason.")
    assert note["scored"] is False
    with pytest.raises(ResetBlocked):
        desk._journal(created["tenant_id"]).score()
    skill_hash = hash_tree(os.path.join(desk.store.root, "tenants", created["tenant_id"], "skills"))
    journal_hash = hash_tree(os.path.join(desk.store.root, "tenants", created["tenant_id"], "journal"))
    preview = desk.preview_reset(token)
    desk.reset_capital(
        token, amount="10000.00", preview_token=preview["preview_token"],
        confirmation="RESET PAPER CAPITAL", idempotency_key="reset-skill-01",
    )
    assert hash_tree(os.path.join(desk.store.root, "tenants", created["tenant_id"], "skills")) == skill_hash
    assert hash_tree(os.path.join(desk.store.root, "tenants", created["tenant_id"], "journal")) == journal_hash
    assert (tmp_path / "customers" / "tenants" / created["tenant_id"] / "brain" / "rails.json").read_bytes() == rails
    assert filled["id"]


def test_start_blocked_notifies_operator_only_inside_the_app(tmp_path):
    desk, token, _created = desk_for(tmp_path)
    result = desk.start_trading(token)
    assert result["started"] is False and result["orders_sent"] == 0
    with pytest.raises(Denied):
        desk.operator_incidents(token)
    desk.create_operator("op@example.com", "correct-horse-9", "Op")
    report = desk.operator_incidents(desk.operator_login("op@example.com", "correct-horse-9"))
    assert report["external_notification"] == "off"
    assert report["engineer_running"] is False
    assert report["incidents"][0]["code"] == "prices_unavailable"
    again = desk.start_trading(token)
    assert again["started"] is False
    report = desk.operator_incidents(desk.operator_login("op@example.com", "correct-horse-9"))
    assert len(report["incidents"]) == 1
    assert report["incidents"][0]["recurrence"] >= 2


def test_school_draft_is_not_a_published_course(tmp_path):
    desk, token, _created = desk_for(tmp_path)
    catalog = desk.school_catalog(token)
    assert catalog["name"] == "The Transparent Trading School"
    assert catalog["published"] is False
    assert catalog["content_finished"] is False
    assert len(catalog["modules"]) == 21
    assert all(row["unlocked"] is False for row in catalog["modules"])
    with pytest.raises(ResetBlocked):
        desk.school_begin(token, "tts.m01")
    joined = str(catalog)
    assert "correct_option" not in joined
