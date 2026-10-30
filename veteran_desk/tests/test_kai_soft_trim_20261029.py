"""softtrim Brett 2026-10-29 16:12 WITA: until Fri 2026-10-31 15:11 WITA NEW crypto-seat arms A+ only (reason soft_trim_a_plus_only). Existing armed/open untouched. PAPER."""
import datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import funder_gate as FG
import test_railfix_20261029 as RF
from veteran_desk import book as BK
BAR = dict(last=150.0, hi=150.5, lo=149.5, open=150.0)
IN = dt.datetime(2026, 10, 29, 10, 0, tzinfo=dt.timezone.utc)    # 18:00 WITA Thu - inside window
AFTER = dt.datetime(2026, 10, 30, 7, 12, tzinfo=dt.timezone.utc)  # 15:12 WITA Fri - after expiry
def _run(seat, grade, when, pos=None, eid="VET-ST-1"):
    path, q = RF._book(pos, seat="crypto")
    e = dict(id=eid, book="veteran", state=FG.WAIT, decision="approve", ticker="KRAKEN:SOLUSD", side="LONG", entry=150.0, stop=140.0, target=200.0,
             gate_size_mult=1.0, grade=grade, tags={"strategy": "veteran:%s grade %s" % (seat, grade)})
    json.dump({"entries": {eid: e}}, open(q, "w"))
    old = RF.T; RF.T = when
    try: D = RF._tick(path, q, BAR)
    finally: RF.T = old
    return D, D["seats"][seat]["positions"].get(eid), {x[0]: tuple(x) for x in D["last_events"]}.get(eid)
def test_constant():
    assert BK.SOFT_TRIM_UNTIL.isoformat() == "2026-10-30T15:11:00+08:00"
def test_crypto_A_rejected_in_window():
    D, p, ev = _run("crypto", "A", IN); assert p is None and ev[2] == "soft_trim_a_plus_only"
def test_crypto_aplus_arms_in_window():
    D, p, ev = _run("crypto", "A+", IN); assert p is not None and (ev is None or ev[2] != "soft_trim_a_plus_only")
def test_crypto_A_after_expiry_not_trimmed():
    D, p, ev = _run("crypto", "A", AFTER); assert ev is None or ev[2] != "soft_trim_a_plus_only"
    assert p is not None
def test_non_crypto_A_unaffected():
    D, p, ev = _run("price_action", "A", IN); assert ev is None or ev[2] != "soft_trim_a_plus_only"
def test_existing_xmr_open_not_cancelled():
    x = dict(id="VET-KRAKENXMRUSD-TEST-crypto", seat="crypto", ticker="KRAKEN:XMRUSD", side="LONG", entry=155.0, stop=140.0, target=200.0, shares=58,
             risk_aud=2700.0, risk_pct=.01, status="open", fill=155.0, initial_stop=140.0, r0=15.0, sleeve="core", venue="CRYPTO", mark=150.0, cost_aud=20.0, filled_utc="2026-10-29T05:20:00+00:00", armed_utc="2026-10-29T05:17:21+00:00", mark_utc="2026-10-29T09:00:00+00:00", order_type="limit", peak_fav_R=0.0, rail_state="initial", rail_hwp=155.0, rail_log=[])
    D, p, ev = _run("crypto", "A", IN, pos=x)
    xp = D["seats"]["crypto"]["positions"].get(x["id"])
    assert xp is not None and xp["status"] == "open" and xp["stop"] >= 140.0   # one shared test bar (150) for all symbols, so XMR is scaled to sit above its stop
    assert ev[2] == "soft_trim_a_plus_only"
def test_expiry_boundary_fri_30_oct():
    D, p, ev = _run("crypto", "A", dt.datetime(2026, 10, 30, 7, 10, tzinfo=dt.timezone.utc)); assert ev[2] == "soft_trim_a_plus_only"   # 15:10 WITA Fri 30 Oct: still trimmed
