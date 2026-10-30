#!/usr/bin/env python3
"""INTRADAY RESCAN (PAPER ONLY) - Brett 2026-10-28 09:59 WITA approval.
While a market is open, run a FULL fresh scan + rank every RESCAN_MIN (45) minutes and arm NEW setups into OPEN slots under the
existing rules only: max 3 open/armed per book (OANDA own 10 cap, untouched here), cost caps (0.15R default / Asia engine 0.20R as
wired / LSE 0.30R), the four open-time checks (gap vs plan, relative strength vs index, opening-range volume, spread), the GPT/Claude
verdict stays ADVISORY as wired (hunt_verdict_veto), desk clearance must PASS. A name that is already OPEN or ARMED in the book is never
re-armed; a name ever demoted/promoted is never re-armed (engine law). Session hours respected: slots are open+45, +90, ... and stop
30 min before the close; slots inside a lunch break (JP, HK) are skipped. Crypto is 24/7: the Kraken entry engine already scans every
5 min and owns fills, so its rescan refreshes the ranked watch list only (no arming here).
Chains (existing scripts only, no new trading logic):
  ASX  : universe_asx.py -> asx_hunt48.py -> hunt_clearance run/finalize --book asx -> asx_hunt48.py (HUNT48_INTRADAY=1)
  ASIA/LSE: universe_asia.py <m> -> asia_hunt48.py <m> -> hunt_clearance run/finalize --book <m> -> asia_hunt48.py <m> (HUNT48_INTRADAY=1)
  US   : run_scan (orchestrator.us_scan) -> us_build_plan -> hunt_clearance run -> new CLEARED top-decile names re-checked
         (recheck_us + post_open_gates = RS, OR break+volume, spread; gap via live px vs trigger/stop) -> apply-plan main book.
No broker, no live orders, no keys.
  python3 intraday_rescan.py cron            # launchd com.tfo.intraday-rescan every 300s
  python3 intraday_rescan.py run <mkt> [--force]
  python3 intraday_rescan.py schedule
"""
import datetime as dt, json, os, subprocess, sys, time
from zoneinfo import ZoneInfo
BASE = os.environ.get("DESK_BASE", os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable or "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
UTC = dt.timezone.utc; WITA = ZoneInfo("Asia/Makassar")
RESCAN_MIN = 45; STOP_BEFORE_CLOSE_MIN = 30; FRESH_CLEARANCE_MIN = 60
# 2026-10-30 asiafix: the slot is the run START, so the final run must FINISH before close-30 (arm cutoff). Budget = observed run
# (jp ~5m, asx ~3m, lse ~5m) + up to 5m cron lag + margin. Only the still-running books (HK/KR/TW/SG paused 19:56 -> unchanged).
RUN_BUDGET_MIN = {"jp": 12, "asx": 10, "lse": 12}
MARKETS = ("us", "asx", "jp", "hk", "sg", "kr", "tw", "lse", "crypto")
SD = os.path.join(BASE, "state", "intraday_rescan"); STATUS = os.path.join(BASE, "static", "intraday_rescan_status.json")
INDEX = dict(us="SPY", asx="^AXJO", jp="^N225", hk="^HSI", sg="^STI", kr="^KS11", tw="^TWII", lse="^FTSE")

def now_utc(): return dt.datetime.now(UTC)
def wita(t): return t.astimezone(WITA).strftime("%a %d %b %H:%M WITA") if t else None
def jload(p, d=None):
    try: return json.load(open(p))
    except Exception: return d
def atomic(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True); tmp = p + ".tmp"
    with open(tmp, "w") as f: json.dump(o, f, indent=1, default=str)
    os.replace(tmp, p)

# ---------------------------------------------------------------- sessions (DST-safe via zoneinfo)
def sessions(mkt):
    """(tz, [(open 'HH:MM', close 'HH:MM'), ...], trading_day(date))"""
    if mkt == "us":
        import hunt48_short_paper as H
        return "America/New_York", [("09:30", "16:00")], H.ny_session
    if mkt == "asx":
        import asx_hunt48 as A
        return "Australia/Sydney", [("10:00", "16:00")], A.trading_day
    import asia_markets as AM
    M = AM.MARKETS[mkt]; hol = set(M["holidays"])
    return M["tz"], list(M["sessions"]), (lambda d: d.weekday() < 5 and d not in hol)
def _at(d, tz, hm): h, m = map(int, hm.split(":")); return dt.datetime.combine(d, dt.time(h, m), ZoneInfo(tz)).astimezone(UTC)
def slots_for_day(mkt, d):
    """Rescan times (UTC) for local date d: open+45k, <= close-30, not inside a lunch break."""
    tz, ss, td = sessions(mkt)
    if not td(d): return []
    o = _at(d, tz, ss[0][0]); c = _at(d, tz, ss[-1][1]); out = []; t = o + dt.timedelta(minutes=RESCAN_MIN)
    segs = [(_at(d, tz, a), _at(d, tz, b)) for a, b in ss]
    while t <= c - dt.timedelta(minutes=STOP_BEFORE_CLOSE_MIN):
        if any(a <= t <= b for a, b in segs): out.append(t)
        t += dt.timedelta(minutes=RESCAN_MIN)
    bud = RUN_BUDGET_MIN.get(mkt)
    if bud and out:   # last start must leave the run budget before the arm cutoff; replace a too-late slot with one at cutoff-budget (5-min floor)
        lim = c - dt.timedelta(minutes=STOP_BEFORE_CLOSE_MIN + bud); lim = lim - dt.timedelta(minutes=lim.minute % 5, seconds=lim.second, microseconds=lim.microsecond)
        if out[-1] > lim:
            out = [x for x in out if x <= lim]
            if any(a <= lim <= b for a, b in segs) and (not out or lim > out[-1]): out.append(lim)
    return out
def last_start_ok(mkt, now):
    """Latest start (UTC) for a run that must finish (RUN_BUDGET_MIN) before close-STOP_BEFORE_CLOSE_MIN, today local; far future if no budget/no session."""
    tz, ss, td = sessions(mkt); d = now.astimezone(ZoneInfo(tz)).date()
    if not td(d): return now + dt.timedelta(days=3650)
    return _at(d, tz, ss[-1][1]) - dt.timedelta(minutes=STOP_BEFORE_CLOSE_MIN)
def session_open_utc(mkt, now):
    tz, ss, td = sessions(mkt); d = now.astimezone(ZoneInfo(tz)).date()
    return _at(d, tz, ss[0][0]) if td(d) else None
def in_session(mkt, now):
    if mkt == "crypto": return True
    tz, ss, td = sessions(mkt); d = now.astimezone(ZoneInfo(tz)).date()
    return td(d) and any(_at(d, tz, a) <= now <= _at(d, tz, b) for a, b in ss)
def crypto_slots(now):
    d0 = dt.datetime.combine(now.astimezone(UTC).date(), dt.time(0, 0), UTC)
    return [d0 + dt.timedelta(minutes=RESCAN_MIN * k) for k in range(0, 24 * 60 // RESCAN_MIN + 2)]
def current_slot(mkt, now):
    """Latest slot <= now (within RESCAN_MIN) or None."""
    if mkt == "crypto": c = [s for s in crypto_slots(now) if s <= now]
    else:
        tz = sessions(mkt)[0]; c = [s for s in slots_for_day(mkt, now.astimezone(ZoneInfo(tz)).date()) if s <= now]
    s = c[-1] if c else None
    return s if s and now - s < dt.timedelta(minutes=RESCAN_MIN) else None
def next_slot(mkt, now):
    if mkt == "crypto": return next(s for s in crypto_slots(now) + crypto_slots(now + dt.timedelta(days=1)) if s > now)
    tz = sessions(mkt)[0]; d = now.astimezone(ZoneInfo(tz)).date()
    for i in range(10):
        for s in slots_for_day(mkt, d + dt.timedelta(days=i)):
            if s > now: return s
    return None
def due(mkt, now, st=None):
    st = st if st is not None else jload(os.path.join(SD, "state.json"), {}) or {}
    s = current_slot(mkt, now)
    if not s or os.path.exists(os.path.join(SD, f"lock_{mkt}")): return False
    bud = RUN_BUDGET_MIN.get(mkt)   # 2026-10-30 asiafix: don't START a run that cannot finish before the close-30 arm cutoff
    if bud and now + dt.timedelta(minutes=bud) > last_start_ok(mkt, now): return False
    last = (st.get(mkt) or {}).get("slot_utc")
    return last != s.isoformat()

# ---------------------------------------------------------------- the four open-time checks (open_gates pieces, fail closed)
def open_time_checks(mkt, t, now=None, bars_fn=None, idx_fn=None, spread_fn=None):
    """(ok, [why]) for an intraday arm: (1) gap: today's opening print vs cleared trigger/stop, (2) RS vs index since prior close
    (longs beating / shorts lagging), (3) opening-range volume >= VOL_MIN x baseline, (4) live spread <= 2x normal. Any data error -> not ok."""
    import open_gates as OG
    OG.SPREAD_DIR = os.path.join(BASE, "state", "hunt_orch", "spreads")
    now = now or now_utc(); o = session_open_utc(mkt, now); sym = t.get("yahoo") or t.get("yf") or t.get("ticker") or t.get("sym")
    tt = dict(t, ticker=sym, direction=t.get("direction", "long"))
    try:
        bars = (bars_fn or (lambda s: OG.intraday(s, "5m", "30d")))(sym)
        if not o or not bars: return False, ["no session/intraday bars (fail closed)"]
        today = [b for b in bars if b["start"] >= o]; prior = [b for b in bars if b["start"] < o]
        if not today or not prior: return False, ["no bars today/prior (fail closed)"]
        why = []
        ok1, w1, _ = OG.gap_gate(tt, today[0]["o"])
        if not ok1: why.append("gap: " + w1)
        pc = prior[-1]["c"]; ix = (idx_fn or (lambda: OG.intraday(INDEX[mkt], "5m", "5d")))() or []
        ixp = [b for b in ix if b["start"] < o]; ixt = [b for b in ix if b["start"] >= o]
        ichg = OG.pct(ixt[-1]["c"], ixp[-1]["c"]) if ixp and ixt else None
        ok5, w5 = OG.rs_gate(tt, OG.pct(today[-1]["c"], pc), ichg)
        if not ok5: why.append("RS: " + w5)
        orng = OG.opening_range(bars, o, OG.OR_MIN)
        if not orng or not orng.get("complete"): why.append("opening range not complete")
        else:
            opens = [b["start"] for i, b in enumerate(bars) if b["start"] < o and (i == 0 or (b["start"] - bars[i - 1]["start"]).total_seconds() > 3600)][-20:]
            base, _n = OG.or_volume_baseline(bars, opens, OG.OR_MIN); okv, wv, _vr = OG.volume_gate(orng["vol"], base)
            if not okv: why.append("volume: " + wv)
        normal = OG.normal_spread_bp(sym, OG.corwin_schultz_bp(prior[-78:]), today[-1]["c"]); live = (spread_fn or OG.live_spread_bp)(sym)
        oks, ws = OG.spread_gate(live, normal)
        if not oks: why.append("spread: " + ws)
        return not why, why
    except Exception as e: return False, [f"open-time check data error (fail closed): {str(e)[:80]}"]

# ---------------------------------------------------------------- chains
def sh(args, timeout=2400, env=None, log=None):
    e = dict(os.environ); e.update(env or {}); t0 = time.time()
    try:
        r = subprocess.run(args, cwd=BASE, capture_output=True, text=True, timeout=timeout, env=e); rc, out = r.returncode, (r.stdout or "")[-800:] + (r.stderr or "")[-400:]
    except subprocess.TimeoutExpired: rc, out = -9, f"timeout {timeout}s"
    if log is not None: log.append(dict(cmd=" ".join(a for a in args[1:4]), rc=rc, s=round(time.time() - t0), tail=out[-300:]))
    return rc, out
def _ledger(mkt):
    sd = {"asx": "hunt48_asx", "us": "hunt48_short"}.get(mkt, f"hunt48_{mkt}")
    return jload(os.path.join(BASE, "state", sd, "ledger.json"), {"trades": []}) or {"trades": []}
def _live_syms(L): return {(t.get("sym") or t.get("ticker")) for t in L.get("trades", []) if t.get("status") in ("ARMED", "FILLED", "PARTIAL", "awaiting_funder")}
def engine_chain(mkt, log):
    lk = os.path.join(BASE, "state", f"hunt48_{mkt}", "preopen.lock")    # the 120s tick skips while this exists (same as the pre-open chain)
    if os.path.exists(lk) and time.time() - os.path.getmtime(lk) < 2 * 3600: log.append(dict(step="chain", note="pre-open/rescan chain already running")); return 0
    open(lk, "w").write("intraday_rescan")
    try: return _engine_chain(mkt, log)
    finally:
        try: os.remove(lk)
        except Exception: pass
def _engine_chain(mkt, log):
    if mkt == "asx":
        sh([PY, "-W", "ignore", "universe_asx.py"], 1200, {"RANKER_DIR": "state/calendar/ranker"}, log)
        eng = [PY, "-W", "ignore", "asx_hunt48.py"]
    else:
        sh([PY, "-W", "ignore", "universe_asia.py", mkt], 1200, None, log); eng = [PY, "-W", "ignore", "asia_hunt48.py", mkt]
    sh(eng, 900, None, log)
    sh([PY, "-W", "ignore", "hunt_clearance.py", "run", "--book", mkt], 1800, None, log)
    sh([PY, "-W", "ignore", "hunt_clearance.py", "finalize", "--book", mkt], 300, None, log)
    rc, _ = sh(eng, 900, {"HUNT48_INTRADAY": "1"}, log)
    return rc
def us_chain(now, log, arm=True):
    import hunt_orchestrator as HO, hunt48_short_paper as H, setup_hash as SH
    D = HO.dkey(now); ok, note = HO.us_scan(D + "_rescan_" + now.astimezone(WITA).strftime("%H%M"), log); log.append(dict(step="us_scan", ok=ok, note=note))
    if not ok: return 1
    P, an = HO.us_build_plan(D, H.live_audusd(0.70), log)
    sh([PY, "-W", "ignore", "hunt_clearance.py", "run"], 2400, None, log)
    cl = HO.clearance_status(D, "main"); L = HO.us_ledger("main"); held = _live_syms(L); armed = []
    if not arm or HO.us_open_slots(L) <= 0: log.append(dict(step="us_arm", note="no open slot" if arm else "arm off")); return 0
    o = HO.us_open_today(now); C = HO.cfg()
    for s in sorted(P["set"], key=lambda s: -(an.get(s["ticker"], {}).get("score") or 0)):
        tk = s["ticker"]; c = cl.get(tk) or {}
        if tk in held: continue                                                     # never re-arm an open/armed name
        if not s.get("top_decile"): continue                                        # existing rule: only top decile arms
        if c.get("final") != "CLEARED" or c.get("setup_hash") != SH.setup_hash(s): continue
        try: px = (H.bars_15m(tk) or [{}])[-1].get("c"); daily = H.bars_daily(tk)
        except Exception: px, daily = None, []
        why = HO.recheck_us(s, HO.us_ledger("main"), px, daily, now)
        if not why:
            g = HO.post_open_gates(s, now, C, open_utc=o); why = g["why"]; nt = g.get("new_t")
        if why: log.append(dict(step="us_skip", ticker=tk, why=why[:3])); continue
        spec = dict(nt or s, cleared=D, intraday_rescan=wita(now))
        if HO.apply_arm("main", [spec], D, f"intraday rescan {wita(now)}: cleared + 4 open-time checks", log): armed.append(tk); held.add(tk)
        if HO.us_open_slots(HO.us_ledger("main")) <= 0: break
    log.append(dict(step="us_arm", armed=armed)); return 0
def run(mkt, now=None, force=False):
    now = now or now_utc(); log = []; os.makedirs(SD, exist_ok=True)
    if mkt != "crypto" and not in_session(mkt, now) and not force: return dict(market=mkt, skipped="market closed")
    lock = os.path.join(SD, f"lock_{mkt}"); open(lock, "w").write(str(os.getpid()))
    before = _live_syms(_ledger(mkt)) if mkt != "crypto" else set(); rc = 0
    try:
        if mkt == "crypto": rc, _ = sh([PY, "-W", "ignore", "hunt_orchestrator.py", "plan", "crypto"], 300, None, log)
        elif mkt == "us": rc = us_chain(now, log)
        else: rc = engine_chain(mkt, log)
    except Exception as e: rc = 1; log.append(dict(error=str(e)[:300]))
    finally:
        try: os.remove(lock)
        except Exception: pass
    after = _live_syms(_ledger(mkt)) if mkt != "crypto" else set()
    newly = sorted(after - before); slot = current_slot(mkt, now)
    rec = dict(market=mkt, slot_utc=(slot or now).isoformat(), ran_wita=wita(now), finished_wita=wita(now_utc()), rc=rc, newly_armed=newly,
               next_wita=wita(next_slot(mkt, now_utc())), steps=len(log))
    st = jload(os.path.join(SD, "state.json"), {}) or {}; st[mkt] = rec; atomic(os.path.join(SD, "state.json"), st)
    with open(os.path.join(SD, "events.jsonl"), "a") as f: f.write(json.dumps(dict(rec, log=log), default=str) + "\n")
    write_status(); return rec
def write_status(now=None):
    now = now or now_utc(); st = jload(os.path.join(SD, "state.json"), {}) or {}; out = {}
    for m in MARKETS:
        r = st.get(m) or {}; n = next_slot(m, now)
        out[m] = dict(last_rescan_wita=r.get("ran_wita"), last_rc=r.get("rc"), newly_armed=r.get("newly_armed") or [], next_rescan_wita=wita(n), in_session=in_session(m, now),
                      every_min=RESCAN_MIN, note="entry engine scans every 5 min + owns fills; rescan refreshes ranking" if m == "crypto" else None)
    atomic(STATUS, dict(paper_only=True, updated_wita=wita(now), every_min=RESCAN_MIN, markets=out)); return out
def cron(now=None):
    now = now or now_utc(); st = jload(os.path.join(SD, "state.json"), {}) or {}; started = []
    for f in os.listdir(SD) if os.path.isdir(SD) else []:
        p = os.path.join(SD, f)
        if f.startswith("lock_") and time.time() - os.path.getmtime(p) > 2 * 3600: os.remove(p)
    for m in MARKETS:
        if due(m, now, st):
            os.makedirs(os.path.join(SD, "logs"), exist_ok=True); lf = open(os.path.join(SD, "logs", f"{m}_{now.astimezone(WITA):%Y%m%d}.log"), "a")
            open(os.path.join(SD, f"lock_{m}"), "w").write("spawning")
            subprocess.Popen([PY, "-W", "ignore", os.path.abspath(__file__), "run", m], cwd=BASE, stdout=lf, stderr=lf, start_new_session=True); started.append(m)
    write_status(now); return started
if __name__ == "__main__":
    a = sys.argv[1:] or ["schedule"]
    if a[0] == "cron": print(json.dumps(dict(spawned=cron())))
    elif a[0] == "run": print(json.dumps(run(a[1], force="--force" in a), default=str))
    elif a[0] == "status": print(json.dumps(write_status(), indent=1))
    else:
        n = now_utc(); print("\n".join(f"{m:7s} in_session={in_session(m, n)!s:5s} next {wita(next_slot(m, n))}" for m in MARKETS))
