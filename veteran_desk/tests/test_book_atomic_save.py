import json, os, sys, tempfile, glob
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
def test_atomic_roundtrip_no_tmp_left():
    d = tempfile.mkdtemp(); p = os.path.join(d, "b.json"); BK._wj(p, dict(a=1)); BK._wj(p, dict(a=2))
    assert json.load(open(p)) == dict(a=2) and not glob.glob(os.path.join(d, "*.tmp"))
def test_failed_dump_keeps_old_file():
    d = tempfile.mkdtemp(); p = os.path.join(d, "b.json"); BK._wj(p, dict(a=1))
    try: BK._wj(p, dict(a=object.__new__(type("X", (), {"__repr__": None}))))
    except Exception: pass
    assert json.load(open(p)) == dict(a=1) and not glob.glob(os.path.join(d, "*.tmp"))
