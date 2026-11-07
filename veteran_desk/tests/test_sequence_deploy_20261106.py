"""Apply the deployed increments in order. The role archive is not in this sequence."""
import hashlib
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
SEQUENCE = (
    "deploy/incremental_20261106/thf-increment-20261106-r2.tar.gz",
    "deploy/chatgpt_stall_20261106/thf-chatgpt-stall-20261106-r2.tar.gz",
    "deploy/runtime_20261106/thf-runtime-20261106.tar.gz",
    "deploy/deepseek_transport_20261106/thf-deepseek-transport-20261106.tar.gz",
    "deploy/learning_health_20261106/thf-learning-health-20261106.tar.gz",
    "deploy/clock_status_20261106/thf-clock-status-20261106.tar.gz",
)
ROLE = "deploy/role_align_20261106/thf-role-align-20261106.tar.gz"


def _extract(archive, dest):
    with tarfile.open(ROOT / archive, "r:gz") as tar:
        tar.extractall(dest)
    installs = list(Path(dest).rglob("install.py"))
    assert len(installs) == 1, archive
    return installs[0]


def _run(install, root, *extra):
    return subprocess.run([sys.executable, str(install), "--root", str(root), *extra],
                          cwd=install.parent, text=True, capture_output=True, check=False)


def test_sequence_apply_rollback_and_reapply_keeps_book_and_samples(tmp_path):
    assert ROLE not in SEQUENCE
    stage = tmp_path / "live"
    book = stage / "veteran_desk/book.py"
    book.parent.mkdir(parents=True)
    book.write_bytes((ROOT / "docs/live_bank07/book.py").read_bytes())
    sample = stage / "state/thf/shadow_samples.jsonl"
    sample.parent.mkdir(parents=True)
    sample.write_text('{"kept":true,"kind":"shadow_evaluation"}\n')
    marker = sample.read_bytes()
    installs = [_extract(archive, tmp_path / ("src-" + str(index))) for index, archive in enumerate(SEQUENCE)]

    def apply_all():
        for install in installs:
            result = _run(install, stage, "--apply")
            assert result.returncode == 0, result.stdout + result.stderr
            assert "BLOCKED" not in result.stdout
            assert hashlib.sha256(book.read_bytes()).hexdigest() == BOOK
            assert sample.read_bytes() == marker

    apply_all()
    for install in reversed(installs):
        result = _run(install, stage, "--rollback")
        assert result.returncode == 0, result.stdout + result.stderr
        assert hashlib.sha256(book.read_bytes()).hexdigest() == BOOK
        assert sample.read_bytes() == marker
    apply_all()
    assert hashlib.sha256(book.read_bytes()).hexdigest() == BOOK
    assert sample.read_bytes() == marker
