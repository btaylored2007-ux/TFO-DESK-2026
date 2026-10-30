"""Brett 28 Oct 13:40 approve-all #1: ESSENTIALS utilities floor 15% on new buys/rebalances only. PAPER."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import essentials_book as eb

def _picks():
    ps = [dict(tk="U%d" % i, sector="Utilities", industry="Utilities - Regulated Electric") for i in range(3)]
    ps += [dict(tk="E%d" % i, sector="Energy", industry="Oil & Gas") for i in range(10)]
    return ps

def test_floor_lifts_utilities_to_15pct():
    ps = _picks(); w = {d["tk"]: 1 / 13 for d in ps}
    w2 = eb.apply_util_floor(w, ps)
    u = sum(v for k, v in w2.items() if k.startswith("U")) * eb.CORE_CAP / eb.NAV0
    assert u >= 0.15 - 1e-6 and abs(sum(w2.values()) - 1) < 1e-6
    assert max(w2.values()) <= eb.NAME_CAP * eb.NAV0 / eb.CORE_CAP + 1e-9

def test_floor_noop_when_already_met():
    ps = _picks(); w = {d["tk"]: (0.1 if d["tk"].startswith("U") else 0.07) for d in ps}
    assert eb.apply_util_floor(w, ps) == w

def test_reserve_and_no_forced_sells():
    L = eb.load.__wrapped__() if hasattr(eb.load, "__wrapped__") else None
    L = {"positions": {"U0": dict(tk="U0", qty=1000, mark_aud=50.0, entry_px_aud=50.0, sector="Utilities"),
                       "E0": dict(tk="E0", qty=1000, mark_aud=300.0, entry_px_aud=300.0, sector="Energy")},
         "staged": [dict(tk="U1", target_aud=40000, sector="Utilities")]}
    ux = eb.util_exposure(L, lookup={})
    assert ux["held_aud"] == 50000 and ux["staged_aud"] == 40000
    assert ux["reserve_aud"] == 150000 - 90000
    assert set(L["positions"]) == {"U0", "E0"}   # nothing sold

def test_non_utility_fill_leaves_reserve():
    import tempfile
    L = dict(cash_aud=eb.NAV0, positions={}, staged=[], fills=[], costs_paid_aud=0.0)
    o = dict(tk="Y", mkt="ASX", sleeve="core", target_aud=900000.0, ccy="AUD", sector="Energy")
    fills, lg = eb.FILLS, eb.log
    eb.FILLS = os.path.join(tempfile.mkdtemp(), "f.csv"); eb.log = lambda m: None
    import theme_exposure as _te; _cb = _te.check_buy; _te.check_buy = lambda *a, **k: (True, "")   # theme cap stubbed (tested in #5)
    try: f = eb._fill(L, o, 10.0, {"AUD": 1.0}, "test")
    finally: eb.FILLS, eb.log = fills, lg; _te.check_buy = _cb
    assert f["notional_aud"] + f["cost_aud"] <= (eb.NAV0 - eb.SPARE) - eb.UTIL_FLOOR * eb.NAV0 + 1e-6
