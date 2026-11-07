"""Display-only reset mark. The book file is not a participant."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
PACKAGE = ROOT / "deploy/display_epoch_20261107"
INSTALL = PACKAGE / "install.py"
SYNTHETIC = {
    "id": "test-mark",
    "set_utc": "2026-11-07T00:00:00Z",
    "pnl_since_inception_aud": -20.0,
    "equity_aud": 80.0,
    "seed_aud": 100.0,
    "cash_aud": 40.0,
    "invested_aud": 70.0,
}


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_epoch():
    spec = importlib.util.spec_from_file_location("thf_display_epoch", ROOT / "veteran_desk/thf_display_epoch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_formula_is_equity_minus_seed_and_invested_is_not_capital():
    assert round(SYNTHETIC["equity_aud"] - SYNTHETIC["seed_aud"], 2) == SYNTHETIC["pnl_since_inception_aud"]
    assert round(SYNTHETIC["cash_aud"] + SYNTHETIC["invested_aud"], 2) != SYNTHETIC["seed_aud"]
    assert not (ROOT / "deploy/display_epoch_20261107/baseline_owner_20261107T121234Z.json").exists()
    assert not (ROOT / "deploy/retail_dash_20261107/review/baseline_owner_20261107T121234Z.json").exists()


def test_mark_is_zero_only_when_the_book_field_matches_and_moves_once(tmp_path):
    module = load_epoch()
    base = dict(SYNTHETIC)
    first = module.establish(tmp_path, base, module.CONFIRM)
    assert first["status"] == "SET"
    saved = sha256(tmp_path / "display_epoch.json")
    again = module.establish(tmp_path, base, module.CONFIRM)
    assert again["status"] == "ALREADY_SET"
    assert sha256(tmp_path / "display_epoch.json") == saved
    assert (tmp_path / "display_epoch_audit.jsonl").read_text(encoding="utf-8").count("\n") == 1
    epoch = module.read_epoch(tmp_path / "display_epoch.json")
    assert module.since_reset_aud(base["pnl_since_inception_aud"], epoch) == 0
    moved = module.since_reset_aud(base["pnl_since_inception_aud"] + 10, epoch)
    assert moved == 10
    home = dict(since_aud=base["pnl_since_inception_aud"] + 10, since_txt="-A$32,235", seed_txt="A$5,500,000", equity_txt="A$5,467,765", book_updated="07 Nov 20:12 WITA")
    module.attach(home, dict(seed_aud=base["seed_aud"], pnl_since_inception_aud=home["since_aud"]), tmp_path / "display_epoch.json")
    assert home["since_reset_aud"] == 10
    assert home["since_txt"] == "-A$32,235"
    assert "Not since inception" in home["since_reset_note"]
    prize = dict(since_aud=base["pnl_since_inception_aud"], book_updated="later")
    module.attach(prize, dict(seed_aud=base["seed_aud"] + 1000, pnl_since_inception_aud=prize["since_aud"]), tmp_path / "display_epoch.json")
    assert prize["since_reset_aud"] == 0
    assert "Capital (seed) has changed" in prize["since_reset_note"]
    bare = dict(since_aud=-1, since_reset_aud=0, since_reset_txt="A$0")
    module.attach(bare, {}, tmp_path / "missing.json")
    assert "since_reset_aud" not in bare


def test_failed_replace_leaves_no_mark(tmp_path, monkeypatch):
    module = load_epoch()
    base = dict(SYNTHETIC)

    def boom(*_args, **_kwargs):
        raise OSError("injected")

    monkeypatch.setattr(module.os, "replace", boom)
    try:
        module.establish(tmp_path, base, module.CONFIRM)
    except OSError:
        pass
    else:
        raise AssertionError("replace failure must not report success")
    assert not (tmp_path / "display_epoch.json").exists()
    assert list(tmp_path.glob(".epoch-*")) == []


def test_wrong_token_and_book_are_untouched(tmp_path):
    module = load_epoch()
    before = sha256(ROOT / "veteran_desk/book.py")
    assert before == BOOK
    try:
        module.establish(tmp_path, dict(SYNTHETIC), "nope")
    except ValueError:
        pass
    else:
        raise AssertionError("bad token must be refused")
    assert not (tmp_path / "display_epoch.json").exists()
    assert sha256(ROOT / "veteran_desk/book.py") == before
    dash = (ROOT / "veteran_desk/thf_dash.py").read_text(encoding="utf-8")
    post = dash.split("def _fm_post", 1)[1].split("H.do_POST", 1)[0]
    assert "display_epoch" not in post
    assert "/api/display" not in dash


def test_cli_and_package_refuse_production(tmp_path):
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    baseline = tmp_path / "synthetic.json"
    baseline.write_text(json.dumps(SYNTHETIC), encoding="utf-8")
    run = subprocess.run(
        [sys.executable, "-m", "veteran_desk.thf_display_epoch", "establish", "--state-dir", str(tmp_path / "state"), "--baseline", str(baseline), "--confirm", "nope"],
        cwd=ROOT, text=True, capture_output=True, check=False, env=env,
    )
    assert run.returncode == 2 and "NOT_APPLIED" in run.stdout
    refused = subprocess.run([sys.executable, str(INSTALL), "--root", "/opt/tfo/dash/base"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert refused.returncode == 2 and "REFUSED_ROOT" in refused.stdout
    live = subprocess.run([sys.executable, str(INSTALL), "--root", "/opt/tfo/live/thf"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert live.returncode == 2 and "REFUSED_ROOT" in live.stdout
    text = INSTALL.read_text(encoding="utf-8")
    assert "systemctl" not in text and "ssh " not in text


def test_staging_apply_rolls_back_and_a_partial_copy_writes_no_receipt(tmp_path):
    manifest = json.loads((PACKAGE / "MANIFEST.json").read_text(encoding="utf-8"))
    live = tmp_path / "stage"
    dash = live / "veteran_desk/thf_dash.py"
    dash.parent.mkdir(parents=True)
    base_bytes = subprocess.check_output(["git", "show", "e3aa0b7:veteran_desk/thf_dash.py"], cwd=ROOT)
    assert hashlib.sha256(base_bytes).hexdigest() == manifest["replace"][0]["base_sha256"]
    dash.write_bytes(base_bytes)
    book = live / "veteran_desk/book.py"
    book.write_bytes((ROOT / "veteran_desk/book.py").read_bytes())
    pin = tmp_path / "pin.json"
    recorded = subprocess.run([sys.executable, str(INSTALL), "--root", str(live), "--staging", "--record-capture", "--out", str(pin)], cwd=ROOT, text=True, capture_output=True, check=False)
    assert recorded.returncode == 0, recorded.stdout
    applied = subprocess.run([sys.executable, str(INSTALL), "--root", str(live), "--pin", str(pin), "--staging", "--apply"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert applied.returncode == 0, applied.stdout
    assert "STAGED" in applied.stdout and "NOT_DEPLOYED" in applied.stdout
    assert sha256(live / "veteran_desk/thf_dash.py") == manifest["replace"][0]["target_sha256"]
    assert (live / "veteran_desk/thf_display_epoch.py").is_file()
    assert not (live / "state/thf/display_epoch.json").exists()
    assert sha256(live / "veteran_desk/book.py") == BOOK
    rolled = subprocess.run([sys.executable, str(INSTALL), "--root", str(live), "--staging", "--rollback"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert rolled.returncode == 0, rolled.stdout
    assert sha256(live / "veteran_desk/thf_dash.py") == manifest["replace"][0]["base_sha256"]
    assert not (live / "veteran_desk/thf_display_epoch.py").exists()
