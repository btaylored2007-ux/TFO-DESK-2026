"""Image upload limits, types, storage, and retrieval. No chat message is posted."""
import base64
import io
import json
from pathlib import Path

from veteran_desk import thf_funder as FM

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8ffff3f0005fe02fe6e2d2a0000000049454e44ae426082")
JPEG = b"\xff\xd8\xff\xd9"
GIF = b"GIF89a"
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP"
HEIC = b"\x00\x00\x00\x18ftypheic"


def _use(tmp_path, monkeypatch):
    monkeypatch.setattr(FM, "IMG_DIR", str(tmp_path))
    monkeypatch.setattr(FM, "L", str(tmp_path))


def _multipart(raw, filename="shot.png", mime="image/png"):
    body = (
        b"--bound\r\n"
        b'Content-Disposition: form-data; name="file"; filename="%s"\r\n' % filename.encode()
        + b"Content-Type: %s\r\n\r\n" % mime.encode()
        + raw
        + b"\r\n--bound--\r\n"
    )
    return 'multipart/form-data; boundary=bound', body


def test_multipart_and_json_round_trip_accepted_types(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch)
    saved = FM.img_save_multipart(*_multipart(PNG))
    raw, mime = FM.img_read(saved["id"])
    assert raw == PNG and mime == "image/png"
    assert saved["url"] == "/api/chat_img/" + saved["id"]
    assert (tmp_path / saved["id"]).is_file()
    for blob, kind in ((JPEG, "image/jpeg"), (GIF, "image/gif"), (WEBP, "image/webp")):
        item = FM.img_save(raw=blob)
        assert item["id"].endswith("." + FM.IMG_MIME[kind])
        assert FM.img_read(item["id"])[0] == blob
    data = "data:image/png;base64," + base64.b64encode(PNG).decode()
    via_json = FM.img_save(data_url=data)
    assert FM.img_read(via_json["id"])[1] == "image/png"


def test_limits_types_and_bad_ids_do_not_store(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch)
    huge = PNG + b"x" * (FM.IMG_MAX + 1)
    try:
        FM.img_save(raw=huge)
    except ValueError as error:
        assert "8MB" in str(error)
    else:
        raise AssertionError("oversize image stored")
    try:
        FM.img_save_multipart(*_multipart(HEIC, "shot.heic", "image/heic"))
    except ValueError as error:
        assert "not a PNG" in str(error)
    else:
        raise AssertionError("heic stored")
    try:
        FM.img_save(data_url="data:text/plain;base64,YQ==")
    except ValueError as error:
        assert "bad data_url" in str(error)
    try:
        FM.img_read("../secrets")
    except FileNotFoundError:
        pass
    try:
        FM.img_read("ci-20261106120000-abcd.jpg/../../book.py")
    except FileNotFoundError:
        pass
    assert list(tmp_path.iterdir()) == []


def test_dashboard_post_and_get_use_the_known_route(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch)
    from veteran_desk import thf_dash

    class Headers(dict):
        def get_all(self, key):
            value = self.get(key)
            return [value] if value is not None else []

    ctype, body = _multipart(PNG)
    sent = {}

    class Handler:
        path = "/api/chat_img"
        headers = Headers({"Content-Type": ctype, "Content-Length": str(len(body)), "Host": "127.0.0.1:18870"})
        rfile = io.BytesIO(body)
        command = "POST"
        def _send(self, code, payload, content_type):
            sent["post"] = (code, payload, content_type)

    thf_dash.H.do_POST(Handler())
    code, payload, content_type = sent["post"]
    assert code == 200 and content_type == "application/json"
    saved = json.loads(payload)
    assert saved["id"].startswith("ci-")

    class Get(thf_dash.H):
        def __init__(self):
            self.path = "/api/chat_img/" + saved["id"]
            self.headers = Headers({"Host": "127.0.0.1:18870", "Accept-Encoding": ""})
            self.command = "GET"
            self.wfile = io.BytesIO()
            self.close_connection = True
        def send_response(self, code, message=None):
            sent["get_code"] = code
        def send_header(self, *args):
            pass
        def end_headers(self):
            pass
        def log_message(self, *args):
            pass

    getter = Get()
    getter.do_GET()
    assert sent["get_code"] == 200
    assert getter.wfile.getvalue() == PNG
    too_big = "9" * 13
    class Big:
        path = "/api/chat_img"
        headers = Headers({"Content-Type": "application/json", "Content-Length": too_big, "Host": "127.0.0.1"})
        rfile = io.BytesIO(b"{}")
        command = "POST"
        def _send(self, code, payload, content_type):
            sent["big"] = code
    thf_dash.H.do_POST(Big())
    assert sent["big"] == 413


def test_chat_ui_reports_upload_errors_without_a_message_post():
    text = Path("tools/thf_chat.js").read_text(encoding="utf-8")
    assert 'fetch("/api/chat_img"' in text
    assert 'src="/api/chat_img/' in text
    assert "upload failed:" in text
    assert "image too large (>8MB)" in text
    assert "max 4 images" in text
    assert "/eli10" not in text
    assert "fetch(\"/chat\"" not in text
    assert "/desk/chat" in text
