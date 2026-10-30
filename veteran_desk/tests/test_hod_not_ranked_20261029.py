import os, sys, tempfile, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import payouts as PY, leaderboard as LB, dash_server as DS
def test_head_of_desk_not_in_pay_ranking():
    assert "head_of_desk" in PY.NO_BOOK_SEATS and "head_of_desk" in LB.EXCLUDE and "head_of_desk" in DS.RANK_HIDDEN
    tmp = tempfile.mkdtemp(); B = dict(cash_aud=1_000_000.0)
    out = PY.accrue(B, fwd_path=os.path.join(tmp, "f.json"), score_path=os.path.join(tmp, "s.json"), out_path=os.path.join(tmp, "o.json"))
    assert all(r["seat"] != "head_of_desk" for r in out["ranked"]) and [r["rank"] for r in out["ranked"]] == list(range(1, len(out["ranked"]) + 1))
