#!/usr/bin/env python3
"""HUNT ORCHESTRATOR - daily all-market hunt, arm-into-open-slots, ranked backups, auto re-slot. PAPER ONLY.
# GIVEBACK DNA (Brett HARD 2026-10-29 17:17 WITA, see RULE_LOG.md): GIVEBACK DNA (Brett HARD 2026-10-29 17:17 WITA; spec final 17:20; PAPER): every bot and engine. Never sit through a reverse that gives back open profit. Mechanical early-bank trigger on an open winner BEFORE its normal half-bank: (a) peak >= +0.5R then >= 50% of that peak given back; OR (b) a bar closes through the prior bar's low (shorts: high) on >= 2x average volume; OR (c) a logged adverse news item. Action: bank half or more at the live mark, residual stop to BE then normal trail. Noise (no trigger) is not a turn. Never forces an entry; never sells what is not held. Every early bank logs reason + rail counterfactual (what the normal rail would have done). Normal half-bank = net A$ >= 1R of ORIGINAL entry risk after costs (NO +1% leg); Dex trend seat 1.5R.
# TODO(giveback): wire triggers (a) peak>=0.5R & >=50% giveback, (b) close through prior bar on >=2x avg vol, (c) logged news into this engine's exit path;
#   bank >= half, residual BE, log reason + rail counterfactual. Until wired, apply by hand. No logic change in this edit.

Brett mandate 2026-10-27 19:38 WITA (pt1525u): "This trade setup needs to trading daily - and making sure we cover all
tradable assets across all markets and in-depth analysis everyday to setup the trades for and arm them each day if there
is open slots and have backups to slot in".

This module OWNS NO TRADING LOGIC. It sequences the existing engines and reviewers and only arms through their own CLIs:
  US   : state/calendar/ranker/run_scan.py (NASDAQ+NYSE+Kraken universe) -> gate/B1/room/levels -> plan_bench_<D>.json
         -> hunt_clearance.py run (3 desks + GPT via Codex; Claude retired 2026-10-29) -> hunt48_short_paper.py apply-plan (open slots only)
  ASX  : universe_asx.py -> asx_hunt48.py -> hunt_clearance.py --book asx -> finalize -> asx_hunt48.py (engine promotes)
  ASIA : state/hunt48_<mkt>/preopen_<mkt>.sh (existing chain) if it has not run today; engine arms same-day PASS pre-open
  LSE  : read-only (London build owns its chain)
  FX   : ranked_oanda.csv -> gate/room/levels at 1% NAV -> plan + backups (oanda_sleeve.py owns OANDA entries; never called)
  CRYPTO: Kraken entry-engine screen + ranked.csv kraken rows -> plan + backups (entry engine owns fills; never called)
Book rules: max 3 open per book (OANDA: max 10, 1% risk), one per sector, A$2.5k risk, stop on every trade, room >= 1R with
T2 = 1.5R (hunt48_room.room), ranked BACKUP list (target >= 5). Backups re-slot automatically (US) after gates are re-checked.
No live orders. No keys read or printed. Never edits an engine ledger directly.

  python3 hunt_orchestrator.py cron            # launchd com.tfo.hunt-orch every 300s
  python3 hunt_orchestrator.py hunt <mkt>      # asx jp hk sg kr tw lse us fx crypto
  python3 hunt_orchestrator.py plan [mkt|all]  # rebuild daily plans + War Room status (read-only on engines)
  python3 hunt_orchestrator.py reslot          # US: arm next valid backup into a free slot
  python3 hunt_orchestrator.py schedule        # today's schedule in WITA
"""
import math, csv, datetime as dt, json, os, shutil, subprocess, sys, time
from zoneinfo import ZoneInfo

BASE = os.environ.get("DESK_BASE", os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable or "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
UTC = dt.timezone.utc; WITA = ZoneInfo("Asia/Makassar")
ORCH = os.path.join(BASE, "state", "hunt_orch"); PLAN_DIR = os.path.join(BASE, "reports", "daily_plan")
STATUS = os.path.join(BASE, "static", "hunt_orchestrator_status.json"); EVENTS = os.path.join(ORCH, "events.jsonl")
RISK_AUD = 5000.0; MAX_OPEN = 3; US_MAIN_MAX_OPEN = 4   # Brett 2026-10-29 20:17 WITA (brett_override_20261029_2017): "Raise the hunt cap to A$5k a trade and add a 4th slot" - US MAIN hunt book only (was 2500 / 3); MAX_OPEN 3 stays for ASX/Asia/LSE/crypto plan views
MIN_BACKUPS = 5; MAX_BACKUPS = 8; OANDA_MAX = 10; OANDA_RISK = 0.01
EARN_BLOCK_D = 21    # 3-session hold + 16 calendar days of possible extension (audit fix H6 / hunt_clearance US horizon)
CAND_N = 40          # US: names taken to the full bar/room analysis after the fast gate
US_BOOKS = {"main": ("hunt48_short", "us_main"), "gpt": ("hunt48_short_gpt", "us_gpt"), "claude": ("hunt48_short_claude", "us_claude")}
ASIA = ("jp", "hk", "sg", "kr", "tw")
LEAD_MIN = dict(asx=55, jp=60, kr=60, sg=45, tw=45, hk=45, lse=30, us=90)

def now_utc(): return dt.datetime.now(UTC)
def wita(t): return t.astimezone(WITA).strftime("%a %d %b %H:%M WITA") if t else None
def dkey(now): return now.astimezone(WITA).strftime("%Y%m%d")
def fnum(x, d=None):
    try:
        v = float(x); return v if v == v else d
    except Exception: return d
def atomic(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True); tmp = p + ".tmp"
    with open(tmp, "w") as f: json.dump(o, f, indent=1, default=str)
    os.replace(tmp, p)
def jload(p, d=None):
    try: return json.load(open(p))
    except Exception: return d
def event(kind, **kw):
    os.makedirs(ORCH, exist_ok=True)
    with open(EVENTS, "a") as f: f.write(json.dumps(dict(utc=now_utc().isoformat(timespec="seconds"), wita=wita(now_utc()), kind=kind, **kw), default=str) + "\n")
def cfg():
    """state/hunt_orch/config.json (optional). auto_arm_us_books: which US books the orchestrator arms (default main only:
    the gpt/claude comparison books keep their own pickers)."""
    c = dict(auto_arm_us_books=["main"], us_arm=True, reslot=True, open_gates=True, or_min=30, pregate_lead_min=15)
    c.update(jload(os.path.join(ORCH, "config.json"), {}) or {}); return c

# ------------------------------------------------------------------ schedule (DST-safe: all via zoneinfo)
def _open_at(d, tzname, hm):
    h, m = map(int, hm.split(":")); return dt.datetime.combine(d, dt.time(h, m), ZoneInfo(tzname)).astimezone(UTC)
def market_days(mkt):
    """(tz, open 'HH:MM', trading_day(date)->bool)"""
    if mkt == "us":
        import hunt48_short_paper as H
        return "America/New_York", "09:30", H.ny_session
    if mkt == "asx":
        import asx_hunt48 as A
        return "Australia/Sydney", "10:00", A.trading_day
    import asia_markets as AM
    M = AM.MARKETS[mkt]; hol = set(M["holidays"])
    return M["tz"], M["sessions"][0][0], (lambda d: d.weekday() < 5 and d not in hol)
def next_open(mkt, now):
    """Next regular-session open (UTC) at or after now - 6h (so the current day's open is returned until 6h after it)."""
    tzn, hm, td = market_days(mkt); d = (now - dt.timedelta(hours=6)).astimezone(ZoneInfo(tzn)).date()
    for _ in range(15):
        if td(d):
            o = _open_at(d, tzn, hm)
            if o >= now - dt.timedelta(hours=6): return o
        d += dt.timedelta(days=1)
    return None
def run_at(mkt, now):
    """(scheduled hunt time UTC, deadline UTC, day key) for the next/current cycle."""
    if mkt == "fx":   # after the 17:00 New York daily close (Sun-Thu NY -> next FX session)
        ny = ZoneInfo("America/New_York"); d = (now - dt.timedelta(hours=8)).astimezone(ny).date()
        for _ in range(8):
            t = dt.datetime.combine(d, dt.time(17, 40), ny).astimezone(UTC)
            if d.weekday() in (6, 0, 1, 2, 3) and t + dt.timedelta(hours=8) > now: return t, t + dt.timedelta(hours=8), "fx_" + d.isoformat()
            d += dt.timedelta(days=1)
    if mkt == "crypto":   # after the 00:00 UTC daily candle
        d = (now - dt.timedelta(hours=8)).date()
        t = dt.datetime.combine(d, dt.time(0, 10), UTC)
        if t + dt.timedelta(hours=8) <= now: t += dt.timedelta(days=1)
        return t, t + dt.timedelta(hours=8), "crypto_" + t.date().isoformat()
    o = next_open(mkt, now); t = o - dt.timedelta(minutes=LEAD_MIN[mkt])
    return t, o, f"{mkt}_{o.date().isoformat()}"
def schedule(now=None):
    now = now or now_utc(); out = []
    for m in ("fx", "asx", "jp", "kr", "sg", "tw", "hk", "crypto", "lse", "us"):
        t, dl, k = run_at(m, now)
        out.append(dict(market=m, hunt_wita=wita(t), open_or_deadline_wita=wita(dl), key=k, done=os.path.exists(marker(k))))
    return sorted(out, key=lambda r: r["key"])
def marker(k): return os.path.join(ORCH, "done", k + ".json")
def due(now=None):
    now = now or now_utc(); out = []
    for m in ("fx", "asx", "jp", "kr", "sg", "tw", "hk", "crypto", "lse", "us"):
        t, dl, k = run_at(m, now)
        if t <= now < dl and not os.path.exists(marker(k)) and not os.path.exists(os.path.join(ORCH, "lock_" + m)): out.append(m)
    return out

# ------------------------------------------------------------------ subprocess helpers (existing scripts only)
def sh(args, timeout=3600, env=None, log=None):
    e = dict(os.environ); e.update(env or {}); t0 = time.time()
    try:
        r = subprocess.run(args, cwd=BASE, capture_output=True, text=True, timeout=timeout, env=e)
        rc, out = r.returncode, (r.stdout or "")[-1500:] + (r.stderr or "")[-800:]
    except subprocess.TimeoutExpired: rc, out = -9, f"timeout {timeout}s"
    rec = dict(cmd=" ".join(os.path.basename(a) if i < 2 else a for i, a in enumerate(args)), rc=rc, s=round(time.time() - t0), tail=out[-600:])
    if log is not None: log.append(rec)
    return rc, out

# ------------------------------------------------------------------ US: candidate analysis (pure where possible)
def load_csv(p):
    try: return list(csv.DictReader(open(p)))
    except Exception: return []
def is_indonesian(r):
    s = (r.get("sym") or "").upper()
    return (r.get("country") or "").lower() == "indonesia" or s.endswith(".JK") or "IDR" in s.split("_")
SI_BLOCK_PCT = 20.0; DTC_BLOCK = 10.0; DTC_HALF = 5.0   # review fix #8 (hunt_review_20261027)
def si_pct(x):
    v = fnum(x)
    if v is None: return None
    return v * 100 if v <= 1.0 else v
# Scan-cleanup data sanity guards (Brett 2026-10-28 04:45 WITA): bad rows seen on 27 Oct (SXTC ATR ~2,300%, FMC/INTR < $5,
# TTMI trigger far from price). Reject: ATR% > 25 or uncomputable; price < $5; trigger > 15% from the last close. Every reject
# carries a "sanity:" reason (logged in candidates.json cut list and the plan log step "sanity_reject").
TOP_DECILE = 0.10   # Brett 2026-10-28 04:50 WITA: only top-10% ranked setups may ever ARM (manual_stage exempt); slots stay empty otherwise
TOP_N = 6   # Brett 2026-10-28 21:07 WITA: "Change rule to top 6" (replaces top decile for arm eligibility)
def arm_eligible(b): return bool(b.get("manual_stage")) or b.get("top_decile") is True
CAT_BONUS = 0.25   # catalyst flag score bonus (Brett 2026-10-28 04:50 WITA)
SANITY_MAX_ATR_PCT = 25.0; SANITY_MIN_PX = 5.0; SANITY_MAX_TRIG_DIST = 0.15
def sanity_guard(r):
    why = []; px = fnum(r.get("px")); a = fnum(r.get("atr_pct"))
    if a is None or a != a or a <= 0: why.append("sanity: ATR% uncomputable")
    elif a > SANITY_MAX_ATR_PCT: why.append(f"sanity: ATR% {a:.1f} > {SANITY_MAX_ATR_PCT:.0f}")
    if px is None or px != px or px <= 0: why.append("sanity: price missing")
    elif px < SANITY_MIN_PX and r.get("venue") == "ibkr": why.append(f"sanity: price {px:.2f} < ${SANITY_MIN_PX:.0f}")
    return why
def trigger_sanity(trigger, last_close):
    t = fnum(trigger); c = fnum(last_close)
    if t is None or c is None or c <= 0: return ["sanity: trigger/last close uncomputable"]
    d = abs(t / c - 1)
    return [f"sanity: trigger {t:.4g} is {d*100:.1f}% from last close {c:.4g} (> {SANITY_MAX_TRIG_DIST*100:.0f}%)"] if d > SANITY_MAX_TRIG_DIST else []
def quick_screen(r, direction):
    """Fast, no-network screen. [] = pass."""
    import event_calendar as EC, hunt48_room as HR
    why = list(EC.gate(r, direction, max_atr_pct=(EC.US_MAX_ATR_PCT if r.get("venue") == "ibkr" else None)))   # review fix #4: US ATR cap 8%
    if is_indonesian(r) and "Indonesian asset" not in why: why.append("Indonesian asset")
    why += sanity_guard(r)
    if direction == "short": why += HR.rsi_block(r)
    ed = fnum(r.get("earn_days"))
    if ed is not None and 0 <= ed <= EARN_BLOCK_D: why.append(f"earnings in {ed:.0f}d inside the hold + extension window ({EARN_BLOCK_D}d, audit H6)")
    # review fix #8: scan SI is a FRACTION (0.74 = 74%); old check (si > 25) never fired. Shorts: block SI > 20% or DTC > 10.
    si = si_pct(r.get("short_pct_float")); dtc = fnum(r.get("days_to_cover"))
    if direction == "short" and si is not None and si > SI_BLOCK_PCT: why.append(f"squeeze risk: SI {si:.0f}% float > {SI_BLOCK_PCT:.0f}%")
    if direction == "short" and dtc is not None and dtc > DTC_BLOCK: why.append(f"squeeze risk: {dtc:.1f} days to cover > {DTC_BLOCK:.0f}")
    roc5 = fnum(r.get("roc5"), 0); relv = fnum(r.get("relvol"), 1)
    if direction == "long" and roc5 > 10 and relv > 3: why.append(f"pump-chase: 5d +{roc5:.0f}% on {relv:.1f}x volume")
    return why
def analysis(r):
    """In-depth chart + catalyst read for the plan (all from the scan row; no fabrication: missing = None)."""
    g = lambda k: fnum(r.get(k))
    heads = (r.get("headlines") or "")[:300] or None
    return dict(trend=dict(stack=r.get("stack"), adx=g("adx"), macd_up=r.get("macd_up"), px=g("px"), m20=g("m20"), m50=g("m50"), m200=g("m200")),
                momentum=dict(rsi=g("rsi"), roc5=g("roc5"), roc20=g("roc20"), roc60=g("roc60"), rs20=g("rs20")),
                vol=dict(atr_pct=g("atr_pct"), relvol=g("relvol"), squeeze=r.get("squeeze"), nr7=r.get("nr7"), bbw=g("bbw")),
                structure=dict(hi20=g("hi20"), lo10=g("lo10"), pdh=g("pdh"), pdl=g("pdl"), breakout=r.get("breakout"), pullback=r.get("pullback")),
                catalyst=dict(cat_score=g("cat_score"), cat_notes=(r.get("cat_notes") or None), next_earn=r.get("next_earn") or None, earn_days=g("earn_days"),
                              last_surprise_pct=g("last_surprise_pct"), headlines=heads, insider_buys=r.get("insider_open_mkt_buys") or None, event_play=r.get("event_play") or None),
                positioning=dict(short_pct_float=g("short_pct_float"), days_to_cover=g("days_to_cover"), rec=r.get("rec") or None, tgt=g("tgt")),
                liquidity=dict(adv_usd=g("adv_usd") or g("vol24_usd"), cost_pct=g("cost_pct")))
# review fix #9 (hunt_review_20261027): event SIZING, not blocking - US plan shares x0.5 when the base hold spans FOMC,
# x0.75 when it spans a tier-2 macro event (hunt48_short_paper.hi_events: GDP/PCE/ECB). FOMC list = ranker config event_shield.
# Unreadable FOMC list -> assume FOMC (x0.5, fail safe). Multiplies with the DTC half-size (fix #8).
FOMC_MULT = 0.5; TIER2_MULT = 0.75
def event_size_mult(now, fomc=None, tier2=None, hold_end=None):
    import hunt48_short_paper as H
    notes = []
    try: end = hold_end or H.session_exit(now)
    except Exception: end = now + dt.timedelta(days=5)
    if fomc is None:
        try: fomc = [dt.datetime.fromisoformat(x) for x in json.load(open(os.path.join(BASE, "state", "calendar", "ranker", "config.json")))["event_shield"]["fomc"]]
        except Exception as e: return FOMC_MULT, [f"FOMC list unreadable ({str(e)[:40]}): assume FOMC x{FOMC_MULT}"]
    if tier2 is None:
        try: tier2 = H.hi_events()
        except Exception: tier2 = []
    f = [x for x in fomc if now <= x <= end]
    if f: return FOMC_MULT, [f"hold spans FOMC {f[0].astimezone(WITA).strftime('%a %d %b %H:%M WITA')}: x{FOMC_MULT}"]
    t2 = [(t, n) for t, n in tier2 if now <= t <= end]
    if t2: return TIER2_MULT, [f"hold spans {t2[0][1]}: x{TIER2_MULT}"]
    return 1.0, notes
def build_candidate(r, direction, audusd, bars, risk_aud=RISK_AUD, q2usd=None, us_room=False, now=None):
    """Levels (T2 = 1.5R) + B1 exhaustion + 1R room. Returns (cand, reasons_blocked).
    Review fix #5 (hunt_review_20261027): us_room=True (US hunt) applies the SAME room definition as arming/clearance
    (max_cost_R=HR.US_MAX_COST_R, min_bars=HR.US_MIN_BARS) so no backup is built that the engine will always refuse. OANDA unchanged."""
    import event_calendar as EC, hunt48_room as HR
    lv = EC.stage_levels(r, direction, risk_aud, audusd, quote_to_usd=q2usd, t2_r=HR.T2_R, atr_floor=(EC.STOP_ATR_FLOOR if us_room else None), fixed_pct=None)   # review fix #4; 2026-10-29 16:08 WITA Brett 16:05: US hunt R-based (stop=max(1 ATR floor, min(1.5 ATR, 5%)), T2=1.5R), fixed +3%/-8% dropped for new setups
    if not lv: return None, ["levels uncomputable (no quote->USD)"]
    sym = r["sym"]
    c = dict(ticker=sym, direction=direction, trigger=lv["trigger"], max_fill=lv["max_fill"], stop=lv["stop"], t1=lv["t1"], t2=lv["t2"],
             shares=lv["shares"], atr=lv["atr"], cost_R=lv["cost_R"], scan_px=fnum(r.get("px")), rank_score=fnum(r.get("rank_score"), 0),
             sector=r.get("sector") or r.get("cls") or None, venue=r.get("venue"))
    why = []
    if not c["stop"] or c["stop"] == c["max_fill"]: why.append("no valid stop")
    why += trigger_sanity(c["trigger"], c["scan_px"])
    if direction == "short": why += HR.exhaustion_block(r, bars)
    dtc = fnum(r.get("days_to_cover"))
    if direction == "short" and dtc is not None and dtc > DTC_HALF and c["shares"]:   # review fix #8: 0.5x size when DTC > 5 (risk A$1,250)
        c["shares"] = max(1, int(c["shares"] * 0.5)) if not isinstance(c["shares"], float) else round(c["shares"] * 0.5, 6)
        c["size_mult"] = 0.5; c["size_note"] = f"DTC {dtc:.1f} > {DTC_HALF:.0f}: half size"
    if us_room and c["shares"]:   # review fix #9: event sizing (US plan only)
        em, en = event_size_mult(now or now_utc())
        if em < 1.0:
            c["shares"] = max(1, int(c["shares"] * em)) if not isinstance(c["shares"], float) else round(c["shares"] * em, 6)
            c["size_mult"] = round(c.get("size_mult", 1.0) * em, 4); c["size_note"] = "; ".join(filter(None, [c.get("size_note"), *en]))
    rb = HR.room(c, bars, **(dict(max_cost_R=HR.US_MAX_COST_R, min_bars=HR.US_MIN_BARS) if us_room else {}))
    if rb: why.append(rb)
    c["score"] = round(c["rank_score"] + 0.25 * min(max(fnum(c.get("room_R"), 0), 0), 3), 3)
    c["analysis"] = analysis(r)
    return c, why
def mark_top_decile(cands, frac=None):
    """Top 10% (ceil, min 1) of the night's ranked candidate list by final score -> top_decile=True."""
    # Brett 2026-10-28 21:07 WITA "Change rule to top 6": top TOP_N ranked candidates per night per market (field name kept)
    ordered = sorted(cands, key=lambda x: -x["score"]); n_top = min(TOP_N, len(ordered)) if frac is None else (max(1, math.ceil(frac * len(ordered))) if ordered else 0)
    for i, c in enumerate(ordered, 1): c["rank_pos"] = i; c["rank_n"] = len(ordered); c["top_decile"] = i <= n_top
    return n_top


def pick(cands, holdings, n_slots, sector_of, max_open=US_MAIN_MAX_OPEN):
    """Rank-ordered selection. holdings: open + armed primaries [{ticker,direction,sector}].
    Primaries respect one-per-sector + direction balance against holdings and each other; the rest (distinct tickers)
    become backups in rank order (sector clash with a CURRENT holding is skipped; it is re-checked again at slot time)."""
    held = {h["ticker"] for h in holdings}; book = [dict(h) for h in holdings]; prim, back = [], []
    for c in sorted(cands, key=lambda x: -x["score"]):
        if c["ticker"] in held: continue
        sec = c.get("sector") or sector_of(c["ticker"]); c["sector"] = sec
        clash = next((h["ticker"] for h in book if (h.get("sector") or sector_of(h["ticker"])) == sec), None)
        same = sum(h.get("direction", "short") == c["direction"] for h in book)
        bal = len(book) >= max_open - 1 and same >= max_open - 1
        if len(prim) < n_slots and not clash and not bal:
            prim.append(c); book.append(c); held.add(c["ticker"]); continue
        if clash and any(h["ticker"] == clash for h in holdings): c["backup_note"] = f"sector {sec} held by {clash} now; re-checked at slot time"
        back.append(c); held.add(c["ticker"])
    return prim, back

def us_ledger(book): return jload(os.path.join(BASE, "state", US_BOOKS[book][0], "ledger.json"), {"trades": []})
def us_holdings(L):
    fil = [t for t in L["trades"] if t["status"] == "FILLED"]; arm = [t for t in L["trades"] if t["status"] in ("ARMED", "awaiting_funder") and not t.get("bench")]
    return fil, arm, [t for t in L["trades"] if t["status"] in ("ARMED", "awaiting_funder") and t.get("bench")]
def us_open_slots(L):
    fil, arm, _ = us_holdings(L); return max(0, US_MAIN_MAX_OPEN - len(fil) - len(arm))

def drop_bad_ohlcv_cache(out, log, min_syms=2000):
    """run_scan reuses ohlcv_<date>.pkl; a partial download (most tickers empty) must not be reused."""
    import glob
    for p in glob.glob(os.path.join(out, "ohlcv_*.pkl")):
        try:
            import pandas as pd
            n = int(pd.read_pickle(p)["Close"].notna().sum().gt(60).sum())
        except Exception: n = -1
        if n < min_syms:
            os.replace(p, p + f".bad_{n}"); log.append(dict(step="ohlcv_cache_dropped", file=os.path.basename(p), syms_with_bars=n))
def us_scan(D, log):
    """Fresh full US+Kraken universe -> state/calendar/inputs/ranked*.csv (previous copies kept in the run dir)."""
    out = os.path.join(ORCH, "us_scan", D); os.makedirs(out, exist_ok=True); new = os.path.join(out, "ranked.csv")
    for _ in range(90):   # another run_scan already writing this dir (e.g. a manual first run) -> wait for it, then reuse
        if subprocess.run(["pgrep", "-f", f"run_scan.py --out {out}"], capture_output=True).returncode != 0: break
        time.sleep(30)
    fresh = os.path.exists(new) and time.time() - os.path.getmtime(new) < 3 * 3600 and len(load_csv(new)) >= 1000
    if not fresh: drop_bad_ohlcv_cache(out, log)
    # isolated yfinance cache (HOME) so a concurrent yfinance process cannot lock the tz sqlite db mid-download (2026-10-27 failure)
    yh = os.path.join(ORCH, "yf_home"); os.makedirs(yh, exist_ok=True)
    rc, _ = (0, "reused") if fresh else sh([PY, "-W", "ignore", "state/calendar/ranker/run_scan.py", "--out", out, "--risk", str(int(RISK_AUD))], timeout=3000, log=log,
               env={"RANKER_DIR": "state/calendar/ranker", "HOME": yh})
    rows = load_csv(new)
    if rc != 0 or len(rows) < 1000: return False, f"scan rc={rc} rows={len(rows)}: kept previous ranked.csv"
    inp = os.path.join(BASE, "state", "calendar", "inputs")
    for f in ("ranked.csv", "ranked_all_incl_excluded.csv"):
        src = os.path.join(out, f)
        if not os.path.exists(src): continue
        if os.path.exists(os.path.join(inp, f)): shutil.copy2(os.path.join(inp, f), os.path.join(out, "prev_" + f))
        shutil.copy2(src, os.path.join(inp, f + ".orch_tmp")); os.replace(os.path.join(inp, f + ".orch_tmp"), os.path.join(inp, f))
    return True, f"{len(rows)} ranked rows installed"

def us_news_lift(c, feed=None, now=None):
    """2026-10-28 Brett 10:37: catalyst.py +0.5 (aligned, <=72h, no conflict) for US; total news lift = max(catalyst_capture flag 0.25, catalyst.py) <= 0.5."""
    try:
        import catalyst as CAT; nb, ninfo = CAT.bonus("us", c["ticker"], c.get("direction") or "long", c["score"], -1e9, now=now, feed=feed)
    except Exception: nb, ninfo = 0.0, None
    lift = max(CAT_BONUS if c.get("catalyst_flag") else 0.0, nb)
    if ninfo: c["catalyst"] = dict(ninfo, bonus=lift)
    if lift: c["score"] = round(c["score"] + lift, 3)
    return lift
def _truthy(x): return str(x).strip().lower() in ("true", "1", "1.0", "yes")
def shadow_hits(r, direction, shadow_setups):
    """Which SHADOW-ONLY setups a LONG scan row trips: 'breakout' = close >= 20d high (scan breakout flag; near-breakout excluded),
    'pullback' = scan pullback flag (uptrend, 5d roc < 0 near 20MA). Shorts are not affected."""
    if direction != "long" or not shadow_setups: return []
    return [k for k in shadow_setups if k in ("breakout", "pullback") and _truthy(r.get(k))]
def shadow_record(D, c, hits):
    try:
        d = os.path.join(ORCH, "shadow_setups"); os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{D}.jsonl"), "a") as f:
            f.write(json.dumps(dict(utc=now_utc().isoformat(), ticker=c.get("ticker"), direction=c.get("direction"), setups=hits,
                                    trigger=c.get("trigger"), stop=c.get("stop"), t1=c.get("t1"), t2=c.get("t2"), scan_px=c.get("scan_px"),
                                    book="SHADOW_ZERO_RISK", risk_aud=0), default=str) + "\n")
    except Exception as e: print("shadow_record failed:", e)
def us_build_plan(D, audusd, log, bars_fn=None, sector_of=None):
    """Main book plan_bench_<D>.json (longs + shorts, Brett 2026-10-27 20:56 WITA; direction = scan row dir): primaries for open
    slots + ranked backups (bench=True)."""
    import hunt48_room as HR, hunt48_short_paper as H
    bars_fn = bars_fn or HR.daily; sector_of = sector_of or H.sector_of
    rows = [r for r in load_csv(os.path.join(BASE, "state", "calendar", "inputs", "ranked.csv")) if r.get("venue") == "ibkr"]
    regime = "short" if (rows and sum(r.get("dir") == "short" for r in rows) > len(rows) / 2) else "long"
    pool, cut = [], []
    ordered = sorted(rows, key=lambda x: -fnum(x.get("rank_score"), 0)); n_top = min(TOP_N, len(ordered))
    pos = {r["sym"]: i for i, r in enumerate(ordered, 1)}
    for r in sorted(rows, key=lambda x: -fnum(x.get("rank_score"), 0)):
        d = r.get("dir")
        if d not in ("long", "short"): continue      # Brett 2026-10-27 20:56 WITA: main book takes longs and shorts
        why = quick_screen(r, d)
        if why: cut.append(dict(ticker=r["sym"], reason="; ".join(why)[:160])); continue
        pool.append(r)
        if len(pool) >= CAND_N: break
    cands = []
    for i, r in enumerate(pool, 1):
        c, why = build_candidate(r, r["dir"], audusd, bars_fn(r["sym"]), us_room=True)   # review fix #5: one room definition
        if c is None or why: cut.append(dict(ticker=r["sym"], reason="; ".join(why)[:160])); continue
        c["scan_rank"] = i; c["universe_pos"] = pos.get(r["sym"]); c["universe_n"] = len(ordered)
        cands.append(c)
    try:   # catalyst flag into scoring (Brett 2026-10-28 04:50 WITA): +CAT_BONUS when state/catalyst_capture/<D>.json flags the name
        import catalyst_capture as CC; cf = CC.load_flags(D)
    except Exception: cf = {}
    for c in cands:
        f = cf.get(c["ticker"]) or {}
        c["catalyst_flag"] = bool(f.get("catalyst_flag")); c["catalyst_why"] = f.get("catalyst_why") or []
        # 2026-10-28 Brett 10:37: same capped news catalyst as other markets (catalyst.py, +0.5 aligned, <=72h, no conflict).
        # Total news lift capped at +0.5 (max of the two, never stacked). Every US candidate here already passed all gates,
        # so the bonus only re-orders real setups - it cannot create one.
        us_news_lift(c)
    # Brett 2026-10-28 19:39 WITA: SHADOW-ONLY setups (state/hunt_orch/config.json shadow_setups) - logged outside P&L, never armed/benched
    rowmap = {r["sym"]: r for r in rows}; sh_list = cfg().get("shadow_setups") or []; live = []
    for c in cands:
        hits = shadow_hits(rowmap.get(c["ticker"], {}), c.get("direction"), sh_list)
        if hits: shadow_record(D, c, hits)
        else: live.append(c)
    if len(live) != len(cands): log.append(dict(step="shadow_setups", shadowed=[c["ticker"] for c in cands if c not in live], rules=sh_list))
    cands = live
    # Brett 2026-10-28 05:27 WITA: decile basis = this hunt's own ranked candidate list (per book, per night), min 1 name
    mark_top_decile(cands)
    L = us_ledger("main"); fil, arm, _ = us_holdings(L)
    hold = [dict(ticker=t["ticker"], direction=t.get("direction", "short"), sector=t.get("sector")) for t in fil + arm]
    prim, back = pick([c for c in cands if c["top_decile"]], hold, us_open_slots(L), sector_of)
    back += [c for c in sorted(cands, key=lambda x: -x["score"]) if not c["top_decile"]]   # ranked backups only: never arm
    keep = ("ticker", "trigger", "max_fill", "stop", "t1", "t2", "shares", "atr", "direction", "scan_px", "scan_rank", "sector", "top_decile", "rank_pos", "rank_n")
    for c in cands: c.setdefault("size_mult", 1.0)
    P = dict(label=L.get("book_label", "Our picks"), set=[dict({k: c[k] for k in keep}, bench=False) for c in prim] + [dict({k: c[k] for k in keep}, bench=True) for c in back[:MAX_BACKUPS + 4]],
             cut=[], note=f"hunt_orchestrator {D}: {len(prim)} primaries for {us_open_slots(L)} open slots + {len(back)} backups; T2 1.5R, room>=1R, one per sector, A${RISK_AUD:.0f} risk; regime {regime}")
    pth = os.path.join(BASE, "state", "hunt48_short", f"plan_bench_{D}.json")
    if os.path.exists(pth) and not os.path.exists(os.path.join(ORCH, "us_scan", D, "prev_plan_bench.json")):
        os.makedirs(os.path.join(ORCH, "us_scan", D), exist_ok=True); shutil.copy2(pth, os.path.join(ORCH, "us_scan", D, "prev_plan_bench.json"))
    atomic(pth, P)
    an = {c["ticker"]: c for c in cands}
    atomic(os.path.join(ORCH, "us_scan", D, "candidates.json"), dict(regime=regime, cands=cands, cut=cut[:200], pool=len(pool), rows=len(rows)))
    log.append(dict(step="sanity_reject", rejected=[x for x in cut if "sanity:" in x["reason"]][:200]))
    log.append(dict(step="plan_bench", primaries=[c["ticker"] for c in prim], backups=[c["ticker"] for c in back], cut=len(cut)))
    return P, an

def clearance_status(D, book="main"):
    S = jload(os.path.join(BASE, "state", "clearance", D, "summary.json"), {}) or {}
    ext = jload(os.path.join(BASE, "state", "clearance", D, "external_reviews.json"), {}) or {}
    out = {}
    for tk, v in (S.get("books", {}).get(book, {}) or {}).items():
        e = ext.get(book, {}).get(tk, {})
        out[tk] = dict(final=v.get("final"), internal=v.get("internal"), GPT=e.get("GPT"), CLAUDE=e.get("CLAUDE"), why=v.get("why"), setup_hash=v.get("setup_hash"))
    return out

def write_arm_file(book, specs, D, why):
    p = os.path.join(ORCH, "arm", D, f"{book}_{now_utc().strftime('%H%M%S')}.json")
    atomic(p, dict(set=[{k: s[k] for k in ("ticker", "trigger", "max_fill", "stop", "t1", "t2", "shares", "atr", "direction", "sector", "scan_px", "scan_rank", "cleared", "ai_gpt", "ai_claude") if k in s} | {"bench": False, "orch": why}
                        for s in specs], cut=[], note=f"hunt_orchestrator {D}: {why}"))
    return p
def wait_tick(ledger, max_s=150):
    """Apply right after the engine's 120s tick has written the ledger (narrows the tick/apply race; engine has no lock)."""
    m0 = os.path.getmtime(ledger) if os.path.exists(ledger) else 0; t0 = time.time()
    while time.time() - t0 < max_s:
        time.sleep(2)
        if os.path.exists(ledger) and os.path.getmtime(ledger) != m0: time.sleep(1); return True
    return False
def apply_arm(book, specs, D, why, log, wait=True):
    import hunt48_short_paper as H
    if not hasattr(H, "session_exit"): log.append(dict(step="arm", blocked="engine has no per-trade time exit (audit C1) - not arming")); return False
    if not specs: return False
    p = write_arm_file(book, specs, D, why); led = os.path.join(BASE, "state", US_BOOKS[book][0], "ledger.json")
    if wait: wait_tick(led)
    args = [PY, "-W", "ignore", "hunt48_short_paper.py", "apply-plan", p] + (["--book", book] if book != "main" else [])
    rc, out = sh(args, timeout=300, log=log)
    pend = jload(os.path.join(ORCH, "verify.json"), []) or []
    pend += [dict(book=book, ticker=s["ticker"], file=p, utc=now_utc().isoformat(timespec="seconds"), tries=1) for s in specs]
    atomic(os.path.join(ORCH, "verify.json"), pend)
    event("armed" if rc == 0 else "arm_failed", book=book, tickers=[s["ticker"] for s in specs], why=why, rc=rc)
    return rc == 0
def verify_arms(log):
    """A tick that loaded the ledger before apply-plan wrote it can overwrite the new ARMED row: re-apply once."""
    pend = jload(os.path.join(ORCH, "verify.json"), []) or []; keep = []
    for v in pend:
        age = (now_utc() - dt.datetime.fromisoformat(v["utc"])).total_seconds()
        if age < 260: keep.append(v); continue
        L = us_ledger(v["book"])
        if any(t["ticker"] == v["ticker"] for t in L["trades"]): continue
        if v["tries"] >= 2: event("arm_lost", **v); continue
        P = jload(v["file"], {}); sp = [s for s in P.get("set", []) if s["ticker"] == v["ticker"]]
        if sp and apply_arm(v["book"], sp, dkey(now_utc()), "re-apply after tick race", log, wait=True):
            pend2 = jload(os.path.join(ORCH, "verify.json"), []) or []
            for x in pend2:
                if x["ticker"] == v["ticker"] and x["book"] == v["book"]: x["tries"] = v["tries"] + 1
            keep += [x for x in pend2 if x["ticker"] == v["ticker"] and x["book"] == v["book"]]
    atomic(os.path.join(ORCH, "verify.json"), keep)

def us_backup_state(D):
    return jload(os.path.join(ORCH, f"us_backups_{D}.json"), None)
def recheck_us(b, L, px, daily, now):
    """Gates re-checked at slot time. [] = ok to arm."""
    import hunt48_short_paper as H
    why = []
    t = dict(b); t.setdefault("direction", "short")
    if px is None: why.append("no live price")
    elif H._past(t, px): why.append(f"missed: px {px:g} already past trigger {t['trigger']:g} by > 1/2 stop distance")
    elif (px >= t["stop"]) if t["direction"] == "short" else (px <= t["stop"]): why.append(f"invalidated: px {px:g} through stop {t['stop']:g}")
    fil, arm, _ = us_holdings(L)
    if any(x["ticker"] == t["ticker"] and x["status"] in ("ARMED", "FILLED", "awaiting_funder") for x in L["trades"]): why.append("already in book")
    if len(fil) + len(arm) >= US_MAIN_MAX_OPEN: why.append("no open slot")
    mg = H.mix_gate(t, fil + arm, US_MAIN_MAX_OPEN)
    if mg: why.append(mg)
    if daily:
        rc = H.room_check(t, daily)
        if not rc["ok"]: why.append(rc.get("why") or "room < 1R")
    else: why.append("no daily bars (fail closed)")
    return why
def reslot(now=None, log=None, px_fn=None, daily_fn=None):
    """US books: when FILLED + ARMED primaries < 3 and a cleared backup is valid -> arm it (one per call per book)."""
    import hunt48_short_paper as H
    now = now or now_utc(); log = log if log is not None else []; D = dkey(now); C = cfg()
    if not C.get("reslot", True): return []
    st = us_backup_state(D); done = []
    if not st: return done
    px_fn = px_fn or (lambda s: (H.bars_15m(s) or [{}])[-1].get("c"))
    daily_fn = daily_fn or H.bars_daily
    for book in C["auto_arm_us_books"]:
        B = st.get(book) or []
        L = us_ledger(book)
        if us_open_slots(L) <= 0: continue
        for b in B:
            if b.get("state") != "READY": continue
            if not arm_eligible(b): b["last_block"] = ["not top 6: never arms (Brett 2026-10-28 21:07)"]; continue
            try: px = px_fn(b["ticker"]); daily = daily_fn(b["ticker"])
            except Exception as e: px, daily = None, []
            why = recheck_us(b, L, px, daily, now)
            b["checked_wita"] = wita(now); b["check_px"] = px
            if why: b["state"] = "INVALID" if any(w.startswith(("missed", "invalidated", "already")) for w in why) else "READY"; b["last_block"] = why; 
            if why: continue
            arm_b = b
            if C.get("open_gates", True):
                if not gates_done(D): continue      # before the opening-range pass, backups wait (the gate layer promotes them)
                g = post_open_gates(b, now, C)
                b["gates"] = g["summary"]
                if not g["ok"]: b["state"] = "CANCELLED"; b["last_block"] = g["why"]; event("gate_cancel", book=book, ticker=b["ticker"], why=g["why"]); continue
                arm_b = g["new_t"]
            if apply_arm(book, [arm_b], D, f"re-slot backup #{b.get('rank')} into free slot (open gates passed)", log):
                b.update({k: arm_b[k] for k in ("trigger", "max_fill", "t1", "t2", "shares") if k in arm_b})
                b["state"] = "ARMED"; b["armed_wita"] = wita(now); done.append((book, b["ticker"])); break
        atomic(os.path.join(ORCH, f"us_backups_{D}.json"), st)
    return done


# ------------------------------------------------------------------ OPEN-GATE LAYER (Brett add-on 2026-10-27 20:01 WITA)
# (1) pre-market gap vs entry/stop  (5) relative strength vs index pre-market   -> at open - pregate_lead_min
# (2) opening-range break + volume  (7) opening spread <= 2x normal              -> at open + or_min (+2 min)
# Armed US trades are held off the engine until the opening range is complete (cut "OR wait" -> re-armed at the
# range-break level), so the engine can only fill on a break of the range. Cancelled setups promote the next backup.
def _og():
    import open_gates as OG
    OG.SPREAD_DIR = os.path.join(ORCH, "spreads"); return OG
def us_open_today(now):
    o = next_open("us", now)
    return o if o and abs((o - now).total_seconds()) < 12 * 3600 else None
def gates_marker(D, phase): return marker(f"gates_{phase}_us_{D}")
def gates_done(D): return os.path.exists(gates_marker(D, "or"))
def gates_state(D): return jload(os.path.join(ORCH, f"us_gates_{D}.json"), None) or {"held": [], "results": [], "pre_done": False, "or_done": False}
def save_gates(D, G): atomic(os.path.join(ORCH, f"us_gates_{D}.json"), G)
def apply_cut(book, tickers, D, why, log):
    if not tickers: return True
    p = os.path.join(ORCH, "arm", D, f"{book}_cut_{now_utc().strftime('%H%M%S')}.json")
    atomic(p, dict(set=[], cut=[dict(ticker=t, reason=f"hunt_orchestrator open gates {D}: {why}") for t in tickers], note=f"hunt_orchestrator open gates {D}: {why}"))   # gatecut 2026-10-30
    wait_tick(os.path.join(BASE, "state", US_BOOKS[book][0], "ledger.json"))
    rc, _ = sh([PY, "-W", "ignore", "hunt48_short_paper.py", "apply-plan", p] + (["--book", book] if book != "main" else []), timeout=300, log=log)
    event("gate_cut" if rc == 0 else "gate_cut_failed", book=book, tickers=list(tickers), why=why, rc=rc)
    return rc == 0
def _spec(t):
    return {k: t[k] for k in ("ticker", "trigger", "max_fill", "stop", "t1", "t2", "shares", "atr", "direction", "sector", "scan_px", "scan_rank", "score", "rank", "cleared") if k in t}
def pre_gates(t, open_utc, cache):
    import open_gates as OG
    return OG.pre_open_check(dict(t, direction=t.get("direction", "short")), open_utc, OG.INDEX["us"], cache)
def post_open_gates(b, now, C, open_utc=None, check_fn=None, rs_fn=None):
    """Backups promoted after the open: live gap/RS vs index since prior close, then OR break + volume + spread."""
    import hunt48_short_paper as H
    OG = _og(); open_utc = open_utc or us_open_today(now); t = dict(b, direction=b.get("direction", "short")); why = []
    rs = (rs_fn or _live_rs)(t)
    if not rs[0]: why.append(rs[1])
    oc = (check_fn or OG.open_check)(t, open_utc, RISK_AUD * H.live_audusd(0.70), C.get("or_min", OG.OR_MIN))
    why += oc.get("why", [])
    return dict(ok=not why, why=why, new_t=oc.get("new_t"), summary=dict(rs=rs[1] or "ok", vol_ratio=oc.get("vol_ratio"), spread_bp=oc.get("spread_bp"),
                normal_bp=oc.get("normal_bp"), spread_note=oc.get("spread_note"), or_=oc.get("or_"), checked=wita(now)))
def _live_rs(t):
    import open_gates as OG
    now = now_utc(); o = us_open_today(now)
    try:
        px = OG.intraday(t["ticker"], "5m", "1d")[-1]["c"]; ix = OG.intraday(OG.INDEX["us"], "5m", "1d")[-1]["c"]
        return OG.rs_gate(t, OG.pct(px, OG.prev_close(t["ticker"], o)), OG.pct(ix, OG.prev_close(OG.INDEX["us"], o)))
    except Exception as e: return False, f"RS data error (fail closed): {str(e)[:80]}"
def _occupied(book, D, st):
    L = us_ledger(book); fil, arm, _ = us_holdings(L)
    staged = [b for b in (st or {}).get(book, []) if b.get("state") in ("STAGED", "PRIMARY")]
    return L, fil, arm, staged
def promote(book, D, st, now, phase, open_utc, cache, log, pre_fn=None, post_fn=None):
    """Fill free slots (ledger FILLED+ARMED + STAGED < 3) from READY backups that pass recheck + the phase's gates."""
    import hunt48_short_paper as H
    out = []
    for b in st.get(book, []):
        L, fil, arm, staged = _occupied(book, D, st)
        if len(fil) + len(arm) + len(staged) >= (US_MAIN_MAX_OPEN if book == "main" else MAX_OPEN): break
        if b.get("state") != "READY": continue
        if not arm_eligible(b): b["last_block"] = ["not top 6: never arms (Brett 2026-10-28 21:07)"]; continue
        # Review fix #2 (hunt_review_20261027, Brett-approved): NO scan_px / synthetic trigger-stop midpoint substitute. A backup is
        # promoted only on a REAL last price (regular-session 15m bars; pre-open = last real print). No price -> recheck_us says
        # "no live price" -> stays READY (fail closed), never staged/armed on a made-up price that could already be through its stop.
        try: px = (H.bars_15m(b["ticker"]) or [{}])[-1].get("c"); daily = H.bars_daily(b["ticker"])
        except Exception: px, daily = None, []
        L2 = dict(L, trades=L["trades"] + [dict(s, status="ARMED") for s in staged])
        why = recheck_us(b, L2, px, daily, now)
        if why: b["state"] = "INVALID" if any(w.startswith(("missed", "invalidated", "already")) for w in why) else "READY"; b["last_block"] = why; continue
        if phase == "pre":
            g = (pre_fn or pre_gates)(b, open_utc, cache)
            b["gates"] = dict(pre=g)
            if not g["ok"]: b["state"] = "CANCELLED"; b["last_block"] = g["why"]; event("gate_cancel", book=book, ticker=b["ticker"], phase=phase, why=g["why"]); continue
            b["state"] = "STAGED"; b["promoted_wita"] = wita(now); out.append(b["ticker"]); event("gate_promote", book=book, ticker=b["ticker"], phase=phase)
        else:
            g = (post_fn or post_open_gates)(b, now, cfg(), open_utc)
            b["gates"] = dict(b.get("gates") or {}, post=g["summary"])
            if not g["ok"]: b["state"] = "CANCELLED"; b["last_block"] = g["why"]; event("gate_cancel", book=book, ticker=b["ticker"], phase=phase, why=g["why"]); continue
            if apply_arm(book, [_spec(g["new_t"])], D, f"backup #{b.get('rank')} promoted: open gates passed (OR break)", log):
                b.update({k: g["new_t"][k] for k in ("trigger", "max_fill", "t1", "t2", "shares") if k in g["new_t"]})
                b["state"] = "ARMED"; b["armed_wita"] = wita(now); out.append(b["ticker"])
    return out
def open_gate_us(now=None, log=None, pre_fn=None, open_fn=None, post_fn=None, cut_fn=None, arm_fn=None):
    """Called by cron every 5 min. Phase PRE once in [open-lead, open); phase OR once after open+or_min+2 (retries until bars complete)."""
    OG = _og()
    now = now or now_utc(); log = log if log is not None else []; C = cfg()
    if not C.get("open_gates", True): return "disabled"
    o = us_open_today(now)
    if not o: return "no session"
    D = dkey(o); st = us_backup_state(D) or {}; G = gates_state(D); cache = {}
    cut_fn = cut_fn or apply_cut; arm_fn = arm_fn or apply_arm
    lead = dt.timedelta(minutes=C.get("pregate_lead_min", 15)); orm = C.get("or_min", OG.OR_MIN)
    hunt_done = os.path.exists(marker(f"us_{o.date().isoformat()}"))
    if o - lead <= now < o + dt.timedelta(minutes=orm) and not G.get("pre_done") and (hunt_done or now >= o):
        for book in US_BOOKS:      # every US book: armed trades re-scored on the pre-market print, then held for the opening range
            L = us_ledger(book); arm = us_holdings(L)[1]      # live ARMED rows only (engine bench rows untouched)
            cancel, hold = [], []
            for t in arm:
                g = (pre_fn or pre_gates)(t, o, cache)
                G["results"].append(dict(book=book, ticker=t["ticker"], phase="pre", ok=g["ok"], why=g["why"], gap_R=g.get("gap_R"), chg=g.get("chg"), idx_chg=g.get("idx_chg")))
                (hold if g["ok"] else cancel).append(t)
            if cancel and cut_fn(book, [t["ticker"] for t in cancel], D, "open gates: pre-market gap/RS fail", log):
                for t in cancel: event("gate_cancel", book=book, ticker=t["ticker"], phase="pre")
            if hold and cut_fn(book, [t["ticker"] for t in hold], D, f"held for {orm}m opening range (re-armed on the range break)", log):
                G["held"] += [dict(book=book, t=_spec(t), state="HELD") for t in hold]
        for b in st.get("main", []):   # orchestrator-staged primaries
            if b.get("state") in ("STAGED", "PRIMARY"):
                g = (pre_fn or pre_gates)(b, o, cache); b["gates"] = dict(pre=g)
                G["results"].append(dict(book="main", ticker=b["ticker"], phase="pre", ok=g["ok"], why=g["why"], gap_R=g.get("gap_R"), chg=g.get("chg"), idx_chg=g.get("idx_chg")))
                if g["ok"]: b["state"] = "STAGED"
                else: b["state"] = "CANCELLED"; b["last_block"] = g["why"]; event("gate_cancel", book="main", ticker=b["ticker"], phase="pre", why=g["why"])
        if "main" in C["auto_arm_us_books"] and st.get("main"): promote("main", D, st, now, "pre", o, cache, log, pre_fn=pre_fn)
        G["pre_done"] = True; G["pre_wita"] = wita(now)
        if st: atomic(os.path.join(ORCH, f"us_backups_{D}.json"), st)
        save_gates(D, G); atomic(gates_marker(D, "pre"), dict(wita=wita(now)))
        return "pre"
    if now >= o + dt.timedelta(minutes=orm + 2) and now < o + dt.timedelta(hours=6) and not G.get("or_done"):
        import hunt48_short_paper as H
        risk = RISK_AUD * H.live_audusd(0.70); pending = False; armed = {}
        items = [(h["book"], h["t"], h) for h in G["held"] if h["state"] == "HELD"] + [("main", b, b) for b in st.get("main", []) if b.get("state") in ("STAGED", "PRIMARY") and arm_eligible(b)]
        for book, t, holder in items:
            if holder is t and "pre" not in (t.get("gates") or {}):   # staged after the pre-open pass (late hunt): live RS + OR gates
                pg = (post_fn or post_open_gates)(t, now, C, o)
                oc = dict(ok=pg["ok"], why=pg["why"], new_t=pg["new_t"], **{k: pg["summary"].get(k) for k in ("vol_ratio", "spread_bp", "normal_bp", "spread_note", "or_")})
            else:
                oc = (open_fn or OG.open_check)(dict(t, direction=t.get("direction", "short")), o, risk, orm)
            if oc.get("wait"): pending = True; continue
            G["results"].append(dict(book=book, ticker=t["ticker"], phase="or", ok=oc["ok"], why=oc["why"], vol_ratio=oc.get("vol_ratio"), spread_bp=oc.get("spread_bp"), normal_bp=oc.get("normal_bp"), spread_note=oc.get("spread_note"), or_=oc.get("or_")))
            if oc["ok"]: armed.setdefault(book, []).append((_spec(oc["new_t"]), holder, oc["new_t"]))
            else:
                holder["state"] = "CANCELLED"; holder["last_block"] = oc["why"]; event("gate_cancel", book=book, ticker=t["ticker"], phase="or", why=oc["why"])
        if pending and now < o + dt.timedelta(minutes=orm + 20): save_gates(D, G); return "or-wait"
        for book, lst in armed.items():
            if arm_fn(book, [x[0] for x in lst], D, f"open gates passed: entry on {orm}m opening-range break, volume + spread confirmed", log):
                for spec, holder, nt in lst:
                    holder.update({k: nt[k] for k in ("trigger", "max_fill", "t1", "t2", "shares") if k in nt}); holder["state"] = "ARMED"; holder["armed_wita"] = wita(now)
        if "main" in C["auto_arm_us_books"] and st.get("main"): promote("main", D, st, now, "or", o, cache, log, post_fn=post_fn)
        G["or_done"] = True; G["or_wita"] = wita(now)
        if st: atomic(os.path.join(ORCH, f"us_backups_{D}.json"), st)
        save_gates(D, G); atomic(gates_marker(D, "or"), dict(wita=wita(now)))
        return "or"
    return "idle"

# ---- engine books (ASX / Asia / LSE): the engines arm and fill themselves; this layer cannot hold or cut their trades
# without an engine hook, so it writes an advisory VETO file each session (state/hunt_orch/open_gate_<mkt>_<D>.json).
def engine_gate(mkt, now=None, open_fn=None, rs_fn=None):
    OG = _og()
    now = now or now_utc(); o = next_open(mkt, now); C = cfg(); orm = C.get("or_min", OG.OR_MIN)
    if not o or not (o + dt.timedelta(minutes=orm + 2) <= now < o + dt.timedelta(hours=5)): return None
    D = dkey(o); p = os.path.join(ORCH, f"open_gate_{mkt}_{D}.json")
    if os.path.exists(p): return None
    L = jload(os.path.join(BASE, "state", f"hunt48_{mkt}", "ledger.json"), {"trades": []}) or {"trades": []}
    res = []
    for t in [t for t in L.get("trades", []) if t.get("status") == "ARMED"]:
        sym = t.get("yf") or t.get("sym"); tt = dict(t, ticker=sym, direction=t.get("direction", "short"))
        try:
            bars = OG.intraday(sym, "5m", "30d"); first = [b for b in bars if b["start"] >= o][:1]
            auction = first[0]["o"] if first else None
            ok1, w1, gR = OG.gap_gate(tt, auction)
            rs = (rs_fn or (lambda x: OG.rs_gate(x, OG.pct(auction, OG.prev_close(sym, o)), OG.pct((OG.intraday(OG.INDEX[mkt], "5m", "1d") or [{}])[0].get("o"), OG.prev_close(OG.INDEX[mkt], o)))))(tt)
            oc = (open_fn or OG.open_check)(tt, o, 1e9, orm, bars_fn=lambda s: bars)
            why = ([] if ok1 else [w1]) + ([] if rs[0] else [rs[1]]) + oc.get("why", [])
        except Exception as e: why, gR, oc = [f"data error: {str(e)[:80]}"], None, {}
        res.append(dict(sym=t.get("sym"), veto=bool(why), why=why, gap_R=gR, vol_ratio=oc.get("vol_ratio"), spread_bp=oc.get("spread_bp"), or_=oc.get("or_")))
    atomic(p, dict(market=mkt, open_wita=wita(o), checked_wita=wita(now), advisory=True, results=res,
                   hook="ENGINE HOOK NEEDED: engine to skip/cut ARMED rows listed veto=true and promote its next CLEARED backup"))
    if any(r["veto"] for r in res): event("engine_gate_veto_advisory", market=mkt, syms=[r["sym"] for r in res if r["veto"]])
    return res

def hunt_us(now, log):
    import hunt48_short_paper as H
    D = dkey(now); C = cfg()
    ok, note = us_scan(D, log); log.append(dict(step="us_scan", ok=ok, note=note))
    try:   # review fix #3: scan crash / kraken_pairs 0 / empty cat_notes / dead feed -> state/hunt_orch/feed_alarms.json (local only)
        import feed_alarms as FA; fa = FA.raise_alarms(ORCH, "us", D, FA.check_us_scan(D, ok, note, ORCH)); log.append(dict(step="feed_alarms", n=fa["n"]))
    except Exception as e: log.append(dict(step="feed_alarms", error=str(e)[:120]))
    try:   # review fix #7: catalyst x relvol SHADOW rank, frozen point-in-time daily (never used for selection)
        import shadow_rank as SR; log.append(dict(step="shadow_rank", file=SR.build(D, os.path.join(ORCH, "us_scan", D, "ranked.csv"), os.path.join(ORCH, "shadow_rank"))))
    except Exception as e: log.append(dict(step="shadow_rank", error=str(e)[:120]))
    try:   # catalyst capture (Brett 2026-10-28 04:50 WITA): fresh pool flags before the plan; free yfinance + EDGAR, never blocks
        rc0, _ = sh([PY, "-W", "ignore", "catalyst_capture.py"], timeout=900, log=log); log.append(dict(step="catalyst_capture", rc=rc0))
    except Exception as e: log.append(dict(step="catalyst_capture", error=str(e)[:120]))
    audusd = H.live_audusd(0.70)
    P, an = us_build_plan(D, audusd, log)
    rc, _ = sh([PY, "-W", "ignore", "hunt_clearance.py", "run"], timeout=2400, log=log)
    cl = clearance_status(D, "main")
    ranked = sorted([s for s in P["set"]], key=lambda s: -an.get(s["ticker"], {}).get("score", 0))
    for s in ranked: s.update({k: an[s["ticker"]].get(k) for k in ("score", "room_R", "room_basis", "cost_R", "analysis") if s["ticker"] in an})
    # review fix #2: CLEARED counts only for the exact levels reviewed today (setup hash), and the row carries cleared=D so the
    # engine expires it after that NY session (hunt48_short_paper.clearance_expired).
    import setup_hash as SH
    cleared = [dict(s, cleared=D) for s in ranked if (cl.get(s["ticker"]) or {}).get("final") == "CLEARED"
               and (cl.get(s["ticker"]) or {}).get("setup_hash") and cl[s["ticker"]]["setup_hash"] == SH.setup_hash(s)]
    for s in ranked:
        c0 = cl.get(s["ticker"]) or {}
        if c0.get("final") == "CLEARED" and c0.get("setup_hash") != SH.setup_hash(s): c0["final"] = "BLOCKED"; c0["hash_block"] = f"setup hash {c0.get('setup_hash')} != plan {SH.setup_hash(s)}"
    L = us_ledger("main"); slots = us_open_slots(L)
    fil, arm, _ = us_holdings(L)
    hold = [dict(ticker=t["ticker"], direction=t.get("direction", "short"), sector=t.get("sector")) for t in fil + arm]
    # Brett 2026-10-27 23:36 WITA: a hand-staged main-book setup (manual_stage=True, state STAGED, written into
    # us_backups_<D>.json before this hunt) is carried over, not overwritten. It takes a slot first, under the same
    # one-per-sector + direction-balance rule (pick); a clash or no slot -> CANCELLED. It is never armed here: the
    # open-gate layer runs gap/RS pre-open and the OR/volume/spread checks before apply_arm, like any STAGED primary.
    man = [dict(x, score=x.get("score") or 1e9) for x in ((us_backup_state(D) or {}).get("main") or [])
           if x.get("manual_stage") and x.get("state") in ("STAGED", "PRIMARY")]
    mprim, mrest = pick([dict(m) for m in man], hold, slots, H.sector_of) if man else ([], [])
    for m in mrest: m.update(state="CANCELLED", last_block=[m.get("backup_note") or "manual stage: no free slot / sector or direction clash at hunt time"])
    for m in mprim: m["state"] = "STAGED"
    hold += [dict(ticker=m["ticker"], direction=m.get("direction", "short"), sector=m.get("sector")) for m in mprim]
    slots = max(0, slots - len(mprim))
    prim, back = pick([dict(c) for c in cleared if c["ticker"] not in {m["ticker"] for m in man} and arm_eligible(c)], hold, slots, H.sector_of)
    back += [dict(c, arm_note="not top decile: ranked backup only, never arms") for c in cleared if c["ticker"] not in {m["ticker"] for m in man} and not arm_eligible(c)]
    st = {"main": mprim + mrest}
    if man: log.append(dict(step="manual_stage_carry", staged=[m["ticker"] for m in mprim], cancelled=[m["ticker"] for m in mrest]))
    for i, s in enumerate(prim + back, len(st["main"]) + 1):
        st["main"].append(dict(s, rank=i, state="PRIMARY" if s in prim else "READY", clearance=cl.get(s["ticker"])))
    for s in ranked:
        if s["ticker"] not in {x["ticker"] for x in st["main"]}:
            st["main"].append(dict(s, rank=None, state="BLOCKED", clearance=cl.get(s["ticker"])))
    if C.get("open_gates", True):   # Brett add-on 2026-10-27: primaries are STAGED; the open-gate layer arms after the opening range
        for x in st["main"]:
            if x["state"] == "PRIMARY": x["state"] = "STAGED"
    elif C.get("us_arm", True) and "main" in C["auto_arm_us_books"] and prim:
        if apply_arm("main", prim, D, "daily pre-open hunt: top cleared setups into open slots", log):
            for x in st["main"]:
                if x["state"] == "PRIMARY": x["state"] = "ARMED"; x["armed_wita"] = wita(now_utc())
    atomic(os.path.join(ORCH, f"us_backups_{D}.json"), st)
    try:   # review fix #1: freeze today's funnel output + code version for the forward shadow cohort (offline, never blocks the hunt)
        import edge_cohort; log.append(dict(step="edge_cohort", file=edge_cohort.record(D, os.path.dirname(os.path.dirname(ORCH)))))
    except Exception as e: log.append(dict(step="edge_cohort", error=str(e)[:120]))
    log.append(dict(step="arm_main", slots=slots, armed=[s["ticker"] for s in prim], backups_ready=[s["ticker"] for s in back], cleared=len(cleared), clearance_rc=rc))

def hunt_asx(now, log):
    env = {"RANKER_DIR": "state/calendar/ranker"}
    sh([PY, "-W", "ignore", "universe_asx.py"], timeout=1200, env=env, log=log)
    sh([PY, "-W", "ignore", "asx_hunt48.py"], timeout=900, log=log)
    sh([PY, "-W", "ignore", "hunt_clearance.py", "run", "--book", "asx"], timeout=1800, log=log)
    sh([PY, "-W", "ignore", "hunt_clearance.py", "finalize", "--book", "asx"], timeout=300, log=log)
    sh([PY, "-W", "ignore", "asx_hunt48.py"], timeout=900, log=log)
def asia_chain_ran(mkt, now):
    p = os.path.join(BASE, "state", "clearance", dkey(now), mkt, "summary.json")
    if not os.path.exists(p): return False
    return dt.datetime.fromtimestamp(os.path.getmtime(p), UTC) > now - dt.timedelta(hours=6)
def hunt_asia(mkt, now, log):
    if asia_chain_ran(mkt, now): log.append(dict(step="preopen_chain", note="existing chain already ran this morning - not re-run")); return
    if os.path.exists(os.path.join(BASE, "state", f"hunt48_{mkt}", "preopen.lock")): log.append(dict(step="preopen_chain", note="existing chain running now")); return
    sh(["/bin/bash", os.path.join("state", f"hunt48_{mkt}", f"preopen_{mkt}.sh")], timeout=2400, log=log)

def hunt(mkt, now=None):
    now = now or now_utc(); log = []; t, dl, k = run_at(mkt, now)
    lock = os.path.join(ORCH, "lock_" + mkt); os.makedirs(ORCH, exist_ok=True)
    open(lock, "w").write(str(os.getpid()))
    try:
        event("hunt_start", market=mkt, key=k)
        if mkt == "us": hunt_us(now, log)
        elif mkt == "asx": hunt_asx(now, log)
        elif mkt in ASIA: hunt_asia(mkt, now, log)
        elif mkt == "fx":
            try: os.remove(os.path.join(ORCH, "fx_cands.json"))
            except Exception: pass
        plan(mkt, now)
        atomic(marker(k), dict(market=mkt, started_wita=wita(now), finished_wita=wita(now_utc()), log=log))
        event("hunt_done", market=mkt, key=k, steps=len(log))
    except Exception as e:
        event("hunt_error", market=mkt, err=str(e)[:300]); log.append(dict(error=str(e)[:300]))
        try:   # review fix #3: a hunt crash is an alarm, not just an .err marker
            import feed_alarms as FA; FA.raise_alarms(ORCH, mkt, dkey(now), [dict(code="hunt_crash", msg=str(e)[:200])])
        except Exception: pass
        atomic(marker(k) + ".err", dict(market=mkt, log=log))
    finally:
        try: os.remove(lock)
        except Exception: pass
    return log

# ------------------------------------------------------------------ daily plans (read-only on engines)
def _cov(name):
    return jload(os.path.join(BASE, "state", "calendar", "inputs", f"ranked_{name}_report.json"), {}) or {}
def plan_us(book, now):
    D = dkey(now); L = us_ledger(book); fil, arm, bench = us_holdings(L); st = (us_backup_state(D) or {}).get(book)
    rep = jload(os.path.join(ORCH, "us_scan", D, "scan_report.json"), {}) or {}
    cov = dict(rep.get("universe") or {}, source="run_scan.py (NASDAQ+NYSE listed + Kraken)", run_at=rep.get("run_at"))
    if st is None:
        st = [dict(ticker=t["ticker"], direction=t.get("direction", "short"), trigger=t["trigger"], stop=t["stop"], state="ENGINE_BENCH", rank=i)
              for i, t in enumerate(bench, 1)]
    backups = [b for b in st if b.get("state") in ("READY", "ENGINE_BENCH")]
    note = None if book in cfg()["auto_arm_us_books"] else "comparison book: its own picker arms it; orchestrator reports only"
    G = gates_state(D)
    gates = dict(pre_done=G.get("pre_done"), pre_wita=G.get("pre_wita"), or_done=G.get("or_done"), or_wita=G.get("or_wita"),
                 results=[r for r in G.get("results", []) if r.get("book") == book][-12:], held=[h["t"]["ticker"] + ":" + h["state"] for h in G.get("held", []) if h["book"] == book])
    return dict(armed=[_t(t) for t in arm], open=[_t(t) for t in fil], backups=[_b(b) for b in backups], blocked=[_b(b) for b in st if b.get("state") in ("BLOCKED", "INVALID", "CANCELLED")][:10],
                staged=[_b(b) for b in st if b.get("state") in ("STAGED", "PRIMARY")], max_open=(US_MAIN_MAX_OPEN if book == "main" else MAX_OPEN), coverage=cov, note=note, open_gates=gates)
def _t(t): return dict(sym=t.get("ticker") or t.get("sym"), dir=(t.get("direction") or "short")[:1].upper(), trigger=t.get("trigger"), stop=t.get("stop"),
                      t2=t.get("t2"), entry=t.get("entry"), status=t.get("status"), sector=t.get("sector"))
def _b(b):
    a = b.get("analysis") or {}
    cl = b.get("clearance") or {}
    return dict(rank=b.get("rank"), sym=b.get("ticker") or b.get("sym"), dir=(b.get("direction") or "short")[:1].upper(), trigger=b.get("trigger"), stop=b.get("stop"),
                t2=b.get("t2"), score=b.get("score"), room_R=b.get("room_R"), sector=b.get("sector"), state=b.get("state"),
                gate=("CLEARED" if cl.get("final") == "CLEARED" else cl.get("final") or b.get("gate")), why=(b.get("last_block") or cl.get("why") or b.get("why")),
                catalyst=(a.get("catalyst") or {}).get("cat_notes") or b.get("catalyst"))
def plan_engine(mkt, now):
    """ASX / Asia / LSE engines: armed + open from the ledger; backups = never-demoted BENCH ranked (clearance PASS + room>=1R first)."""
    L = jload(os.path.join(BASE, "state", f"hunt48_{mkt}", "ledger.json"), {"trades": []}) or {"trades": []}
    T = L.get("trades", [])
    fil = [t for t in T if t["status"] == "FILLED"]; arm = [t for t in T if t["status"] == "ARMED"]
    def valid(t):
        note = (t.get("bench_note") or "")
        return not (t.get("demoted_utc") or t.get("promoted_utc") or "DEMOTED" in note.upper())
    def gate(t):
        c = t.get("clearance") or {}
        if c.get("desk") not in (None, "PENDING", "PASS"): return "DESK_FAIL"
        if fnum(t.get("room_R"), 0) < 1.0: return "ROOM<1R"
        return "CLEARED" if c.get("desk") == "PASS" else "AWAIT_CLEARANCE"
    bench = [t for t in T if t["status"] == "BENCH" and valid(t)]
    order = {"CLEARED": 0, "AWAIT_CLEARANCE": 1, "ROOM<1R": 2, "DESK_FAIL": 3}
    bench.sort(key=lambda t: (order[gate(t)], -fnum(t.get("score"), 0)))
    backups = [dict(rank=i, sym=t["sym"], dir=t.get("direction", "short")[:1].upper(), trigger=t.get("trigger"), stop=t.get("stop"), t2=t.get("t2"),
                    score=t.get("score"), room_R=t.get("room_R"), sector=t.get("sector"), state="READY" if gate(t) == "CLEARED" else "WAIT", gate=gate(t),
                    why=(t.get("bench_note") or "")[:140]) for i, t in enumerate(bench, 1)]
    rep = _cov(mkt)
    cov = {k: rep.get(k) for k in ("listed", "with_bars", "scored", "adv_ge_floor", "adv_ge_2m_aud", "shortable_in_universe", "run_at", "regime", "regime_asx", "indonesian_excluded") if k in rep}
    note = dict(asx="engine auto-promotes a CLEARED backup into a free slot (same-day desk PASS); demoted names never re-armed",
                lse="London build owns this book; read-only here").get(mkt, "engine arms only same-day desk PASS before the open (arming law); intraday re-slot = engine change")
    return dict(armed=[_t(dict(t, ticker=t["sym"])) for t in arm], open=[_t(dict(t, ticker=t["sym"])) for t in fil], backups=[b for b in backups if b["state"] == "READY"] + [b for b in backups if b["state"] != "READY"],
                max_open=MAX_OPEN, coverage=cov, note=note)
FX_CACHE_S = 3600
def plan_fx(now, bars_fn=None):
    """FX/CFD candidates are cached for an hour (cron runs every 5 min); the daily fx hunt forces a refresh."""
    import event_calendar as EC, hunt48_room as HR
    rows = load_csv(os.path.join(BASE, "state", "calendar", "inputs", "ranked_oanda.csv")); R = {r["sym"]: r for r in rows}
    snap = jload(os.path.join(BASE, "state", "oanda", "snapshot.json"), {}) or {}; pend = jload(os.path.join(BASE, "state", "oanda", "pending.json"), {}) or {}
    ocfg = jload(os.path.join(BASE, "state", "oanda", "config.json"), {}) or {}
    mx = int(ocfg.get("max_trades", OANDA_MAX)); nav = fnum(snap.get("nav"), 0) or fnum(ocfg.get("start_nav_aud"), 50000)
    opn = [t for t in snap.get("trades", []) if t.get("owner") == "TFO"]; q = pend.get("queue", []) or []
    held = {t.get("instrument") for t in opn} | {x.get("ins") for x in q}
    q2 = EC.make_q2usd(R); audusd = fnum((R.get("AUD_USD") or {}).get("px"), 0.70)
    cp = os.path.join(ORCH, "fx_cands.json"); C = jload(cp, None)
    if C and time.time() - fnum(C.get("ts"), 0) < FX_CACHE_S and bars_fn is None: cands = [c for c in C["cands"] if c["ticker"] not in held]
    else: cands = None
    bars_fn = bars_fn or HR.daily
    for r in ([] if cands is not None else sorted(rows, key=lambda x: -fnum(x.get("rank_score"), 0))):
        if cands is None: cands = []
        if r["sym"] in held: continue
        d = r.get("dir") or "long"
        if quick_screen(r, d): continue
        sym_y = r.get("yahoo") or None
        c, why = build_candidate(r, d, audusd, bars_fn(sym_y) if sym_y else [], risk_aud=round(nav * OANDA_RISK, 2), q2usd=q2)
        if c is None or why: continue
        c["sector"] = r.get("cls"); c["ccys"] = r["sym"].split("_"); cands.append(c)
        if len(cands) >= MAX_BACKUPS + 4: break
    cands = cands or []; cands.sort(key=lambda c: -c["score"])
    if C is None or time.time() - fnum(C.get("ts"), 0) >= FX_CACHE_S: atomic(cp, dict(ts=time.time(), wita=wita(now), cands=cands))
    backups = [dict(_b(dict(c, state="READY")), rank=i, units=c["shares"], gate="GATES PASS (oanda_sleeve re-checks its own caps)") for i, c in enumerate(cands, 1)]
    return dict(armed=[dict(sym=x.get("ins"), dir="L" if x.get("dir", 1) > 0 else "S", trigger=x.get("trigger"), stop=None, status="QUEUED", design=x.get("design"), expires_utc=x.get("expires_utc")) for x in q],
                open=[dict(sym=t.get("instrument"), dir="L" if fnum(t.get("units"), 0) > 0 else "S", status="OPEN", entry=t.get("price") or t.get("entry")) for t in opn],
                backups=backups, max_open=mx, risk_pct=OANDA_RISK * 100,
                coverage=dict(instruments=len(rows), by_class={c: sum(r.get("cls") == c for r in rows) for c in sorted({r.get("cls") for r in rows})}, shadow_only=["fx_exotic", "soft"]),
                note=f"oanda_sleeve.py owns entries (its designs, 1% risk, max {mx}, caps). Backups are the orchestrator's ranked gate-passing setups; feeding them into the sleeve = engine change.")
def plan_crypto(now):
    import event_calendar as EC
    ht = jload(os.path.join(BASE, "reports", "tfo_ht_crypto.json"), {}) or {}; ee = jload(os.path.join(BASE, "state", "entry_engine", "latest.json"), {}) or {}
    rows = [r for r in load_csv(os.path.join(BASE, "state", "calendar", "inputs", "ranked.csv")) if r.get("venue") == "kraken"]
    regime = "long" if any(r.get("dir") == "long" for r in rows) else "short"
    backups = []
    for x in (ee.get("read") or []):
        rej = [y[0] for y in (x.get("rejects") or [])]
        backups.append(dict(sym=x.get("name"), dir="L", state="WAIT", gate=("READY" if not rej else ",".join(rej[:3])), score=-len(rej), live_px=x.get("live_px"),
                            why="; ".join(y[1] for y in (x.get("rejects") or [])[:2])[:160]))
    for r in rows:
        why = quick_screen(r, "long")
        backups.append(dict(sym=r["sym"], dir="L", state="WAIT", gate="GATES PASS" if not why else "; ".join(why)[:80], score=fnum(r.get("rank_score"), 0) - 10, why=r.get("dir_note")))
    backups.sort(key=lambda b: -fnum(b.get("score"), -99))
    for i, b in enumerate(backups, 1): b["rank"] = i
    pos = ht.get("positions") or {}
    return dict(armed=[dict(sym=k, status="RESTING") for k in (ht.get("resting_entries") or [])][:5], open=[dict(sym=k, status="OPEN") for k in pos], backups=backups[:MAX_BACKUPS + 4],
                max_open=MAX_OPEN, coverage=dict(kraken_screened=len(ee.get("screen") or []) + len(ee.get("screen_dropped") or []), kraken_ranked=len(rows), entry_engine_scan=ee.get("utc")),
                note=f"Kraken spot: longs only; regime {regime}. Entry engine (com.tfo.entry-engine) owns triggers/fills (max 3/day); orchestrator ranks the watch list.")

def book_plan(key, now):
    if key.startswith("us_"): return plan_us(key[3:], now)
    if key == "oanda": return plan_fx(now)
    if key == "crypto": return plan_crypto(now)
    return plan_engine(key, now)
MKT_KEYS = dict(us=["us_main", "us_gpt", "us_claude"], asx=["asx"], jp=["jp"], hk=["hk"], sg=["sg"], kr=["kr"], tw=["tw"], lse=["lse"], fx=["oanda"], crypto=["crypto"])
KEY_MKT = {k: m for m, ks in MKT_KEYS.items() for k in ks}
def plan(mkt="all", now=None):
    now = now or now_utc(); D = dkey(now)
    S = jload(STATUS, {}) or {}; S.setdefault("books", {})
    keys = [k for m, ks in MKT_KEYS.items() for k in ks] if mkt == "all" else MKT_KEYS[mkt]
    sched = {r["market"]: r for r in schedule(now)}
    for k in keys:
        try: p = book_plan(k, now)
        except Exception as e: p = dict(armed=[], open=[], backups=[], max_open=MAX_OPEN, error=str(e)[:200])
        m = KEY_MKT[k]; mx = p.get("max_open", MAX_OPEN)
        p.update(book=k, market=m, date=D, paper_only=True, generated_wita=wita(now), next_hunt_wita=sched[m]["hunt_wita"], open_or_deadline_wita=sched[m]["open_or_deadline_wita"],
                 open_slots=max(0, mx - len(p["open"]) - len(p["armed"]) - len(p.get("staged") or [])), backups_ready=sum(b.get("state") == "READY" for b in p["backups"]),
                 last_hunt=(jload(marker(sched[m]["key"]), {}) or {}).get("finished_wita"))
        p["thin_bench"] = p["backups_ready"] < MIN_BACKUPS
        atomic(os.path.join(PLAN_DIR, D, f"{k}.json"), p)
        open(os.path.join(PLAN_DIR, D, f"{k}.md"), "w").write(md(p))
        S["books"][k] = {kk: p.get(kk) for kk in ("book", "market", "generated_wita", "next_hunt_wita", "open_or_deadline_wita", "last_hunt", "max_open", "open_slots", "backups_ready", "thin_bench", "note", "error")} | \
                        dict(armed=p["armed"][:6], open=p["open"][:10], backups=p["backups"][:MAX_BACKUPS], plan=f"reports/daily_plan/{D}/{k}.md",
                             staged=(p.get("staged") or [])[:3], open_gates=p.get("open_gates"))
    S.update(paper_only=True, updated_wita=wita(now), updated_utc=now.isoformat(timespec="seconds"), schedule=list(sched.values()),
             rules="max 3 open/book, one per sector, A$2.5k risk, stop on every trade, room>=1R with T2 1.5R; OANDA 1% risk max 10; >=5 ranked backups; no Indonesian assets, no pump-chasing")
    atomic(STATUS, S); return S
def md(p):
    L = [f"# Daily plan {p['book']} ({p['market']}) {p['date']} - PAPER", f"Generated {p['generated_wita']}. Next hunt {p['next_hunt_wita']}; open/deadline {p['open_or_deadline_wita']}.",
         f"Open {len(p['open'])}, armed {len(p['armed'])}, max {p['max_open']} -> **{p['open_slots']} open slot(s)**. Ready backups {p['backups_ready']}" + (" (THIN: < 5)" if p["thin_bench"] else ""), ""]
    if p.get("note"): L.append(f"_{p['note']}_\n")
    if p.get("coverage"): L.append("Coverage: " + json.dumps(p["coverage"], default=str)[:600] + "\n")
    for name in ("open", "armed"):
        L.append(f"## {name.title()}"); L += [f"- {x.get('sym')} {x.get('dir', '')} {x.get('status', '')} trig {x.get('trigger')} stop {x.get('stop')} entry {x.get('entry')}" for x in p[name]] or ["- none"]
    if p.get("staged"): L.append("## Staged (armed after the opening-range gates)"); L += [f"- {b.get('sym')} {b.get('dir')} trig {b.get('trigger')} stop {b.get('stop')} T2 {b.get('t2')}" for b in p["staged"]]
    g = p.get("open_gates")
    if g:
        L.append(f"## Open gates (gap / RS vs index / opening-range break + volume / spread)\nPre-open pass: {g.get('pre_wita') or 'pending'}; opening-range pass: {g.get('or_wita') or 'pending'}")
        L += [f"- {r.get('ticker')} [{r.get('phase')}] {'PASS' if r.get('ok') else 'CANCEL'} {'; '.join(r.get('why') or [])}" for r in g.get("results", [])]
    L.append("## Backups (ranked)")
    L += [f"{b.get('rank')}. {b.get('sym')} {b.get('dir')} [{b.get('state')}/{b.get('gate')}] trig {b.get('trigger')} stop {b.get('stop')} T2 {b.get('t2')} room {b.get('room_R')}R score {b.get('score')} {b.get('sector') or ''} {('- ' + str(b.get('why'))[:120]) if b.get('why') else ''}" for b in p["backups"][:15]] or ["- none"]
    return "\n".join(L) + "\n"

# ------------------------------------------------------------------ cron
def spawn(mkt):
    os.makedirs(os.path.join(ORCH, "logs"), exist_ok=True)
    f = open(os.path.join(ORCH, "logs", f"hunt_{mkt}_{dkey(now_utc())}.log"), "a")
    open(os.path.join(ORCH, "lock_" + mkt), "w").write("spawning")
    subprocess.Popen([PY, "-W", "ignore", os.path.abspath(__file__), "hunt", mkt], cwd=BASE, stdout=f, stderr=f, start_new_session=True)
def clear_stale_locks(max_h=2):
    for m in MKT_KEYS:
        p = os.path.join(ORCH, "lock_" + m)
        if os.path.exists(p) and time.time() - os.path.getmtime(p) > max_h * 3600: os.remove(p); event("stale_lock_cleared", market=m)
def cron():
    now = now_utc(); clear_stale_locks(); started = []
    for m in due(now): spawn(m); started.append(m)
    log = []
    try: verify_arms(log)
    except Exception as e: event("verify_error", err=str(e)[:200])
    try:
        r = open_gate_us(now, log)
        if r not in ("idle", "no session", "disabled"): event("open_gate_us", phase=r)
    except Exception as e: event("open_gate_error", err=str(e)[:300])
    for m in ("asx",) + ASIA + ("lse",):
        try: engine_gate(m, now)
        except Exception as e: event("engine_gate_error", market=m, err=str(e)[:200])
    try:
        import hunt48_short_paper as H
        if H.us_rth(now) or os.path.exists(marker(run_at("us", now)[2])): reslot(now, log)
    except Exception as e: event("reslot_error", err=str(e)[:200])
    plan("all", now)
    if started: event("cron_spawned", markets=started)
    return started

if __name__ == "__main__":
    a = sys.argv[1:] or ["plan"]
    if a[0] == "cron": print(json.dumps(dict(spawned=cron())))
    elif a[0] == "hunt": print(json.dumps(hunt(a[1]), indent=1, default=str)[-4000:])
    elif a[0] == "plan": S = plan(a[1] if len(a) > 1 else "all"); print(json.dumps({k: dict(open_slots=v["open_slots"], backups_ready=v["backups_ready"], armed=len(v["armed"]), open=len(v["open"])) for k, v in S["books"].items()}, indent=1))
    elif a[0] == "reslot": print(reslot())
    elif a[0] == "gates": print(open_gate_us())
    elif a[0] == "schedule": print("\n".join(f"{r['market']:7s} hunt {r['hunt_wita']}  open/deadline {r['open_or_deadline_wita']}  {'DONE' if r['done'] else ''}" for r in schedule()))
    else: print(__doc__)
