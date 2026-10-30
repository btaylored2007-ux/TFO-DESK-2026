#!/usr/bin/env python3
"""HUNT48 ASIA BOOKS (PAPER ONLY) - Brett 2026-10-27 (pt1481u) "Do exactly the same setup for the Asian markets" as HUNT48_ASX.
One SEPARATE A$1,000,000 paper book per market (jp hk sg kr tw): ledger state/hunt48_<mkt>/ledger.json, status static/hunt48_<mkt>_status.json,
own MAX 3 open/armed slots, own bench <=15. Same rules as asx_hunt48: event_calendar.gate (ATR<=6%, RSI, no pump/crash chase), A$2m-equivalent
20d ADV floor (universe_asia.py), regime filter (local index vs 50dMA), rank_score>=3, earnings in hold -> BLOCK, ONE per sector, direction balance,
A$2,500 risk/trade (converted to local ccy), notional cap min(A$333k, 2% ADV if ADV<A$5m else 5% ADV), stop on every trade, IBKR real costs
(asia_markets.side_cost incl. HK stamp both sides, KR 0.2% / TW 0.3% sell tax) + 10bp slip/side + half tick/side + IBKR borrow 3d for shorts;
cost_R>0.15 or room_R=min(structure 1y,T2)-cost<1R -> BENCH (fail closed, Rules Auditor 27 Oct 08:40); B1 shorts RSI<30/missing or exhausted true 20d low blocked. Shorts only jp/hk with confirmed borrow (hk also designated list); sg/kr/tw LONG-ONLY.
ARMING LAW (fixes the ASX re-promotion bug by construction):
  * new names enter BENCH only; BENCH -> ARMED only via a SAME-DAY (WITA) hunt_clearance.py --book <mkt> PASS, BEFORE today's local open,
    using the cleared levels; ARMED is a DAY arm (unfilled at session close -> EXPIRED, needs fresh clearance next day).
  * any ARMED that fails anything (re-review, cost/room, clearance, live pre-open check) -> BENCH with demoted_utc and is NEVER re-armed.
  * pre-open (open-35min..open): live price re-check; px through stop or beyond max_fill, or levels drifted >0.5 ATR -> BENCH (demoted).
  * clearance missing at open -> book stays flat today.
Fills simulated on Yahoo 1h bars via cal_paper_bench.step(). No broker, no orders, no keys."""
import csv, datetime as dt, json, os, sys
from zoneinfo import ZoneInfo
import cal_paper_bench as CB, event_calendar as EC, asia_markets as AM
import hunt48_room as HR   # 2026-10-27 Rules Auditor 08:40: single room definition + B1 short screen (fail closed)
COST_R_MAX = 0.15; MIN_ROOM_R = HR.MIN_ROOM_R; HOLD_DAYS = 3; SLIP = 0.001
CAPITAL_AUD = 1_000_000.0; RISK_AUD = 2500.0; MAX_OPEN = 3; MAX_BENCH = 15; MIN_SCORE = 3.0
MAX_NOTIONAL_AUD = CAPITAL_AUD / 3; MAX_PCT_ADV = 5.0; THIN_ADV_AUD = 5e6; THIN_PCT_ADV = 2.0; AUDUSD = CB.AUDUSD
PREOPEN_MIN = 35; DRIFT_ATR = 0.5
UTC = dt.timezone.utc; WITA = ZoneInfo("Asia/Makassar")
BASE = os.environ.get("DESK_BASE", os.path.dirname(os.path.abspath(__file__)))
LEVEL_KEYS = ("trigger", "max_fill", "stop", "t1", "t2", "shares", "risk_aud", "risk_aud_actual", "risk_local", "notional_local", "notional_aud", "est_cost_local",
              "est_cost_aud", "est_cost_usd", "cost_R", "cost_buy_local", "cost_sell_local", "borrow_pa", "borrow_local", "cost_model", "cap_aud", "cap_rule", "lot", "atr")
def paths(mkt, base=BASE):
    sd = os.path.join(base, "state", f"hunt48_{mkt}")
    return sd, os.path.join(sd, "ledger.json"), os.path.join(base, "static", f"hunt48_{mkt}_status.json")
def tz(mkt): return ZoneInfo(AM.MARKETS[mkt]["tz"])
def trading_day(mkt, d): return d.weekday() < 5 and d not in AM.MARKETS[mkt]["holidays"]
def _t(mkt, d, hm): h, m = map(int, hm.split(":")); return dt.datetime.combine(d, dt.time(h, m), tz(mkt)).astimezone(UTC)
def session_open(mkt, d): return _t(mkt, d, AM.MARKETS[mkt]["sessions"][0][0])
def session_close(mkt, d): return _t(mkt, d, AM.MARKETS[mkt]["sessions"][-1][1])
def next_td(mkt, d):
    d += dt.timedelta(days=1)
    while not trading_day(mkt, d): d += dt.timedelta(days=1)
    return d
def prev_td(mkt, d):
    d -= dt.timedelta(days=1)
    while not trading_day(mkt, d): d -= dt.timedelta(days=1)
    return d
def local_day(mkt, now): return now.astimezone(tz(mkt)).date()
def in_hours(mkt, now, pad=20):
    d = local_day(mkt, now)
    return trading_day(mkt, d) and session_open(mkt, d) - dt.timedelta(minutes=pad) <= now <= session_close(mkt, d) + dt.timedelta(minutes=pad)
def pre_open(mkt, now):
    d = local_day(mkt, now); o = session_open(mkt, d)
    return trading_day(mkt, d) and o - dt.timedelta(minutes=PREOPEN_MIN) <= now < o
def before_open_today(mkt, now):
    d = local_day(mkt, now); return trading_day(mkt, d) and now < session_open(mkt, d)
def w(t): return t.astimezone(WITA).strftime("%a %d %b %H:%M WITA") if t else None
def wday(now): return now.astimezone(WITA).strftime("%Y%m%d")
# ---------------- data
def load_rows(mkt, base=BASE):
    p = os.path.join(base, "state", "calendar", "inputs", f"ranked_{mkt}.csv")
    return {r["sym"]: r for r in csv.DictReader(open(p))} if os.path.exists(p) else {}
def load_events(mkt, base=BASE):
    j = CB.load(os.path.join(base, "state", "calendar", "inputs", f"{mkt}_events.json"), {}); earn = {}
    for c, d in (j.get("earnings") or {}).items():
        try: earn[c] = dt.date.fromisoformat(d)
        except Exception: pass
    return earn, set(j.get("halted") or [])
def fx_aud(mkt, rows):
    """local -> AUD from the universe run (Yahoo <CCY>AUD=X)."""
    for r in rows.values():
        v = EC.fnum(r.get("fx_aud"))
        if v: return v
    return None
def regime(mkt, rows):
    ix = rows.get(AM.MARKETS[mkt]["idx"])
    if not ix: return "unknown"
    return "long" if EC.fnum(ix.get("px"), 0) > EC.fnum(ix.get("m50"), 0) else "short"
def borrow_ok(mkt, t_or_row, shares):
    """CONFIRMED borrow only (stricter than ASX fallback): shortable flag from universe (designated list for HK) and IBKR file available >= shares."""
    if not AM.MARKETS[mkt]["shorts"]: return False
    av = EC.fnum(t_or_row.get("borrow_avail"), 0) or 0
    return str(t_or_row.get("shortable")) == "True" and av >= abs(shares or 0)
def live_px(sym):
    try:
        import yfinance as yf
        v = yf.Ticker(sym).fast_info["last_price"]; return float(v) if v and v == v else None
    except Exception: return None
def _daily(sym): return HR.daily(sym)      # 1y daily bars (was 3mo); [] -> BENCH (fail closed)
def opposing_levels(daily, long=False):
    d = daily[:-1] if len(daily) > 5 else daily; lv = []
    for i in range(2, len(d) - 2):
        if not long and d[i]["l"] <= min(d[j]["l"] for j in (i-2, i-1, i+1, i+2)): lv.append(d[i]["l"])
        if long and d[i]["h"] >= max(d[j]["h"] for j in (i-2, i-1, i+1, i+2)): lv.append(d[i]["h"])
    for n in (20, 50):
        if len(d) >= n: lv.append(sum(x["c"] for x in d[-n:]) / n)
    return lv
def cost_gate(t, daily=None):
    """None if ok; else reason. cost_R <= 0.15R AND hunt48_room.room(): room_R = min(structure, T2) - cost_R >= 1R (fail closed; room_R always numeric)."""
    daily = _daily(t.get("yahoo") or t["sym"]) if daily is None else daily
    why = HR.room(t, daily)
    if (t.get("cost_R") or 0) > COST_R_MAX: return f"IBKR cost {t['cost_R']:.3f}R > {COST_R_MAX}R"
    if t.get("room_R") is None: return "room_R null -> BENCH (never fail open)"
    return why
# ---------------- review + levels
def review(mkt, rows, earn, halted, now, hold_end):
    reg = regime(mkt, rows); out = []; M = AM.MARKETS[mkt]
    for s, r in rows.items():
        if r.get("cls") != f"{mkt}_equity": continue
        d = r.get("dir") if r.get("dir") in ("long", "short") else "long"
        score = EC.fnum(r.get("rank_score"), 0) or 0; why = EC.gate(r, d)
        if d == "short": why += HR.rsi_block(r)                     # B1: RSI<30 or missing -> blocked (Rules Auditor 08:40)
        if (EC.fnum(r.get("adv_aud"), 0) or 0) < 2e6: why.append("ADV < A$2m equivalent")
        if d == "short" and not M["shorts"]: why.append("LONG-ONLY market: no IBKR short selling")
        if d == "short" and str(r.get("shortable")) != "True": why.append("not shortable (" + str(r.get("short_src"))[:80] + ")")
        if s in halted: why.append("trading halt")
        if reg in ("long", "short") and reg != d: why.append(f"regime filter ({M['idx']}): {reg}-only")
        if score < MIN_SCORE: why.append(f"rank_score {score:.2f}<3.0")
        if d == "short" and not why: why += HR.exhaustion_block(r, _daily(s))   # B1: true 20d low + 5d drop >1.5 ATR (fail closed)
        e = earn.get(s)
        if e and local_day(mkt, now) - dt.timedelta(days=1) <= e <= local_day(mkt, hold_end): why.append(f"BLOCK: earnings {e} inside hold window")
        out.append(dict(sym=s, dir=d, rank_score=round(score, 2), score=round(score, 2), sector=r.get("sector") or s, passed=not why, why=why))
    return reg, out
def levels(mkt, r, d, px=None):
    """stage levels in LOCAL ccy (EC.stage_levels with quote=1), lot-rounded, A$ notional cap, IBKR costs. px overrides scan px (live re-stage)."""
    fx = EC.fnum(r.get("fx_aud")); M = AM.MARKETS[mkt]
    if not fx: return None
    rr = dict(r)
    if px: rr["px"] = px; rr["pdh"] = max(EC.fnum(r.get("pdh"), px), px); rr["pdl"] = min(EC.fnum(r.get("pdl"), px), px)
    risk_local = RISK_AUD / fx
    lv = EC.stage_levels(dict(rr, venue=mkt), d, risk_local, 1.0, t2_r=HR.T2_R)   # 2026-10-27 pt1486u: hunt48 T2 = 1.5R
    if not lv or not lv["shares"]: return None
    lot = int(EC.fnum(r.get("lot"), M["lot"] or 1) or 1)
    adv_aud = EC.fnum(r.get("adv_aud"), 0) or 1; pct = THIN_PCT_ADV if adv_aud < THIN_ADV_AUD else MAX_PCT_ADV
    cap_aud = min(MAX_NOTIONAL_AUD, adv_aud * pct / 100); sh = int(lv["shares"])
    if sh * lv["max_fill"] * fx > cap_aud: sh = int(cap_aud / (lv["max_fill"] * fx))
    sh = (sh // lot) * lot
    if sh <= 0: return None
    mf, st = lv["max_fill"], lv["stop"]; n_loc = sh * mf
    buy = AM.side_cost(mkt, sh, mf, "buy"); sell = AM.side_cost(mkt, sh, mf, "sell"); tk = AM.tick(mkt, mf)
    bpa = (EC.fnum(r.get("borrow_pa")) or 0.0) if d == "short" else 0.0; borrow = n_loc * bpa * HOLD_DAYS / 365
    cost = buy + sell + 2 * SLIP * n_loc + tk * sh + borrow; risk = sh * abs(mf - st) or 1
    lv.update(shares=sh, lot=lot, risk_local=round(risk_local, 2), risk_aud=RISK_AUD, risk_aud_actual=round(risk * fx, 2), notional_local=round(n_loc), notional_aud=round(n_loc * fx),
              cap_aud=round(cap_aud), cap_rule=f"min(A$333k, {pct:g}% of 20d ADV A${adv_aud/1e6:.1f}m), lot {lot}", cost_buy_local=round(buy, 2), cost_sell_local=round(sell, 2),
              borrow_pa=round(bpa, 5), borrow_local=round(borrow, 2), est_cost_local=round(cost, 2), est_cost_aud=round(cost * fx, 2), est_cost_usd=round(cost * fx * AUDUSD, 2),
              cost_R=round(cost / risk, 3), cost_model=f"{M['cost_src']} + 10bp slip/side + half tick/side" + (" + IBKR borrow 3d" if d == "short" else ""))
    return lv
def mix_ok(c, slots):
    for o in slots:
        if o["sector"] == c["sector"]: return f"sector cap: {o['sym']} holds {c['sector']}"
    same = sum(o["direction"] == c["direction"] for o in slots)
    if len(slots) >= MAX_OPEN - 1 and same >= MAX_OPEN - 1: return f"direction balance: {same} {c['direction']}s; last slot opposite only"
    return None
def clearance(mkt, now, base=BASE):
    """{sym: {internal, R, B, S, why, setup}} from TODAY's (WITA) hunt_clearance.py run --book <mkt>; missing -> {} (nothing arms)."""
    d = os.path.join(base, "state", "clearance", wday(now), mkt)
    try: S = json.load(open(os.path.join(d, "summary.json")))["books"].get(mkt, {})
    except Exception: return {}
    try: IV = json.load(open(os.path.join(d, "internal_verdicts.json")))["books"][mkt]["rows"]
    except Exception: IV = {}
    for s, v in S.items(): v["setup"] = (IV.get(s) or {}).get("setup")
    return S
def demote(t, now, why):
    t.update(status="BENCH", demoted_utc=now.isoformat(), bench_note=("DEMOTED (never re-armed): " + why)[:240])
# ---------------- engine
def run(mkt, now=None, fetch=CB.bars_yf, base=BASE, force=False, live=live_px):
    now = now or CB.now_utc(); M = AM.MARKETS[mkt]; SD, LEDGER, STATUS = paths(mkt, base)
    rows = load_rows(mkt, base); earn, halted = load_events(mkt, base); fx = fx_aud(mkt, rows)
    L = CB.load(LEDGER, dict(book=f"HUNT48_{mkt.upper()}", market=M["name"], paper_only=True, capital_aud=CAPITAL_AUD, ccy=M["ccy"], trades=[]))
    T = L["trades"]; nid = lambda: max([t["id"] for t in T] or [0]) + 1
    today = local_day(mkt, now); open_today = session_open(mkt, today) if trading_day(mkt, today) else None
    nxt_open = open_today if (open_today and now < open_today) else session_open(mkt, next_td(mkt, today))
    hold_end = nxt_open + dt.timedelta(hours=72); CL = clearance(mkt, now, base)
    reg, revs = review(mkt, rows, earn, halted, now, hold_end); R = {v["sym"]: v for v in revs}
    pending = lambda t: t["status"] in ("ARMED", "BENCH") and dt.datetime.fromisoformat(t["armed_utc"]) >= now
    # 0) day-arm expiry: ARMED unfilled whose clearance day != today -> EXPIRED; demoted BENCH from an earlier day -> DISARMED (name may re-enter fresh)
    for t in T:
        if t["status"] == "ARMED" and now > session_close(mkt, local_day(mkt, dt.datetime.fromisoformat(t["armed_utc"]))) + dt.timedelta(minutes=90):
            t.update(status="EXPIRED", note=f"day arm: cleared {t.get('cleared_wita')} for that session only, unfilled")
        if t["status"] == "BENCH" and t.get("demoted_utc") and dt.datetime.fromisoformat(t["demoted_utc"]).astimezone(WITA).strftime("%Y%m%d") != wday(now):
            t.update(status="DISARMED", note="demoted earlier day; " + str(t.get("bench_note", ""))[:160])
        if t["status"] == "BENCH" and dt.datetime.fromisoformat(t["armed_utc"]) < now: t.update(armed_utc=nxt_open.isoformat(), armed_wita=w(nxt_open))   # bench rolls to next open
    # 1) pending re-review; BENCH re-staged from scan (or live pre-open); ARMED keeps CLEARED levels, failures -> demoted BENCH
    for t in T:
        if not pending(t): continue
        v = R.get(t["sym"])
        if not v or not v["passed"] or v["dir"] != t["direction"]:
            why = "re-review failed: " + "; ".join((v or {}).get("why", ["not in universe"]))
            if t["status"] == "ARMED": demote(t, now, why)
            else: t.update(status="DISARMED", note=why[:200])
            continue
        lp = live(t["yahoo"]) if pre_open(mkt, now) else None
        if lp: t["live_px"] = lp; t["live_utc"] = now.isoformat()
        if t["status"] == "BENCH":
            lv = levels(mkt, rows[t["sym"]], t["direction"], lp)
            if lv: t.update({k: lv[k] for k in LEVEL_KEYS + ("rr_t1", "rr_t2") if k in lv}, restaged_utc=now.isoformat(), restaged_from=("live " + str(lp)) if lp else "scan")
            rw = cost_gate(t)                                                       # room_R always written (no nulls)
            if rw and not t.get("demoted_utc"): t["bench_note"] = "room/cost gate: " + rw
        elif lp:      # ARMED, pre-open live check vs CLEARED levels
            lg = t["direction"] == "long"
            if (lg and lp <= t["stop"]) or (not lg and lp >= t["stop"]): demote(t, now, f"pre-open live {lp:g} through stop {t['stop']:g}"); continue
            if (lg and lp > t["max_fill"]) or (not lg and lp < t["max_fill"]): demote(t, now, f"pre-open live {lp:g} beyond max_fill {t['max_fill']:g} (would chase)"); continue
            nl = levels(mkt, rows[t["sym"]], t["direction"], lp)
            if nl and abs(nl["trigger"] - t["trigger"]) > DRIFT_ATR * (t.get("atr") or nl["atr"]): demote(t, now, f"live re-stage moved trigger {t['trigger']:g}->{nl['trigger']:g} (>{DRIFT_ATR} ATR): needs fresh clearance"); continue
            t["preopen_check"] = f"OK live {lp:g} at {w(now)}"
    # 1b) borrow/cost gates on pending
    for t in T:
        if not pending(t): continue
        if t["direction"] == "short" and not borrow_ok(mkt, rows.get(t["sym"], t), t["shares"]): t.update(status="SHADOW", shadow_reason="no confirmed borrow"); continue
        if t["status"] == "ARMED":
            why = cost_gate(t)
            if why: demote(t, now, "IBKR cost gate: " + why)
    active = {t["sym"] for t in T if t["status"] in ("ARMED", "FILLED", "BENCH")}
    # 2) new candidates -> BENCH only
    cands = sorted([v for v in revs if v["passed"] and v["sym"] not in active], key=lambda v: -v["score"])
    _rk, _seen = {}, {}
    for v in cands: _seen[v["sector"]] = _seen.get(v["sector"], 0) + 1; _rk[v["sym"]] = _seen[v["sector"]]
    cands.sort(key=lambda v: (min(_rk[v["sym"]], 2), -v["score"]))
    live_ = lambda: [t for t in T if t["status"] in ("ARMED", "FILLED")]
    for v in cands:
        if sum(t["status"] == "BENCH" for t in T) >= MAX_BENCH: break
        r = rows[v["sym"]]; lv = levels(mkt, r, v["dir"])
        if not lv: continue
        T.append(dict(id=nid(), sym=v["sym"], yahoo=v["sym"], market=mkt, direction=v["dir"], status="BENCH", armed_utc=nxt_open.isoformat(), armed_wita=w(nxt_open),
                      recorded_utc=now.isoformat(), sector=v["sector"], name=r.get("name"), shortable=r.get("shortable"), short_src=r.get("short_src"), borrow_avail=r.get("borrow_avail"),
                      score=v["score"], rank_score=v["rank_score"], ccy=M["ccy"], fx_aud=fx, q2usd=round((fx or 0) * AUDUSD, 8), adv_aud=EC.fnum(r.get("adv_aud")),
                      **{k: lv[k] for k in LEVEL_KEYS + ("rr_t1", "rr_t2", "breakeven_text") if k in lv}))
        if v["dir"] == "short" and not borrow_ok(mkt, r, lv["shares"]): T[-1].update(status="SHADOW", shadow_reason="no confirmed borrow")
        else:
            rw = cost_gate(T[-1])                                                   # room_R written at bench entry (no nulls)
            if rw: T[-1]["bench_note"] = "room/cost gate: " + rw
    # 2b) clearance annotations; ARMED that FAILED today's clearance -> demoted BENCH
    for t in T:
        c = CL.get(t["sym"])
        if t["status"] in ("ARMED", "BENCH"):
            t["clearance"] = (dict(date=wday(now), desk=c.get("internal"), RISK=c.get("R"), BEHAVIOR=c.get("B"), SPECIALIST=c.get("S"), GPT="PENDING", CLAUDE="RETIRED") if c else dict(desk="PENDING", date=wday(now)))
        if pending(t) and t["status"] == "ARMED" and c and c.get("internal") == "FAIL": demote(t, now, "desk clearance FAIL: " + "; ".join(c.get("why") or [])[:200])
    # 3) promotion: ONLY never-armed, never-demoted BENCH, same-day clearance PASS, BEFORE today's open, cleared levels; gates re-checked
    can_arm = before_open_today(mkt, now)
    for t in sorted([t for t in T if t["status"] == "BENCH"], key=lambda t: -t["score"]):
        if t.get("demoted_utc") or t.get("promoted_utc"): continue          # NEVER re-arm (ASX bug)
        c = CL.get(t["sym"])
        if not c or c.get("internal") != "PASS" or not c.get("setup"): t["bench_note"] = "awaiting same-day desk clearance" if not c else ("desk clearance " + str(c.get("internal"))); continue
        if not can_arm: t["bench_note"] = "cleared but market already open/closed today -> flat today (fresh clearance before next open)"; continue
        if len(live_()) >= MAX_OPEN: t["bench_note"] = "own 3 slots full"; continue
        why = mix_ok(t, live_())
        if why: t["bench_note"] = why; continue
        s = c["setup"]; cand = dict(t, **{k: s[k] for k in ("trigger", "max_fill", "stop", "t1", "t2", "shares") if s.get(k) is not None})
        lv = levels(mkt, rows.get(t["sym"], {}), t["direction"])
        if lv and abs(lv["trigger"] - cand["trigger"]) > DRIFT_ATR * lv["atr"]: t["bench_note"] = "levels drifted since clearance -> needs fresh clearance"; continue
        if t["direction"] == "short" and not borrow_ok(mkt, rows.get(t["sym"], t), cand["shares"]): t["bench_note"] = "no confirmed borrow"; continue
        why = cost_gate(cand)
        if why: t["bench_note"] = "IBKR cost gate: " + why; t["room_R"] = cand.get("room_R"); t["room_basis"] = cand.get("room_basis"); continue
        if cand.get("room_R") is None: t["bench_note"] = "room_R null -> BENCH (never fail open)"; continue
        lp = live(t["yahoo"]); lg = t["direction"] == "long"
        if lp and ((lg and (lp <= cand["stop"] or lp > cand["max_fill"])) or (not lg and (lp >= cand["stop"] or lp < cand["max_fill"]))):
            t["bench_note"] = f"live {lp:g} outside stop/max_fill of cleared setup"; continue
        t.update({k: cand[k] for k in ("trigger", "max_fill", "stop", "t1", "t2", "shares")}, status="ARMED", promoted_utc=now.isoformat(), cleared_wita=wday(now),
                 armed_utc=open_today.isoformat(), armed_wita=w(open_today), live_px=lp, room_R=cand.get("room_R"), room_basis=cand.get("room_basis"))
        t.pop("bench_note", None)
    for t in sorted([t for t in T if t["status"] == "BENCH"], key=lambda t: -t["score"])[MAX_BENCH:]: t.update(status="DISARMED", note=f"bench full (>{MAX_BENCH})")
    # 4) fills/exits on own slots
    slots = MAX_OPEN - sum(t["status"] == "FILLED" for t in T)
    if in_hours(mkt, now, 75) or force:
        for t in [t for t in T if t["status"] in ("ARMED", "FILLED")]:
            if dt.datetime.fromisoformat(t["armed_utc"]) > now: continue
            try:
                bars = fetch(t["yahoo"], dt.datetime.fromisoformat(t["armed_utc"])); was = t["status"] == "FILLED"
                if t["status"] == "ARMED": bars = [b for b in bars if b["t"] <= session_close(mkt, local_day(mkt, dt.datetime.fromisoformat(t["armed_utc"]))) + dt.timedelta(minutes=65)]   # day arm
                if was: cut, why = event_cut(mkt, t["sym"], earn, dt.datetime.fromisoformat(t["fill_utc"])); bars = [b for b in bars if b["t"] <= cut]
                slots = CB.step(t, bars, now, slots + (1 if was else 0)) - (1 if was and t["status"] == "FILLED" else 0)
                if t["status"] == "FILLED":
                    cut, why = event_cut(mkt, t["sym"], earn, dt.datetime.fromisoformat(t["fill_utc"])); t["exit_cut_utc"] = cut.isoformat(); t["exit_cut_wita"] = w(cut); t["exit_cut_why"] = why
                    if now >= cut:
                        px = t.get("mark", t["fill"]); sg = 1 if t["direction"] == "long" else -1
                        t["realised_usd"] += sg * (px - t["fill"]) * t["qty_open"] * t["q2usd"]; t["qty_open"] = 0; t["status"] = "CLOSED"; t["exit"] = "PRIOR_CLOSE_EVENT" if why.startswith("event") else "TIME"
                if t["status"] == "CLOSED" and "net_aud" not in t:
                    t["net_usd"] = round(t["realised_usd"] - t["est_cost_usd"], 2); t["net_aud"] = round(t["net_usd"] / AUDUSD, 2)
            except Exception as e: t["last_error"] = str(e)[:120]
    L.update(updated_utc=now.isoformat(), capital_aud=CAPITAL_AUD); os.makedirs(SD, exist_ok=True); CB.atomic(LEDGER, L)
    return write_status(mkt, L, now, reg, revs, rows, CL, base)
def event_cut(mkt, sym, earn, fill_utc):
    fd = local_day(mkt, fill_utc); cut = fill_utc + dt.timedelta(hours=72); why = "72h time exit (last session close)"
    d = local_day(mkt, cut)
    while not (trading_day(mkt, d) and session_close(mkt, d) <= cut): d -= dt.timedelta(days=1)
    cut = session_close(mkt, d); e = earn.get(sym)
    if e and e > fd:
        pc = session_close(mkt, prev_td(mkt, e))
        if pc < cut: cut, why = pc, f"event {e} -> exit prior session close"
    return cut, why
def write_status(mkt, L, now, reg, revs, rows, CL, base=BASE):
    M = AM.MARKETS[mkt]; SD, LEDGER, STATUS = paths(mkt, base); T = L["trades"]; closed = [t for t in T if t["status"] == "CLOSED"]
    net_closed = round(sum(t.get("net_aud", 0) for t in closed), 2)
    open_aud = round(sum((1 if t["direction"] == "long" else -1) * (t.get("mark", t["fill"]) - t["fill"]) * t["qty_open"] * (t.get("fx_aud") or 0) for t in T if t["status"] == "FILLED"), 2)
    rep = CB.load(os.path.join(base, "state", "calendar", "inputs", f"ranked_{mkt}_report.json"), {})
    st = dict(book=f"HUNT48_{mkt.upper()}", market=mkt, name=M["name"], paper_only=True, ccy=M["ccy"], capital_aud=CAPITAL_AUD, equity_aud=round(CAPITAL_AUD + net_closed + open_aud, 2),
              net_aud_closed=net_closed, open_pnl_aud=open_aud, risk_per_trade_aud=RISK_AUD, max_open=MAX_OPEN, max_bench=MAX_BENCH, updated_utc=now.isoformat(), updated_wita=w(now),
              hours_wita=f"{M['open_wita']}-{M['close_wita']} WITA", in_hours=in_hours(mkt, now), regime=reg, index=M["idx"], long_only=not M["shorts"], short_rule=M["short_rule"],
              cost_model=M["cost_src"], universe=dict(listed=rep.get("listed"), with_bars=rep.get("with_bars"), passing_floor=rep.get("adv_ge_floor"), scored=rep.get("scored"), run_at=rep.get("run_at")),
              liquidity_floor_label=rep.get("floor_label") or "A$2m-equivalent 20d ADV", position_cap_rule="notional <= min(A$333k, 2% ADV if ADV<A$5m else 5% ADV), board-lot rounded",
              counts={k: sum(t["status"] == k for t in T) for k in ("ARMED", "BENCH", "FILLED", "CLOSED", "SHADOW", "MISSED", "EXPIRED", "DISARMED")},
              slots_used=sum(t["status"] in ("ARMED", "FILLED") for t in T), reviewed=len(revs), passed=sum(v["passed"] for v in revs),
              clearance=dict(date_wita=wday(now), reviewed=len(CL), desk_pass=sorted(s for s, v in CL.items() if v.get("internal") == "PASS"), desk_fail=sorted(s for s, v in CL.items() if v.get("internal") == "FAIL"),
                             gpt="PENDING", claude="RETIRED", rule=f"arm only after same-day RISK+BEHAVIOR+SPECIALIST PASS before open (hunt_clearance.py run --book {mkt}); GPT pending; Claude retired 2026-10-29"),
              arming_law="BENCH->ARMED only via same-day clearance before open; demoted never re-armed; day arm; pre-open live re-check",
              armed=[t for t in T if t["status"] in ("ARMED", "FILLED")], bench=sorted([t for t in T if t["status"] == "BENCH"], key=lambda t: -t["score"]),
              recent=[t for t in T if t["status"] not in ("ARMED", "FILLED", "BENCH")][-15:])
    CB.atomic(STATUS, st); return st
def clearance_plan(mkt, base=BASE):
    """for hunt_clearance.py --book <mkt>: candidates = ledger BENCH (never-demoted, pending) + ARMED, current levels, in LOCAL ccy."""
    now = dt.datetime.now(UTC); rows = load_rows(mkt, base); SD, LEDGER, _ = paths(mkt, base)
    L = CB.load(LEDGER, {"trades": []}); out = []
    for t in sorted(L["trades"], key=lambda t: -t.get("score", 0)):
        if t["status"] not in ("ARMED", "BENCH") or t.get("demoted_utc") or dt.datetime.fromisoformat(t["armed_utc"]) < now: continue
        r = rows.get(t["sym"], {})
        out.append(dict(ticker=t["sym"], direction=t["direction"], trigger=t["trigger"], max_fill=t["max_fill"], stop=t["stop"], t1=t["t1"], t2=t["t2"], shares=t["shares"],
                        atr=EC.fnum(r.get("atr")) or t.get("atr"), bench=t["status"] == "BENCH", scan_px=EC.fnum(r.get("px")), scan_rank=t.get("score"), notional_aud=t.get("notional_aud"),
                        risk_aud_actual=t.get("risk_aud_actual"), cap_rule=t.get("cap_rule"), adv_aud=t.get("adv_aud"), cost_R=t.get("cost_R"), est_cost_local=t.get("est_cost_local"),
                        newly_eligible=bool((t.get("adv_aud") or 0) < 5e6), _from_ledger=t["status"] == "ARMED"))
    return dict(label=f"HUNT48_{mkt.upper()} A$1m (PAPER) {AM.MARKETS[mkt]['name']}", set=out, regime=regime(mkt, rows))
def local_per_aud(mkt, base=BASE):
    f = fx_aud(mkt, load_rows(mkt, base)); return (1 / f) if f else None
if __name__ == "__main__":
    mkt = sys.argv[1]; assert mkt in AM.MARKETS, "usage: asia_hunt48.py jp|hk|sg|kr|tw [--force]"
    s = run(mkt, force="--force" in sys.argv)
    print(mkt, json.dumps(s["counts"]), "| regime", s["regime"], "| reviewed", s["reviewed"], "passed", s["passed"], "| clearance", len(s["clearance"]["desk_pass"]), "PASS")
    for t in s["armed"]: print("ARMED", t["sym"], t["direction"], t["sector"], "trig", t["trigger"], "max", t["max_fill"], "stop", t["stop"], "T1", t["t1"], "T2", t["t2"], t["shares"], "sh A$", t["risk_aud_actual"], "from", t["armed_wita"])
    for t in s["bench"]: print("BENCH", t["sym"], t["direction"], t["sector"], t["score"], t.get("bench_note", ""))
