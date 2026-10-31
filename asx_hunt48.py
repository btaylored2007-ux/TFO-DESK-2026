#!/usr/bin/env python3
"""HUNT48 ASX BOOK (PAPER ONLY) - Brett 2026-10-27 (pt1470u): ASX run as a SEPARATE A$1,000,000 AUD paper book.
Own ledger state/hunt48_asx/ledger.json, own status static/hunt48_asx_status.json, own MAX 3 open/armed slots
(NOT counted against US/crypto/FX hunt books, and theirs do not count against it), own bench of up to 15 armed backups.
Same rules as the calendar/hunt engine: event_calendar.gate() (ADV>=A$2m ASX-only (27 Oct), ATR<=6%, RSI/chase, ASX shortable list, tick+slip<=0.15R),
earnings inside hold window -> BLOCK, price-sensitive ASX announcement = trigger (+0.5 score), ^AXJO regime filter, rank_score>=3,
ONE per sector, direction balance (if 2 same direction, 3rd slot opposite only), A$2,500 risk/trade, levels from stage_levels()
(costs: IBKR Fixed 0.08% min A$6/side + 10bp/side + half tick/side + IBKR borrow, via ibkr_costs.py, 27 Oct). Shorts only where asx_shortable (flagged). Fills simulated on Yahoo .AX 1h bars
via cal_paper_bench.step(); ASX hours 07:00-13:00 WITA. Exit at PRIOR SESSION CLOSE if an event (earnings/result) falls before the
next open, and at last session close <= fill+72h. No broker, no orders, no keys."""
import csv, datetime as dt, json, os, sys
from zoneinfo import ZoneInfo
import cal_paper_bench as CB, event_calendar as EC
import hunt48_room as HR   # 2026-10-27 Rules Auditor 08:40: single room definition + B1 short screen (fail closed)
HARD_DISARMED = {"ARB.AX"}   # 2026-10-28 Brett: ARB.AX stays disarmed (was kept off by GPT FAIL; AI now advisory)
import hunt_verdict_veto as VV   # 2026-10-27 20:56 WITA Brett: GPT/Claude verdict veto (BLOCKED / GPT FAIL / CLAUDE FAIL / missing-stale -> never arm)
import ibkr_costs as IC   # 2026-10-27 Brett: real IBKR ASX Fixed 0.08% min A$6/side + IBKR per-name borrow + 10bp slip/side (+half tick/side)
COST_R_MAX = 0.20; MIN_ROOM_R = HR.MIN_ROOM_R; HOLD_DAYS = 3; ASX_SLIP = 0.001   # 2026-10-28 07:18 Brett: ASX/Asia cost cap 0.15 -> 0.20R
def ibkr_cost(lv, sym, direction, px=None):
    """Overwrite lv cost fields with the IBKR schedule (AUD-native). Sizing/levels untouched."""
    q, mf = lv["shares"], lv["max_fill"]; n_aud = q * mf; side = IC.asx_side_aud(q, mf); tick = EC.asx_tick(px or mf)
    bpa = IC.borrow_pa(sym, "asx") if direction == "short" else 0.0; borrow = n_aud * bpa * HOLD_DAYS / 365
    cost_aud = 2 * side + 2 * ASX_SLIP * n_aud + tick * q + borrow
    risk = q * abs(mf - lv["stop"]) or 1
    lv.update(est_cost_aud=round(cost_aud, 2), est_cost_usd=round(cost_aud * AUDUSD, 2), cost_R=round(cost_aud / risk, 3), cost_side_aud=round(side, 2),
              borrow_pa=round(bpa, 5), borrow_aud=round(borrow, 2), cost_model="IBKR Fixed ASX 0.08% min A$6/side + 10bp/side + half tick/side + IBKR borrow 3d")
    return lv
def _daily(sym):      # 1y daily bars (was 3mo); [] -> BENCH (fail closed)
    if getattr(HR, "_fetch", None) is getattr(HR, "_fetch_yf", None):   # 2026-10-30 paidbars: paid-first (EODHD exact match), HR.daily (Yahoo) fallback; tests that patch HR._fetch keep HR.daily
        import paid_bars as PB; return PB.daily_hr(sym, HR.daily, HR.DATA_STATUS)
    return HR.daily(sym)
def opposing_levels(daily, long=False):   # same as hunt48_short_paper.opposing_levels (pivots 2 each side + 20/50d MA, today excluded)
    d = daily[:-1] if len(daily) > 5 else daily; lv = []
    for i in range(2, len(d) - 2):
        if not long and d[i]["l"] <= min(d[j]["l"] for j in (i-2, i-1, i+1, i+2)): lv.append(d[i]["l"])
        if long and d[i]["h"] >= max(d[j]["h"] for j in (i-2, i-1, i+1, i+2)): lv.append(d[i]["h"])
    for n in (20, 50):
        if len(d) >= n: lv.append(sum(x["c"] for x in d[-n:]) / n)
    return lv
def cost_gate(t, daily=None):
    """None if ok; else reason. cost_R <= 0.15R AND hunt48_room.room(): room_R = min(structure, T2) - cost_R >= 1R.
    Fail closed: no daily bars / missing cost or levels -> reason (BENCH). Always writes numeric t['room_R']."""
    daily = _daily(t.get("yahoo") or t["sym"]) if daily is None else daily
    why = HR.room(t, daily, min_bars=HR.US_MIN_BARS)   # audit M4 hardening (Brett 27 Oct 2026 21:11 WITA): <200 valid daily bars -> BENCH (fail closed)
    if (t.get("cost_R") or 0) > COST_R_MAX: return f"IBKR cost {t['cost_R']:.3f}R > {COST_R_MAX}R"
    if t.get("room_R") is None: return "room_R null -> BENCH (never fail open)"
    return why
def no_borrow(t): return t["direction"] == "short" and not IC.has_borrow(t["sym"], t["shares"], "asx")
BASE = os.environ.get("DESK_BASE", os.path.dirname(os.path.abspath(__file__)))
SD = os.path.join(BASE, "state", "hunt48_asx"); LEDGER = os.path.join(SD, "ledger.json")  # defaults; run()/write_status always re-derive from `base`
STATUS = os.path.join(BASE, "static", "hunt48_asx_status.json")
def paths(base=BASE):
    """2026-10-27 audit: ledger + status follow `base` so a test/sim with base=tmp cannot write live state (the 14:39 WITA bug)."""
    sd = os.path.join(base, "state", "hunt48_asx")
    return sd, os.path.join(sd, "ledger.json"), os.path.join(base, "static", "hunt48_asx_status.json")
UTC = dt.timezone.utc; WITA = ZoneInfo("Asia/Makassar"); SYD = ZoneInfo("Australia/Sydney")
CAPITAL_AUD = 1_000_000.0; RISK_AUD = 2500.0; MAX_OPEN = 3; MAX_BENCH = 40; MIN_SCORE = 3.0; EVENT_BONUS = 0.5   # fix 4: MAX_BENCH default 40 (was 15); live per-market value from state/hunt_bench.json via HR.max_bench
MAX_NOTIONAL_AUD = CAPITAL_AUD / 3; MAX_PCT_ADV = 5.0; THIN_ADV_AUD = 5e6; THIN_PCT_ADV = 2.0; AUDUSD = CB.AUDUSD   # THIN_*: Brett 27 Oct 04:50 ADV<A$5m -> 2% ADV cap
ASX_HOLIDAYS = {dt.date(2026, 12, 25), dt.date(2026, 12, 28), dt.date(2027, 1, 1), dt.date(2027, 1, 26)}

def trading_day(d): return d.weekday() < 5 and d not in ASX_HOLIDAYS
def session_open(d): return dt.datetime.combine(d, dt.time(10, 0), SYD).astimezone(UTC)
def session_close(d): return dt.datetime.combine(d, dt.time(16, 0), SYD).astimezone(UTC)
def next_td(d):
    d += dt.timedelta(days=1)
    while not trading_day(d): d += dt.timedelta(days=1)
    return d
def prev_td(d):
    d -= dt.timedelta(days=1)
    while not trading_day(d): d -= dt.timedelta(days=1)
    return d
def in_hours(now, pad_min=20):
    d = now.astimezone(SYD).date()
    return trading_day(d) and session_open(d) - dt.timedelta(minutes=pad_min) <= now <= session_close(d) + dt.timedelta(minutes=pad_min)
def clearance(now, base=BASE):
    """Desk clearance (hunt_clearance.py run --book asx): {sym: summary row} for TODAY (WITA) only; missing/stale -> {} (nothing promotes)."""
    p = os.path.join(base, "state", "clearance", now.astimezone(WITA).strftime("%Y%m%d"), "asx", "summary.json")
    try: return json.load(open(p))["books"].get("asx", {})
    except Exception: return {}
def intraday_ok(now, base=BASE):
    """HUNT48_INTRADAY=1 (set by intraday_rescan) + ASX in session and >=30 min before close + today's asx clearance <= 60 min old."""
    if os.environ.get("HUNT48_INTRADAY") != "1": return False
    d = now.astimezone(SYD).date()
    if not trading_day(d) or not (session_open(d) <= now <= session_close(d) - dt.timedelta(minutes=30)): return False
    p = os.path.join(base, "state", "clearance", now.astimezone(WITA).strftime("%Y%m%d"), "asx", "summary.json")
    return os.path.exists(p) and now.timestamp() - os.path.getmtime(p) <= 3600
def valid_from_for(now):
    """Brett/parent 27 Oct 04:54 WITA: before today's session open on a trading day -> TODAY's open; otherwise next session open."""
    d = now.astimezone(SYD).date()
    if trading_day(d) and now < session_open(d) - dt.timedelta(minutes=20): return session_open(d)
    return next_open_after_today(now)
def next_open_after_today(now):
    """Arming outside/inside a session -> valid from the NEXT session open (Brett: arm Tue for Wed open)."""
    return session_open(next_td(now.astimezone(SYD).date()))
def w(t): return t.astimezone(WITA).strftime("%a %d %b %H:%M WITA") if t else None

def load_rows(base=BASE):
    p = os.path.join(base, "state", "calendar", "inputs", "ranked_asx.csv")
    rows = {r["sym"]: r for r in csv.DictReader(open(p))} if os.path.exists(p) else {}
    HR.fill_b1_inputs(rows, _daily)      # Brett 28 Oct hunt fix 2: missing roc5/atr_pct filled from daily bars (fail closed only if unfetchable, logged)
    for r in rows.values():            # same as event_calendar.load_universe: ADV in USD from adv_aud
        if not EC.fnum(r.get("adv_usd")) and EC.fnum(r.get("adv_aud")): r["adv_usd"] = EC.fnum(r["adv_aud"]) * AUDUSD
    return rows
def load_events(base=BASE, now=None):
    j = CB.load(os.path.join(base, "state", "calendar", "inputs", "asx_events.json"), {})
    now = now or CB.now_utc(); ann, earn = {}, {}
    for a in j.get("announcements", []):
        if a.get("price_sensitive") and (now - dt.datetime.fromisoformat(a["when_utc"])).total_seconds() <= 4 * 86400:
            ann.setdefault(a["code"] + ".AX", []).append(a["headline"])
    for c, d in (j.get("earnings") or {}).items():
        try: earn[c + ".AX"] = dt.date.fromisoformat(d)
        except Exception: pass
    return ann, earn, set((c if c.endswith(".AX") else c + ".AX") for c in (j.get("halted") or []))

def event_cut(sym, earn, fill_utc):
    """latest allowed exit: prior session close before an event dated after fill day, and last session close <= fill+72h."""
    fd = fill_utc.astimezone(SYD).date(); cut = fill_utc + dt.timedelta(hours=72); why = "72h time exit (last session close)"
    d = cut.astimezone(SYD).date()
    while not (trading_day(d) and session_close(d) <= cut): d -= dt.timedelta(days=1)
    cut = session_close(d)
    e = earn.get(sym)
    if e and e > fd:
        pc = session_close(prev_td(e))
        if pc < cut: cut, why = pc, f"event {e} before open -> exit prior session close"
    return cut, why

def review(rows, ann, earn, halted, now, hold_end):
    reg = EC.asx_regime(rows); out = []
    for s, r in rows.items():
        if r.get("cls") != "asx_equity": continue
        d = r.get("dir") if r.get("dir") in ("long", "short") else "long"
        score = EC.fnum(r.get("rank_score"), 0) or 0; why = EC.gate(r, d)
        rsl = HR.rs_long(r, reg, MIN_SCORE)   # 2026-10-29: RS long vs short regime (Brett-approved loosening)
        if rsl: d = "long"; score = EC.fnum(r.get("score"), 0) or 0; why = EC.gate(r, d) + ["RS-long tag: rs20 %.1f vs index in short regime" % EC.fnum(r.get("rs20"), 0)][:0]
        if d == "short": why += HR.rsi_block(r)                     # B1: RSI<30 or missing -> blocked (Rules Auditor 08:40)
        if d == "short" and not why: why += HR.exhaustion_block(r, _daily(s))   # B1: true 20d low + 5d drop >1.5 ATR (fail closed)
        if s in halted: why.append("trading halt")
        if reg in ("long", "short") and reg != d and not rsl: why.append(f"regime filter (^AXJO): {reg}-only")
        try:
            import catalyst as CAT; cb, cinfo = CAT.bonus("asx", s, d, score, MIN_SCORE)   # 2026-10-28 Brett 10:29: capped news catalyst
        except Exception: cb, cinfo = 0.0, None
        score += cb
        if score < MIN_SCORE: why.append(f"rank_score {score:.2f}<3.0")
        e = earn.get(s)
        if e and now.astimezone(SYD).date() - dt.timedelta(days=1) <= e <= hold_end.astimezone(SYD).date(): why.append(f"BLOCK: earnings/result {e} inside hold window")
        out.append(dict(sym=s, dir=d, rank_score=round(score, 2), sector=r.get("sector") or s, shortable=str(r.get("asx_shortable")) == "True",
                        short_src=r.get("asx_short_src"), anns=ann.get(s, [])[:3], passed=not why, why=why, catalyst=cinfo,
                        score=round(score + (EVENT_BONUS if s in ann and not cb else 0), 2)))   # total news lift capped at +0.5
    return reg, out

def levels(r, d):
    # 2026-10-29 14:12 WITA TFO head-trader (PAPER): ASX drops the 28 Oct 07:12 fixed +3%/-8% levels -> R-based targets (pre-28-Oct path:
    # stop = min(1.5 ATR, 5%), T2 = HR.T2_R x stop distance). MIN_ROOM_R 1.0 unchanged, no exemption.
    lv = EC.stage_levels(r, d, RISK_AUD, AUDUSD, t2_r=HR.T2_R, maxfill_atr=CB.hunt_fill_rule()["max_fill_atr"])   # fix 3 band; 2026-10-27 pt1486u: hunt48 T2 = 1.5R
    if not lv or not lv["shares"]: return None
    n_aud = lv["notional_usd"] / AUDUSD; adv_aud = EC.fnum(r.get("adv_aud"), 0)
    if not adv_aud: return None   # 2026-10-29 item 8: null ADV is a reject (never size against a fake A$1)
    pct = THIN_PCT_ADV if adv_aud < THIN_ADV_AUD else MAX_PCT_ADV      # liquidity-scaled: thin names (A$2-5m ADV) capped at 2% ADV
    cap = min(MAX_NOTIONAL_AUD, adv_aud * pct / 100); lv["cap_aud"] = round(cap); lv["cap_rule"] = f"min(A$333k, {pct:g}% of 20d ADV A${adv_aud/1e6:.1f}m)"
    if n_aud > cap:      # notional cap -> fewer shares, risk scales down
        k = cap / n_aud; lv["shares"] = int(lv["shares"] * k); lv["notional_usd"] = round(lv["notional_usd"] * k); lv["risk_aud"] = round(RISK_AUD * k, 2)
        lv["est_cost_usd"] = round(EC.asx_cost_usd(lv["notional_usd"], EC.fnum(r["px"]), AUDUSD), 2)
    lv["notional_aud"] = round(lv["notional_usd"] / AUDUSD); lv["est_cost_aud"] = round(lv["est_cost_usd"] / AUDUSD, 2)
    lv["risk_aud_actual"] = round(lv["shares"] * abs(lv["max_fill"] - lv["stop"]), 2)
    ibkr_cost(lv, r["sym"], d, EC.fnum(r.get("px")))
    return lv

def mix_ok(c, slots):
    for o in slots:
        if o["sector"] == c["sector"]: return f"sector cap: {o['sym']} holds {c['sector']}"
    same = sum(o["direction"] == c["direction"] for o in slots)
    if len(slots) >= MAX_OPEN - 1 and same >= MAX_OPEN - 1: return f"direction balance: {same} {c['direction']}s; last slot opposite only"
    return None

def _track_fetch(f):   # 2026-10-29 TFO 11:12 WITA fix 2 (additive): record latest hourly bar END used -> health bars_asof
    def g(*a, **k):
        b = f(*a, **k)
        try:
            if b: HR.note_bar(b[-1].get("t"))
        except Exception: pass
        return b
    return g
def _firm_block(trade):
    import firm_pretrade
    return firm_pretrade.blocked(firm_pretrade.hunt_proposal(trade, "hunt_asx", "ASX", CAPITAL_AUD, RISK_AUD))

def run(now=None, fetch=None, base=BASE, force=False):
    if fetch is None:   # 2026-10-30 paidbars: paid-first hourly bars (EODHD where it matches Yahoo exactly), Yahoo CB.bars_yf fallback; source logged
        import paid_bars as PB; fetch = lambda y, s: PB.hourly(y, s, CB.bars_yf)
    fetch = _track_fetch(fetch)
    now = now or CB.now_utc(); rows = load_rows(base); ann, earn, halted = load_events(base, now)
    SD, LEDGER, STATUS = paths(base)   # noqa: N806 — local shadows of module defaults, keyed off `base`
    L = CB.load(LEDGER, dict(book="HUNT48_ASX", paper_only=True, capital_aud=CAPITAL_AUD, ccy="AUD", trades=[]))
    T = L["trades"]; nid = lambda: max([t["id"] for t in T] or [0]) + 1
    valid_from = valid_from_for(now) if not in_hours(now) or now.astimezone(SYD).hour >= 15 else None
    CL = clearance(now, base); VT = VV.load("asx", now, base)    # GPT/Claude verdicts (same files as the US books); missing/stale -> every name vetoed
    hold_end = (valid_from or now) + dt.timedelta(hours=72)
    reg, revs = review(rows, ann, earn, halted, now, hold_end); R = {v["sym"]: v for v in revs}
    # 1-) 2026-10-28 health review (a): BENCH records still on pre-07:12 legacy levels (not +3%/-8%) whose arm time has passed were judged by
    #     the old 1R room rule and blocked the bench (+ the name from fresh staging). Expire them (kept in ledger) so the name is re-staged fresh.
    for t in T:
        # 2026-10-29 14:12 WITA TFO: rule reversed -> the LEGACY shape is now fixed +3%/-8%; such BENCH rows expire and re-stage on R-based levels
        if t["status"] == "BENCH" and dt.datetime.fromisoformat(t["armed_utc"]) <= now and HR.is_fixed(t.get("max_fill") or 0, t.get("stop") or 0, t.get("t2") or 0):
            t.update(status="EXPIRED", note="legacy fixed +3%/-8% levels -> expired; name re-staged fresh on R-based levels (TFO 29 Oct 14:12)", expired_utc=now.isoformat())
    # 1) pending (not yet live) ARMED/BENCH: re-review + re-stage on latest bars; failures -> DISARMED (kept, never deleted)
    for t in T:
        if t["status"] in ("ARMED", "BENCH") and dt.datetime.fromisoformat(t["armed_utc"]) > now:
            v = R.get(t["sym"])
            if not v or not v["passed"] or v["dir"] != t["direction"]:
                why = "re-review failed: " + "; ".join((v or {}).get("why", ["not in universe"]))
                if v and v.get("why") and v["dir"] == t["direction"] and all(HR.is_data_fail(x) for x in v["why"]):   # review fix #3: DATA_FAIL != RULE_FAIL
                    if t["status"] == "ARMED": t.update(status="BENCH", demoted_utc=now.isoformat(), data_fail=why[:200], data_fail_utc=now.isoformat(), bench_note="DATA_FAIL (armed -> bench, fail closed): " + why[:160])
                    else: t.update(data_fail=why[:200], data_fail_utc=now.isoformat())
                    continue
                t.update(status="DISARMED", note=why[:200]); continue
            t.pop("data_fail", None)
            lv = levels(rows[t["sym"]], t["direction"])
            if lv: t.update({k: lv[k] for k in ("trigger", "max_fill", "stop", "t1", "t2", "shares", "risk_aud", "risk_aud_actual", "notional_aud", "est_cost_usd", "est_cost_aud", "cost_R", "rr_t1", "rr_t2", "cost_side_aud", "borrow_pa", "borrow_aud", "cost_model")}, restaged_utc=now.isoformat())
            if t["status"] == "BENCH":
                rw = cost_gate(t)                                                   # room_R always written (no nulls)
                if rw and not t.get("demoted_utc"): t["bench_note"] = "room/cost gate: " + rw
    live = lambda: [t for t in T if t["status"] in ("ARMED", "FILLED", "awaiting_funder")]   # awaiting_funder holds its slot
    # 1b) IBKR cost/borrow gate on pending ARMED/BENCH: short with no IBKR borrow -> SHADOW 'no borrow'; ARMED breaking cost_R/room -> BENCH
    for t in T:
        if t["status"] in ("ARMED", "BENCH") and dt.datetime.fromisoformat(t["armed_utc"]) >= now:
            if no_borrow(t): t.update(status="SHADOW", shadow_reason="no borrow"); continue
            if t["status"] == "ARMED":
                why = cost_gate(t)
                if why: t.update(status="BENCH", demoted_utc=now.isoformat(), bench_note="IBKR cost gate: " + why)   # sticky demotion (2026-10-27 fix)
    active = {t["sym"] for t in T if t["status"] in ("ARMED", "FILLED", "BENCH") or t.get("shadow_reason") == "no borrow"}
    _af0 = (valid_from or now).isoformat()[:10]
    active |= {t["sym"] for t in T if str(t.get("armed_utc"))[:10] == _af0}   # fix 2: never re-record a name for the same session
    # 2) new candidates -> bench (ranked)
    cands = sorted([v for v in revs if v["passed"] and v["sym"] not in active], key=lambda v: -v["score"])
    _rk, _seen = {}, {}                  # sector leaders first so one hot sector cannot crowd the bench (1/sector rule)
    for v in cands: _seen[v["sector"]] = _seen.get(v["sector"], 0) + 1; _rk[v["sym"]] = _seen[v["sector"]]
    cands.sort(key=lambda v: (min(_rk[v["sym"]], 2), -v["score"]))
    for v in cands:
        if sum(t["status"] == "BENCH" for t in T) + len(live()) >= MAX_OPEN + HR.max_bench("asx", MAX_BENCH, os.path.join(base, "state")): break
        lv = levels(rows[v["sym"]], v["dir"])
        if not lv: continue
        af = valid_from or now
        T.append(dict(id=nid(), sym=v["sym"], yahoo=v["sym"], direction=v["dir"], status="BENCH", armed_utc=af.isoformat(), armed_wita=w(af), recorded_utc=now.isoformat(),
                      sector=v["sector"], shortable=v["shortable"], short_flag=(f"SHORT: shortable per {v['short_src']}" if v["dir"] == "short" else None),
                      score=v["score"], rank_score=v["rank_score"], trigger_anns=v["anns"], **({"catalyst": v["catalyst"]} if v.get("catalyst") else {}), q2usd=AUDUSD, ccy="AUD",
                      **{k: lv[k] for k in ("trigger", "max_fill", "stop", "t1", "t2", "shares", "risk_aud", "risk_aud_actual", "notional_aud", "est_cost_usd", "est_cost_aud", "cost_R", "rr_t1", "rr_t2", "breakeven_text", "cost_side_aud", "borrow_pa", "borrow_aud", "cost_model")}))
        if no_borrow(T[-1]): T[-1].update(status="SHADOW", shadow_reason="no borrow")
        else:
            rw = cost_gate(T[-1])                                                   # room_R written at bench entry (no nulls)
            if rw: T[-1]["bench_note"] = "room/cost gate: " + rw
    # 2b) desk clearance gate (3 desk reviews RISK/BEHAVIOR/SPECIALIST): pending ARMED that FAILED -> BENCH with reason (never deleted)
    for t in T:
        c = CL.get(t["sym"])
        if t["status"] == "ARMED" and dt.datetime.fromisoformat(t["armed_utc"]) > now and c and c.get("internal") == "FAIL":
            t.update(status="BENCH", demoted_utc=now.isoformat(), bench_note="desk clearance FAIL: " + "; ".join(c.get("why") or [f"R={c.get('R')} B={c.get('B')} S={c.get('S')}"])[:200])
        if t["status"] == "BENCH" and c and c.get("internal") == "FAIL" and not str(t.get("bench_note", "")).startswith("desk clearance FAIL"):
            t["bench_note"] = "desk clearance FAIL: " + "; ".join(c.get("why") or [f"R={c.get('R')} B={c.get('B')} S={c.get('S')}"])[:200]
        vr = VT["rows"].get(t["sym"]) or {}
        if t["status"] in ("ARMED", "BENCH") and vr: t.update(VV.ai_tags(t["sym"], VT))   # 2026-10-28 AI advisory: record ai_gpt / ai_claude
        if t["status"] in ("ARMED", "BENCH"):
            t["clearance"] = (dict(desk=c.get("internal"), RISK=c.get("R"), BEHAVIOR=c.get("B"), SPECIALIST=c.get("S"), GPT=vr.get("GPT") or "PENDING", CLAUDE=vr.get("CLAUDE") or "RETIRED", final=vr.get("final")) if c else dict(desk="PENDING"))
        vv = VV.veto(t["sym"], VV.for_armed("asx", now, base, VV.wday(dt.datetime.fromisoformat(t.get("promoted_utc") or t.get("recorded_utc") or t["armed_utc"])), VT)) if t["status"] == "ARMED" and dt.datetime.fromisoformat(t["armed_utc"]) > now else None   # veto hook: pending ARMED (not yet live) BLOCKED / GPT or CLAUDE FAIL / unverdicted / no fresh verdict -> BENCH, sticky
        if vv: t.update(status="BENCH", demoted_utc=now.isoformat(), bench_note=vv, verdict_veto=vv); continue
        if t["status"] == "ARMED" and c and c.get("internal") == "PASS" and valid_from and dt.datetime.fromisoformat(t["armed_utc"]) > valid_from:
            t.update(armed_utc=valid_from.isoformat(), armed_wita=w(valid_from))      # cleared -> today's open
    # 3) promote bench -> ARMED (ONLY names that passed all 3 desk reviews today) into free own slots (sector + direction gates against armed+open)
    for t in sorted([t for t in T if t["status"] == "BENCH"], key=lambda t: -t["score"]):
        if len(live()) >= MAX_OPEN: break
        # 2026-10-27 FIX (Brett): a BENCH that was ever demoted (cost gate / clearance FAIL / audit) or already promoted once is NEVER re-armed by the engine
        if t.get("demoted_utc") or t.get("audit_bench_reason") or t.get("promoted_utc"):
            t.setdefault("bench_note", "demoted - never re-armed (needs a fresh record)"); continue
        if t["sym"] in HARD_DISARMED: t["bench_note"] = "ARB.AX stays disarmed (Brett) - never armed by the engine"; continue
        c = CL.get(t["sym"])
        if not c or c.get("internal") != "PASS": t["bench_note"] = t.get("bench_note") if c else "awaiting desk clearance"; continue
        vv = VV.veto(t["sym"], VT)                                           # GPT veto hook (fail closed): never arm
        if vv: t["bench_note"] = vv; continue
        why = mix_ok(t, live())
        if why: t["bench_note"] = why; continue
        why = ("no borrow" if no_borrow(t) else None) or cost_gate(t)
        if why: t["bench_note"] = "IBKR cost gate: " + why; continue
        if t.get("room_R") is None: t["bench_note"] = "room_R null -> BENCH (never fail open)"; continue
        if t["sym"] in {x["sym"] for x in live()}: t["bench_note"] = "already open/armed in book -> never re-armed"; continue
        if not valid_from:   # 2026-10-28 09:59 WITA Brett: in-session arms only via intraday_rescan (fresh clearance) + the four open-time checks
            if not intraday_ok(now, base): t["bench_note"] = "in session: arms only at an intraday rescan (fresh clearance, >=30 min before close)"; continue
            import intraday_rescan as IR
            ok4, why4 = IR.open_time_checks("asx", t, now)
            if not ok4: t["bench_note"] = "intraday open-time checks: " + "; ".join(why4)[:200]; continue
            t.update(armed_utc=now.isoformat(), armed_wita=w(now), intraday_rescan=w(now))
        if valid_from and dt.datetime.fromisoformat(t["armed_utc"]) > valid_from: t.update(armed_utc=valid_from.isoformat(), armed_wita=w(valid_from))
        _why = _firm_block(t)
        if _why: t["bench_note"] = "firm_risk:" + _why; continue
        t["status"] = "ARMED"; t["promoted_utc"] = now.isoformat(); t.pop("bench_note", None)
    # 3b) bench capped at MAX_BENCH (lowest score beyond 15 -> DISARMED 'bench full'; kept in ledger, never deleted)
    for t in sorted([t for t in T if t["status"] == "BENCH"], key=lambda t: -t["score"])[HR.max_bench("asx", MAX_BENCH, os.path.join(base, "state")):]:
        t.update(status="DISARMED", note=f"bench full (>{HR.max_bench('asx', MAX_BENCH, os.path.join(base, 'state'))})")
    # 3c) 2026-10-28 Brett 10:37 FUNDER GATE (see funder_gate.py)
    import funder_gate as FG
    _exp = lambda t, n: n > session_close(dt.datetime.fromisoformat(t["armed_utc"]).astimezone(SYD).date()) + dt.timedelta(minutes=90)
    def _lp(t):
        b = fetch(t.get("yahoo") or t["sym"], now - dt.timedelta(days=4)); return b[-1]["c"] if b else None
    for sy, a in FG.gate_tick("asx", L, now, _lp, _exp, base=base): print("FUNDER", sy, a)
    # 4) step fills/exits (own slots only); event/prior-close cut
    slots = MAX_OPEN - sum(t["status"] == "FILLED" for t in T)
    if in_hours(now) or force:
        for t in [t for t in T if t["status"] in ("ARMED", "FILLED")]:
            if dt.datetime.fromisoformat(t["armed_utc"]) > now: continue
            if t["status"] == "ARMED":
                _why = _firm_block(t)
                if _why: t["firm_risk_block"] = _why; continue
            try:
                bars = fetch(t["yahoo"], dt.datetime.fromisoformat(t["armed_utc"]))
                if t["status"] == "ARMED": bars = [b for b in bars if b["t"] <= session_close(dt.datetime.fromisoformat(t["armed_utc"]).astimezone(SYD).date()) + dt.timedelta(minutes=65)]   # day arm (2026-10-27 audit, = asia_hunt48)
                was = t["status"] == "FILLED"
                if was: cut, why = event_cut(t["sym"], earn, dt.datetime.fromisoformat(t["fill_utc"])); bars = [b for b in bars if b["t"] <= cut]
                slots = CB.step(t, bars, now, slots + (1 if was else 0), rule=CB.hunt_fill_rule()["rule"], giveback=True, engine="hunt48_asx", half_1r=True) - (1 if was and t["status"] == "FILLED" else 0)
                if t["status"] == "FILLED":
                    cut, why = event_cut(t["sym"], earn, dt.datetime.fromisoformat(t["fill_utc"])); t["exit_cut_utc"] = cut.isoformat(); t["exit_cut_wita"] = w(cut); t["exit_cut_why"] = why
                    if now >= cut:
                        px = t.get("mark", t["fill"]); sg = 1 if t["direction"] == "long" else -1
                        t["realised_usd"] += sg * (px - t["fill"]) * t["qty_open"] * t["q2usd"]; t["qty_open"] = 0; t["status"] = "CLOSED"; t["exit"] = "PRIOR_CLOSE_EVENT" if why.startswith("event") else "TIME"
                        t["net_usd"] = round(t["realised_usd"] - t["est_cost_usd"], 2); t["net_aud"] = round(t["net_usd"] / AUDUSD, 2)
                if t["status"] == "SHADOW": t["note"] = "own 3 slots full"
            except Exception as e: t["last_error"] = str(e)[:120]
    # 4b) day-arm expiry (2026-10-27 audit; same law as asia_hunt48 step 0, "arming lasts one session"). Runs AFTER the fill step so a
    #     catch-up tick still replays the arm session's bars first: ARMED still unfilled 90 min after the close of the session it was
    #     armed for -> EXPIRED (kept, never deleted; the name needs a fresh record + clearance to arm again).
    for t in T:
        if t["status"] == "ARMED" and now > session_close(dt.datetime.fromisoformat(t["armed_utc"]).astimezone(SYD).date()) + dt.timedelta(minutes=90):
            t.update(status="EXPIRED", expired_utc=now.isoformat(), note=f"day arm: armed {t.get('armed_wita')} for that session only, unfilled")
    L.update(updated_utc=now.isoformat(), capital_aud=CAPITAL_AUD); os.makedirs(SD, exist_ok=True); CB.atomic(LEDGER, L)
    return write_status(L, now, reg, revs, base)

def _cl_summary(now, base=BASE):
    c = clearance(now, base); f = lambda k: sorted(s for s, v in c.items() if v.get("internal") == k); vs = VV.summary(VV.load("asx", now, base))
    return dict(date_wita=now.astimezone(WITA).strftime("%Y%m%d"), reviewed=len(c), desk_pass=f("PASS"), desk_fail=f("FAIL"), gpt_fail=vs.get("gpt_fail", []), claude_fail=vs.get("claude_fail", []),
                final_blocked=vs.get("blocked", []), cleared=vs.get("cleared", []), verdict_veto=vs,
                rule="arm only after RISK+BEHAVIOR+SPECIALIST PASS AND finalize CLEARED with no GPT FAIL (Claude retired 2026-10-29) (hunt_clearance.py run/finalize --book asx); verdict files missing/stale -> stay out")
def write_status(L, now, reg, revs, base=BASE):
    T = L["trades"]; closed = [t for t in T if t["status"] == "CLOSED"]
    net_closed = round(sum(t.get("net_aud", 0) for t in closed), 2)
    open_aud = round(sum((1 if t["direction"] == "long" else -1) * (t.get("mark", t["fill"]) - t["fill"]) * t["qty_open"] for t in T if t["status"] == "FILLED"), 2)
    try: import causal_guard; cv = causal_guard.audit(T, now)
    except Exception: cv = []
    st = dict(book="HUNT48_ASX", paper_only=True, ccy="AUD", capital_aud=CAPITAL_AUD, equity_aud=round(CAPITAL_AUD + net_closed + open_aud, 2),
              net_aud_closed=net_closed, open_pnl_aud=open_aud, risk_per_trade_aud=RISK_AUD, max_open=MAX_OPEN, max_bench=HR.max_bench("asx", MAX_BENCH, os.path.join(base, "state")),
              updated_utc=now.isoformat(), updated_wita=w(now), asx_hours_wita="07:00-13:00 WITA (10:00-16:00 AEDT)", in_hours=in_hours(now), regime_axjo=reg,
              counts={k: sum(t["status"] == k for t in T) for k in ("ARMED", "awaiting_funder", "BENCH", "FILLED", "CLOSED", "SHADOW", "MISSED", "EXPIRED", "DISARMED")},
              slots_used=sum(t["status"] in ("ARMED", "FILLED") for t in T), reviewed=len(revs), passed=sum(v["passed"] for v in revs),
              data_fail=dict(reviews=sum(bool(v["why"]) and all(HR.is_data_fail(x) for x in v["why"]) for v in revs), bench_waiting=sorted(t["sym"] for t in T if t["status"] == "BENCH" and t.get("data_fail"))),
              causal_violations_count=len(cv), rules="separate A$1m book; own 3 slots; 1/sector; direction balance; earnings block; price-sensitive anns trigger; costs IBKR 0.08% min A$6/side+10bp+half tick+IBKR borrow; cost_R<=0.15 & room_R=min(structure 1y,T2)-cost>=1R (fail closed); B1 shorts RSI<30/exhausted 20d low blocked; no borrow->SHADOW; shorts only shortable; exit prior close if event before open",
              liquidity_floor_aud=2e6, liquidity_floor_label="ASX floor A$2m 20d ADV (US stays A$5m)", position_cap_rule="notional <= min(A$333k, 2% ADV if ADV<A$5m else 5% ADV)",
              clearance=_cl_summary(now, base),
              armed=[t for t in T if t["status"] in ("ARMED", "FILLED")], awaiting_funder=[t for t in T if t["status"] == "awaiting_funder"], bench=sorted([t for t in T if t["status"] == "BENCH"], key=lambda t: -t["score"]),
              recent=[t for t in T if t["status"] not in ("ARMED", "FILLED", "BENCH")][-15:])
    _, _, STATUS = paths(base); os.makedirs(os.path.dirname(STATUS), exist_ok=True); CB.atomic(STATUS, st)
    # 2026-10-29 TFO 11:12 WITA fix 1: ASX health refreshed every tick (was a one-off snapshot dated 28 Oct 15:10; asx_hunt48 never wrote it). Never arms.
    try:
        _sd, _, _ = paths(base); _c = st["counts"]
        health = dict(book="hunt48_asx", market="asx", paper_only=True, ok=True, counts=_c, reviewed=st["reviewed"], passed=st["passed"], regime=reg,
                      bench_n=_c.get("BENCH", 0), bench=_c.get("BENCH", 0), armed=_c.get("ARMED", 0), filled=_c.get("FILLED", 0), awaiting_funder=_c.get("awaiting_funder", 0),
                      updated_utc=st["updated_utc"], updated_wita=st["updated_wita"], bars_asof=HR.bars_asof_iso(),
                      src="static/hunt48_asx_status.json", note="health snapshot for always-on watchdog (written every ASX tick)")
        CB.atomic(os.path.join(_sd, "health.json"), health)
        CB.atomic(os.path.join(base, "static", "hunt48_asx_health.json"), health)
    except Exception:
        pass
    return st

if __name__ == "__main__":
    s = run(force="--force" in sys.argv)
    print(json.dumps(s["counts"]), "| regime", s["regime_axjo"], "| reviewed", s["reviewed"], "passed", s["passed"])
    for t in s["armed"]: print("ARMED", t["sym"], t["direction"], t["sector"], "trig", t["trigger"], "max", t["max_fill"], "stop", t["stop"], "T1", t["t1"], "T2", t["t2"], t["shares"], "sh A$", t["risk_aud_actual"], "from", t["armed_wita"])
    for t in s["bench"]: print("BENCH", t["sym"], t["direction"], t["sector"], t["score"], t.get("bench_note", ""))
