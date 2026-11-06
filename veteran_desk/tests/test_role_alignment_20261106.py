"""Live roster evidence. This checkout does not invent seats the droplet already allows."""
import hashlib
import json
from pathlib import Path

from veteran_desk import lesson_trace as LT

LIVE = Path("/tmp/seatlive/thf-upgrade-seat-registry-20261106")
ROOT = Path(__file__).resolve().parents[2]


def test_role_sources_match_the_live_snapshot_and_are_not_relabeled():
    config = json.loads((LIVE / "live/state/thf/deepseek_api/config.safe.json").read_text())
    assert set(config["allowed_seats"]) == set(LT.SEATS)
    assert len(config["allowed_seats"]) == 18
    registry = ROOT / "veteran_desk/seat_registry.py"
    funder = ROOT / "veteran_desk/thf_funder.py"
    assert hashlib.sha256(registry.read_bytes()).hexdigest() == hashlib.sha256((LIVE / "live/veteran_desk/seat_registry.py").read_bytes()).hexdigest()
    assert hashlib.sha256(funder.read_bytes()).hexdigest() == "6a5bd148bb5ffdb81b94ef242a53f565a21388dbe5e08b949e9959c9809cca2c"
    assert "Named staff stay addressable" not in registry.read_text()
