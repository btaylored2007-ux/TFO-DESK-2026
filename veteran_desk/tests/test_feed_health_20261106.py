"""EU cap rotation and Asia bar freshness. No network and no ledger writes."""
import datetime as dt
from zoneinfo import ZoneInfo

from veteran_desk.feed_health import allocate_rest, asia_data_status, asia_session_open, eu_quote_word, us_rth_open

UTC = dt.timezone.utc

def test_asia_open_file_with_null_bar_is_not_fresh():
    now = dt.datetime(2026, 11, 6, 2, 0, tzinfo=UTC)  # 13:00 Sydney, ASX open
    record = dict(markets_in_session=["ASX"], last_bar_utc=None, syms_with_bars_8h=0, rows=0)
    assert asia_data_status(record, now) == "stale_underlying"

def test_asia_closed_does_not_pretend_bars_are_fresh():
    now = dt.datetime(2026, 11, 7, 12, 0, tzinfo=UTC)  # Saturday
    record = dict(markets_in_session=[], last_bar_utc=None, syms_with_bars_8h=0)
    assert asia_data_status(record, now) == "session_closed"

def test_asia_holiday_and_dst_session():
    christmas = dt.datetime(2026, 12, 25, 1, 0, tzinfo=UTC)
    assert asia_session_open("ASX", christmas, ("Australia/Sydney", "10:00", "16:12")) is False
    wednesday = dt.datetime(2026, 11, 4, 1, 0, tzinfo=UTC)  # 12:00 Sydney AEDT
    assert asia_session_open("ASX", wednesday, ("Australia/Sydney", "10:00", "16:12")) is True
    assert wednesday.astimezone(ZoneInfo("Australia/Sydney")).hour == 12

def test_us_rth_is_not_open_before_the_bell():
    # 2026-11-06 20:43 WITA = 07:43 America/New_York, before 09:30. Not a closed-bug.
    before = dt.datetime(2026, 11, 6, 12, 43, tzinfo=UTC)
    bell = dt.datetime(2026, 11, 6, 14, 30, tzinfo=UTC)
    assert us_rth_open(before) is False
    assert us_rth_open(bell) is True
    assert us_rth_open(dt.datetime(2026, 11, 26, 15, 0, tzinfo=UTC)) is False  # Thanksgiving

def test_eu_rotation_respects_cap_and_does_not_enable_quotes():
    plan = allocate_rest(["ASC.L", "VOD.L", "RIO.L", "HSBA.L", "BP.L", "SHEL.L"], ["HSBA.L"], cap=3, rotate=1,
                          entitled=["ASC.L", "VOD.L", "RIO.L", "HSBA.L", "BP.L", "SHEL.L"])
    assert plan["subscribe"][0] == "HSBA.L"
    assert len(plan["subscribe"]) == 3
    assert plan["quote_stream"] is False
    assert "ASC.L" in plan["subscribe"] or "ASC.L" in plan["rotated_out"]
    blocked = allocate_rest(["ASC.L", "ZZ.L"], ["HSBA.L"], cap=5, entitled=["HSBA.L"])
    assert blocked["subscribe"] == ["HSBA.L"]
    assert "ASC.L" in blocked["not_entitled"]
    unknown = allocate_rest(["RIO.L"], [], cap=1, entitled=None)
    assert unknown["entitlement"] == "unknown" and unknown["subscribe"] == ["RIO.L"]

def test_eu_quote_off_is_not_reported_up():
    assert eu_quote_word(False, 10, 0) == "off"
    assert eu_quote_word(True, 0, 0) == "off: quota"
