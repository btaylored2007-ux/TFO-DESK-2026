"""No installer on this branch has an owner-approval switch. This does not apply anything."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NAMED = (
    "deploy/empty_prompt_20261107/install.py",
    "deploy/review_budget_20261107/install.py",
)
OLDER = (
    "deploy/incremental_20261106/install.py",
    "deploy/chatgpt_stall_20261106/install.py",
    "deploy/chatgpt_maintenance_guard_20261106/install.py",
    "deploy/runtime_20261106/install.py",
    "deploy/dash_bind_20261106/install.py",
)


def _flags(text):
    return re.findall(r'add_argument\("--([^"]+)"', text)


def _production_body(text):
    start = text.index("def production_root")
    end = text.index("\ndef ", start + 1)
    return text[start:end]


def test_named_packages_cannot_be_unlocked_by_a_flag_or_environment():
    for rel in NAMED:
        text = (REPO / rel).read_text()
        assert _flags(text) == ["root", "staging", "apply", "rollback"]
        body = _production_body(text)
        assert "/opt/tfo/live" in body and "/opt/tfo/dash" in body
        assert "os.environ" not in body
        assert "approv" not in body.lower()


def test_older_apply_scripts_are_not_an_owner_approval_route():
    for rel in OLDER:
        text = (REPO / rel).read_text()
        assert "def production_root" not in text
        assert "approv" not in " ".join(_flags(text)).lower()
        assert "--apply" in text
