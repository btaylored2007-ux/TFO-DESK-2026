"""Lookahead-leak tripwires for data/pit (run: python3 -m pytest -q veteran_desk/tests or python3 veteran_desk/tests/test_pit_leak.py)."""
import datetime as dt, os, random, sqlite3, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import pit as P
UTC = dt.timezone.utc
def _synthetic():
    c = P.con(os.path.join(tempfile.mkdtemp(), "t.sqlite")); random.seed(7); px = 100.0; rows = []
    d = dt.date(2020, 1, 1)
    for i in range(800):
        d += dt.timedelta(days=1); px *= 1 + random.gauss(0, 0.01); rows.append((d.isoformat(), px, px * 1.01, px * .99, px, 1e6))
    P.put_bars(c, "SYN", rows, "test"); return c
def test_asof_never_returns_future():
    c = _synthetic()
    for k in range(50):
        asof = dt.datetime(2020, 3, 1, tzinfo=UTC) + dt.timedelta(days=13 * k, hours=k)
        for r in P.bars_asof(c, "SYN", asof): assert r[6] <= asof.isoformat()
def test_same_day_bar_hidden_before_close():
    c = _synthetic(); d = "2021-06-15"
    assert all(r[0] < d for r in P.bars_asof(c, "SYN", dt.datetime(2021, 6, 15, 12, 0, tzinfo=UTC)))
    assert P.bars_asof(c, "SYN", dt.datetime(2021, 6, 15, 21, 0, tzinfo=UTC))[-1][0] == d
def _strategy_score(c, shift):
    """Sign-of-yesterday-return strategy. shift=+1 cheats (uses tomorrow) -> must look much better."""
    closes = [r[4] for r in c.execute("SELECT * FROM bars WHERE sym='SYN' ORDER BY date")]
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    return sum((1 if rets[i - 1 + shift] > 0 else -1) * rets[i] for i in range(1, len(rets) - 1)) / len(rets)
def test_shifted_feed_must_improve_and_shuffled_is_zero():
    c = _synthetic(); honest, cheat = _strategy_score(c, 0), _strategy_score(c, 1)
    assert cheat > honest + 0.004, (honest, cheat)          # tripwire (b): leak must be detectable
    closes = [r[4] for r in c.execute("SELECT * FROM bars WHERE sym='SYN' ORDER BY date")]
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]; random.seed(1)
    sig = [1 if x > 0 else -1 for x in rets]; random.shuffle(sig)
    assert abs(sum(s * r for s, r in zip(sig, rets)) / len(rets)) < 0.0015   # tripwire (a): shuffled labels ~0
def test_indonesian_banned():
    c = _synthetic()
    try: P.put_bars(c, "BBCA.JK", [("2024-01-02", 1, 1, 1, 1, 1)], "t"); assert False
    except ValueError: pass
def test_live_store_has_no_future_rows_and_bias_flags():
    if not os.path.exists(P.DB): return
    c = sqlite3.connect(P.DB); now = dt.datetime.now(UTC).isoformat()
    assert c.execute("SELECT COUNT(*) FROM bars WHERE knowable_utc<date").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM bars WHERE date>?", (now[:10],)).fetchone()[0] == 0
    flags = dict(c.execute("SELECT venue,bias_flag FROM venues").fetchall()); assert flags.get("US") == "UNIVERSE_BIASED"
    assert c.execute("SELECT status FROM universe WHERE sym='LEHMQ'").fetchone()[0] == "delisted"
    assert c.execute("SELECT COUNT(*) FROM filings WHERE sym='LEHMQ'").fetchone()[0] > 0
    assert c.execute("SELECT COUNT(*) FROM bars WHERE sym LIKE '%.JK'").fetchone()[0] == 0
if __name__ == "__main__":
    fs = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for f in fs: f(); print("PASS", f.__name__)
