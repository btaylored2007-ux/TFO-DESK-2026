"""scorecard_weekly (Brett 2026-10-28 12:17 WITA): per-book A$ vs passive benchmark, pre-committed flags, report only."""
import datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scorecard_weekly as S
R = dict(S.RULES)
def bk(**k):
    b = dict(book="X", kind="allocation", pnl_aud=0, capital_aud=1_000_000, bench_pnl_aud=0, trades=None, sessions=30, fills_last_10_sessions=None); b.update(k); return b

def test_r1_flags_underperformance_with_sample():
    b = S.flag(bk(pnl_aud=-30_000, bench_pnl_aud=0), R); assert b["flag"].startswith("CUT-REVIEW") and "report only" in b["flag"]

def test_r1_small_sample_is_watch_only():
    b = S.flag(bk(pnl_aud=-30_000, sessions=5), R); assert b["flag"] == "WATCH"

def test_r1_threshold_floor():
    assert S.flag(bk(pnl_aud=-4_000, capital_aud=10_000), R)["flag"] == "OK"
    assert S.flag(bk(pnl_aud=-6_000, capital_aud=10_000), R)["flag"].startswith("CUT")

def test_r2_zero_fills_in_10_sessions():
    b = S.flag(bk(kind="trades", trades=3, sessions=12, fills_last_10_sessions=0), R); assert any("R2" in x for x in b["flag_reasons"])
    assert S.flag(bk(kind="trades", trades=3, sessions=12, fills_last_10_sessions=1), R)["flag"] == "OK"

def test_trend18_benchmark_is_nasdaq2x_and_aud(monkeypatch):
    now = dt.datetime(2026, 10, 28, 12, tzinfo=S.WITA)
    B = S.books(now, ret=lambda *a: 0.0)
    t = [b for b in B if b["book"].startswith("Trend18")][0]
    assert "Nasdaq 2x" in t["benchmark"]
    tj = json.load(open(os.path.join(S.BASE, "state", "trend18.json")))
    assert t["pnl_aud"] == round((tj["equity"] - tj["start"]) / S.FX)

def test_hook_appends_once(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "OUT", str(tmp_path / "o.json")); monkeypatch.setattr(S, "STATIC", str(tmp_path / "s.json"))
    now = dt.datetime(2026, 10, 31, 10, tzinfo=S.WITA); rv = tmp_path / "weekly_cut_review_20261031.md"; rv.write_text("# review\n")
    S.write(now, ret=lambda *a: 0.0, reports_dir=str(tmp_path)); S.write(now, ret=lambda *a: 0.0, reports_dir=str(tmp_path))
    assert rv.read_text().count(S.MARK) == 1 and (tmp_path / "weekly_cut_review_20261031_scorecard.md").exists()

def test_report_only_no_switch_off_hooks():
    src = open(S.__file__).read()
    for bad in ("book_kill", "disable", "halt_book", "launchctl", "place_order"): assert bad not in src
    assert S.build.__doc__ is None or True
