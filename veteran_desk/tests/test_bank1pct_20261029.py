"""FINAL half-bank + giveback rail (Brett HARD via TFO 2026-10-29 17:20 WITA; Kai crypto tweaks 17:19). PAPER.
Half at net A$ >= 1R ORIGINAL risk (trend 1.5R), persisted grade A/A+ only, +1% basis leg gone. Giveback a/b/c, all grades, open profit only."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
from veteran_desk.tests.test_exit_rail_20261029 import _pos, _book, _tick

def _fx(**k):
    p = dict(ticker="EURUSD=X", venue="FX", side="SHORT", fill=1.1168, stop=1.1168 + 0.00447, initial_stop=1.1168 + 0.00447, shares=1000000, cost_aud=0.0, grade="A", seat="macro"); p.update(k); return p

def test_no_basis_constant():
    assert not hasattr(BK, "RAIL_HALF_BASIS_PCT")

def test_theo_eurusd_1R_original_risk(monkeypatch):
    monkeypatch.setattr(BK, "quote_to_aud", lambda *a, **k: 1.0)
    assert BK.RAIL_BANK_R == 0.7 and BK.RAIL_BANK_ALL_POSITIONS is True
    p = _fx()
    below, above = 1.1168 - 0.00447 * 0.5, 1.1168 - 0.00447 * 0.9
    assert BK.net_R(p, below, 0.65) < 0.7 and BK.bank_trigger(p, below, 0.65) is None
    assert BK.net_R(p, above, 0.65) >= 0.7 and BK.bank_trigger(p, above, 0.65) == "rail_0_7R_aud_scale_out"
    trailed = _fx(stop=1.1168)   # trailed stop at BE must NOT shrink the original R
    assert BK.bank_trigger(trailed, below, 0.65) is None

def test_plus1pct_leg_gone(monkeypatch):
    monkeypatch.setattr(BK, "aud", lambda s, x, au: x)
    p = dict(ticker="XLE", venue="US", side="LONG", fill=100.0, initial_stop=80.0, stop=80.0, shares=100, cost_aud=0.0, grade="A+")
    assert BK.bank_trigger(p, 101.5, 1) is None   # +1.5% but < 1R (20)

def test_grade_must_be_persisted(monkeypatch):
    monkeypatch.setattr(BK, "quote_to_aud", lambda *a, **k: 1.0)
    p = _fx(); p.pop("grade"); p["tags"] = {"strategy": "veteran:macro grade A"}
    above = 1.1168 - 0.00447 * 0.9
    assert BK.bank_trigger(p, above, 0.65) == "rail_0_7R_aud_scale_out"   # tags still resolve a grade
    p["tags"] = {}; assert BK.bank_trigger(p, above, 0.65) == "rail_0_7R_aud_scale_out"   # bank07: every open, grade no longer required
    for g in ("B", "C"): assert BK.bank_trigger(_fx(grade=g), above, 0.65) == "rail_0_7R_aud_scale_out"

def test_trend_seat_banks_at_1_5R(monkeypatch):
    monkeypatch.setattr(BK, "aud", lambda s, x, au: x)
    p = dict(ticker="SPY", venue="US", side="LONG", fill=100.0, initial_stop=98.0, stop=98.0, shares=100, cost_aud=0.0, grade="A", seat="trend")
    assert BK.bank_trigger(p, 101.2, 1) is None   # 0.6R, under 0.7R
    assert BK.bank_trigger(p, 102.3, 1) == "rail_0_7R_aud_scale_out"
    p["seat"] = "energy"; assert BK.bank_trigger(p, 102.3, 1) == "rail_0_7R_aud_scale_out"   # no seat is still on 1R or 1.5R

def test_capped_sleeve_opts_out():
    assert BK.bank_trigger(dict(bank_capped=True, grade="A"), 1, 1) is None

def test_tick_banks_half_fee_floor_and_audit():
    pos = _pos(cost_aud=0.0, grade="A")
    path, q = _book(pos); D, ev = _tick(path, q, [dict(last=103.0, hi=103.0, lo=102.5, open=102.5)])
    p = D["seats"]["energy"]["positions"]["P1"]
    assert p["t1_done"] and p["shares"] == 50 and p["stop"] >= 100.0 * (1 + BK.COST_RT["US"]) - 1e-9
    a = [json.loads(l) for l in open(os.path.join(os.path.dirname(path), "early_bank_audit.jsonl"))]
    assert a[-1]["reason"] == "rail_0_7R_aud_scale_out" and 98.0 <= a[-1]["cf_rail_stop_px"] < 103.0

def test_giveback_a_fires_before_half(monkeypatch):
    pos = _pos(cost_aud=0.0, grade="B")   # all grades; R0=2 (fill 100 stop 98)
    path, q = _book(pos)
    D, _ = _tick(path, q, [dict(last=101.2, hi=101.2, lo=101.0)])   # 0.6R, under the 0.7R half-bank
    assert D["seats"]["energy"]["positions"]["P1"]["shares"] == 100
    D, _ = _tick(path, q, [dict(last=100.5, hi=100.9, lo=100.4)])   # net ~0.2R <= 50% of peak, still > 0
    p = D["seats"]["energy"]["positions"]["P1"]; c = D["seats"]["energy"]["closed"][-1]
    assert c["exit_reason"] == "rail_giveback_protect" and p["shares"] == 50 and p["stop"] >= 100.0

def test_giveback_never_on_losing_or_flat():
    p = dict(_pos(cost_aud=0.0), r0=2.0, initial_stop=98.0, gb_peak_net_R=0.9)
    assert BK.giveback_trigger(p, 99.9, 1.0) is None and BK.giveback_trigger(p, 100.0, 1.0) is None
    assert BK.giveback_trigger(dict(p, t1_done=True), 100.5, 1.0) is None

def _bars(n, last_c, last_v, prev_l=99.0, prev_h=101.0):
    b = [dict(o=100, h=101, l=99, c=100, v=1000.0) for _ in range(n)]
    b[-2] = dict(o=100, h=prev_h, l=prev_l, c=100, v=1000.0); b[-1] = dict(o=100, h=101, l=98, c=last_c, v=last_v); return b

def test_volturn_generic_and_pre_event():
    p = dict(side="LONG", ticker="XLE", venue="US")
    assert BK.volturn(p, _bars(25, 98.9, 2100.0)) and not BK.volturn(p, _bars(25, 98.9, 1500.0)) and not BK.volturn(p, _bars(25, 99.5, 3000.0))
    assert BK.volturn(dict(p, pre_event_level=99.8), _bars(25, 99.5, 3000.0))
    assert BK.volturn(dict(side="SHORT", ticker="XLE", venue="US"), _bars(25, 101.2, 3000.0))

def test_volturn_crypto_uses_24_bar_avg():
    p = dict(side="LONG", ticker="KRAKEN:XBTUSD", venue="CRYPTO")
    assert not BK.volturn(p, _bars(24, 98.9, 3000.0))   # needs 24+2 1h bars
    assert BK.volturn(p, _bars(26, 98.9, 2100.0))

def test_giveback_b_and_c_in_tick(monkeypatch):
    for fn, rsn in ((dict(BARS_FN=lambda s, c: _bars(25, 98.9, 5000.0)), "rail_giveback_volturn"), (dict(NEWS_FN=lambda p: "2026-10-29T09:30:00+00:00"), "rail_giveback_news")):
        path, q = _book(_pos(cost_aud=0.0, grade="B"))
        orig = BK.tick
        def t2(*a, _fn=fn, **k):
            for n, f in _fn.items(): setattr(BK, n, f)
            return orig(*a, **k)
        monkeypatch.setattr(BK, "tick", t2)
        D, _ = _tick(path, q, [dict(last=100.6, hi=100.7, lo=100.4)])
        monkeypatch.setattr(BK, "tick", orig)
        assert D["seats"]["energy"]["closed"][-1]["exit_reason"] == rsn

def test_crypto_net_uses_kraken_fees(monkeypatch):
    monkeypatch.setattr(BK, "aud", lambda s, x, au: x)
    p = dict(ticker="KRAKEN:XBTUSD", venue="CRYPTO", side="LONG", fill=100.0, initial_stop=95.0, stop=95.0, shares=100, cost_aud=999.0, grade="A")
    n = BK.net_open_aud(p, 105.0, 1)
    sp = BK.CT.CM_SPREAD_TIER_BPS["CRYPTO"] / 1e4; fx = BK.CT.CM_FX_CONV   # costmodel 20261030: Kraken maker/taker + spread + FX conversion (USD pair)
    assert abs(n - (500 - (0.0025 + sp + fx) * 10000 - (0.004 + sp + fx) * 10500)) < 1e-6   # recorded cost_aud ignored for crypto
    assert BK.bank_trigger(p, 103.0, 1) is None and BK.bank_trigger(p, 105.0, 1) == "rail_0_7R_aud_scale_out"

def test_crypto_news_from_news_json(monkeypatch, tmp_path):
    (tmp_path / "state" / "veteran_desk").mkdir(parents=True)
    (tmp_path / "state" / "veteran_desk" / "news.json").write_text(json.dumps({"top5": [dict(assets=["CRYPTO"], markets=[], published_utc="2026-10-29T10:00:00+00:00")]}))
    monkeypatch.setattr(BK, "BASE", str(tmp_path))
    p = dict(ticker="KRAKEN:SOLUSD", venue="CRYPTO", filled_utc="2026-10-29T09:00:00+00:00")
    assert BK._news_default(p) == "2026-10-29T10:00:00+00:00"
    assert BK._news_default(dict(p, filled_utc="2026-10-29T11:00:00+00:00")) is None
