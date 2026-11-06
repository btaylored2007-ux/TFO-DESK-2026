"""Customer isolation stays off the owner desk until a later review."""
import json
from pathlib import Path

import pytest

from veteran_desk import tenant_foundation as T

BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"


def _store(tmp_path):
    return T.Store(tmp_path / "customers", enabled=True)


def test_foundation_is_off_and_not_mounted_on_the_owner_gate(tmp_path):
    with pytest.raises(T.FoundationOff):
        T.Store(tmp_path / "customers")
    dash = Path("veteran_desk/thf_dash.py").read_text(encoding="utf-8")
    assert "tenant_foundation" not in dash
    text = Path("veteran_desk/tenant_foundation.py").read_text(encoding="utf-8")
    assert "ThreadingHTTPServer" not in text and "api-fxtrade" not in text
    assert "oanda" not in text and "chatgpt_profile" not in text


def test_store_refuses_the_owner_live_tree_and_a_copied_book(tmp_path):
    with pytest.raises(T.Denied):
        T.assert_separate_root("/opt/tfo/live/thf/customers")
    book = tmp_path / "veteran_desk" / "book.py"
    book.parent.mkdir()
    book.write_text("owner book")
    with pytest.raises(T.Denied):
        T.Store(tmp_path, enabled=True)


def test_no_default_password_and_no_public_registration_helper():
    with pytest.raises(T.Denied):
        T._reject_password("password")
    with pytest.raises(T.Denied):
        T._reject_password("short")
    assert "def register" not in Path("veteran_desk/tenant_foundation.py").read_text(encoding="utf-8")


def test_session_tenant_wins_over_a_claimed_tenant(tmp_path):
    store = _store(tmp_path)
    owner = store.provision_tenant("ada@example.com", "correct-horse-1", "Ada")
    other = store.provision_tenant("bea@example.com", "correct-horse-2", "Bea")
    ada = store.login("ada@example.com", "correct-horse-1")
    bea = store.login("bea@example.com", "correct-horse-2")
    saved = store.upload(ada, "note.png", b"\x89PNG\r\n\x1a\nowner-bytes")
    with pytest.raises(T.Denied):
        store.read_upload(bea, saved["id"], claimed_tenant=owner["tenant_id"])
    with pytest.raises(T.Denied):
        store.paper(bea, claimed_tenant=owner["tenant_id"])
    with pytest.raises(T.Denied):
        store.write_note(ada, "hello", claimed_tenant=other["tenant_id"])
    paper = store.paper(ada)
    assert paper["imported_owner_book"] is False and paper["broker"] is None
    assert paper["positions"] == []
    assert BOOK not in json.dumps(paper)
    sessions = (tmp_path / "customers" / "sessions.json").read_text(encoding="utf-8")
    accounts = (tmp_path / "customers" / "accounts.json").read_text(encoding="utf-8")
    assert ada not in sessions and "correct-horse-1" not in accounts


def test_viewer_cannot_upload_and_member_cannot_administer(tmp_path):
    store = _store(tmp_path)
    store.provision_tenant("ada@example.com", "correct-horse-1", "Ada")
    ada = store.login("ada@example.com", "correct-horse-1")
    store.add_member(ada, "view@example.com", "correct-horse-3", "viewer")
    store.add_member(ada, "mem@example.com", "correct-horse-4", "member")
    with pytest.raises(T.Denied):
        store.add_member(ada, "admin2@example.com", "correct-horse-5", "tenant_admin")
    viewer = store.login("view@example.com", "correct-horse-3")
    member = store.login("mem@example.com", "correct-horse-4")
    with pytest.raises(T.Denied):
        store.upload(viewer, "x.png", b"abc")
    with pytest.raises(T.Denied):
        store.add_member(member, "new@example.com", "correct-horse-6", "viewer")
    assert store.paper(viewer)["paper_only"] is True
    own = store.upload(member, "x.png", b"abc")
    assert store.read_upload(member, own["id"]) == b"abc"
    with pytest.raises(T.Denied):
        store.upload(member, "../paper.json", b"abc")
    with pytest.raises(T.Denied):
        store.read_upload(member, "../paper.json")
    rows = store.audit(viewer)
    assert rows and all(row["tenant_id"] == store.context(viewer)["tenant_id"] for row in rows)
    assert all(row["tenant_id"] != "tn_other" for row in rows)


def test_bad_login_and_expired_session_are_refused(tmp_path):
    store = _store(tmp_path)
    store.provision_tenant("ada@example.com", "correct-horse-1", "Ada")
    with pytest.raises(T.Denied):
        store.login("ada@example.com", "wrong-password-9")
    with pytest.raises(T.Denied):
        store.login("nobody@example.com", "correct-horse-1")
    token = store.login("ada@example.com", "correct-horse-1")
    sessions = json.loads((tmp_path / "customers" / "sessions.json").read_text(encoding="utf-8"))
    key = next(iter(sessions))
    sessions[key]["expires"] = 1
    (tmp_path / "customers" / "sessions.json").write_text(json.dumps(sessions), encoding="utf-8")
    with pytest.raises(T.Denied):
        store.paper(token)
    assert not hasattr(store, "revoke")
