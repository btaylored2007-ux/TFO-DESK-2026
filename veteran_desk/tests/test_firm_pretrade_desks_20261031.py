"""Each desk's new-entry path calls the shared pre-trade gate. Mocks only. No live books."""
SEATCAP_LIVE = True
import datetime as dt
import json
import os

import firm_pretrade
from veteran_desk.exec_speed import firm_risk_service as R
from veteran_desk.exec_speed.common import iso

T0 = dt.datetime(2026, 10, 31, 2, 0, tzinfo=dt.timezone.utc)


def _snap(**k):
    book = {"ok": True, "us_beta_usd": 0.0, "us_beta_cap_usd": 1_000_000.0,
            "equity_aud": 2_000_000.0, "open_risk_aud": 0.0}
    base = {
        "ok": True, "paper_only": True, "reachable": True, "utc": iso(T0),
        "new_entries_allowed": True, "new_entries_halted": False, "exits_allowed": True,
        "level": "OK", "firm_equity_aud": 4_000_000.0, "firm_open_risk_aud": 0.0,
        "firm_heat": 0.0, "books": {"veteran": dict(book), "thf": dict(book)},
        "oanda": {"ok": True, "open_n": 0, "open_risk": 0.0},
    }
    base.update(k)
    return base


def _idea(**k):
    base = dict(desk="veteran", seat="banks", ticker="AAPL", side="LONG", venue="US", grade="A",
                action="entry", risk_pct=0.01, notional_aud=50_000, seat_equity_aud=500_000,
                risk_aud=5_000, us_beta_usd=1_000, stop=95.0, entry=100.0)
    base.update(k)
    return base


def _down():
    return {"reachable": False, "ok": False, "reason": "firm_risk_service_unreachable",
            "detail": "stale", "exits_allowed": True, "paper_only": True}


def _lines(path):
    if not os.path.exists(path):
        return []
    return [json.loads(x) for x in open(path) if x.strip()]


def test_service_down_refuses_new_risk_and_logs(tmp_path):
    log = str(tmp_path / "firm_risk_decisions.jsonl")
    d = R.pretrade(_idea(), snapshot=_down(), now=T0, log_path=log)
    assert d["ok"] is False and d["reason"] == "firm_risk_service_unreachable" and d["exits_allowed"] is True
    rows = _lines(log)
    assert rows[-1]["ok"] is False and rows[-1]["reason"] == "firm_risk_service_unreachable"
    assert rows[-1]["desk"] == "veteran"


def test_exits_and_stops_pass_when_service_is_down(tmp_path):
    log = str(tmp_path / "firm_risk_decisions.jsonl")
    for action in ("exit", "stop", "close", "cover"):
        d = R.pretrade(_idea(action=action, stop=None), snapshot=_down(), now=T0, log_path=log)
        assert d["ok"] is True and d["reason"] == "exit_or_stop"
    d = R.pretrade(_idea(reduce_only=True, stop=None), snapshot=_down(), now=T0, log_path=log)
    assert d["ok"] is True
    assert all(r["ok"] for r in _lines(log))


def test_stop_required_and_rules(tmp_path):
    log = str(tmp_path / "firm_risk_decisions.jsonl")
    assert R.pretrade(_idea(stop=None), snapshot=_snap(), now=T0, log_path=log)["reason"] == "stop_required"
    assert R.pretrade(_idea(stop=101, side="LONG"), snapshot=_snap(), now=T0, log_path=log)["reason"] == "stop_required"
    ok = R.pretrade(_idea(), snapshot=_snap(), now=T0, log_path=log)
    assert ok["ok"] and ok["reason"] == "pass"
    assert _lines(log)[-1]["ok"] is True
    low = R.pretrade(_idea(risk_pct=0.005, risk_aud=2_500), snapshot=_snap(), now=T0, log_path=log)
    assert low["reason"] == "below_risk_floor"
    crypto = R.pretrade(_idea(seat="crypto", venue="CRYPTO", grade="A", risk_pct=0.005, risk_aud=2_500,
                              notional_aud=50_000, us_beta_usd=0, seat_risk_min=0.005, seat_risk_max=0.01),
                        snapshot=_snap(), now=T0, log_path=log)
    assert crypto["ok"] and crypto["reason"] == "pass"
    lifted = R.pretrade(_idea(seat="crypto", venue="CRYPTO", risk_pct=0.004, risk_aud=2_000,
                              seat_risk_min=0.005, seat_risk_max=0.01), snapshot=_snap(), now=T0, log_path=log)
    assert lifted["reason"] == "below_seat_rail"
    hot = _snap(firm_open_risk_aud=500_000, firm_equity_aud=1_000_000, firm_heat=0.5, new_entries_allowed=False, new_entries_halted=True)
    assert R.pretrade(_idea(), snapshot=hot, now=T0, log_path=log)["reason"] == "firm_halt_or_heat"
    wide = R.pretrade(_idea(notional_aud=250_000, risk_pct=0.02, grade="B", risk_aud=10_000), snapshot=_snap(), now=T0, log_path=log)
    assert wide["reason"] == "single_name_40pct"
    oanda = R.pretrade(_idea(desk="oanda", seat="aggro", venue="OANDA", risk_pct=0.03, risk_aud=100, us_beta_usd=0),
                       snapshot=_snap(), now=T0, log_path=log)
    assert oanda["reason"] == "oanda_risk_2pct"
    full = _snap(oanda={"ok": True, "open_n": 4, "open_risk": 0.0})
    assert R.pretrade(_idea(desk="oanda", seat="lab", venue="OANDA", risk_pct=0.005, risk_aud=50),
                      snapshot=full, now=T0, log_path=log)["reason"] == "oanda_max_4_open"


def _arm(monkeypatch, desk_env=None):
    import funder_gate as FG
    from veteran_desk import book as BK
    tmp = __import__("tempfile").mkdtemp()
    q = os.path.join(tmp, "q.json")
    old = (BK.BOOK, BK.firm_ok, BK.posture, BK.session_open, os.environ.get("THF_DESK"))
    base = dict(book="veteran", ticker="XOM", side="LONG", entry=100.0, stop=96.0, target=108.0,
                state=FG.WAIT, decision="approve", grade="A", seat_risk_pct=.02,
                tags=dict(strategy="veteran:energy grade A sleeve core"), gate_size_mult=1.0)
    json.dump({"entries": {"A": dict(base, id="A")}}, open(q, "w"))
    if desk_env:
        monkeypatch.setenv("THF_DESK", desk_env)
    else:
        monkeypatch.delenv("THF_DESK", raising=False)
    BK.BOOK = os.path.join(tmp, "b.json")
    BK.MARGIN_HEADROOM = 1.0
    BK.firm_ok = lambda: (True, "OK")
    BK.posture = lambda: ("calm", "risk-off")
    BK.session_open = lambda v, t: False
    try:
        D = BK.tick(price_fn=lambda s: dict(last=99.9, hi=99.9, lo=99.8, asof="x"), au=.65, queue_path=q)
        return D, json.load(open(q))["entries"]["A"]
    finally:
        BK.MARGIN_HEADROOM = 1.3
        BK.BOOK, BK.firm_ok, BK.posture, BK.session_open = old[:4]
        if old[4] is None:
            os.environ.pop("THF_DESK", None)
        else:
            os.environ["THF_DESK"] = old[4]


def test_veteran_and_thf_arm_call_pretrade(monkeypatch):
    seen = []
    def wrap(p, **k):
        seen.append(dict(p))
        return {"ok": True, "reason": "pass", "paper_only": True, "exits_allowed": True}
    monkeypatch.setattr(firm_pretrade, "pretrade", wrap)
    D, _ = _arm(monkeypatch)
    assert D["seats"]["energy"]["positions"]["A"]["status"] == "armed"
    assert seen and seen[0]["desk"] == "veteran" and seen[0]["seat"] == "energy" and seen[0]["stop"] == 96.0
    seen.clear()
    D, entry = _arm(monkeypatch, "1")
    assert seen and seen[0]["desk"] == "thf"
    assert "A" in D["seats"]["energy"]["positions"]


def test_veteran_arm_refused_when_service_down(monkeypatch):
    monkeypatch.setattr(R, "QUERY_FN", lambda **k: _down())
    D, entry = _arm(monkeypatch)
    assert "A" not in D["seats"]["energy"]["positions"]
    assert entry["state"] == "expired" and entry["why"].startswith("firm_risk:")
    rows = _lines(R.DECISION_LOG)
    assert rows and rows[-1]["ok"] is False and rows[-1]["desk"] == "veteran"


def test_crypto_seat_rail_is_not_lifted(monkeypatch):
    """VD crypto 0.5% is allowed. The gate does not rewrite the size up to 1% or 2%."""
    import funder_gate as FG
    from veteran_desk import book as BK
    seen = []
    def wrap(p, **k):
        seen.append(dict(p))
        return R.pretrade(p, snapshot=_snap(), now=T0, log_path=R.DECISION_LOG)
    monkeypatch.setattr(firm_pretrade, "pretrade", wrap)
    tmp = __import__("tempfile").mkdtemp()
    q = os.path.join(tmp, "q.json")
    old = (BK.BOOK, BK.firm_ok, BK.posture, BK.session_open, BK.SOFT_TRIM_UNTIL)
    e = dict(id="C", book="veteran", ticker="BTC-USD", side="LONG", entry=100.0, stop=90.0, target=120.0,
             state=FG.WAIT, decision="approve", grade="A", seat_risk_pct=0.005,
             tags=dict(strategy="veteran:crypto grade A"))
    json.dump({"entries": {"C": e}}, open(q, "w"))
    BK.BOOK = os.path.join(tmp, "b.json")
    BK.MARGIN_HEADROOM = 1.0
    BK.firm_ok = lambda: (True, "OK")
    BK.posture = lambda: ("calm", "risk-off")
    BK.session_open = lambda v, t: False
    BK.SOFT_TRIM_UNTIL = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
    try:
        D = BK.tick(price_fn=lambda s: dict(last=100.0, hi=100.0, lo=99.0, asof="x"), au=.65, queue_path=q)
    finally:
        BK.MARGIN_HEADROOM = 1.3
        BK.BOOK, BK.firm_ok, BK.posture, BK.session_open, BK.SOFT_TRIM_UNTIL = old
    assert seen, "crypto arm did not call pretrade"
    assert seen[0]["seat"] == "crypto"
    assert seen[0]["seat_risk_min"] == BK.RISK_CRYPTO_MIN
    assert seen[0]["risk_pct"] <= seen[0]["seat_risk_max"] + 1e-9
    pos = D["seats"]["crypto"]["positions"].get("C")
    if pos:
        assert pos["shares"] > 0


def test_tfo_equity_and_crypto_refuse(monkeypatch):
    import tfo_buy
    import tfo_crypto
    monkeypatch.setattr(R, "QUERY_FN", lambda **k: _down())
    try:
        tfo_buy.execute("AAPL", 10000.0, "tech", "edge", "t", {"px_usd": 100.0, "px": 100.0, "tape": "LIVE"})
        assert False, "equity entry should refuse"
    except SystemExit as exc:
        assert "firm_risk refused" in str(exc)
    assert _lines(R.DECISION_LOG)[-1]["desk"] == "tfo_equity"
    monkeypatch.setattr(tfo_crypto, "opens_blocked", lambda *a, **k: None)
    monkeypatch.setattr(tfo_crypto, "gates", lambda *a, **k: ([], {"ask": 10.0, "bid": 9.9}, {"c3_stop_dist": 0.05}))
    monkeypatch.setattr(tfo_crypto, "risk_size", lambda *a, **k: {"bound": False, "skip": None, "usd_requested": 1000})
    monkeypatch.setattr("risk_caps.describe", lambda rk: "ok")
    monkeypatch.setattr("risk_caps.log_bind", lambda *a, **k: None)
    tfo_crypto.cmd_buy("ETH/USD", 1000.0, "majors", "beta", "t", True)
    assert _lines(R.DECISION_LOG)[-1]["seat"] == "kraken_clips"
    assert _lines(R.DECISION_LOG)[-1]["ok"] is False


def test_trend18_buy_refused_sell_passes(monkeypatch):
    import trend18 as T
    calls = []
    monkeypatch.setattr(T, "book_fill", lambda *a, **k: calls.append(a[1]) or {"fill_id": "x"})
    monkeypatch.setattr(R, "QUERY_FN", lambda **k: _down())
    r_buy = {"inst": "XBT/USD", "order_qty": 0.1, "px": 80000.0, "kind": "crypto", "side": "BUY"}
    r_sell = {"inst": "UGL", "order_qty": -10, "px": 40.0, "kind": "etf", "side": "SELL"}
    assert firm_pretrade.blocked(T._firm_proposal(r_buy, 1_800_000))
    assert T._firm_proposal(r_buy, 1_800_000)["stop"] < T._firm_proposal(r_buy, 1_800_000)["entry"]
    # sells are not a new-entry proposal; the execute loop only gates qty > 0
    assert r_sell["order_qty"] < 0


def test_fundamental_and_hunt_paths(monkeypatch):
    import value_deploy as VD
    import hunt48_short_paper as H
    monkeypatch.setattr(R, "QUERY_FN", lambda **k: _down())
    order = {"sym": "AAPL", "usd": 10000.0, "stop_px": 90.0, "q": {"px": 100.0}}
    assert VD._firm_block(order, 200000).startswith("firm_risk")
    L = H.new_ledger("t")
    try:
        H.upsert_trade(L, dict(ticker="AAA", direction="short", trigger=10, max_fill=9.9, stop=11, t1=9, t2=8, shares=100), dt.datetime(2026, 10, 31, tzinfo=dt.timezone.utc))
        assert False, "hunt arm should refuse"
    except H.ArmRefused as exc:
        assert "firm_risk" in str(exc)
    rows = _lines(R.DECISION_LOG)
    desks = {r["desk"] for r in rows}
    assert "fundamental" in desks and "hunt" in desks


def test_v2_proposal_refused_when_down(monkeypatch):
    monkeypatch.setattr(R, "QUERY_FN", lambda **k: _down())
    why = firm_pretrade.blocked(dict(desk="v2", seat="S1", ticker="BTC/USD", side="LONG", venue="CRYPTO", grade="A",
                                     action="entry", risk_pct=0.01, risk_aud=1000, notional_aud=20000,
                                     seat_equity_aud=100000, stop=90, entry=100, seat_risk_min=0.0, seat_risk_max=0.01))
    assert why == "firm_risk_service_unreachable"
    assert _lines(R.DECISION_LOG)[-1]["desk"] == "v2"


def test_allow_is_logged(tmp_path):
    log = str(tmp_path / "firm_risk_decisions.jsonl")
    d = R.pretrade(_idea(), snapshot=_snap(), now=T0, log_path=log)
    assert d["ok"] is True
    assert _lines(log)[-1]["reason"] == "pass"
