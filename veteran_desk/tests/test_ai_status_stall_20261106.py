"""Provider chips stay unknown when the last check is old or the last ask failed."""
import datetime as dt

from veteran_desk import thf_chat_home as CH

NOW = dt.datetime(2026, 11, 6, 14, 7, tzinfo=dt.timezone.utc)

def _row(rows, name):
    return next(row for row in rows if row["id"] == name)

def _status(**kwargs):
    kwargs.setdefault("cd", {})
    kwargs.setdefault("hb", {})
    kwargs.setdefault("probe", {})
    kwargs.setdefault("recent", None)
    kwargs.setdefault("sgw", {})
    return CH.ai_status(now=NOW, **kwargs)

def test_fresh_signed_in_chatgpt_with_a_good_ask_is_up():
    rows = _status(gpt=dict(state="logged_in", checked_utc=NOW.isoformat(), last_ask_ok=True, busy=False, queue=0), grok_provider={})
    row = _row(rows, "chatgpt")
    assert row["state"] == "up"

def test_busy_and_timed_out_chatgpt_are_not_shown_as_up():
    busy = _status(gpt=dict(state="logged_in", checked_utc=NOW.isoformat(), busy=True, queue=1, last_ask_ok=True), grok_provider={})
    assert _row(busy, "chatgpt")["state"] == "busy"
    failed = _status(gpt=dict(state="logged_in", checked_utc=NOW.isoformat(), busy=False, queue=0, last_ask_ok=False, last_err="timeout"), grok_provider={})
    assert _row(failed, "chatgpt")["state"] == "unknown"
    assert "timed out" in _row(failed, "chatgpt")["note"]

def test_stale_grok_success_is_unknown_and_names_the_old_timeout():
    old = (NOW - dt.timedelta(hours=2)).isoformat()
    rows = _status(gpt=dict(state="down"), probe={"grok": {"ok": True, "checked_utc": old}},
                   grok_provider=dict(ok=False, checked_utc=old, detail="grok timeout; process group stopped"))
    row = _row(rows, "grok")
    assert row["state"] == "unknown"
    assert row["state"] != "up"
    assert "timeout" in row["note"]
