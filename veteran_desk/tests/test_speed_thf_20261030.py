"""Speed identity + THF no-trade blockers. PAPER. Does not loosen a strategy gate."""
import datetime as dt, inspect, os, sqlite3, tempfile
from veteran_desk import pit as P, vd_engine as E, thf_dash as TD, book as BK, gate_v2 as G
UTC = dt.timezone.utc
def test_fresh_heartbeat_is_not_broken():
    assert TD.stale_min(0.0, 5) is False
    assert TD.stale_min(0, 5) is False
    assert TD.stale_min(4.9, 5) is False
    assert TD.stale_min(5.1, 5) is True
    assert TD.stale_min(None, 5) is True
def test_small_positive_exp_is_not_the_hold(monkeypatch):
    """exp R 0.06 clears the wake history rule (need >= 0). 'Safe moment' is market closed / hysteresis, not this."""
    from veteran_desk.tests.test_gate_v2 import rows
    def fake(A, side, k, rr, H=10, cost=0.001):
        import numpy as np
        out = np.full(len(A["c"]), 0.06); return out
    monkeypatch.setattr(G, "outcomes", fake)
    rs = rows(400, drift=0.0002, seed=3)
    f = dict(sym="DG", cls="EQ", dir=1, k_atr=1.5, er20=0.5, _rows=rs)
    h = E.history_check("essentials_utilities", f)
    assert h["exp_R"] == 0.06 and h["wake_ok"] is True and h["natural_pct"] >= 2, h
    closed = dict(grade="A", wake_ok=False, market_open=False, market="US", bar_age_d=1, history=h)
    why = E._wake_why(closed)
    assert "market closed" in why and "needs >=0" not in why
    assert E._wake_why(dict(closed, wake_ok=True, market_open=True)) is None
def test_hysteresis_is_why_not_a_booked_trade():
    x = dict(grade="A+", wake_ok=True, wake_block="hysteresis: first A cycle", market_open=True, market="FX", bar_age_d=0, history=dict(wake_ok=True, exp_R=0.2, n=80, n_min=40, natural_pct=2))
    assert E._wake_why(x) == "hysteresis: first A cycle"
def test_second_look_timeout_does_not_gate():
    src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "desk.py")).read()
    src += open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "book.py")).read()
    src += open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "gate_v2.py")).read()
    assert "second_look" not in src
    eng = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "vd_engine.py")).read()
    assert "blocks_entry=False" in eng and "advisory only, after the seat round" in eng
def test_queue_is_not_behind_the_marcus_note():
    from veteran_desk import desk as D
    src = inspect.getsource(D.run)
    assert src.find("apply_marcus_limits") < src.find("enqueue(") < src.find("head_of_desk(")
def test_cut_a_is_not_rejected_at_quarter_pct():
    """1% x the 0.25 size-cut floor is 0.25% of a A$500k seat. Grade A is lifted back to 1% and must not be vetoed."""
    from veteran_desk.tests.test_gate_v2 import HIST, P0, asof, ENV
    rows = list(HIST[0])
    old_know = (dt.datetime.fromisoformat(rows[-1][6]) - dt.timedelta(days=10)).isoformat()
    rows[-1] = rows[-1][:6] + (old_know,)
    a = dt.datetime.fromisoformat(old_know) + dt.timedelta(days=10)
    c = rows[-1][4]; atr = G.arrays(rows)["atr"][-1]
    p = P0(grade="A", entry=c, stop=c - 0.7 * atr, target=c + 2 * atr)
    r = G.gate(p, None, dict(call="LONG"), dict(factual_kill=False), a, 0, hist=(rows, "t"), env=ENV)
    assert r["passed"], r["reasons"]
    assert abs(r["risk_pct"] - 0.01) < 1e-9
    assert not any("0.25%" in x for x in r["reasons"])
def test_atr_batch_matches_per_bar_query():
    td = tempfile.mkdtemp(); db = os.path.join(td, "p.sqlite"); c = P.con(db)
    prior = []
    px = 100.0
    for i in range(40):
        d = (dt.date(2024, 1, 2) + dt.timedelta(days=i)).isoformat()
        h, l, cl = px + 1, px - 1, px + (0.1 if i % 2 else -0.1); prior.append((d, h, l, cl)); px = cl
        c.execute("INSERT INTO bars VALUES(?,?,?,?,?,?,?,?,?)", ("AAA", d, px, h, l, cl, 1000, d + "T21:00:00+00:00", "t"))
    c.commit()
    for d, h, l, cl in prior[10:]:
        assert P._atr_from_prior(prior, d) == P._atr_before(c, "AAA", d)
def test_bars_asof_many_matches_one_by_one():
    td = tempfile.mkdtemp(); db = os.path.join(td, "p.sqlite"); c = P.con(db)
    asof = dt.datetime(2026, 10, 30, 21, tzinfo=UTC)
    for s, base in (("AAA", 10.0), ("BBB", 20.0)):
        for i in range(30):
            d = (dt.date(2026, 9, 1) + dt.timedelta(days=i)).isoformat()
            kn = dt.datetime(2026, 9, 1, 21, tzinfo=UTC) + dt.timedelta(days=i)
            if kn > asof: continue
            c.execute("INSERT INTO bars VALUES(?,?,?,?,?,?,?,?,?)", (s, d, base, base + 1, base - 1, base + 0.5, 5, kn.isoformat(), "t"))
    c.commit()
    many = P.bars_asof_many(c, ["AAA", "BBB", "CCC"], asof, 10)
    for s in ("AAA", "BBB", "CCC"):
        assert many[s] == P.bars_asof(c, s, asof, 10)
def _min_book():
    seat = lambda: dict(seed_aud=500000.0, cash_aud=500000.0, equity_aud=500000.0, positions={}, closed=[], commission_expense_aud=0.0)
    return dict(seats={s: seat() for s in BK.TRADERS}, hedge={}, kill_switch={}, peak_equity_aud=500000.0 * len(BK.TRADERS), seed_aud=500000.0 * len(BK.TRADERS), audusd=0.65)
def test_armed_order_is_not_withdrawn_on_the_next_tick(monkeypatch):
    monkeypatch.setattr(BK, "MARGIN_HEADROOM", 1.0)
    from veteran_desk.tests.test_insufficient_cash_20261029 import _tick, _entry, MK, CLOSED
    e = _entry("ABBV1"); e.update(ticker="ABBV", acked=True, ack_required=True, grade="A")
    marks = dict(MK, ABBV=dict(last=40.5, hi=40.6, lo=40.4))
    D, Q = _tick(_min_book(), {"ABBV1": e}, marks, session=CLOSED)
    assert D["seats"]["banks"]["positions"]["ABBV1"]["status"] == "armed"
    D2, Q2 = _tick(D, Q, marks, session=CLOSED)
    assert D2["seats"]["banks"]["positions"]["ABBV1"]["status"] == "armed"
    assert Q2["ABBV1"]["state"] == "armed"
def test_feed_verdicts_respect_delay_and_the_close():
    from tools.feed_speed import judge
    now_open = True
    assert judge("eodhd", True, 0.4, age_min=14, market_open=now_open, expected_delay_min=15)["verdict"] == "green"
    assert judge("eodhd", True, 0.4, age_min=40, market_open=now_open, expected_delay_min=15)["verdict"] == "red"
    assert judge("fmp", True, 0.4, age_min=400, market_open=False, after_close_ok=True)["verdict"] == "green"
    assert judge("fmp", False, key_present=False)["verdict"] == "amber"
    assert judge("yahoo", False, err="timeout")["verdict"] == "red"
    assert judge("massive_polygon", True, 12.0, age_min=1, market_open=True)["verdict"] == "amber"
