"""Book observation and dash-only recovery. Does not apply to a host."""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "deploy/display_guard_20261107/release_control.py"
BASE = ROOT / "deploy/display_release_20261107/captured_base"
KIT = ROOT / "deploy/display-epoch-ui-kit-20261107.tar.gz"
KIT_SHA = "abfc2af4bdeae0959727afb14f308b0f99ebab06eb7e87bce583e872a5f955e4"


def load():
    spec = importlib.util.spec_from_file_location("display_guard_20261107", GUARD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tree(tmp, name, book):
    root = tmp / name
    for path in BASE.rglob("*"):
        if path.is_file():
            dest = root / path.relative_to(BASE)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(path.read_bytes())
    book_path = root / "veteran_desk/book.py"
    book_path.write_bytes(book)
    return root


def backups(module, root):
    for item in module.ALLOW["archives"][0]["files"]:
        rel = item["path"]
        if item.get("new"):
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("new module\n", encoding="utf-8")
            continue
        source = BASE / rel
        backup = root / "state/thf/display_release_20261107_backup" / rel
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(source.read_bytes())
        dest = root / rel
        dest.write_text("replaced\n", encoding="utf-8")


def test_matching_live_book_is_observed_and_not_required_to_match_the_old_pin(tmp_path):
    module = load()
    book = b"live book module bytes are not the historical pin\n"
    live, dash = tree(tmp_path, "live", book), tree(tmp_path, "dash", book)
    observed = module._observe_books((live, dash))
    assert observed[live] == book
    assert hashlib.sha256(book).hexdigest() != "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"


def test_diverged_or_changed_book_stops_the_guard(tmp_path):
    module = load()
    live, dash = tree(tmp_path, "live", b"one\n"), tree(tmp_path, "dash", b"two\n")
    try:
        module._observe_books((live, dash))
    except module.ReleaseError as error:
        assert error.code == "REFUSED_BOOK_DIVERGED"
    else:
        raise AssertionError("diverged books were accepted")
    same = b"same\n"
    live2, dash2 = tree(tmp_path, "live2", same), tree(tmp_path, "dash2", same)
    books = module._observe_books((live2, dash2))
    (live2 / "veteran_desk/book.py").write_bytes(b"changed during apply\n")
    try:
        module._same_books((live2, dash2), books)
    except module.ReleaseError as error:
        assert error.code == "REFUSED_BOOK_CHANGED"
    else:
        raise AssertionError("a book change during apply was ignored")


def test_restore_after_restart_begin_puts_bases_back_and_leaves_book(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "guard")
    module = load()
    book = b"observed book\n"
    live, dash = tree(tmp_path, "live", book), tree(tmp_path, "dash", book)
    for root in (live, dash):
        backups(module, root)
        module._journal(root, "files_committed")
        module._journal(root, "restart_begin")
    result = module.recover_release((live, dash), "restore-files")
    assert result["book_written"] is False
    assert (live / "veteran_desk/book.py").read_bytes() == book
    assert not (live / "veteran_desk/thf_display_epoch.py").exists()
    assert (live / "veteran_desk/thf_dash.py").read_bytes() == (BASE / "veteran_desk/thf_dash.py").read_bytes()
    assert (live / "veteran_desk/chatgpt_bridge.py").exists() is False
    receipt = json.loads((live / "state/thf/display_release_20261107_receipt.json").read_text(encoding="utf-8"))
    assert receipt["restored"] is True and receipt["book_written"] is False
    assert module._events(live)[-1] == "rolled_back"


def test_restart_dash_is_explicit_and_does_not_touch_book(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "guard")
    module = load()
    book = b"observed book\n"
    live, dash = tree(tmp_path, "live", book), tree(tmp_path, "dash", book)
    archive = ROOT / "deploy/display_release_20261107/display-epoch-ui-20261107.tar.gz"
    import io
    import tarfile
    blob = archive.read_bytes()
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        members = {item.name: tar.extractfile(item).read() for item in tar.getmembers() if item.isfile()}
    for root in (live, dash):
        for item in module.ALLOW["archives"][0]["files"]:
            dest = root / item["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(members[item["path"]])
        module._journal(root, "files_committed")
        module._journal(root, "restart_failed")
    calls = []
    result = module.recover_release((live, dash), "restart-dash", restarter=calls.append)
    assert result["unit"] == "tfo-dash-thf.service" and calls == ["tfo-dash-thf.service"]
    assert (live / "veteran_desk/book.py").read_bytes() == book
    assert "restart_ok" in module._events(live)


def test_production_recovery_refuses_any_other_root(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    module = load()
    live, dash = tree(tmp_path, "live", b"book\n"), tree(tmp_path, "dash", b"book\n")
    try:
        module.recover_release((live, dash), "restore-files")
    except module.ReleaseError as error:
        assert error.code == "REFUSED_ROOT"
    else:
        raise AssertionError("a non-production root was recovered")
    assert hashlib.sha256(KIT.read_bytes()).hexdigest() == KIT_SHA
