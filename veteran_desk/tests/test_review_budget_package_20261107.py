"""The budget package applies only after the empty-prompt targets are present."""
import hashlib
import importlib.util
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "deploy" / "review_budget_20261107"
LIVE = "a779a30626e765e5d6351ab851399240154802a54cdb955c309e8f97d4ad28ef"
BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load():
    spec = importlib.util.spec_from_file_location("review_budget_install", PKG / "install.py")
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


def test_dry_run_replaces_only_the_post_empty_prompt_sampler(tmp_path, capsys):
    install = _load()
    code = install.main(["--root", str(_stage(tmp_path))])
    out = capsys.readouterr().out
    assert code == 0
    assert "REPLACE veteran_desk/thf_shadow_sampler.py" in out
    assert "UNCHANGED veteran_desk/claude_brain.py" in out
    assert "UNCHANGED veteran_desk/thf_grok.py" in out
    assert "UNCHANGED veteran_desk/chatgpt_bridge.py" in out
    assert "DRY_RUN" in out and "NOT_DEPLOYED" in out


def test_raw_live_sampler_is_blocked(tmp_path, capsys):
    install = _load()
    root = _stage(tmp_path)
    dest = root / "veteran_desk" / "thf_shadow_sampler.py"
    dest.write_bytes((REPO / "deploy" / "empty_prompt_20261107" / "base" / "veteran_desk" / "thf_shadow_sampler.py").read_bytes())
    assert _sha(dest) == LIVE
    code = install.main(["--root", str(root), "--staging", "--apply"])
    out = capsys.readouterr().out
    assert code == 2 and "BLOCKED veteran_desk/thf_shadow_sampler.py" in out and "APPLY_REFUSED" in out
    assert _sha(dest) == LIVE
    assert not (root / "state" / "thf" / "review_budget_20261107_receipt.json").exists()


def test_failed_copy_restores_the_sampler_and_leaves_the_bridge(tmp_path, capsys):
    install = _load()
    root = _stage(tmp_path)
    before = _sha(root / "veteran_desk" / "thf_shadow_sampler.py")
    bridge = _sha(root / "veteran_desk" / "chatgpt_bridge.py")
    real = install.shutil.copy2

    def wrapped(src, dst, *args, **kwargs):
        if "payload" in str(src):
            real(src, dst, *args, **kwargs)
            raise OSError("injected")
        return real(src, dst, *args, **kwargs)

    install.shutil.copy2 = wrapped
    try:
        code = install.main(["--root", str(root), "--staging", "--apply"])
    finally:
        install.shutil.copy2 = real
    out = capsys.readouterr().out
    assert code == 2 and "FAILED_COPY" in out and "injected" in out and "NOT_DEPLOYED" in out
    assert _sha(root / "veteran_desk" / "thf_shadow_sampler.py") == before
    assert _sha(root / "veteran_desk" / "chatgpt_bridge.py") == bridge
    assert _sha(root / "veteran_desk" / "book.py") == BOOK
    assert not (root / "state" / "thf" / "review_budget_20261107_receipt.json").exists()


def test_apply_and_rollback_keep_the_live_bridge(tmp_path, capsys):
    install = _load()
    root = _stage(tmp_path)
    bridge = _sha(root / "veteran_desk" / "chatgpt_bridge.py")
    code = install.main(["--root", str(root), "--staging", "--apply"])
    out = capsys.readouterr().out
    assert code == 0 and "STAGED" in out and "NOT_DEPLOYED" in out
    assert _sha(root / "veteran_desk" / "thf_shadow_sampler.py") == _sha(PKG / "payload" / "veteran_desk" / "thf_shadow_sampler.py")
    assert _sha(root / "veteran_desk" / "chatgpt_bridge.py") == bridge
    assert _sha(root / "veteran_desk" / "book.py") == BOOK
    code = install.main(["--root", str(root), "--staging", "--rollback"])
    out = capsys.readouterr().out
    assert code == 0 and "ROLLED_BACK" in out
    assert _sha(root / "veteran_desk" / "thf_shadow_sampler.py") == _sha(PKG / "base" / "veteran_desk" / "thf_shadow_sampler.py")


def test_production_roots_are_refused(capsys):
    install = _load()
    for banned in ("/opt/tfo/live/thf", "/opt/tfo/dash/base"):
        code = install.main(["--root", banned, "--staging", "--apply"])
        out = capsys.readouterr().out
        assert code == 2 and "REFUSED_ROOT" in out and "NOT_DEPLOYED" in out
