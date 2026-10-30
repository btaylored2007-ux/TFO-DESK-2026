"""2026-10-30 asiafix: EODHD earnings merge (jp/asx/lse) + duration-aware final rescan slot."""
import datetime as dt, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import eodhd_earnings as EE
T = dt.date(2026, 10, 30)
ROWS = [{"code": "7203.TSE", "report_date": "2026-11-05"}, {"code": "7203.TSE", "report_date": "2027-02-05"},
        {"code": "BHP.AU", "report_date": "2027-02-17"}, {"code": "VOD.LSE", "report_date": "2026-11-11"},
        {"code": "0700.HK", "report_date": "2026-11-12"}, {"code": "OLD.TSE", "report_date": "2026-10-01"}, {"code": "BAD.TSE", "report_date": "x"}]
def test_parse_maps_suffix_and_earliest_future():
    assert EE.parse(ROWS, "jp", T) == {"7203.T": "2026-11-05"}
    assert EE.parse(ROWS, "asx", T) == {"BHP.AX": "2027-02-17"}
    assert EE.parse(ROWS, "lse", T) == {"VOD.L": "2026-11-11"}
def test_paused_markets_out_of_scope():
    for m in ("hk", "kr", "tw", "sg"): assert EE.next_dates(m, T, fetch=lambda a, b: None) == {}
def test_fill_missing_and_stale_only_unknown_stays_absent():
    out = EE.fill({"A.T": "2026-09-01", "B.T": "2026-12-01"}, {"A.T": "2026-11-20", "B.T": "2027-01-10"}, ["A.T", "B.T", "C.T"], T)
    assert out["A.T"] == "2026-11-20"      # stale yahoo -> eodhd
    assert out["B.T"] == "2026-12-01"      # yahoo future & earlier -> kept
    assert "C.T" not in out                # no date anywhere -> still UNKNOWN (fail closed)
def test_fill_takes_earlier_eodhd():
    assert EE.fill({"A.T": "2027-01-10"}, {"A.T": "2026-11-20"}, ["A.T"], T)["A.T"] == "2026-11-20"
def test_fetch_error_gives_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(EE, "CACHE", str(tmp_path))
    bad = lambda a, b: types.SimpleNamespace(ok=False, data=None)
    assert EE.next_dates("jp", T, fetch=bad) == {}
    def boom(a, b): raise RuntimeError("net")
    assert EE.next_dates("jp", T, fetch=boom) == {}
def test_fetch_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(EE, "CACHE", str(tmp_path)); n = []
    def f(a, b): n.append(1); return types.SimpleNamespace(ok=True, data={"earnings": ROWS})
    assert EE.next_dates("jp", T, fetch=f)["7203.T"] == "2026-11-05"; EE.next_dates("lse", T, fetch=f); assert len(n) == 1
def test_final_slot_finishes_before_cutoff():
    import intraday_rescan as I
    d = dt.date(2026, 11, 2)
    for m in ("jp", "asx", "lse"):
        tz, ss, td = I.sessions(m); c = I._at(d, tz, ss[-1][1]); s = I.slots_for_day(m, d)
        assert s and s[-1] + dt.timedelta(minutes=I.RUN_BUDGET_MIN[m]) <= c - dt.timedelta(minutes=I.STOP_BEFORE_CLOSE_MIN)
        assert s == sorted(set(s))
def test_paused_markets_schedule_unchanged():
    import intraday_rescan as I
    for m in ("hk", "kr", "tw", "sg"): assert m not in I.RUN_BUDGET_MIN
def test_due_refuses_start_that_cannot_finish(monkeypatch, tmp_path):
    import intraday_rescan as I
    monkeypatch.setattr(I, "SD", str(tmp_path)); d = dt.date(2026, 11, 2); tz, ss, td = I.sessions("jp")
    cut = I._at(d, tz, ss[-1][1]) - dt.timedelta(minutes=I.STOP_BEFORE_CLOSE_MIN)
    fin = I.slots_for_day("jp", d)[-1]
    assert I.due("jp", fin + dt.timedelta(minutes=1), {})
    assert not I.due("jp", cut - dt.timedelta(minutes=I.RUN_BUDGET_MIN["jp"] - 1), {})
    assert not I.due("jp", cut + dt.timedelta(minutes=1), {})
def test_clearance_eodhd_pick():
    import hunt_clearance as H
    now = dt.datetime(2026, 10, 30, 3, tzinfo=dt.timezone.utc)
    assert H._eodhd_pick(None, "2026-11-02", now)
    assert H._eodhd_pick("2026-10-20", "2026-11-02", now)              # recent past Yahoo -> upcoming EODHD
    assert H._eodhd_pick("2026-12-01T00:00:00+00:00", "2026-11-02", now)  # earlier future wins
    assert not H._eodhd_pick("2026-11-02", "2026-12-01", now)
    assert not H._eodhd_pick("2026-10-20", None, now)                  # no EODHD -> unchanged
    assert not H._eodhd_pick(None, None, now)                          # nothing -> stays UNKNOWN (fail closed)
