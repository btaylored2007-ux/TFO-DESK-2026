"""Reset and private-brain tests for the customer web template."""

import json
import os
import threading
from decimal import Decimal
from pathlib import Path

import pytest

from customer_platform.errors import (
    Crash,
    Denied,
    IdempotencyConflict,
    InvalidAmount,
    LiveDisabled,
    RailsIntegrity,
    ResetBlocked,
)
from customer_platform.files import append_jsonl, hash_tree, read_bytes
from customer_platform.http_app import worker_loop
from customer_platform.money import parse_money
from customer_platform.platform import Platform
from veteran_desk.tenant_foundation import FoundationOff

ROOT = Path(__file__).resolve().parents[2]
PHRASE = "RESET PAPER CAPITAL"
SECRET = b"test-webhook-secret"


def platform(tmp_path, **kwargs):
    kwargs.setdefault("webhook_secret", SECRET)
    return Platform(tmp_path / "customers", enabled=True, **kwargs)


def account(tmp_path, **kwargs):
    desk = platform(tmp_path, **kwargs)
    created = desk.provision_tenant("ada@example.com", "correct-horse-1", "Ada", starting_capital="10000.00")
    token = desk.login("ada@example.com", "correct-horse-1")
    return desk, token, created


def trade(desk, token, *, symbol="AAPL", qty="10", buy="100.00", sell="90.00", fx=None, sector=None, key="a"):
    opened = desk.place_order(
        token, symbol=symbol, side="buy", qty=qty, limit_price=buy, sector=sector,
        idempotency_key="buy-{0}-{1}".format(key, symbol),
    )
    desk.fill_order(token, opened["order_id"], price=buy, fx=fx)
    closed = desk.place_order(
        token, symbol=symbol, side="sell", qty=qty, limit_price=sell, sector=sector,
        idempotency_key="sell-{0}-{1}".format(key, symbol),
    )
    return desk.fill_order(token, closed["order_id"], price=sell, fx=fx)


def reset(desk, token, amount=None, *, key="reset-key-01", ack=False, crash_at=None, claimed=None):
    preview = desk.preview_reset(token, amount, claimed_tenant=claimed)
    return desk.reset_capital(
        token, amount=amount, preview_token=preview["preview_token"], confirmation=PHRASE,
        idempotency_key=key, acknowledge_zero=ack, crash_at=crash_at, claimed_tenant=claimed,
    )


def test_foundation_stays_off_and_unmounted(tmp_path):
    with pytest.raises(FoundationOff):
        Platform(tmp_path / "customers")
    for relative in ("veteran_desk/thf_dash.py", "veteran_desk/book.py", "veteran_desk/gate_v2.py"):
        assert "customer_platform" not in (ROOT / relative).read_text(encoding="utf-8")
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "customer_platform").rglob("*.py")
        if "tests" not in path.parts
    )
    for banned in ("veteran_desk.book", "veteran_desk.gate_v2", "veteran_desk.desk", "veteran_desk.learning", "electron", "pyinstaller"):
        assert banned not in source
    assert "/equity" not in source and "/ equity" not in source


def test_signup_is_closed_until_the_web_process_enables_it(tmp_path):
    desk = platform(tmp_path)
    with pytest.raises(Denied):
        desk.signup("ada@example.com", "correct-horse-1", "Ada", starting_capital="10000.00")
    opened = platform(tmp_path / "web", signup_enabled=True)
    created = opened.signup("bea@example.com", "correct-horse-1", "Bea", starting_capital="2500.00")
    other = opened.signup("cy@example.com", "correct-horse-2", "Cy", starting_capital="2500.00")
    assert created["tenant_id"] != other["tenant_id"]
    status = opened.setup_status()
    assert status["product"] == "hosted customer web app"
    assert "desktop" in status["distribution"]
    assert "public leaderboard statistics" in status["later_not_built"]
    assert status["private_performance_published"] is False
    assert status["public_signup"] is False
    assert status["web_signup_enabled"] is True


def test_money_rejects_float_nan_negative_and_extra_places():
    assert parse_money("10000", "USD") == Decimal("10000.00")
    assert parse_money("0", "USD") == Decimal("0.00")
    assert parse_money("0", "JPY") == Decimal("0")
    for bad in (0.1, True, "NaN", "Infinity", "-1", "10.999", "1e2", "0.000", ""):
        with pytest.raises(InvalidAmount):
            parse_money(bad, "USD")
    with pytest.raises(InvalidAmount):
        parse_money("1.0", "JPY")
    with pytest.raises(InvalidAmount):
        parse_money("1000000.01", "USD")


def test_reset_uses_chosen_capital_and_starts_epoch_pnl_at_zero(tmp_path):
    desk, token, _created = account(tmp_path)
    trade(desk, token)
    tenant = desk.desk(token)["tenant_id"]
    before = desk.tree_hashes(tenant)
    preview = desk.preview_reset(token)
    assert preview["amount_is_default"] is True
    assert preview["chosen_starting_capital"] == "10000.00"
    assert preview["new_epoch_session_pnl"] == "0.00"
    assert "not the capital" in preview["new_epoch_session_pnl_label"]
    assert preview["historical_realized_pnl"] == "-100.00"
    result = reset(desk, token, "2500.50", key="reset-chosen-01")
    view = desk.desk(token)
    assert result["new_epoch_capital"] == "2500.50"
    assert result["new_epoch_session_pnl"] == "0.00"
    assert view["practice_capital"] == "2500.50"
    assert view["session_pnl"] == "0.00"
    assert view["historical_realized_pnl"] == "-100.00"
    assert view["currency"] == "USD"
    after = desk.tree_hashes(view["tenant_id"])
    assert after["brain"] == before["brain"]
    assert after["knowledge"] == before["knowledge"]
    assert after["ledger"].startswith(before["ledger"])
    assert b"balance_adjustment" in after["ledger"]
    epochs = desk.performance(token)["by_epoch"]
    assert epochs[0]["realized_pnl"] == "-100.00"
    assert desk.preview_reset(token)["configured_starting_capital"] == "10000.00"


def test_zero_capital_warns_and_blocks_new_entries_without_erasing_brain(tmp_path):
    desk, token, created = account(tmp_path)
    trade(desk, token)
    desk.run_brain(token, trigger="manual", idempotency_key="brain-before-zero")
    tenant = created["tenant_id"]
    brain = hash_tree(os.path.join(desk.store.root, "tenants", tenant, "brain"))
    preview = desk.preview_reset(token, "0")
    assert preview["zero_warning"]
    with pytest.raises(ResetBlocked) as blocked:
        reset(desk, token, "0", key="reset-zero-01")
    assert blocked.value.code == "zero_acknowledgement_required"
    result = reset(desk, token, "0.00", key="reset-zero-01", ack=True)
    assert result["new_epoch_capital"] == "0.00"
    assert result["new_epoch_session_pnl"] == "0.00"
    with pytest.raises(ResetBlocked) as risk:
        desk.place_order(token, symbol="AAPL", side="buy", qty="1", limit_price="10.00", idempotency_key="blocked-buy-01")
    assert risk.value.code == "zero_equity"
    assert "ratio" in str(risk.value)
    assert hash_tree(os.path.join(desk.store.root, "tenants", tenant, "brain")) == brain
    desk.add_paper_funds(token, "100.00", "PAPER FUNDS NOT REAL MONEY")
    opened = desk.place_order(token, symbol="AAPL", side="buy", qty="1", limit_price="10.00", idempotency_key="funded-buy-01")
    assert opened["status"] == "pending"
    assert desk.desk(token)["session_pnl"] == "0.00"


def test_open_position_and_pending_order_block_reset(tmp_path):
    desk, token, created = account(tmp_path)
    tenant = created["tenant_id"]
    opened = desk.place_order(token, symbol="AAPL", side="buy", qty="1", limit_price="10.00", idempotency_key="open-buy-01")
    desk.fill_order(token, opened["order_id"], price="10.00")
    before = read_bytes(os.path.join(desk.store.root, "tenants", tenant, "paper", "ledger.jsonl"))
    brain = hash_tree(os.path.join(desk.store.root, "tenants", tenant, "brain"))
    with pytest.raises(ResetBlocked) as blocked:
        reset(desk, token, key="reset-open-01")
    assert blocked.value.code == "open_risk"
    assert read_bytes(os.path.join(desk.store.root, "tenants", tenant, "paper", "ledger.jsonl")) == before
    desk.mark_position(token, "AAPL", "8.00")
    desk.settle_flat(token, confirmation="SETTLE PAPER POSITIONS AT MARKS")
    assert desk.performance(token)["historical_realized_pnl"] == "-2.00"
    pending = desk.place_order(token, symbol="AAPL", side="buy", qty="1", limit_price="10.00", idempotency_key="pending-buy-01")
    with pytest.raises(ResetBlocked):
        reset(desk, token, key="reset-pending-01")
    desk.cancel_order(token, pending["order_id"], confirmation="CANCEL PENDING PAPER ORDERS")
    result = reset(desk, token, key="reset-after-flat-01")
    assert result["new_epoch_session_pnl"] == "0.00"
    assert desk.performance(token)["historical_realized_pnl"] == "-2.00"
    assert hash_tree(os.path.join(desk.store.root, "tenants", tenant, "brain")) == brain
