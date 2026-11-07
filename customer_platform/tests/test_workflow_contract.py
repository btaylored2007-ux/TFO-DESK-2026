"""Labelled synthetic workflow. Not a live provider and not the real course."""

import json
import os
import subprocess
import sys
import textwrap

from customer_platform.files import release_file_lock, try_file_lock
from customer_platform.http_app import bind
from customer_platform.platform import Platform

SECRET = b"test-webhook-secret"


def test_recheck_and_escalate_are_aliases(tmp_path):
    desk = Platform(tmp_path / "customers", enabled=True, webhook_secret=SECRET)
    created = desk.provision_tenant("ada@example.com", "correct-horse-1", "Ada", starting_capital="1000.00")
    token = desk.login("ada@example.com", "correct-horse-1")
    desk.start_trading(token)
    desk.create_operator("op@example.com", "correct-horse-9", "Operator")
    operator = desk.operator_login("op@example.com", "correct-horse-9")
    incident = desk.operator_incidents(operator)["incidents"][0]["id"]
    assert desk.operator_incident_action(operator, incident, "recheck")["verification"]
    closed = desk.operator_incident_action(operator, incident, "escalate", learning="still off")
    assert closed["status"] == "escalated"
    assert created["tenant_id"]


def test_two_processes_take_one_writer(tmp_path):
    desk = Platform(tmp_path / "store", enabled=True, webhook_secret=SECRET)
    created = desk.provision_tenant("ada@example.com", "correct-horse-1", "Ada", starting_capital="10000.00")
    token = desk.login("ada@example.com", "correct-horse-1")
    tenant = created["tenant_id"]
    desk.arm_synthetic_replay(token)
    desk.set_replay_print(token, "100.00")
    desk.start_trading(token)
    opened = desk.place_order(
        token, symbol="AAPL", side="buy", qty="1", limit_price="100.00", stop="90.00", idempotency_key="proc-buy",
    )
    desk.paper_tick_tenant(tenant)
    assert opened["decision"] == "customer_order"
    position = next(row for row in desk._book(tenant).load()["positions"] if row["status"] == "open")
    assert position["decision"] == "customer_order" and position["decision_order_id"] == opened["order_id"]
    desk.stop_trading(token)
    desk.set_replay_print(token, "80.00")
    lock = try_file_lock(os.path.join(desk._tenant_dir(tenant), "paper", "engine.lock"))
    assert lock is not None
    busy = _tick_process(tmp_path / "store", tenant)
    assert busy["reason"] == "writer busy" and busy["filled"] == 0
    release_file_lock(lock)
    first = _tick_process(tmp_path / "store", tenant)
    second = _tick_process(tmp_path / "store", tenant)
    assert first["managed"] is True and second["filled"] == 0
    exits = [line for line in desk._book(tenant).ledger() if line.get("decision") == "stop_exit"]
    assert len(exits) == 1 and exits[0]["price"] == "80.00"


def test_active_rule_places_one_synthetic_order_and_fixture_school_grades(tmp_path):
    desk = Platform(tmp_path / "customers", enabled=True, signup_enabled=True, webhook_secret=SECRET)
    httpd = bind(desk)
    cookies = {}

    def request(method, path, form=None):
        import http.client
        from urllib.parse import urlencode
        connection = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        headers = {}
        if cookies:
            headers["Cookie"] = "; ".join("{0}={1}".format(key, value) for key, value in cookies.items())
        body = None
        if form is not None:
            body = urlencode(form)
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read().decode()
        for header in response.headers.get_all("Set-Cookie") or []:
            pair = header.split(";", 1)[0]
            key, value = pair.split("=", 1)
            cookies[key] = value
        connection.close()
        return response.status, payload

    try:
        request("GET", "/")
        request("POST", "/signup", {
            "csrf": cookies["cp_csrf"], "email": "ada@example.com", "password": "correct-horse-1",
            "display_name": "Ada", "starting_capital": "10000.00", "currency": "USD",
        })
        status, closed = request("POST", "/school/begin", {"csrf": cookies["cp_csrf"], "module_id": "tts.m01"})
        assert status == 400 and "correct_option" not in closed
        token = desk.login("ada@example.com", "correct-horse-1")
        desk.authorize_synthetic_school(token, _fixture_course())
        status, quiz = request("POST", "/school/begin", {"csrf": cookies["cp_csrf"], "module_id": "tts.m01"})
        assert status == 200 and "correct_option" not in quiz and "Synthetic?" in quiz
        attempt = quiz.split("attempt_id\" value=\"", 1)[1].split("\"", 1)[0]
        status, graded = request("POST", "/school/grade", {
            "csrf": cookies["cp_csrf"], "attempt_id": attempt, "answer-tts.m01.q01": "a",
        })
        assert status == 200 and "True" in graded
        status, practical = request("POST", "/school/practical", {
            "csrf": cookies["cp_csrf"], "module_id": "tts.m01", "text": "Synthetic practical note.",
        })
        assert status == 200 and "False" in practical
        capstone = {"csrf": cookies["cp_csrf"]}
        capstone.update({"c{0}".format(index): "2" for index in range(10)})
        status, cap = request("POST", "/school/capstone", capstone)
        assert status == 200 and "practice evidence" in cap
        before = open(
            os.path.join(os.path.dirname(__file__), "..", "school_content", "draft", "course.json"), "rb",
        ).read()
        assert __import__("hashlib").sha256(before).hexdigest() == "47710df87eba1bac393bb7bbc85d129add2424fd6b343a7c20e7498585a0f702"
        saved = desk.teach_save(token, trader_ids=["desk"], text=(
            "Entry: buy AAPL\nExit: flat\nRisk: stop 5 percent below entry\nMarket: shares\n"
        ))
        tested = desk.teach_run_test(token, saved["id"])
        desk.teach_status(token, tested["id"], "ready_for_review")
        desk.teach_status(token, tested["id"], "active")
        request("POST", "/desk/replay", {"csrf": cookies["cp_csrf"]})
        request("POST", "/desk/start", {"csrf": cookies["cp_csrf"]})
        status, produced = request("POST", "/desk/replay/print", {"csrf": cookies["cp_csrf"], "price": "100.00"})
        assert status == 200 and "not AI trading" in produced
        orders = desk._book(desk.store.context(token)["tenant_id"]).load()["orders"]
        strategy = [row for row in orders if row.get("decision") == "strategy_rule"]
        assert len(strategy) == 1 and strategy[0]["strategy_id"] == saved["id"] and strategy[0]["strategy_version"] != "unknown"
        request("POST", "/desk/replay/print", {"csrf": cookies["cp_csrf"], "price": "100.00"})
        orders = desk._book(desk.store.context(token)["tenant_id"]).load()["orders"]
        assert len([row for row in orders if row.get("decision") == "strategy_rule"]) == 1
    finally:
        httpd.paper_stop.set()
        desk._service_started = False
        httpd.shutdown()
        httpd.server_close()


def _tick_process(root, tenant):
    script = textwrap.dedent("""
        import json, sys
        from customer_platform.platform import Platform
        desk = Platform(sys.argv[1], enabled=True, webhook_secret=b"test-webhook-secret")
        print(json.dumps(desk.paper_tick_tenant(sys.argv[2])))
    """)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.getcwd()
    completed = subprocess.run(
        [sys.executable, "-c", script, str(root), tenant],
        check=True, capture_output=True, text=True, env=env,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _fixture_course():
    questions = [{
        "id": "tts.m01.q01",
        "pool": "A",
        "stem": "Synthetic?",
        "correct_option": "a",
        "options": [
            {"id": "a", "text": "Yes", "explanation": "hidden"},
            {"id": "b", "text": "No", "explanation": "hidden"},
        ],
    }]
    return {
        "brand": "Synthetic fixture",
        "capstone": {"criteria": [{"id": "c{0}".format(index)} for index in range(10)]},
        "label": "SYNTHETIC FIXTURE",
        "modules": [{
            "id": "tts.m01",
            "lessons": [{"id": "tts.m01.l1", "title": "One", "body": "Synthetic lesson.", "layer": "core"}],
            "number": 1,
            "practical_required": False,
            "questions": questions,
            "title": "Synthetic module",
        }],
        "schema": "tts-course",
        "version": "synthetic-fixture",
    }
