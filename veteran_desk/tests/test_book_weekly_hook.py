import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import seat_weekly_score as WS
from veteran_desk.tests import test_book_pretrade as T
def test_tick_writes_weekly_beside_temp_book_only():
    real = [open(p).read() if os.path.exists(p) else None for p in (WS.SNAP, WS.OUT)]
    D = T._run(dict(T.E), lambda *a, **k: dict(ok=True, why="cash long")); assert "weekly_score_error" not in D, D.get("weekly_score_error")
    assert os.path.exists(os.path.join(D["_tmp"], "b.json.snapshots.jsonl"))
    assert [open(p).read() if os.path.exists(p) else None for p in (WS.SNAP, WS.OUT)] == real
