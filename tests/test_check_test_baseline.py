"""CI and safe_deploy share one baseline rule: known reds may stay, new reds fail."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools.check_test_baseline as C


def _run(tmp_path, known, log):
    base = tmp_path / "baseline.json"
    out = tmp_path / "pytest.txt"
    base.write_text(json.dumps({"known_failures": known}))
    out.write_text(log)
    return C.main(["check_test_baseline.py", str(base), str(out)])


def test_subset_of_the_baseline_passes(tmp_path):
    log = "FAILED tests/a.py::test_a - AssertionError: x\n1 failed, 10 passed in 0.2s\n"
    assert _run(tmp_path, ["tests/a.py::test_a", "tests/b.py::test_b"], log) == 0


def test_a_failure_outside_the_baseline_fails(tmp_path):
    log = (
        "FAILED tests/a.py::test_a - AssertionError: x\n"
        "FAILED tests/new.py::test_new - AssertionError: y\n"
        "2 failed, 10 passed in 0.2s\n"
    )
    assert _run(tmp_path, ["tests/a.py::test_a"], log) == 1


def test_a_fixed_known_name_still_passes(tmp_path):
    log = "10 passed in 0.2s\n"
    assert _run(tmp_path, ["tests/a.py::test_a"], log) == 0


def test_no_summary_means_pytest_did_not_run(tmp_path):
    assert _run(tmp_path, [], "interrupted\n") == 2


def test_error_nodes_count_the_same_way(tmp_path):
    log = "ERROR veteran_desk/tests/e.py::test_e - TimeoutError\n1 error in 0.1s\n"
    assert _run(tmp_path, [], log) == 1
    assert _run(tmp_path, ["veteran_desk/tests/e.py::test_e"], log) == 0
