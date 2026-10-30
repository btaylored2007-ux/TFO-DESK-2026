"""THF seat accountability (Brett standing order 2026-10-30 19:32 WITA). PAPER only; THF book only (THF_DESK=1).
Every round (= one seat wake/desk run): a seat with negative net P&L (week-to-date OR since inception) gets a STERN REVIEW
line in its wake prompt (loss in A$, where it went wrong from its closed trades + lesson log) and must return a written
recovery plan for Marcus (mitigation + clawback WITHIN the rails: no revenge trades, no oversizing, no C setups).
Winning seats get specific praise. A seat red for 3 straight rounds drops to HALF RISK until it posts a green round
(round equity change > 0). tfo_review_20261030: floor is 1% for A (tfo_a1pct_20261031 Brett 00:07: 1% allowed until proven; NEVER force up to 2%); non-A needs 2%; half of an A sizes to max(half, 1%)
(A+ 4% -> 2% under half). Crypto floor 0.5%. BOTH engines: THF (THF_DESK=1, state/thf)
and the Veteran Desk (reports/veteran_desk_book.json, state/veteran_desk). Under pytest writes go to a temp dir, never live.
State: state/thf/seat_rounds.json; plans: state/thf/recovery_plans.jsonl (shown on the THF dash)."""
import datetime as dt, json, os, threading
_LK = threading.Lock()
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def _paths():
    """(state dir, book path) for the running engine; pytest -> temp dir unless SEAT_REVIEW_DIR is set (never the live state)."""
    o = os.environ.get("SEAT_REVIEW_DIR")
    if active(): d, b = os.path.join(BASE, "state", "thf"), os.path.join(BASE, "state", "thf", "thf_book.json")
    else: d, b = os.path.join(BASE, "state", "veteran_desk"), os.path.join(BASE, "reports", "veteran_desk_book.json")
    if o: d = o
    elif os.environ.get("PYTEST_CURRENT_TEST"):
        import tempfile; d = os.path.join(tempfile.gettempdir(), "seat_review_pytest_%d" % os.getpid())
    return d, b
class _P(str): pass
def _D(): return _paths()[0]
def _BOOKP(): return os.environ.get("SEAT_REVIEW_BOOK") or _paths()[1]
def _ROUNDS(): return os.path.join(_D(), "seat_rounds.json")
def _PLANS(): return os.path.join(_D(), "recovery_plans.jsonl")
def enabled(): return True   # tfo_review_20261030: stern review / praise / half risk on BOTH engines
WITA = dt.timezone(dt.timedelta(hours=8)); RED_ROUNDS_HALF = 3; HALF = 0.5
def active(): return os.environ.get("THF_DESK") == "1"
def _rj(p, d=None):
    try: return json.load(open(p))
    except Exception: return d
def _wj(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True); t = p + ".tmp.%d" % os.getpid(); json.dump(o, open(t, "w"), indent=1, default=str); os.replace(t, p)
def _week(t=None): t = (t or dt.datetime.now(WITA)); return "%d-W%02d" % t.isocalendar()[:2]
def _seat(seat, B=None):
    B = B if B is not None else (_rj(_BOOKP(), {}) or {}); s = (B.get("seats") or {}).get(seat) or {}
    seed = float(s.get("seed_aud") or 500000.0); eq = float(s.get("equity_aud") or s.get("cash_aud") or seed); return s, seed, eq
def _round_update(seat, round_id, B=None):
    """Advance the seat's round counter once per round_id; returns the seat's round state."""
    R = _rj(_ROUNDS(), {}) or {}; st = R.setdefault(seat, dict(red_streak=0, half_risk=False, last_eq=None, week=None, week_start_eq=None, rounds=0))
    s, seed, eq = _seat(seat, B); wk = _week()
    if st.get("week") != wk: st.update(week=wk, week_start_eq=st.get("last_eq") if st.get("last_eq") is not None else seed)
    if st.get("last_round") != round_id:
        delta = None if st.get("last_eq") is None else eq - float(st["last_eq"])
        inc = eq - seed; wtd = eq - float(st.get("week_start_eq") or seed); red = inc < -0.5 or wtd < -0.5
        green = delta is not None and delta > 0.5
        if green: st.update(red_streak=0, half_risk=False, half_cleared_round=round_id) if st.get("half_risk") else st.update(red_streak=0)
        elif red: st["red_streak"] = int(st.get("red_streak") or 0) + 1
        elif not st.get("half_risk"): st["red_streak"] = 0   # a non-red round breaks the streak (half risk itself only clears on a green round)
        if st["red_streak"] >= RED_ROUNDS_HALF and not st.get("half_risk"): st.update(half_risk=True, half_since_round=round_id, half_since_wita=dt.datetime.now(WITA).strftime("%F %H:%M WITA"))
        st.update(last_round=round_id, last_eq=round(eq, 2), last_delta=None if delta is None else round(delta, 2), inc_aud=round(inc, 2), wtd_aud=round(wtd, 2), red=red, rounds=int(st.get("rounds") or 0) + 1, updated_wita=dt.datetime.now(WITA).strftime("%F %H:%M WITA"))
        _wj(_ROUNDS(), R)
    return st
def round_update(seat, round_id, B=None):
    with _LK: return _round_update(seat, round_id, B)
def size_mult(seat):
    return HALF if ((_rj(_ROUNDS(), {}) or {}).get(seat) or {}).get("half_risk") else 1.0
def _went_wrong(s):
    L = [c for c in (s.get("closed") or []) if float(c.get("net_aud") or 0) < 0][-3:]
    return "; ".join("%s %s %s net A$%.0f" % (c.get("ticker"), c.get("side"), c.get("exit_reason"), float(c.get("net_aud") or 0)) for c in L) or "no losing closes yet - loss is open/marked positions: " + (", ".join("%s %s" % (p.get("ticker"), p.get("side")) for p in (s.get("positions") or {}).values() if p.get("status") == "open") or "none open")
def prompt_line(seat, round_id):
    try:
        st = round_update(seat, round_id); s, seed, eq = _seat(seat); inc, wtd = eq - seed, st.get("wtd_aud") or 0.0
        if inc < -0.5 or wtd < -0.5:
            loss = min(inc, wtd)
            t = ("\nSTERN REVIEW (%s standing order, Brett 30 Oct 19:32 WITA): your seat is DOWN A$%s (week-to-date A$%s, since inception A$%s). Where it went wrong: %s. "
                 "Read your lessons above and own it. You MUST add to your JSON a field \"recovery_plan\": {\"loss_aud\":num,\"what_went_wrong\":str,\"mitigation\":str,\"clawback_within_rails\":str,\"no_revenge_no_oversize_no_C\":true} - it is reported to Marcus. "
                 "Clawback only inside the rails: no revenge trades, no oversizing, no C setups, stop on every trade, A 1-2%% (1%% ok until proven, never force up) / A+ 4%% / cap 5%%." % ("THF" if active() else "Veteran Desk", "{:,.0f}".format(-loss), "{:,.0f}".format(wtd), "{:,.0f}".format(inc), _went_wrong(s)))
            if st.get("half_risk"): t += " HALF RISK IN FORCE: red %d straight rounds - your risk is halved until you post a green round (A at half sizes to the 1%% floor - allowed, never rejected; non-A below 2%% is rejected; A+ 4%% -> 2%%)." % st["red_streak"]
            elif st.get("red_streak"): t += " Red %d straight round(s); at %d you drop to half risk." % (st["red_streak"], RED_ROUNDS_HALF)
            return t + "\n"
        if inc > 0.5 or wtd > 0.5:
            best = max((s.get("closed") or []), key=lambda c: float(c.get("net_aud") or 0), default=None)
            return ("\nPRAISE (%s): you are UP A$%s since inception (week-to-date A$%s)%s. Good discipline - keep the same process, do not size up on the back of it.\n"
                    % ("THF" if active() else "Veteran Desk", "{:,.0f}".format(inc), "{:,.0f}".format(wtd), (" - best trade %s %s net A$%.0f (%s)" % (best.get("ticker"), best.get("side"), float(best.get("net_aud") or 0), best.get("exit_reason"))) if best and float(best.get("net_aud") or 0) > 0 else ""))
        return ""
    except Exception as x: return "\n(seat review unavailable: %s)\n" % type(x).__name__
def record_plan(seat, round_id, j, trader=None):
    """Persist the seat's recovery plan (or a 'missing' row when a red seat did not give one)."""
    st = ((_rj(_ROUNDS(), {}) or {}).get(seat) or {})
    if not st.get("red"): return
    plan = (j or {}).get("recovery_plan") if isinstance(j, dict) else None
    row = dict(utc=dt.datetime.now(dt.timezone.utc).isoformat(), wita=dt.datetime.now(WITA).strftime("%F %H:%M WITA"), round=round_id, seat=seat, trader=trader, to="Marcus (head of desk)",
               inc_aud=st.get("inc_aud"), wtd_aud=st.get("wtd_aud"), red_streak=st.get("red_streak"), half_risk=st.get("half_risk"), status="filed" if plan else "MISSING - seat gave no recovery plan", plan=plan)
    os.makedirs(_D(), exist_ok=True); open(_PLANS(), "a").write(json.dumps(row, default=str) + "\n")
def dash():
    R = _rj(_ROUNDS(), {}) or {}; last = {}
    try:
        for l in open(_PLANS()).read().splitlines()[-500:]:
            if l.strip(): r = json.loads(l); last[r["seat"]] = r
    except Exception: pass
    return dict(rounds=R, plans=last, rule="red (WTD or inception) -> stern review + recovery plan to Marcus; red %d straight rounds -> half risk until a green round" % RED_ROUNDS_HALF)
