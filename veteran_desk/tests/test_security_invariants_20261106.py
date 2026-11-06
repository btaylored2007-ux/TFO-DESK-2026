"""Invariants of the single-owner paper desk. These do not prove a customer product."""
import pathlib

from veteran_desk import thf_funder as F
from veteran_desk.thf_http import mutation_error

ROOT = pathlib.Path(__file__).resolve().parents[2]
PRACTICE_FILES = (
    "paid_feeds.py",
    "veteran_desk/pit_fx_sgp.py",
    "oanda_sleeve.py",
    "desk_doh.py",
)


class Headers(dict):
    def get_all(self, key):
        value = self.get(key)
        return [value] if value is not None else []


def test_cross_site_posts_are_refused_and_there_is_no_customer_session():
    refused = mutation_error(Headers({
        "Sec-Fetch-Site": "cross-site",
        "Content-Length": "2",
        "Host": "100.120.8.11:18870",
    }))
    assert refused[0] == 403
    dash = (ROOT / "veteran_desk/thf_dash.py").read_text(encoding="utf-8")
    http = (ROOT / "veteran_desk/thf_http.py").read_text(encoding="utf-8")
    assert "Set-Cookie" not in dash and "session_id" not in dash
    assert "X-Content-Type-Options" in http
    assert F.check_pin("anything") is True


def test_chat_image_ids_cannot_leave_the_store(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "IMG_DIR", str(tmp_path))
    assert F.img_abs("../secrets") is None
    assert F.img_abs("ci-20261106120000-abcd.png/../../book.py") is None
    try:
        F.img_read("../../etc/passwd")
    except FileNotFoundError:
        pass


def test_quoted_fx_hosts_in_source_are_practice_only():
    for rel in PRACTICE_FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "https://api-fxtrade.oanda.com" not in text
        if "OANDA_HOST" in text or "HOST = " in text:
            assert "api-fxpractice.oanda.com" in text


def test_nginx_excerpt_listens_on_the_tailnet_and_allows_the_image_post():
    text = (ROOT / "nginx_safe/tfo-dash.conf.excerpt").read_text(encoding="utf-8")
    assert "listen 100.120.8.11:18870" in text
    assert "listen 0.0.0.0" not in text
    assert "location = /api/chat_img" in text
    assert "auth_basic" not in text
    assert "limit_except GET HEAD" in text
