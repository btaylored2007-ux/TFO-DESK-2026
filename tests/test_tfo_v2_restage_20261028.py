import datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tfo_v2 as v
T = dt.datetime(2026, 10, 28, 6, 0, tzinfo=dt.timezone.utc)
def _s(orders): return {"orders": orders, "runs": {}, "exit_flags": {}, "data_errors": {}}
def _o(sym, st, vu="2026-10-27T22:00:00Z", setup="S4", **k): return dict(id="X" + sym, setup=setup, sym=sym, status=st, valid_until_utc=vu, **k)
def test_expired_unheld_is_restaged():
    # Live S4 is shadow-disabled (Brett 2026-10-28 19:42); restage_needed skips disabled setups.
    # Pin enabled=True here so this test locks the restage logic, not the live shadow flag.
    cfg = v.config(); cfg = {**cfg, "setups": {**cfg["setups"], "S4": {**cfg["setups"]["S4"], "enabled": True, "shadow": False}}}
    r = v.restage_needed(_s([_o("XLE", "EXPIRED")]), cfg, {"positions": {}}, T)
    assert r == {"S4": {"XLE": "XXLE"}}
def test_disabled_setup_not_restaged():
    cfg = v.config(); cfg = {**cfg, "setups": {**cfg["setups"], "S4": {**cfg["setups"]["S4"], "enabled": False}}}
    assert v.restage_needed(_s([_o("XLE", "EXPIRED")]), cfg, {"positions": {}}, T) == {}
def test_held_staged_filled_old_disabled_skipped():
    o = [_o("USO", "EXPIRED"), _o("GLD", "EXPIRED"), _o("GLD", "STAGED"), _o("TLT", "EXPIRED", vu="2026-10-20T00:00:00Z"),
         _o("SPY", "EXPIRED", setup="S2"), _o("EFA", "EXPIRED", restaged_to="Y")]
    assert v.restage_needed(_s(o), v.config(), {"positions": {"USO": {}}}, T) == {}
def test_law_params_and_rails_kept():
    L = v.config()["law"]
    assert L["risk_pct"] == 0.01 and L["max_open"] == 15 and L["s4_top_n"] == 5
    assert L["heat_pct"] == 0.03 and L["max_loss_pct"] == 0.08 and L["cap_us"] == 300000.0 and L["gap_mult_stock"] == 2.0
def test_theme_gate_fails_closed(monkeypatch):
    import theme_exposure
    monkeypatch.setattr(theme_exposure, "check_buy", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    ok, why = v.theme_gate("XLE", 50000)
    assert not ok and "fail closed" in why
def test_tech_blocked_when_red(monkeypatch):
    monkeypatch.setattr(v, "us_tech_red", lambda: True)
    assert not v.theme_gate("XLK", 50000)[0] and not v.theme_gate("ARKK", 50000)[0]
    assert not v.theme_gate("MSFT", 50000, "Technology")[0]
