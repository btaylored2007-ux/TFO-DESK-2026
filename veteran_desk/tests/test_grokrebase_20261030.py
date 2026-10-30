import json, os
from veteran_desk import vd_engine as E, llm_backend as LB
def test_failed_round_refunds_wake(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "ST", str(tmp_path)); monkeypatch.setattr(E, "COUNT_P", str(tmp_path / "wake_counts.json"))
    (tmp_path / "wakes").mkdir(); day = E.wita().date().isoformat()
    json.dump(dict(day=day, total=3, seat={"trend": 2, "macro": 1}), open(tmp_path / "wake_counts.json", "w"))
    W = dict(wita=day + " 22:22 WITA", seats={"trend": {}, "macro": {}})
    got = E.refund_failed("W1", W, dict(seats={"trend": None, "macro": "stand_aside"}))
    C = json.load(open(tmp_path / "wake_counts.json"))
    assert got == ["trend"] and C["total"] == 2 and C["seat"]["trend"] == 1 and C["seat"]["macro"] == 1
    assert E.refund_failed("W1", W, dict(seats={"trend": None})) == []   # idempotent
def test_seat_label_uses_seat_id():
    def go(s): return LB._ctx()
    def wrap(s): return go(s)
    assert wrap({"seat": "fx_commodities", "role": "proposer"}) == "fx_commodities"
