"""VET exit rail v1 (Brett 2026-10-29 12:44 WITA, TFO approved): BE at +1R, trail from +1.5R, half-bank at T1 or +2R,
giveback flatten (<=60% of peak once peak>=2R), hard stop wins, tighten-only; seat_cli partial close + target amend. PAPER."""
import datetime as dt, json, os, sys, tempfile, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK, seat_cli as SC
from veteran_desk.tests.synthetic_book import skeleton
U = dt.timezone.utc; T = dt.datetime(2026, 10, 28, 18, 45, tzinfo=U)   # US session open, past open window

def _book(pos, seat="energy"):
    tmp = tempfile.mkdtemp(); D = copy.deepcopy(skeleton())
    for B in D["seats"].values(): B["positions"] = {}; B["closed"] = []; B["cash_aud"] = B["seed_aud"]
    D["hedge"] = {"disabled": True}; D["kill_switch"] = {"state": "OK"}; D["peak_equity_aud"] = None
    D["seats"][seat]["positions"][pos["id"]] = pos
    p = os.path.join(tmp, "b.json"); json.dump(D, open(p, "w")); q = os.path.join(tmp, "q.json"); json.dump({"entries": {}}, open(q, "w"))
    return p, q

def _pos(side="LONG", fill=100.0, r=2.0, target=None, shares=100, **kw):
    sg = 1 if side == "LONG" else -1
    p = dict(id="P1", seat="energy", ticker="XLE", side=side, entry=fill, fill=fill, stop=fill - sg * r, target=target if target is not None else fill + sg * 5 * r,
             shares=shares, risk_aud=100.0, status="open", venue="US", sleeve="core", cost_aud=10.0, filled_utc=(T - dt.timedelta(hours=1)).isoformat(),
             off_hours_checked=True, scale_t1=True)
    p.update(kw); return p

def _tick(path, q, bars, atr=0.1):
    """bars: list of dicts(last,hi,lo); one tick each. Returns (book, events per tick)."""
    old = (BK.BOOK, BK.now, BK.fallback_quote, BK.ATR_FN, BK.BARS_FN, BK.NEWS_FN); evs = []
    try:
        BK.BOOK = path; BK.now = lambda: T; BK.fallback_quote = lambda s: None; BK.ATR_FN = lambda s: atr; BK.BARS_FN = lambda s, c: None; BK.NEWS_FN = lambda p: None
        for b in bars:
            D = BK.tick(price_fn=lambda s: dict(b, asof=T.isoformat()), au=0.70, queue_path=q); evs.append(D["last_events"])
    finally: BK.BOOK, BK.now, BK.fallback_quote, BK.ATR_FN, BK.BARS_FN, BK.NEWS_FN = old
    return D, evs

def _p(D, seat="energy"): return D["seats"][seat]["positions"].get("P1")
def _closed(D, seat="energy"): return D["seats"][seat]["closed"]

def test_long_breakeven_at_1R():
    path, q = _book(_pos(bank_capped=True))
    D, _ = _tick(path, q, [dict(last=101.5, hi=102.1, lo=101.0)])   # peak 1.05R
    p = _p(D); assert p["rail_state"] == "be" and p["stop"] >= 100.0 and p["stop"] < 101.0 and abs(p["peak_fav_R"] - 1.05) < 1e-6 and p["initial_stop"] == 98.0

def test_short_breakeven_at_1R():
    path, q = _book(_pos("SHORT", bank_capped=True))
    D, _ = _tick(path, q, [dict(last=98.5, hi=99.0, lo=97.9)])
    p = _p(D); assert p["rail_state"] == "be" and p["stop"] <= 100.0 and p["stop"] > 99.0

def test_trail_long_and_min_distance():
    path, q = _book(_pos(bank_capped=True))
    D, _ = _tick(path, q, [dict(last=103.4, hi=103.6, lo=103.0)], atr=0.1)   # peak 1.8R -> fill+0.8R0=101.6; min dist max(1.0,0.1)=1.0 from 103.6 -> 102.6 ok
    p = _p(D); assert p["rail_state"] == "trailing" and abs(p["stop"] - 101.6) < 1e-6
    path, q = _book(_pos(bank_capped=True))
    D, _ = _tick(path, q, [dict(last=103.4, hi=103.6, lo=103.0)], atr=2.5)   # ATR 2.5 > 0.5R0 -> cap at 103.6-2.5=101.1
    assert abs(_p(D)["stop"] - 101.1) < 1e-6

def test_trail_short():
    path, q = _book(_pos("SHORT", bank_capped=True))
    D, _ = _tick(path, q, [dict(last=96.6, hi=97.0, lo=96.4)])   # peak 1.8R
    p = _p(D); assert p["rail_state"] == "trailing" and abs(p["stop"] - 98.4) < 1e-6

def test_half_bank_at_2R_before_T1():
    path, q = _book(_pos(target=112.0))   # T1 is far; 0.7R net banks first
    D, evs = _tick(path, q, [dict(last=101.6, hi=101.7, lo=101.2)])   # net crosses 0.7R; still under the old 2R price
    p = _p(D); half = [c for c in _closed(D) if c["id"] == "P1-T1"]
    assert half and half[0]["shares"] == 50 and half[0]["exit"] == 101.6 and half[0]["exit_reason"] == "rail_0_7R_aud_scale_out"
    assert p["shares"] == 50 and p["t1_done"] and p["rail_state"] == "t1_done" and p["stop"] >= 100.0

def test_half_bank_at_T1_short_keeps_semantics():
    path, q = _book(_pos("SHORT", target=99.2))   # 0.4R, before the 0.7R bank
    D, _ = _tick(path, q, [dict(last=99.2, hi=99.4, lo=99.0)])
    half = [c for c in _closed(D) if c["id"] == "P1-T1"][0]; p = _p(D)
    assert half["exit"] == 99.2 and half["exit_reason"] == "target1_scale_out" and p["shares"] == 50 and p["stop"] <= 100.0

def test_giveback_flatten(monkeypatch):
    monkeypatch.setattr(BK, "FULL_FLAT_PCT", None)   # 2026-10-30 fullflat: fixture runs +5.8% vs basis; this test isolates the R giveback geometry
    path, q = _book(_pos(target=120.0))
    D, evs = _tick(path, q, [dict(last=105.8, hi=106.0, lo=105.5), dict(last=103.5, hi=104.0, lo=103.4)], atr=0.1)
    # tick1: peak 3R -> half at 2R (104), trail 104.0 (fill+2R0) capped by hwp-1.0=105 -> 104.0; tick2: lo 103.4 <= stop 104 -> hard stop first
    c = _closed(D); assert any(x["exit_reason"] == "stop" for x in c)
    path, q = _book(_pos(target=120.0))
    D, evs = _tick(path, q, [dict(last=105.8, hi=106.0, lo=105.5)], atr=4.0)   # wide ATR: trail = 106-4=102 -> floored BE+
    p = _p(D); assert p and p["stop"] < 103.6
    old = (BK.BOOK,)
    D, evs = _tick(path, q, [dict(last=103.5, hi=103.9, lo=103.5)], atr=4.0)   # open R 1.75 <= 0.6*3=1.8 -> giveback
    assert _p(D) is None and _closed(D)[-1]["exit_reason"] == "giveback" and _closed(D)[-1]["exit"] == 103.5

def test_hard_stop_wins():
    path, q = _book(_pos())
    D, _ = _tick(path, q, [dict(last=98.5, hi=102.5, lo=97.9)])   # same bar touches stop and +1.25R
    assert _p(D) is None and _closed(D)[-1]["exit_reason"] == "stop" and _closed(D)[-1]["exit"] == 98.0

def test_never_widen():
    path, q = _book(_pos(stop=101.5, initial_stop=98.0))   # existing stop already tighter than the rail would set
    D, _ = _tick(path, q, [dict(last=103.0, hi=103.2, lo=102.8)])   # peak 1.6R -> trail 101.2 < 101.5
    p = _p(D); assert p["stop"] == 101.5
    D, _ = _tick(path, q, [dict(last=102.0, hi=102.2, lo=101.8)])
    assert _p(D)["stop"] >= 101.5 and _p(D)["peak_fav_R"] >= 1.6

def test_legacy_open_gets_rail_and_scale_t1():
    pos = _pos(); pos.pop("scale_t1")
    path, q = _book(pos)
    D, _ = _tick(path, q, [dict(last=100.5, hi=100.6, lo=100.2)])
    p = _p(D); assert p["scale_t1"] is True and p["rail_state"] == "initial" and p["peak_fav_R"] == 0.3 and p["stop"] == 98.0 and isinstance(p["rail_log"], list)

def test_legacy_fx_short():
    pos = dict(_pos("SHORT", fill=1.1168, r=0.0077, target=1.096, shares=990959, bank_capped=True), ticker="EURUSD=X", venue="FX", seat="macro"); pos.pop("scale_t1")
    path, q = _book(pos, "macro")
    D, _ = _tick(path, q, [dict(last=1.1080, hi=1.1095, lo=1.1078)], atr=0.005)   # peak (1.1168-1.1078)/0.0077=1.17R -> BE
    p = _p(D, "macro"); assert p["rail_state"] == "be" and p["stop"] <= 1.1168 and p["stop"] > 1.1095

def _seat_env(pos, seat="energy"):
    path, q = _book(pos, seat); return path

def test_partial_close_and_residual_guard():
    path = _seat_env(_pos(shares=10, mark=103.0))
    old = BK.BOOK
    try:
        BK.BOOK = path
        r = SC.close("energy", "P1", "bank some", price_fn=lambda s: dict(last=103.0), shares=4)
        assert r["ok"] and r["closed_shares"] == 4 and r["residual_shares"] == 6
        D = json.load(open(path)); p = D["seats"]["energy"]["positions"]["P1"]; c = D["seats"]["energy"]["closed"][-1]
        assert p["shares"] == 6 and c["shares"] == 4 and c["exit"] == 103.0 and c["exit_reason"].startswith("seat_partial_close")
        r = SC.close("energy", "P1", "too much", price_fn=lambda s: dict(last=103.0), shares=6); assert not r["ok"]
        r = SC.close("energy", "P1", "pct", price_fn=lambda s: dict(last=103.0), pct=50); assert r["ok"] and r["residual_shares"] == 3
    finally: BK.BOOK = old

def test_target_amend():
    path = _seat_env(_pos(mark=103.0))
    old = BK.BOOK
    try:
        BK.BOOK = path
        assert not SC.amend("energy", "P1", "inside", target=102.5)["ok"]          # inside mark for a long
        r = SC.amend("energy", "P1", "extend", target=115.0); assert r["ok"] and r["target"] == 115.0
        assert not SC.amend("energy", "P1", "loosen", stop=97.0)["ok"]
        D = json.load(open(path)); D["seats"]["energy"]["positions"]["P1"].update(t1_done=True, target=110.0, shares=50); json.dump(D, open(path, "w"))
        r = SC.amend("energy", "P1", "residual", target=112.0); assert r["ok"] and json.load(open(path))["seats"]["energy"]["positions"]["P1"]["target"] == 112.0
    finally: BK.BOOK = old
