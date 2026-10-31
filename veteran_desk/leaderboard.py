"""Veteran Desk DAILY LEADERBOARD (Brett 2026-10-28 20:13 WITA). PAPER only - no broker, no live orders, no real money.
Every real trading seat (book.TRADERS; placeholder code seats Sloane/Jonah/Ada never ranked) is ranked by its day P/L in A$:
  day P/L = equity now - equity at the day's opening mark - capital added to the seat inside the window
equity = cash (realised) + open trades at mark, so day P/L = realised + change in unrealised; a trade need not be closed.
Day window: 06:00 WITA -> 06:00 WITA (22:00 UTC, after the US cash close). At the cutoff the top 3 POSITIVE seats get
A$1,000 / A$750 / A$500 of new PAPER capital as capital events (type 'leaderboard_prize'), never as P/L. <3 positive -> fewer prizes.
HOLD (no award, reason logged) if any open position is MARK_MISSING, or the VIX hedge is marked off a stale quote.
WEEKLY (Brett 20:21 WITA): after the Friday-session cutoff (Sat 06:00 WITA) the seat with the highest Mon-Fri total day P/L (sum of that
week's daily-cutoff day P/L, same basis) gets an extra A$5,000 paper capital, only if positive; held if any daily cutoff that week was held or a mark is stale now.
ANTI-GAMBLING GUARDS (Brett 20:23 WITA "Yes add that"): (1) weekly A$5k only to a seat whose P/L ex capital events is at/above its own
all-time high-water mark (book seat pnl_hwm_aud) at week end; else next eligible seat; none eligible -> no winner. (2) a seat down more than 5%
week-to-date (equity vs Monday open mark, ex capital added) sits out daily prizes for the rest of that week ("sitting out (DD)").
Per-trade rails unchanged (2-5% risk cap, stop on every trade).
  python3 -m veteran_desk.leaderboard cutoff | standings | init"""
import datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from veteran_desk import book as BK
UTC = dt.timezone.utc; WITA = dt.timezone(dt.timedelta(hours=8)); CUTOFF_WITA = (6, 0)
PRIZES = (1000.0, 750.0, 500.0); WEEK_DD_SITOUT = -0.05; WEEKLY_PRIZE = 5000.0; EXCLUDE = ("blind_challenger", "devils_advocate", "risk_gate", "head_of_desk")
SD = os.path.join(BK.BASE, "state", "veteran_desk")
AWARDS = os.path.join(SD, "leaderboard_awards.jsonl")   # one line per cutoff: awarded | held | no_winners
OPEN = os.path.join(SD, "leaderboard_open.json")        # the day's opening marks per seat
LIVE = os.path.join(SD, "leaderboard.json")             # last standings (dashboard)
def _now(): return dt.datetime.now(UTC)
def window_start(t):
    w = t.astimezone(WITA); c = w.replace(hour=CUTOFF_WITA[0], minute=CUTOFF_WITA[1], second=0, microsecond=0)
    return (c if w >= c else c - dt.timedelta(days=1)).astimezone(UTC)
def _wj(p, o):
    tmp = p + ".tmp"; json.dump(o, open(tmp, "w"), indent=1, default=str); os.replace(tmp, p)
def _rj(p, d=None):
    try: return json.load(open(p))
    except Exception: return d
def _seat_of_event(e):
    if e.get("seat"): return e["seat"]
    s = str(e.get("source", "")); return s.split("for seat ", 1)[1].split()[0] if "for seat " in s else None
def seats_ranked(D): return [s for s in BK.TRADERS if s in D["seats"] and s not in EXCLUDE]
def week_monday(t): w = window_start(t).astimezone(WITA).date(); return (w - dt.timedelta(days=w.weekday())).isoformat()
def prefill_equity(B, ws, t):
    """Follow-up 1 (TFO HT 21:16): day baseline = seat equity BEFORE any fill inside the window, so entry costs count against the day.
    For each position/closed trade filled in [ws, t) the pre-fill equity is equity_at_mark + its entry cost (at the fill instant mark == fill price,
    so the only equity change a fill makes is its cost) minus any P/L accrued after the fill up to the mark. Simplest exact form available:
    baseline = last snapshot taken before the first in-window fill if one exists, else current equity + in-window entry costs."""
    ws_ = ws.isoformat() if hasattr(ws, "isoformat") else str(ws); t_ = t.isoformat()
    fills = []
    for p in list(B.get("positions", {}).values()) + list(B.get("closed", [])):
        fu = (p.get("off_hours_fill_audit") or {}).get("filled_utc") or p.get("filled_utc")
        if fu and ws_ <= fu < t_: fills.append((fu, float(p.get("cost_aud") or 0)))
    eq = float(B.get("equity_aud") or B["seed_aud"])
    if not fills: return eq, None
    first = min(f[0] for f in fills); snap = None
    try:
        for l in open(os.path.join(SD, "seat_equity_snapshots.jsonl")):
            r = json.loads(l)
            if ws_ <= r["utc"] < first and B.get("_seat") in r.get("seats", {}): snap = float(r["seats"][B["_seat"]]["equity_aud"])
    except Exception: pass
    if snap is not None: return snap, "snapshot before first fill %s" % first
    return eq + sum(c for _, c in fills), "equity + in-window entry costs"
def open_marks(D, t, prev=None):
    ws = window_start(t); eqs = {}; why = {}
    for s in seats_ranked(D):
        B = dict(D["seats"][s], _seat=s); eqs[s], w = prefill_equity(B, ws, t)
        if w: why[s] = w
    wo = (prev or {}).get("week_open")
    if not wo or wo.get("monday") != week_monday(t): wo = dict(monday=week_monday(t), taken_utc=t.isoformat(), seats=dict(eqs), sat_out={})
    for s in eqs: wo["seats"].setdefault(s, eqs[s])
    return dict(week_open=wo, window_start_utc=window_start(t).isoformat(), window_start_wita=window_start(t).astimezone(WITA).strftime("%Y-%m-%d %H:%M WITA"),
                taken_utc=t.isoformat(), seats=eqs, baseline="pre-fill mark (entry costs count against the day)", baseline_why=why)
def holds(D):
    why = []
    for s in seats_ranked(D):
        for p in D["seats"][s]["positions"].values():
            if p.get("status") == "open" and p.get("mark_missing"): why.append("MARK_MISSING %s %s (no real mark at last tick)" % (s, p["ticker"]))
    H = D.get("hedge") or {}
    try:
        from veteran_desk import feeds as FD
        if H.get("vix_asof"):
            st_, w_ = FD.vix_stale(H["vix_asof"], _now())
            if st_: why.append("VIX hedge stale: %s %s (%s)" % (H.get("vix_src"), H["vix_asof"], w_))
    except Exception as x: why.append("VIX staleness check failed: %s" % x)
    if H.get("mark_note") or (H.get("premium_aud") and H.get("value_src") != "live"): why.append("VIX hedge stale: " + (H.get("mark_note") or "hedge value_src=%s" % H.get("value_src")))
    try:
        if (_now() - dt.datetime.fromisoformat(D["updated"])).total_seconds() > 1800: why.append("book not ticked in last 30 min (updated %s)" % D["updated"])
    except Exception: why.append("book has no updated time")
    return why
def equity_mismatch(rows, Dfresh=None):
    """Fix 3/7: compare leaderboard rows against what seat_cli status reports now (fresh book read). >A$1 on equity or since-start P/L -> hold prizes."""
    try: Dfresh = Dfresh or json.load(open(BK.BOOK))
    except Exception as x: return ["equity check: book unreadable (%s)" % type(x).__name__]
    why = []
    for r in rows:
        B = (Dfresh.get("seats") or {}).get(r["seat"])
        if B is None: continue
        V = BK.seat_view(B)
        for k in ("equity_aud", "pnl_since_inception_aud"):
            a, b = r.get(k), V.get(k)
            if a is not None and b is not None and abs(float(a) - float(b)) > 1.0: why.append("EQUITY MISMATCH %s %s leaderboard %.2f vs seat_cli %.2f (>A$1)" % (r["seat"], k, float(a), float(b)))
    return why
def standings(D=None, O=None, t=None):
    D = D or BK.load(); t = t or _now(); O = O or _rj(OPEN) or open_marks(D, t); ws = O["window_start_utc"]
    add = {}
    for e in D.get("capital_events", []):
        s = _seat_of_event(e)
        if s and str(e.get("utc", "")) > O.get("taken_utc", ws): add[s] = add.get(s, 0.0) + float(e.get("amount_aud") or 0)
    rows = []
    for s in seats_ranked(D):
        B = D["seats"][s]; eq = float(BK.seat_view(B)["equity_aud"])   # fix 3/7: one source with seat_cli
        a = add.get(s, 0.0)
        op = float(O["seats"][s]) if s in O["seats"] else float(B["seed_aud"]) - a   # seat born mid-window opens at its seed (seed injection is capital, not P/L)
        rows.append(dict(seat=s, open_equity_aud=round(op, 2), equity_aud=round(eq, 2), capital_added_aud=round(a, 2), day_pnl_aud=round(eq - op - a, 2),
                         open_trades=sum(1 for p in B["positions"].values() if p.get("status") == "open"), mark_missing=[p["ticker"] for p in B["positions"].values() if p.get("status") == "open" and p.get("mark_missing")]))
    wo = O.get("week_open") or {}; wadd = {}
    for e in D.get("capital_events", []):
        s_ = _seat_of_event(e)
        if s_ and str(e.get("utc", "")) > wo.get("taken_utc", "9"): wadd[s_] = wadd.get(s_, 0.0) + float(e.get("amount_aud") or 0)
    for r in rows:
        B = D["seats"][r["seat"]]; w0 = (wo.get("seats") or {}).get(r["seat"])
        r["wtd_pnl_aud"] = round(r["equity_aud"] - w0 - wadd.get(r["seat"], 0.0), 2) if w0 else None
        r["wtd_pct"] = round(r["wtd_pnl_aud"] / w0 * 100, 2) if w0 else None
        r["sitting_out_dd"] = bool((wo.get("sat_out") or {}).get(r["seat"]) or (r["wtd_pct"] is not None and r["wtd_pct"] < WEEK_DD_SITOUT * 100))
        r["pnl_since_inception_aud"] = BK.seat_view(B)["pnl_since_inception_aud"]; r["pnl_hwm_aud"] = B.get("pnl_hwm_aud", 0.0)
        r["at_hwm"] = (B.get("pnl_since_inception_aud") or 0) >= (B.get("pnl_hwm_aud") or 0) - 0.005
    rows.sort(key=lambda r: (-r["day_pnl_aud"], r["seat"]))
    for i, r in enumerate(rows): r["rank"] = i + 1
    return dict(window_start_utc=ws, window_start_wita=O.get("window_start_wita"), cutoff_wita="%02d:%02d WITA daily" % CUTOFF_WITA, asof_utc=t.isoformat(), book_updated_utc=D.get("updated"),
                rows=rows, holds=holds(D), prizes_aud=list(PRIZES), week_open=O.get("week_open"), guards=GUARDS, rule="Top 3 positive day P/L (realised + change in unrealised from the 06:00 WITA opening mark) win A$1,000/750/500 paper capital. Stale mark or stale VIX hedge = held.")
GUARDS = ["weekly A$5k only if seat P/L (ex capital events) is at/above its own all-time high-water mark at week end; else next eligible; none -> no winner",
          "seat down >5% week-to-date (equity vs Monday open mark, ex capital added) sits out daily prizes for the rest of the week",
          "per-trade rails unchanged: 2-5% risk cap, stop on every trade"]
def _awards(): 
    try: return [json.loads(l) for l in open(AWARDS) if l.strip()]
    except FileNotFoundError: return []
def apply_prizes(D, au, t):
    """Called inside book.tick (real book only): credit every 'awarded' line not yet in capital_events. Capital, not P/L."""
    done = {e.get("award_id") for e in D.get("capital_events", [])}
    for a in _awards():
        if a.get("status") != "awarded": continue
        for w in a["winners"]:
            aid = "%s#%d" % (a["day"], w["rank"])
            if aid in done or w["seat"] not in D["seats"]: continue
            B = D["seats"][w["seat"]]; x = float(w["prize_aud"])
            B["cash_aud"] += x; B["seed_aud"] = B["seed_aud"] + x
            if B.get("day_start_equity_aud") is not None: B["day_start_equity_aud"] += x
            D["seed_aud"] = D.get("seed_aud", 0) + x; D["start"] = round(D.get("start", 0) + x * au, 2)
            if D.get("day_start_equity_aud") is not None: D["day_start_equity_aud"] += x
            D.setdefault("capital_events", []).append(dict(type="leaderboard_prize", award_id=aid, seat=w["seat"], rank=w["rank"], amount_aud=x, amount_usd=round(x * au, 2), utc=t.isoformat(),
                source="daily leaderboard prize %s rank %d for seat %s (paper capital, not P&L)" % (a["day"], w["rank"], w["seat"]), authorised="Brett 2026-10-28 20:13 WITA"))
            try:
                import audit
                audit.capital("veteran", {"type": "leaderboard_prize", "award_id": aid, "seat": w["seat"], "rank": w["rank"], "amount_aud": x, "paper_only": True})
            except Exception:
                pass
def weekly(day, holds_now, D=None):
    """day = WITA date the daily window opened. Runs only when that is a Friday (US Friday session, cutoff Sat 06:00 WITA)."""
    d0 = dt.date.fromisoformat(day)
    if d0.weekday() != 4: return None
    mon = d0 - dt.timedelta(days=4); wid = "week-%s" % mon.isoformat()
    A = _awards()
    if any(a.get("day") == wid for a in A): return None
    days = [a for a in A if a.get("kind", "daily") == "daily" and mon.isoformat() <= a.get("day", "") <= day]
    tot = {}
    for a in days:
        for r in a.get("standings", []): tot[r["seat"]] = round(tot.get(r["seat"], 0.0) + r["day_pnl_aud"], 2)
    rows = sorted(({"seat": k, "week_pnl_aud": v} for k, v in tot.items()), key=lambda r: (-r["week_pnl_aud"], r["seat"]))
    rec = dict(kind="weekly", day=wid, week_mon_to_fri=[mon.isoformat(), day], days_counted=sorted(a["day"] for a in days), cutoff_utc=_now().isoformat(), paper_only=True, standings=rows)
    held = [a["day"] for a in days if a.get("status") == "held"]
    why = (["daily cutoff held on %s (stale price)" % ", ".join(held)] if held else []) + list(holds_now)
    S_ = (D or {}).get("seats") or {}; skipped = []
    def hwm_ok(s):
        B = S_.get(s) or {}; return (B.get("pnl_since_inception_aud") or 0) >= (B.get("pnl_hwm_aud") or 0) - 0.005
    top = None
    for r in rows:
        if r["week_pnl_aud"] <= 0: break
        if hwm_ok(r["seat"]): top = r; break
        skipped.append(dict(seat=r["seat"], week_pnl_aud=r["week_pnl_aud"], why="below own high-water mark (P/L %s < HWM %s)" % ((S_.get(r["seat"]) or {}).get("pnl_since_inception_aud"), (S_.get(r["seat"]) or {}).get("pnl_hwm_aud"))))
    rec.update(guards=GUARDS, skipped_below_hwm=skipped)
    if why: rec.update(status="held", winners=[], held_reasons=why, would_win=[dict(rank=1, seat=top["seat"], week_pnl_aud=top["week_pnl_aud"], prize_aud=WEEKLY_PRIZE)] if top else [])
    elif not top: rec.update(status="no_winners", winners=[], note="no seat had a positive Mon-Fri total at/above its high-water mark")
    else: rec.update(status="awarded", winners=[dict(rank=1, seat=top["seat"], week_pnl_aud=top["week_pnl_aud"], prize_aud=WEEKLY_PRIZE)])
    return rec
def week_so_far(t=None):
    t = t or _now(); w = window_start(t).astimezone(WITA).date(); mon = w - dt.timedelta(days=w.weekday())
    tot = {}
    for a in _awards():
        if a.get("kind", "daily") == "daily" and mon.isoformat() <= a.get("day", "") <= (mon + dt.timedelta(days=4)).isoformat():
            for r in a.get("standings", []): tot[r["seat"]] = round(tot.get(r["seat"], 0.0) + r["day_pnl_aud"], 2)
    return dict(week_monday=mon.isoformat(), rows=sorted(({"seat": k, "week_pnl_aud": v} for k, v in tot.items()), key=lambda r: (-r["week_pnl_aud"], r["seat"])))
def cutoff(t=None, tick=True):
    t = t or _now(); os.makedirs(SD, exist_ok=True)
    D = BK.tick() if tick else BK.load()   # fresh real marks first
    O = _rj(OPEN)
    if not O: O = open_marks(D, t); _wj(OPEN, O); return dict(ok=True, action="init", open=O)
    day = dt.datetime.fromisoformat(O["window_start_utc"]).astimezone(WITA).date().isoformat()
    if any(a.get("day") == day and a.get("kind", "daily") == "daily" for a in _awards()): return dict(ok=True, action="already_done", day=day)
    if t < dt.datetime.fromisoformat(O["window_start_utc"]) + dt.timedelta(hours=20): return dict(ok=False, action="too_early", day=day, window_start=O["window_start_utc"])
    st = standings(D, O, t); hw = st["holds"] + equity_mismatch(st["rows"])   # fix 3/7
    sat = [r["seat"] for r in st["rows"] if r.get("sitting_out_dd")]
    pos = [r for r in st["rows"] if r["day_pnl_aud"] > 0 and not r.get("sitting_out_dd")][:len(PRIZES)]
    rec = dict(kind="daily", day=day, window_start_utc=O["window_start_utc"], cutoff_utc=t.isoformat(), cutoff_wita=t.astimezone(WITA).strftime("%Y-%m-%d %H:%M WITA"), paper_only=True, guards=GUARDS, sitting_out_dd=sat,
               standings=[{k: r.get(k) for k in ("rank", "seat", "day_pnl_aud", "open_equity_aud", "equity_aud", "capital_added_aud", "wtd_pnl_aud", "wtd_pct", "sitting_out_dd", "pnl_since_inception_aud", "pnl_hwm_aud", "at_hwm")} for r in st["rows"]])
    if hw: rec.update(status="held", winners=[], held_reasons=hw, would_win=[dict(rank=i + 1, seat=r["seat"], day_pnl_aud=r["day_pnl_aud"], prize_aud=PRIZES[i]) for i, r in enumerate(pos)])
    elif not pos: rec.update(status="no_winners", winners=[], note="no seat had a positive day")
    else: rec.update(status="awarded", winners=[dict(rank=i + 1, seat=r["seat"], day_pnl_aud=r["day_pnl_aud"], prize_aud=PRIZES[i]) for i, r in enumerate(pos)])
    with open(AWARDS, "a") as fh: fh.write(json.dumps(rec, default=str) + "\n")
    for x in sat: O.setdefault("week_open", {}).setdefault("sat_out", {})[x] = day   # sticky for the rest of the week
    wk = weekly(day, hw, D)
    if wk:
        with open(AWARDS, "a") as fh: fh.write(json.dumps(wk, default=str) + "\n")
        rec["weekly"] = wk
    D = BK.tick() if (tick and (rec["status"] == "awarded" or (wk or {}).get("status") == "awarded")) else D   # apply prize capital now (idempotent)
    _wj(OPEN, open_marks(D, t, O)); _wj(LIVE, standings(D, _rj(OPEN), t))
    return dict(ok=True, action=rec["status"], record=rec)
if __name__ == "__main__":
    c = (sys.argv[1:] or ["standings"])[0]
    if c == "cutoff": print(json.dumps(cutoff(), default=str))
    elif c == "init":
        D = BK.load(); O = _rj(OPEN)
        if O: print(json.dumps(dict(ok=False, error="open marks already exist", open=O)))
        else: O = open_marks(D, _now()); _wj(OPEN, O); print(json.dumps(dict(ok=True, open=O)))
    else: s = standings(); _wj(LIVE, s); print(json.dumps(s, default=str))
