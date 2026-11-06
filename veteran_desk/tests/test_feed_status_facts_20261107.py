"""Keeper status, session, entitlement, and freshness stay separate."""
import datetime as dt

from veteran_desk.feed_status_facts import classify, summarize
from veteran_desk.thf_home import feed_rows


SATURDAY = dt.datetime(2026, 11, 6, 17, 8, tzinfo=dt.timezone.utc)  # 2026-11-07 01:08 WITA


def test_weekend_eu_keeps_422_and_is_not_an_outage_or_usable():
    row = classify("eodhd_eu", dict(
        status="ok", age_s=30000, max_age_s=600, authorized=True, process_state="running",
        last_status=dict(code=422, message="Symbols limit reached", utc="2026-11-05T10:54:42+00:00")), SATURDAY)
    assert row["status"] == "ok"
    assert row["facts"]["entitlement"] == "authorized"
    assert row["facts"]["last_failure"]["code"] == 422
    assert row["facts"]["session_expected"] is False
    assert row["facts"]["fresh_usable"] is False
    assert row["facts"]["label"] == "session closed"
    assert "outage" not in row["facts"]["label"]


def test_crypto_is_expected_and_missing_evidence_stays_unknown():
    crypto = classify("kraken_ticker", dict(status="ok", age_s=10, max_age_s=120, process_state="running"), SATURDAY)
    assert crypto["facts"]["session_expected"] is True and crypto["facts"]["fresh_usable"] is True
    assert crypto["facts"]["entitlement"] == "unknown"
    blank = classify("sector_fill", dict(status="ok"), SATURDAY)
    assert blank["facts"]["process_running"] is None
    assert blank["facts"]["session_expected"] is None
    assert blank["facts"]["entitlement"] == "unknown"
    assert blank["facts"]["fresh_usable"] is None


def test_ok_count_is_not_a_usable_count_and_the_home_row_says_so():
    feeds = {("f%d" % i): dict(status="ok", age_s=10, max_age_s=30) for i in range(38)}
    feeds["eodhd_eu"] = dict(status="ok", age_s=10, max_age_s=30, authorized=True,
                             last_status=dict(code=422, message="Symbols limit reached"))
    summary = summarize(feeds, dt.datetime(2026, 11, 7, 12, tzinfo=dt.timezone.utc))
    assert summary["keeper_status"].get("ok") == 39
    assert summary["usable"] != summary["keeper_status"]["ok"]
    assert summary["ok_is_not_usable"] is True
    shown = feed_rows(dict(feeds={"eodhd_eu": classify("eodhd_eu", feeds["eodhd_eu"], SATURDAY)}), now=SATURDAY)
    assert shown[0]["status"] == "ok"
    assert "entitlement authorized" in shown[0]["facts"]
    assert "422" in shown[0]["facts"]
    assert shown[0]["usable"] is False
