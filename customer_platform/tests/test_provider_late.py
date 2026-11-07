"""A late provider reply must not open a position after Stop. Scratch HTTP only."""

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

from customer_platform.errors import Denied
from customer_platform.http_app import bind
from customer_platform.platform import Platform
from customer_platform.provider_client import RefuseRedirect

SECRET = b"test-webhook-secret"


def test_stop_during_provider_wait_cancels_the_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("CUSTOMER_PLATFORM_DATA_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)
    desk, token, tenant = _ready(tmp_path)
    opened = desk.place_order(
        token, symbol="MSFT", side="buy", qty="1", limit_price="100.00", stop="90.00", idempotency_key="msft-open",
    )
    desk.fill_order(token, opened["order_id"], price="100.00")
    cookies = {}
    release = threading.Event()
    entered = threading.Event()

    def opener(request, timeout):
        assert request.full_url == "https://api.openai.com/v1/chat/completions"
        entered.set()
        assert release.wait(15)
        payload = json.dumps({
            "choices": [{"message": {"content": json.dumps({
                "market": "shares", "side": "buy", "stop_percent": "5", "symbol": "AAPL",
            })}}],
            "synthetic": True,
        }).encode("utf-8")

        class Reply:
            status = 200

            def read(self, limit=-1):
                return payload if limit < 0 else payload[:limit]

        return Reply()

    desk.install_scratch_opener(token, opener)
    httpd = bind(desk)
    assert entered.wait(5)
    try:
        _login(httpd, cookies)
        status, _page = _request(httpd, cookies, "POST", "/desk/stop", {"csrf": cookies["cp_csrf"]})
        assert status == 200
        status, _page = _request(httpd, cookies, "POST", "/desk/trader", {
            "csrf": cookies["cp_csrf"], "trader_id": "desk", "enabled": "no",
        })
        assert status == 200
        status, _page = _request(httpd, cookies, "POST", "/desk/replay/print", {
            "csrf": cookies["cp_csrf"], "price": "80.00",
        })
        assert status == 200
        status, uploaded = _request(httpd, cookies, "POST", "/teach/upload", {
            "csrf": cookies["cp_csrf"], "filename": "notes.txt", "traders": "desk",
            "file": "Entry: buy MSFT\nExit: flat\nRisk: stop 5 percent below entry\nMarket: shares\n",
        })
        assert status in {200, 303} and "Traceback" not in uploaded
        other = _http_stop_process(httpd.server_address[1], cookies)
        assert other["status"] == 200
        status, _page = _request(httpd, cookies, "POST", "/desk/funds", {
            "csrf": cookies["cp_csrf"], "amount": "1.00", "acknowledge": "yes",
        })
        assert status == 303
    finally:
        release.set()
        for _ in range(30):
            if any(row.get("status") in {"cancelled", "skipped", "committed"} for row in desk.decision_audit(tenant)):
                break
            threading.Event().wait(0.1)
        httpd.paper_stop.set()
        desk._service_started = False
        httpd.shutdown()
        httpd.server_close()
    orders = desk._book(tenant).load()["orders"]
    assert not [row for row in orders if row.get("decision") == "provider_decision"]
    assert not [row for row in desk._book(tenant).load()["positions"] if row["status"] == "open" and row["symbol"] == "AAPL"]
    msft = [row for row in desk._book(tenant).load()["positions"] if row["symbol"] == "MSFT"]
    assert msft and msft[-1]["status"] == "closed"
    audit = desk.decision_audit(tenant)
    assert any(row.get("status") == "cancelled" for row in audit)
    assert not any(row.get("limit_price") == "100.00" and row.get("decision") == "provider_decision" for row in orders)
    kept = list(audit)
    preview = desk.preview_reset(token)
    desk.reset_capital(
        token, preview_token=preview["preview_token"], confirmation="RESET PAPER CAPITAL",
        idempotency_key="reset-after-cancel",
    )
    assert desk.decision_audit(tenant) == kept
    assert os.environ.get("CUSTOMER_PLATFORM_PROVIDER_EGRESS") != "allow"


def test_redirects_are_not_followed(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)
    hits = {"steal": 0, "auth": []}

    class Steal(BaseHTTPRequestHandler):
        def do_POST(self):
            hits["steal"] += 1
            hits["auth"].append(self.headers.get("Authorization") or self.headers.get("X-Api-Key") or "")
            self.send_response(200)
            self.end_headers()

        def log_message(self, _fmt, *_args):
            return

    steal = ThreadingHTTPServer(("127.0.0.1", 0), Steal)
    threading.Thread(target=steal.serve_forever, daemon=True).start()
    try:
        for code in (303, 307, 308):
            _assert_redirect_refused(code, steal.server_address[1], hits)
        assert hits["steal"] == 0
        assert all(item == "" or "ada-secret-key-42" not in item for item in hits["auth"])
    finally:
        steal.shutdown()
    request = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=b"{}", method="POST")
    from customer_platform.provider_client import product_urlopen
    with pytest.raises(Denied) as caught:
        product_urlopen(request, 1)
    assert "public egress is off" in str(caught.value)


def _assert_redirect_refused(code, steal_port, hits):
    class First(BaseHTTPRequestHandler):
        def do_POST(self):
            hits["first"] = hits.get("first", 0) + 1
            self.send_response(code)
            self.send_header("Location", "http://127.0.0.1:{0}/steal".format(steal_port))
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, _fmt, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), First)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        opener = urllib.request.build_opener(RefuseRedirect)
        request = urllib.request.Request(
            "http://127.0.0.1:{0}/v1/chat/completions".format(server.server_address[1]),
            data=b"{}",
            headers={"Authorization": "Bearer ada-secret-key-42"},
            method="POST",
        )
        with pytest.raises(Denied) as caught:
            opener.open(request, timeout=2)
        assert "redirect" in str(caught.value)
    finally:
        server.shutdown()


def _ready(tmp_path):
    desk = Platform(tmp_path / "customers", enabled=True, webhook_secret=SECRET)
    created = desk.provision_tenant("ada@example.com", "correct-horse-1", "Ada", starting_capital="10000.00")
    token = desk.login("ada@example.com", "correct-horse-1")
    desk.key_save(token, "openai", "ada-secret-key-42")
    desk.enable_provider_send(token, "openai", "Send to my provider")
    saved = desk.teach_save(token, trader_ids=["desk"], text="I want one share of Apple and a five percent stop under the entry.")
    desk.arm_synthetic_replay(token)
    desk.set_replay_print(token, "100.00")
    desk.start_trading(token)

    def opener(request, timeout):
        payload = json.dumps({
            "choices": [{"message": {"content": json.dumps({
                "market": "shares", "side": "buy", "stop_percent": "5", "symbol": "AAPL",
            })}}],
            "synthetic": True,
        }).encode("utf-8")

        class Reply:
            status = 200

            def read(self, limit=-1):
                return payload if limit < 0 else payload[:limit]

        return Reply()

    desk.install_scratch_opener(token, opener)
    interpreted = desk.teach_interpret(token, saved["id"])
    assert interpreted["ai"] == "test fixture" and interpreted["activated"] is False
    desk.teach_run_test(token, saved["id"])
    desk.teach_status(token, saved["id"], "ready_for_review")
    desk.teach_status(token, saved["id"], "active")
    return desk, token, created["tenant_id"]


def _forbid_network(*_args, **_kwargs):
    raise AssertionError("public provider network was called")


def _login(httpd, cookies):
    _request(httpd, cookies, "GET", "/")
    _request(httpd, cookies, "POST", "/login", {
        "csrf": cookies["cp_csrf"], "email": "ada@example.com", "password": "correct-horse-1",
    })


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


def _http_stop_process(port, cookies):
    script = textwrap.dedent("""
        import http.client, json, sys
        from urllib.parse import urlencode
        port, cookie, csrf = sys.argv[1:]
        connection = http.client.HTTPConnection("127.0.0.1", int(port), timeout=10)
        body = urlencode({"csrf": csrf})
        connection.request("POST", "/desk/stop", body=body, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": cookie,
        })
        response = connection.getresponse()
        response.read()
        print(json.dumps({"status": response.status}))
    """)
    cookie = "; ".join("{0}={1}".format(key, value) for key, value in cookies.items())
    env = dict(os.environ)
    env["PYTHONPATH"] = os.getcwd()
    completed = subprocess.run(
        [sys.executable, "-c", script, str(port), cookie, cookies["cp_csrf"]],
        check=True, capture_output=True, text=True, env=env,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])
