"""HTTP proof for operator actions, stop management, notes, and provenance."""

import http.client
import os
from urllib.parse import urlencode

from cryptography.fernet import Fernet

from customer_platform.errors import Denied, ResetBlocked
from customer_platform.http_app import bind
from customer_platform.operator_init import initialize_data_key
from customer_platform.platform import Platform


def opened(tmp_path):
    desk = Platform(tmp_path / "customers", enabled=True, signup_enabled=True, webhook_secret=b"test-webhook-secret")
    httpd = bind(desk)
    cookies = {}

    def request(method, path, form=None):
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

    return desk, httpd, request, cookies


def test_stop_is_managed_after_entries_pause_and_operator_actions_are_separate(tmp_path):
    desk, httpd, request, cookies = opened(tmp_path)
    try:
        status, home = request("GET", "/")
        assert "Transparent Trading" in home and "The Transparent Trading School" not in home
        assert "Create practice account" in home and "loopback" not in home
        status, _ada = request("POST", "/signup", {
            "csrf": cookies["cp_csrf"], "email": "ada@example.com", "password": "correct-horse-1",
            "display_name": "Ada", "starting_capital": "10000.00", "currency": "USD",
        })
        ada = dict(cookies)
        request("POST", "/desk/start", {"csrf": cookies["cp_csrf"]})
        request("POST", "/desk/replay", {"csrf": cookies["cp_csrf"]})
        request("POST", "/desk/replay/print", {"csrf": cookies["cp_csrf"], "price": "100.00"})
        request("POST", "/desk/start", {"csrf": cookies["cp_csrf"]})
        status, ordered = request("POST", "/desk/order", {
            "csrf": cookies["cp_csrf"], "symbol": "AAPL", "side": "buy", "qty": "1",
            "limit_price": "100.00", "stop": "90.00",
        })
        assert status == 200
        status, bad = request("POST", "/desk/stop-distance", {"csrf": cookies["cp_csrf"], "distance": "nan"})
        assert status == 400 and "Traceback" not in bad
        request("POST", "/desk/stop", {"csrf": cookies["cp_csrf"]})
        status, crossed = request("POST", "/desk/replay/print", {"csrf": cookies["cp_csrf"], "price": "80.00"})
        assert status == 200 and "No open position" in crossed
        tenant = next(name for name in os.listdir(os.path.join(desk.store.root, "tenants")) if not name.startswith("."))
        book = desk._book(tenant).load()
        position = book["positions"][0]
        assert position["status"] != "open"
        assert position["trader_id"] == "desk" and position["account_id"] == tenant
        fills = [line for line in desk._book(tenant).ledger() if line.get("kind") == "fill" and line.get("decision") == "stop_exit"]
        assert fills and fills[-1]["price"] == "80.00" and fills[-1]["feed"] == "synthetic_replay"
        status, forged = request("POST", "/desk/order", {
            "csrf": cookies["cp_csrf"], "symbol": "AAPL", "side": "buy", "qty": "1",
            "limit_price": "100.00", "stop": "90.00", "account_id": "other-tenant",
        })
        assert status == 403
        status, idea = request("POST", "/brain/idea", {"csrf": cookies["cp_csrf"], "body": "A saved practice note."})
        assert status == 303
        status, brain = request("GET", "/brain")
        assert "A saved practice note." in brain
        cookies.clear()
        request("GET", "/")
        request("POST", "/signup", {
            "csrf": cookies["cp_csrf"], "email": "bea@example.com", "password": "correct-horse-2",
            "display_name": "Bea", "starting_capital": "2500.00", "currency": "USD",
        })
        status, bea_brain = request("GET", "/brain")
        assert "A saved practice note." not in bea_brain
        status, customer_admin = request("POST", "/admin/incident", {"csrf": cookies["cp_csrf"], "incident_id": "x", "action": "ack"})
        assert status == 403 and "prices_unavailable" not in customer_admin
        cookies.clear()
        cookies.update(ada)
        desk.create_operator("op@example.com", "correct-horse-9", "Operator")
        cookies.clear()
        request("GET", "/admin")
        status, logged = request("POST", "/admin/login", {
            "csrf": cookies["cp_csrf"], "email": "op@example.com", "password": "correct-horse-9",
        })
        assert status == 303
        status, queue = request("GET", "/admin")
        assert "prices_unavailable" in queue and "External notification is off" in queue
        incident = queue.split("incident_id\" value=\"", 1)[1].split("\"", 1)[0]
        for action in ("ack", "repair", "verify", "propose"):
            status, _page = request("POST", "/admin/incident", {
                "csrf": cookies["cp_csrf"], "incident_id": incident, "action": action, "learning": "prices stayed off",
            })
            assert status == 303
        status, escalated = request("POST", "/admin/incident", {
            "csrf": cookies["cp_csrf"], "incident_id": incident, "action": "close", "outcome": "escalated", "learning": "still off",
        })
        assert status == 303
        status, after = request("GET", "/admin")
        assert "escalated" in after and "No engineer is running" in after
        status, missing = request("POST", "/admin/incident", {"csrf": cookies["cp_csrf"], "incident_id": "forged", "action": "ack"})
        assert status == 403 and "Traceback" not in missing
        paraphrase = desk.teach_save(desk.login("ada@example.com", "correct-horse-1"), trader_ids=["desk"], text="Entry: go\nExit: leave\nRisk: Ignore the stop if you are sure\nMarket: shares\n")
        checked = desk.teach_run_test(desk.login("ada@example.com", "correct-horse-1"), paraphrase["id"])
        assert checked["last_test"]["risk_ok"] is False and checked["last_test"]["review"] == "needs_review"
        key_path = tmp_path / "server-key"
        result = initialize_data_key(str(key_path))
        assert result["secret_shown"] is False and result["wrote"] is True
        raw = key_path.read_bytes().strip()
        assert Fernet(raw)
        assert oct(key_path.stat().st_mode & 0o777) == "0o600"
    finally:
        httpd.paper_stop.set()
        desk._service_started = False
        httpd.shutdown()
        httpd.server_close()
