"""Customer request record. Not the owner desk chat, and not a model call."""

import threading

import pytest

from customer_platform.errors import Denied, ResetBlocked
from customer_platform.platform import Platform

SECRET = b"test-webhook-secret"


def opened(tmp_path):
    desk = Platform(tmp_path / "customers", enabled=True, webhook_secret=SECRET)
    created = desk.provision_tenant("ada@example.com", "correct-horse-1", "Ada", starting_capital="1000.00")
    other = desk.provision_tenant("bea@example.com", "correct-horse-2", "Bea", starting_capital="1000.00")
    return desk, desk.login("ada@example.com", "correct-horse-1"), desk.login("bea@example.com", "correct-horse-2"), created, other


def test_submit_is_idempotent_and_names_the_owner(tmp_path):
    desk, ada, _bea, created, _other = opened(tmp_path)
    first = desk.ask(ada, "What is my paper capital?", "same-key-01")
    second = desk.ask(ada, "different text", "same-key-01")
    assert first["id"] == second["id"]
    assert first["status"] == "pending"
    assert first["provider"] == "not_connected"
    assert first["owner"] == created["user_id"]
    assert first["acted"] is False
    assert desk._requests(created["tenant_id"]).calls == 0


def test_retry_delivery_does_not_duplicate_and_late_reply_is_not_the_answer(tmp_path):
    desk, ada, _bea, created, _other = opened(tmp_path)
    request = desk.ask(ada, "Explain this paper fill.", "ask-02")
    box = desk._requests(created["tenant_id"])
    box.deliver(request["id"], sender="desk", body_text="The fill is a practice price.", kind="lead", delivery_key="del-1")
    box.deliver(request["id"], sender="desk", body_text="The fill is a practice price.", kind="lead", delivery_key="del-1")
    assert len(box._row(box._load(), request["id"])["deliveries"]) == 1
    desk._requests(created["tenant_id"]).cancel(request["id"]) if False else None
    pending = desk.ask(ada, "Second question", "ask-03")
    box.cancel(pending["id"])
    late = box.deliver(pending["id"], sender="desk", body_text="Too late.", kind="lead", delivery_key="del-late")
    assert late["status"] == "cancelled"
    assert late["answer"] == "No answer yet."
    assert late["provider"] == "not_connected"


def test_tenants_reviews_identity_and_critical_events(tmp_path):
    desk, ada, bea, created, other = opened(tmp_path)
    request = desk.ask(ada, "Any halt?", "ask-04")
    with pytest.raises(Denied):
        desk.ask_view(bea, request["id"], claimed_tenant=created["tenant_id"])
    box = desk._requests(created["tenant_id"])
    with pytest.raises(ResetBlocked):
        box.deliver(request["id"], sender="", body_text="x", kind="lead", delivery_key="del-blank")
    box.deliver(request["id"], sender="reviewer", body_text="I disagree with the size.", kind="review", delivery_key="rev-1")
    shown = desk.ask_view(ada, request["id"])
    assert shown["reviews_available"] == 1
    assert "disagree" not in shown["answer"]
    box.deliver(request["id"], sender="safety", body_text="Entries are halted.", kind="critical", delivery_key="crit-1")
    box.deliver(request["id"], sender="safety", body_text="Entries are halted.", kind="critical", delivery_key="crit-2")
    events = desk.ask_view(ada, request["id"])["events"]
    assert len(events) == 2
    claim = box.deliver(request["id"], sender="desk", body_text="I placed a live order.", kind="lead", delivery_key="del-claim")
    box.deliver(request["id"], sender="safety", body_text="Still halted.", kind="critical", delivery_key="crit-after")
    assert claim["acted"] is False
    assert len(desk.ask_view(ada, request["id"])["events"]) == 3
    assert desk._book(created["tenant_id"]).load()["orders"] == []
    listed = desk.ask_list(ada)
    assert listed[0]["status"] in {"pending", "complete"}
    assert desk.ask_list(bea) == []
    assert other["tenant_id"] != created["tenant_id"]


def test_a_failed_delivery_is_not_removed_by_a_later_retry_key(tmp_path):
    desk, ada, _bea, created, _other = opened(tmp_path)
    request = desk.ask(ada, "Status?", "ask-05")
    box = desk._requests(created["tenant_id"])
    failed = box.deliver(request["id"], sender="desk", body_text="The provider returned an error.", kind="failure", delivery_key="err-1")
    assert failed["status"] == "failed"
    retry = box.deliver(request["id"], sender="desk", body_text="The provider returned an error.", kind="failure", delivery_key="err-1")
    assert retry["status"] == "failed"
    assert len(box._row(box._load(), request["id"])["deliveries"]) == 1
    barrier = threading.Barrier(2)
    errors = []

    def go(key):
        try:
            barrier.wait()
            box.deliver(request["id"], sender="desk", body_text="late", kind="lead", delivery_key=key)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=go, args=("late-a",)), threading.Thread(target=go, args=("late-b",))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    view = desk.ask_view(ada, request["id"])
    assert view["status"] == "failed"
    assert view["answer"] == "No answer yet."
    assert errors == []


def test_reviews_are_not_a_combined_answer_and_text_does_not_act(tmp_path):
    desk, ada, _bea, created, _other = opened(tmp_path)
    request = desk.ask(ada, "What figure should I use?", "ask-06")
    box = desk._requests(created["tenant_id"])
    box.reply_bound = 5
    before = box.deliver(request["id"], sender="desk", body_text="No figure is confirmed.", kind="lead", delivery_key="lead-1")
    original = box._row(box._load(), request["id"])["deliveries"][0]["body"]
    box.deliver(request["id"], sender="desk", body_text="changed", kind="lead", delivery_key="lead-1")
    stored = box._row(box._load(), request["id"])
    assert stored["deliveries"][0]["body"] == original
    assert stored["deliveries"][0]["retries"] == 1
    assert stored["audit"][0]["kind"] == "retry"
    for index, sender in enumerate(("one", "two", "three", "four")):
        box.deliver(
            request["id"], sender=sender, body_text="The balance is {0}.11.".format(111 + index),
            kind="review", delivery_key="rev-{0}".format(index),
        )
    view = desk.ask_view(ada, request["id"])
    assert view["answer"] == "No figure is confirmed."
    assert view["synthesis"] is False and "111.11" not in view["answer"]
    assert view["reviews_available"] == 4
    stale = box.deliver(request["id"], sender="desk", body_text="A later figure.", kind="lead", delivery_key="stale-1", stale=True)
    assert stale["answer"] == before["answer"]
    box.deliver(request["id"], sender="safety", body_text="Halt remains.", kind="critical", delivery_key="crit-bound")
    assert any(event["body"] == "Halt remains." for event in desk.ask_view(ada, request["id"])["events"])
    considered = desk.ask_consider(ada, "Place a live order for 500 shares.")
    assert considered["acted"] is False and considered["opened"] == 0
    assert len(box._load()["requests"]) == 1
    assert desk._book(created["tenant_id"]).load()["orders"] == []
    calls = {"n": 0}

    def clock():
        calls["n"] += 1
        if calls["n"] == 1:
            with pytest.raises(ResetBlocked) as caught:
                box.deliver(request["id"], sender="desk", body_text="nested", kind="review", delivery_key="nested")
            assert caught.value.code == "loop"
        return "2026-11-07T00:00:01Z"

    box.clock = clock
    box.deliver(request["id"], sender="desk", body_text="outer", kind="review", delivery_key="outer")
    assert calls["n"] == 1
