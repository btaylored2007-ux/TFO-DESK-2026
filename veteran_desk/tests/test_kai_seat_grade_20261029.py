"""kaiseat 2026-10-29 15:04 WITA (Rules Auditor): crypto rails by seat=='crypto' (not KRAKEN: prefix); real grade field; missing grade rejected. PAPER."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import funder_gate as FG
from test_railfix_20261029 import _book, _tick
BAR = dict(last=150.0, hi=150.5, lo=149.5, open=150.0)
def _arm(seat, grade="A", stop=135.0, mult=1.0, eid="VET-K-1"):
    path, q = _book()
    e = dict(id=eid, book="veteran", state=FG.WAIT, decision="approve", ticker="KRAKEN:SOLUSD", side="LONG", entry=150.0, stop=stop, target=200.0,
             gate_size_mult=mult, tags={"strategy": "veteran:%s grade %s" % (seat, grade or "")})
    if grade is not None: e["grade"] = grade
    json.dump({"entries": {eid: e}}, open(q, "w"))
    import datetime as _dt; from veteran_desk import book as _BK; _o = _BK.SOFT_TRIM_UNTIL; _BK.SOFT_TRIM_UNTIL = _dt.datetime(2000, 1, 1, tzinfo=_dt.timezone.utc)   # softtrim 2026-10-29: these rails tests run with the A+-only window off
    try: D = _tick(path, q, BAR)
    finally: _BK.SOFT_TRIM_UNTIL = _o
    ev = {x[0]: tuple(x) for x in D["last_events"]}
    return D["seats"][seat]["positions"].get(eid), ev.get(eid)
def test_non_crypto_seat_kraken_keeps_2pct_floor():
    p, ev = _arm("price_action", mult=0.4)   # t2808u: price_action full risk is 2%; the 0.4 cut stays at 0.8% and is not lifted to 1%.
    assert p is not None and abs(p["risk_pct"] - .008) < 1e-9
def test_crypto_seat_gets_band():
    p, ev = _arm("crypto", mult=0.5)          # 1% x0.5 = 0.5% -> allowed on the crypto seat
    assert p and p["status"] in ("armed", "open") and abs(p["risk_pct"] - .005) < 1e-9   # floortol 16:24: integer-unit rounding below 0.5% accepted
    assert abs(p["risk_aud"] - .005 * 500000) <= 1 and p.get("floor_note") is None and p["shares"] != int(p["shares"])   # head-trader ruling 30 Oct: fractional Kraken units, no 0.98 tolerance
    p, ev = _arm("crypto")
    assert p and abs(p["risk_pct"] - .01) < 1e-9
def test_missing_grade_rejected():
    p, ev = _arm("crypto", grade=None); assert p is None and ev[2] == "missing_grade"
def test_notional_caps_by_grade():
    a, _ = _arm("crypto", "A", stop=140.0); ap, _ = _arm("crypto", "A+", stop=140.0)
    u = 150.0 / 0.70
    assert a and a["shares"] * u <= 50000 + 1 and a["shares"] * u > 49000
    assert ap and ap["shares"] * u <= 100000 + 1 and ap["shares"] * u > 50000
