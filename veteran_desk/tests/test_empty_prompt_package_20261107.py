"""The empty-prompt package replaces only the captured live bases."""
import hashlib
import importlib.util
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "deploy" / "empty_prompt_20261107"
BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load():
    spec = importlib.util.spec_from_file_location("empty_prompt_install", PKG / "install.py")
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


def test_dry_run_replaces_the_captured_bases(tmp_path, capsys):
    install = _load()
    root = _stage(tmp_path)
    code = install.main(["--root", str(root)])
    out = capsys.readouterr().out
    assert code == 0
    assert "REPLACE veteran_desk/thf_shadow_sampler.py" in out
    assert "REPLACE veteran_desk/claude_brain.py" in out
    assert "REPLACE veteran_desk/thf_grok.py" in out
    assert "DRY_RUN" in out and "NOT_DEPLOYED" in out


def test_unknown_bytes_are_blocked_and_left_untouched(tmp_path, capsys):
    install = _load()
    root = _stage(tmp_path)
    dest = root / "veteran_desk" / "claude_brain.py"
    before = dest.read_bytes()
    dest.write_bytes(before + b"\n# live drift\n")
    code = install.main(["--root", str(root), "--staging", "--apply"])
    out = capsys.readouterr().out
    assert code == 2
    assert "BLOCKED veteran_desk/claude_brain.py" in out
    assert "APPLY_REFUSED" in out
    assert dest.read_bytes() == before + b"\n# live drift\n"
    assert _sha(root / "veteran_desk" / "thf_shadow_sampler.py") == _sha(PKG / "base" / "veteran_desk" / "thf_shadow_sampler.py")
    assert not (root / "state" / "thf" / "empty_prompt_20261107_receipt.json").exists()


def test_failed_copy_restores_the_finished_replace(tmp_path, capsys):
    install = _load()
    root = _stage(tmp_path)
    before = {str(path.relative_to(PKG / "base")): _sha(root / path.relative_to(PKG / "base"))
              for path in (PKG / "base").rglob("*") if path.is_file()}
    seen = {"n": 0}
    real = install.shutil.copy2

    def wrapped(src, dst, *args, **kwargs):
        if "payload" in str(src):
            seen["n"] += 1
            if seen["n"] == 2:
                real(src, dst, *args, **kwargs)
                raise OSError("injected")
        return real(src, dst, *args, **kwargs)

    install.shutil.copy2 = wrapped
    try:
        code = install.main(["--root", str(root), "--staging", "--apply"])
    finally:
        install.shutil.copy2 = real
    out = capsys.readouterr().out
    assert code == 2
    assert "FAILED_COPY" in out and "injected" in out
    assert "APPLY_REFUSED" in out and "NOT_DEPLOYED" in out
    for rel, digest in before.items():
        assert _sha(root / rel) == digest
    assert _sha(root / "veteran_desk" / "book.py") == BOOK
    assert not (root / "state" / "thf" / "empty_prompt_20261107_receipt.json").exists()


def test_staging_apply_and_rollback_restore_the_live_bases(tmp_path, capsys):
    install = _load()
    root = _stage(tmp_path)
    code = install.main(["--root", str(root), "--staging", "--apply"])
    out = capsys.readouterr().out
    assert code == 0 and "STAGED" in out and "NOT_DEPLOYED" in out
    for rel in ("veteran_desk/thf_shadow_sampler.py", "veteran_desk/claude_brain.py", "veteran_desk/thf_grok.py"):
        assert _sha(root / rel) == _sha(PKG / "payload" / rel)
    assert _sha(root / "veteran_desk" / "book.py") == BOOK
    code = install.main(["--root", str(root), "--staging", "--rollback"])
    out = capsys.readouterr().out
    assert code == 0 and "ROLLED_BACK" in out
    for rel in ("veteran_desk/thf_shadow_sampler.py", "veteran_desk/claude_brain.py", "veteran_desk/thf_grok.py"):
        assert _sha(root / rel) == _sha(PKG / "base" / rel)


def test_production_roots_are_refused(capsys):
    install = _load()
    for banned in ("/opt/tfo/live/thf", "/opt/tfo/dash/base"):
        code = install.main(["--root", banned, "--staging", "--apply"])
        out = capsys.readouterr().out
        assert code == 2
        assert "REFUSED_ROOT" in out and "NOT_DEPLOYED" in out
