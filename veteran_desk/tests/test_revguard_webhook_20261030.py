"""tfo_revguard_webhook_20261030: POST on G3 fire. Secrets never logged. PAPER."""
import datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
import pytest, urllib.request
T = dt.datetime(2026, 10, 30, 8, tzinfo=dt.timezone.utc)
URL, KEY = "https://hook.example.invalid/SECRETURLTOKEN", "SECRETKEY123"
P = dict(ticker="XYZ", side="LONG"); A = dict(peak_R=2.5, current_R=1.9, trigger="gave_back_20pct_of_peak")
@pytest.fixture
def sec(monkeypatch, tmp_path):
    monkeypatch.delenv("VD_REVGUARD_WEBHOOK_DISABLE", raising=False)
    monkeypatch.setattr(BK, "REVG_SECRETS_DIR", str(tmp_path)); return tmp_path
def cfg(d, url=URL, key=KEY):
    (d / "vd_revguard_url").write_text(" " + url + "\n"); (d / "vd_revguard_key").write_text(key + "\n")
class R:
    status = 200
    def close(self): pass
def test_posts_when_configured(sec, monkeypatch, capsys):
    cfg(sec); got = {}
    def fake(req, timeout=None): got.update(req=req, timeout=timeout); return R()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert BK.revguard_webhook("energy", "T1", P, T, A) == 200
    r = got["req"]; assert r.full_url == URL and r.get_method() == "POST" and got["timeout"] == 3
    assert r.get_header("Authorization") == "Bearer " + KEY
    assert json.loads(r.data) == dict(trade_id="T1", ticker="XYZ", side="LONG", seat="energy", peak_r=2.5, current_r=1.9, trigger="gave_back_20pct_of_peak", ts=T.isoformat())
    o = capsys.readouterr(); assert KEY not in o.out + o.err and URL not in o.out + o.err
@pytest.mark.parametrize("which", ["none", "url_empty", "key_missing"])
def test_skips_when_unconfigured(sec, monkeypatch, capsys, which):
    if which == "url_empty": cfg(sec, url="  ")
    if which == "key_missing": (sec / "vd_revguard_url").write_text(URL)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("must not post"))
    assert BK.revguard_webhook("energy", "T1", P, T, A) is False
    o = capsys.readouterr(); assert o.out.strip() == "revguard webhook skipped: not configured" and KEY not in o.out and URL not in o.out
def test_failure_swallowed_no_secrets(sec, monkeypatch, capsys):
    cfg(sec)
    def boom(req, timeout=None): raise TimeoutError("timed out talking to " + URL + " with " + KEY)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert BK.revguard_webhook("energy", "T1", P, T, A) is False
    o = capsys.readouterr(); assert o.out.strip() == "revguard webhook post failed: TimeoutError"
    assert KEY not in o.out + o.err and URL not in o.out + o.err and "Bearer" not in o.out
def test_env_disable_blocks(sec, monkeypatch, capsys):
    cfg(sec); monkeypatch.setenv("VD_REVGUARD_WEBHOOK_DISABLE", "1")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("must not post"))
    assert BK.revguard_webhook("energy", "T1", P, T, A) is False
def test_payload_override_bytes(sec, monkeypatch):
    cfg(sec); got = {}
    def fake(req, timeout=None): got["req"] = req; return R()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert BK.revguard_webhook("t", "T", {}, T, {}, payload={"test": True}) == 200
    assert isinstance(got["req"].data, bytes) and json.loads(got["req"].data) == {"test": True}
