"""Railfix 2026-10-29 (Rules Auditor 14:09 WITA): (1) sub-2% ideas REJECTED not lifted (crypto 0.5% floor kept; A+ 4%; cap 5%),
(2) true limit fills: at the limit, better only on a gap; off-hours rebook never through the limit, (4) amend recomputes risk_aud, r0 stays initial. PAPER."""
import datetime as dt, json, os, sys, tempfile, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK, seat_cli as SC
from veteran_desk.tests.synthetic_book import skeleton
U = dt.timezone.utc; T = dt.datetime(2026, 10, 28, 18, 45, tzinfo=U)   # US + FX session open, past the open window

# ---- 1. reject, don't lift ----
def test_risk_plan_rejects_sub_floor():
    # t2808u: B full is 1% of equity. mult 0.45 keeps 0.45%. Not below_2pct_nonA_floor and not lifted.
    r = BK.risk_plan(500000, "B", False, .45)
    assert r["reject"] is None and abs(r["pct"] - .0045) < 1e-9 and r["risk_aud"] == 2250 and abs(r["wanted_pct"] - .0045) < 1e-9 and not r["sized_up"]
def test_risk_plan_grades_and_cap():
    r = BK.risk_plan(500000, "A"); assert r["reject"] is None and r["pct"] == .01 and r["risk_aud"] == 5000   # tfo_review_fix_20261031 A default 1%
    r = BK.risk_plan(500000, "A+"); assert r["pct"] == .04 and r["reject"] is None
    r = BK.risk_plan(500000, "A+", mult=.5); assert r["pct"] == .02 and r["reject"] is None
    r = BK.risk_plan(500000, "A+", mult=2.0); assert r["pct"] == BK.RISK_MAX == .05
def test_crypto_seat_floor_kept():
    r = BK.risk_plan(500000, "A", mult=.8, floor=BK.RISK_CRYPTO_MIN); assert r["reject"] is None and abs(r["pct"] - .008) < 1e-9   # kairails: crypto base 1%
    r = BK.risk_plan(500000, "A", mult=.2, floor=BK.RISK_CRYPTO_MIN); assert r["reject"] is None and abs(r["pct"] - .002) < 1e-9 and not r["sized_up"]   # t2808u: the cut stands

def _book(pos=None, seat="energy"):
    tmp = tempfile.mkdtemp(); D = copy.deepcopy(skeleton())
    for B in D["seats"].values(): B["positions"] = {}; B["closed"] = []; B["cash_aud"] = B["seed_aud"]
    D["hedge"] = {"disabled": True}; D["kill_switch"] = {"state": "OK"}; D["peak_equity_aud"] = None
    if pos: D["seats"][seat]["positions"][pos["id"]] = pos
    p = os.path.join(tmp, "b.json"); json.dump(D, open(p, "w")); q = os.path.join(tmp, "q.json"); json.dump({"entries": {}}, open(q, "w"))
    return p, q
def _tick(path, q, bar, sym=None, fp=None):
    old = (BK.BOOK, BK.now, BK.fallback_quote, BK.ATR_FN, BK.first_print_after_open, BK.firm_ok, BK.posture); _onews, _obars = BK.NEWS_FN, BK.BARS_FN
    try:
        BK.BOOK = path; BK.now = lambda: T; BK.fallback_quote = lambda s: None; BK.ATR_FN = lambda s: 0.1
        BK.NEWS_FN = lambda p: None; BK.BARS_FN = lambda s, c: None   # t1sync: isolate from live news_alerts.jsonl (ECB headline 18:08 WITA leaked in)
        BK.firm_ok = lambda: (True, ""); BK.posture = lambda: (False, "risk-off")
        if fp is not None: BK.first_print_after_open = lambda s, t: fp
        return BK.tick(price_fn=lambda s: dict(bar, asof=T.isoformat()), au=0.70, queue_path=q)
    finally: BK.BOOK, BK.now, BK.fallback_quote, BK.ATR_FN, BK.first_print_after_open, BK.firm_ok, BK.posture = old; BK.NEWS_FN, BK.BARS_FN = _onews, _obars

def test_arm_rejects_sub_floor_idea():
    import funder_gate as FG
    path, q = _book()
    e = dict(id="VET-X-1", book="veteran", state=FG.WAIT, decision="approve", ticker="XLE", side="LONG", entry=68.2, stop=66.0, target=72.6,
             gate_size_mult=0.45, grade="A", tags={"strategy": "veteran:energy grade A"})   # tfo_review_fix_20261031: cut A now sizes to 1% (arms)
    json.dump({"entries": {e["id"]: e}}, open(q, "w"))
    D = _tick(path, q, dict(last=68.5, hi=68.6, lo=68.4))
    p = D["seats"]["energy"]["positions"]["VET-X-1"]; assert abs(p["risk_pct"] - .009) < 1e-9   # t2808u: energy full risk is 2%; the 0.45 cut stays at 0.9% and is not lifted to 1%

def _armed(side="LONG", entry=68.2, ot="limit", **kw):
    sg = 1 if side == "LONG" else -1
    p = dict(id="P1", seat="energy", ticker="XLE", side=side, entry=entry, stop=entry - sg * 2.2, target=entry + sg * 4.4, shares=100, risk_aud=220 / .7,
             status="armed", order_type=ot, armed_utc=(T - dt.timedelta(minutes=30)).isoformat(), venue="US", sleeve="core", scale_t1=True); p.update(kw); return p

# ---- 2. limit fills ----
def test_long_limit_fills_at_limit_not_worse():
    path, q = _book(_armed())
    p = _tick(path, q, dict(last=68.48, hi=68.6, lo=68.15, open=68.4))["seats"]["energy"]["positions"]["P1"]
    assert p["status"] == "open" and p["fill"] == 68.2 and p["slippage_bps"] <= 0
def test_long_limit_gap_fills_better():
    path, q = _book(_armed())
    p = _tick(path, q, dict(last=68.1, hi=68.15, lo=67.9, open=68.0))["seats"]["energy"]["positions"]["P1"]
    assert p["fill"] == 68.0
def test_short_limit_fills_at_or_above():
    path, q = _book(_armed("SHORT", 68.2))
    p = _tick(path, q, dict(last=67.9, hi=68.25, lo=67.8, open=68.0))["seats"]["energy"]["positions"]["P1"]
    assert p["fill"] == 68.2
    path, q = _book(_armed("SHORT", 68.2))
    p = _tick(path, q, dict(last=68.6, hi=68.7, lo=68.4, open=68.5))["seats"]["energy"]["positions"]["P1"]
    assert p["fill"] == 68.5
def test_fill_initialises_mark_and_rail():
    path, q = _book(_armed())
    p = _tick(path, q, dict(last=68.3, hi=68.4, lo=68.1, open=68.3))["seats"]["energy"]["positions"]["P1"]
    assert p["mark"] == 68.3 and p["rail_state"] == "initial" and abs(p["r0"] - 2.2) < 1e-9 and p["peak_fav_R"] == 0.0 and p["initial_stop"] == 66.0
def _offhours(fill, **kw):
    return _armed(status="open", fill=fill, filled_utc="2026-10-28T12:16:40+00:00", cost_aud=15.0, slippage_bps=4.4, off_hours_checked=True, off_hours_fill=True,
                  rebook_pending=True, off_hours_fill_audit=dict(fill=fill, filled_utc="2026-10-28T12:16:40+00:00", cost_aud=15.0, shares=100, risk_aud=300.0), **kw)
def test_rebook_never_through_limit():   # XLE: limit 68.2, first print 68.48 -> no fill, order back to working
    path, q = _book(_offhours(68.23))
    p = _tick(path, q, dict(last=68.45, hi=68.6, lo=68.4), fp=dict(px=68.48, asof="2026-10-28 09:30:00-04:00"))["seats"]["energy"]["positions"]["P1"]
    assert p["status"] == "armed" and "fill" not in p
def test_rebook_below_floor_rejected_not_lifted():
    # t2808u: a rebook that is not a history cut may lift to the grade budget and must tag it. It is not cancelled below_1pct_floor.
    path, q = _book(_offhours(68.1))
    p = _tick(path, q, dict(last=68.1, hi=68.2, lo=68.0), fp=dict(px=68.1, asof="2026-10-28 09:30:00-04:00"))["seats"]["energy"]["positions"]["P1"]
    assert p["status"] == "open" and p.get("lift_tag") == "lifted_subfloor" and p["risk_pct_actual"] >= 0.01 - 1e-9
    path, q = _book(_offhours(68.1, size_mult=0.4))
    kept = _tick(path, q, dict(last=68.1, hi=68.2, lo=68.0), fp=dict(px=68.1, asof="2026-10-28 09:30:00-04:00"))["seats"]["energy"]["positions"]["P1"]
    assert kept["status"] == "open" and kept["shares"] == 100 and kept.get("lift_tag") is None and "not lifted" in kept["rebook_note"]

# ---- 4. amend recomputes risk_aud; r0 stays the initial risk ----
def test_amend_short_fx_recomputes_risk_keeps_r0():
    pid = "P1"; seat = "macro"
    pos = dict(id=pid, seat=seat, ticker="EURUSD=X", side="SHORT", entry=1.1174, fill=1.11682, stop=1.1245, target=1.096, shares=990959, risk_aud=10000.0, risk_pct=.02,
               status="open", venue="FX", mark=1.1140, scale_t1=True, off_hours_checked=True, cost_aud=235.0, filled_utc=(T - dt.timedelta(hours=1)).isoformat())
    path, q = _book(pos, seat)
    D = json.load(open(path)); D["audusd"] = 0.70; json.dump(D, open(path, "w")); old = BK.BOOK
    try:
        BK.BOOK = path; out = SC.amend(seat, pid, "pre-ECB cut", stop=1.12); assert out["ok"], out
    finally: BK.BOOK = old
    p = json.load(open(path))["seats"][seat]["positions"][pid]
    assert abs(p["risk_aud"] - (1.12 - 1.11682) * 990959 / 0.70) < 1.0 and p["initial_stop"] == 1.1245
    p = _tick(path, q, dict(last=1.1140, hi=1.1150, lo=1.1135))["seats"][seat]["positions"][pid]
    assert abs(p["r0"] - (1.1245 - 1.11682)) < 1e-9 and p["stop"] == 1.12
