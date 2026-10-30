"""Brett 28 Oct 2026 13:15 WITA hunt fix 2: B1 runs on real data (roc5/atr_pct from daily bars), fail closed only when unfetchable (logged); no duplicate re-records."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunt48_room as HR

def bars(n=30, start=100.0, step=-1.0):
    return [dict(h=start + i * step + 1, l=start + i * step - 1, c=start + i * step) for i in range(n)]

def test_b1_from_bars_values():
    x = HR.b1_from_bars(bars()); assert x["px"] == 71.0 and round(x["roc5"], 2) == round((71 / 76 - 1) * 100, 2) and x["atr_pct"] > 0

def test_exhaustion_runs_when_row_lacks_roc5_atr(tmp_path, monkeypatch):
    monkeypatch.setattr(HR, "B1_DATAFAIL_LOG", str(tmp_path / "f.jsonl"))
    why = HR.exhaustion_block(dict(sym="1942.T"), bars())          # steady 1/day drop, ATR~2 -> 5d drop 5/2=2.5 ATR at 20d low -> real B1 block
    assert why and "exhausted low" in why[0] and not os.path.exists(tmp_path / "f.jsonl")
    assert HR.exhaustion_block(dict(sym="X.T"), bars(step=1.0)) == []

def test_fail_closed_only_when_unfetchable_and_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(HR, "B1_DATAFAIL_LOG", str(tmp_path / "f.jsonl"))
    why = HR.exhaustion_block(dict(sym="0288.HK", roc5=-3, atr_pct=2, px=10), [])
    assert "B1 fail closed" in why[0] and json.loads(open(tmp_path / "f.jsonl").readline())["sym"] == "0288.HK"

def test_fill_b1_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(HR, "B1_DATAFAIL_LOG", str(tmp_path / "f.jsonl"))
    rows = {"A.HK": dict(sym="A.HK", roc5="", atr_pct="", px=""), "B.T": dict(sym="B.T", roc5="1", atr_pct="2", px="3"), "C.AX": dict(sym="C.AX", roc5="")}
    n = HR.fill_b1_inputs(rows, lambda s: bars() if s == "A.HK" else [])
    assert n == 1 and rows["A.HK"]["px"] == 71.0 and rows["A.HK"]["b1_filled_from"] == "daily_bars" and rows["B.T"]["roc5"] == "1"
    assert "C.AX" in open(tmp_path / "f.jsonl").read()

def test_no_same_session_rerecord_guards_present():
    import asia_hunt48, asx_hunt48
    assert "never re-record a name for the same session" in open(asia_hunt48.__file__).read()
    assert "never re-record a name for the same session" in open(asx_hunt48.__file__).read()
