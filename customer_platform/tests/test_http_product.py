"""Black-box HTTP for the customer app. No network provider and no live order."""

import http.client
from urllib.parse import urlencode

from customer_platform.http_app import bind
from customer_platform.platform import Platform


def opened(tmp_path):
    desk = Platform(tmp_path / "customers", enabled=True, signup_enabled=True, webhook_secret=b"test-webhook-secret")
    httpd = bind(desk)
    cookies = {}

    def request(method, path, form=None, raw=None, content_type=None):
        connection = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        headers = {}
        if cookies:
            headers["Cookie"] = "; ".join("{0}={1}".format(key, value) for key, value in cookies.items())
        body = None
        if form is not None:
            body = urlencode(form)
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif raw is not None:
            body = raw
            headers["Content-Type"] = content_type
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read().decode()
        for header in response.headers.get_all("Set-Cookie") or []:
            pair = header.split(";", 1)[0]
            key, value = pair.split("=", 1)
            cookies[key] = value
        status = response.status
        connection.close()
        return status, payload

    return desk, httpd, request, cookies


def test_customer_entrypoint_and_owner_dash_import():
    import customer_platform.__main__ as entry
    import customer_platform.http_app as http
    import veteran_desk.thf_dash as dash
    assert callable(entry.main) and callable(http.serve)
    assert callable(dash.wire_payload)


def test_http_paper_replay_teach_ask_keys_and_incidents(tmp_path, monkeypatch):
    monkeypatch.delenv("CUSTOMER_PLATFORM_DATA_KEY", raising=False)
    desk, httpd, request, cookies = opened(tmp_path)
    try:
        status, home = request("GET", "/")
        assert status == 200
        status, _ada = request("POST", "/signup", {
            "csrf": cookies["cp_csrf"], "email": "ada@example.com", "password": "correct-horse-1",
            "display_name": "Ada", "starting_capital": "10000.00", "currency": "USD",
        })
        assert status == 303
        status, started = request("POST", "/desk/start", {"csrf": cookies["cp_csrf"]})
        assert status == 200 and "disabled" in started and "Arm synthetic replay" in started
        assert "not connected" in started
        status, armed = request("POST", "/desk/replay", {"csrf": cookies["cp_csrf"]})
        assert status == 200 and "not a live feed" in armed and "Running" not in armed
        status, replay = request("POST", "/desk/start", {"csrf": cookies["cp_csrf"]})
        assert status == 200 and "Paper replay" in replay and "Running" not in replay
        status, ordered = request("POST", "/desk/order", {
            "csrf": cookies["cp_csrf"], "symbol": "AAPL", "side": "buy", "qty": "1", "limit_price": "100.00", "stop": "90.00",
        })
        assert status == 200 and "Orders the paper engine sent: 0" not in ordered
        assert "Running" not in ordered
        status, activity = request("POST", "/desk/activity", {"csrf": cookies["cp_csrf"], "level": "2"})
        assert status == 200
        status, allow = request("POST", "/desk/allow_b", {"csrf": cookies["cp_csrf"], "allowed": "yes"})
        assert status == 200 and "Running" not in allow
        status, trader = request("POST", "/desk/trader", {"csrf": cookies["cp_csrf"], "trader_id": "desk", "enabled": "yes"})
        assert status == 200
        status, keys = request("GET", "/keys")
        assert status == 200 and "type=\"password\"" in keys and "not ready" in keys
        status, refused = request("POST", "/keys", {"csrf": cookies["cp_csrf"], "provider": "openai", "secret": "sk-test-secret-value"})
        assert status == 400 and "sk-test-secret-value" not in refused
        status, teach = request("GET", "/teach")
        assert status == 200 and "type=\"file\"" in teach and "Roll back" not in teach
        status, saved = request("POST", "/teach/save", {
            "csrf": cookies["cp_csrf"], "traders": "desk", "entry": "buy", "exit": "sell", "risk": "1 percent", "market": "paper",
        })
        assert status == 303
        status, listed = request("GET", "/teach")
        assert status == 200 and "draft" in listed and "type=\"file\"" in listed and "Run synthetic paper test" in listed
        boundary = "----cp"
        raw = (
            "--{b}\r\nContent-Disposition: form-data; name=\"csrf\"\r\n\r\n{csrf}\r\n"
            "--{b}\r\nContent-Disposition: form-data; name=\"traders\"\r\n\r\ndesk\r\n"
            "--{b}\r\nContent-Disposition: form-data; name=\"upload\"; filename=\"note.txt\"\r\n"
            "Content-Type: text/plain\r\n\r\nEntry: wait\nExit: flat\nRisk: small\nMarket: paper\r\n"
            "--{b}--\r\n"
        ).format(b=boundary, csrf=cookies["cp_csrf"]).encode()
        status, uploaded = request("POST", "/teach/upload", raw=raw, content_type="multipart/form-data; boundary=" + boundary)
        assert status == 303
        status, ask = request("GET", "/ask")
        assert status == 200 and "retried or cancelled" in ask
        status, asked = request("POST", "/ask", {"csrf": cookies["cp_csrf"], "text": "What is my paper state?"})
        assert status == 303
        status, pending = request("GET", "/ask")
        assert status == 200 and "What is my paper state?" in pending and "not_sent" in pending and "Retry" in pending and "Cancel" in pending
        ada = dict(cookies)
        cookies.clear()
        status, _home = request("GET", "/")
        status, _bea = request("POST", "/signup", {
            "csrf": cookies["cp_csrf"], "email": "bea@example.com", "password": "correct-horse-2",
            "display_name": "Bea", "starting_capital": "2500.00", "currency": "USD",
        })
        status, bea_ask = request("GET", "/ask")
        assert "What is my paper state?" not in bea_ask
        status, bea_teach = request("GET", "/teach")
        assert "buy" not in bea_teach or "No skill saved yet." in bea_teach
        cookies.clear()
        cookies.update(ada)
        status, admin = request("GET", "/admin")
        assert status == 200 and "prices_unavailable" not in admin
        desk.create_operator("op@example.com", "correct-horse-9", "Operator")
        cookies.clear()
        status, login_page = request("GET", "/admin")
        status, logged = request("POST", "/admin/login", {
            "csrf": cookies["cp_csrf"], "email": "op@example.com", "password": "correct-horse-9",
        })
        assert status == 303
        status, queue = request("GET", "/admin")
        assert status == 200 and "prices_unavailable" in queue and "External notification is off" in queue
        assert "No engineer is running" in queue and "sk-test-secret-value" not in queue
    finally:
        httpd.paper_stop.set()
        httpd.shutdown()
        httpd.server_close()
