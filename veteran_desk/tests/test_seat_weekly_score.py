import datetime as dt, json, os, sqlite3, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import seat_weekly_score as W, pit as P
U = dt.timezone.utc
def test_forward_weekly_vs_bench_no_lookahead():
    t = tempfile.mkdtemp(); db = os.path.join(t, "p.sqlite"); c = P.con(db)
    P.put_bars(c, "XLE", [("2026-10-16", 1, 1, 1, 100.0, 1), ("2026-10-23", 1, 1, 1, 102.0, 1), ("2026-10-26", 1, 1, 1, 200.0, 1)], "t"); c.commit(); c.close()
    bp = os.path.join(t, "b.json"); sp = os.path.join(t, "s.jsonl"); op = os.path.join(t, "o.json")
    def book(eq): json.dump(dict(seats=dict(energy=dict(seed_aud=5e5, equity_aud=eq, closed=[]))), open(bp, "w"))
    book(500000); W.snapshot(dt.datetime(2026, 10, 18, 12, tzinfo=U), bp, sp, db)          # Sun wk of 10-12: bench 100
    book(505000); W.snapshot(dt.datetime(2026, 10, 24, 12, tzinfo=U), bp, sp, db)          # Sat wk of 10-19: bench 102
    book(505000); assert W.snapshot(dt.datetime(2026, 10, 24, 12, 30, tzinfo=U), bp, sp, db) is None   # <1h dedupe
    o = W.score(dt.datetime(2026, 10, 28, tzinfo=U), bp, sp, op)["seats"]["energy"]
    wk = [r for r in o["weeks"] if r["week_start_utc"].startswith("2026-10-19")][0]
    assert wk["seat_ret"] == 0.01 and wk["bench_ret"] == 0.02 and wk["excess"] == -0.01 and wk["complete"], wk
    assert o["verdict"].startswith("NO VERDICT") and json.load(open(op))["forward_only"]
if __name__ == "__main__":
    for k, v in dict(globals()).items():
        if k.startswith("test_"): v(); print("PASS", k)
