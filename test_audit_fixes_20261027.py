"""Regression tests for the 2026-10-27 full-audit fixes (Brett approved 19:35 WITA). OFFLINE: every ledger/status/event path is
redirected to a tmp dir; no network, no broker. Source of the findings: research/full_audit_20261027/REPORT.md."""
import datetime as dt, json, os, sys
import pytest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import hunt48_short_paper as H
from zoneinfo import ZoneInfo
UTC = dt.timezone.utc; NY = ZoneInfo("America/New_York")

@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(H, "SD", str(tmp_path)); monkeypatch.setattr(H, "LEDGER", str(tmp_path / "ledger.json"))
    monkeypatch.setattr(H, "EVENTS", str(tmp_path / "events.jsonl")); monkeypatch.setattr(H, "STATUS", str(tmp_path / "status.json"))
    monkeypatch.setattr(H, "SECTOR_CACHE", str(tmp_path / "sector_cache.json"))
    monkeypatch.setattr(H.IC, "has_borrow", lambda *a, **k: True); monkeypatch.setattr(H.IC, "borrow_pa", lambda *a, **k: 0.03)
    yield

def _bar(start, o, h, l, c): return dict(start=start, o=o, h=h, l=l, c=c)
SPEC = dict(ticker="XYZ", direction="short", trigger=50.0, max_fill=49.9, stop=52.0, t1=49.0, t2=47.8, shares=1000, atr=1.0, sector="TECH")

# ---------------------------------------------------------------- C1: exit = fill + N NY sessions
def test_C1_session_exit_counts_ny_sessions_dst_and_holidays():
    f = dt.datetime(2026, 10, 26, 18, 0, tzinfo=UTC)                      # Mon 26 Oct 14:00 ET
    assert H.session_exit(f) == dt.datetime(2026, 10, 29, 15, 55, tzinfo=NY).astimezone(UTC)    # Thu close
    f = dt.datetime(2026, 10, 29, 15, 0, tzinfo=UTC)                      # Thu 29 Oct -> Tue 3 Nov (skips weekend, EST after 1 Nov)
    te = H.session_exit(f); assert te.astimezone(NY).date() == dt.date(2026, 11, 3) and te.astimezone(NY).hour == 15 and te == dt.datetime(2026, 11, 3, 20, 55, tzinfo=UTC)
    f = dt.datetime(2026, 11, 24, 15, 0, tzinfo=UTC)                      # Tue 24 Nov: Wed 25, (Thu 26 holiday), Fri 27 early close, Mon 30
    assert H.session_exit(f, 2) == dt.datetime(2026, 11, 27, 12, 55, tzinfo=NY).astimezone(UTC)
    assert H.session_exit(f, 3).astimezone(NY).date() == dt.date(2026, 11, 30)
    assert H.session_exit(dt.datetime(2026, 10, 31, 3, 0, tzinfo=UTC), 1).astimezone(NY).date() == dt.date(2026, 11, 3)   # Sat -> counts from Mon

def test_C1_new_trade_after_29oct_holds_and_does_not_time_exit():   # flipped audit F2
    now = dt.datetime(2026, 11, 3, 15, 0, tzinfo=UTC)                    # Tue 3 Nov 10:00 ET
    L = H.new_ledger("audit"); t = H.upsert_trade(L, dict(SPEC), now)
    assert dt.datetime.fromisoformat(t["time_exit_utc"]) > now
    assert H.try_fill(t, [_bar(now + dt.timedelta(minutes=15), 50.1, 50.2, 49.9, 49.95)], now + dt.timedelta(minutes=31), 0.75) == "filled"
    assert dt.datetime.fromisoformat(t["time_exit_utc"]) == H.session_exit(now + dt.timedelta(minutes=30))   # Fri 6 Nov 15:55 ET
    assert "NY sessions" in t["time_exit_rule"]
    ev = H.manage(t, [_bar(now + dt.timedelta(minutes=30), 49.95, 49.97, 49.9, 49.92)], now + dt.timedelta(minutes=46), daily=None)
    assert "time_exit" not in ev and t["status"] == "FILLED"

def test_C1_refuses_past_exit_and_accepts_future_exit_in_spec():
    now = dt.datetime(2026, 11, 3, 15, 0, tzinfo=UTC); L = H.new_ledger("audit")
    with pytest.raises(H.ArmRefused): H.upsert_trade(L, dict(SPEC, time_exit_utc="2026-10-29T14:00:00+00:00"), now)
    t = H.upsert_trade(L, dict(SPEC, time_exit_utc="2026-11-05T20:55:00+00:00"), now)   # no duplicate-kwarg TypeError
    assert t["time_exit_utc"] == "2026-11-05T20:55:00+00:00" and len(L["trades"]) == 1
    with pytest.raises(H.ArmRefused): H.upsert_trade(L, dict(SPEC, time_exit_utc="2026-11-01T00:00:00+00:00"), now)   # re-level path too

def test_C1_apply_plan_skips_refused_and_continues(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "bars_15m", lambda s: [])
    now = dt.datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
    p = tmp_path / "plan.json"; p.write_text(json.dumps(dict(label="x", set=[dict(SPEC, ticker="OLD", time_exit_utc="2026-10-29T14:00:00+00:00"), dict(SPEC, ticker="NEW")])))
    L = H.apply_plan(str(p), now); assert [t["ticker"] for t in L["trades"]] == ["NEW"]
    assert any(json.loads(x)["kind"] == "arm_refused" for x in open(H.EVENTS))

def test_C1_manage_never_exits_on_exit_before_entry():
    e = dt.datetime(2026, 11, 3, 15, 30, tzinfo=UTC)
    t = dict(ticker="XYZ", status="FILLED", direction="short", entry=50.0, entry_utc=e.isoformat(), shares=100, open_qty=100, half_done=False, trail=None, stop=52.0,
             trigger=50.5, t1=48.0, lowest=49.9, atr=1.0, last_px=49.9, ext_days=0, time_exit_utc="2026-10-29T14:00:00+00:00", exits=[], realised_usd=0.0, fees_usd=0.0)
    assert H.manage(t, [], e + dt.timedelta(minutes=20)) == [] and t["status"] == "FILLED" and t["time_exit_repaired"]
    assert dt.datetime.fromisoformat(t["time_exit_utc"]) == H.session_exit(e)

def test_C1_existing_open_trade_exit_unchanged():
    e = dt.datetime(2026, 10, 26, 18, 0, tzinfo=UTC)   # like the live FLG/ECH: filled Mon, legacy Thu exit stays as is
    t = dict(ticker="FLG", direction="short", entry=10.0, entry_utc=e.isoformat(), shares=100, open_qty=100, half_done=False, trail=None, stop=10.5,
             trigger=10.1, t1=9.6, lowest=9.9, atr=0.2, last_px=9.95, ext_days=0, time_exit_utc="2026-10-29T14:00:00+00:00", exits=[], realised_usd=0.0, fees_usd=0.0)
    H.manage(t, [], e + dt.timedelta(hours=3)); assert t["time_exit_utc"] == "2026-10-29T14:00:00+00:00" and "time_exit_repaired" not in t

# ---------------------------------------------------------------- H9: stops run before (optional) daily data
def test_H9_daily_failure_does_not_skip_stop(monkeypatch):
    now = dt.datetime(2026, 10, 29, 15, 0, tzinfo=UTC)   # after the legacy exit
    L = H.new_ledger("audit"); t = H.upsert_trade(L, dict(SPEC), now - dt.timedelta(days=3))
    t.update(status="FILLED", entry=49.95, entry_utc=(now - dt.timedelta(days=3)).isoformat(), open_qty=1000, lowest=49.9,
             time_exit_utc="2026-10-29T14:00:00+00:00", _last_bar=(now - dt.timedelta(minutes=45)).isoformat())
    json.dump(L, open(H.LEDGER, "w"), default=str)
    monkeypatch.setattr(H, "now_utc", lambda: now); monkeypatch.setattr(H, "us_rth", lambda n: True)
    monkeypatch.setattr(H, "live_audusd", lambda fb: 0.70); monkeypatch.setattr(H, "hi_events", lambda *a: [])
    monkeypatch.setattr(H, "bars_15m", lambda s: [_bar(now - dt.timedelta(minutes=30), 51.0, 52.5, 50.9, 52.2)])   # stop 52 hit
    def boom(s): raise RuntimeError("yfinance daily download failed")
    monkeypatch.setattr(H, "bars_daily", boom)
    H.tick(); d = json.load(open(H.LEDGER))["trades"][0]
    assert d["status"] == "STOPPED" and d["exits"][0]["why"] == "stop" and "data_error" not in d

def test_H9_daily_failure_in_profit_means_time_exit_not_crash():
    te = dt.datetime(2026, 10, 29, 14, 0, tzinfo=UTC)
    t = dict(ticker="XYZ", direction="short", entry=50.0, entry_utc=(te - dt.timedelta(days=2)).isoformat(), shares=100, open_qty=100, half_done=False, trail=None,
             stop=52.0, trigger=50.5, t1=48.0, lowest=49.0, atr=1.0, last_px=49.0, ext_days=0, time_exit_utc=te.isoformat(), exits=[], realised_usd=0.0, fees_usd=0.0)
    def boom(): raise RuntimeError("down")
    ev = H.manage(t, [], te + dt.timedelta(minutes=1), None, daily_fn=boom)
    assert ev == ["time_exit"] and t["daily_error"] == "down"

def test_H9_daily_fetched_only_when_extension_considered():
    calls = []
    t = dict(ticker="XYZ", direction="short", entry=50.0, entry_utc="2026-10-27T15:00:00+00:00", shares=100, open_qty=100, half_done=False, trail=None,
             stop=52.0, trigger=50.5, t1=48.0, lowest=49.9, atr=1.0, last_px=49.9, ext_days=0, time_exit_utc="2026-10-30T19:55:00+00:00", exits=[], realised_usd=0.0, fees_usd=0.0)
    H.manage(t, [], dt.datetime(2026, 10, 28, 15, 0, tzinfo=UTC), None, daily_fn=lambda: calls.append(1))
    assert calls == []


# ---------------------------------------------------------------- H3: fill committed only after the gates pass
def _h3_book(monkeypatch, daily):
    now = dt.datetime(2026, 10, 28, 15, 0, tzinfo=UTC)           # Wed 28 Oct 11:00 ET (RTH)
    L = H.new_ledger("audit")
    for i, tk in enumerate(("AAA", "BBB")):
        t = H.upsert_trade(L, dict(SPEC, ticker=tk, trigger=10.0, max_fill=9.9, stop=11.0, t1=9.5, t2=8.9, shares=100, atr=0.5, sector="S%d" % i), now - dt.timedelta(hours=5))
        t.update(status="FILLED", entry=9.95, entry_utc=(now - dt.timedelta(hours=4)).isoformat(), open_qty=100, lowest=9.9,
                 time_exit_utc="2026-10-30T19:55:00+00:00", _last_bar=(now - dt.timedelta(minutes=30)).isoformat())
        if tk == "BBB": t.update(direction="long", stop=9.0, t1=10.5, t2=11.1)   # keep the direction-balance gate out of the way
    n = H.upsert_trade(L, dict(SPEC, ticker="DDD", trigger=20.0, max_fill=19.9, stop=21.0, t1=19.5, t2=18.9, shares=100, atr=0.5, sector="S9"), now - dt.timedelta(hours=1))
    json.dump(L, open(H.LEDGER, "w"), default=str)
    bars = {"DDD": [_bar(now - dt.timedelta(minutes=30), 20.0, 20.05, 19.93, 19.95)]}
    monkeypatch.setattr(H, "now_utc", lambda: now); monkeypatch.setattr(H, "us_rth", lambda n: True)
    monkeypatch.setattr(H, "live_audusd", lambda fb: 0.70); monkeypatch.setattr(H, "hi_events", lambda *a: [])
    monkeypatch.setattr(H, "bars_15m", lambda s: bars.get(s, [])); monkeypatch.setattr(H, "bars_daily", daily)
    return now

def test_H3_daily_failure_keeps_trade_armed_not_filled(monkeypatch):   # flipped audit F4
    def boom(s): raise RuntimeError("yfinance daily download failed")
    _h3_book(monkeypatch, boom); H.tick()
    L2 = json.load(open(H.LEDGER)); d = [t for t in L2["trades"] if t["ticker"] == "DDD"][0]
    assert d["status"] == "ARMED" and d["entry"] is None and d["open_qty"] == 0 and d["fees_usd"] == 0.0
    assert "gate error" in d["data_error"]
    assert sum(t["status"] == "FILLED" for t in L2["trades"]) == 2
    assert any(json.loads(x)["kind"] == "gate_error_kept_armed" for x in open(H.EVENTS))

def test_H3_slot_gate_exception_keeps_trade_armed(monkeypatch):
    _h3_book(monkeypatch, lambda s: [])
    monkeypatch.setattr(H, "room_check", lambda t, d, e=None: dict(ok=True, room_R=None, level=None, cost_R=0.02, why="ok"))
    def boom(*a, **k): raise RuntimeError("sector lookup blew up")
    monkeypatch.setattr(H, "mix_gate", boom); H.tick()
    d = [t for t in json.load(open(H.LEDGER))["trades"] if t["ticker"] == "DDD"][0]
    assert d["status"] == "ARMED" and d["entry"] is None

def test_H3_gates_pass_then_fill_commits(monkeypatch):
    _h3_book(monkeypatch, lambda s: [])
    monkeypatch.setattr(H, "room_check", lambda t, d, e=None: dict(ok=True, room_R=None, level=None, cost_R=0.02, why="ok"))
    H.tick()
    d = [t for t in json.load(open(H.LEDGER))["trades"] if t["ticker"] == "DDD"][0]
    assert d["status"] == "FILLED" and d["entry"] and d["room_check"]["ok"]

def test_H3_fourth_fill_still_shadowed_by_max_open(monkeypatch):
    now = _h3_book(monkeypatch, lambda s: [])
    L = json.load(open(H.LEDGER)); c = dict([t for t in L["trades"] if t["ticker"] == "AAA"][0], ticker="CCC", id=99, sector="S5"); L["trades"].append(c)
    json.dump(L, open(H.LEDGER, "w"), default=str)
    monkeypatch.setattr(H, "room_check", lambda t, d, e=None: dict(ok=True, room_R=None, level=None, cost_R=0.02, why="ok"))
    H.tick()
    d = [t for t in json.load(open(H.LEDGER))["trades"] if t["ticker"] == "DDD"][0]
    assert d["status"] == "SHADOW" and "max 3 open" in d["shadow_reason"]


# ---------------------------------------------------------------- H5: A$2.5k cap measured at max_fill, tol 1.00, recheck at fill
def _HC(monkeypatch):
    monkeypatch.setattr(sys, "argv", [sys.argv[0]]); import hunt_clearance as HC; return HC

def test_H5_clearance_risk_at_max_fill_resizes_oversize(monkeypatch):
    HC = _HC(monkeypatch); assert HC.RISK_TOL == 1.00
    BR = {"XYZ": dict(sector_key="TECH", _closes=[])}
    s = dict(ticker="XYZ", direction="short", trigger=50.0, max_fill=49.0, stop=52.0, shares=875)   # A$2.5k at trigger, A$3.75k at max_fill
    out, meta = HC.risk_review([s], BR, 0.70)
    r = out["XYZ"]; assert r["verdict"] == "PASS" and r["resized_shares"] == 583 and s["shares"] == 583
    assert r["risk_aud"] <= 2500 and abs(s["stop"] - s["max_fill"]) * 583 / 0.70 <= 2500

def test_H5_clearance_no_hidden_10pct_tolerance(monkeypatch):
    HC = _HC(monkeypatch); BR = {"XYZ": dict(sector_key="TECH", _closes=[])}
    s = dict(ticker="XYZ", direction="short", trigger=50.0, max_fill=50.0, stop=52.0, shares=920)   # A$2,629 (was PASS under 1.10)
    r = HC.risk_review([s], BR, 0.70)[0]["XYZ"]; assert r["resized_shares"] == 875 and r["risk_aud"] <= 2500
    s = dict(ticker="XYZ", direction="short", trigger=50.0, max_fill=50.0, stop=52.0, shares=800)    # within cap -> untouched
    r = HC.risk_review([s], BR, 0.70)[0]["XYZ"]; assert "resized_shares" not in r and s["shares"] == 800 and r["verdict"] == "PASS"

def test_H5_clearance_resize_respects_lot(monkeypatch):
    HC = _HC(monkeypatch); BR = {"7203.T": dict(sector_key="AUTO", _closes=[])}
    s = dict(ticker="7203.T", direction="long", trigger=3000.0, max_fill=3030.0, stop=2900.0, shares=3000, lot=100)
    r = HC.risk_review([s], BR, 100.0)[0]["7203.T"]   # 100 JPY per AUD
    assert r["verdict"] == "PASS" and r["resized_shares"] % 100 == 0 and r["resized_shares"] * 130 / 100.0 <= 2500

def test_H5_engine_rechecks_risk_at_actual_fill():
    now = dt.datetime(2026, 11, 3, 15, 0, tzinfo=UTC); L = H.new_ledger("audit")
    t = H.upsert_trade(L, dict(SPEC, trigger=50.0, max_fill=49.0, stop=52.0, shares=875), now)
    assert H.try_fill(t, [_bar(now + dt.timedelta(minutes=15), 50.1, 50.2, 49.4, 49.5)], now + dt.timedelta(minutes=31), 0.70) == "filled"
    assert t["shares_planned"] == 875 and t["shares"] == t["open_qty"] < 875
    assert abs(t["entry"] - t["stop"]) * t["shares"] / 0.70 <= 2500 + 1e-6

def test_H5_engine_within_cap_untouched():
    now = dt.datetime(2026, 11, 3, 15, 0, tzinfo=UTC); L = H.new_ledger("audit")
    t = H.upsert_trade(L, dict(SPEC, trigger=50.0, max_fill=49.9, stop=52.0, shares=500), now)
    assert H.try_fill(t, [_bar(now + dt.timedelta(minutes=15), 50.1, 50.2, 49.9, 49.95)], now + dt.timedelta(minutes=31), 0.70) == "filled"
    assert t["shares"] == t["open_qty"] == 500 and "shares_planned" not in t


# ---------------------------------------------------------------- H6: no fill / no extension with earnings inside time_exit + MAX_EXT_DAYS
@pytest.fixture(autouse=True)
def _iso_earn(monkeypatch, tmp_path):
    if hasattr(H, "EARN_DIR"): monkeypatch.setattr(H, "EARN_DIR", str(tmp_path / "clearance"))
    yield

def _ext_trade(earn=None, v2=True):
    te = dt.datetime(2026, 11, 5, 20, 55, tzinfo=UTC)
    t = dict(ticker="XYZ", direction="short", entry=50.0, entry_utc=(te - dt.timedelta(days=2)).isoformat(), shares=100, open_qty=100, half_done=False, trail=None,
             stop=52.0, trigger=50.5, t1=48.0, lowest=49.0, atr=1.0, last_px=49.0, ext_days=0, time_exit_utc=te.isoformat(), exits=[], realised_usd=0.0, fees_usd=0.0)
    t["_te_rule"] = "v2"   # pre-fix open trades carry v2 too (event-adjust), so it is NOT the discriminator
    if v2: t["time_exit_rule"] = "fill + 3 NY sessions (audit C1)"
    if earn: t["next_earnings_utc"] = earn
    return t, te

def test_H6_earnings_horizon_is_time_exit_plus_10_sessions():
    assert H.earnings_horizon(dt.datetime(2026, 11, 5, 20, 55, tzinfo=UTC)).astimezone(NY).date() == dt.date(2026, 11, 19)   # 6, 9-13, 16-19
    assert H.earnings_horizon(dt.datetime(2026, 11, 20, 20, 55, tzinfo=UTC)).astimezone(NY).date() == dt.date(2026, 12, 7)    # Thanksgiving skipped

def test_H6_fill_blocked_when_earnings_inside_max_hold(monkeypatch):
    _h3_book(monkeypatch, lambda s: [])
    monkeypatch.setattr(H, "room_check", lambda t, d, e=None: dict(ok=True, room_R=None, level=None, cost_R=0.02, why="ok"))
    L = json.load(open(H.LEDGER)); [t for t in L["trades"] if t["ticker"] == "DDD"][0]["next_earnings_utc"] = "2026-11-06T12:30:00+00:00"
    json.dump(L, open(H.LEDGER, "w"), default=str); H.tick()
    d = [t for t in json.load(open(H.LEDGER))["trades"] if t["ticker"] == "DDD"][0]
    assert d["status"] == "SHADOW" and "earnings 2026-11-06" in d["shadow_reason"] and d["entry"] is None

def test_H6_fill_allowed_when_earnings_beyond_horizon_or_unknown(monkeypatch, tmp_path):
    for earn in ("2026-12-15T12:30:00+00:00", None):
        _h3_book(monkeypatch, lambda s: [])
        monkeypatch.setattr(H, "room_check", lambda t, d, e=None: dict(ok=True, room_R=None, level=None, cost_R=0.02, why="ok"))
        L = json.load(open(H.LEDGER)); [t for t in L["trades"] if t["ticker"] == "DDD"][0]["next_earnings_utc"] = earn
        json.dump(L, open(H.LEDGER, "w"), default=str); H.tick()
        assert [t for t in json.load(open(H.LEDGER))["trades"] if t["ticker"] == "DDD"][0]["status"] == "FILLED"

def test_H6_earnings_from_clearance_file_fallback(tmp_path):
    p = tmp_path / "clearance" / "20261027"; p.mkdir(parents=True)
    (p / "XYZ.json").write_text(json.dumps(dict(calendar=dict(next_earnings_utc="2026-11-10T21:00:00+00:00"))))
    t, te = _ext_trade(); assert H.next_earnings(t, te - dt.timedelta(days=2)) == dt.datetime(2026, 11, 10, 21, 0, tzinfo=UTC)   # H6 hardening: explicit 'now' (past dates are skipped)
    assert H.earnings_block(t, te - dt.timedelta(days=2))

def test_H6_extension_blocked_by_earnings_v2_trade():
    t, te = _ext_trade("2026-11-12T12:30:00+00:00")
    ev = H.manage(t, [], te + dt.timedelta(minutes=1), None, daily_fn=lambda: (_ for _ in ()).throw(AssertionError("daily must not be fetched")))
    assert ev == ["time_exit"] and t["status"] == "CLOSED"

def test_H6_extension_still_allowed_without_earnings(monkeypatch):
    monkeypatch.setattr(H, "extension_ok", lambda d, long=False: True)
    t, te = _ext_trade("2027-01-28T21:00:00+00:00")
    assert H.manage(t, [], te + dt.timedelta(minutes=1), None, daily_fn=lambda: [1]) == ["extended"]

def test_H6_preexisting_open_position_not_altered(monkeypatch):
    monkeypatch.setattr(H, "extension_ok", lambda d, long=False: True)
    t, te = _ext_trade("2026-11-12T12:30:00+00:00", v2=False)   # opened before the fix -> behaviour unchanged
    assert H.manage(t, [], te + dt.timedelta(minutes=1), None, daily_fn=lambda: [1]) == ["extended"]

def test_H6_clearance_us_horizon_covers_extension(monkeypatch):
    HC = _HC(monkeypatch); now = dt.datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    b = dict(asset_class="equity", sweep=dict(rsi="50", roc5="0", atr_pct="3", venue="ibkr"), technical={}, calendar=dict(next_earnings_utc="2026-11-10T21:00:00+00:00"))
    s = dict(ticker="XYZ", direction="short", trigger=50.0, stop=52.0, time_exit_utc="2026-10-30T19:55:00+00:00")
    v = HC.behavior_review(s, b, now); assert v["verdict"] == "FAIL" and any("earnings" in r for r in v["reasons"]) and v["next_earnings_utc"]
    monkeypatch.setattr(HC, "ASX_ONLY", True); assert HC.behavior_review(s, b, now)["verdict"] == "PASS"   # ASX/Asia books keep +2d (exit before events)


# ---------------------------------------------------------------- H7: apply-plan survives BENCH/SHADOW/FILLED rows; ledger before events
def _h7_ledger(now):
    L = H.new_ledger("audit")
    for tk, st in (("BEN", "BENCH"), ("SHD", "SHADOW"), ("FIL", "FILLED"), ("ARM", "ARMED")):
        t = H.upsert_trade(L, dict(SPEC, ticker=tk), now - dt.timedelta(hours=3)); t["status"] = st
    json.dump(L, open(H.LEDGER, "w"), default=str); return L

def test_H7_apply_plan_relevels_bench_and_rearms_shadow(monkeypatch, tmp_path):
    now = dt.datetime(2026, 11, 3, 15, 0, tzinfo=UTC); _h7_ledger(now)
    monkeypatch.setattr(H, "bars_15m", lambda s: [])
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(dict(label="x", cut=[dict(ticker="ARM", reason="r"), dict(ticker="FIL", reason="r")],
                                    set=[dict(SPEC, ticker=tk, trigger=51.0) for tk in ("BEN", "SHD", "FIL", "NEW")])))
    L = H.apply_plan(str(plan), now)
    by = {}
    for t in L["trades"]: by.setdefault(t["ticker"], []).append(t["status"])
    assert by["ARM"] == ["CUT"]                                   # cut applied even though a later cut/set row is refused
    assert by["BEN"] == ["ARMED"] and [t for t in L["trades"] if t["ticker"] == "BEN"][0]["relevel_from"] == "BENCH"
    assert sorted(by["SHD"]) == ["ARMED", "SHADOW"]               # SHADOW record kept, fresh ARMED row
    assert by["FIL"] == ["FILLED"] and by["NEW"] == ["ARMED"]     # FILLED never re-levelled, plan continued past it
    ev = [json.loads(x) for x in open(H.EVENTS)]
    assert {e["kind"] for e in ev} >= {"cut_refused", "arm_refused", "armed"}
    disk = json.load(open(H.LEDGER)); assert {t["ticker"] for t in disk["trades"] if t["status"] == "ARMED"} == {"BEN", "SHD", "NEW"}

def test_H7_ledger_written_before_events(monkeypatch, tmp_path):
    now = dt.datetime(2026, 11, 3, 15, 0, tzinfo=UTC); _h7_ledger(now); monkeypatch.setattr(H, "bars_15m", lambda s: [])
    order = []; real_atomic, real_event = H.atomic, H.event
    monkeypatch.setattr(H, "atomic", lambda p, o: (order.append("ledger" if p == H.LEDGER else "other"), real_atomic(p, o))[1])
    monkeypatch.setattr(H, "event", lambda k, **kw: (order.append("event"), real_event(k, **kw))[1])
    plan = tmp_path / "plan.json"; plan.write_text(json.dumps(dict(set=[dict(SPEC, ticker="NEW")])))
    H.apply_plan(str(plan), now)
    assert order.index("ledger") < order.index("event")


# ---------------------------------------------------------------- H8: max 3 open is PER BOOK (one definition: bench == engine)
def _h8_desk(tmp_path, us_filled=3):
    d = tmp_path / "desk"; (d / "state" / "calendar").mkdir(parents=True); (d / "static").mkdir()
    for b in ("hunt48_short", "hunt48_short_gpt"):
        (d / "state" / b).mkdir(); (d / "state" / b / "ledger.json").write_text(json.dumps(dict(trades=[dict(status="FILLED")] * us_filled)))
    return d

def test_H8_cal_bench_slots_per_book_not_global(tmp_path, monkeypatch):
    import cal_paper_bench as CB
    d = _h8_desk(tmp_path); monkeypatch.setattr(CB, "LEDGER", str(d / "state" / "calendar" / "paper_bench_ledger.json"))
    seen = []
    def step(t, bars, now, slots): seen.append(slots); return slots
    monkeypatch.setattr(CB, "step", step); monkeypatch.setattr(CB, "yahoo_of", lambda s, base=None: "EURUSD=X")
    cal = dict(staged=[dict(venue="oanda", top3=True, sym="EUR_USD", direction="long", trigger=1.1, max_fill=1.101, stop=1.09, t1=1.11, t2=1.12, shares=1000,
                            notional_usd=1100, est_cost_usd=1, risk_aud=2500)])
    CB.run(now=dt.datetime(2026, 10, 28, 3, 0, tzinfo=UTC), fetch=lambda y, s: [], cal=cal, base=str(d))
    assert CB.hunt_filled(str(d)) == 6 and seen == [3]          # 6 US positions elsewhere do NOT consume this book's 3 slots

def test_H8_book_open_matches_engine_definition():
    import cal_paper_bench as CB
    tr = [dict(status=s) for s in ("FILLED", "PARTIAL", "ARMED", "SHADOW", "CLOSED", "FILLED")]
    assert CB.book_open(tr) == 3 and CB.MAX_OPEN == H.MAX_OPEN == 3

def test_H8_asx_bench_slots_per_book(tmp_path, monkeypatch):
    import cal_paper_bench as CB, asx_paper_bench as AB
    src = open(AB.__file__).read()
    assert "CB.hunt_filled(base)" not in src and "CB.book_open(L[\"trades\"])" in src


# ---------------------------------------------------------------- H4: Day P&L = today's change; H14: freshness from the price time + data errors
def _WM():
    import warroom_markets as WM; return WM

def _closed_trade(L, tk, when, usd):
    t = H.upsert_trade(L, dict(SPEC, ticker=tk), when - dt.timedelta(days=1))
    t.update(status="CLOSED", entry=50.0, entry_utc=(when - dt.timedelta(hours=5)).isoformat(), open_qty=0, realised_usd=usd, fees_usd=0.0,
             exits=[dict(qty=1000, px=49.0, why="T2", utc=when.isoformat())]); return t

def test_H4_old_closed_trade_is_not_day_pnl():   # flipped audit F6
    now = dt.datetime(2026, 11, 10, 3, 0, tzinfo=UTC); L = H.new_ledger("audit")
    _closed_trade(L, "OLD", now - dt.timedelta(days=10), 700.0)
    st = H.write_status(L, 0.70, now)
    assert st["pnl_today_aud"] == 0 and st["pnl_since_entry_aud"] > 900   # ~A$1,000 less modelled costs
    wm = _WM().us_book("us_main", "x", st, L, now)
    assert wm["pnl_today_aud"] == 0 and wm["pnl_since_entry_aud"] == st["pnl_since_entry_aud"] and wm["realized_today_aud"] == 0.0

def test_H4_day_change_counts_todays_close_and_resets_next_day():
    d0 = dt.datetime(2026, 11, 10, 1, 0, tzinfo=UTC); L = H.new_ledger("audit")   # 09:00 WITA
    _closed_trade(L, "OLD", d0 - dt.timedelta(days=10), 700.0); s0 = H.write_status(L, 0.70, d0)
    _closed_trade(L, "NEW", d0 + dt.timedelta(hours=2), 350.0)
    st = H.write_status(L, 0.70, d0 + dt.timedelta(hours=3))
    assert 480 < st["pnl_today_aud"] <= 500.0 and st["pnl_today_aud"] == round(st["pnl_since_entry_aud"] - s0["pnl_since_entry_aud"], 2)
    st = H.write_status(L, 0.70, d0 + dt.timedelta(days=1))       # next WITA day: new baseline
    assert st["pnl_today_aud"] == 0 and L["day_base"]["wita_date"] == "2026-11-11"

def test_H4_old_status_without_basis_is_not_shown_as_day_pnl():
    wm = _WM().us_book("us_main", "x", dict(pnl_today_aud=-1360.0, trades=[], audusd=0.7), None, dt.datetime(2026, 10, 27, 12, 0, tzinfo=UTC))
    assert wm["pnl_today_aud"] is None

def test_H14_stale_price_not_green_while_ny_open():   # flipped audit F9
    now = dt.datetime(2026, 10, 28, 15, 0, tzinfo=UTC); L = H.new_ledger("audit")
    t = H.upsert_trade(L, dict(SPEC, ticker="AAA"), now - dt.timedelta(days=2))
    t.update(status="FILLED", entry=49.95, entry_utc=(now - dt.timedelta(days=2)).isoformat(), open_qty=100, lowest=49.9, last_px=49.0,
             last_px_utc=(now - dt.timedelta(days=2)).isoformat(), time_exit_utc="2026-10-30T19:55:00+00:00")
    st = H.write_status(L, 0.70, now); WM = _WM()
    assert st["price_asof_utc"] == t["last_px_utc"]
    b = WM.us_book("us_main", "x", st, L, now)
    f = WM.us_fresh(b, {"open": True}, now); assert f["level"] == "red" and f["basis"] == "price"
    f = WM.us_fresh(b, {"open": False}, now); assert f["level"] == "green" and f["basis"] == "status"   # market closed: status time

def test_H14_data_errors_in_status_and_card(monkeypatch):
    now = dt.datetime(2026, 10, 28, 15, 0, tzinfo=UTC); L = H.new_ledger("audit")
    t = H.upsert_trade(L, dict(SPEC, ticker="AAA"), now - dt.timedelta(hours=2)); json.dump(L, open(H.LEDGER, "w"), default=str)
    monkeypatch.setattr(H, "now_utc", lambda: now); monkeypatch.setattr(H, "us_rth", lambda n: True)
    monkeypatch.setattr(H, "live_audusd", lambda fb: 0.70); monkeypatch.setattr(H, "hi_events", lambda *a: [])
    def boom(s): raise RuntimeError("yahoo 429")
    monkeypatch.setattr(H, "bars_15m", boom); H.tick()
    st = json.load(open(H.STATUS)); assert st["data_error_count"] == 1 and "429" in st["data_errors"][0]["error"]
    WM = _WM(); f = WM.us_fresh(WM.us_book("us_main", "x", st, None, now), {"open": False}, now)
    assert f["data_errors"] == 1 and f["level"] == "amber"
    monkeypatch.setattr(H, "bars_15m", lambda s: [_bar(now - dt.timedelta(minutes=30), 51.0, 51.1, 50.9, 51.0)]); H.tick()
    assert json.load(open(H.STATUS))["data_error_count"] == 0   # clears once data flows again


# ---------------------------------------------------------------- H1: paper-bench stops gapped through fill at the open (+5bp); whole-lot halves
def _h1_trade(direction="long", qty=1000, stop=95.0, t1=105.0):
    t0 = dt.datetime(2026, 10, 27, 0, 0, tzinfo=UTC)
    return t0, dict(direction=direction, status="FILLED", armed_utc=t0.isoformat(), fill=100.0, fill_utc=t0.isoformat(), stop=stop, stop_live=stop,
                    qty_open=qty, shares=qty, realised_usd=0.0, t1=t1, t2=110.0 if direction == "long" else 90.0, q2usd=1.0, est_cost_usd=0.0, last_bar_utc=t0.isoformat())

def test_H1_long_gap_through_stop_fills_at_open_plus_slip():
    import cal_paper_bench as CB
    t0, t = _h1_trade()
    CB.step(t, [dict(t=t0 + dt.timedelta(hours=17), o=88.0, h=89.0, l=87.0, c=88.5)], t0 + dt.timedelta(hours=18), 3)
    assert t["status"] == "CLOSED" and t["exit"] == "STOP"
    assert t["realised_usd"] == pytest.approx((88.0 * 0.9995 - 100.0) * 1000)

def test_H1_long_intrabar_stop_fills_at_stop_plus_slip():
    import cal_paper_bench as CB
    t0, t = _h1_trade()
    CB.step(t, [dict(t=t0 + dt.timedelta(hours=1), o=97.0, h=98.0, l=94.0, c=96.0)], t0 + dt.timedelta(hours=2), 3)
    assert t["realised_usd"] == pytest.approx((95.0 * 0.9995 - 100.0) * 1000)

def test_H1_short_gap_through_stop_fills_at_open_plus_slip():
    import cal_paper_bench as CB
    t0, t = _h1_trade("short", stop=105.0, t1=95.0)
    CB.step(t, [dict(t=t0 + dt.timedelta(hours=17), o=112.0, h=113.0, l=111.0, c=112.5)], t0 + dt.timedelta(hours=18), 3)
    assert t["exit"] == "STOP" and t["realised_usd"] == pytest.approx(-(112.0 * 1.0005 - 100.0) * 1000)

def test_H1_half_exit_whole_lots():
    import cal_paper_bench as CB
    t0, t = _h1_trade(qty=7, t1=101.0)
    CB.step(t, [dict(t=t0 + dt.timedelta(hours=1), o=100, h=101.5, l=99.5, c=101)], t0 + dt.timedelta(hours=2), 3)
    assert t["qty_open"] == 4 and isinstance(t["qty_open"], int) and t["realised_usd"] == pytest.approx(3.0) and t["stop_live"] == 100.0


# ---------------------------------------------------------------- H2+M4: US uses the ONE hunt48_room.room at max_fill; <200 bars / cost_R>0.15 -> BENCH
def _h2_t(**k):
    d = dict(ticker="XYZ", direction="short", trigger=50.0, max_fill=49.9, stop=52.0, t1=48.5, t2=46.0, shares=1000); d.update(k); return d
_H2_FLAT = [dict(h=60, l=55, c=58)] * 250        # 1y history, nothing below 49.9 -> T2 binds

def test_H2_us_partial_history_fails_closed():
    rc = H.room_check(_h2_t(), [dict(h=60, l=55, c=58)] * 150)
    assert rc["ok"] is False and "daily bars" in rc["why"] and isinstance(rc["room_R"], float)

def test_H2_us_t2_binds_and_passes_with_room():
    rc = H.room_check(_h2_t(), _H2_FLAT); assert rc["ok"] is True and 1.0 <= rc["room_R"] < 1.86 and "T2" in rc["basis"]
    rc = H.room_check(_h2_t(t2=49.0), _H2_FLAT); assert rc["ok"] is False and "to T2" in rc["why"]

def test_H2_us_cost_cap_015(monkeypatch):
    import ibkr_costs as IC
    monkeypatch.setattr(IC, "us_side", lambda q, px, side: 0.2 * q)   # 0.4/share round trip on 2.1 R -> ~0.21R
    rc = H.room_check(_h2_t(t2=40.0), _H2_FLAT); assert rc["cost_R"] > 0.15 and rc["ok"] is False and "cap" in rc["why"]

def test_H2_us_room_at_max_fill_not_fill_price():
    a = H.room_check(_h2_t(), _H2_FLAT, entry=50.0); b = H.room_check(_h2_t(), _H2_FLAT)
    assert a["room_R"] == b["room_R"] and a["fill_entry"] == 50.0

def test_H2_engine_matches_single_definition():
    import hunt48_room as HR
    rc = H.room_check(_h2_t(), _H2_FLAT); rt = dict(direction="short", max_fill=49.9, stop=52.0, t2=46.0, cost_R=rc["cost_R"])
    HR.room(rt, _H2_FLAT); assert abs(rt["room_R"] - rc["room_R"]) < 0.002

def test_H2_asx_asia_room_defaults_unchanged():
    import hunt48_room as HR
    t = dict(direction="short", max_fill=10.0, stop=10.3, t2=9.55, cost_R=0.3)
    assert HR.room(t, [dict(h=11, l=10.5, c=10.8)] * 60) is None     # no min_bars / no 0.15 cap unless the caller asks (London cap 0.30)


# ---------------------------------------------------------------- H11: ibkr_costs.has_borrow fails CLOSED (no cache / cache > 24h)
import ibkr_costs as _IC_H11; _H11_REAL_HAS_BORROW = _IC_H11.has_borrow   # captured at import, before the autouse _iso stub
def _h11_cache(tmp_path, monkeypatch, age_h=None, names=None):
    import ibkr_costs as IC
    p = tmp_path / "ibkr_borrow.json"; monkeypatch.setattr(IC, "CACHE", str(p)); monkeypatch.setattr(IC, "has_borrow", _H11_REAL_HAS_BORROW)
    if age_h is not None:
        u = (dt.datetime.now(UTC) - dt.timedelta(hours=age_h)).isoformat()
        p.write_text(json.dumps(dict(updated_utc=u, us=dict(names=names or {}))))
    return IC

def test_H11_no_cache_fails_closed(tmp_path, monkeypatch):
    IC = _h11_cache(tmp_path, monkeypatch); assert IC.has_borrow("XYZ", 100) is False

def test_H11_stale_cache_fails_closed(tmp_path, monkeypatch):
    IC = _h11_cache(tmp_path, monkeypatch, 25, dict(XYZ=dict(ok=True, available=10000, fee_pa=0.01))); assert IC.has_borrow("XYZ", 100) is False

def test_H11_fresh_cache_decides(tmp_path, monkeypatch):
    IC = _h11_cache(tmp_path, monkeypatch, 2, dict(XYZ=dict(ok=True, available=10000, fee_pa=0.01)))
    assert IC.has_borrow("XYZ", 100) is True and IC.has_borrow("XYZ", 20000) is False and IC.has_borrow("ABS", 1) is False
    assert IC.has_borrow("BHP.AX", 1) is False   # no asx section -> fail closed
