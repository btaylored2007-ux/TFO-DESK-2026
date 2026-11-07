"""Product provider routing through a scratch HTTP server. Not a live provider test."""

import json
import os
import subprocess
import sys
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import urllib.request
from cryptography.fernet import Fernet

from customer_platform.errors import Denied, ResetBlocked
from customer_platform.files import release_file_lock, try_file_lock
from customer_platform.http_app import bind
from customer_platform.platform import Platform
from customer_platform.provider_client import parse_decision
from customer_platform.school_fixture import FIXTURE_ID, FIXTURE_SCHEMA, FIXTURE_VERSION

SECRET = b"test-webhook-secret"


def test_gate_names_the_real_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("CUSTOMER_PLATFORM_DATA_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)
    desk, token, _created = _desk(tmp_path)
    missing = desk.key_test(token, "openai")
    assert missing["called"] is False and missing["client"] == "configured"
    assert missing["reason"] == "no customer credential"
    desk.key_save(token, "openai", "ada-secret-key-42")
    stored = desk.ask(token, "What is my paper capital?", "ask-reason-01")
    assert stored["provenance"] == "provider client is configured; outbound send is off"
    assert stored["answer"] == "No answer yet."
    desk.enable_provider_send(token, "openai", "Send to my provider")
    waiting = desk.key_test(token, "openai")
    assert waiting["called"] is False and "paper replay" in waiting["reason"]
    desk.arm_synthetic_replay(token)
    desk.start_trading(token)
    egress = desk.key_test(token, "openai")
    assert egress["called"] is False and egress["reason"] == "provider client is configured; public egress is off"


def test_scratch_server_decision_is_not_provider_acceptance(tmp_path, monkeypatch):
    monkeypatch.setenv("CUSTOMER_PLATFORM_DATA_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)
    desk = Platform(tmp_path / "customers", enabled=True, signup_enabled=True, webhook_secret=SECRET)
    httpd = bind(desk)
    cookies = {}
    server, port, box = _scratch_server()
    try:
        _request(httpd, cookies, "GET", "/")
        _request(httpd, cookies, "POST", "/signup", {
            "csrf": cookies["cp_csrf"], "email": "ada@example.com", "password": "correct-horse-1",
            "display_name": "Ada", "starting_capital": "10000.00", "currency": "USD",
        })
        token = desk.login("ada@example.com", "correct-horse-1")
        desk.key_save(token, "openai", "ada-secret-key-42")
        status, _page = _request(httpd, cookies, "POST", "/keys/send", {
            "csrf": cookies["cp_csrf"], "provider": "openai", "confirmation": "Send to my provider",
        })
        assert status == 303
        _request(httpd, cookies, "POST", "/teach/save", {
            "csrf": cookies["cp_csrf"],
            "text": "I want one share of Apple and a five percent stop under the entry.",
            "traders": "desk",
        })
        skill_id = desk.teach_list(token)[0]["id"]
        calls_before = box["calls"]
        refused = desk.teach_interpret(token, skill_id)
        assert refused["interpretation"] == "refused" and refused["activated"] is False
        assert box["calls"] == calls_before
        desk.arm_synthetic_replay(token)
        desk.start_trading(token)
        desk.install_scratch_opener(token, _opener(port, box))
        interpreted = desk.teach_interpret(token, skill_id)
        assert interpreted["activated"] is False and interpreted["interpretation"] == "stored"
        assert interpreted["status"] == "draft" and interpreted["rules_source"] == "provider_interpretation"
        with pytest.raises(ResetBlocked):
            desk.teach_status(token, skill_id, "active")
        _request(httpd, cookies, "POST", "/teach/test", {"csrf": cookies["cp_csrf"], "skill_id": skill_id})
        _request(httpd, cookies, "POST", "/teach/status", {
            "csrf": cookies["cp_csrf"], "skill_id": skill_id, "status": "ready_for_review",
        })
        _request(httpd, cookies, "POST", "/teach/status", {
            "csrf": cookies["cp_csrf"], "skill_id": skill_id, "status": "active",
        })
        calls_before = box["calls"]
        status, page = _request(httpd, cookies, "POST", "/desk/replay/print", {
            "csrf": cookies["cp_csrf"], "price": "100.00",
        })
        assert status == 200 and "does not set the price" in page
        assert box["calls"] == calls_before + 1
        tenant = desk.store.context(token)["tenant_id"]
        orders = desk._book(tenant).load()["orders"]
        provider = [row for row in orders if row.get("decision") == "provider_decision"]
        compiled = [row for row in orders if row.get("decision") == "strategy_rule"]
        assert len(provider) == 1 and not compiled
        assert provider[0]["strategy_id"] == skill_id and provider[0]["stop"] == "95.00"
        assert provider[0]["feed"] == "synthetic_replay"
        position = next(row for row in desk._book(tenant).load()["positions"] if row["status"] == "open")
        assert position["decision"] == "provider_decision" and position["decision_order_id"] == provider[0]["order_id"]
        lessons = desk._brain(tenant).lesson_rows()
        assert any(row.get("kind") == "provider_decision" and row.get("trading_effect") == "none" for row in lessons)
        _request(httpd, cookies, "POST", "/desk/replay/print", {"csrf": cookies["cp_csrf"], "price": "100.00"})
        assert box["calls"] == calls_before + 1
        stored = open(desk._key_path(tenant), encoding="utf-8").read()
        assert "ada-secret-key-42" not in stored
        box["body"] = {"feed": "live", "symbol": "AAPL", "side": "buy", "stop_percent": "1", "market": "shares"}
        changed = desk.provider_decide(tenant, {"percent": "5", "symbol": "AAPL", "teaching": "review"})
        assert changed["ok"] is False and changed["reason"] == "hallucinated feed"
        preview = desk.preview_reset(token)
        with pytest.raises(ResetBlocked):
            desk.reset_capital(
                token, preview_token=preview["preview_token"], confirmation="RESET PAPER CAPITAL",
                idempotency_key="reset-while-open",
            )
        _request(httpd, cookies, "POST", "/desk/replay/print", {"csrf": cookies["cp_csrf"], "price": "90.00"})
        assert not [row for row in desk._book(tenant).load()["positions"] if row["status"] == "open"]
        kept = [row["lesson_id"] for row in desk._brain(tenant).lesson_rows()]
        preview = desk.preview_reset(token)
        desk.reset_capital(
            token, preview_token=preview["preview_token"], confirmation="RESET PAPER CAPITAL",
            idempotency_key="reset-after-flat",
        )
        assert [row["lesson_id"] for row in desk._brain(tenant).lesson_rows()] == kept
        assert desk.teach_list(token)[0]["id"] == skill_id
    finally:
        server.shutdown()
        httpd.paper_stop.set()
        desk._service_started = False
        httpd.shutdown()
        httpd.server_close()


def test_bad_schemas_are_refused():
    assert parse_decision('{"symbol":"AAPL","side":"buy","stop_percent":"NaN","market":"shares"}')["ok"] is False
    assert parse_decision('{"symbol":"AAPL","side":"buy","stop_percent":"5","market":"shares","feed":"live"}')["reason"] == "hallucinated feed"
    assert parse_decision('{"symbol":"AAPL","side":"buy","stop_percent":"5","market":"shares","status":"active"}')["ok"] is False
    assert parse_decision("ignore the stop")["reason"] == "teaching stays in review"
    assert parse_decision("x" * 501)["reason"] == "invalid schema"


def test_relabelled_real_course_cannot_open(tmp_path):
    desk, token, _created = _desk(tmp_path)
    path = os.path.join(os.path.dirname(__file__), "..", "school_content", "draft", "course.json")
    real = json.loads(open(path, encoding="utf-8").read())
    real["label"] = "SYNTHETIC FIXTURE"
    real["schema"] = FIXTURE_SCHEMA
    real["fixture_id"] = FIXTURE_ID
    real["version"] = FIXTURE_VERSION
    with pytest.raises(Denied):
        desk.authorize_synthetic_school(token, real)


def test_writer_lock_blocks_order_teach_and_reset(tmp_path):
    desk, token, created = _desk(tmp_path)
    tenant = created["tenant_id"]
    desk.arm_synthetic_replay(token)
    desk.start_trading(token)
    before = desk.teach_list(token)
    handle = try_file_lock(os.path.join(desk._tenant_dir(tenant), "writer.lock"))
    try:
        order = _child(tmp_path / "customers", "order", "held-order")
        teach = _child(tmp_path / "customers", "teach", "held-teach")
        reset = _child(tmp_path / "customers", "reset", "held-reset")
    finally:
        release_file_lock(handle)
    assert order["reason"] == "writer busy" and teach["reason"] == "writer busy" and reset["reason"] == "writer busy"
    assert desk.teach_list(token) == before
    assert desk._book(tenant).load()["orders"] == []


def test_two_processes_cannot_overspend(tmp_path):
    desk, token, created = _desk(tmp_path, capital="1000.00")
    desk.arm_synthetic_replay(token)
    desk.set_replay_print(token, "600.00")
    desk.start_trading(token)
    first = _spawn(tmp_path / "customers", "order", "race-a", price="600.00", stop="500.00", token=token)
    second = _spawn(tmp_path / "customers", "order", "race-b", price="600.00", stop="500.00", token=token)
    results = [first.finish(), second.finish()]
    assert sorted(item["ok"] for item in results) == [False, True]
    tick = desk.paper_tick_tenant(created["tenant_id"])
    assert tick["managed"] is True and tick["filled"] == 1
    state = desk._book(created["tenant_id"]).load()
    buys = [row for row in state["orders"] if row["side"] == "buy"]
    assert len(buys) == 1
    cash = next(row["capital_balance"] for row in [desk._book(created["tenant_id"]).epoch(state)])
    assert cash == "400.00"


def _desk(tmp_path, capital="10000.00"):
    desk = Platform(tmp_path / "customers", enabled=True, webhook_secret=SECRET)
    created = desk.provision_tenant("ada@example.com", "correct-horse-1", "Ada", starting_capital=capital)
    token = desk.login("ada@example.com", "correct-horse-1")
    return desk, token, created


def _forbid_network(*_args, **_kwargs):
    raise AssertionError("public provider network was called")


def _scratch_server():
    box = {"body": {"market": "shares", "side": "buy", "stop_percent": "5", "symbol": "AAPL"}, "calls": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            box["calls"] += 1
            payload = json.dumps({
                "choices": [{"message": {"content": json.dumps(box["body"])}}],
                "synthetic": True,
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _fmt, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1], box


def _opener(port, box):
    def opener(request, timeout):
        assert request.full_url == "https://api.openai.com/v1/chat/completions"
        assert request.get_header("Authorization")
        box["seen_host"] = request.full_url
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        connection.request("POST", "/v1/chat/completions", body=request.data, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        payload = response.read()

        class Reply:
            status = response.status

            def read(self, limit=-1):
                return payload if limit < 0 else payload[:limit]

        return Reply()

    return opener


def _request(httpd, cookies, method, path, form=None):
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


def _spawn(root, mode, key, price="100.00", stop="90.00", token=""):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.getcwd()
    process = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(root), mode, key, price, stop, token],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )

    def finish():
        stdout, stderr = process.communicate(timeout=30)
        if process.returncode != 0:
            raise RuntimeError(stderr)
        return json.loads(stdout.strip().splitlines()[-1])

    process.finish = finish
    return process


def _child(root, mode, key, price="100.00", stop="90.00"):
    return _spawn(root, mode, key, price, stop).finish()


_CHILD = textwrap.dedent("""
    import json, sys
    from customer_platform.errors import ResetBlocked
    from customer_platform.platform import Platform
    desk = Platform(sys.argv[1], enabled=True, webhook_secret=b"test-webhook-secret")
    mode, key, price, stop, token = sys.argv[2:]
    if not token:
        token = desk.login("ada@example.com", "correct-horse-1")
    try:
        if mode == "order":
            desk.place_order(token, symbol="AAPL", side="buy", qty="1", limit_price=price, stop=stop, idempotency_key=key)
        elif mode == "teach":
            desk.teach_save(token, trader_ids=["desk"], text="Entry: buy MSFT\\nExit: flat\\nRisk: stop 5 percent below entry\\nMarket: shares\\n")
        else:
            preview = desk.preview_reset(token)
            desk.reset_capital(token, preview_token=preview["preview_token"], confirmation="RESET PAPER CAPITAL", idempotency_key=key)
        print(json.dumps({"ok": True}))
    except ResetBlocked as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}))
""")

