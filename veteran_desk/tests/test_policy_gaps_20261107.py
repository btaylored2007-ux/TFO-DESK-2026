"""The 19 remaining red nodes are owner decisions. This file does not change their assertions."""
import importlib.util
from pathlib import Path

from veteran_desk import book as BK
from veteran_desk import gate_v2 as G
from veteran_desk.tests import test_gate_v2 as TG

REPO = Path(__file__).resolve().parents[2]


def test_gate_still_does_not_arm_and_still_asks_for_brett():
    row = TG.g()
    assert row["armed"] is False and row["can_arm"] is False and row["needs_brett_tick"] is True
    assert BK.RAIL_BANK_R == 0.7


def test_grade_and_floor_gaps_are_still_open():
    assert abs(BK.risk_plan(500000, "B")["pct"] - 0.01) < 1e-9
    cut = BK.risk_plan(500000, "B", mult=0.5)
    assert cut["pct"] == 0.01 and cut["reject"] is None
    crypto = BK.risk_plan(500000, "A", mult=0.2, floor=BK.RISK_CRYPTO_MIN)
    assert crypto["reject"] is None
    assert TG.g(TG.P0(grade="B"))["passed"] is True
    assert TG.g(th=dict(base_block_exp=5.0))["passed"] is True


def test_narrow_installers_still_refuse_production_roots():
    for name in ("empty_prompt_20261107", "review_budget_20261107"):
        path = REPO / "deploy" / name / "install.py"
        spec = importlib.util.spec_from_file_location("guard_" + name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for banned in ("/opt/tfo/live/thf", "/opt/tfo/dash/base"):
            assert module.production_root(banned) is True
            code = module.main(["--root", banned, "--staging", "--apply"])
            assert code == 2
