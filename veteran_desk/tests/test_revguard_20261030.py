"""REVERSAL GUARD G3 alert-only (tfo_revguard_20261030). PAPER only."""
import copy, datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
import pytest
T = dt.datetime(2026, 10, 30, 8, tzinfo=dt.timezone.utc)
def P(**kw):
    p = dict(id="T", seat="energy", ticker="XYZ", side="LONG", fill=100.0, initial_stop=90.0, stop=95.0, shares=100, venue="US", cost_aud=0.0, target=130.0, status="open"); p.update(kw); return p
@pytest.fixture(autouse=True)
def _m(monkeypatch, tmp_path):
    monkeypatch.setattr(BK, "net_open_aud", lambda p, px, au, marks=None: (px - p["fill"]) * p["shares"])
    monkeypatch.setattr(BK, "risk0_aud", lambda p, au, marks=None: 10.0 * p["shares"])
    monkeypatch.setattr(BK, "_state_dir", lambda: str(tmp_path))
    import urllib.request; monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("revguard test must not hit network"))
    monkeypatch.setattr(BK, "revguard_webhook", lambda *a, **k: False); return tmp_path
def run(p, pxs): return [BK.revguard_check(p, x, 1.0) for x in pxs]
def test_arm_threshold():
    p = P(); assert run(p, [119.0, 100.0]) == [None, None]   # peak 1.9R never arms
    p = P(); r = run(p, [120.0, 115.9]); assert r[0] is None and r[1] is not None   # peak 2.0 arms
def test_fire_20pct_path():
    p = P(); r = run(p, [125.0, 120.1, 119.9])   # peak 2.5: 20%=0.5 -> 2.0 ; 0.6 off -> 1.9 ; first = 2.0
    assert r[1] is None and r[2]["trigger"] == "gave_back_20pct_of_peak"
def test_fire_060R_path():
    p = P(); r = run(p, [140.0, 134.1, 133.9])   # peak 4.0: 0.6 off -> 3.4 first
    assert r[1] is None and r[2]["trigger"] == "fell_0.60R_off_peak"
def test_no_lookahead_peak():
    p = P(); assert BK.revguard_check(p, 125.0, 1.0) is None and p["revguard_peak_net_R"] == 2.5
def test_single_fire():
    p = P(); r = run(p, [125.0, 119.0, 130.0, 110.0]); assert sum(1 for x in r if x) == 1 and p["revguard_fired"]
def test_no_order_stop_size_side_effects(_m):
    p = P(); B = dict(positions={"T": p}, closed=[], cash_aud=1000.0); B0 = copy.deepcopy(B)
    for x in (125.0, 119.0): a = BK.revguard_check(p, x, 1.0)
    BK.revguard_emit("energy", "T", p, T, a)
    for f in ("stop", "shares", "status", "target", "cost_aud"): assert p[f] == B0["positions"]["T"][f]
    assert B["closed"] == [] and B["cash_aud"] == 1000.0
    rows = [json.loads(l) for l in open(_m / "reversal_guard.jsonl")]; assert len(rows) == 1 and rows[0]["time_wita"].endswith("WITA")
    A = json.load(open(_m / "reversal_guard_alerts.json")); assert len(A["alerts"]) == 1 and A["alerts"][0]["acked"] is False
    A["alerts"][0]["acked"] = True; json.dump(A, open(_m / "reversal_guard_alerts.json", "w"))
    p2 = P(); [BK.revguard_check(p2, x, 1.0) for x in (125.0,)]; a2 = BK.revguard_check(p2, 119.0, 1.0); BK.revguard_emit("energy", "T2", p2, T, a2)
    A = json.load(open(_m / "reversal_guard_alerts.json")); assert [x["trade_id"] for x in A["alerts"]] == ["T2"]
def test_shadow_pnl_math():
    p = P(); run(p, [125.0]); a = BK.revguard_check(p, 119.0, 1.0)   # cur 1.9R, 100 sh
    assert a["shadow_bank_shares"] == 50 and a["shadow_bank_net_aud"] == 950.0     # 50 x 19
    assert a["shadow_lock_shares"] == 50 and a["shadow_lock_floor_net_aud"] == 625.0   # 1.25 x 10 x 50
    assert a["shadow_total_net_aud"] == 1575.0
def test_rule_log():
    assert any(r.get("id") == "tfo_revguard_20261030" for r in BK.RULE_LOG)
