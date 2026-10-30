#!/usr/bin/env python3
"""HUNT48 PAPER SHORT ENGINE (Brett approval 2026-10-26 22:26 WITA: "build the paper short path and arm GL, SLV and IBKR").
PAPER ONLY. Separate from every long route (tfo_buy / auto_fire / swing_paper are NOT imported or called).
Never touches a broker API: market data only (yfinance 15m + daily bars, read-only). Own state only:
  state/hunt48_short/ledger.json, state/hunt48_short/events.jsonl, static/hunt48_short_status.json
Rules per armed trade:
  ENTRY  a COMPLETED 15-min bar (regular session) closes < trigger AND close >= max_fill -> short at max(close*(1-slip), max_fill)
         a close below max_fill does not fill (no chase); trade stays armed. If a close is past the trigger by more than
         half the stop distance before any fill -> MISSED (never chased).
  STOP   buy-to-cover when a 15m bar high >= stop (fill = max(stop, bar open) * (1+slip)).
  T1     half covered at t1 (+slip); stop moves to entry, then 2xATR trail from the lowest low since entry.
  +5%    trail tightens to 1xATR (price <= entry*0.95). Remainder rides the trail (T2 shown as guide).
  TIME   time_exit (Thu 29 Oct 22:00 WITA): if P&L>0 AND ((close<10dMA AND ADX rising) OR new 5d low) extend 1 trading
         day at a time (max 10 trading days), else cover at the bar close.
COSTS  (2026-10-27) Brett's real IBKR schedule via ibkr_costs.py: Pro FIXED US$0.005/sh min $1 max 1% + SEC/CAT (+TAF) pass-throughs,
       5 bp slippage/side (unchanged), borrow = IBKR's published per-name fee (short-stock file + gateway tick 236), accrued per hour.
       A short with no IBKR borrow available goes to SHADOW with reason 'no borrow'.
P&L    A$ after costs at the live AUDUSD (fallback the ledger's armed rate).
CLI: hunt48_short_paper.py tick | status | arm-default | apply-plan PLAN.json   [--book NAME for a comparison ledger]
"""
import datetime as dt, json, math, os, sys, tempfile, time
import causal_guard
import copy
import ibkr_costs as IC
# 2026-10-28 Brett B3: hunt scripts honour the desk base path (env DESK_BASE, default = this folder)
BASE = os.environ.get("DESK_BASE", os.path.dirname(os.path.abspath(__file__)))
# --book NAME selects a separate comparison ledger (e.g. claude): state/hunt48_short_NAME/, static/hunt48_short_NAME_status.json
BOOK = sys.argv[sys.argv.index("--book") + 1].lower() if "--book" in sys.argv else ""
_SFX = ("_" + BOOK) if BOOK else ""
SD = os.path.join(BASE, "state", "hunt48_short" + _SFX); LEDGER = os.path.join(SD, "ledger.json"); EVENTS = os.path.join(SD, "events.jsonl")
STATUS = os.path.join(BASE, "static", "hunt48_short" + _SFX + "_status.json")
UTC = dt.timezone.utc; WITA = dt.timezone(dt.timedelta(hours=8))
COMM_USD = 1.0; SLIP = 0.0005; BORROW_PA = 0.03; MAX_EXT_DAYS = 10
MAIN_BOOK = BOOK in ("", "main")
# Brett 2026-10-29 20:17 WITA (brett_override_20261029_2017): "Raise the hunt cap to A$5k a trade and add a 4th slot" - US MAIN hunt book only; gpt/claude books keep 3 / A$2.5k
HUNT_RISK_AUD = 5000.0 if MAIN_BOOK else 2500.0
MAX_OPEN = 4 if MAIN_BOOK else 3   # was 3: Brett 2026-10-27 01:24 WITA: max 3 open at once; first triggers take free slots; the rest are logged SHADOW (no fill)

def now_utc(): return dt.datetime.now(UTC)
# EVENT-AWARE TIME EXIT (2026-10-27, GPT calendar review): if a time exit falls within 30 min of a high-impact event
# (ECB decision/presser, US GDP/PCE) move it to 45 min before that event; repeat until clear of every such event.
EVENT_GUARD_MIN, EVENT_LEAD_MIN = 30, 45
HI_EVENTS_STATIC = [  # verified vs official pages 2026-10-27 (BEA schedule, ECB press pages) - used if the calendar file is missing
    ("2026-10-29T12:30:00+00:00", "US GDP adv Q3 + PCE Sep (BEA 08:30 ET)"),
    ("2026-10-29T13:15:00+00:00", "ECB decision (14:15 CET)"),
    ("2026-10-29T13:45:00+00:00", "ECB press conference (14:45 CET)")]
import re as _re
def hi_events(base=BASE):
    evs = [(dt.datetime.fromisoformat(u), n) for u, n in HI_EVENTS_STATIC]
    try:
        C = json.load(open(os.path.join(base, "static", "event_calendar.json")))
        for e in C.get("next72", []):
            if e.get("sym") == "MACRO" and e.get("type") == "macro_high" and e.get("when_utc") and _re.search(r"ECB|Refinancing|Monetary Policy Statement|GDP|PCE", e.get("title", "")):
                evs.append((dt.datetime.fromisoformat(e["when_utc"]), e["title"][:60]))
    except Exception: pass
    return sorted(set(evs))
def event_safe_exit(te, evs):
    """Returns (new_te, reasons). Moves te to EVENT_LEAD_MIN before any event within +-EVENT_GUARD_MIN; iterates."""
    why = []
    for _ in range(20):
        hit = [(t, n) for t, n in evs if abs((te - t).total_seconds()) <= EVENT_GUARD_MIN * 60]
        if not hit: break
        t, n = min(hit); te = t - dt.timedelta(minutes=EVENT_LEAD_MIN); why.append(f"{n} @ {t.astimezone(WITA).strftime('%a %H:%M WITA')}")
    return te, why
from zoneinfo import ZoneInfo as _ZI
_NY = _ZI("America/New_York")
def prior_close_exit(te, evs):
    """Head trader 2026-10-27 04:36: if the scheduled exit day (NY date) has a high-impact event before/at the US open (09:30 ET),
    exit at the PRIOR session's close (15:55 ET, last ~5 min). Repeats if that day has the same problem."""
    why = []
    for _ in range(7):
        d = te.astimezone(_NY).date()
        pre = [(t, n) for t, n in evs if t.astimezone(_NY).date() == d and t.astimezone(_NY).time() <= dt.time(9, 30)]
        if not pre: break
        p = d - dt.timedelta(days=1)
        while not ny_session(p): p -= dt.timedelta(days=1)   # audit C1 hardening: NYSE holidays skipped too
        te = ny_close_utc(p)                                  # 15:55 ET, 12:55 ET on early-close days
        why.append("pre-open " + "; ".join(f"{n} @ {t.astimezone(WITA).strftime('%a %H:%M WITA')}" for t, n in pre) + " -> prior close")
    return te, why
def apply_event_exit(L, evs, now):
    """Adjust each open/armed trade's time_exit_utc once per exit value; records original + reason. Never moves an exit already passed."""
    out = []
    for t in L["trades"]:
        if t["status"] not in ("ARMED", "FILLED", "PARTIAL") or (t.get("_te_checked") == t["time_exit_utc"] and t.get("_te_rule") == "v2"): continue
        base = t["time_exit_orig_utc"] if (t.get("_te_rule") != "v2" and t.get("time_exit_orig_utc") and not t.get("ext_days")) else t["time_exit_utc"]
        te = dt.datetime.fromisoformat(base); nte, why = prior_close_exit(te, evs); nte, why2 = event_safe_exit(nte, evs); why += why2
        t["_te_rule"] = "v2"
        if not why and base != t["time_exit_utc"]: why = ["rule v2 recompute"]
        if why and nte > now:
            t.setdefault("time_exit_orig_utc", t["time_exit_utc"]); t["time_exit_utc"] = nte.isoformat()
            t["time_exit_adj"] = dict(from_utc=te.isoformat(), to_utc=nte.isoformat(), events=why,
                note=("pre-US-open: executes at first RTH tick (~09:30 ET)" if nte.astimezone(_NY).time() < dt.time(9, 30) else ""))
            out.append((t["ticker"], te.isoformat(), nte.isoformat()))
        t["_te_checked"] = t["time_exit_utc"]
    return out


def wita(t): return t.astimezone(WITA).strftime("%a %d %b %H:%M WITA")
def atomic(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path)); 
    with os.fdopen(fd, "w") as f: json.dump(obj, f, indent=1, default=str)
    os.replace(tmp, path)
def event(kind, **kw):
    os.makedirs(SD, exist_ok=True)
    with open(EVENTS, "a") as f: f.write(json.dumps(dict(utc=now_utc().isoformat(timespec="seconds"), kind=kind, **kw), default=str) + "\n")

DEFAULT_TRADES = [
 dict(ticker="GL",  trigger=153.94, max_fill=153.705, stop=158.92, t1=150.86, t2=146.24, shares=336, atr=3.9871),
 dict(ticker="SLV", trigger=47.82,  max_fill=47.75,   stop=49.67,  t1=46.87,  t2=45.43,  shares=915, atr=1.475),
 dict(ticker="IBKR",trigger=80.0599,max_fill=79.9398, stop=83.8233,t1=78.4587,t2=76.0569,shares=452, atr=3.0107),
]
TIME_EXIT_UTC = "2026-10-29T14:00:00+00:00"   # Thu 29 Oct 22:00 WITA - LEGACY: arm-default only (the original GL/SLV/IBKR set)
# AUDIT FIX C1 (Brett approved 2026-10-27 19:35 WITA): every trade's time exit is set AT FILL as fill + HOLD_SESSIONS New York
# sessions, at that session's close (15:55 ET; 12:55 ET on early-close days). Weekends + NYSE holidays skipped; DST via zoneinfo.
# The exit stamped at arm is provisional only. A trade whose exit has already passed is refused (never armed / never filled).
TIME_STOP_SESSIONS = 1   # bankfinal 17:44: close of session 2 (fill session + 1) = 48h hunt hold; fixed at entry
HOLD_SESSIONS = 3   # ~72h: matches the original 48-72h hold (Mon fill -> Thu close)
NYSE_HOLIDAYS = {"2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
                 "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24", "2028-01-17", "2028-02-21", "2028-04-14", "2028-05-29",
                 "2028-06-19", "2028-07-04", "2028-09-04", "2028-11-23", "2028-12-25"}
NYSE_EARLY = {"2026-11-27", "2026-12-24", "2027-11-26", "2028-07-03", "2028-11-24"}   # 13:00 ET close
def ny_session(d): return d.weekday() < 5 and d.isoformat() not in NYSE_HOLIDAYS
def ny_close_utc(d):
    return dt.datetime.combine(d, dt.time(12, 55) if d.isoformat() in NYSE_EARLY else dt.time(15, 55), _NY).astimezone(UTC)
# Brett 2026-10-28 20:59 WITA approved hunt fix 1: thesis exit never on the FIRST 15m bar of a NY session, and only on a
# completed 15m close beyond the broken level + 0.5 ATR (ATR missing -> |entry-stop|/1.5 proxy). Hard stop / T1 / trail unchanged.
THESIS_ATR_BUF = 0.5
def first_bar_of_session(start_utc):
    s = start_utc.astimezone(_NY); return s.time() == dt.time(9, 30)
def thesis_buf(t):
    a = t.get("atr")
    if not (isinstance(a, (int, float)) and a > 0): a = abs(float(t["entry"]) - float(t["stop"])) / 1.5
    return THESIS_ATR_BUF * a
def session_exit(fill_utc, n=None):
    """Close of the n-th NY session after the fill's session (fill outside a session counts from the next one)."""
    n = HOLD_SESSIONS if n is None else n
    f = fill_utc.astimezone(_NY); d = f.date()
    while not ny_session(d) or f > dt.datetime.combine(d, dt.time(16, 0), _NY): d += dt.timedelta(days=1); f = dt.datetime.combine(d, dt.time(9, 30), _NY)
    for _ in range(n):
        d += dt.timedelta(days=1)
        while not ny_session(d): d += dt.timedelta(days=1)
    return ny_close_utc(d)
class ArmRefused(Exception): pass
# AUDIT FIX H6 (Brett approved 2026-10-27 19:35 WITA): no hunt48 trade may be filled, or extended, when the company's earnings fall
# before time_exit + MAX_EXT_DAYS NY sessions (the longest the engine could hold it). Source: the trade's next_earnings_utc (from
# clearance) else the newest state/clearance/<date>/<TICKER>.json. UNKNOWN (ETFs, no data) = no block. The extension block applies to
# trades whose exit was stamped at fill by the C1 path (time_exit_rule 'NY sessions'); positions open before this fix are left as they were (Brett: do not alter them).
EARN_DIR = os.path.join(BASE, "state", "clearance")
# AUDIT H6 HARDENING (Brett 27 Oct 2026 21:11 WITA): a PAST date (yfinance often returns the last report) is not a next-earnings date;
# keep scanning older clearance files when one is empty/UNKNOWN/past/corrupt. No future date anywhere = UNKNOWN (None).
def _earn_dt(v):
    if not v or str(v).upper() == "UNKNOWN": return None
    v = str(v); e = dt.datetime.fromisoformat(v if "T" in v else v + "T12:00:00+00:00")
    return e if e.tzinfo else e.replace(tzinfo=UTC)
# EARNINGS POST-REPORT (Brett approved 27 Oct 2026 ~23:20 WITA): once a report is PUBLISHED (post-earnings, catalyst confirmed) the
# name may arm/fill. A report counts as published only for an EXACT timestamp at least REPORT_GRACE_H hours in the past (the same 2h
# grace event_calendar gives scheduled stamps: AMC '16:00 ET' stamps print a few minutes later). Earnings still UPCOMING inside the
# hold window keep blocking exactly as before. Date-only dates (session unknown, could be after the close) are never 'published' here:
# they keep the old [now-12h, horizon] block (fail closed).
REPORT_GRACE_H = 2
def _published(v, e, now):
    return e is not None and "T" in str(v) and e + dt.timedelta(hours=REPORT_GRACE_H) <= now
def _earn_scan(t, now, want_published):
    floor = now - dt.timedelta(hours=12)
    v = t.get("next_earnings_utc"); e = _earn_dt(v)
    if e and e >= floor and _published(v, e, now) == want_published: return e
    try: days = sorted(os.listdir(EARN_DIR), reverse=True)[:8]
    except Exception: days = []
    for d in days:
        p = os.path.join(EARN_DIR, d, t["ticker"] + ".json")
        try:
            v = (json.load(open(p)).get("calendar") or {}).get("next_earnings_utc") if os.path.exists(p) else None
            e = _earn_dt(v)
        except Exception: e = None
        if e and e >= floor and _published(v, e, now) == want_published: return e
    return None
def next_earnings(t, now=None):
    """Next PENDING earnings (not yet published) on or after now-12h, else None. A just-published report is skipped (post-earnings)."""
    return _earn_scan(t, now or now_utc(), False)
def recent_report(t, now=None):
    """An earnings report already PUBLISHED inside the last 12h (post-earnings, catalyst confirmed), else None."""
    return _earn_scan(t, now or now_utc(), True)
def earnings_horizon(te):
    d = te.astimezone(_NY).date()
    for _ in range(MAX_EXT_DAYS):
        d += dt.timedelta(days=1)
        while not ny_session(d): d += dt.timedelta(days=1)
    return ny_close_utc(d)
def earnings_status(t, now):
    """('block', reason) if PENDING earnings fall inside [now-12h, time_exit + MAX_EXT_DAYS sessions]; ('clear', None|post-report note);
    ('unknown', why). A report already published (post-earnings) never blocks. May raise."""
    e = next_earnings(t, now)
    if not e:
        r = recent_report(t, now)
        if r: return "clear", f"post-earnings: reported {r.isoformat(timespec='minutes')} (published, catalyst confirmed)"
        return "unknown", "no future earnings date on file"
    hz = earnings_horizon(dt.datetime.fromisoformat(t["time_exit_utc"]))
    if now - dt.timedelta(hours=12) <= e and e.astimezone(_NY).date() <= hz.astimezone(_NY).date():
        return "block", f"earnings {e.date()} before time exit + {MAX_EXT_DAYS} sessions ({hz.astimezone(_NY).date()})"
    return "clear", None
def earnings_block(t, now):
    """Reason string if PENDING earnings fall inside [now-12h, time_exit + MAX_EXT_DAYS sessions], else None (post-report = None)."""
    try: s_, r = earnings_status(t, now)
    except Exception: return None
    return r if s_ == "block" else None
def _is_equity(t):
    sec = str(t.get("sector") or "")
    return not (t["ticker"].upper() in COMMODITY_ETFS or sec == "COMMODITIES" or sec.startswith("ETF"))
def extension_earnings_veto(t, now):
    """Reason the time-exit extension is refused on earnings grounds, else None.
    Positions without a C1 fill stamp ('NY sessions' rule) are untouched (Brett: existing positions unchanged).
    AUDIT H6 HARDENING: trades carrying the fill-time earnings stamp (every new engine fill) fail CLOSED:
    an equity with UNKNOWN earnings, or an earnings-check error, gets no extension. ETFs/commodity funds are exempt from UNKNOWN."""
    if "NY sessions" not in str(t.get("time_exit_rule") or ""): return None
    stamped = bool(t.get("earnings_at_fill"))
    try: s_, r = earnings_status(t, now)
    except Exception as e:
        return ("earnings check error: " + str(e))[:120] if stamped else None
    if s_ == "block": return r
    if s_ == "unknown" and stamped and _is_equity(t): return "earnings unknown (equity): no extension"
    return None
def stamp_exit_at_fill(t, fill_utc):
    te = session_exit(fill_utc)
    t["time_exit_utc"] = te.isoformat(); t["time_exit_rule"] = f"fill + {HOLD_SESSIONS} NY sessions (audit C1)"
    for k in ("time_exit_orig_utc", "time_exit_adj", "_te_checked"): t.pop(k, None)
    t["_te_rule"] = "v2"
    return te

def arm_default(audusd=0.7022, risk_aud=2500.0, now=None):
    te = session_exit(now or now_utc()).isoformat()   # audit C1 hardening: provisional exit on the NY calendar (re-stamped at fill)
    L = dict(book="HUNT48_SHORT_PAPER", paper_only=True, sleeve_aud=1_000_000, approved="Brett 2026-10-26 22:26 WITA",
             fomc_half_size=True, borrow_assumption_pa=BORROW_PA, audusd_armed=audusd,
             dropped=[dict(ticker="QQQ", reason="dropped by Brett 22:26 WITA; replaced by IBKR")], trades=[])
    for i, t in enumerate(DEFAULT_TRADES, 1):
        L["trades"].append(dict(id=i, direction="short", status="ARMED", risk_cap_aud=risk_aud, time_exit_utc=te,
            armed_utc=now_utc().isoformat(timespec="seconds"), entry=None, entry_utc=None, open_qty=0, half_done=False,
            trail=None, lowest=None, ext_days=0, realised_usd=0.0, fees_usd=0.0, borrow_usd=0.0, exits=[], last_px=None, **t))
    return L

# ---------------------------------------------------------------- pure logic (unit-tested)
def cut_trade(L, ticker, reason, now):
    for t in L["trades"]:
        if t["ticker"] == ticker and t["status"] == "ARMED":
            t.update(status="CUT", cut_reason=reason, cut_utc=now.isoformat(timespec="seconds"), cut_wita=wita(now)); return t
    raise SystemExit(f"{ticker}: not an ARMED trade; refusing to cut")

def fixed_levels(spec, d, L, risk_aud):
    """Brett 2026-10-28 07:12 WITA: every NEW/re-levelled ARMED hunt trade (main, claude, gpt) = target +3% / stop -8% from max_fill
    (shorts -3% / +8%); T1 = T2 = target (full exit); shares so the 8% stop risks risk_aud (x size_mult). Open/FILLED trades untouched."""
    import hunt48_room as HR
    e = spec.get("max_fill")
    if not e: return spec
    sg = 1 if d == "long" else -1; e = float(e)
    if spec.get("stop") is not None and spec.get("t2") is not None and HR.is_fixed(e, float(spec["stop"]), float(spec["t2"])): return spec
    fx = float(L.get("audusd") or 0.7022); sm = float(spec.get("size_mult") or 1.0)
    sh = max(0, int(risk_aud * fx * sm // (HR.FIXED_STOP_PCT * e)))
    return dict(spec, stop=round(e * (1 - sg * HR.FIXED_STOP_PCT), 4), t1=round(e * (1 + sg * HR.FIXED_TP_PCT), 4), t2=round(e * (1 + sg * HR.FIXED_TP_PCT), 4),
                shares=sh, levels_rule="fixed +3%/-8% (Brett 2026-10-28)", levels_orig={k: spec.get(k) for k in ("stop", "t1", "t2", "shares")})
def upsert_trade(L, spec, now, risk_aud=None):
    """Re-level an ARMED or BENCH trade (fill clock restarts) or add a new ARMED one. Never touches a FILLED trade.
    audit H7 (Brett 27 Oct 19:35 WITA): BENCH rows are re-levellable (back to ARMED, relevel_from=BENCH); SHADOW rows are terminal
    for the engine (tick skips them), so a re-staged SHADOW name gets a fresh ARMED row and the SHADOW record is kept."""
    if risk_aud is None: risk_aud = HUNT_RISK_AUD
    for t in L["trades"]:
        if t["ticker"] == spec["ticker"] and t["status"] not in ("CUT", "MISSED", "CLOSED", "STOPPED", "SHADOW"):
            if t["status"] not in ("ARMED", "BENCH"): raise SystemExit(f"{t['ticker']} is {t['status']}; refusing to re-level")
            spec = {k: v for k, v in spec.items() if k != "direction"}
            if spec.get("time_exit_utc") and dt.datetime.fromisoformat(spec["time_exit_utc"]) <= now:
                raise ArmRefused(f"{t['ticker']}: time exit {spec['time_exit_utc']} already passed; refusing to re-level")
            t.setdefault("level_history", []).append({k: t.get(k) for k in ("trigger", "max_fill", "stop", "t1", "t2", "shares")})
            if t["status"] == "BENCH": t.update(status="ARMED", relevel_from="BENCH")
            t.update(armed_utc=now.isoformat(timespec="seconds"), **spec); return t
    spec = dict(spec); d = spec.pop("direction", "short").lower()
    # Brett 2026-10-27 20:56 WITA: the main book may take longs as well as shorts (was: longs only in --book comparison books).
    # Unchanged: A$2.5k risk, max 3 open, one per sector + direction balance (mix_gate/slot_gate), room/cost gates, clearance.
    if d not in ("long", "short"): raise SystemExit(f"direction must be long or short, got {d!r}")
    te = spec.pop("time_exit_utc", None)   # audit C1: no duplicate kwarg; provisional exit, re-stamped at fill
    if te and dt.datetime.fromisoformat(te) <= now: raise ArmRefused(f"{spec['ticker']}: time exit {te} already passed; refusing to arm")
    te = te or session_exit(now).isoformat()
    t = dict(id=max([x["id"] for x in L["trades"]] or [0]) + 1, direction=d, status="ARMED", risk_cap_aud=risk_aud,
             time_exit_utc=te, time_exit_rule="provisional; set at fill", armed_utc=now.isoformat(timespec="seconds"), entry=None, entry_utc=None, open_qty=0,
             half_done=False, trail=None, lowest=None, ext_days=0, realised_usd=0.0, fees_usd=0.0, borrow_usd=0.0, exits=[], last_px=None, **spec)
    L["trades"].append(t); return t

def new_ledger(label, audusd=0.7022):
    return dict(book="HUNT48_SHORT_PAPER" + (("_" + BOOK.upper()) if BOOK else ""), book_label=label, paper_only=True, sleeve_aud=1_000_000,
                borrow_assumption_pa=BORROW_PA, audusd_armed=audusd, dropped=[], trades=[])

def _long(t): return t.get("direction", "short") == "long"
def _past(t, px):
    """price beyond the trigger (in the trade's direction) by more than half the stop distance."""
    if _long(t): return px > t["trigger"] + 0.5 * (t["trigger"] - t["stop"])
    return px < t["trigger"] - 0.5 * (t["stop"] - t["trigger"])

def pre_check_missed(t, px):
    """Live price already past the trigger by more than half the stop distance -> MISSED (no chase)."""
    if px is not None and _past(t, px):
        t["status"] = "MISSED"; t["missed_px"] = px; return True
    return False

# Review fix #2 (hunt_review_20261027, Brett-approved): clearance is SAME-DAY. A row cleared on WITA day D (hunt_clearance /
# hunt_orchestrator stamp cleared=D) is good for that night's NY session only: it expires at D+1 08:00 WITA (00:00 UTC, after the
# 16:00 ET close + after-hours in both EDT and EST). Expired -> refused at apply-plan; an unfilled ARMED row is CUT. Rows without
# a cleared stamp (hand-staged / legacy) are untouched.
def clearance_expiry(t):
    c = str(t.get("cleared") or "")
    if not (len(c) == 8 and c.isdigit()): return None
    return dt.datetime(int(c[:4]), int(c[4:6]), int(c[6:]), 8, 0, tzinfo=WITA) + dt.timedelta(days=1)
def clearance_expired(t, now):
    x = clearance_expiry(t); return bool(x) and now >= x
# Brett 2026-10-28 20:59 WITA approved hunt fix 3: a BENCH name fills only after the same full clearance as primaries
# (hunt_clearance final CLEARED = desk PASS + no AI FAIL + >=1 AI PASS, on the exact setup hash). Valid same-day `cleared` stamp,
# or a CLEARED row in state/clearance/<D>/summary.json for this book (D = WITA clearance day covering `now`). Fail closed.
CLEARANCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "clearance")
def bench_clearance(t, now):
    """-> (ok, why). Primaries are not checked here."""
    if t.get("cleared") and not clearance_expired(t, now): return True, f"cleared stamp {t['cleared']}"
    D = (now.astimezone(WITA) - dt.timedelta(hours=8)).strftime("%Y%m%d")   # clearance day D is valid until D+1 08:00 WITA
    try: S = json.load(open(os.path.join(CLEARANCE_DIR, D, "summary.json")))
    except Exception: return False, f"no clearance summary {D}"
    r = (S.get("books", {}).get(BOOK or "main") or {}).get(t["ticker"])
    if not r: return False, f"not reviewed in clearance {D}"
    if r.get("final") != "CLEARED": return False, f"clearance {D}: {r.get('final')} {'; '.join(r.get('why') or [])[:160]}"
    try:
        import setup_hash as _SH; hz = _SH.setup_hash(t)
    except Exception as e: return False, f"setup hash unavailable: {e}"
    if r.get("setup_hash") and hz != r["setup_hash"]: return False, f"setup hash {hz} != cleared {r['setup_hash']}"
    return True, f"clearance {D} CLEARED"
# tfo_usbook_cap_20261030 (Brett 30 Oct 19:35 WITA, PAPER): same US beta cap as VD/THF on the MAIN book (us_book_cap.py).
# Arm: a NEW or re-levelled ARMED row is refused when it would take main-book net US beta past 40% of main-book equity.
# Fill: re-checked at the fill (the row's own ARMED entry excluded); a breach keeps it ARMED (no position) and retries next tick.
# Never touches FILLED trades or exits. US_CAP_FN is the test seam.
US_CAP_FN = None
def _hunt_book_label(): return "Hunt short" + (("_" + BOOK) if BOOK else "")
def _us_cap(sym, usd, side, exclude, others):
    if US_CAP_FN: return US_CAP_FN(sym, usd, side, exclude, others)
    import us_book_cap; return us_book_cap.check(sym, usd, side, book=_hunt_book_label(), exclude=exclude, others_usd=others, accumulate=False)
def us_cap_arm_block(sp, L, run):
    """None if allowed (run[0] += leg), else reason. Re-level of an existing ARMED row excludes that row's old size."""
    tk = sp.get("ticker"); px = sp.get("max_fill") or sp.get("trigger"); sh = sp.get("shares")
    if not tk or not px or not sh: return None
    ex = next((x for x in L["trades"] if x["ticker"] == tk and x["status"] in ("ARMED", "BENCH")), None)
    side = (sp.get("direction") or (ex or {}).get("direction") or "short").lower()
    ok, why, info = _us_cap(tk, float(px) * float(sh), side, [(_hunt_book_label(), tk)] if ex else [], run[0])
    if not ok: return why
    run[0] += float((info or {}).get("new_usd") or 0); return None
def us_cap_fill_block(t):
    ok, why, _ = _us_cap(t["ticker"], float(t["entry"]) * float(t["shares"]), t.get("direction", "short"), [(_hunt_book_label(), t["ticker"])], 0.0)
    return None if ok else why

def apply_plan(path, now):
    """plan json: {label, cut:[{ticker,reason}], set:[{ticker,trigger,max_fill,stop,t1,t2,shares,atr}], cash_slots:[..], note}"""
    P = json.load(open(path))
    L = json.load(open(LEDGER)) if os.path.exists(LEDGER) else new_ledger(P.get("label", "Our picks"))
    evs = []   # audit H7: one bad row never aborts the plan; the ledger is written BEFORE the events are appended
    for c in P.get("cut", []):
        if not isinstance(c, dict): c = dict(ticker=str(c), reason=P.get("note") or "cut")   # gatecut 2026-10-30: accept plain ticker strings
        try: cut_trade(L, c["ticker"], c.get("reason") or P.get("note") or "cut", now)
        except SystemExit as e: evs.append(("cut_refused", dict(ticker=c.get("ticker"), why=str(e))))
    _claude_off = BOOK == "claude" and (os.path.exists(os.path.join(BASE, "state", "config", "claude_retired.json")) or os.environ.get("TFO_CLAUDE_OFF") == "1")   # Brett 2026-10-29 08:33: Claude book retired - open trades run to own stop/exit, no new picks
    _ub_run = [0.0]
    for sp in P.get("set", []):
        if _claude_off and not any(x["ticker"] == sp.get("ticker") and x["status"] == "FILLED" for x in L["trades"]):
            evs.append(("arm_refused", dict(ticker=sp.get("ticker"), why="Claude book retired 2026-10-29 (no new Claude picks)"))); continue
        if clearance_expired(sp, now):
            evs.append(("arm_refused", dict(ticker=sp.get("ticker"), why=f"clearance {sp.get('cleared')} expired {clearance_expiry(sp).isoformat()} (same-day rule)"))); continue
        _ex = next((x for x in L["trades"] if x["ticker"] == sp.get("ticker") and x["status"] in ("ARMED", "BENCH")), None)
        if BOOK == "claude": sp = fixed_levels(sp, (sp.get("direction") or (_ex or {}).get("direction") or "short").lower(), L, 2500.0)   # 2026-10-29 16:08 WITA Brett 16:05: main+gpt keep staged R-based levels; retired claude book untouched (fixed). FILLED (FLG) never re-levelled
        _ubw = us_cap_arm_block(sp, L, _ub_run)   # tfo_usbook_cap_20261030: main-book US beta cap at arm (counts open+armed+queued+this plan)
        if _ubw: evs.append(("arm_refused", dict(ticker=sp.get("ticker"), why=_ubw))); continue
        try: t = upsert_trade(L, sp, now)
        except (ArmRefused, SystemExit) as e: evs.append(("arm_refused", dict(ticker=sp.get("ticker"), why=str(e)))); continue
        for k in ("ai_gpt", "ai_claude"):                                   # 2026-10-28 Brett: AI verdicts advisory, recorded on the trade
            if sp.get(k): t[k] = sp[k]
        px = None
        try: b = bars_15m(t["ticker"]); px = b[-1]["c"] if b else None
        except Exception as e: t["data_error"] = str(e)[:120]
        if px: t["last_px"] = px
        if pre_check_missed(t, px): evs.append(("missed_at_arm", dict(ticker=t["ticker"], px=px)))
        evs.append(("armed", dict(ticker=t["ticker"], trigger=t["trigger"], stop=t["stop"], shares=t["shares"], px=px, **({"relevel_from": t["relevel_from"]} if t.get("relevel_from") else {}))))
    if "cash_slots" in P: L["cash_slots"] = P["cash_slots"]
    if "label" in P: L["book_label"] = P["label"]
    L.setdefault("verdicts", []).append(dict(utc=now.isoformat(timespec="seconds"), note=P.get("note", "")))
    atomic(LEDGER, L)
    for k, kw in evs: event(k, **kw)
    return L

# Brett 2026-10-28 20:59 WITA approved hunt fix 2: no NEW short/breakdown entries in the last 2h of the NY session
# (bar ending after 14:00 ET; 11:00 ET on 13:00 early-close days). The trade is not MISSED - it stays ARMED for the next open.
LATE_NO_ENTRY_H = 2
def in_late_session(bar_end_utc):
    e = bar_end_utc.astimezone(_NY); d = e.date()
    close = dt.datetime.combine(d, dt.time(13, 0) if d.isoformat() in NYSE_EARLY else dt.time(16, 0), _NY)
    return close - dt.timedelta(hours=LATE_NO_ENTRY_H) < e <= close
# Brett 2026-10-28 20:59 WITA approved US hunt fix (b): a SHORT does not carry overnight unless, on the last 15m bar of the
# NY session, it is >= +1R in profit (R = |entry - initial stop|) or its stop/trail is at or better than breakeven. Else flat at that close.
CARRY_MIN_R = 1.0
def is_session_last_bar(bar_end_utc):
    e = bar_end_utc.astimezone(_NY); d = e.date()
    if not ny_session(d): return False
    return e.time() == (dt.time(13, 0) if d.isoformat() in NYSE_EARLY else dt.time(16, 0))
def short_may_carry(t, px):
    r = abs(float(t["entry"]) - float(t["stop"]))
    if r > 0 and (float(t["entry"]) - float(px)) >= CARRY_MIN_R * r: return True
    return t.get("trail") is not None and float(t["trail"]) <= float(t["entry"])
def try_fill(t, bars, now, fx=None):
    """bars: list of dict(start: datetime UTC, o,h,l,c) 15m regular-session bars. Only bars completed by `now` and
    started after armed_utc count. Mutates t; returns event kind or None."""
    armed = dt.datetime.fromisoformat(t["armed_utc"])
    for b in bars:
        if b["start"] + dt.timedelta(minutes=15) <= armed or b["start"] + dt.timedelta(minutes=15) > now: continue
        c = b["c"]; lg = _long(t)
        if not lg and in_late_session(b["start"] + dt.timedelta(minutes=15)):   # fix 2: no new short/breakdown fills in last 2h; stay ARMED for next open
            t["late_session_skip"] = (b["start"] + dt.timedelta(minutes=15)).isoformat(); continue
        if _past(t, c):
            t["status"] = "MISSED"; return "missed"
        if (c > t["trigger"] and c <= t["max_fill"]) if lg else (c < t["trigger"] and c >= t["max_fill"]):
            px = round(min(c * (1 + SLIP), t["max_fill"]) if lg else max(c * (1 - SLIP), t["max_fill"]), 4)   # risk is checked on the stored (4dp) entry
            # audit H5 hardening (Brett 27 Oct 2026 21:11 WITA): the A$ risk recheck at the ACTUAL fill fails CLOSED.
            # No usable AUDUSD, or not even 1 share inside the cap -> SHADOW (never a position). Oversize -> floor shares to the cap.
            end = b["start"] + dt.timedelta(minutes=15)
            def _no_fill(why):
                t.update(status="SHADOW", shadow_reason=why, shadow_entry=px, shadow_utc=end.isoformat(), entry=None, entry_utc=None, open_qty=0); return "shadow"
            if not isinstance(fx, (int, float)) or isinstance(fx, bool) or not (fx == fx) or fx <= 0 or fx == float("inf"):
                return _no_fill("no AUDUSD at fill: A$ risk unchecked")
            cap_aud = 2500.0 if t.get("risk_cap_aud") is None else float(t["risk_cap_aud"]); cap_usd = cap_aud * fx
            Rps = abs(px - t["stop"])
            if not (Rps > 0): return _no_fill("zero risk distance at fill")
            n = int(cap_usd // Rps)
            while n > 0 and Rps * n > cap_usd: n -= 1
            if n < 1: return _no_fill(f"risk > cap at fill: 1 sh = A${Rps / fx:,.2f} > A${cap_aud:,.0f}")
            if Rps * t["shares"] > cap_usd:
                t["shares_planned"] = t["shares"]; t["shares"] = n
                t["risk_resized_at_fill"] = f"{t['shares_planned']} -> {t['shares']} sh (risk at fill > A${cap_aud:,.0f})"
            fin = IC.us_side(t["shares"], px, "buy" if lg else "sell")
            t.update(status="FILLED", entry=px, entry_utc=(b["start"] + dt.timedelta(minutes=15)).isoformat(),
                     open_qty=t["shares"], lowest=c, fees_usd=t["fees_usd"] + fin, fee_in_usd=fin, last_px=c)
            t["_last_bar"] = b["start"].isoformat()
            stamp_exit_at_fill(t, b["start"] + dt.timedelta(minutes=15))   # audit C1
            if not lg and not IC.has_borrow(t["ticker"], t["shares"]): _shadow(t, "no borrow"); return "shadow"
            if not lg: t["borrow_pa"] = IC.borrow_pa(t["ticker"])
            return "filled"
    return None

# Brett 2026-10-27 04:30 WITA (approved): (a) max ONE open trade per sector bucket (GICS-like sector via yfinance/sweep;
# FX, COMMODITIES, CRYPTO are their own asset-class buckets); (b) direction balance: if 2 open in one direction the 3rd
# slot only takes the opposite direction. Breaches -> SHADOW. Existing open trades are never force-closed.
SECTOR_CACHE = os.path.join(BASE, "state", "hunt48_sector_cache.json")
COMMODITY_ETFS = {"SLV", "GLD", "IAU", "SIVR", "PSLV", "PPLT", "PALL", "USO", "BNO", "UNG", "DBC", "DBA", "CPER", "GDX", "GDXJ", "URA", "WEAT", "CORN"}
def sector_of(ticker, t=None):
    if t and t.get("sector"): return t["sector"]
    tk = ticker.upper()
    if "_" in tk: return "COMMODITIES" if tk.split("_")[0] in ("XAU", "XAG", "XPT", "XPD", "WTICO", "BCO", "NATGAS", "CORN", "WHEAT", "SOYBN", "SUGAR", "XCU") else "FX"
    if "/" in tk or tk.endswith("-USD"): return "CRYPTO"
    if tk in COMMODITY_ETFS: return "COMMODITIES"
    try: C = json.load(open(SECTOR_CACHE))
    except Exception: C = {}
    if tk in C: return C[tk]
    sec = None
    try:
        import csv
        for f in ("ranked_all_incl_excluded.csv", "ranked.csv"):
            pth = os.path.join(BASE, "state", "calendar", "inputs", f)
            if os.path.exists(pth):
                for r in csv.DictReader(open(pth)):
                    if r["sym"] == tk and r.get("sector"): sec = r["sector"]; break
            if sec: break
        if not sec:
            import yfinance as yf
            info = yf.Ticker(tk).info or {}
            sec = info.get("sector") or (("ETF:" + info["category"]) if info.get("category") else None)
            if sec and "commodit" in sec.lower(): sec = "COMMODITIES"
    except Exception: pass
    sec = sec or ("UNKNOWN:" + tk)
    if not sec.startswith("UNKNOWN"):
        C[tk] = sec
        try: atomic(SECTOR_CACHE, C)
        except Exception: pass
    return sec
def mix_gate(t, open_trades, max_open=MAX_OPEN):
    """None if allowed, else reason string (sector cap / direction balance)."""
    sec = sector_of(t["ticker"], t); t["sector"] = sec
    for o in open_trades:
        if o is not t and sector_of(o["ticker"], o) == sec: return f"sector cap: {o['ticker']} already open in {sec}"
    d = t.get("direction", "short"); same = sum(o.get("direction", "short") == d for o in open_trades if o is not t)
    if len(open_trades) >= max_open - 1 and same >= max_open - 1: return f"direction balance: {same} {d}s already open; last slot opposite direction only"
    return None
def slot_gate(t, open_now, max_open=MAX_OPEN, open_trades=None):
    """Called right after try_fill returned 'filled'. If the book already holds max_open FILLED trades, or (when open_trades
    is given) the sector/direction mix gate fails, undo the fill and mark the trade SHADOW (never a position)."""
    why = mix_gate(t, open_trades, max_open) if open_trades is not None and open_now < max_open else None
    if open_now < max_open and not why: return "filled"
    t["shadow_reason"] = why or f"max {max_open} open"
    t.update(status="SHADOW", shadow_entry=t["entry"], shadow_utc=t["entry_utc"], entry=None, entry_utc=None, open_qty=0,
             lowest=None, fees_usd=t["fees_usd"] - t.pop("fee_in_usd", COMM_USD)); t.pop("_last_bar", None); return "shadow"

# Review fix #6 (Brett-approved hunt_review_20261027): FIRM-level rules across the three US hunt books (main/gpt/claude),
# checked at fill after the book's own slot_gate: (a) one book per ticker firm-wide; (b) once >=3 positions are open firm-wide
# (counting this fill), no direction may carry more than 70% of open risk. No quota on the first positions. Existing positions are
# never touched. A sibling ledger that exists but cannot be read raises -> trade kept ARMED and retried (fail closed, no fill).
FIRM_BOOK_DIRS = ("hunt48_short", "hunt48_short_gpt"); FIRM_NET_DIR_MAX = 0.70; FIRM_MIN_OPEN = 3  # claude retired 20261029 (TFO 11:41 follow-up)
def firm_other_open(ledger=None):
    own = os.path.dirname(os.path.abspath(ledger or LEDGER)); root = os.path.dirname(own); out = []
    for d in FIRM_BOOK_DIRS:
        p = os.path.join(root, d, "ledger.json")
        if os.path.join(root, d) == own or not os.path.exists(p): continue
        try: X = json.load(open(p))
        except Exception as e: raise RuntimeError(f"firm gate: sibling ledger {d} unreadable ({str(e)[:60]})")
        out += [dict(o, _book=d) for o in X.get("trades", []) if o.get("status") == "FILLED"]
    return out
def firm_gate(t, own_open, others):
    """None if allowed, else reason. own_open/others = FILLED trades (this book excl. t / sibling books)."""
    tk = t["ticker"]; hit = next((o for o in others if o.get("ticker") == tk), None)
    if hit: return f"firm: {tk} already held in {hit['_book']} (one book per ticker)"
    allp = list(own_open) + list(others) + [t]
    if len(allp) < FIRM_MIN_OPEN: return None
    rk = lambda o: float(o.get("risk_aud") or 2500.0); tot = sum(rk(o) for o in allp)
    d = t.get("direction", "short"); same = sum(rk(o) for o in allp if o.get("direction", "short") == d)
    if tot and same / tot > FIRM_NET_DIR_MAX + 1e-9:
        return f"firm net direction: {d} would be {100*same/tot:.0f}% of open risk across {len(allp)} positions (> {100*FIRM_NET_DIR_MAX:.0f}%)"
    return None
def firm_undo(t, why):
    k = slot_gate(t, MAX_OPEN, MAX_OPEN); t["shadow_reason"] = why; return k

# Head trader 2026-10-27 04:40 (GPT debrief rule 4): reject entries with < 1R of room to the nearest OPPOSING level
# (short: nearest support below entry; long: nearest resistance above) after estimated round-trip costs.
MIN_ROOM_R = 1.0
def opposing_levels(daily, long=False):
    """Daily swing pivots (2 bars each side) + 20/50-day MAs from completed daily bars (last bar = today, excluded)."""
    d = daily[:-1] if len(daily) > 5 else daily; lv = []
    for i in range(2, len(d) - 2):
        if not long and d[i]["l"] <= min(d[j]["l"] for j in (i-2, i-1, i+1, i+2)): lv.append(d[i]["l"])
        if long and d[i]["h"] >= max(d[j]["h"] for j in (i-2, i-1, i+1, i+2)): lv.append(d[i]["h"])
    for n in (20, 50):
        if len(d) >= n: lv.append(sum(x["c"] for x in d[-n:]) / n)
    return lv
def room_check(t, daily, entry=None):
    """audit H2+M4 (Brett 27 Oct 2026 19:35 WITA): the ONE hunt48 room definition (hunt48_room.room), measured at max_fill:
    min(structure 1y, T2) - cost_R >= 1.0R, cost_R <= 0.15, >= 200 daily bars; anything missing -> not ok (fail closed).
    entry (actual fill) is recorded only; room is judged at max_fill (worst permitted fill), same as clearance.
    Returns dict(ok, room_R, level, cost_R, basis, why)."""
    import hunt48_room as HR
    lg = _long(t); e = t.get("max_fill") or entry; st = t.get("stop"); q = max(t.get("shares") or 1, 1)
    R = abs(e - st) if (e and st) else 0
    if R <= 0: return dict(ok=False, room_R=None, level=None, cost_R=None, why="zero risk distance")
    cost = IC.us_side(q, e, "buy") + IC.us_side(q, e, "sell") + 2 * SLIP * e * q + (0 if lg else e * q * IC.borrow_pa(t["ticker"]) * 3 / 365)
    cost_R = cost / (R * q)
    rt = dict(direction="long" if lg else "short", max_fill=e, stop=st, t2=t.get("t2"), cost_R=cost_R)
    why = HR.room(rt, daily or [], max_cost_R=HR.US_MAX_COST_R, min_bars=HR.US_MIN_BARS)
    no_data = not any(HR._ok_bar(b) for b in (daily or []))   # audit retry-edge: zero usable daily rows = data outage, not a verdict
    return dict(ok=why is None, room_R=rt.get("room_R"), level=rt.get("room_level"), cost_R=round(cost_R, 3), basis=rt.get("room_basis"),
                fill_entry=entry, why=why, no_data=no_data)
def _shadow(t, why):
    t.update(status="SHADOW", shadow_reason=why, shadow_entry=t["entry"], shadow_utc=t["entry_utc"], entry=None, entry_utc=None, open_qty=0,
             lowest=None, fees_usd=t["fees_usd"] - t.pop("fee_in_usd", COMM_USD)); t.pop("_last_bar", None)

def _cover(t, qty, px, why, when):
    if _long(t): px = px * (1 - SLIP); pnl = (px - t["entry"]) * qty
    else: px = px * (1 + SLIP); pnl = (t["entry"] - px) * qty
    t["realised_usd"] += pnl; t["fees_usd"] += IC.us_side(qty, px, "sell" if _long(t) else "buy"); t["open_qty"] -= qty
    t["exits"].append(dict(qty=qty, px=round(px, 4), why=why, utc=when.isoformat()))

def _te_valid(t):
    """audit C1 guard: an exit at/before the trade's own entry is invalid -> re-stamp from the entry (never exit on it)."""
    te = dt.datetime.fromisoformat(t["time_exit_utc"])
    if t.get("entry_utc") and te <= dt.datetime.fromisoformat(t["entry_utc"]):
        te = stamp_exit_at_fill(t, dt.datetime.fromisoformat(t["entry_utc"])); t["time_exit_repaired"] = True
    return te
def _daily_for_ext(t, daily, daily_fn):
    """audit H9: daily bars are fetched only AFTER stops/T1 ran, only when an extension is being considered, and are optional."""
    if daily is not None or daily_fn is None: return daily
    try: return daily_fn()
    except Exception as e: t["daily_error"] = str(e)[:120]; return None
def manage(t, bars, now, daily=None, daily_fn=None):
    if _long(t): return manage_long(t, bars, now, daily, daily_fn)
    """Exits for a FILLED trade over completed bars after the last processed bar. Returns list of event kinds."""
    import hunt_giveback as GB; _GBN = GB.load_news()
    ev = []; last = dt.datetime.fromisoformat(t["_last_bar"]) if t.get("_last_bar") else dt.datetime.fromisoformat(t["entry_utc"])
    for b in bars:
        if b["start"] <= last or b["start"] + dt.timedelta(minutes=15) > now or t["open_qty"] <= 0: continue
        end = b["start"] + dt.timedelta(minutes=15)
        stop = t["trail"] if t["trail"] is not None else t["stop"]
        if b["h"] >= stop:
            _cover(t, t["open_qty"], max(stop, b["o"]), "stop" if t["trail"] is None else "trail", end)
            t["status"] = "STOPPED" if not t["half_done"] else "CLOSED"; ev.append(t["status"].lower()); break
        # review fix (hunt_review_20261027 §2): T1 is checked BEFORE the thesis exit; a bar that tags T1 intrabar and then
        # closes back through the level books the T1 half first, the thesis exit then covers the remainder at the close.
        if not t["half_done"]:
            # Brett 2026-10-29 17:30 WITA: main+gpt half-bank at net >= +1R of original fill-to-stop risk after costs (replaces T1/+2%). Claude (retired) keeps T1.
            q = t["open_qty"] // 2
            if BOOK != "claude":   # TFO amend 17:37: NET = entry fee + exit commission + exit slippage + accrued borrow (shorts); 1.5R for trend setups
                import hunt_giveback as GB1
                sh = float(t.get("shares") or t["open_qty"]); risk_usd = abs(float(t["entry"]) - float(t["stop"])) * sh; need = GB1.half_r(t) * risk_usd
                fav = b["l"]; qo = t["open_qty"]
                rt = float(t.get("fees_usd") or 0) + IC.us_side(qo, fav, "buy") + SLIP * fav * qo + float(t.get("borrow_usd") or 0)
                if q > 0 and risk_usd > 0 and (-1 * (fav - t["entry"]) * qo - rt) >= need - 1e-9:
                    lvl = t["entry"] + -1 * (need + rt) / qo
                    px = b["c"] if (-1 * (b["c"] - t["entry"]) * qo - rt) >= need - 1e-9 else lvl
                    _cover(t, q, px, "1R half", end); t["half_done"] = True
                    t["trail"] = round(GB1.fee_floor(t["entry"], -1, rt, qo), 6)   # fee floor only AFTER the half-bank
                    t.update(half_rule="%gR_net" % GB1.half_r(t), half_px=round(px, 6), half_rt_cost_usd=round(rt, 4), stop_rule="fee_floor_after_half"); ev.append("t1")
            elif b["l"] <= t["t1"]:
                _cover(t, q, t["t1"], "T1 half", end); t["half_done"] = True
                t["trail"] = t["entry"]; ev.append("t1")
                if t.get("t2") is not None and abs(float(t["t2"]) - float(t["t1"])) < 1e-9 and t["open_qty"] > 0:   # Brett 2026-10-28 fixed +3%: T1==T2 -> full exit
                    _cover(t, t["open_qty"], t["t1"], "target +3%", end); t.update(status="CLOSED", exit_reason="target +3% (T1=T2)")
                    t["last_px"] = b["c"]; t["_last_bar"] = b["start"].isoformat(); ev.append("target"); break
        if BOOK != "claude" and t["open_qty"] > 0:   # bankfinal (Brett 17:38 WITA): full flat at net >= 3R of ORIGINAL risk (R locked at entry); replaces +5% on hunts
            import hunt_giveback as GB1
            _qo = t["open_qty"]; _rk = abs(float(t["entry"]) - float(t["stop"])) * float(t.get("shares") or _qo)
            if _rk > 0:
                _rt = float(t.get("fees_usd") or 0) + IC.us_side(_qo, b["l"], "buy") + SLIP * b["l"] * _qo + float(t.get("borrow_usd") or 0)
                _fl = GB1.flat_level(t["entry"], -1, _rk, _rt, float(t.get("shares") or _qo))   # level on ORIGINAL size: 3R price move net of RT cost/share
                if b["l"] <= _fl:
                    _cover(t, _qo, b["c"] if b["c"] <= _fl else _fl, "3R full flat", end); t.update(status="CLOSED", exit_reason="full flat at net 3R of original risk", flat_rt_cost_usd=round(_rt, 4))
                    t["last_px"] = b["c"]; t["_last_bar"] = b["start"].isoformat(); ev.append("flat_3r"); break
        lvl = t.get("broken_level") or t["trigger"]   # thesis exit (head trader 2026-10-27): completed 15m close back through the broken level
        if b["c"] > lvl + thesis_buf(t) and not first_bar_of_session(b["start"]):   # fix 1 (Brett 28 Oct 20:59 WITA)
            _cover(t, t["open_qty"], b["c"], "thesis exit", end); t.update(status="CLOSED", exit_reason="thesis exit: 15m close above %.2f (level %.2f + 0.5 ATR)" % (lvl + thesis_buf(t), lvl))
            t["last_px"] = b["c"]; t["_last_bar"] = b["start"].isoformat(); ev.append("thesis_exit"); break
        if BOOK != "claude" and t["open_qty"] > 0:   # Brett DNA 2026-10-29: giveback early bank (hunt_giveback.py); retired claude book untouched
            _gb = GB.check(t, b, [x for x in bars if x["start"] < b["start"]], -1, t["entry"], t["stop"], t["entry_utc"], end, _GBN)
            if _gb:
                _rail = t["trail"] if t["trail"] is not None else t["stop"]
                _cover(t, t["open_qty"], b["c"], "giveback " + _gb, end); GB.record(t, _gb, b["c"], _rail, end, "hunt48_short" + ("_" + BOOK if BOOK else ""))
                t.update(status="CLOSED", exit_reason="giveback early bank (" + _gb + "): " + GB.REASON[_gb])
                t["last_px"] = b["c"]; t["_last_bar"] = b["start"].isoformat(); ev.append("giveback"); break
        t["lowest"] = min(t["lowest"], b["l"])
        if t["half_done"]:
            k = 1.0 if t["lowest"] <= t["entry"] * 0.95 else 2.0
            t["trail"] = min(t["trail"], t["lowest"] + k * t["atr"])
        t["last_px"] = b["c"]; t["_last_bar"] = b["start"].isoformat()
        if BOOK != "claude" and t["open_qty"] > 0 and GB.time_stop_applies(t.get("entry_utc")):   # TIME STOP (Brett 17:44 WITA): < +0.5R net by session-2 close; deadline fixed at entry, never extended
            t.setdefault("time_stop_utc", session_exit(dt.datetime.fromisoformat(t["entry_utc"]), TIME_STOP_SESSIONS).isoformat())
            _sh = float(t.get("shares") or t["open_qty"]); _rt = float(t.get("fees_usd") or 0) + IC.us_side(_sh, b["c"], "buy") + SLIP * b["c"] * _sh + float(t.get("borrow_usd") or 0)
            if GB.time_stop_due(t, dt.datetime.fromisoformat(t["time_stop_utc"]), end, b.get("c"), -1, t["entry"], t["stop"], _sh, _rt):
                _rail = t["trail"] if t["trail"] is not None else t["stop"]
                _cover(t, t["open_qty"], b["c"], "rail_time_stop", end); GB.record(t, "time_stop", b["c"], _rail, end, "hunt48_short" + ("_" + BOOK if BOOK else ""))
                t.update(status="CLOSED", exit_reason="rail_time_stop"); ev.append("time_stop"); break
        if t["open_qty"] > 0 and is_session_last_bar(end) and not short_may_carry(t, b["c"]):   # fix (b) Brett 28 Oct 20:59 WITA
            _cover(t, t["open_qty"], b["c"], "no overnight carry", end)
            t.update(status="CLOSED", exit_reason="no overnight carry: <1R profit and stop not at breakeven at NY close"); ev.append("eod_flat"); break
    # time exit
    te = _te_valid(t)
    if t["open_qty"] > 0 and now >= te:
        upl = (t["entry"] - (t["last_px"] or t["entry"])) * t["open_qty"]
        if upl > 0 and t["ext_days"] < MAX_EXT_DAYS and not extension_earnings_veto(t, now) and extension_ok(_daily_for_ext(t, daily, daily_fn)):
            t["ext_days"] += 1; t["time_exit_utc"] = session_exit(te, 1).isoformat()   # audit C1 hardening: next NY session close
            if t["trail"] is None: t["trail"] = min(t["stop"], (t["lowest"] or t["last_px"]) + 2 * t["atr"])
            ev.append("extended")
        else:
            _cover(t, t["open_qty"], t["last_px"] or t["entry"], "time exit", now); t["status"] = "CLOSED"; ev.append("time_exit")
    return ev

def manage_long(t, bars, now, daily=None, daily_fn=None):
    """LONG mirror of manage() (comparison books only). t['lowest'] holds the HIGHEST high since entry for longs."""
    import hunt_giveback as GB; _GBN = GB.load_news()
    ev = []; last = dt.datetime.fromisoformat(t["_last_bar"]) if t.get("_last_bar") else dt.datetime.fromisoformat(t["entry_utc"])
    for b in bars:
        if b["start"] <= last or b["start"] + dt.timedelta(minutes=15) > now or t["open_qty"] <= 0: continue
        end = b["start"] + dt.timedelta(minutes=15)
        stop = t["trail"] if t["trail"] is not None else t["stop"]
        if b["l"] <= stop:
            _cover(t, t["open_qty"], min(stop, b["o"]), "stop" if t["trail"] is None else "trail", end)
            t["status"] = "STOPPED" if not t["half_done"] else "CLOSED"; ev.append(t["status"].lower()); break
        # review fix (hunt_review_20261027 §2): T1 is checked BEFORE the thesis exit; a bar that tags T1 intrabar and then
        # closes back through the level books the T1 half first, the thesis exit then covers the remainder at the close.
        if not t["half_done"]:
            # Brett 2026-10-29 17:30 WITA: main+gpt half-bank at net >= +1R of original fill-to-stop risk after costs (replaces T1/+2%). Claude (retired) keeps T1.
            q = t["open_qty"] // 2
            if BOOK != "claude":   # TFO amend 17:37: NET = entry fee + exit commission + exit slippage + accrued borrow (shorts); 1.5R for trend setups
                import hunt_giveback as GB1
                sh = float(t.get("shares") or t["open_qty"]); risk_usd = abs(float(t["entry"]) - float(t["stop"])) * sh; need = GB1.half_r(t) * risk_usd
                fav = b["h"]; qo = t["open_qty"]
                rt = float(t.get("fees_usd") or 0) + IC.us_side(qo, fav, "sell") + SLIP * fav * qo + float(t.get("borrow_usd") or 0)
                if q > 0 and risk_usd > 0 and (1 * (fav - t["entry"]) * qo - rt) >= need - 1e-9:
                    lvl = t["entry"] + 1 * (need + rt) / qo
                    px = b["c"] if (1 * (b["c"] - t["entry"]) * qo - rt) >= need - 1e-9 else lvl
                    _cover(t, q, px, "1R half", end); t["half_done"] = True
                    t["trail"] = round(GB1.fee_floor(t["entry"], 1, rt, qo), 6)   # fee floor only AFTER the half-bank
                    t.update(half_rule="%gR_net" % GB1.half_r(t), half_px=round(px, 6), half_rt_cost_usd=round(rt, 4), stop_rule="fee_floor_after_half"); ev.append("t1")
            elif b["h"] >= t["t1"]:
                _cover(t, q, t["t1"], "T1 half", end); t["half_done"] = True
                t["trail"] = t["entry"]; ev.append("t1")
                if t.get("t2") is not None and abs(float(t["t2"]) - float(t["t1"])) < 1e-9 and t["open_qty"] > 0:   # Brett 2026-10-28 fixed +3%: T1==T2 -> full exit
                    _cover(t, t["open_qty"], t["t1"], "target +3%", end); t.update(status="CLOSED", exit_reason="target +3% (T1=T2)")
                    t["last_px"] = b["c"]; t["_last_bar"] = b["start"].isoformat(); ev.append("target"); break
        if BOOK != "claude" and t["open_qty"] > 0:   # bankfinal (Brett 17:38 WITA): full flat at net >= 3R of ORIGINAL risk (R locked at entry); replaces +5% on hunts
            import hunt_giveback as GB1
            _qo = t["open_qty"]; _rk = abs(float(t["entry"]) - float(t["stop"])) * float(t.get("shares") or _qo)
            if _rk > 0:
                _rt = float(t.get("fees_usd") or 0) + IC.us_side(_qo, b["h"], "sell") + SLIP * b["h"] * _qo + float(t.get("borrow_usd") or 0)
                _fl = GB1.flat_level(t["entry"], 1, _rk, _rt, float(t.get("shares") or _qo))   # level on ORIGINAL size: 3R price move net of RT cost/share
                if b["h"] >= _fl:
                    _cover(t, _qo, b["c"] if b["c"] >= _fl else _fl, "3R full flat", end); t.update(status="CLOSED", exit_reason="full flat at net 3R of original risk", flat_rt_cost_usd=round(_rt, 4))
                    t["last_px"] = b["c"]; t["_last_bar"] = b["start"].isoformat(); ev.append("flat_3r"); break
        lvl = t.get("broken_level") or t["trigger"]   # thesis exit (head trader 2026-10-27): completed 15m close back through the broken level
        if b["c"] < lvl - thesis_buf(t) and not first_bar_of_session(b["start"]):   # fix 1 (Brett 28 Oct 20:59 WITA)
            _cover(t, t["open_qty"], b["c"], "thesis exit", end); t.update(status="CLOSED", exit_reason="thesis exit: 15m close below %.2f (level %.2f - 0.5 ATR)" % (lvl - thesis_buf(t), lvl))
            t["last_px"] = b["c"]; t["_last_bar"] = b["start"].isoformat(); ev.append("thesis_exit"); break
        if BOOK != "claude" and t["open_qty"] > 0:   # Brett DNA 2026-10-29: giveback early bank (hunt_giveback.py); retired claude book untouched
            _gb = GB.check(t, b, [x for x in bars if x["start"] < b["start"]], 1, t["entry"], t["stop"], t["entry_utc"], end, _GBN)
            if _gb:
                _rail = t["trail"] if t["trail"] is not None else t["stop"]
                _cover(t, t["open_qty"], b["c"], "giveback " + _gb, end); GB.record(t, _gb, b["c"], _rail, end, "hunt48_short" + ("_" + BOOK if BOOK else ""))
                t.update(status="CLOSED", exit_reason="giveback early bank (" + _gb + "): " + GB.REASON[_gb])
                t["last_px"] = b["c"]; t["_last_bar"] = b["start"].isoformat(); ev.append("giveback"); break
        t["lowest"] = max(t["lowest"], b["h"])
        if t["half_done"]:
            k = 1.0 if t["lowest"] >= t["entry"] * 1.05 else 2.0
            t["trail"] = max(t["trail"], t["lowest"] - k * t["atr"])
        t["last_px"] = b["c"]; t["_last_bar"] = b["start"].isoformat()
        if BOOK != "claude" and t["open_qty"] > 0 and GB.time_stop_applies(t.get("entry_utc")):   # TIME STOP (Brett 17:44 WITA): < +0.5R net by session-2 close; deadline fixed at entry, never extended
            t.setdefault("time_stop_utc", session_exit(dt.datetime.fromisoformat(t["entry_utc"]), TIME_STOP_SESSIONS).isoformat())
            _sh = float(t.get("shares") or t["open_qty"]); _rt = float(t.get("fees_usd") or 0) + IC.us_side(_sh, b["c"], "sell") + SLIP * b["c"] * _sh + float(t.get("borrow_usd") or 0)
            if GB.time_stop_due(t, dt.datetime.fromisoformat(t["time_stop_utc"]), end, b.get("c"), 1, t["entry"], t["stop"], _sh, _rt):
                _rail = t["trail"] if t["trail"] is not None else t["stop"]
                _cover(t, t["open_qty"], b["c"], "rail_time_stop", end); GB.record(t, "time_stop", b["c"], _rail, end, "hunt48_short" + ("_" + BOOK if BOOK else ""))
                t.update(status="CLOSED", exit_reason="rail_time_stop"); ev.append("time_stop"); break
    te = _te_valid(t)
    if t["open_qty"] > 0 and now >= te:
        upl = ((t["last_px"] or t["entry"]) - t["entry"]) * t["open_qty"]
        if upl > 0 and t["ext_days"] < MAX_EXT_DAYS and not extension_earnings_veto(t, now) and extension_ok(_daily_for_ext(t, daily, daily_fn), long=True):
            t["ext_days"] += 1; t["time_exit_utc"] = session_exit(te, 1).isoformat()   # audit C1 hardening: next NY session close
            if t["trail"] is None: t["trail"] = max(t["stop"], (t["lowest"] or t["last_px"]) - 2 * t["atr"])
            ev.append("extended")
        else:
            _cover(t, t["open_qty"], t["last_px"] or t["entry"], "time exit", now); t["status"] = "CLOSED"; ev.append("time_exit")
    return ev

def extension_ok(daily, long=False):
    """short mirror: (close < 10dMA AND ADX rising) OR new 5-day low. daily: list of dict(h,l,c), oldest first."""
    if not daily or len(daily) < 30: return False
    c = [d["c"] for d in daily]; h = [d["h"] for d in daily]; l = [d["l"] for d in daily]
    if long:
        if h[-1] >= max(h[-5:]): return True
    elif l[-1] <= min(l[-5:]): return True
    ma10 = sum(c[-10:]) / 10
    def adx(n):
        tr, pdm, ndm = [], [], []
        for i in range(1, n):
            tr.append(max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))); up, dn = h[i]-h[i-1], l[i-1]-l[i]
            pdm.append(up if up > dn and up > 0 else 0); ndm.append(dn if dn > up and dn > 0 else 0)
        dx = []
        for j in range(14, len(tr) + 1):
            a = sum(tr[j-14:j]) or 1e-9; p = 100*sum(pdm[j-14:j])/a; m = 100*sum(ndm[j-14:j])/a; dx.append(100*abs(p-m)/((p+m) or 1e-9))
        return sum(dx[-14:]) / 14
    return (c[-1] > ma10 if long else c[-1] < ma10) and adx(len(c)) > adx(len(c) - 1)

def mark(t, px, audusd, now):
    held_h = 0
    if t.get("entry_utc"): held_h = (now - dt.datetime.fromisoformat(t["entry_utc"])).total_seconds() / 3600
    if t["entry"] and not _long(t):
        t["borrow_pa"] = IC.borrow_pa(t["ticker"])
        t["borrow_usd"] = t["entry"] * t["shares"] * t["borrow_pa"] * held_h / 8760 * (t["open_qty"] / t["shares"] if t["open_qty"] else 0.5)
    upl = ((px * (1 - SLIP) - t["entry"]) if _long(t) else (t["entry"] - px * (1 + SLIP))) * t["open_qty"] - (IC.us_side(t["open_qty"], px, "sell" if _long(t) else "buy") if t["open_qty"] else 0) if t["entry"] and px else 0.0
    net = t["realised_usd"] + upl - t["fees_usd"] - t["borrow_usd"]
    return dict(upl_usd=round(upl, 2), pnl_aud=round(net / audusd, 2))

# ---------------------------------------------------------------- data (read-only market data)
def bars_15m(sym):
    import yfinance as yf
    d = yf.Ticker(sym).history(period="5d", interval="15m", prepost=False)
    return [dict(start=i.to_pydatetime().astimezone(UTC), o=float(r.Open), h=float(r.High), l=float(r.Low), c=float(r.Close), v=float(getattr(r, "Volume", 0) or 0) or None) for i, r in d.iterrows()]   # v: giveback (b)
def bars_daily(sym):
    import yfinance as yf
    d = yf.Ticker(sym).history(period="1y", interval="1d")   # audit H2+M4: 1y of structure, same lookback as hunt48_room (was 3mo)
    return [dict(h=float(r.High), l=float(r.Low), c=float(r.Close)) for _, r in d.iterrows()]
def live_audusd(fallback):
    try:
        import yfinance as yf; return float(yf.Ticker("AUDUSD=X").history(period="5d").Close.iloc[-1])
    except Exception: return fallback
def us_rth(now):
    from zoneinfo import ZoneInfo
    ny = now.astimezone(ZoneInfo("America/New_York"))
    return ny.weekday() < 5 and dt.time(9, 30) <= ny.time() <= dt.time(16, 5)

def tick(force=False):
    L = json.load(open(LEDGER)); now = now_utc(); fx = live_audusd(L["audusd_armed"]); rth = us_rth(now)
    # 2026-10-28 Brett 10:37 FUNDER GATE: ARMED (not funder-approved) -> awaiting_funder + state/funder_queue.json; ticks applied here, before any fill
    import funder_gate as FG
    def _lp(t):
        b = bars_15m(t["ticker"]); return b[-1]["c"] if b else None
    def _fexp(t, n):      # same-day clearance rule; no clearance date -> the first NY close after it was queued
        if clearance_expiry(t): return clearance_expired(t, n)
        q = dt.datetime.fromisoformat(t.get("funder_queued_utc") or n.isoformat()); d = q.astimezone(_NY).date()
        while not ny_session(d) or ny_close_utc(d) <= q: d += dt.timedelta(days=1)
        return n > ny_close_utc(d)
    for tk, a in FG.gate_tick({"": "us_main", "gpt": "us_gpt", "claude": "us_claude"}.get(BOOK, "us_" + BOOK), L, now, _lp, _fexp, audusd=fx, base=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(LEDGER))))):
        event("funder_gate", ticker=tk, action=a)
    open_now = sum(t["status"] == "FILLED" for t in L["trades"])
    for tk, f, to in apply_event_exit(L, hi_events(), now): event("time_exit_event_adjusted", ticker=tk, from_utc=f, to_utc=to)
    for t in sorted(L["trades"], key=lambda x: bool(x.get("bench"))):   # primaries (SLV/FLG) get first claim within a tick
        if t["status"] in ("MISSED", "STOPPED", "CLOSED", "CUT", "SHADOW", "awaiting_funder", "REJECTED_FUNDER", "EXPIRED"): continue
        if t["status"] == "ARMED" and clearance_expired(t, now):   # review fix #2: same-day clearance; never fills on a stale approval
            cut_trade(L, t["ticker"], f"clearance {t['cleared']} expired (same-day rule)", now); event("clearance_expired_cut", ticker=t["ticker"], cleared=t["cleared"]); continue
        if not (rth or force): continue
        try:
            b = bars_15m(t["ticker"]); t.pop("data_error", None)   # audit H14: an error clears once data flows again
            if b: t["last_px"] = b[-1]["c"]; t["last_px_utc"] = b[-1]["start"].isoformat()
            if t["status"] == "ARMED" and t.get("bench"):   # fix 3: bench needs full clearance; else stays ARMED, no fill
                ok, why = bench_clearance(t, now); t["bench_clearance"] = dict(ok=ok, why=why, utc=now.isoformat(timespec="seconds"))
                if not ok: continue
            if t["status"] == "ARMED":
                # audit H3 (Brett 27 Oct 19:35 WITA): a fill is committed only after causal_guard, room_check and slot_gate all pass.
                # Any exception in the fill or a gate restores the ARMED snapshot (no FILLED trade ever bypasses the gates).
                snap = copy.deepcopy(t)
                try:
                    k = try_fill(t, b, now, fx)
                    if k == "filled" and causal_guard.check(t, now):   # reject non-causal fill: back to ARMED, log
                        vio = causal_guard.check(t, now)
                        t.clear(); t.update(copy.deepcopy(snap))   # audit H3 hardening: restore the pre-fill ARMED snapshot (no fees/shares/exit/_last_bar leak)
                        t["last_causal_reject"] = dict(utc=now.isoformat(timespec="seconds"), v=vio)
                        L["causal_rejects"] = L.get("causal_rejects", 0) + 1; event("causal_reject", ticker=t["ticker"], v=vio); k = None
                    if k == "filled":
                        rc = room_check(t, bars_daily(t["ticker"]), t["entry"]); t["room_check"] = rc
                        if not rc["ok"] and rc.get("no_data"):   # audit retry-edge hardening (Brett 21:11 WITA): empty daily bars -> kept ARMED, retried next tick
                            raise RuntimeError("no daily bars at fill (data outage): retry next tick")
                        if not rc["ok"]: _shadow(t, rc["why"]); k = "shadow"
                    if k == "filled":   # audit H6: earnings inside the maximum hold -> never a position
                        es, eb = earnings_status(t, now)   # audit H6 hardening: a check error raises -> kept ARMED, retried next tick
                        t["earnings_at_fill"] = dict(utc=now.isoformat(timespec="seconds"), status=es, why=eb)
                        if es == "block": _shadow(t, eb); k = "shadow"
                    if k == "filled": k = slot_gate(t, open_now, MAX_OPEN, [x for x in L["trades"] if x["status"] == "FILLED" and x is not t])
                    if k == "filled":   # review fix #6: firm-level ticker + net-direction rules (sibling ledger read error -> kept ARMED)
                        fw = firm_gate(t, [x for x in L["trades"] if x["status"] == "FILLED" and x is not t], firm_other_open())
                        if fw: k = firm_undo(t, fw)
                    if k == "filled":   # tfo_usbook_cap_20261030: main-book US beta cap at fill -> stays ARMED (retried), no position
                        _ubw = us_cap_fill_block(t)
                        if _ubw:
                            t.clear(); t.update(copy.deepcopy(snap)); t["us_book_cap_block"] = dict(utc=now.isoformat(timespec="seconds"), why=_ubw[:200])
                            event("us_book_cap_block", ticker=t["ticker"], why=_ubw[:200]); k = None
                except Exception as e:
                    t.clear(); t.update(snap); t["data_error"] = ("gate error, kept ARMED: " + str(e))[:120]
                    event("gate_error_kept_armed", ticker=t["ticker"], e=str(e)[:120]); k = None
                open_now += k == "filled"
                if k == "filled" and t.get("risk_resized_at_fill") and not snap.get("risk_resized_at_fill"):
                    event("risk_trim", ticker=t["ticker"], note=t["risk_resized_at_fill"])   # audit H5 hardening
                if k: event(k, ticker=t["ticker"], entry=t.get("entry"), shadow_entry=t.get("shadow_entry"), bench=bool(t.get("bench")))
            if t["status"] == "FILLED" and t.get("pending_cover") and b:   # discretionary cut: cover ALL at current RTH price
                _cover(t, t["open_qty"], b[-1]["c"], t["pending_cover"], now); t.update(status="CLOSED", exit_reason=t.pop("pending_cover"))
                event("discretionary_cover", ticker=t["ticker"], exits=t["exits"][-1:]); continue
            if t["status"] == "FILLED":   # audit H9: stops/T1 first; daily bars lazily + optional (failure = no extension, never skips stops)
                for k in manage(t, b, now, None, daily_fn=(lambda tk=t["ticker"]: bars_daily(tk))): event(k, ticker=t["ticker"], exits=t["exits"][-1:] or None)
                if t.pop("daily_error", None): event("daily_data_error", ticker=t["ticker"], note="no extension this tick")
        except Exception as e: t["data_error"] = str(e)[:120]; event("data_error", ticker=t["ticker"], e=str(e)[:120])
    write_status(L, fx, now); atomic(LEDGER, L)

def write_status(L, fx, now):
    rows = []; tot = 0.0; cut = []
    for t in L["trades"]:
        if t["status"] == "CUT":
            cut.append(dict(ticker=t["ticker"], reason=t.get("cut_reason", ""), cut_wita=t.get("cut_wita"), trigger=t["trigger"], stop=t["stop"])); continue
        m = mark(t, t.get("last_px"), fx, now); tot += m["pnl_aud"]
        px = t.get("last_px"); dist = round(100 * (px / t["trigger"] - 1), 2) if px else None
        rows.append(dict(ticker=t["ticker"], status=t["status"], direction=t.get("direction", "short").upper(), price=px, price_utc=t.get("last_px_utc"),
            trigger=t["trigger"], max_fill=t["max_fill"], stop=t["trail"] or t["stop"], orig_stop=t["stop"], t1=t["t1"], t2=t["t2"],
            shares=t["shares"], open_qty=t["open_qty"], entry=t["entry"], risk_aud=t["risk_cap_aud"], dist_to_trigger_pct=dist,
            pnl_aud=m["pnl_aud"], bench=bool(t.get("bench")), shadow_entry=t.get("shadow_entry"), time_exit_wita=wita(dt.datetime.fromisoformat(t["time_exit_utc"])), time_exit_adj=t.get("time_exit_adj"), exits=t["exits"]))
    regime = "US: longs + shorts per name (own trend/RS/catalyst); SPY vs 50-day = rank input only, counter-regime names ranked lower"   # Brett 27 Oct 2026 (review fixes): label matches the per-row direction rule
    # audit H4 (Brett 27 Oct 19:35 WITA): Day P&L = today's CHANGE (realized + marks vs the first status of the WITA day), not lifetime.
    dkey = now.astimezone(WITA).date().isoformat(); base = L.get("day_base") or {}
    if base.get("wita_date") != dkey: base = L["day_base"] = dict(wita_date=dkey, pnl_aud=round(tot, 2), set_utc=now.isoformat(timespec="seconds"))
    # audit H14: freshness from the PRICE time (oldest mark of the open positions, else armed), plus data errors
    pts = [t.get("last_px_utc") for t in L["trades"] if t["status"] == "FILLED" and t.get("last_px_utc")] or \
          [t.get("last_px_utc") for t in L["trades"] if t["status"] == "ARMED" and not t.get("bench") and t.get("last_px_utc")]
    errs = [dict(ticker=t["ticker"], status=t["status"], error=t["data_error"]) for t in L["trades"] if t.get("data_error") and t["status"] in ("ARMED", "FILLED")]
    st = dict(book=L["book"], paper_only=True, updated_utc=now.isoformat(timespec="seconds"), updated_wita=wita(now), audusd=round(fx, 4),
              regime=regime, armed=sum(r["status"] == "ARMED" and not r["bench"] for r in rows), open=sum(r["status"] == "FILLED" for r in rows),
              bench_armed=sum(r["status"] == "ARMED" and r["bench"] for r in rows), shadow=sum(r["status"] == "SHADOW" for r in rows), max_open=MAX_OPEN,
              pnl_today_aud=round(tot - base["pnl_aud"], 2), pnl_since_entry_aud=round(tot, 2), pnl_today_basis=f"change since first status of WITA day {dkey} (realized + marks)",
              price_asof_utc=min(pts) if pts else None, data_error_count=len(errs), data_errors=errs[:10], costs=f"IBKR Pro {IC.PLAN.title()}: US$0.005/sh min $1 max 1% + SEC/CAT fees; 5bp slip/side; borrow = IBKR per-name fee", trades=rows, dropped=L.get("dropped", []),
              cut=cut, book_label=L.get("book_label", "Our picks"), borrow_note="Borrow: IBKR short-stock file + paper gateway tick 236 (state/ibkr_borrow.json). No borrow -> SHADOW.",
              cash_slots=L.get("cash_slots", []), causal_violations=causal_guard.audit(L["trades"], now), causal_rejects=L.get("causal_rejects", 0))
    st["causal_violations_count"] = len(st["causal_violations"])
    atomic(STATUS, st)
    # 2026-10-29 TFO 11:12 WITA fix 2 (additive): per-book health with bars_asof = latest bar used (bar START, UTC ISO). Never arms.
    try:
        _lb = [dt.datetime.fromisoformat(t["_last_bar"]) for t in L["trades"] if t.get("_last_bar")]
        health = dict(book=L["book"], market="us", paper_only=True, ok=True, open=st["open"], armed=st["armed"], bench_armed=st["bench_armed"],
                      price_asof_utc=st["price_asof_utc"], data_error_count=st["data_error_count"], bars_asof=max(_lb).isoformat() if _lb else None,
                      updated_utc=st["updated_utc"], updated_wita=st["updated_wita"])
        atomic(STATUS.replace("_status.json", "_health.json"), health)
    except Exception:
        pass
    return st

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "arm-default":
        if os.path.exists(LEDGER) and "--force" not in sys.argv: sys.exit("ledger exists; refusing to re-arm (use --force)")
        L = arm_default(); atomic(LEDGER, L); event("armed", tickers=[t["ticker"] for t in L["trades"]]); write_status(L, L["audusd_armed"], now_utc()); print("armed", [t["ticker"] for t in L["trades"]])
    elif cmd == "apply-plan":
        L = apply_plan(sys.argv[2], now_utc()); write_status(L, live_audusd(L["audusd_armed"]), now_utc())
        print([(t["ticker"], t["status"], t.get("last_px")) for t in L["trades"]])
    elif cmd == "cover":   # cover TICKER "reason": flags a FILLED trade; executed at the next RTH tick at the live price
        L = json.load(open(LEDGER)); tk = sys.argv[2].upper(); why = sys.argv[3]
        t = next(x for x in L["trades"] if x["ticker"] == tk and x["status"] == "FILLED")
        t["pending_cover"] = why; t["pending_cover_utc"] = now_utc().isoformat(timespec="seconds"); atomic(LEDGER, L); event("cover_requested", ticker=tk, why=why); print("pending", tk, why)
    elif cmd == "room-audit":   # annotate ARMED trades with room_check at max_fill (no status change); print fail counts
        L = json.load(open(LEDGER)); fails = []
        for t in L["trades"]:
            if t["status"] != "ARMED": continue
            try: t["room_check"] = room_check(t, bars_daily(t["ticker"]))
            except Exception as e: t["room_check"] = dict(ok=None, why=str(e)[:80])
            if t["room_check"].get("ok") is False: fails.append((t["ticker"], bool(t.get("bench")), t["room_check"]["room_R"]))
        L2 = json.load(open(LEDGER)); rcs = {x["ticker"]: x.get("room_check") for x in L["trades"] if x["status"] == "ARMED"}
        for t in L2["trades"]:
            if t["status"] == "ARMED" and t["ticker"] in rcs: t["room_check"] = rcs[t["ticker"]]
        atomic(LEDGER, L2); print(json.dumps(dict(book=BOOK or "main", armed=len(rcs), fail=len(fails), fails=fails)))
    elif cmd == "tick": tick("--force" in sys.argv); print(open(STATUS).read()[:1500])
    else: print(open(STATUS).read())
