"""Hunt shape trial (Brett 2026-10-28 12:17 WITA): frozen shadow +5/-4 vs +3/-8, identical entries; funnel."""
import datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import hunt_shape_trial as H
UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 2, 0, 0, tzinfo=UTC)

@pytest.fixture
def tmp(tmp_path, monkeypatch):
    d = tmp_path / "trial"; monkeypatch.setattr(H, "D", str(d))
    for k, f in (("PREREG", "prereg.json"), ("PREREG_MD", "PREREGISTRATION.md"), ("BOOK", "trial_book.json"), ("SUMMARY", "summary.json"), ("FUNNEL", "funnel.json"), ("FUNNEL_LOG", "funnel_log.jsonl")):
        monkeypatch.setattr(H, k, str(d / f))
    monkeypatch.setattr(H, "FROZEN_DIR", str(tmp_path / "frozen")); return tmp_path

def bars(seq, start):
    return [dict(t=start + dt.timedelta(hours=i + 1), o=o, h=h, l=l, c=c) for i, (o, h, l, c) in enumerate(seq)]

def ledger(tmp, trades, name="hunt48_test"):
    p = tmp / name; p.mkdir(); f = p / "ledger.json"; f.write_text(json.dumps({"trades": trades})); return str(f)

def test_prereg_frozen_checksum_and_not_overwritten(tmp):
    a = H.register(T0); assert H.verify(a) and a["params"]["stopping_rule_n_closed"] == 50
    b = H.register(T0 + dt.timedelta(days=3)); assert b["start_utc"] == a["start_utc"]
    assert "Hypothesis" in open(H.PREREG_MD).read()

def test_tampered_params_halt(tmp):
    a = H.register(T0); a["params"]["arms"]["variant"]["stop_pct"] = 0.02; json.dump(a, open(H.PREREG, "w"))
    assert H.run(now=T0, ledgers=[]) is None and "MISMATCH" in json.load(open(H.SUMMARY))["error"]

def test_identical_entry_both_arms_variant_hits_target_control_hits_target_earlier(tmp):
    H.register(T0); e = T0 + dt.timedelta(hours=1)
    t = dict(id=1, ticker="ABC", direction="long", status="CLOSED", entry=100.0, entry_utc=e.isoformat(), shares=100, trigger=100, fees_usd=0)
    L = ledger(tmp, [t]); B = bars([(100, 103.5, 99.5, 103), (103, 105.5, 102, 105)], e)
    s = H.run(now=e + dt.timedelta(hours=80), ledgers=[L], bars_fn=lambda s: B)
    it = json.load(open(H.BOOK))["items"]["test:1"]
    assert it["entry"] == 100.0 and it["control"]["why"] == "target" and it["variant"]["why"] == "target"
    assert it["control"]["exit_px"] == 103 and it["variant"]["exit_px"] == 105
    assert s["cohorts"]["filled"]["variant"]["pnl_aud"] > s["cohorts"]["filled"]["control"]["pnl_aud"]

def test_stop_first_on_same_bar_and_short_side(tmp):
    r = H.simulate(100, T0, "short", bars([(100, 104.5, 94, 100)], T0), 0.05, 0.04, T0 + dt.timedelta(hours=72), T0 + dt.timedelta(hours=1))
    assert r["why"] == "stop" and r["exit_px"] == 104

def test_time_exit_at_72h(tmp):
    r = H.simulate(100, T0, "long", bars([(100, 101, 99, 100.5)] * 80, T0), 0.05, 0.04, T0 + dt.timedelta(hours=72), T0 + dt.timedelta(hours=100))
    assert r["why"] == "time" and r["status"] == "CLOSED"

def test_cleared_unfilled_shadow_filled_and_tagged_separately(tmp):
    H.register(T0); a = T0 + dt.timedelta(hours=1)
    t = dict(id=7, sym="XYZ.AX", direction="long", status="DISARMED", armed_utc=a.isoformat(), trigger=10.0, max_fill=10.02, shares=1000, q2usd=0.7022, est_cost_aud=20, clearance={"final": "CLEARED"})
    L = ledger(tmp, [t]); B = bars([(9.9, 10.05, 9.85, 10.0), (10.0, 10.6, 9.95, 10.5)], a)
    s = H.run(now=a + dt.timedelta(hours=100), ledgers=[L], bars_fn=lambda s: B)
    it = json.load(open(H.BOOK))["items"]["test:7"]
    assert it["cohort"] == "cleared_unfilled" and it["shadow_fill_px"] == 10.0 and "SHADOW" in it["shadow_tag"]
    assert s["cohorts"]["filled"]["variant"]["n_closed"] == 0 and s["cohorts"]["cleared_unfilled"]["variant"]["n_closed"] == 1

def test_gap_beyond_max_fill_no_shadow_fill():
    a = T0; t = dict(direction="long", trigger=10.0, max_fill=10.02, armed_utc=a.isoformat())
    assert H.shadow_fill(t, bars([(10.5, 10.7, 10.4, 10.6)], a), a + dt.timedelta(hours=24)) is None

def test_pre_start_trades_excluded(tmp):
    H.register(T0); t = dict(id=2, ticker="OLD", direction="long", status="FILLED", entry=50, entry_utc=(T0 - dt.timedelta(hours=5)).isoformat(), shares=10)
    s = H.run(now=T0 + dt.timedelta(hours=1), ledgers=[ledger(tmp, [t])], bars_fn=lambda s: [])
    assert json.load(open(H.BOOK))["items"] == {}

def test_funnel_counts(tmp):
    H.register(T0)
    T = [dict(id=1, sym="A", status="BENCH", trigger=1), dict(id=2, sym="B", status="DISARMED", trigger=1, clearance={"final": "CLEARED"}),
         dict(id=3, sym="C", status="CLOSED", trigger=1, entry=1, entry_utc="2026-10-01T00:00:00Z", clearance={"final": "CLEARED"})]
    H.run(now=T0, ledgers=[ledger(tmp, T)], bars_fn=lambda s: [])
    f = json.load(open(H.FUNNEL))["books"]["test"]["all_time"]
    assert {k: f[k] for k in ("candidates", "cleared", "triggered", "filled")} == dict(candidates=3, cleared=2, triggered=1, filled=1)

def test_never_writes_ledgers_or_live_shape():
    src = open(H.__file__).read()
    assert "hunt48_room" not in src and "FIXED_TP_PCT" not in src and "place_order" not in src

# --- Brett 28 Oct 13:15 WITA fix 1: cleared = final CLEARED, unique names ---
def test_is_cleared_requires_final_cleared():
    assert not H.is_cleared(dict(status="DISARMED")) and not H.is_cleared(dict(status="ARMED", clearance={"desk": "PENDING"}))
    assert not H.is_cleared(dict(clearance={"final": "BLOCKED"})) and H.is_cleared(dict(clearance={"final": "cleared"}))

def test_funnel_counts_unique_names(tmp):
    H.register(T0); a = (T0 + dt.timedelta(hours=1)).isoformat()
    rows = [dict(id=i, sym="0288.HK", status="DISARMED", armed_utc=a, recorded_utc=a, trigger=1, clearance={"final": "CLEARED"}) for i in range(4)]
    rows += [dict(id=9, sym="1177.HK", status="BENCH", armed_utc=a, recorded_utc=a, trigger=1), dict(id=10, sym="1177.HK", status="BENCH", armed_utc=a, recorded_utc=a, trigger=1)]
    L = ledger(tmp, rows, "hunt48_hk"); f = H.funnel([L], T0 + dt.timedelta(hours=2))["hk"]["all_time"]
    assert f["candidates"] == 2 and f["cleared"] == 1 and f["rows"] == 6

def test_duplicate_cleared_rows_trial_once(tmp):
    H.register(T0); a = (T0 + dt.timedelta(hours=1)).isoformat()
    rows = [dict(id=i, sym="0288.HK", status="DISARMED", armed_utc=a, trigger=10, clearance={"final": "CLEARED"}) for i in range(3)]
    assert len(H.eligible(T0, [ledger(tmp, rows)])) == 1

def test_amend_logs_without_changing_checksum(tmp):
    a = H.register(T0); H.amend("measurement fix", T0); H.amend("measurement fix", T0)
    b = json.load(open(H.PREREG)); assert len(b["amendments"]) == 1 and H.verify(b) and b["params_sha256"] == a["params_sha256"]
