"""A copy that fails after an earlier success restores the staged tree."""
import hashlib
import importlib.util
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "deploy" / "lesson_final_20261107"
BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load():
    spec = importlib.util.spec_from_file_location("lesson_final_install", PKG / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stage(tmp_path):
    root = tmp_path / "stage"
    for path in (PKG / "base").rglob("*"):
        if path.is_file():
            dest = root / path.relative_to(PKG / "base")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(path, dest)
    shutil.copy(REPO / "veteran_desk" / "book.py", root / "veteran_desk" / "book.py")
    return root


def _bases(root):
    found = {}
    for path in (PKG / "base").rglob("*"):
        if path.is_file():
            rel = path.relative_to(PKG / "base")
            found[str(rel)] = _sha(root / rel)
    return found


def _inject(module, predicate):
    real = module.shutil.copy2

    def wrapped(src, dst, *args, **kwargs):
        if "payload" in str(src) and predicate(str(src)):
            real(src, dst, *args, **kwargs)
            raise OSError("injected")
        return real(src, dst, *args, **kwargs)

    module.shutil.copy2 = wrapped


def test_failed_copy_after_a_successful_write_restores_the_tree(tmp_path, capsys):
    install = _load()
    root = _stage(tmp_path)
    before = _bases(root)
    seen = {"n": 0}

    def second_payload(src):
        seen["n"] += 1
        return seen["n"] == 2

    _inject(install, second_payload)
    code = install.main(["--root", str(root), "--staging", "--apply"])
    out = capsys.readouterr().out
    assert code == 2
    assert "APPLY_REFUSED" in out
    assert "FAILED_COPY" in out and "injected" in out
    assert _bases(root) == before
    assert _sha(root / "veteran_desk" / "book.py") == BOOK
    assert not (root / "veteran_desk" / "feed_status_facts.py").exists()
    assert not (root / "state" / "thf" / "lesson_final_20261107_receipt.json").exists()


def test_failed_new_file_after_replaces_is_removed(tmp_path, capsys):
    install = _load()
    root = _stage(tmp_path)
    before = _bases(root)
    _inject(install, lambda src: src.endswith("feed_status_facts.py"))
    code = install.main(["--root", str(root), "--staging", "--apply"])
    out = capsys.readouterr().out
    assert code == 2
    assert "FAILED_COPY veteran_desk/feed_status_facts.py: injected" in out
    assert _bases(root) == before
    assert _sha(root / "veteran_desk" / "book.py") == BOOK
    assert not (root / "veteran_desk" / "feed_status_facts.py").exists()
    assert not (root / "state" / "thf" / "lesson_final_20261107_receipt.json").exists()
