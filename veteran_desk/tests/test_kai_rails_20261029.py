"""kairails 2026-10-29 14:28 WITA: Kai crypto seat rails (1% default, 0.5-2% band, 5% hard cap, A$50k/A$100k A+ clip, no ATR-band skip, A$500k floor + 5% ADV + stop kept)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from veteran_desk import book as BK
from test_gate_v2 import G, HIST, P0, g, rows
EQ = 500000.0; C = HIST[0][-1][4]; A = G.arrays(HIST[0])["atr"][-1]
def rp(grade="A", mult=1.0): return BK.risk_plan(EQ, grade, False, mult, floor=BK.RISK_CRYPTO_MIN)
def test_default_1pct(): r = rp(); assert r["pct"] == 0.01 and r["risk_aud"] == 5000 and not r["reject"]
def test_band_max_2pct():
    assert rp("A+")["pct"] == 0.02
    assert rp("A+", 10)["pct"] == 0.02 and rp("A", 10)["pct"] <= 0.02
def test_below_floor_rejected(): r = rp("A", 0.4); assert r["reject"] == "below_crypto_floor" and r["risk_aud"] == 0
def test_never_above_5pct():
    for gr in ("A", "A+"):
        for m in (1, 3, 100): assert rp(gr, m)["pct"] <= BK.RISK_MAX
    assert BK.risk_plan(EQ, "A+", True, 100)["pct"] <= BK.RISK_MAX   # other seats keep the 5% cap too
def test_other_seats_unchanged(): assert BK.risk_plan(EQ, "A")["pct"] == 0.01 and BK.risk_plan(EQ, "B")["pct"] == 0.02 and BK.risk_plan(EQ, "A+")["pct"] == 0.04
def test_clip_caps():
    r, q, rj = BK.crypto_clip(5000, 10.0, 100.0, EQ, "A"); assert abs(q * 100 - 50000) < 1e-6 and rj is None and r == 5000
    r, q, rj = BK.crypto_clip(5000, 2.0, 100.0, EQ, "A"); assert abs(q * 100 - 50000) < 1e-6 and r == 1000 and rj == "below_crypto_floor"
    r, q, rj = BK.crypto_clip(10000, 5.0, 100.0, EQ, "A+"); assert abs(q * 100 - 100000) < 1e-6 and r == 5000 and rj is None
def test_wide_atr_sized_down_not_skipped(monkeypatch):
    from veteran_desk import desk as DK
    monkeypatch.setattr(DK, "sweep_row_for", lambda t: {"vol24h_aud": 1e9})   # no live Kraken sweep in this tree
    r = g(P0(ticker="KRAKEN:SOLUSD", stop=C - 8 * A, target=C + 20 * A))
    assert not any("not a stop" in x for x in r["reasons"]), r["reasons"]
    assert any("sized down toward 0.5%" in w for w in r["warnings"])
    assert r["passed"], r["reasons"]; assert abs(r["risk_pct"] - 0.005) < 1e-9
def test_stop_required(): assert not g(P0(ticker="KRAKEN:SOLUSD", stop=0))["passed"]
def test_adv_and_value_floor_kept():
    r = g(P0(ticker="KRAKEN:SOLUSD", stop=C - 8 * A, target=C + 20 * A), hist=(rows(vol=50), "t"))
    assert not r["passed"] and any("ADV" in x for x in r["reasons"]) and any("A$500k" in x for x in r["reasons"])
def test_floor_constant_kept():
    from veteran_desk import feeds as FD; assert FD.MIN_24H_AUD == 500000 and G.TH["adv_hard"] == 0.05 and G.TH["min_value_aud"] == 500000
