#!/usr/bin/env python3
"""HUNT48 ASIA BOOKS (PAPER ONLY) - Brett 2026-10-27 (pt1481u) "Do exactly the same setup for the Asian markets" as HUNT48_ASX.
One SEPARATE A$1,000,000 paper book per market (jp hk sg kr tw): ledger state/hunt48_<mkt>/ledger.json, status static/hunt48_<mkt>_status.json,
own MAX 3 open/armed slots, own bench <=15. Same rules as asx_hunt48: event_calendar.gate (ATR<=6%, RSI, no pump/crash chase), A$2m-equivalent
20d ADV floor (universe_asia.py), regime filter (local index vs 50dMA), rank_score>=3, earnings in hold -> BLOCK, ONE per sector, direction balance,
A$2,500 risk/trade (converted to local ccy), notional cap min(A$333k, 2% ADV if ADV<A$5m else 5% ADV), stop on every trade, IBKR real costs
(asia_markets.side_cost incl. HK stamp both sides, KR 0.2% / TW 0.3% sell tax) + 10bp slip/side + half tick/side + IBKR borrow 3d for shorts;
cost_R>COST_R_MAX(mkt) (0.15 default; lse 0.30 Brett 19:44 WITA) or room_R=min(structure 1y,T2)-cost<1R -> BENCH (fail closed, Rules Auditor 27 Oct 08:40); B1 shorts RSI<30/missing or exhausted true 20d low blocked. Shorts only jp/hk with confirmed borrow (hk also designated list); sg/kr/tw LONG-ONLY.
ARMING LAW (fixes the ASX re-promotion bug by construction):
  * new names enter BENCH only; BENCH -> ARMED only via a SAME-DAY (WITA) hunt_clearance.py --book <mkt> PASS, BEFORE today's local open,
    using the cleared levels; ARMED is a DAY arm (unfilled at session close -> EXPIRED, needs fresh clearance next day).
  * any ARMED that fails anything (re-review, cost/room, clearance, live pre-open check) -> BENCH with demoted_utc and is NEVER re-armed.
  * pre-open (open-35min..open): live price re-check; px through stop or beyond max_fill, or levels drifted >0.5 ATR -> BENCH (demoted).
  * clearance missing at open -> book stays flat today.
Fills simulated on Yahoo 1h bars via cal_paper_bench.step(). No broker, no orders, no keys.
2026-10-27 19:14 WITA Brett pt1522u "Build it and add the same capital": + LONDON book `lse` (FTSE 350, pence quotes -> A$ via GBPAUD/100, UK stamp duty 0.5% on buys
+ PTM levy + half spread/side in asia_markets.side_cost; same A$1m capital, A$2.5k risk, 3 open, 1/sector, T2 1.5R, 1R room, stop on every trade, same arming law)."""
import csv, datetime as dt, json, os, sys
from zoneinfo import ZoneInfo
import cal_paper_bench as CB, event_calendar as EC, asia_markets as AM
import hunt48_room as HR   # 2026-10-27 Rules Auditor 08:40: single room definition + B1 short screen (fail closed)
import hunt_verdict_veto as VV   # 2026-10-27 20:56 WITA Brett: GPT/Claude verdict veto (BLOCKED / GPT FAIL / CLAUDE FAIL / missing-stale -> never arm)
COST_R_MAX = 0.20; MIN_ROOM_R = HR.MIN_ROOM_R; HOLD_DAYS = 3; SLIP = 0.001   # 2026-10-28 07:18 Brett: Asia cost cap 0.15 -> 0.20R (lse override 0.30 unchanged)
CAPITAL_AUD = 1_000_000.0; RISK_AUD = 2500.0; MAX_OPEN = 3; MAX_BENCH = 40; MIN_SCORE = 3.0   # fix 4: MAX_BENCH default 40 (was 15); live per-market value from state/hunt_bench.json via HR.max_bench
MAX_NOTIONAL_AUD = CAPITAL_AUD / 3; MAX_PCT_ADV = 5.0; THIN_ADV_AUD = 5e6; THIN_PCT_ADV = 2.0; AUDUSD = CB.AUDUSD
PREOPEN_MIN = 35; DRIFT_ATR = 0.5
R_BASED_MKTS = ("jp", "hk", "kr", "sg", "tw", "lse")   # 2026-10-29 16:08 WITA TFO: + lse (Brett 16:05 WITA chose R-based for LSE/US); prev comment:   # 2026-10-29 14:12 WITA TFO: R-based targets (no fixed +3%/-8%) for these Asia books; asx_hunt48 same; 14:23 TFO: + sg, tw (all Asia)
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
    rows = {r["sym"]: r for r in csv.DictReader(open(p))} if os.path.exists(p) else {}
    HR.fill_b1_inputs(rows, _daily)      # Brett 28 Oct hunt fix 2: missing roc5/atr_pct filled from daily bars (fail closed only if unfetchable, logged)
    return rows
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
def _daily(sym):      # 1y daily bars (was 3mo); [] -> BENCH (fail closed)
    if getattr(HR, "_fetch", None) is getattr(HR, "_fetch_yf", None):   # 2026-10-30 paidbars: paid-first (EODHD exact match), HR.daily (Yahoo) fallback; tests that patch HR._fetch keep HR.daily
        import paid_bars as PB; return PB.daily_hr(sym, HR.daily, HR.DATA_STATUS)
    return HR.daily(sym)
def opposing_levels(daily, long=False):
    d = daily[:-1] if len(daily) > 5 else daily; lv = []
    for i in range(2, len(d) - 2):
        if not long and d[i]["l"] <= min(d[j]["l"] for j in (i-2, i-1, i+1, i+2)): lv.append(d[i]["l"])
        if long and d[i]["h"] >= max(d[j]["h"] for j in (i-2, i-1, i+1, i+2)): lv.append(d[i]["h"])
    for n in (20, 50):
        if len(d) >= n: lv.append(sum(x["c"] for x in d[-n:]) / n)
    return lv
def cost_R_cap(t_or_mkt):
    """Per-market cost_R ceiling. Default COST_R_MAX 0.15; MARKETS[mkt].cost_R_max overrides (lse=0.30 Brett 2026-10-27 19:44 WITA). London only."""
    m = t_or_mkt if isinstance(t_or_mkt, str) else (t_or_mkt or {}).get("market")
    if m and m in AM.MARKETS:
        v = AM.MARKETS[m].get("cost_R_max")
        if v is not None: return float(v)
    return COST_R_MAX
def cost_gate(t, daily=None):
    """None if ok; else reason. cost_R <= cost_R_cap(market) AND hunt48_room.room(): room_R = min(structure, T2) - cost_R >= 1R (fail closed; room_R always numeric)."""
    daily = _daily(t.get("yahoo") or t["sym"]) if daily is None else daily
    why = HR.room(t, daily, min_bars=None if str(t.get("market") or "").lower() == "lse" else HR.US_MIN_BARS)   # audit M4 hardening (Brett 27 Oct 2026 21:11 WITA): jp/hk/sg/kr/tw <200 valid daily bars -> BENCH (fail closed); London book (lse, built in parallel) not changed here
    cap = cost_R_cap(t)
    if (t.get("cost_R") or 0) > cap: return f"IBKR cost {t['cost_R']:.3f}R > {cap}R"
    if t.get("room_R") is None: return "room_R null -> BENCH (never fail open)"
    return why
# ---------------- review + levels
RS_LONG_TAG = "rs_long_in_short_regime"; RS_LONG_MIN_RS20 = 5.0; RS_LONG_MAX_RS20 = 30.0   # 2026-10-28 Brett 09:59 (+14:58 funnel): longs in short regime if RS20 +5..+30 vs index (was +25; KR/TW beta left the band empty)
def _cat_bonus(mkt, sym, d, score):
    try:
        import catalyst as CAT; return CAT.bonus(mkt, sym, d, score, MIN_SCORE)
    except Exception: return 0.0, None                        # feed missing/broken -> no bonus, nothing else changes
def shorts_blocked(mkt, rows):
    """True when this book cannot short: long-only market, or no name in today's universe is shortable (IBKR list empty)."""
    if not AM.MARKETS[mkt]["shorts"]: return True
    return not any(str(r.get("shortable")) == "True" for r in rows.values() if r.get("cls") == f"{mkt}_equity")
def review(mkt, rows, earn, halted, now, hold_end):
    reg = regime(mkt, rows); out = []; M = AM.MARKETS[mkt]
    rs_mode = reg == "short" and shorts_blocked(mkt, rows)
    for s, r in rows.items():
        if r.get("cls") != f"{mkt}_equity": continue
        d = r.get("dir") if r.get("dir") in ("long", "short") else "long"
        score = EC.fnum(r.get("rank_score"), 0) or 0; tag = None
        if rs_mode:                                   # relative-strength long: long side, long score, must clearly beat the index
            d = "long"; score = EC.fnum(r.get("score"), 0) or 0; tag = RS_LONG_TAG
        # 2026-10-28 Brett 14:58 WITA: rs_mode (long-only Asia in short regime) uses ATR cap 8% —
        # same US_MAX_ATR_PCT already in event_calendar; 6% left KR with zero passers (only ATR fail on score>=3 RS-band names).
        why = EC.gate(r, d, max_atr_pct=(8.0 if rs_mode else None))
        if rs_mode:
            rs20 = EC.fnum(r.get("rs20"))
            if rs20 is None: why.append("RS vs index UNKNOWN (fail closed)")
            elif rs20 < RS_LONG_MIN_RS20: why.append(f"short regime: long needs RS20 >= +{RS_LONG_MIN_RS20:g} vs {M['idx']} (is {rs20:+.1f})")
            elif rs20 > RS_LONG_MAX_RS20: why.append(f"pump-chase guard: RS20 {rs20:+.1f} > +{RS_LONG_MAX_RS20:g} vs {M['idx']}")
        if d == "short": why += HR.rsi_block(r)                     # B1: RSI<30 or missing -> blocked (Rules Auditor 08:40)
        if (EC.fnum(r.get("adv_aud"), 0) or 0) < 2e6: why.append("ADV < A$2m equivalent")
        if d == "short" and not M["shorts"]: why.append("LONG-ONLY market: no IBKR short selling")
        if d == "short" and str(r.get("shortable")) != "True": why.append("not shortable (" + str(r.get("short_src"))[:80] + ")")
        if s in halted: why.append("trading halt")
        if reg in ("long", "short") and reg != d and not rs_mode: why.append(f"regime filter ({M['idx']}): {reg}-only")
        cb, cinfo = _cat_bonus(mkt, s, d, score)          # 2026-10-28 Brett 10:29: capped news catalyst (borderline lift only; levels/size untouched)
        score += cb
        if score < MIN_SCORE: why.append(f"rank_score {score:.2f}<3.0")
        if d == "short" and not why: why += HR.exhaustion_block(r, _daily(s))   # B1: true 20d low + 5d drop >1.5 ATR (fail closed)
        e = earn.get(s)
        if e and local_day(mkt, now) - dt.timedelta(days=1) <= e <= local_day(mkt, hold_end): why.append(f"BLOCK: earnings {e} inside hold window")
        out.append(dict(sym=s, dir=d, rank_score=round(score, 2), score=round(score, 2), sector=r.get("sector") or s, passed=not why, why=why, strategy=tag, catalyst=cinfo,
                        rs20=EC.fnum(r.get("rs20"))))
    return reg, out
def levels(mkt, r, d, px=None):
    """stage levels in LOCAL ccy (EC.stage_levels with quote=1), lot-rounded, A$ notional cap, IBKR costs. px overrides scan px (live re-stage)."""
    fx = EC.fnum(r.get("fx_aud")); M = AM.MARKETS[mkt]
    if not fx: return None
    rr = dict(r)
    if px: rr["px"] = px; rr["pdh"] = max(EC.fnum(r.get("pdh"), px), px); rr["pdl"] = min(EC.fnum(r.get("pdl"), px), px)
    risk_local = RISK_AUD / fx
    # 2026-10-29 14:12 WITA TFO head-trader (PAPER): ASX/JP/HK/KR (incl. RS longs) drop the 28 Oct 07:12 fixed +3%/-8% levels and return to
    # R-based targets (stop = min(1.5 ATR, 5%), T2 = HR.T2_R x stop distance, pre-28-Oct stage_levels path); MIN_ROOM_R 1.0 unchanged, no exemption.
    # 14:23 WITA TFO: sg + tw added (Brett decision covers all Asia). Only lse (London) on this engine keeps the fixed levels.
    fp = None if mkt in R_BASED_MKTS else (HR.FIXED_TP_PCT, HR.FIXED_STOP_PCT)
    lv = EC.stage_levels(dict(rr, venue=mkt), d, risk_local, 1.0, t2_r=HR.T2_R, fixed_pct=fp, maxfill_atr=CB.hunt_fill_rule()["max_fill_atr"])   # fix 3 band; 2026-10-27 pt1486u: hunt48 T2 = 1.5R
    if not lv or not lv["shares"]: return None
    lot = int(EC.fnum(r.get("lot"), M["lot"] or 1) or 1)
    adv_aud = EC.fnum(r.get("adv_aud"), 0)
    if not adv_aud: return None   # 2026-10-29 item 8: null ADV is a reject (never size against a fake A$1)
    pct = THIN_PCT_ADV if adv_aud < THIN_ADV_AUD else MAX_PCT_ADV
    cap_aud = min(MAX_NOTIONAL_AUD, adv_aud * pct / 100); sh = int(lv["shares"])
    if sh * lv["max_fill"] * fx > cap_aud: sh = int(cap_aud / (lv["max_fill"] * fx))
    sh = (sh // lot) * lot
    if sh <= 0: return None
    mf, st = lv["max_fill"], lv["stop"]; n_loc = sh * mf
    spb = EC.fnum(r.get("spread_bp"))     # LSE: per-name quoted-spread estimate (None -> market default; ignored by markets without a spread term)
    buy = AM.side_cost(mkt, sh, mf, "buy", spread_bp=spb); sell = AM.side_cost(mkt, sh, mf, "sell", spread_bp=spb); tk = AM.tick(mkt, mf)
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
INTRADAY_STOP_BEFORE_CLOSE_MIN = 30; INTRADAY_FRESH_MIN = 60
def intraday_ok(mkt, now, base=BASE):
    """True only when called by intraday_rescan (HUNT48_INTRADAY=1), market in session (not within 30 min of close) and today's
    clearance summary was written <= 60 min ago."""
    if os.environ.get("HUNT48_INTRADAY") != "1": return False
    d = local_day(mkt, now)
    if not trading_day(mkt, d) or not (session_open(mkt, d) <= now <= session_close(mkt, d) - dt.timedelta(minutes=INTRADAY_STOP_BEFORE_CLOSE_MIN)): return False
    p = os.path.join(base, "state", "clearance", wday(now), mkt, "summary.json")
    return os.path.exists(p) and now.timestamp() - os.path.getmtime(p) <= INTRADAY_FRESH_MIN * 60
def IR_checks(mkt, t, now):
    import intraday_rescan as IR
    return IR.open_time_checks(mkt, t, now)
def demote(t, now, why):
    t.update(status="BENCH", demoted_utc=now.isoformat(), bench_note=("DEMOTED (never re-armed): " + why)[:240])
# ---------------- engine
def _track_fetch(f):   # 2026-10-29 TFO 11:12 WITA fix 2 (additive): record latest hourly bar END used -> health bars_asof
    def g(*a, **k):
        b = f(*a, **k)
        try:
            if b: HR.note_bar(b[-1].get("t"))
        except Exception: pass
        return b
    return g
def _firm_block(trade, mkt):
    import firm_pretrade
    venue = "US" if mkt == "us" else str(mkt).upper()
    return firm_pretrade.blocked(firm_pretrade.hunt_proposal(trade, "hunt_" + str(mkt), venue, CAPITAL_AUD, RISK_AUD))

def run(mkt, now=None, fetch=None, base=BASE, force=False, live=live_px):
    if fetch is None:   # 2026-10-30 paidbars: paid-first hourly bars (EODHD where it matches Yahoo exactly), Yahoo CB.bars_yf fallback; source logged
        import paid_bars as PB; fetch = lambda y, s: PB.hourly(y, s, CB.bars_yf)
    fetch = _track_fetch(fetch)
    now = now or CB.now_utc(); M = AM.MARKETS[mkt]; SD, LEDGER, STATUS = paths(mkt, base)
    rows = load_rows(mkt, base); earn, halted = load_events(mkt, base); fx = fx_aud(mkt, rows)
    L = CB.load(LEDGER, dict(book=f"HUNT48_{mkt.upper()}", market=M["name"], paper_only=True, capital_aud=CAPITAL_AUD, ccy=M["ccy"], trades=[]))
    T = L["trades"]; nid = lambda: max([t["id"] for t in T] or [0]) + 1
    today = local_day(mkt, now); open_today = session_open(mkt, today) if trading_day(mkt, today) else None
    nxt_open = open_today if (open_today and now < open_today) else session_open(mkt, next_td(mkt, today))
    hold_end = nxt_open + dt.timedelta(hours=72); CL = clearance(mkt, now, base); VT = VV.load(mkt, now, base)   # GPT/Claude verdicts (US-book files); missing/stale -> all vetoed
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
            if v and v.get("why") and v["dir"] == t["direction"] and all(HR.is_data_fail(x) for x in v["why"]):   # review fix #3: DATA_FAIL != RULE_FAIL
                if t["status"] == "ARMED": demote(t, now, "DATA_FAIL " + why)                                       # armed + no data -> still out (fail closed)
                else: t.update(data_fail=why[:200], data_fail_utc=now.isoformat())                                  # BENCH kept: re-evaluated next run, not DISARMED
                continue
            if t["status"] == "ARMED": demote(t, now, why)
            else: t.update(status="DISARMED", note=why[:200])
            continue
        t.pop("data_fail", None)
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
    active |= {t["sym"] for t in T if str(t.get("armed_utc"))[:10] == nxt_open.isoformat()[:10]}   # fix 2: never re-record a name for the same session
    # 2) new candidates -> BENCH only
    cands = sorted([v for v in revs if v["passed"] and v["sym"] not in active], key=lambda v: -v["score"])
    _rk, _seen = {}, {}
    for v in cands: _seen[v["sector"]] = _seen.get(v["sector"], 0) + 1; _rk[v["sym"]] = _seen[v["sector"]]
    cands.sort(key=lambda v: (min(_rk[v["sym"]], 2), -v["score"]))
    live_ = lambda: [t for t in T if t["status"] in ("ARMED", "FILLED", "awaiting_funder")]   # awaiting_funder holds its slot
    for v in cands:
        if sum(t["status"] == "BENCH" for t in T) >= HR.max_bench(mkt, MAX_BENCH, os.path.join(base, "state")): break
        r = rows[v["sym"]]; lv = levels(mkt, r, v["dir"])
        if not lv: continue
        T.append(dict(id=nid(), sym=v["sym"], yahoo=v["sym"], market=mkt, direction=v["dir"], status="BENCH", armed_utc=nxt_open.isoformat(), armed_wita=w(nxt_open),
                      recorded_utc=now.isoformat(), sector=v["sector"], name=r.get("name"), shortable=r.get("shortable"), short_src=r.get("short_src"), borrow_avail=r.get("borrow_avail"),
                      score=v["score"], rank_score=v["rank_score"], **({"strategy": v["strategy"], "rs20": v.get("rs20")} if v.get("strategy") else {}), **({"catalyst": v["catalyst"]} if v.get("catalyst") else {}), ccy=M["ccy"], fx_aud=fx, q2usd=round((fx or 0) * AUDUSD, 8), adv_aud=EC.fnum(r.get("adv_aud")),
                      **{k: lv[k] for k in LEVEL_KEYS + ("rr_t1", "rr_t2", "breakeven_text") if k in lv}))
        if v["dir"] == "short" and not borrow_ok(mkt, r, lv["shares"]): T[-1].update(status="SHADOW", shadow_reason="no confirmed borrow")
        else:
            rw = cost_gate(T[-1])                                                   # room_R written at bench entry (no nulls)
            if rw: T[-1]["bench_note"] = "room/cost gate: " + rw
    # 2b) clearance annotations; ARMED that FAILED today's clearance -> demoted BENCH
    for t in T:
        c = CL.get(t["sym"])
        vr = VT["rows"].get(t["sym"]) or {}
        if t["status"] in ("ARMED", "BENCH") and vr: t.update(VV.ai_tags(t["sym"], VT))   # 2026-10-28 AI advisory: record ai_gpt / ai_claude
        if t["status"] in ("ARMED", "BENCH"):
            t["clearance"] = (dict(date=wday(now), desk=c.get("internal"), RISK=c.get("R"), BEHAVIOR=c.get("B"), SPECIALIST=c.get("S"), GPT=vr.get("GPT") or "PENDING", CLAUDE=vr.get("CLAUDE") or "RETIRED", final=vr.get("final")) if c else dict(desk="PENDING", date=wday(now)))
        if pending(t) and t["status"] == "ARMED" and c and c.get("internal") == "FAIL": demote(t, now, "desk clearance FAIL: " + "; ".join(c.get("why") or [])[:200])
        if pending(t) and t["status"] == "ARMED":                            # veto hook: pending ARMED BLOCKED / GPT or CLAUDE FAIL / no fresh verdict -> demoted (never re-armed)
            vv = VV.veto(t["sym"], VV.for_armed(mkt, now, base, t.get("cleared_wita"), VT))
            if vv: demote(t, now, vv); t["verdict_veto"] = vv
    # 3) promotion: ONLY never-armed, never-demoted BENCH, same-day clearance PASS, BEFORE today's open, cleared levels; gates re-checked
    can_arm = before_open_today(mkt, now)
    # 2026-10-28 09:59 WITA Brett: INTRADAY RESCAN arm (intraday_rescan.py sets HUNT48_INTRADAY=1 right after a fresh clearance).
    # In session, >=30 min before close, clearance summary <= 60 min old -> may arm into OPEN slots; armed_utc = now (fills only on
    # later bars), plus the four open-time checks (gap / RS vs index / OR volume / spread). Same gates otherwise; never re-arms.
    intraday = intraday_ok(mkt, now, base)
    held = {x["sym"] for x in T if x["status"] in ("ARMED", "FILLED", "awaiting_funder")}
    for t in sorted([t for t in T if t["status"] == "BENCH"], key=lambda t: -t["score"]):
        if t.get("demoted_utc") or t.get("promoted_utc"): continue          # NEVER re-arm (ASX bug)
        c = CL.get(t["sym"])
        if not c or c.get("internal") != "PASS" or not c.get("setup"): t["bench_note"] = "awaiting same-day desk clearance" if not c else ("desk clearance " + str(c.get("internal"))); continue
        vv = VV.veto(t["sym"], VT)                                           # GPT veto hook (fail closed): never arm
        if vv: t["bench_note"] = vv; continue
        if not (can_arm or intraday): t["bench_note"] = "cleared but market already open/closed today -> flat today (fresh clearance before next open / next intraday rescan)"; continue
        if t["sym"] in held: t["bench_note"] = "already open/armed in book -> never re-armed"; continue
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
        if not can_arm:                                                      # intraday arm: the four open-time checks, fail closed
            ok4, why4 = IR_checks(mkt, cand, now)
            if not ok4: t["bench_note"] = "intraday open-time checks: " + "; ".join(why4)[:200]; continue
        arm_at = open_today if can_arm else now
        _why = _firm_block(dict(cand, direction=t["direction"], sym=t["sym"]), mkt)
        if _why: t["bench_note"] = "firm_risk:" + _why; continue
        t.update({k: cand[k] for k in ("trigger", "max_fill", "stop", "t1", "t2", "shares")}, status="ARMED", promoted_utc=now.isoformat(), cleared_wita=wday(now),
                 armed_utc=arm_at.isoformat(), armed_wita=w(arm_at), intraday_rescan=(w(now) if not can_arm else None), live_px=lp, room_R=cand.get("room_R"), room_basis=cand.get("room_basis"))
        t.pop("bench_note", None)
    for t in sorted([t for t in T if t["status"] == "BENCH"], key=lambda t: -t["score"])[HR.max_bench(mkt, MAX_BENCH, os.path.join(base, "state")):]: t.update(status="DISARMED", note=f"bench full (>{HR.max_bench(mkt, MAX_BENCH, os.path.join(base, 'state'))})")
    # 3c) 2026-10-28 Brett 10:37 FUNDER GATE: every ARMED (not yet funder-approved) -> awaiting_funder + queue; apply ticks (approve/reject)
    import funder_gate as FG
    _exp = lambda t, n: n > session_close(mkt, local_day(mkt, dt.datetime.fromisoformat(t["armed_utc"]))) + dt.timedelta(minutes=90)
    for sy, a in FG.gate_tick(mkt, L, now, lambda t: live(t["yahoo"]), _exp, base=base): print("FUNDER", sy, a)
    # 4) fills/exits on own slots
    slots = MAX_OPEN - sum(t["status"] == "FILLED" for t in T)
    if in_hours(mkt, now, 75) or force:
        for t in [t for t in T if t["status"] in ("ARMED", "FILLED")]:
            if dt.datetime.fromisoformat(t["armed_utc"]) > now: continue
            if t["status"] == "ARMED":
                _why = _firm_block(t, mkt)
                if _why: t["firm_risk_block"] = _why; continue
            try:
                bars = fetch(t["yahoo"], dt.datetime.fromisoformat(t["armed_utc"])); was = t["status"] == "FILLED"
                if t["status"] == "ARMED": bars = [b for b in bars if b["t"] <= session_close(mkt, local_day(mkt, dt.datetime.fromisoformat(t["armed_utc"]))) + dt.timedelta(minutes=65)]   # day arm
                if was: cut, why = event_cut(mkt, t["sym"], earn, dt.datetime.fromisoformat(t["fill_utc"])); bars = [b for b in bars if b["t"] <= cut]
                slots = CB.step(t, bars, now, slots + (1 if was else 0), rule=CB.hunt_fill_rule()["rule"], giveback=True, engine=f"hunt48_{mkt}", half_1r=True) - (1 if was and t["status"] == "FILLED" else 0)
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
              net_aud_closed=net_closed, open_pnl_aud=open_aud, risk_per_trade_aud=RISK_AUD, max_open=MAX_OPEN, max_bench=HR.max_bench(mkt, MAX_BENCH, os.path.join(base, "state")), updated_utc=now.isoformat(), updated_wita=w(now),
              hours_wita=f"{M['open_wita']}-{M['close_wita']} WITA", hours_note=M.get("hours_note"), entry_rule=M.get("entry_rule"), in_hours=in_hours(mkt, now), regime=reg, index=M["idx"], long_only=not M["shorts"], short_rule=M["short_rule"],
              cost_model=M["cost_src"], universe=dict(listed=rep.get("listed"), with_bars=rep.get("with_bars"), passing_floor=rep.get("adv_ge_floor"), scored=rep.get("scored"), run_at=rep.get("run_at")),
              liquidity_floor_label=rep.get("floor_label") or "A$2m-equivalent 20d ADV", position_cap_rule="notional <= min(A$333k, 2% ADV if ADV<A$5m else 5% ADV), board-lot rounded", stops="stop on every trade (staged with the setup; BE after T1)",
              cost_R_max=cost_R_cap(mkt),
              counts={k: sum(t["status"] == k for t in T) for k in ("ARMED", "awaiting_funder", "BENCH", "FILLED", "CLOSED", "SHADOW", "MISSED", "EXPIRED", "DISARMED")},
              slots_used=sum(t["status"] in ("ARMED", "FILLED") for t in T), reviewed=len(revs), passed=sum(v["passed"] for v in revs),
              data_fail=dict(reviews=sum(bool(v["why"]) and all(HR.is_data_fail(x) for x in v["why"]) for v in revs), bench_waiting=sorted(t["sym"] for t in T if t["status"] == "BENCH" and t.get("data_fail")),
                             note="DATA_FAIL = feed/fetch failure (retried + 24h disk cache), not a rule fail; such BENCH names are re-evaluated next run, never armed on missing data"),
              clearance=dict(date_wita=wday(now), reviewed=len(CL), desk_pass=sorted(s for s, v in CL.items() if v.get("internal") == "PASS"), desk_fail=sorted(s for s, v in CL.items() if v.get("internal") == "FAIL"),
                             gpt="PENDING", claude="RETIRED", rule=f"arm only after same-day RISK+BEHAVIOR+SPECIALIST PASS before open (hunt_clearance.py run --book {mkt}); GPT pending; Claude retired"),
              arming_law="BENCH->ARMED only via same-day clearance before open; demoted never re-armed; day arm; pre-open live re-check",
              armed=[t for t in T if t["status"] in ("ARMED", "FILLED")], awaiting_funder=[t for t in T if t["status"] == "awaiting_funder"], bench=sorted([t for t in T if t["status"] == "BENCH"], key=lambda t: -t["score"]),
              recent=[t for t in T if t["status"] not in ("ARMED", "FILLED", "BENCH")][-15:])
    vs = VV.summary(VV.load(mkt, now, base))
    st["clearance"].update(gpt=f"FAIL {len(vs.get('gpt_fail', []))} / CLEARED {len(vs.get('cleared', []))}" if vs.get("ok") else "NO VERDICT FILE", claude=f"FAIL {len(vs.get('claude_fail', []))}" if vs.get("ok") else "NO VERDICT FILE", gpt_fail=vs.get("gpt_fail", []), claude_fail=vs.get("claude_fail", []), final_blocked=vs.get("blocked", []), cleared=vs.get("cleared", []), verdict_veto=vs,
                           rule=f"arm only after same-day RISK+BEHAVIOR+SPECIALIST PASS AND finalize CLEARED with no GPT FAIL (Claude retired 2026-10-29), before open (hunt_clearance.py run/finalize --book {mkt}); verdict files missing/stale -> stay out")
    CB.atomic(STATUS, st)
    # 2026-10-28 Brett 14:58 WITA: per-book health for always-on watchdog (never arms)
    try:
        health = dict(book=st["book"], market=mkt, paper_only=True, ok=True,
                      counts=st["counts"], reviewed=st["reviewed"], passed=st["passed"],
                      regime=reg, awaiting_funder=st["counts"].get("awaiting_funder", 0),
                      bench=st["counts"].get("BENCH", 0),
                      funder_tick_required=not __import__("funder_gate").auto_approve(mkt, base), auto_arm=bool(__import__("funder_gate").auto_approve(mkt, base)),
                      updated_utc=st["updated_utc"], updated_wita=st["updated_wita"])
        health["bars_asof"] = HR.bars_asof_iso()   # fix 2 2026-10-29: latest bar used this tick (ISO+offset; None = no fresh fetch)
        CB.atomic(os.path.join(SD, "health.json"), health)
        CB.atomic(os.path.join(base, "static", f"hunt48_{mkt}_health.json"), health)
    except Exception as _he:
        pass
    return st
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
    mkt = sys.argv[1]; assert mkt in AM.MARKETS, "usage: asia_hunt48.py jp|hk|sg|kr|tw|lse [--force]"
    s = run(mkt, force="--force" in sys.argv)
    print(mkt, json.dumps(s["counts"]), "| regime", s["regime"], "| reviewed", s["reviewed"], "passed", s["passed"], "| clearance", len(s["clearance"]["desk_pass"]), "PASS")
    for t in s["armed"]: print("ARMED", t["sym"], t["direction"], t["sector"], "trig", t["trigger"], "max", t["max_fill"], "stop", t["stop"], "T1", t["t1"], "T2", t["t2"], t["shares"], "sh A$", t["risk_aud_actual"], "from", t["armed_wita"])
    for t in s["bench"]: print("BENCH", t["sym"], t["direction"], t["sector"], t["score"], t.get("bench_note", ""))
