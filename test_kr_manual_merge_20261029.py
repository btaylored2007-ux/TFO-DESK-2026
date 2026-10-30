import json, os, tempfile, universe_asia as U
def test_manual_survives_rewrite():
    d = tempfile.mkdtemp(); p = os.path.join(d, "kr_events.json")
    json.dump({"earnings": {"089860.KS": "2026-11-05"}, "manual": {"089860.KS": {"date": "2026-11-05", "source": "manual"}}}, open(p, "w"))
    out = U.merge_manual_events(p, {"005930.KS": "2026-10-28"})   # Yahoo has no 089860 date
    assert out["earnings"]["089860.KS"] == "2026-11-05" and out["earnings"]["005930.KS"] == "2026-10-28"
    assert out["manual"]["089860.KS"]["source"] == "manual"
def test_yahoo_wins_and_nonmanual_dropped():
    d = tempfile.mkdtemp(); p = os.path.join(d, "kr_events.json")
    json.dump({"earnings": {"X.KS": "2026-01-01"}, "manual": {"A.KS": {"date": "2026-11-05", "source": "manual"}}}, open(p, "w"))
    out = U.merge_manual_events(p, {"A.KS": "2026-11-07"})
    assert out["earnings"] == {"A.KS": "2026-11-07"}
