"""Invariants of the single-owner paper desk. These do not prove a customer product."""
import hashlib
import pathlib
import subprocess
import sys

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


def test_dashboard_bind_defaults_to_loopback_and_consumers_use_it(monkeypatch):
    from veteran_desk import thf_dash as D
    monkeypatch.delenv("THF_DASH_BIND", raising=False)
    assert D.listen_host() == "127.0.0.1"
    monkeypatch.setenv("THF_DASH_BIND", "127.0.0.1")
    assert D.listen_host() == "127.0.0.1"
    monkeypatch.setenv("THF_DASH_BIND", "")
    assert D.listen_host() == "127.0.0.1"
    text = (ROOT / "veteran_desk/thf_dash.py").read_text(encoding="utf-8")
    assert "ThreadingHTTPServer((listen_host(), PORT)" in text
    assert "127.0.0.1:28870" in (ROOT / "thf_guardian/config.py").read_text(encoding="utf-8")
    for rel in ("veteran_desk/dash_context.py", "veteran_desk/thf_home.py"):
        path = ROOT / rel
        if path.is_file():
            assert "127.0.0.1:28870" in path.read_text(encoding="utf-8")
    nginx = (ROOT / "nginx_safe/tfo-dash.conf.excerpt").read_text(encoding="utf-8")
    assert "proxy_pass http://127.0.0.1:28870" in nginx
    assert "PIN is enforced" not in nginx
    assert "check_pin returns true" in nginx


def test_bind_installer_accepts_the_captured_live_dash_and_rolls_back(tmp_path):
    live = pathlib.Path("/tmp/rtdiag/thf-upgrade-runtime-diag-20261106/dash/thf_dash.py")
    if not live.is_file():
        return
    dest = tmp_path / "veteran_desk/thf_dash.py"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(live.read_bytes())
    book = tmp_path / "veteran_desk/book.py"
    book.write_bytes((ROOT / "veteran_desk/book.py").read_bytes())
    install = ROOT / "deploy/dash_bind_20261106/install.py"
    result = subprocess.run([sys.executable, str(install), "--root", str(tmp_path), "--apply"],
                            cwd=ROOT, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout
    assert "listen_host" in dest.read_text(encoding="utf-8")
    assert hashlib.sha256(book.read_bytes()).hexdigest() == "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
    back = subprocess.run([sys.executable, str(install), "--root", str(tmp_path), "--rollback"],
                          cwd=ROOT, text=True, capture_output=True, check=False)
    assert back.returncode == 0, back.stdout
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == "e77d367e97f6a149ef0890f49262d0599753e6fac85efceaeb944158d133e745"


def test_nginx_excerpt_listens_on_the_tailnet_and_allows_the_image_post():
    text = (ROOT / "nginx_safe/tfo-dash.conf.excerpt").read_text(encoding="utf-8")
    assert "listen 100.120.8.11:18870" in text
    assert "listen 0.0.0.0" not in text
    assert "location = /api/chat_img" in text
    assert "auth_basic" not in text
    assert "limit_except GET HEAD" in text
