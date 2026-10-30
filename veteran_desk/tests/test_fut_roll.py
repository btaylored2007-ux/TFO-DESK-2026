"""Roll back-adjustment tests (2026-10-28). Offline: synthetic two-contract series."""
import datetime as dt, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import pit as P, fut_roll as F
def _db():
    c = P.con(os.path.join(tempfile.mkdtemp(), "f.sqlite")); F.ensure(c); rows = []; d = dt.date(2025, 1, 1); old, new = 2.70, 3.00
    while d < dt.date(2025, 3, 31):
        d += dt.timedelta(days=1)
        if d.weekday() >= 5: continue
        old *= 1.001; new *= 1.001; rolled = d >= dt.date(2025, 1, 29); px = new if rolled else old
        rows.append((d.isoformat(), px, px, px, px, 5e5 if d == dt.date(2025, 1, 29) else 1e5))
    c.executemany("INSERT INTO bars_fut_raw VALUES(?,?,?,?,?,?,?,?,?)", [("NG=F", r[0], *r[1:], P.knowable(r[0], "FUT"), "yahoo") for r in rows]); return c, rows
def test_roll_gap_removed_and_pit_kept():
    c, raw = _db(); s = F.rebuild(c, "NG=F", exact_last=False)
    adj = c.execute("SELECT date,c,knowable_utc FROM bars WHERE sym='NG=F' ORDER BY date").fetchall()
    assert len(adj) == len(raw) and adj[-1][1] == raw[-1][4]
    rets = [adj[i][1] / adj[i - 1][1] - 1 for i in range(1, len(adj))]
    assert max(abs(r) for r in rets) < 0.0015, max(rets)              # 11% roll jump gone; ~0.1% daily drift kept
    for (d, _, k) in adj: assert k == P.knowable(d, "FUT")             # availability time untouched
    assert c.execute("SELECT COUNT(*) FROM fut_rolls WHERE sym='NG=F'").fetchone()[0] >= 1
if __name__ == "__main__":
    test_roll_gap_removed_and_pit_kept(); print("PASS")
