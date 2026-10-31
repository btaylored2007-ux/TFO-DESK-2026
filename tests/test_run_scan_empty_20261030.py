"""tforul 2026-10-30: run_scan empty frame -> logged SystemExit(2), never AttributeError. PAPER."""
import os, sys, importlib.util, pytest
import pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def _rs():
    p = os.path.join(ROOT, "state", "calendar", "ranker", "run_scan.py")
    if not os.path.exists(p): p = os.path.join(ROOT, "staged_fixes", "tforul", "ranker", "run_scan.py")
    spec = importlib.util.spec_from_file_location("run_scan_t", p)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def test_empty_frame_exits_2(capsys):
    m = _rs()
    with pytest.raises(SystemExit) as e: m.empty_scan_guard(pd.DataFrame([]))
    assert e.value.code == 2 and "EMPTY scan frame" in capsys.readouterr().err
    assert m.FEEDS["scan.rows"]["ok"] is False
def test_nonempty_frame_passes():
    m = _rs(); R = pd.DataFrame([dict(sym="A", venue="ibkr")])
    assert m.empty_scan_guard(R) is R
