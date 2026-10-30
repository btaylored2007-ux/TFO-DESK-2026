"""Brett 2026-10-28 20:59 WITA approved hunt fixes 1-3 (PAPER)."""
import datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunt48_short_paper as H
UTC = dt.timezone.utc
def _t(**k):
    t = dict(direction="short", status="FILLED", entry=10.0, entry_utc="2026-10-26T18:00:00+00:00", stop=11.0, trigger=10.0, t1=9.0, t2=8.5, atr=0.4,
             open_qty=100, half_done=False, trail=None, lowest=10.0, ext_days=0, realised_usd=0.0, fees_usd=0.0, exits=[], last_px=10.0,
             time_exit_utc="2026-10-30T19:55:00+00:00", ticker="X")
    t.update(k); return t
def _b(h, m, o, hi, lo, c, day=27): return dict(start=dt.datetime(2026, 10, day, h, m, tzinfo=UTC), o=o, h=hi, l=lo, c=c)
NOW = dt.datetime(2026, 10, 27, 20, 0, tzinfo=UTC)
def test_fix1_no_thesis_exit_on_first_bar():
    t = _t(); H.manage(t, [_b(13, 30, 10.3, 10.5, 10.2, 10.45)], NOW)   # 09:30 ET bar closes > level+0.5ATR -> ignored
    assert t["status"] == "FILLED"
def test_fix1_needs_half_atr_buffer():
    t = _t(); H.manage(t, [_b(13, 45, 10.1, 10.2, 10.05, 10.15)], NOW)   # +0.15 < 0.2 buffer
    assert t["status"] == "FILLED"
    t = _t(); H.manage(t, [_b(13, 45, 10.1, 10.3, 10.05, 10.25)], NOW)   # +0.25 > 0.2 buffer, not first bar
    assert t["status"] == "CLOSED" and "0.5 ATR" in t["exit_reason"]
def test_fix1_hard_stop_unchanged_on_first_bar():
    t = _t(); H.manage(t, [_b(13, 30, 10.9, 11.2, 10.8, 11.1)], NOW)
    assert t["status"] == "STOPPED"
def _armed(**k):
    t = dict(ticker="X", direction="short", status="ARMED", trigger=10.0, max_fill=9.8, stop=10.6, t1=9.4, t2=9.0, shares=100, atr=0.3,
             armed_utc="2026-10-27T13:00:00+00:00", fees_usd=0.0, time_exit_utc="2026-10-30T19:55:00+00:00")
    t.update(k); return t
def test_fix2_no_short_fill_last_2h_stays_armed():
    t = _armed(); ev = H.try_fill(t, [_b(18, 30, 10.05, 10.1, 9.85, 9.9)], NOW, 0.65)   # 14:30 ET bar -> blocked
    assert ev is None and t["status"] == "ARMED" and t.get("late_session_skip")
def test_fix2_late_bar_past_does_not_miss():
    t = _armed(); ev = H.try_fill(t, [_b(19, 0, 9.8, 9.8, 9.5, 9.5)], NOW, 0.65)   # would be MISSED, but late -> ignored
    assert ev is None and t["status"] == "ARMED"
def test_fix2_midday_short_still_fills():
    t = _armed(); ev = H.try_fill(t, [_b(15, 0, 10.05, 10.1, 9.85, 9.9)], NOW, 0.65)   # 11:00 ET
    assert ev in ("filled", "shadow") and t["status"] != "ARMED"
def test_fix2_window_edges():
    assert not H.in_late_session(dt.datetime(2026, 10, 27, 18, 0, tzinfo=UTC))   # 14:00 ET bar end = boundary, allowed
    assert H.in_late_session(dt.datetime(2026, 10, 27, 18, 15, tzinfo=UTC))
    assert H.in_late_session(dt.datetime(2026, 11, 27, 16, 15, tzinfo=UTC))      # early close 13:00 ET -> 11:15 ET blocked
import json, tempfile
def _summ(tmp, D, rows):
    os.makedirs(os.path.join(tmp, D), exist_ok=True); json.dump(dict(meta={}, books=dict(main=rows)), open(os.path.join(tmp, D, "summary.json"), "w"))
def test_fix3_bench_needs_clearance(monkeypatch):
    import setup_hash as SH
    tmp = tempfile.mkdtemp(); monkeypatch.setattr(H, "CLEARANCE_DIR", tmp); monkeypatch.setattr(H, "BOOK", "")
    now = dt.datetime(2026, 10, 28, 15, 0, tzinfo=UTC)   # 23:00 WITA 28 Oct -> clearance day 20261028
    t = _armed(bench=True)
    assert H.bench_clearance(t, now) == (False, "no clearance summary 20261028")
    _summ(tmp, "20261028", {"X": dict(final="BLOCKED", why=["exhausted"])}); assert not H.bench_clearance(t, now)[0]
    _summ(tmp, "20261028", {"X": dict(final="CLEARED", setup_hash="deadbeef")}); assert "hash" in H.bench_clearance(t, now)[1]
    _summ(tmp, "20261028", {"X": dict(final="CLEARED", setup_hash=SH.setup_hash(t))}); assert H.bench_clearance(t, now)[0]
    _summ(tmp, "20261028", {"Y": dict(final="CLEARED")}); assert not H.bench_clearance(t, now)[0]
    assert H.bench_clearance(dict(t, cleared="20261028"), now)[0]                       # same-day stamp from cleared_plan
    assert not H.bench_clearance(dict(t, cleared="20261027"), now)[0]                   # stale stamp -> falls to summary (Y only)
