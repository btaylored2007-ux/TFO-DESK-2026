"""Staging receipt for the two paper packages. Does not copy to /opt/tfo and does not add an installer."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from veteran_desk import book as BK

REPO = Path(__file__).resolve().parents[2]
EMPTY = REPO / "deploy" / "empty_prompt_20261107"
BUDGET = REPO / "deploy" / "review_budget_20261107"
BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
CAPTURE = "1cfe1b911954c3d16fcb0cd103a39031eda73f7737bf25091405b6b465250f67"

# Exact commands. Production roots are refused by the installers already on this branch.
STAGING_COMMANDS = (
    "python3 deploy/empty_prompt_20261107/install.py --root {root}",
    "python3 deploy/empty_prompt_20261107/install.py --root {root} --staging --apply",
    "python3 deploy/review_budget_20261107/install.py --root {root}",
    "python3 deploy/review_budget_20261107/install.py --root {root} --staging --apply",
)
REFUSED_COMMANDS = (
    "python3 deploy/empty_prompt_20261107/install.py --root /opt/tfo/live/thf --staging --apply",
    "python3 deploy/empty_prompt_20261107/install.py --root /opt/tfo/dash/base --staging --apply",
    "python3 deploy/review_budget_20261107/install.py --root /opt/tfo/live/thf --staging --apply",
    "python3 deploy/review_budget_20261107/install.py --root /opt/tfo/dash/base --staging --apply",
)
# PR21 lives on cursor/retail-emerald-ui-1473. This branch does not carry those UI files.
PR21_COMMAND = "python3 deploy/retail_dash_20261107/install.py --root {root} --staging --apply"
PR21_REFUSED = "python3 deploy/retail_dash_20261107/install.py --root /opt/tfo/live/thf --staging --apply"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _run(argv):
    return subprocess.run([sys.executable, *argv], cwd=REPO, capture_output=True, text=True)


def _stage(tmp_path):
    root = tmp_path / "capture"
    for package in (EMPTY, BUDGET):
        for path in (package / "base").rglob("*"):
            if path.is_file():
                dest = root / path.relative_to(package / "base")
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    shutil.copy(path, dest)
    shutil.copy(REPO / "veteran_desk" / "book.py", root / "veteran_desk" / "book.py")
    return root


def test_quoted_owner_words_do_not_cover_the_nineteen():
    quoted = " ".join(str(row.get("words") or "") for row in BK.RULE_LOG).lower()
    assert "giveback" not in quoted
    assert "grade b" not in quoted
    assert "stale" not in quoted
    assert "half size" not in quoted
    cost = next(row for row in BK.RULE_LOG if row.get("id") == "tfo_costmodel_20261030_v2")
    assert "giveback" in cost["text"].lower() and "held" in cost["text"].lower()
    bank = next(row for row in BK.RULE_LOG if row.get("id") == "bank07_20261106")
    assert "words" not in bank and "0.7" in bank["rule"]
    assert BK.RAIL_BANK_R == 0.7
    assert _sha(REPO / "veteran_desk" / "book.py") == BOOK


def test_staging_sequence_writes_both_receipts_and_refuses_production(tmp_path):
    root = _stage(tmp_path)
    dry = _run(["deploy/empty_prompt_20261107/install.py", "--root", str(root)])
    assert dry.returncode == 0 and "DRY_RUN" in dry.stdout and "NOT_DEPLOYED" in dry.stdout
    assert "REPLACE veteran_desk/thf_shadow_sampler.py" in dry.stdout
    first = _run(["deploy/empty_prompt_20261107/install.py", "--root", str(root), "--staging", "--apply"])
    assert first.returncode == 0 and "STAGED" in first.stdout and "NOT_DEPLOYED" in first.stdout
    receipt = json.loads((root / "state/thf/empty_prompt_20261107_receipt.json").read_text())
    assert receipt["production"] is False and receipt["deployed"] is False
    assert receipt["source_capture_sha256"] == CAPTURE and len(receipt["replaced"]) == 3
    second_dry = _run(["deploy/review_budget_20261107/install.py", "--root", str(root)])
    assert second_dry.returncode == 0 and "REPLACE veteran_desk/thf_shadow_sampler.py" in second_dry.stdout
    assert "UNCHANGED veteran_desk/chatgpt_bridge.py" in second_dry.stdout
    second = _run(["deploy/review_budget_20261107/install.py", "--root", str(root), "--staging", "--apply"])
    assert second.returncode == 0 and "STAGED" in second.stdout and "NOT_DEPLOYED" in second.stdout
    budget = json.loads((root / "state/thf/review_budget_20261107_receipt.json").read_text())
    assert budget["production"] is False and budget["deployed"] is False
    assert budget["depends_on_package"] == "empty_prompt_20261107"
    assert budget["replaced"] == ["veteran_desk/thf_shadow_sampler.py"]
    assert _sha(root / "veteran_desk/thf_shadow_sampler.py") == json.loads((BUDGET / "MANIFEST.json").read_text())["payloads"][0]["target_sha256"]
    assert _sha(root / "veteran_desk/book.py") == BOOK
    for command in REFUSED_COMMANDS:
        argv = command.split()[1:]
        refused = _run(argv)
        assert refused.returncode == 2 and "REFUSED_ROOT" in refused.stdout and "NOT_DEPLOYED" in refused.stdout
    assert not (REPO / "deploy" / "retail_dash_20261107" / "install.py").exists()
