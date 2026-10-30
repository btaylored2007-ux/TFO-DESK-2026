"""2026-10-30 paidbars: paid bars must match the Yahoo schema + completed-bar semantics exactly; Yahoo fallback on any gap."""
import datetime as dt, os, sys, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import paid_bars as PB
UTC = dt.timezone.utc

@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(PB, "LOGD", str(tmp_path)); PB._DC.clear(); PB._DIV.clear()

def _ms(t): return int(t.timestamp() * 1000)
def test_us_15m_schema_rth_only_and_sessions():
    t0 = dt.datetime(2026, 10, 30, 13, 30, tzinfo=UTC)   # 09:30 ET
    rows = [dict(t=_ms(t0 - dt.timedelta(minutes=15)), o=1, h=2, l=0.5, c=1.5, v=10),          # pre-market -> dropped
            dict(t=_ms(t0), o=1, h=2, l=0.5, c=1.5, v=10), dict(t=_ms(t0 + dt.timedelta(minutes=15)), o=1.5, h=2.5, l=1, c=2, v=0),
            dict(t=_ms(dt.datetime(2026, 10, 30, 20, 0, tzinfo=UTC)), o=1, h=1, l=1, c=1, v=5)]   # 16:00 ET post -> dropped
    b = PB.us_15m_from_rows(rows)
    assert [x["start"] for x in b] == [t0, t0 + dt.timedelta(minutes=15)]
    assert set(b[0]) == {"start", "o", "h", "l", "c", "v"} and b[0]["start"].tzinfo is not None
    assert b[1]["v"] is None            # Yahoo path: float(0) or None -> None
    many = [dict(t=_ms(t0 + dt.timedelta(days=k)), o=1, h=1, l=1, c=1, v=1) for k in range(-9, 1) if (t0 + dt.timedelta(days=k)).weekday() < 5]
    assert len({x["start"].date() for x in PB.us_15m_from_rows(many)}) == 5

def test_us_15m_completed_bar_semantics_unchanged():
    """manage()/try_fill() only use bars with start+15m <= now; the in-progress paid bar is skipped exactly like Yahoo's."""
    import hunt48_short_paper as H
    now = dt.datetime(2026, 10, 30, 14, 10, tzinfo=UTC)
    t = dict(ticker="X", direction="short", status="ARMED", armed_utc="2026-10-30T13:00:00+00:00", trigger=10.0, max_fill=9.5, stop=11.0, t1=9.0, t2=8.0, shares=10)
    part = [dict(start=dt.datetime(2026, 10, 30, 14, 0, tzinfo=UTC), o=10.5, h=10.6, l=9.0, c=9.2, v=100)]   # in progress at 14:10
    tt = copy.deepcopy(t); assert H.try_fill(tt, part, now) is None and tt["status"] == "ARMED"

def test_div_adjust_matches_yahoo_rule():
    d = dt.date
    rows = [(d(2026, 1, 1), 11.0, 9.0, 10.0), (d(2026, 1, 2), 11.0, 9.0, 10.0), (d(2026, 1, 5), 10.5, 9.5, 10.0)]
    out = PB.div_adjust(rows, [(d(2026, 1, 5), 0.5), (d(2027, 1, 1), 9.0), (d(2025, 1, 1), 9.0)])
    assert out[2] == dict(h=10.5, l=9.5, c=10.0)
    assert abs(out[0]["c"] - 9.5) < 1e-12 and abs(out[1]["h"] - 11.0 * 0.95) < 1e-12
    assert set(out[0]) == {"h", "l", "c"}

def test_asx_daily_requires_latest_session_row():
    rows = [dict(date="2026-10-29", high=11, low=9, close=10, adjusted_close=9.5), dict(date="2026-10-30", high=12, low=10, close=11, adjusted_close=11)]
    out = PB.asx_daily_from_rows(rows, dt.date(2026, 10, 30))
    assert out[0] == dict(h=11 * 0.95, l=9 * 0.95, c=9.5) and out[1] == dict(h=12.0, l=10.0, c=11.0)
    assert PB.asx_daily_from_rows(rows, dt.date(2026, 11, 2)) is None      # stale -> None -> Yahoo fallback

def test_asx_1h_end_labels_and_cover(monkeypatch):
    # 30 Oct 2026 Sydney (UTC+11): buckets start 10:00..16:00 local = 23:00(29th)..05:00 UTC; END = +1h
    starts = [dt.datetime(2026, 10, 29, 23, tzinfo=UTC) + dt.timedelta(hours=k) for k in range(7)]
    rows = [dict(timestamp=int(s.timestamp()), open=1, high=2, low=0.5, close=1.5, volume=100) for s in starts]
    b = PB.asx_1h_from_rows(rows)
    assert [x["t"] for x in b] == [s + dt.timedelta(hours=1) for s in starts] and set(b[0]) == {"t", "o", "h", "l", "c", "v"}
    start = dt.datetime(2026, 10, 30, 0, tzinfo=UTC); now = dt.datetime(2026, 10, 30, 3, 30, tzinfo=UTC)
    assert PB.asx_buckets_ended(now, start) == [dt.datetime(2026, 10, 30, h, tzinfo=UTC) for h in (0, 1, 2, 3)]
    class F:
        def __init__(s, r): s.r = r
        def eodhd(s, path, **q): return s.r
    monkeypatch.setattr(PB, "_pf", lambda: F(rows)); assert PB.asx_1h("BHP.AX", start, now) is not None
    gap = [r for i, r in enumerate(rows) if i != 2]                         # an ENDED bucket missing -> Yahoo
    monkeypatch.setattr(PB, "_pf", lambda: F(gap)); assert PB.asx_1h("BHP.AX", start, now) is None

def test_hourly_falls_back_and_logs(monkeypatch):
    calls = []
    yahoo = lambda y, s: calls.append(y) or [dict(t=s, o=1, h=1, l=1, c=1, v=None)]
    class F:
        def eodhd(s, path, **q): raise RuntimeError("eodhd HTTP 404")
    monkeypatch.setattr(PB, "_pf", lambda: F()); s = dt.datetime(2026, 10, 30, tzinfo=UTC)
    assert PB.hourly("BHP.AX", s, yahoo)[0]["c"] == 1 and PB.hourly("7203.T", s, yahoo) and calls == ["BHP.AX", "7203.T"]
    assert PB.STATS.get("yahoo_fallback", 0) >= 1 and PB.STATS.get("yahoo", 0) >= 1

def test_daily_hr_paid_then_fallback(monkeypatch):
    monkeypatch.setattr(PB, "asx_daily", lambda s, now=None: [dict(h=1.0, l=1.0, c=1.0)] if s == "A.AX" else None)
    st = {}; hr = lambda s: [dict(h=2.0, l=2.0, c=2.0)]
    assert PB.daily_hr("A.AX", hr, st) == [dict(h=1.0, l=1.0, c=1.0)] and st["A.AX"] == "ok"
    assert PB.daily_hr("B.AX", hr, st) == [dict(h=2.0, l=2.0, c=2.0)] and PB.daily_hr("7203.T", hr, st)[0]["c"] == 2.0

def test_us_fetch_error_returns_none(monkeypatch):
    class F:
        def massive(s, *a, **k): raise RuntimeError("massive HTTP 429")
    monkeypatch.setattr(PB, "_pf", lambda: F())
    assert PB.us_15m("URA") is None and PB.us_daily("URA") is None and PB.us_15m("BHP.AX") is None
