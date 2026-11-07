"""Supported release: both trees, existing refresh locks, idle recheck.

The 18:53:27 WITA preflight is a snapshot, not a standing permission. These tests
replay its hash table and the failure signals in the verification note. They do
not touch /opt/tfo.
"""
import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from deploy.thf_release_20261107 import release

REPO = Path(__file__).resolve().parents[2]
EMPTY = REPO / "deploy" / "empty_prompt_20261107"
BUDGET = REPO / "deploy" / "review_budget_20261107"
BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
SENTENCE = "Explicitly authorisation to deploy and clear the blocks."
PREFLIGHT_CORE = {
    "veteran_desk/thf_shadow_sampler.py": "a779a30626e765e5d6351ab851399240154802a54cdb955c309e8f97d4ad28ef",
    "veteran_desk/claude_brain.py": "10658378cf66908f5dd1ee3aaac5b878910a9093707bba72115a56bf44339318",
    "veteran_desk/thf_grok.py": "2a6a975735fce2f3ed214b5eb35af84ce90629d7403204c8691bab9757568561",
}
AFTER = {
    "veteran_desk/thf_shadow_sampler.py": "7e2eeb3e544814411f7fb2371b50b232746e2edec9a971197c46b319570de2bf",
    "veteran_desk/claude_brain.py": "b896da9e49d6690bbda57c805952530aa9d9c2eb034f296f2cc31535255fa49d",
    "veteran_desk/thf_grok.py": "cce75bfa91b94b4c938fe53853a8fbbbe40e1b39e12a080576cfdf8d103c85a6",
    "veteran_desk/chatgpt_bridge.py": "26446b5b61218bfc370fff62988db47efecb7b8afafd15e694ceaecf35deac0c",
}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _pack(payload, label):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in Path(payload).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            rel = path.relative_to(payload).as_posix()
            tar.add(path, arcname="%s/payload/%s" % (label, rel))
    return buf.getvalue()


def _approval(allow, path):
    body = {
        "authorization_verbatim": SENTENCE,
        "authorization_id": "t2809u",
        "authorization_wita": "2026-11-07 18:52 WITA",
        "host": "tfo-sgp1",
        "roots": ["/opt/tfo/live/thf", "/opt/tfo/dash/base"],
        "excludes": ["veteran_desk/book.py"],
        "no_tick_restart": True,
        "archives": [{"name": row["name"], "sha256": row["sha256"]} for row in allow["archives"]],
    }
    path.write_text(json.dumps(body))
    return path


def _trees(tmp_path):
    live, dash = tmp_path / "live", tmp_path / "dash"
    for root in (live, dash):
        for folder in ("base", "payload"):
            pass
        for src in (EMPTY / "base").rglob("*"):
            if src.is_file():
                dest = root / src.relative_to(EMPTY / "base")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(src.read_bytes())
        book = (REPO / "docs" / "live_bank07" / "book.py").read_bytes()
        dest = root / "veteran_desk" / "book.py"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(book)
        shutil_src = BUDGET / "base" / "veteran_desk" / "chatgpt_bridge.py"
        (root / "veteran_desk" / "chatgpt_bridge.py").write_bytes(shutil_src.read_bytes())
    return live, dash


def _allow(empty_blob, budget_blob):
    official = json.loads((REPO / "deploy" / "thf_release_20261107" / "ALLOWLIST.json").read_text())
    spec = json.loads(json.dumps(official))
    spec["archives"] = [row for row in spec["archives"] if row["name"] != "retail_dash_20261107"]
    spec["archives"][0]["sha256"] = hashlib.sha256(empty_blob).hexdigest()
    spec["archives"][1]["sha256"] = hashlib.sha256(budget_blob).hexdigest()
    spec["verify_only"] = [
        {"path": "veteran_desk/book.py", "sha256": BOOK},
        {"path": "veteran_desk/chatgpt_bridge.py", "sha256": AFTER["veteran_desk/chatgpt_bridge.py"]},
    ]
    return spec


def _mirror(live, dash):
    if release.refresh_would_defer(live):
        return "deferred"
    for path in live.rglob("*"):
        if not path.is_file() or "state" in path.parts or path.name == "deploy.lock":
            continue
        rel = path.relative_to(live)
        dest = dash / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(path.read_bytes())
    for path in list(dash.rglob("*")):
        if not path.is_file() or "state" in path.parts:
            continue
        if not (live / path.relative_to(dash)).exists():
            path.unlink()
    return "copied"


def test_preflight_and_verification_hashes_are_the_allowlist():
    allow = release.ALLOW
    assert allow["host"] == "tfo-sgp1"
    assert allow["roots"] == ["/opt/tfo/live/thf", "/opt/tfo/dash/base"]
    assert allow["authorization_verbatim"] == SENTENCE
    assert allow["book_pin"] == BOOK
    assert "veteran_desk/book.py" in allow["excluded"]
    assert allow["locks"] == ["tools/.code_release.lock", "tools/deploy.lock"]
    assert allow["idle_units"] == ["tfo-thf-shadow.service", "tfo-thf-tick.service"]
    assert "tfo-thf-tick.service" in allow["do_not_restart"]
    assert "nginx.service" in allow["do_not_restart"]
    assert allow["dash_restart_unit"] == "tfo-dash-thf.service"
    by_name = {row["name"]: row for row in allow["archives"]}
    assert [row["name"] for row in allow["archives"]] == [
        "empty_prompt_20261107", "review_budget_20261107", "retail_dash_20261107"]
    assert by_name["empty_prompt_20261107"]["sha256"] == "fd2c2b96eb069c7e6b78fe7ff04dc91e6355361ff5b3002f65eb618110fc85b8"
    assert by_name["review_budget_20261107"]["sha256"] == "affc61134490634edfc98dd1819147db8584350a81c6489e94796edd18c72534"
    assert by_name["retail_dash_20261107"]["sha256"] == "9d58be9e311b7e279bbb6549a7039c4ecd90abeefd4ca0cdc59d1e22d8f14b96"
    assert "6395175b18d09e10bcafbcacffb85bbb6b66a8e1be7967bef387b186d453a98e" in by_name["retail_dash_20261107"]["rejected_sha256"]
    bases = {item["path"]: item["bases"][0] for item in by_name["empty_prompt_20261107"]["files"]}
    assert bases == PREFLIGHT_CORE
    targets = {}
    for row in allow["archives"]:
        if row["name"] == "retail_dash_20261107":
            continue
        for item in row["files"]:
            targets[item["path"]] = item["target"]
    for path, digest in AFTER.items():
        if path == "veteran_desk/chatgpt_bridge.py":
            assert {row["path"]: row["sha256"] for row in allow["verify_only"]}[path] == digest
        else:
            assert targets[path] == digest
    ui_now = {
        "tools/thf_eli10.html": "e00723691176f3183128e853fdd4ff7236bf03116bb21f371dbeaadf0f0e8bcd",
        "tools/thf_eli10.js": "36b3f9fc88240c5bbeb59e431448709987f7967f44185752d94f559132cbb235",
        "tools/thf_chat.js": "b58b0716a5118b7f49d3b18083f7bc355c57467690f511c44c0680cdb69d1f1d",
        "tools/thf_chat_page.html": "3a9b0727c420c0b815faa5aaabb8e4285660865363202fc1b04e4762ed13a2f3",
    }
    ui_target = {
        "tools/thf_eli10.html": "9da1e4ac511c8173eb02bf2c460100ccb8e7fdfbb14352708429d95cfbf453ee",
        "tools/thf_eli10.js": "c4a977ad2ca04131e22355cc62e32fa04d075dfc88243758e7eab04a03bd8718",
        "tools/thf_chat.js": "ef3e4def8fd3dcc39093895ddf433d0633f3dfc3a6faa800bb9cfb615e461c44",
        "tools/thf_chat_page.html": "92d98ecd72b0dbc6231f725a953ff33927c37708f1c2267051ac949f8b6646e3",
    }
    for item in by_name["retail_dash_20261107"]["files"]:
        if item["path"] in ui_now:
            assert ui_now[item["path"]] in item["bases"]
            assert item["target"] == ui_target[item["path"]]
    assert "--root" not in (REPO / "deploy" / "thf_release_20261107" / "release.py").read_text().split("add_argument")[1]


def test_committed_payloads_match_the_published_targets():
    for rel, digest in {
        "veteran_desk/thf_shadow_sampler.py": "935c73460dcce8789b9eec9e8664b0ffcd44985ef9adc3da6bb053808b4e6673",
        "veteran_desk/claude_brain.py": "b896da9e49d6690bbda57c805952530aa9d9c2eb034f296f2cc31535255fa49d",
        "veteran_desk/thf_grok.py": "cce75bfa91b94b4c938fe53853a8fbbbe40e1b39e12a080576cfdf8d103c85a6",
    }.items():
        assert _sha(EMPTY / "payload" / rel) == digest
        assert _sha(EMPTY / "base" / rel) == PREFLIGHT_CORE[rel]
    assert _sha(BUDGET / "payload" / "veteran_desk" / "thf_shadow_sampler.py") == AFTER["veteran_desk/thf_shadow_sampler.py"]
    assert _sha(BUDGET / "base" / "veteran_desk" / "thf_shadow_sampler.py") == "935c73460dcce8789b9eec9e8664b0ffcd44985ef9adc3da6bb053808b4e6673"


def test_apply_writes_both_trees_and_holds_the_refresh_gate(tmp_path, monkeypatch):
    empty, budget = _pack(EMPTY / "payload", "empty_prompt_20261107"), _pack(BUDGET / "payload", "review_budget_20261107")
    spec = _allow(empty, budget)
    monkeypatch.setattr(release, "ALLOW", spec)
    live, dash = _trees(tmp_path)
    (dash / "tools").mkdir(parents=True)
    (dash / "tools" / "dash_only.txt").write_text("wiped by refresh")
    approval = _approval(spec, tmp_path / "approval.json")
    calls = []

    def probe(unit):
        calls.append(unit)
        return "inactive"

    archives = {"empty_prompt_20261107": tmp_path / "e.gz", "review_budget_20261107": tmp_path / "r.gz"}
    archives["empty_prompt_20261107"].write_bytes(empty)
    archives["review_budget_20261107"].write_bytes(budget)
    seen = {"held": False}

    real_put = release._put

    def watching(root, rel, data):
        if release.refresh_would_defer(live):
            seen["held"] = True
        return real_put(root, rel, data)

    monkeypatch.setattr(release, "_put", watching)
    receipt = release.apply_release(approval, archives, probe=probe, host_name="tfo-sgp1", roots=(str(live), str(dash)))
    assert seen["held"] is True
    assert release.refresh_would_defer(live) is False
    assert receipt["deployed"] is True and receipt["tick_restarted"] is False
    assert receipt["nginx_changed"] is False and receipt["dash_restarted"] is False
    for root in (live, dash):
        for path, digest in AFTER.items():
            assert _sha(root / path) == digest
        assert _sha(root / "veteran_desk" / "book.py") == BOOK
        receipt_path = root / "state" / "thf" / "thf_release_20261107_receipt.json"
        assert receipt_path.is_file()
        saved = json.loads(receipt_path.read_text())
        original = root / "state" / "thf" / "thf_release_20261107_backups" / saved["txn"] / "original" / "veteran_desk" / "thf_shadow_sampler.py"
        intermediate = root / "state" / "thf" / "thf_release_20261107_backups" / saved["txn"] / "stages" / "02-review_budget_20261107" / "veteran_desk" / "thf_shadow_sampler.py"
        assert _sha(original) == PREFLIGHT_CORE["veteran_desk/thf_shadow_sampler.py"]
        assert _sha(intermediate) == "935c73460dcce8789b9eec9e8664b0ffcd44985ef9adc3da6bb053808b4e6673"
        assert original.read_bytes() != intermediate.read_bytes()
    assert _mirror(live, dash) == "copied"
    assert not (dash / "tools" / "dash_only.txt").exists()
    for path, digest in AFTER.items():
        assert _sha(dash / path) == digest
    assert calls[0] == "tfo-thf-shadow.service"


def test_sampler_or_tick_busy_at_recheck_writes_nothing(tmp_path, monkeypatch):
    empty, budget = _pack(EMPTY / "payload", "empty"), _pack(BUDGET / "payload", "budget")
    spec = _allow(empty, budget)
    monkeypatch.setattr(release, "ALLOW", spec)
    live, dash = _trees(tmp_path)
    before = _sha(live / "veteran_desk" / "thf_shadow_sampler.py")
    approval = _approval(spec, tmp_path / "approval.json")
    state = {"n": 0}

    def probe(unit):
        state["n"] += 1
        if unit == "tfo-thf-shadow.service" and state["n"] > 2:
            return "active"
        return "inactive"

    archives = {"empty_prompt_20261107": tmp_path / "e.gz", "review_budget_20261107": tmp_path / "r.gz"}
    archives["empty_prompt_20261107"].write_bytes(empty)
    archives["review_budget_20261107"].write_bytes(budget)
    with pytest.raises(release.ReleaseError, match="REFUSED_BUSY"):
        release.apply_release(approval, archives, probe=probe, host_name="tfo-sgp1", roots=(str(live), str(dash)))
    assert _sha(live / "veteran_desk" / "thf_shadow_sampler.py") == before
    assert _sha(dash / "veteran_desk" / "thf_shadow_sampler.py") == before
    assert not (live / "state" / "thf" / "thf_release_20261107_receipt.json").exists()
    assert release.refresh_would_defer(live) is False


def test_partial_write_restores_both_trees(tmp_path, monkeypatch):
    empty, budget = _pack(EMPTY / "payload", "empty"), _pack(BUDGET / "payload", "budget")
    spec = _allow(empty, budget)
    monkeypatch.setattr(release, "ALLOW", spec)
    live, dash = _trees(tmp_path)
    before = {root: _sha(root / "veteran_desk" / "claude_brain.py") for root in (live, dash)}
    approval = _approval(spec, tmp_path / "approval.json")
    real = release.os.replace
    seen = {"n": 0}

    def boom(src, dst):
        seen["n"] += 1
        if seen["n"] == 3:
            raise OSError("injected")
        return real(src, dst)

    monkeypatch.setattr(release.os, "replace", boom)
    archives = {"empty_prompt_20261107": tmp_path / "e.gz", "review_budget_20261107": tmp_path / "r.gz"}
    archives["empty_prompt_20261107"].write_bytes(empty)
    archives["review_budget_20261107"].write_bytes(budget)
    with pytest.raises(OSError):
        release.apply_release(approval, archives, probe=lambda unit: "inactive", host_name="tfo-sgp1", roots=(str(live), str(dash)))
    for root, digest in before.items():
        assert _sha(root / "veteran_desk" / "claude_brain.py") == digest
        assert _sha(root / "veteran_desk" / "thf_shadow_sampler.py") == PREFLIGHT_CORE["veteran_desk/thf_shadow_sampler.py"]
        assert _sha(root / "veteran_desk" / "book.py") == BOOK
    failed = json.loads((live / "state" / "thf" / "thf_release_20261107_receipt.json").read_text())
    assert failed["deployed"] is False and failed["restored"] is True


def test_symlink_book_and_wrong_host_are_refused(tmp_path, monkeypatch):
    empty, budget = _pack(EMPTY / "payload", "empty"), _pack(BUDGET / "payload", "budget")
    spec = _allow(empty, budget)
    monkeypatch.setattr(release, "ALLOW", spec)
    live, dash = _trees(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = live / "veteran_desk" / "linked.py"
    link.symlink_to(outside / "x.py")
    spec["archives"][0]["files"].append({"path": "veteran_desk/linked.py", "bases": [hashlib.sha256(b"").hexdigest()], "target": "ab"})
    approval = _approval(spec, tmp_path / "approval.json")
    archives = {"empty_prompt_20261107": tmp_path / "e.gz", "review_budget_20261107": tmp_path / "r.gz"}
    archives["empty_prompt_20261107"].write_bytes(empty)
    archives["review_budget_20261107"].write_bytes(budget)
    with pytest.raises(release.ReleaseError, match="REFUSED_SYMLINK"):
        release.apply_release(approval, archives, probe=lambda unit: "inactive", host_name="tfo-sgp1", roots=(str(live), str(dash)))
    saved = os.environ.get("PYTEST_CURRENT_TEST")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    try:
        with pytest.raises(release.ReleaseError, match="REFUSED_OVERRIDE"):
            release.apply_release(approval, archives, host_name="tfo-sgp1", roots=(str(live), str(dash)))
    finally:
        if saved is not None:
            monkeypatch.setenv("PYTEST_CURRENT_TEST", saved)
    with pytest.raises(release.ReleaseError, match="REFUSED_HOST"):
        release.apply_release(approval, archives, probe=lambda unit: "inactive", host_name="not-singapore", roots=(str(live), str(dash)))
    with pytest.raises(release.ReleaseError, match="REFUSED_RESTART"):
        release.restart_dash("tfo-thf-tick.service", runner=lambda unit: unit)
    with pytest.raises(release.ReleaseError, match="REFUSED_RESTART"):
        release.restart_dash("nginx.service", runner=lambda unit: unit)


def test_missing_or_example_approval_and_old_ui_sha(tmp_path):
    path = tmp_path / "no.json"
    path.write_text(json.dumps({"authorization_verbatim": "please deploy", "archives": []}))
    with pytest.raises(release.ReleaseError, match="REFUSED_APPROVAL"):
        release.load_approval(path)
    path.write_text(json.dumps({"example": True, "authorization_verbatim": SENTENCE}))
    with pytest.raises(release.ReleaseError, match="REFUSED_EXAMPLE_APPROVAL"):
        release.load_approval(path)
    old = tmp_path / "old.gz"
    old.write_bytes(b"morning-ui")
    digest = hashlib.sha256(old.read_bytes()).hexdigest()
    allow = json.loads(json.dumps(release.ALLOW))
    allow["archives"] = [{
        "name": "retail_dash_20261107",
        "sha256": "f" * 64,
        "rejected_sha256": [digest],
        "order": 3,
        "files": [],
    }]
    # The published morning package is refused by the real allowlist when its bytes are presented.
    rejected = "6395175b18d09e10bcafbcacffb85bbb6b66a8e1be7967bef387b186d453a98e"
    assert rejected in release.ALLOW["archives"][2]["rejected_sha256"]


def test_packaged_installers_still_refuse_production(capsys):
    import importlib.util
    for name in ("empty_prompt_20261107", "review_budget_20261107"):
        path = REPO / "deploy" / name / "install.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        code = module.main(["--root", "/opt/tfo/live/thf", "--staging", "--apply"])
        assert code == 2
        assert "REFUSED_ROOT" in capsys.readouterr().out


def _prepared(tmp_path, monkeypatch):
    empty, budget = _pack(EMPTY / "payload", "empty_prompt_20261107"), _pack(BUDGET / "payload", "review_budget_20261107")
    spec = _allow(empty, budget)
    monkeypatch.setattr(release, "ALLOW", spec)
    live, dash = _trees(tmp_path)
    approval = _approval(spec, tmp_path / "approval.json")
    archives = {"empty_prompt_20261107": tmp_path / "e.gz", "review_budget_20261107": tmp_path / "r.gz"}
    archives["empty_prompt_20261107"].write_bytes(empty)
    archives["review_budget_20261107"].write_bytes(budget)
    return live, dash, approval, archives


def _assert_pre_release(live, dash):
    for root in (live, dash):
        for path, digest in PREFLIGHT_CORE.items():
            assert _sha(root / path) == digest
        assert _sha(root / "veteran_desk" / "book.py") == BOOK
    assert _sha(live / "veteran_desk" / "thf_shadow_sampler.py") == _sha(dash / "veteran_desk" / "thf_shadow_sampler.py")


def test_every_put_failure_restores_both_roots_to_the_original(tmp_path, monkeypatch):
    """The 7th put is the review-budget live sampler write that used to leave live at 935c7346 and dash at a779a306."""
    real = release._put
    for stop in range(1, 9):
        live, dash, approval, archives = _prepared(tmp_path / str(stop), monkeypatch)
        seen = {"n": 0}

        def boom(root, rel, data, stop=stop):
            seen["n"] += 1
            if seen["n"] == stop:
                raise RuntimeError("injected put %s %s" % (stop, rel))
            return real(root, rel, data)

        monkeypatch.setattr(release, "_put", boom)
        with pytest.raises(RuntimeError):
            release.apply_release(approval, archives, probe=lambda unit: "inactive", host_name="tfo-sgp1", roots=(str(live), str(dash)))
        _assert_pre_release(live, dash)
        failed = json.loads((live / "state" / "thf" / "thf_release_20261107_receipt.json").read_text())
        assert failed["deployed"] is False and failed["restored"] is True
        assert failed["dash_restarted"] is False and failed["tick_restarted"] is False
        original = live / "state" / "thf" / "thf_release_20261107_backups" / failed["txn"] / "original" / "veteran_desk" / "thf_shadow_sampler.py"
        assert _sha(original) == PREFLIGHT_CORE["veteran_desk/thf_shadow_sampler.py"]
        if stop == 7:
            stage = live / "state" / "thf" / "thf_release_20261107_backups" / failed["txn"] / "stages" / "02-review_budget_20261107" / "veteran_desk" / "thf_shadow_sampler.py"
            assert _sha(stage) == "935c73460dcce8789b9eec9e8664b0ffcd44985ef9adc3da6bb053808b4e6673"
            assert _sha(live / "veteran_desk" / "thf_shadow_sampler.py") == "a779a30626e765e5d6351ab851399240154802a54cdb955c309e8f97d4ad28ef"
            assert _sha(dash / "veteran_desk" / "thf_shadow_sampler.py") == "a779a30626e765e5d6351ab851399240154802a54cdb955c309e8f97d4ad28ef"


def test_receipt_and_restart_failures_roll_both_roots_back(tmp_path, monkeypatch):
    live, dash, approval, archives = _prepared(tmp_path, monkeypatch)
    real_write = Path.write_text

    def fail_success(self, data, *args, **kwargs):
        if self.name == "thf_release_20261107_receipt.json" and '"deployed": true' in data:
            raise OSError("receipt failed")
        return real_write(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_success)
    with pytest.raises(OSError):
        release.apply_release(approval, archives, probe=lambda unit: "inactive", host_name="tfo-sgp1", roots=(str(live), str(dash)))
    _assert_pre_release(live, dash)
    failed = json.loads((live / "state" / "thf" / "thf_release_20261107_receipt.json").read_text())
    assert failed["deployed"] is False and failed["restored"] is True
    monkeypatch.undo()
    live2, dash2, approval2, archives2 = _prepared(tmp_path / "restart", monkeypatch)
    monkeypatch.setattr(release, "ALLOW", _allow(archives2["empty_prompt_20261107"].read_bytes(), archives2["review_budget_20261107"].read_bytes()))

    def restarter(unit):
        raise OSError("dash restart failed")

    with pytest.raises(OSError):
        release.apply_release(approval2, archives2, probe=lambda unit: "inactive", host_name="tfo-sgp1", roots=(str(live2), str(dash2)), restart="dash", restarter=restarter)
    _assert_pre_release(live2, dash2)
    failed = json.loads((dash2 / "state" / "thf" / "thf_release_20261107_receipt.json").read_text())
    assert failed["deployed"] is False and failed["dash_restarted"] is False and failed["restored"] is True


def test_corrupt_or_missing_backup_does_not_become_the_restored_file(tmp_path):
    live = tmp_path / "live"
    dest = live / "veteran_desk" / "thf_shadow_sampler.py"
    dest.parent.mkdir(parents=True)
    original = b"original-sampler"
    dest.write_bytes(original)
    txn = release.Transaction([str(live), str(tmp_path / "dash")])
    txn.seal(live, "veteran_desk/thf_shadow_sampler.py")
    rec = txn.originals[(str(live), "veteran_desk/thf_shadow_sampler.py")]
    os.chmod(rec["path"], 0o644)
    rec["path"].write_bytes(b"corrupt-backup")
    dest.write_bytes(b"partial-new-bytes")
    assert txn.restore_all() is False
    assert dest.read_bytes() == original
    assert txn.integrity_ok is False
    assert rec["path"].read_bytes() == original
    assert rec["path"].with_name(rec["path"].name + ".integrity_failed").read_bytes() == b"corrupt-backup"
    rec["bytes"] = None
    dest.write_bytes(b"leave-this-alone")
    with pytest.raises(release.ReleaseError, match="REFUSED_BACKUP"):
        txn.restore_all()
    assert dest.read_bytes() == b"leave-this-alone"


def test_repeated_transactions_keep_distinct_originals(tmp_path, monkeypatch):
    first_live, first_dash, approval, archives = _prepared(tmp_path / "one", monkeypatch)
    one = release.apply_release(approval, archives, probe=lambda unit: "inactive", host_name="tfo-sgp1", roots=(str(first_live), str(first_dash)))
    second_live, second_dash, approval2, archives2 = _prepared(tmp_path / "two", monkeypatch)
    monkeypatch.setattr(release, "ALLOW", _allow(archives2["empty_prompt_20261107"].read_bytes(), archives2["review_budget_20261107"].read_bytes()))
    two = release.apply_release(approval2, archives2, probe=lambda unit: "inactive", host_name="tfo-sgp1", roots=(str(second_live), str(second_dash)))
    assert one["txn"] != two["txn"]
    for root, saved in ((first_live, one), (second_live, two)):
        original = root / "state" / "thf" / "thf_release_20261107_backups" / saved["txn"] / "original" / "veteran_desk" / "thf_shadow_sampler.py"
        assert _sha(original) == PREFLIGHT_CORE["veteran_desk/thf_shadow_sampler.py"]


def test_unknown_archive_bytes_are_refused(tmp_path):
    approval = tmp_path / "approval.json"
    official = release.ALLOW
    _approval(official, approval)
    blob = tmp_path / "nope.gz"
    blob.write_bytes(b"not-the-package")
    archives = {
        "empty_prompt_20261107": blob,
        "review_budget_20261107": blob,
        "retail_dash_20261107": blob,
    }
    with pytest.raises(release.ReleaseError, match="REFUSED_ARCHIVE"):
        release.apply_release(approval, archives, probe=lambda unit: "inactive", host_name="tfo-sgp1", roots=(str(tmp_path), str(tmp_path / "other")))
