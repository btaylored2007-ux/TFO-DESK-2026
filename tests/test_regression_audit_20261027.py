"""PERMANENT regression locks for the 2026-10-27 full-audit fixes (research/full_audit_20261027/REPORT.md, VERIFY.md).
Written by the independent verifier. OFFLINE + hermetic: every ledger/status/event/sector/earnings path goes to tmp; no network,
no broker, no orders. If one of these fails, a fix has been undone - read VERIFY.md before 'fixing' the test.
Plain unittest (no pytest needed). Run: python3 -m unittest tests.test_regression_audit_20261027 -v   (or: python3 -m unittest discover -s tests)
KNOWN GAP tests are reported as SKIPPED while the gap is open, and pass once the proposed fix lands."""
import copy, datetime as dt, inspect, json, os, random, sys
import contextlib, pathlib, shutil, tempfile, unittest


# ------------------------------------------------------------ tiny stand-ins for the pytest helpers this file used (stdlib only)
_MISSING = object()
class _MonkeyPatch:
    def __init__(self): self._undo = []
    def setattr(self, obj, name, value, raising=True):
        old = getattr(obj, name, _MISSING)
        if old is _MISSING and raising: raise AttributeError(f"{obj!r} has no attribute {name!r}")
        self._undo.append((obj, name, old)); setattr(obj, name, value)
    def undo(self):
        for obj, name, old in reversed(self._undo):
            if old is _MISSING:
                try: delattr(obj, name)
                except AttributeError: pass
            else: setattr(obj, name, old)
        self._undo.clear()
class _Approx:
    def __init__(self, v, rel=None, abs=None): self.v, self.rel, self.abs = v, rel, abs
    def __eq__(self, other):
        tol = max(self.abs if self.abs is not None else 1e-12, (self.rel if self.rel is not None else 1e-6) * abs(self.v))
        return other is not None and abs(other - self.v) <= tol
    def __repr__(self): return f"approx({self.v!r})"
def approx(v, rel=None, abs=None): return _Approx(v, rel=rel, abs=abs)
@contextlib.contextmanager
def raises(exc):
    try: yield
    except exc: return
    raise AssertionError(f"did not raise {exc.__name__}")
def parametrize(names, values):
    names = [n.strip() for n in names.split(",")]
    def deco(fn):
        fn._params = [dict(zip(names, v if len(names) > 1 else (v,))) for v in values]; return fn
    return deco
def xfail(reason="", strict=False):
    def deco(fn): fn._known_gap = reason; return fn
    return deco
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from zoneinfo import ZoneInfo
UTC = dt.timezone.utc; NY = ZoneInfo("America/New_York")
_ARGV = list(sys.argv)
sys.argv = [sys.argv[0]]          # engine/clearance read --book from argv at import; never inherit pytest's argv
import hunt48_short_paper as H
import ibkr_costs as IC
_REAL_HAS_BORROW, _REAL_BORROW_PA = IC.has_borrow, IC.borrow_pa
sys.argv = _ARGV


def _iso(monkeypatch, tmp_path):
    """Runs before every test: all engine paths to a private tmp dir, no borrow/FX/news network."""
    monkeypatch.setattr(H, "SD", str(tmp_path)); monkeypatch.setattr(H, "LEDGER", str(tmp_path / "ledger.json"))
    monkeypatch.setattr(H, "EVENTS", str(tmp_path / "events.jsonl")); monkeypatch.setattr(H, "STATUS", str(tmp_path / "status.json"))
    monkeypatch.setattr(H, "SECTOR_CACHE", str(tmp_path / "sector_cache.json"))
    if hasattr(H, "EARN_DIR"): monkeypatch.setattr(H, "EARN_DIR", str(tmp_path / "clearance"))
    monkeypatch.setattr(H.IC, "has_borrow", lambda *a, **k: True); monkeypatch.setattr(H.IC, "borrow_pa", lambda *a, **k: 0.03)
    monkeypatch.setattr(H, "live_audusd", lambda fb: 0.70); monkeypatch.setattr(H, "hi_events", lambda *a, **k: [])


def _bar(start, o, h, l, c): return dict(start=start, o=o, h=h, l=l, c=c)
def _ny(y, m, d, hh, mm): return dt.datetime(y, m, d, hh, mm, tzinfo=NY).astimezone(UTC)
SPEC = dict(ticker="XYZ", direction="short", trigger=50.0, max_fill=49.9, stop=52.0, t1=49.0, t2=47.8, shares=800, atr=1.0, sector="TECH")
def _events():
    return [json.loads(x) for x in open(H.EVENTS)] if os.path.exists(H.EVENTS) else []
def _sessions_between(a, b):
    """NY sessions strictly after date a up to and including date b."""
    n, d = 0, a
    while d < b:
        d += dt.timedelta(days=1); n += H.ny_session(d)
    return n


# ============================================================ C1: per-trade time exit = fill + HOLD_SESSIONS NY sessions
def case_C1_constants_locked():
    assert H.HOLD_SESSIONS == 3 and H.MAX_EXT_DAYS == 10
    for d in ("2026-11-26", "2026-12-25", "2027-01-01", "2027-03-26", "2027-07-05", "2027-11-25", "2027-12-24"):
        assert d in H.NYSE_HOLIDAYS, d                       # Thanksgiving, Xmas, NY, Good Fri, Jul 4 obs., 2027 Xmas obs.
    assert "2026-11-27" in H.NYSE_EARLY and "2026-12-24" in H.NYSE_EARLY


def case_C1_engine_never_stamps_the_legacy_hardcoded_exit_on_new_trades():
    src = inspect.getsource(H.upsert_trade)
    assert "time_exit_utc=TIME_EXIT_UTC" not in src.replace(" ", "")   # the C1 bug: one hard-coded exit for every trade
    for now in (_ny(2026, 10, 30, 10, 0), _ny(2027, 3, 2, 11, 0), _ny(2028, 1, 5, 14, 0)):
        t = H.upsert_trade(H.new_ledger("r"), dict(SPEC), now)
        assert dt.datetime.fromisoformat(t["time_exit_utc"]) > now


def case_C1_session_exit_sweep_2026_2028():
    """Every 15m RTH fill-bar end on every NY session 26 Oct 2026 -> 2028: exit is a NY session close (15:55, 12:55 early),
    strictly after the fill, exactly HOLD_SESSIONS sessions after the fill session; DST handled by zoneinfo."""
    d = dt.date(2026, 10, 26); n = 0
    while d <= dt.date(2028, 12, 20):
        if H.ny_session(d):
            for hh, mm in ((9, 45), (12, 0), (15, 45), (16, 0)):
                f = dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=NY).astimezone(UTC)
                te = H.session_exit(f); ny = te.astimezone(NY)
                assert te > f and H.ny_session(ny.date()), (f, te)
                want = (12, 55) if ny.date().isoformat() in H.NYSE_EARLY else (15, 55)
                assert (ny.hour, ny.minute) == want, (f, te)
                assert _sessions_between(d, ny.date()) == H.HOLD_SESSIONS, (f, te)
                n += 1
        d += dt.timedelta(days=1)
    assert n > 2000


def case_C1_fill_outside_session_counts_from_next_session():
    for f in (_ny(2026, 10, 31, 12, 0), _ny(2026, 11, 26, 11, 0), _ny(2026, 10, 27, 17, 30)):   # Sat, Thanksgiving, after close
        te = H.session_exit(f).astimezone(NY).date(); fd = f.astimezone(NY).date()
        first = fd + dt.timedelta(days=1)
        while not H.ny_session(first): first += dt.timedelta(days=1)
        assert _sessions_between(first, te) == H.HOLD_SESSIONS


def case_C1_fill_overrides_provisional_exit_and_records_rule():
    now = _ny(2026, 11, 3, 10, 0); t = H.upsert_trade(H.new_ledger("r"), dict(SPEC, time_exit_utc="2026-12-31T20:55:00+00:00"), now)
    assert t["time_exit_utc"] == "2026-12-31T20:55:00+00:00"          # spec exit accepted (no duplicate-kwarg TypeError) ...
    bar = _bar(now + dt.timedelta(minutes=15), 50.1, 50.2, 49.9, 49.95)
    assert H.try_fill(t, [bar], now + dt.timedelta(minutes=31), 0.70) == "filled"
    assert dt.datetime.fromisoformat(t["time_exit_utc"]) == H.session_exit(bar["start"] + dt.timedelta(minutes=15))   # ... but fill re-stamps
    assert "NY sessions" in t["time_exit_rule"]


@parametrize("path", ["new", "relevel"])
def case_C1_past_exit_refused_on_both_arm_paths(path):
    now = _ny(2026, 11, 3, 10, 0); L = H.new_ledger("r")
    if path == "relevel": H.upsert_trade(L, dict(SPEC), now - dt.timedelta(hours=1))
    with raises(H.ArmRefused): H.upsert_trade(L, dict(SPEC, time_exit_utc="2026-10-29T14:00:00+00:00"), now)


def case_C1_exit_before_entry_is_repaired_never_executed():
    e = _ny(2026, 11, 5, 10, 30)
    for d in ("short", "long"):
        t = dict(ticker="XYZ", status="FILLED", direction=d, entry=50.0, entry_utc=e.isoformat(), shares=100, open_qty=100, half_done=False,
                 trail=None, stop=52.0 if d == "short" else 48.0, trigger=50.5 if d == "short" else 49.5, t1=48.0 if d == "short" else 52.0,
                 lowest=50.0, atr=1.0, last_px=50.0, ext_days=0, time_exit_utc="2026-10-29T14:00:00+00:00", exits=[], realised_usd=0.0, fees_usd=0.0)
        assert H.manage(t, [], e + dt.timedelta(hours=1)) == [] and t["status"] == "FILLED"
        assert dt.datetime.fromisoformat(t["time_exit_utc"]) == H.session_exit(e)


# ============================================================ tick() harness (all gates hermetic)
def _tick_env(monkeypatch, now, bars, daily=None, daily_exc=None):
    monkeypatch.setattr(H, "now_utc", lambda: now); monkeypatch.setattr(H, "us_rth", lambda n: True)
    monkeypatch.setattr(H, "bars_15m", lambda s: bars.get(s, []))
    calls = {"daily": 0}
    def _daily(s):
        calls["daily"] += 1
        if daily_exc: raise daily_exc
        return daily if daily is not None else []
    monkeypatch.setattr(H, "bars_daily", _daily)
    return calls
def _ok_room(monkeypatch):
    """Make the room gate pass regardless of which room implementation the engine uses (H2 may swap it)."""
    monkeypatch.setattr(H, "room_check", lambda t, daily, entry=None: dict(ok=True, room_R=2.0, level=None, cost_R=0.05, why=None))
    if hasattr(H, "HR"): monkeypatch.setattr(H.HR, "room", lambda t, *a, **k: (t.update(room_R=2.0) or None), raising=False)
def _save(L): json.dump(L, open(H.LEDGER, "w"), default=str)


def case_C1_tick_fill_after_29oct_is_held_not_time_exited(monkeypatch):
    _ok_room(monkeypatch)
    arm = _ny(2026, 11, 5, 9, 0); L = H.new_ledger("r"); H.upsert_trade(L, dict(SPEC), arm); _save(L)
    now = _ny(2026, 11, 5, 10, 16)
    _tick_env(monkeypatch, now, {"XYZ": [_bar(_ny(2026, 11, 5, 10, 0), 50.1, 50.2, 49.9, 49.95)]})
    H.tick()
    t = json.load(open(H.LEDGER))["trades"][0]
    assert t["status"] == "FILLED" and t["ext_days"] == 0
    assert dt.datetime.fromisoformat(t["time_exit_utc"]).astimezone(NY).date() == dt.date(2026, 11, 10)   # Thu fill -> Tue close
    later = _ny(2026, 11, 6, 11, 1)                         # next day, quiet bar: still held
    _tick_env(monkeypatch, later, {"XYZ": [_bar(_ny(2026, 11, 6, 10, 45), 49.9, 49.95, 49.8, 49.85)]})
    H.tick(); t = json.load(open(H.LEDGER))["trades"][0]
    assert t["status"] == "FILLED" and t["exits"] == [] and t["ext_days"] == 0


# ============================================================ H9: stops/T1 never depend on daily bars
def _filled(**kw):
    e = kw.pop("entry_utc", _ny(2026, 11, 2, 10, 0))
    t = dict(id=1, ticker="XYZ", status="FILLED", direction="short", trigger=50.5, max_fill=49.9, entry=50.0, entry_utc=e.isoformat(), shares=100,
             open_qty=100, half_done=False, trail=None, stop=52.0, t1=48.0, t2=46.0, lowest=49.0, atr=1.0, last_px=49.0, ext_days=0,
             time_exit_utc=H.session_exit(e).isoformat(), time_exit_rule="fill + 3 NY sessions (audit C1)", exits=[], realised_usd=0.0,
             fees_usd=1.0, borrow_usd=0.0, armed_utc=(e - dt.timedelta(hours=1)).isoformat(), risk_cap_aud=2500.0, sector="TECH",
             _last_bar=(e - dt.timedelta(minutes=15)).isoformat())
    t.update(kw); return t


def case_H9_stop_runs_when_daily_download_fails_after_time_exit(monkeypatch):
    t = _filled(); te = dt.datetime.fromisoformat(t["time_exit_utc"]); now = te + dt.timedelta(minutes=40)
    L = H.new_ledger("r"); L["trades"].append(t); _save(L)
    bar = _bar(te + dt.timedelta(minutes=5), 51.0, 52.5, 50.9, 52.2)     # trades through the 52.0 stop
    calls = _tick_env(monkeypatch, now, {"XYZ": [bar]}, daily_exc=RuntimeError("yfinance down"))
    H.tick(); t2 = json.load(open(H.LEDGER))["trades"][0]
    assert t2["status"] == "STOPPED" and t2["open_qty"] == 0 and t2["exits"][-1]["why"] == "stop"
    assert calls["daily"] == 0                                             # stop needs no daily data at all


def case_H9_daily_not_fetched_before_time_exit_and_failure_never_crashes(monkeypatch):
    t = _filled(); L = H.new_ledger("r"); L["trades"].append(t); _save(L)
    now = dt.datetime.fromisoformat(t["entry_utc"]) + dt.timedelta(hours=2)
    calls = _tick_env(monkeypatch, now, {"XYZ": [_bar(now - dt.timedelta(minutes=30), 49.5, 49.6, 49.4, 49.5)]}, daily_exc=RuntimeError("x"))
    H.tick(); assert calls["daily"] == 0 and json.load(open(H.LEDGER))["trades"][0]["status"] == "FILLED"
    # after the exit, in profit, daily fails -> no extension, flat at the time exit, logged (fail closed = flat)
    # Bar close must be >=1R profit so Brett 2026-10-28 20:59 overnight-carry rule does not eod_flat before the
    # time-exit path (H9 under test). R = stop-entry = 2.0; short 1R mark = entry-2 = 48.0.
    te = dt.datetime.fromisoformat(t["time_exit_utc"]); now = te + dt.timedelta(minutes=20)
    calls = _tick_env(monkeypatch, now, {"XYZ": [_bar(te - dt.timedelta(minutes=10), 48.2, 48.4, 47.8, 48.0)]}, daily_exc=RuntimeError("x"))
    H.tick(); t2 = json.load(open(H.LEDGER))["trades"][0]
    assert calls["daily"] == 1 and t2["status"] == "CLOSED" and t2["ext_days"] == 0
    assert "daily_data_error" in [e["kind"] for e in _events()]


def case_H9_losing_trade_at_exit_never_fetches_daily():
    t = _filled(last_px=50.8, lowest=49.9); te = dt.datetime.fromisoformat(t["time_exit_utc"])
    def boom(): raise AssertionError("daily fetched for a losing trade")
    ev = H.manage(t, [], te + dt.timedelta(minutes=1), None, daily_fn=boom)
    assert ev == ["time_exit"] and t["status"] == "CLOSED"


# ============================================================ H3: a FILLED trade always passed every gate
def _three_open(L, now):
    for i, tk in enumerate(("AAA", "BBB", "CCC")):
        L["trades"].append(_filled(id=10 + i, ticker=tk, sector="S%d" % i, direction="short",
                                   entry_utc=now - dt.timedelta(hours=3), _last_bar=(now - dt.timedelta(minutes=30)).isoformat()))


@parametrize("where", ["bars_daily", "room_check", "slot_gate", "causal"])
def case_H3_any_gate_exception_keeps_trade_armed_and_unchanged(monkeypatch, where):
    now = _ny(2026, 11, 4, 11, 0); L = H.new_ledger("r")
    t = H.upsert_trade(L, dict(SPEC), now - dt.timedelta(hours=1)); before = copy.deepcopy(t); _save(L)
    _tick_env(monkeypatch, now, {"XYZ": [_bar(now - dt.timedelta(minutes=30), 50.1, 50.2, 49.9, 49.95)]},
              daily_exc=RuntimeError("daily down") if where == "bars_daily" else None)
    def boom(*a, **k): raise RuntimeError(where + " exploded")
    if where == "room_check": monkeypatch.setattr(H, "room_check", boom)
    else: _ok_room(monkeypatch) if where != "bars_daily" else None
    if where == "slot_gate": monkeypatch.setattr(H, "slot_gate", boom)
    if where == "causal":
        real, n = H.causal_guard.check, {"c": 0}
        def once(*a, **k):
            n["c"] += 1
            if n["c"] == 1: raise RuntimeError("causal exploded")
            return real(*a, **k)
        monkeypatch.setattr(H.causal_guard, "check", once)
    H.tick(); d = json.load(open(H.LEDGER))["trades"][0]
    assert d["status"] == "ARMED" and d["entry"] is None and d["entry_utc"] is None and d["open_qty"] == 0
    assert d["fees_usd"] == before["fees_usd"] and d["shares"] == before["shares"]
    assert "gate_error_kept_armed" in [e["kind"] for e in _events()]


def case_H3_max_open_never_exceeded_even_with_gate_errors(monkeypatch):
    now = _ny(2026, 11, 4, 11, 0); L = H.new_ledger("r"); _three_open(L, now)
    for i, tk in enumerate(("DDD", "EEE")):
        H.upsert_trade(L, dict(SPEC, ticker=tk, sector="N%d" % i, trigger=20.0, max_fill=19.9, stop=21.0, t1=19.5, t2=18.9), now - dt.timedelta(hours=1))
    _save(L); _ok_room(monkeypatch)
    bars = {tk: [_bar(now - dt.timedelta(minutes=30), 20.0, 20.05, 19.93, 19.95)] for tk in ("DDD", "EEE")}
    bars.update({tk: [_bar(now - dt.timedelta(minutes=15), 49.0, 49.1, 48.9, 49.0)] for tk in ("AAA", "BBB", "CCC")})
    _tick_env(monkeypatch, now, bars)
    H.tick(); L2 = json.load(open(H.LEDGER))
    assert sum(x["status"] == "FILLED" for x in L2["trades"]) <= H.MAX_OPEN == 4   # cap5k 2026-10-29 20:17: US main hunt 4 slots / A$5k
    assert all(x["status"] in ("SHADOW", "ARMED") for x in L2["trades"] if x["ticker"] in ("DDD", "EEE"))


def case_H3_tick_source_restores_snapshot():
    src = inspect.getsource(H.tick)
    assert "deepcopy" in src and "gate_error_kept_armed" in src


# ============================================================ H5: A$2.5k cap at the worst permitted fill, no hidden tolerance
def _HC(monkeypatch):
    monkeypatch.setattr(sys, "argv", [sys.argv[0]]); import hunt_clearance as HC; return HC


def case_H5_tolerance_is_exactly_one(monkeypatch):
    HC = _HC(monkeypatch); assert HC.RISK_TOL == 1.00 and HC.RISK_AUD == 2500.0


def case_H5_clearance_risk_sweep_never_above_cap_at_max_fill(monkeypatch):
    HC = _HC(monkeypatch); rnd = random.Random(20261027)
    for i in range(400):
        lg = rnd.random() < 0.3; trig = rnd.uniform(5, 400); R = trig * rnd.uniform(0.01, 0.08); slip = trig * rnd.uniform(0, 0.01)
        stop = trig - R if lg else trig + R; mf = trig + slip if lg else trig - slip
        fx = rnd.uniform(0.6, 0.75); sh = max(1, int(rnd.uniform(0.5, 2.0) * 2500 * fx / R))
        s = dict(ticker="T%d" % i, direction="long" if lg else "short", trigger=trig, max_fill=mf, stop=stop, shares=sh)
        r = HC.risk_review([s], {s["ticker"]: dict(sector_key="SEC%d" % i, _closes=[])}, fx)[0][s["ticker"]]
        worst = abs(stop - mf) * s["shares"] / fx
        if r["verdict"] == "PASS": assert worst <= 2500.5 + 1e-6, (s, r)
        assert r["risk_aud"] <= 2501 or r["verdict"] == "FAIL"


def case_H5_engine_fill_risk_sweep_never_above_cap(monkeypatch):
    rnd = random.Random(7)
    for i in range(300):
        lg = rnd.random() < 0.3; trig = rnd.uniform(5, 300); R = trig * rnd.uniform(0.01, 0.06)
        stop = trig - R if lg else trig + R; mf = trig * (1.006 if lg else 0.994)
        fx = rnd.uniform(0.6, 0.75); sh = max(1, int(rnd.uniform(0.8, 1.6) * 2500 * fx / R))
        now = _ny(2026, 11, 4, 10, 0)
        t = dict(ticker="Z", direction="long" if lg else "short", status="ARMED", armed_utc=(now - dt.timedelta(hours=1)).isoformat(),
                 trigger=trig, max_fill=mf, stop=stop, t1=trig, t2=trig, shares=sh, atr=1.0, risk_cap_aud=2500.0, fees_usd=0.0, exits=[])
        c = trig + (mf - trig) * rnd.uniform(0.05, 0.95)
        k = H.try_fill(t, [_bar(now - dt.timedelta(minutes=30), c, c, c, c)], now, fx)
        if k == "filled": assert abs(t["entry"] - t["stop"]) * t["shares"] / fx <= 2500 + 0.00005 * t["shares"] / fx + 1e-6, t   # only 4dp entry rounding may add cents (VERIFY.md H5)


# ============================================================ H6: no position (or extension) with earnings inside the longest possible hold
def case_H6_horizon_is_exit_plus_max_ext_sessions_sweep():
    d = dt.date(2026, 10, 26)
    while d <= dt.date(2028, 11, 30):
        if H.ny_session(d):
            te = H.ny_close_utc(d); hz = H.earnings_horizon(te).astimezone(NY).date()
            assert _sessions_between(d, hz) == H.MAX_EXT_DAYS and H.ny_session(hz), d
        d += dt.timedelta(days=3)


def _armed_ledger(now, **kw):
    L = H.new_ledger("r"); t = H.upsert_trade(L, dict(SPEC), now - dt.timedelta(hours=1)); t.update(kw); _save(L); return L


@parametrize("src", ["trade", "clearance_file", "date_only"])
def case_H6_fill_with_earnings_inside_hold_is_shadow_never_a_position(monkeypatch, tmp_path, src):
    now = _ny(2026, 11, 4, 11, 0); _ok_room(monkeypatch)
    earn = "2026-11-17T21:05:00+00:00"                       # 3 sessions + 6 more: beyond the 3-session exit, inside +10 extension
    if src == "trade": _armed_ledger(now, next_earnings_utc=earn)
    elif src == "date_only": _armed_ledger(now, next_earnings_utc="2026-11-17")
    else:
        p = tmp_path / "clearance" / "20261103"; p.mkdir(parents=True)
        (p / "XYZ.json").write_text(json.dumps(dict(calendar=dict(next_earnings_utc=earn)))); _armed_ledger(now)
    _tick_env(monkeypatch, now, {"XYZ": [_bar(now - dt.timedelta(minutes=30), 50.1, 50.2, 49.9, 49.95)]})
    H.tick(); d = json.load(open(H.LEDGER))["trades"][0]
    assert d["status"] == "SHADOW" and "earnings" in d["shadow_reason"] and d["entry"] is None and d["open_qty"] == 0


def case_H6_fill_allowed_when_earnings_after_horizon(monkeypatch):
    now = _ny(2026, 11, 4, 11, 0); _ok_room(monkeypatch); _armed_ledger(now, next_earnings_utc="2027-01-20T21:05:00+00:00")
    _tick_env(monkeypatch, now, {"XYZ": [_bar(now - dt.timedelta(minutes=30), 50.1, 50.2, 49.9, 49.95)]})
    H.tick(); assert json.load(open(H.LEDGER))["trades"][0]["status"] == "FILLED"


@parametrize("direction", ["short", "long"])
def case_H6_no_extension_into_earnings_for_fill_stamped_trades(monkeypatch, direction):
    monkeypatch.setattr(H, "extension_ok", lambda d, long=False: True)
    lg = direction == "long"
    t = _filled(direction=direction, stop=48.0 if lg else 52.0, trigger=49.5 if lg else 50.5, t1=60.0 if lg else 40.0,
                last_px=51.0 if lg else 49.0, lowest=51.0 if lg else 49.0)
    te = dt.datetime.fromisoformat(t["time_exit_utc"])
    t["next_earnings_utc"] = (te + dt.timedelta(days=4)).isoformat()
    assert H.manage(t, [], te + dt.timedelta(minutes=1), None, daily_fn=lambda: [1]) == ["time_exit"] and t["status"] == "CLOSED"
    t = _filled(direction=direction, stop=48.0 if lg else 52.0, trigger=49.5 if lg else 50.5, t1=60.0 if lg else 40.0,
                last_px=51.0 if lg else 49.0, lowest=51.0 if lg else 49.0)
    assert H.manage(t, [], te + dt.timedelta(minutes=1), None, daily_fn=lambda: [1]) == ["extended"]   # no earnings -> extension still works


def case_H6_clearance_us_window_covers_ten_session_extension(monkeypatch):
    HC = _HC(monkeypatch); now = dt.datetime(2026, 11, 2, 14, 0, tzinfo=UTC)
    s = dict(ticker="XYZ", direction="short", trigger=50.0, stop=52.0, time_exit_utc="2026-11-05T21:55:00+00:00")
    te = dt.datetime.fromisoformat(s["time_exit_utc"]); hz = H.earnings_horizon(te)
    b = dict(asset_class="equity", sweep=dict(rsi="50", roc5="0", atr_pct="3", venue="ibkr"), technical={}, calendar=dict(next_earnings_utc=hz.isoformat()))
    assert HC.behavior_review(s, b, now)["verdict"] == "FAIL"     # clearance horizon >= engine's own horizon


# ============================================================ H7: apply-plan never crashes on BENCH/SHADOW; ledger before events
def _plan(tmp_path, **P):
    p = tmp_path / "plan.json"; json.dump(P, open(p, "w")); return str(p)
def _ledger_with(now, *rows):
    L = H.new_ledger("r"); L["trades"] = []
    for i, (tk, st) in enumerate(rows, 1):
        t = H.upsert_trade(L, dict(SPEC, ticker=tk), now - dt.timedelta(hours=2)); t["status"] = st
        if st == "FILLED": t.update(entry=49.95, entry_utc=(now - dt.timedelta(hours=1)).isoformat(), open_qty=t["shares"])
        if st == "SHADOW": t.update(shadow_reason="slot full")
    _save(L); return L


def case_H7_bench_row_relevels_to_armed_in_place(monkeypatch, tmp_path):
    now = _ny(2026, 11, 4, 9, 0); monkeypatch.setattr(H, "bars_15m", lambda tk: [])
    _ledger_with(now, ("AAA", "BENCH"))
    L = H.apply_plan(_plan(tmp_path, set=[dict(SPEC, ticker="AAA", trigger=60.0, max_fill=59.9, stop=62.0)]), now)
    rows = [t for t in L["trades"] if t["ticker"] == "AAA"]
    assert len(rows) == 1 and rows[0]["status"] == "ARMED" and rows[0]["relevel_from"] == "BENCH"
    assert rows[0]["trigger"] == 60.0 and rows[0]["level_history"][-1]["trigger"] == 50.0
    assert dt.datetime.fromisoformat(rows[0]["armed_utc"]) == now


def case_H7_shadow_row_is_kept_and_a_fresh_armed_row_is_added(monkeypatch, tmp_path):
    now = _ny(2026, 11, 4, 9, 0); monkeypatch.setattr(H, "bars_15m", lambda tk: [])
    _ledger_with(now, ("SSS", "SHADOW"))
    L = H.apply_plan(_plan(tmp_path, set=[dict(SPEC, ticker="SSS")]), now)
    st = sorted(t["status"] for t in L["trades"] if t["ticker"] == "SSS")
    assert st == ["ARMED", "SHADOW"]
    assert [t for t in L["trades"] if t["status"] == "SHADOW"][0]["shadow_reason"] == "slot full"
    L = H.apply_plan(_plan(tmp_path, set=[dict(SPEC, ticker="SSS", trigger=51.0)]), now)   # re-stage again: re-levels, no 3rd row
    assert sorted(t["status"] for t in L["trades"] if t["ticker"] == "SSS") == ["ARMED", "SHADOW"]


def case_H7_one_bad_row_never_aborts_the_plan_and_filled_is_untouched(monkeypatch, tmp_path):
    now = _ny(2026, 11, 4, 9, 0); monkeypatch.setattr(H, "bars_15m", lambda tk: [])
    _ledger_with(now, ("FFF", "FILLED"), ("CCC", "ARMED"))
    before = copy.deepcopy([t for t in json.load(open(H.LEDGER))["trades"] if t["ticker"] == "FFF"][0])
    L = H.apply_plan(_plan(tmp_path, cut=[dict(ticker="NOPE", reason="x"), dict(ticker="CCC", reason="y")],
                           set=[dict(SPEC, ticker="FFF", shares=1), dict(SPEC, ticker="NEW")]), now)
    by = {t["ticker"]: t for t in L["trades"]}
    assert by["CCC"]["status"] == "CUT" and by["NEW"]["status"] == "ARMED"
    assert {k: by["FFF"][k] for k in before} == before                     # FILLED never re-levelled
    on_disk = {t["ticker"]: t["status"] for t in json.load(open(H.LEDGER))["trades"]}
    assert on_disk["CCC"] == "CUT" and on_disk["NEW"] == "ARMED"
    kinds = [(e.get("kind") or e.get("event") or e.get("type"), e.get("ticker")) for e in _events()]
    assert ("cut_refused", "NOPE") in kinds and ("arm_refused", "FFF") in kinds and ("armed", "NEW") in kinds


def case_H7_ledger_is_written_before_any_event(monkeypatch, tmp_path):
    now = _ny(2026, 11, 4, 9, 0); monkeypatch.setattr(H, "bars_15m", lambda tk: [])
    _ledger_with(now, ("BBB", "BENCH"))
    seen = []
    real = H.event
    def spy(kind, **kw):
        L = json.load(open(H.LEDGER)); seen.append((kind, {t["ticker"]: t["status"] for t in L["trades"]}))
        return real(kind, **kw)
    monkeypatch.setattr(H, "event", spy)
    H.apply_plan(_plan(tmp_path, set=[dict(SPEC, ticker="BBB"), dict(SPEC, ticker="NEW2")]), now)
    assert seen and all(s.get("BBB") == "ARMED" and s.get("NEW2") == "ARMED" for _, s in seen)


# ============================================================ H8: max 3 open is PER BOOK (calendar + ASX benches)
import cal_paper_bench as CB
_T0 = dt.datetime(2026, 10, 27, 0, 0, tzinfo=UTC)
def _cb_base(tmp_path, monkeypatch, syms, hunt_filled=3, own=()):
    base = tmp_path / "desk"; (base / "state" / "calendar" / "inputs").mkdir(parents=True)
    for b in ("hunt48_short", "hunt48_short_claude", "hunt48_short_gpt"):
        (base / "state" / b).mkdir(); json.dump({"trades": [{"status": "FILLED"}] * hunt_filled}, open(base / "state" / b / "ledger.json", "w"))
    open(base / "state" / "calendar" / "inputs" / "ranked_oanda.csv", "w").write("sym,yahoo\n" + "".join(f"{s},{s}=X\n" for s in syms))
    led = base / "state" / "calendar" / "paper_bench_ledger.json"; monkeypatch.setattr(CB, "LEDGER", str(led))
    if own: json.dump(dict(book="CALENDAR_PAPER_BENCH", paper_only=True, trades=list(own)), open(led, "w"))
    cal = {"staged": [dict(sym=s, venue="oanda", top3=True, direction="short", trigger=100, max_fill=99.85, stop=101.5, t1=99.3, t2=98.4,
                           shares=10, notional_usd=998.5, est_cost_usd=1, risk_aud=1250) for s in syms]}
    return str(base), cal
def _cb_bar(h, c): return dict(t=_T0 + dt.timedelta(hours=h), o=100.2, h=100.3, l=c, c=c)


def case_H8_book_open_counts_only_this_books_filled_and_partial():
    assert CB.book_open([{"status": s} for s in ("FILLED", "PARTIAL", "ARMED", "SHADOW", "CLOSED", "MISSED")]) == 2
    assert CB.book_open([]) == 0


def case_H8_other_books_full_never_block_this_bench(monkeypatch, tmp_path):
    base, cal = _cb_base(tmp_path, monkeypatch, ["EUR_USD"], hunt_filled=3)      # 9 FILLED across the 3 hunt48 books
    CB.run(now=_T0 + dt.timedelta(hours=3), fetch=lambda y, s: [], cal=cal, base=base)
    st = CB.run(now=_T0 + dt.timedelta(hours=6), fetch=lambda y, s: [_cb_bar(4, 99.9)], cal=cal, base=base)
    assert [t["status"] for t in st["trades"]] == ["FILLED"]


def case_H8_this_book_never_exceeds_three_open(monkeypatch, tmp_path):
    syms = ["EUR_USD", "GBP_USD", "AUD_USD", "USD_JPY", "USD_CAD"]
    base, cal = _cb_base(tmp_path, monkeypatch, syms, hunt_filled=0)
    CB.run(now=_T0 + dt.timedelta(hours=3), fetch=lambda y, s: [], cal=cal, base=base)
    st = CB.run(now=_T0 + dt.timedelta(hours=6), fetch=lambda y, s: [_cb_bar(4, 99.9)], cal=cal, base=base)
    c = [t["status"] for t in st["trades"]]
    assert c.count("FILLED") == 3 and c.count("SHADOW") == 2
    st = CB.run(now=_T0 + dt.timedelta(hours=7), fetch=lambda y, s: [_cb_bar(4, 99.9)], cal=cal, base=base)   # re-run: still 3
    assert [t["status"] for t in st["trades"]].count("FILLED") == 3


def case_H8_asx_bench_uses_the_per_book_definition():
    import asx_paper_bench as AX
    src = inspect.getsource(AX.run)
    assert "CB.book_open(L[\"trades\"])" in src and "hunt_filled" not in src
    assert "hunt_filled" not in inspect.getsource(CB.run)



# ============================================================ H4: US Day P&L = change since the first status of the WITA day
def _tick_at(monkeypatch, now, px):
    _tick_env(monkeypatch, now, {"XYZ": [_bar(now - dt.timedelta(minutes=20), px, px, px, px)]}); H.tick()
    return json.load(open(H.STATUS)), json.load(open(H.LEDGER))


def case_H4_day_base_persists_across_ticks_and_resets_at_wita_midnight(monkeypatch):
    L = H.new_ledger("r"); L["trades"].append(_filled()); _save(L)
    s1, L1 = _tick_at(monkeypatch, dt.datetime(2026, 11, 3, 15, 0, tzinfo=UTC), 49.5)     # 23:00 WITA Tue
    assert L1["day_base"]["wita_date"] == "2026-11-03" and abs(s1["pnl_today_aud"]) < 0.5
    s2, L2 = _tick_at(monkeypatch, dt.datetime(2026, 11, 3, 15, 45, tzinfo=UTC), 49.0)    # same WITA day, +US$50 move
    assert L2["day_base"] == L1["day_base"]                                                # baseline persisted in the ledger
    assert s2["pnl_today_aud"] == approx(s2["pnl_since_entry_aud"] - L1["day_base"]["pnl_aud"], abs=0.02)
    assert 60 < s2["pnl_today_aud"] < 80                                                    # ~US$50 / 0.70
    s3, L3 = _tick_at(monkeypatch, dt.datetime(2026, 11, 3, 16, 5, tzinfo=UTC), 49.0)     # 00:05 WITA Wed -> new day
    assert L3["day_base"]["wita_date"] == "2026-11-04" and abs(s3["pnl_today_aud"]) < 0.5
    assert s3["pnl_since_entry_aud"] == approx(s2["pnl_since_entry_aud"], abs=0.5)   # lifetime figure kept separately
    assert "WITA day" in s3["pnl_today_basis"]


def case_H4_lifetime_number_never_reported_as_day_pnl():
    now = dt.datetime(2026, 11, 10, 3, 0, tzinfo=UTC); L = H.new_ledger("r")
    t = _filled(entry_utc=now - dt.timedelta(days=12)); t.update(status="CLOSED", open_qty=0, realised_usd=900.0); L["trades"].append(t)
    st = H.write_status(L, 0.70, now)
    assert st["pnl_today_aud"] == 0 and st["pnl_since_entry_aud"] > 1000
    import warroom_markets as WM
    assert WM.us_book("us_main", "x", {k: v for k, v in st.items() if k != "pnl_today_basis"}, L, now)["pnl_today_aud"] is None


# ============================================================ H14: US freshness = price time while NY open; data errors never green
@parametrize("age_min,errs,is_open,want", [
    (10, 0, True, "green"), (50, 0, True, "amber"), (180, 0, True, "red"), (10, 2, True, "amber"),
    (300, 0, False, "green"), (300, 1, False, "amber")])
def case_H14_us_freshness_table(age_min, errs, is_open, want):
    import warroom_markets as WM
    now = dt.datetime(2026, 11, 3, 16, 0, tzinfo=UTC)
    b = dict(price_asof=(now - dt.timedelta(minutes=age_min)).isoformat(), updated=(now - dt.timedelta(minutes=1)).isoformat(), data_error_count=errs)
    f = WM.us_fresh(b, {"open": is_open}, now)
    assert f["level"] == want and f["basis"] == ("price" if is_open else "status")


def case_H14_outage_while_open_shows_errors_and_stale_price(monkeypatch):
    import warroom_markets as WM
    L = H.new_ledger("r"); L["trades"].append(_filled(last_px_utc=dt.datetime(2026, 11, 3, 14, 30, tzinfo=UTC).isoformat())); _save(L)
    now = dt.datetime(2026, 11, 3, 18, 0, tzinfo=UTC)
    monkeypatch.setattr(H, "now_utc", lambda: now); monkeypatch.setattr(H, "us_rth", lambda n: True)
    def boom(s): raise RuntimeError("yahoo 429")
    monkeypatch.setattr(H, "bars_15m", boom); H.tick(); st = json.load(open(H.STATUS))
    assert st["data_error_count"] == 1 and st["price_asof_utc"].startswith("2026-11-03T14:30")
    f = WM.us_fresh(WM.us_book("us_main", "x", st, None, now), {"open": True}, now)
    assert f["level"] == "red" and f["data_errors"] == 1
    monkeypatch.setattr(H, "bars_15m", lambda s: [])                 # empty (not an exception): error clears, price stays stale
    H.tick(); st = json.load(open(H.STATUS))
    assert st["data_error_count"] == 0 and WM.us_fresh(WM.us_book("us_main", "x", st, None, now), {"open": True}, now)["level"] == "red"



# ============================================================ H1: paper-bench stops never fill better than the stop; gaps fill at the open
def _cbt(direction, qty=1000, stop=None, t1=None, fill=100.0):
    lg = direction == "long"
    return dict(direction=direction, status="FILLED", armed_utc=_T0.isoformat(), fill=fill, fill_utc=_T0.isoformat(),
                stop=stop or (95.0 if lg else 105.0), stop_live=stop or (95.0 if lg else 105.0), qty_open=qty, shares=qty, realised_usd=0.0,
                t1=t1 or (105.0 if lg else 95.0), t2=110.0 if lg else 90.0, q2usd=1.0, est_cost_usd=0.0, last_bar_utc=_T0.isoformat())


@parametrize("direction", ["long", "short"])
def case_H1_stop_fill_sweep_never_better_than_stop_and_gaps_fill_at_open(direction):
    rnd = random.Random(11); lg = direction == "long"; n = 0
    for _ in range(500):
        t = _cbt(direction); st = t["stop"]
        o = st + (rnd.uniform(-8, 3) if lg else rnd.uniform(-3, 8))
        lo, hi = (min(o, st) - rnd.uniform(0, 2), max(o, st) + rnd.uniform(0, 1)) if lg else (min(o, st) - rnd.uniform(0, 1), max(o, st) + rnd.uniform(0, 2))
        CB.step(t, [dict(t=_T0 + dt.timedelta(hours=1), o=o, h=hi, l=lo, c=(lo + hi) / 2)], _T0 + dt.timedelta(hours=2), 3)
        assert t["status"] == "CLOSED" and t["exit"] == "STOP"; n += 1
        xp = t["exit_px"]; gapped = (o < st) if lg else (o > st)
        want = (min(o, st) * (1 - CB.STOP_SLIP)) if lg else (max(o, st) * (1 + CB.STOP_SLIP))
        assert xp == approx(want, abs=1e-6)
        assert (xp <= st * (1 - CB.STOP_SLIP) + 1e-9) if lg else (xp >= st * (1 + CB.STOP_SLIP) - 1e-9)    # never better than the stop
        assert t["realised_usd"] == approx((1 if lg else -1) * (xp - 100.0) * 1000, abs=1e-3)
        if gapped: assert xp == approx(o * (1 - CB.STOP_SLIP) if lg else o * (1 + CB.STOP_SLIP), abs=1e-6)
    assert n == 500 and CB.STOP_SLIP == 0.0005


@parametrize("qty", [1, 2, 3, 7, 100, 101, 999])
def case_H1_t1_half_is_whole_units_and_conserves_quantity(qty):
    t = _cbt("long", qty=qty, t1=101.0)
    CB.step(t, [dict(t=_T0 + dt.timedelta(hours=1), o=100.0, h=101.5, l=99.8, c=101.0)], _T0 + dt.timedelta(hours=2), 3)
    banked = qty // 2
    assert isinstance(t["qty_open"], int) and t["qty_open"] == qty - banked and t["t1_done"] and t["stop_live"] == 100.0
    assert t["realised_usd"] == approx(1.0 * banked)


def case_H1_every_exchange_bench_uses_the_shared_step():
    import asx_paper_bench as AX
    for mod in ("asia_hunt48", "asx_hunt48"):
        if os.path.exists(os.path.join(ROOT, mod + ".py")):
            src = open(os.path.join(ROOT, mod + ".py")).read()
            assert "CB.step(" in src and '"STOP"' not in src, mod      # stops only via the shared (H1-fixed) step()
    assert "CB.step(" in inspect.getsource(AX.run)



# ============================================================ H11: borrow evidence fails closed (no cache / >24h old / name absent)
def _borrow_cache(tmp_path, monkeypatch, age_h, names=None, venue="us", updated=None):
    u = updated if updated is not None else (dt.datetime.now(UTC) - dt.timedelta(hours=age_h)).isoformat(timespec="seconds")
    C = {"updated_utc": u, venue: dict(n=1, names=names if names is not None else {"XYZ": dict(fee_pa=0.004, available=50000, ok=True)})}
    f = tmp_path / "ibkr_borrow.json"; f.write_text(json.dumps(C)); monkeypatch.setattr(IC, "CACHE", str(f))


@parametrize("age_h,shares,want", [(1, 800, True), (23.9, 50000, True), (24.1, 800, False), (72, 800, False), (1, 50001, False)])
def case_H11_has_borrow_needs_fresh_file_and_enough_shares(monkeypatch, tmp_path, age_h, shares, want):
    _borrow_cache(tmp_path, monkeypatch, age_h); assert IC.BORROW_MAX_AGE_H == 24
    assert _REAL_HAS_BORROW("XYZ", shares) is want and _REAL_HAS_BORROW("XYZ", -shares) is want   # short sizes may be signed


def case_H11_missing_or_unreadable_evidence_is_never_borrow(monkeypatch, tmp_path):
    monkeypatch.setattr(IC, "CACHE", str(tmp_path / "nope.json")); assert _REAL_HAS_BORROW("XYZ", 1) is False          # no cache file
    _borrow_cache(tmp_path, monkeypatch, 1, updated="garbage"); assert _REAL_HAS_BORROW("XYZ", 1) is False             # bad timestamp
    _borrow_cache(tmp_path, monkeypatch, 1); assert _REAL_HAS_BORROW("ABSENT", 1) is False                             # name not in IBKR file
    assert _REAL_HAS_BORROW("XYZ.AX", 1, "asx") is False                                                               # no data for that venue
    _borrow_cache(tmp_path, monkeypatch, 1, names={"XYZ": dict(fee_pa=0.2, available=0, ok=False)}); assert _REAL_HAS_BORROW("XYZ", 1) is False


def case_H11_engine_fill_without_fresh_borrow_is_shadow_never_a_short(monkeypatch, tmp_path):
    monkeypatch.setattr(IC, "has_borrow", _REAL_HAS_BORROW); _borrow_cache(tmp_path, monkeypatch, 30)        # 30h-old file
    now = _ny(2026, 11, 4, 11, 0); _ok_room(monkeypatch); _armed_ledger(now, next_earnings_utc="2027-01-20T21:05:00+00:00")
    _tick_env(monkeypatch, now, {"XYZ": [_bar(now - dt.timedelta(minutes=30), 50.1, 50.2, 49.9, 49.95)]})
    H.tick(); d = json.load(open(H.LEDGER))["trades"][0]
    assert d["status"] == "SHADOW" and d["shadow_reason"] == "no borrow" and d["open_qty"] == 0


# ============================================================ KNOWN GAPS (VERIFY.md WEAK items). xfail (non-strict) so the suite stays
# green today; when the proposed fix lands these XPASS -> remove the xfail marker to turn each into a permanent lock.
@xfail(reason="VERIFY.md C1-WEAK: extension uses WITA weekday + 1/3 calendar days, not the next NY session close", strict=False)
@parametrize("fill_day", [dt.date(2026, 10, 26), dt.date(2026, 10, 27), dt.date(2026, 11, 20)])
def case_GAP_C1_extension_lands_on_the_next_ny_session_close(monkeypatch, fill_day):
    monkeypatch.setattr(H, "extension_ok", lambda d, long=False: True)
    t = _filled(entry_utc=_ny(fill_day.year, fill_day.month, fill_day.day, 10, 0)); te = dt.datetime.fromisoformat(t["time_exit_utc"])
    assert H.manage(t, [], te + dt.timedelta(minutes=1), None, daily_fn=lambda: [1]) == ["extended"]
    nte = dt.datetime.fromisoformat(t["time_exit_utc"]).astimezone(NY); d = te.astimezone(NY).date() + dt.timedelta(days=1)
    while not H.ny_session(d): d += dt.timedelta(days=1)
    assert nte.date() == d and (nte.hour, nte.minute) == ((12, 55) if d.isoformat() in H.NYSE_EARLY else (15, 55)), nte


def case_GAP_H5_oversized_fill_with_zero_affordable_shares_never_becomes_a_position():
    now = _ny(2026, 11, 4, 10, 0)
    t = dict(ticker="Z", direction="short", status="ARMED", armed_utc=(now - dt.timedelta(hours=1)).isoformat(), trigger=5000.0,
             max_fill=4990.0, stop=9000.0, t1=4900.0, t2=4800.0, shares=10, atr=1.0, risk_cap_aud=2500.0, fees_usd=0.0, exits=[])
    k = H.try_fill(t, [_bar(now - dt.timedelta(minutes=30), 4995, 4995, 4995, 4995)], now, 0.70)
    assert k != "filled" or abs(t["entry"] - t["stop"]) * t["shares"] / 0.70 <= 2500 + 1e-6


def case_GAP_H5_missing_fx_never_fills_unchecked():
    now = _ny(2026, 11, 4, 10, 0)
    t = dict(ticker="Z", direction="short", status="ARMED", armed_utc=(now - dt.timedelta(hours=1)).isoformat(), trigger=50.0,
             max_fill=49.9, stop=52.0, t1=49.0, t2=47.8, shares=5000, atr=1.0, risk_cap_aud=2500.0, fees_usd=0.0, exits=[])
    k = H.try_fill(t, [_bar(now - dt.timedelta(minutes=30), 49.95, 49.95, 49.95, 49.95)], now, None)
    assert k != "filled"



def case_GAP_H1_nan_open_never_produces_nan_pnl():
    t = _cbt("long")
    CB.step(t, [dict(t=_T0 + dt.timedelta(hours=1), o=float("nan"), h=96.0, l=94.0, c=95.5)], _T0 + dt.timedelta(hours=2), 3)
    assert t["realised_usd"] == t["realised_usd"]




def case_GAP_H11_future_dated_borrow_cache_is_not_evidence(monkeypatch, tmp_path):
    _borrow_cache(tmp_path, monkeypatch, -240); assert _REAL_HAS_BORROW("XYZ", 1) is False


def case_GAP_H2_nan_daily_rows_never_count_as_history():
    nan = float("nan"); t = dict(SPEC, max_fill=49.9, stop=52.0, t2=45.0, shares=800)
    rc = H.room_check(t, [dict(h=nan, l=nan, c=nan)] * 250); assert rc["ok"] is False


# ============================================================ H12: crypto protective stops ignore the 5-min ticket cooldown (offline CryptoEngine harness)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from test_tfo_trail_rail import CryptoEngine as _CE
except Exception as _e:                      # harness missing -> report, never silently pass
    _CE = None; _CE_ERR = repr(_e)
if _CE is not None:
    class H12CryptoStopLocks(_CE):
        def setUp(self):
            super().setUp(); self.book(); self.tape = "LIVE"; q0 = self.tc.quote
            def q(pair):
                d = q0(pair); d.update(tape=self.tape, trade_age_sec=1.0 if self.tape == "LIVE" else 900.0); return d
            from unittest.mock import patch
            pq = patch.object(self.tc, "quote", q); pq.start(); self.addCleanup(pq.stop)
        def _held(self, days=2, ago_s=60, **kw):
            now = dt.datetime.now(UTC); f = lambda x: x.strftime("%Y-%m-%dT%H:%M:%SZ")
            pos = {"qty": 1000.0, "entry_px": 10.0, "orig_entry": 10.0, "entry_utc": f(now - dt.timedelta(days=days)), "last_ticket_utc": f(now - dt.timedelta(seconds=ago_s)), **kw}
            b = self.tc.new_book(); b["positions"] = {"LINK/USD": pos}; b["mark_px_usd"] = {"LINK/USD": 10.0}; self.w("tfo_ht_crypto", b)
        def test_H12_stop_inside_cooldown_sells_once_and_only_once(self):
            self._held(ago_s=10); self.bid["LINK/USD"] = 9.4; self.rails(); self.rails()
            b = self.r("tfo_ht_crypto"); tk = [t for t in b["head_trader_tickets"] if t["pair"] == "LINK/USD"]
            self.assertNotIn("LINK/USD", b["positions"]); self.assertEqual(len(tk), 1); self.assertAlmostEqual(tk[0]["qty"], 1000.0)
        def test_H12_max_hold_inside_cooldown_is_protective(self):
            self._held(days=31, ago_s=30, rebase=10.2); self.bid["LINK/USD"] = 10.4; self.rails()
            b = self.r("tfo_ht_crypto"); self.assertNotIn("LINK/USD", b["positions"]); self.assertEqual(b["head_trader_tickets"][-1]["exit_rule"], "MAX_HOLD")
        def test_H12_stale_tape_still_blocks_the_stop(self):
            self._held(ago_s=10); self.bid["LINK/USD"] = 9.4; self.tape = "STALE"; self.rails()
            self.assertAlmostEqual(self.r("tfo_ht_crypto")["positions"]["LINK/USD"]["qty"], 1000.0)   # price validity kept
        def test_H12_planned_half_still_waits_inside_cooldown(self):
            self._held(ago_s=60); self.bid["LINK/USD"] = 10.2; self.rails()
            self.assertAlmostEqual(self.r("tfo_ht_crypto")["positions"]["LINK/USD"]["qty"], 1000.0)
            self._held(ago_s=400); self.rails(); self.assertAlmostEqual(self.r("tfo_ht_crypto")["positions"]["LINK/USD"]["qty"], 500.0)
    for _n in [n for n in dir(_CE) if n.startswith("test_")]: setattr(H12CryptoStopLocks, _n, None)   # run only the H12 locks, not the harness's own tests
    del _CE
else:
    class H12CryptoStopLocks(unittest.TestCase):
        def test_H12_harness_available(self): self.fail("tests/test_tfo_trail_rail.CryptoEngine not importable: " + _CE_ERR)


# ============================================================ H15: OANDA max 10 open TFO trades across ALL buckets (no network, temp state)
class H15OandaTotalCapLocks(unittest.TestCase):
    BOOK = ["EUR_GBP", "AUD_NZD", "EUR_AUD", "GBP_AUD", "CAD_CHF", "EUR_CAD", "GBP_CAD", "AUD_CHF", "NZD_CHF", "GBP_CHF"]
    TREND = ["NZD_CAD", "GBP_NZD", "AUD_CAD", "EUR_NZD"]
    CMDTY = ["XAU_USD", "XAG_USD", "BCO_USD", "XPT_USD", "XCU_USD", "NATGAS_USD"]
    def setUp(self):
        import importlib.util
        sys.path.insert(0, os.path.join(ROOT, "research", "behaviour"))
        spec = importlib.util.spec_from_file_location("sleeve_h15_verifier", os.path.join(ROOT, "oanda_sleeve.py"))
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); self.m = m
        self.tmp = tempfile.mkdtemp(prefix="h15v_"); st = os.path.join(self.tmp, "state", "oanda"); os.makedirs(st)
        m.ST = st; m.LOG = os.path.join(st, "trades.jsonl"); m.CFG = os.path.join(st, "nocfg.json")
        def no_network(*a, **k): raise AssertionError("unexpected OANDA API call")      # PAPER/offline: any broker call fails the test
        m.api = no_network; m.behaviour_ok = lambda *a, **k: True
        m.summary = lambda: {"NAV": "50000", "marginUsed": "0", "currency": "AUD"}
        self.open = []; m.open_trades = lambda: list(self.open); m.pricing = lambda insts: {}
        m.ledger_entry_risk = lambda: {str(i): dict(risk_aud=0.0005 * 50000) for i in range(400)}
    def tearDown(self): shutil.rmtree(self.tmp, ignore_errors=True)
    def _t(self, i, ins, design, tag="TFO"):
        return {"id": str(100 + i), "instrument": ins, "currentUnits": "1", "price": "1.0", "clientExtensions": {"tag": tag, "comment": design}}
    def _open(self, b, t, k):
        self.open = ([self._t(i, p, "OXL2") for i, p in enumerate(self.BOOK[:b])] + [self._t(20 + i, p, "OXF1") for i, p in enumerate(self.TREND[:t])]
                     + [self._t(40 + i, p, "OXP1") for i, p in enumerate(self.CMDTY[:k])])
    def _try(self, design, ins=None):
        ins = ins or ("XPD_USD" if design == "OXP1" else "EUR_JPY")
        return self.m.place_entry(dict(design=design, ins=ins, dir=1, risk_pct=0.005, stop_dist=0.01, floor_sp=1e-4, close=1.0), False)
    def test_H15_rail_is_10_can_be_lowered_never_raised(self):
        self.assertEqual(self.m.cfg()["total_max_trades"], 10)
        for v, want in ((25, 10), (10, 10), (6, 6)):
            json.dump({"total_max_trades": v}, open(self.m.CFG, "w")); self.assertEqual(self.m.cfg()["total_max_trades"], want)
        self._open(4, 2, 0); self.assertEqual(self._try("OXL2"), "skip:total_max_trades")     # lowered to 6: 6 open blocks
    def test_H15_sweep_every_bucket_mix_never_exceeds_ten(self):
        c = self.m.cfg(); lim = dict(OXL2=c["max_trades"], OXF1=c["trend_max_trades"], OXP1=c["cmdty_max_trades"])
        n_checked = 0
        for b in range(0, 11):
            for t in range(0, 5):
                for k in range(0, 7):
                    self._open(b, t, k); n = b + t + k; own = dict(OXL2=b, OXF1=t, OXP1=k)
                    for d in ("OXL2", "OXF1", "OXP1"):
                        r = self._try(d); n_checked += 1
                        if n >= 10: self.assertEqual(r, "skip:total_max_trades", (b, t, k, d))
                        else:
                            self.assertNotIn("total_max_trades", r, (b, t, k, d))
                            if own[d] >= lim[d]: self.assertTrue(r.endswith("max_trades"), (b, t, k, d, r))
        self.assertGreater(n_checked, 900)
    def test_H15_other_owners_and_protected_trades_do_not_count(self):
        self._open(9, 0, 0); self.open += [self._t(90, "USD_JPY", "X", tag="AGGRO"), dict(self._t(91, "EUR_USD", "OXL2"), id="61365")]
        self.assertNotIn("total_max_trades", self._try("OXF1"))
    def test_H15_pending_exit_defers_instead_of_dropping(self):
        self._open(6, 2, 2)
        json.dump({"exits": [{"instrument": "EUR_GBP", "trade_id": "100"}]}, open(os.path.join(self.m.ST, self.m.EXITS), "w"))
        self.assertEqual(self._try("OXL2"), "defer:total_max_trades_exit_pending")
    def test_H15_only_one_tfo_entry_path_exists(self):
        src = open(os.path.join(ROOT, "oanda_sleeve.py")).read()
        self.assertEqual(src.count('acct("/orders")'), 1)        # every TFO entry goes through place_entry's gate
        self.assertIn("len(tr) >= c[\"total_max_trades\"]", inspect.getsource(self.m.place_entry))


# ------------------------------------------------------------ unittest wiring: one test method per case (and per parameter set)
def _run_case(fn, kw):
    def method(self):
        mp, tmp = _MonkeyPatch(), pathlib.Path(tempfile.mkdtemp(prefix="audit20261027_"))
        try:
            _iso(mp, tmp); sig = inspect.signature(fn).parameters
            args = dict(kw); args.update({k: v for k, v in (("monkeypatch", mp), ("tmp_path", tmp)) if k in sig})
            if getattr(fn, "_known_gap", None):
                try: fn(**args)
                except Exception as e: self.skipTest(f"KNOWN GAP still open ({fn._known_gap}): {type(e).__name__}")
            else: fn(**args)
        finally:
            mp.undo(); shutil.rmtree(tmp, ignore_errors=True)
    method.__doc__ = (fn.__doc__ or "").strip().split("\n")[0] or None
    return method


class RegressionAudit20261027(unittest.TestCase):
    """Permanent locks for the 27 Oct 2026 audit fixes. See research/full_audit_20261027/VERIFY.md."""


def _ident(v):
    return re.sub(r"\W+", "_", str(v)).strip("_")[:40]
import re
for _name, _fn in sorted(list(globals().items())):
    if _name.startswith("case_") and callable(_fn):
        for _i, _kw in enumerate(getattr(_fn, "_params", None) or [{}]):
            _suffix = ("__" + "_".join(_ident(v) for v in _kw.values())) if _kw else ""
            setattr(RegressionAudit20261027, "test_" + _name[5:] + _suffix, _run_case(_fn, _kw))


if __name__ == "__main__":
    unittest.main()
