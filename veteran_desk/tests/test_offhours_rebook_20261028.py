import datetime as dt, json, os, sys, tempfile, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
U = dt.timezone.utc
def test_session_open():
    assert not BK.session_open("US", dt.datetime(2026, 10, 28, 12, 16, tzinfo=U))   # 20:16 WITA
    assert BK.session_open("US", dt.datetime(2026, 10, 28, 13, 31, tzinfo=U))       # 21:31 WITA
    assert not BK.session_open("US", dt.datetime(2026, 10, 31, 15, 0, tzinfo=U))    # Saturday
    assert not BK.session_open("US", dt.datetime(2026, 11, 26, 16, 0, tzinfo=U))    # Thanksgiving
    assert BK.session_open("FX", dt.datetime(2026, 10, 31, 15, 0, tzinfo=U))
XLE = dict(id="VET-XLE-20261028-121530-energy", seat="energy", ticker="XLE", side="LONG", entry=68.2, stop=66.0, target=72.6, shares=3199, risk_aud=9997.9, risk_pct=0.02,
           sleeve="core", size_mult=1.0, gate_size_mult=0.75, sizing_warning=None, posture="risk-off", status="open", armed_utc="2026-10-28T12:16:40.312792+00:00", venue="US",
           hash="VET-XLE-20261028-121530-energy", pre_trade={"ok": True, "why": "cash long"}, fill=68.2300033569336, filled_utc="2026-10-28T12:16:40.312792+00:00", cost_aud=155.04,
           slippage_bps=4.4, order="stop_limit", exec_by="Sam Okafor", order_type="stop_limit")   # floortol 2026-10-29: frozen pre-flag fixture (was read from the live book)
def _run(tmp, t, fp):   # self-contained temp book: fresh seats from BK.load() on an empty temp path + the frozen XLE fixture; never reads the live book
    p = os.path.join(tmp, "book.json"); BK.BOOK = p; D = BK.load()
    for B in D["seats"].values(): B["positions"] = {}; B["closed"] = []
    D["seats"]["energy"]["positions"][XLE["id"]] = copy.deepcopy(XLE); D["seats"]["energy"]["cash_aud"] = D["seats"]["energy"]["seed_aud"] - BK.aud("XLE", XLE["fill"], 0.65) * XLE["shares"] - XLE["cost_aud"]   # cash already paid for the fill (consistent ledger); D["hedge"] = {"disabled": True}; D["kill_switch"] = {"state": "OK"}
    json.dump(D, open(p, "w"), default=str)
    q = os.path.join(tmp, "q.json"); json.dump({"entries": {}}, open(q, "w"))
    BK.now = lambda: t; BK.first_print_after_open = lambda s, t_: fp
    px = lambda s: dict(last=68.5, hi=68.6, lo=68.4, asof="x")
    return BK.tick(price_fn=px, au=0.65, queue_path=q), p
def test_flag_then_rebook():
    tmp = tempfile.mkdtemp(); old = (BK.BOOK, BK.now, BK.first_print_after_open, BK.posture)
    BK.posture = lambda: ("calm", "risk-off")   # unknown regime holds rebooks; this test is the off-hours rebook rail
    try:
        D, p = _run(tmp, dt.datetime(2026, 10, 28, 13, 5, tzinfo=U), None)   # 21:05 WITA: closed -> flagged, held
        x = D["seats"]["energy"]["positions"]["VET-XLE-20261028-121530-energy"]
        assert x["off_hours_fill"] and x["rebook_pending"] and x["off_hours_fill_audit"]["fill"] == 68.2300033569336
        BK.BOOK = p; BK.now = lambda: dt.datetime(2026, 10, 28, 13, 35, tzinfo=U); BK.first_print_after_open = lambda s, t_: dict(px=68.20, asof="2026-10-28 09:30:00-04:00")
        q = os.path.join(tmp, "q.json"); D = BK.tick(price_fn=lambda s: dict(last=68.5, hi=68.6, lo=68.4, asof="x"), au=0.65, queue_path=q)
        x = D["seats"]["energy"]["positions"]["VET-XLE-20261028-121530-energy"]
        assert not x["rebook_pending"] and x["fill"] == 68.20 and x["stop"] == 66.0 and x["target"] == 72.6 and x["cost_aud"] > 0
        assert 0.02 - 1e-6 <= x["risk_pct_actual"] <= 0.05 + 1e-6, x["risk_pct_actual"]
        print("REBOOK", x["shares"], x["risk_pct_actual"], x["cost_aud"], D["seats"]["energy"]["equity_aud"])
    finally: BK.BOOK, BK.now, BK.first_print_after_open, BK.posture = old
