"""Retail phone hub: provider status stays off the default screens, and brand files have a route."""
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHAT = (ROOT / "tools" / "thf_chat.js").read_text(encoding="utf-8")
ELI = (ROOT / "tools" / "thf_eli10.js").read_text(encoding="utf-8")
PAGE = (ROOT / "tools" / "thf_eli10.html").read_text(encoding="utf-8")


class Headers(dict):
    def get_all(self, key):
        value = self.get(key)
        return [value] if value is not None else []


def test_retail_templates_do_not_paint_provider_status():
    from veteran_desk import thf_home as H
    html = H.page_html({}, "/")
    assert "The AI brains" not in ELI
    assert "Every seat in plain words" not in ELI
    assert "h.ais" not in ELI and "t.brains" not in ELI
    assert "Page checked " not in ELI
    assert "Figures built " in ELI
    assert 'g!=="info"&&g!=="ai"' in ELI
    assert "/api/ai_status" not in ELI and "/api/ai_status" not in CHAT
    assert "admin=1" not in ELI and "admin=1" not in CHAT
    assert "debug=1" not in ELI and "debug=1" not in CHAT
    assert "[?&]admin" not in ELI and "[?&]debug" not in CHAT
    assert "dc-aisw" not in CHAT and "code reply" not in CHAT and "AI was down" not in CHAT
    assert "min-height:0" in CHAT and 'id=dc-more hidden' in CHAT and "imp_more" in CHAT
    assert "id=dc-t" in CHAT and "id=dc-send" in CHAT and "id=dc-to" in CHAT
    seats = ELI[ELI.index("R.seats=function"):ELI.index("/* ---- Health")]
    assert seats.index("Traders") < seats.index("t.push")
    assert 'href="/assets/brand/apple-touch-icon.png"' in PAGE
    assert "fetch(\"/api/ai_status\"" not in CHAT
    assert "Transparent Trading" in html and "PAPER" in html
    assert "Not a live broker" in html
    assert "Since 06:00 WITA" in html and "Since midnight WITA" in html
    assert 'href="/assets/brand/favicon.svg"' in html
    assert 'href="/assets/brand/tokens.css"' in html
    assert "JPMorgan" not in html and "J.P. Morgan" not in html
    assert 'data-mode="hub"' in html
    header = PAGE.split("<main", 1)[0]
    assert 'href="/desk/chat"' not in header and "Previous page" not in header
    assert "::-webkit-details-marker{display:none}" in PAGE
    assert "prefers-reduced-motion:reduce" in PAGE
    assert 'class="chev" aria-hidden="true"' in ELI and "›" not in ELI
    assert "replies ▾" not in CHAT and "show more ▾" not in CHAT
    assert "dc-clip" in CHAT and "::-webkit-details-marker" in CHAT


def _get(path):
    from veteran_desk import thf_dash
    sent = {}

    class Get(thf_dash.H):
        def __init__(self):
            self.path = path
            self.headers = Headers({"Host": "127.0.0.1:18870", "Accept-Encoding": ""})
            self.command = "GET"
            self.wfile = io.BytesIO()
            self.close_connection = True

        def send_response(self, code, message=None):
            sent["code"] = code

        def send_header(self, *args):
            pass

        def end_headers(self):
            pass

        def log_message(self, *args):
            pass

    getter = Get()
    getter.do_GET()
    return sent["code"], getter.wfile.getvalue()


def test_brand_map_serves_the_four_files_and_refuses_traversal():
    from veteran_desk import thf_dash
    assert set(thf_dash.BRAND_TYPES) == {"favicon.svg", "mark.svg", "wordmark.svg", "tokens.css", "apple-touch-icon.png"}
    for name, media in thf_dash.BRAND_TYPES.items():
        body, ctype = thf_dash.brand_asset("/assets/brand/" + name)
        raw = (ROOT / "assets" / "brand" / name).read_bytes()
        assert body == raw and ctype == media
        code, wired = _get("/assets/brand/" + name + "?admin=1")
        assert code == 200 and raw in wired
    assert thf_dash.brand_asset("/assets/brand/../book.py") is None
    assert thf_dash.brand_asset("/assets/brand/favicon.svg/../../book.py") is None
    assert thf_dash.brand_asset("/assets/brand/not-a-file.svg") is None
    code, body = _get("/assets/brand/../book.py")
    assert code == 404 and b"not found" in body
    assert thf_dash.brand_asset("/assets/brand/favicon.svg?debug=1")[0].startswith(b"<svg")
    assert thf_dash.brand_asset("/assets/brand/apple-touch-icon.png")[0].startswith(b"\x89PNG")


def _post(path, body, headers):
    from veteran_desk import thf_dash
    sent = {}

    class Post(thf_dash.H):
        def __init__(self):
            self.path = path
            self.headers = headers
            self.rfile = io.BytesIO(body)
            self.wfile = io.BytesIO()
            self.command = "POST"
            self.close_connection = True

        def send_response(self, code, message=None):
            sent["code"] = code

        def send_header(self, *args):
            pass

        def end_headers(self):
            pass

        def log_message(self, *args):
            pass

    poster = Post()
    poster.do_POST()
    return sent["code"], poster.wfile.getvalue()


def _json_headers(body, extra=None):
    headers = Headers({
        "Host": "127.0.0.1:18870",
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "Accept-Encoding": "",
    })
    if extra:
        headers.update(extra)
    return headers


def test_send_and_upload_hit_the_real_handlers(tmp_path):
    import veteran_desk.thf_chat_home as CH
    import veteran_desk.thf_funder as FM
    saved = (FM.F, FM.LK, FM.IMG_DIR, FM.ADV, FM.ADVLK, CH.REACTS)
    ledger = tmp_path / "funder_messages.jsonl"
    images = tmp_path / "chat_imgs"
    images.mkdir()
    notes = tmp_path / "advisor_msgs.jsonl"
    FM.F, FM.LK = str(ledger), str(ledger) + ".lock"
    FM.IMG_DIR = str(images)
    FM.ADV, FM.ADVLK = str(notes), str(notes) + ".lock"
    CH.REACTS = str(images / "chat_reacts.jsonl")
    seat = "macro" if "macro" in FM.SEAT_IDS else next(iter(FM.SEAT_IDS))
    try:
        assert 'fetch("/api/funder_msg"' in CHAT
        assert 'fetch("/api/advisor_note"' in CHAT or 'u="/api/advisor_note"' in CHAT
        assert 'fetch("/api/chat_img"' in CHAT
        assert 'fetch("/api/chat_react"' in CHAT
        assert 'fetch("/api/chat"' not in CHAT and 'fetch("/desk/chat"' not in CHAT
        empty = b""
        code, raw = _post("/api/funder_msg", empty, _json_headers(empty))
        assert code == 413 and b"ok" not in raw.lower()
        assert not ledger.exists()
        blank = json.dumps({"kind": "seat", "seat": seat, "text": "   "}).encode()
        code, raw = _post("/api/funder_msg", blank, _json_headers(blank))
        assert code == 400 and not ledger.exists()
        body = json.dumps({"kind": "seat", "seat": seat, "text": "contract probe", "request_id": "retail-contract-1"}).encode()
        code, raw = _post("/api/funder_msg", body, _json_headers(body))
        assert code == 200, raw
        saved_row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
        answered = json.loads(raw)
        assert answered["id"] == saved_row["id"] and saved_row["text"] == "contract probe" and saved_row["id"].startswith("fm-")
        again, raw_again = _post("/api/funder_msg", body, _json_headers(body))
        assert again == 200 and json.loads(raw_again)["id"] == saved_row["id"]
        assert ledger.read_text(encoding="utf-8").count("\n") == 1
        crossed = Headers(_json_headers(body))
        crossed["Sec-Fetch-Site"] = "cross-site"
        code, raw = _post("/api/funder_msg", body, crossed)
        assert code == 403 and ledger.read_text(encoding="utf-8").count("\n") == 1
        note = json.dumps({"trade": "DESK", "advisor": "claude", "text": "contract note"}).encode()
        code, raw = _post("/api/advisor_note", note, _json_headers(note))
        assert code == 200, raw
        stored = json.loads(notes.read_text(encoding="utf-8").splitlines()[-1])
        assert stored["text"] == "contract note" and stored["advisory_only"] is True and json.loads(raw)["id"] == stored["id"]
        missing = json.dumps({"trade": "DESK", "advisor": "claude", "text": ""}).encode()
        code, _raw = _post("/api/advisor_note", missing, _json_headers(missing))
        assert code == 400 and notes.read_text(encoding="utf-8").count("\n") == 1
        import base64
        upload = json.dumps({"data_url": "data:text/plain;base64,QQ=="}).encode()
        code, raw = _post("/api/chat_img", upload, _json_headers(upload))
        assert code == 400 and list(images.glob("ci-*")) == [] and b"ok" not in raw.lower()
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
        assert png.startswith(b"\x89PNG")
        upload = json.dumps({"data_url": "data:image/png;base64," + base64.b64encode(png).decode()}).encode()
        code, raw = _post("/api/chat_img", upload, _json_headers(upload))
        assert code == 200, raw
        image = json.loads(raw)
        stored_path = images / image["id"]
        assert stored_path.is_file() and stored_path.read_bytes() == png and image["ok"] is True
        boundary = "----retailcontract"
        part = (
            ("--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.png\"\r\n"
             "Content-Type: image/png\r\n\r\n").encode()
            + png + ("\r\n--" + boundary + "--\r\n").encode()
        )
        headers = Headers({
            "Host": "127.0.0.1:18870",
            "Content-Type": "multipart/form-data; boundary=" + boundary,
            "Content-Length": str(len(part)),
            "Accept-Encoding": "",
        })
        code, raw = _post("/api/chat_img", part, headers)
        assert code == 200, raw
        second = json.loads(raw)
        assert second["id"] != image["id"] and (images / second["id"]).read_bytes() == png
        heart = json.dumps({"k": 3, "src": "t", "on": True, "chat": "macro"}).encode()
        code, raw = _post("/api/chat_react", heart, _json_headers(heart))
        assert code == 200 and json.loads(raw)["ok"] is True
        react_row = json.loads(Path(CH.REACTS).read_text(encoding="utf-8").splitlines()[-1])
        assert react_row["k"] == 3 and react_row["on"] is True
        bad = json.dumps({"k": 0, "src": "t", "on": True}).encode()
        code, _raw = _post("/api/chat_react", bad, _json_headers(bad))
        assert code == 400 and Path(CH.REACTS).read_text(encoding="utf-8").count("\n") == 1
        blocked = json.dumps({"text": "should not send"}).encode()
        before_lines = ledger.read_text(encoding="utf-8").count("\n")
        for blocked_path in ("/api/chat", "/desk/chat"):
            code, raw = _post(blocked_path, blocked, _json_headers(blocked))
            assert code == 404 and b"ok" not in raw.lower()
        assert ledger.read_text(encoding="utf-8").count("\n") == before_lines
    finally:
        FM.F, FM.LK, FM.IMG_DIR, FM.ADV, FM.ADVLK, CH.REACTS = saved


def test_unread_badge_says_plus_only_when_older_rows_were_dropped():
    from veteran_desk import thf_chat_home as CH
    reg = {"macro": dict(id="macro", name="Theo", desk="Macro", label="Theo", admin=False, star=False, pinned=False, color="#000", av="T", can_send=True)}

    def row(i):
        return dict(k=i, id="m%d" % i, **{"from": "macro"}, to="brett", text="fault %d" % i, ts_wita="2026-11-07 04:00:00", mode="brain")

    exact = CH.summarize("macro", [row(i) for i in range(1, 41)], reg)
    assert exact["imp_more"] is False and len(exact["imp"]) == 40
    dropped = CH.summarize("macro", [row(i) for i in range(1, 50)], reg)
    assert dropped["imp_more"] is True and len(dropped["imp"]) == CH.HOME_IMP_N and dropped["imp"][0] == 10
