"""seatagents_20261103 (PAPER): Helen Park (CFO) money view - READ-ONLY from the real ledgers (Brett HARD 21:13 WITA 2 Nov, fm-20261102131330-7fe9).
Sources: state/thf/thf_book.json (seats[*] equity_aud, seed_aud, cash_aud, invested_aud, today_pnl_aud, commission_expense_aud, closed[*].net_aud/cost_aud,
positions[*]), state/thf/thf_book.json.snapshots.jsonl (equity history -> week/month P&L), state/thf/leaderboard_open.json (week open),
reports/firm_risk.json (TFO Risk drawdown/level), logs/claude_brain.jsonl + logs/seat_brain.jsonl (AI calls + prompt chars per seat = cost proxy; no $ invented).
Output: ELI10 A$ report (daily/weekly/monthly P&L net of costs, cash vs invested, drawdown + firm heat, fee pace vs 19%/yr, per-seat league with AI cost,
ADVISORY allocation suggestions - the CFO never moves money). Posted to the chat as 'cfo' at 07:00 and 21:00 WITA (systemd tfo-thf-cfo-report.timer)
and mailed to Marcus (reporting chain). Never writes any ledger."""
import datetime as dt, json, os, time
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOK = os.path.join(BASE, "state", "thf", "thf_book.json"); SNAP = BOOK + ".snapshots.jsonl"; LBO = os.path.join(BASE, "state", "thf", "leaderboard_open.json")
FIRM = os.path.join(BASE, "reports", "firm_risk.json"); OUT = os.path.join(BASE, "state", "thf", "cfo_report.json"); AICOST = os.path.join(BASE, "state", "thf", "seat_ai_cost.json")
AILOGS = (os.path.join(BASE, "logs", "claude_brain.jsonl"), os.path.join(BASE, "logs", "seat_brain.jsonl"))
WITA = dt.timezone(dt.timedelta(hours=8)); BENCH_PA = 0.19   # funder benchmark 19%/yr before fee
HEAT_WARN = 0.10; HEAT_CAP = 0.15   # book heat cap 15% open risk (book rules)
def _f(x, d=0.0):
    try: return float(x) if x is not None else d
    except Exception: return d
def _rj(p, d=None):
    try: return json.load(open(p))
    except Exception: return d
def _k(x): return ("-" if x < 0 else "+" if x > 0 else "") + ("A$%.1fk" % (abs(x) / 1e3) if abs(x) >= 1000 else "A$%.0f" % abs(x))
def _m(x): return "A$%.2fm" % (x / 1e6) if abs(x) >= 1e6 else _k(x).lstrip("+")
def snapshots(path=None, n=5000):
    try: rows = [json.loads(l) for l in open(path or SNAP).read().splitlines()[-n:] if l.strip()]
    except Exception: rows = []
    return [r for r in rows if isinstance(r, dict) and r.get("utc")]
def _ts(u):
    try: return dt.datetime.fromisoformat(str(u).replace("Z", "+00:00")).timestamp()
    except Exception: return None
def equity_at(rows, when_ts):
    """{seat: equity_aud} from the last snapshot at/before when_ts (None when no snapshot that old)."""
    best = None
    for r in rows:
        t = _ts(r.get("utc"))
        if t is not None and t <= when_ts: best = r
    if not best: return None
    return {s: _f(v.get("equity_aud")) for s, v in (best.get("seats") or {}).items() if isinstance(v, dict) and v.get("equity_aud") is not None}
def ai_cost(hours=24, logs=None, now=None):
    """Per-seat AI calls and prompt chars in the window (cost proxy). No dollar figure is invented."""
    now = now or time.time(); out = {}
    for p in (logs or AILOGS):
        try:
            for l in open(p):
                try: r = json.loads(l)
                except Exception: continue
                t = _ts(r.get("utc"))
                if t is None or now - t > hours * 3600: continue
                s = str(r.get("seat") or "?"); o = out.setdefault(s, dict(calls=0, ok=0, prompt_chars=0, out_chars=0, secs=0.0))
                o["calls"] += 1; o["ok"] += 1 if r.get("ok") else 0; o["prompt_chars"] += int(_f(r.get("prompt_chars"))); o["secs"] += _f(r.get("secs"))
                o["out_chars"] = o.get("out_chars", 0) + int(_f(r.get("chars")))   # seatagents_20261103: answer size too (token estimate = all chars / 4)
        except Exception: continue
    return out
def write_aicost(cost=None, hours=24):
    """seatagents_20261103 (Brett/HT 21:10 WITA 2 Nov "count AI token cost per seat on the Health tab"): state/thf/seat_ai_cost.json per seat:
    calls, ok, prompt/answer chars and an ESTIMATED token count (chars / 4; Claude runs on Brett's flat Ultra plan, so no $ figure is invented).
    Refreshed every 10 min by tfo-thf-aicost.timer and by every CFO report; the Health tab reads this file."""
    cost = ai_cost(hours) if cost is None else cost; seats = {}
    for s, o in sorted(cost.items()):
        pc = int(o.get("prompt_chars", 0)); oc = int(o.get("out_chars", 0))
        seats[s] = dict(calls=int(o.get("calls", 0)), ok=int(o.get("ok", 0)), prompt_kchars=round(pc / 1000.0, 1), answer_kchars=round(oc / 1000.0, 1),
                        est_tokens=int((pc + oc) / 4), ai_minutes=round(_f(o.get("secs")) / 60.0, 1))
    d = dict(wita=dt.datetime.now(WITA).strftime("%Y-%m-%d %H:%M WITA"), hours=hours, seats=seats,
             totals=dict(calls=sum(v["calls"] for v in seats.values()), est_tokens=sum(v["est_tokens"] for v in seats.values())),
             note="AI usage per seat in the last %dh from logs/claude_brain.jsonl + logs/seat_brain.jsonl; est_tokens = characters / 4 (estimate); no dollar price invented (Claude = flat Ultra plan)" % hours)
    tmp = AICOST + ".tmp"; json.dump(d, open(tmp, "w"), indent=1); os.replace(tmp, AICOST); return d
def compute(B=None, snaps=None, firm=None, cost=None, now=None, week_open=None, days=None):
    """All the numbers (pure; unit-tested with a fake book). days = day_clock.seat_days() (live default): 'today' = the dashboard's 06:00 WITA clock;
    the book's midnight day is kept as book_day and labelled "book day (rails)" (seatagents_20261103 16:40, dayclock_20261103)."""
    if days is None and B is None:
        try:
            from veteran_desk import day_clock as DC; days = DC.seat_days()
        except Exception: days = {}
    dayc = days or {}; DS = dayc.get("seats") or {}
    B = B if B is not None else (_rj(BOOK, {}) or {}); now = now or dt.datetime.now(WITA); snaps = snapshots() if snaps is None else snaps
    firm = firm if firm is not None else (_rj(FIRM, {}) or {}); cost = ai_cost() if cost is None else cost
    S = B.get("seats") or {}; seats = {}
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0); wk0 = day0 - dt.timedelta(days=day0.weekday()); mo0 = day0.replace(day=1)
    eq_w = equity_at(snaps, wk0.timestamp()); eq_m = equity_at(snaps, mo0.timestamp())
    if eq_w is None and week_open is None: week_open = ((_rj(LBO, {}) or {}).get("week_open") or {}).get("seats")
    tot = dict(equity=0.0, seed=0.0, cash=0.0, invested=0.0, today=0.0, book_day=0.0, week=0.0, month=0.0, since_seed=0.0, commissions=0.0, closed_net=0.0, closed_costs=0.0, open_risk=0.0, n_open=0, n_armed=0, n_closed=0)
    for s, sb in S.items():
        if not isinstance(sb, dict): continue
        eq = _f(sb.get("equity_aud")); seed = _f(sb.get("seed_aud")) or eq; bday = _f(sb.get("today_pnl_aud"))
        if bday == 0 and sb.get("day_start_equity_aud"): bday = eq - _f(sb.get("day_start_equity_aud"))
        _dc = DS.get(s) or {}; today = _f(_dc.get("day_aud")) if _dc.get("day_aud") is not None else bday   # dayclock: 06:00 WITA dashboard clock, book day as fallback
        closed = [c for c in (sb.get("closed") or []) if isinstance(c, dict)]
        cnet = sum(_f(c.get("net_aud")) for c in closed if c.get("net_aud") is not None); ccost = sum(_f(c.get("cost_aud")) for c in closed if c.get("cost_aud") is not None)
        pos = [p for p in (sb.get("positions") or {}).values() if isinstance(p, dict)]
        opn = [p for p in pos if str(p.get("status")) == "open"]; armed = [p for p in pos if str(p.get("status")) == "armed"]
        orisk = sum(_f(p.get("risk_aud")) for p in opn + armed)
        wk = (eq - eq_w[s]) if (eq_w and s in eq_w) else ((eq - _f(week_open.get(s))) if (week_open and week_open.get(s)) else None)
        mo = (eq - eq_m[s]) if (eq_m and s in eq_m) else (eq - seed)   # book younger than the month: since seed
        row = dict(equity=round(eq, 2), seed=round(seed, 2), since_seed=round(eq - seed, 2), today=round(today, 2), book_day=round(bday, 2), today_clock=_dc.get("clock") or "book", week=None if wk is None else round(wk, 2), month=round(mo, 2),
                   cash=round(_f(sb.get("cash_aud")), 2), invested=round(_f(sb.get("invested_aud")), 2), commissions=round(_f(sb.get("commission_expense_aud")), 2),
                   closed_n=len(closed), closed_filled=sum(1 for c in closed if c.get("fill") is not None), closed_net=round(cnet, 2), closed_costs=round(ccost, 2),
                   open_n=len(opn), armed_n=len(armed), open_risk=round(orisk, 2), heat_pct=round(100 * orisk / eq, 2) if eq else None,
                   ai_calls=int((cost.get(s) or {}).get("calls", 0)), ai_prompt_kchars=round((cost.get(s) or {}).get("prompt_chars", 0) / 1000.0, 1))
        seats[s] = row
        tot["equity"] += eq; tot["seed"] += seed; tot["cash"] += row["cash"]; tot["invested"] += row["invested"]; tot["today"] += today; tot["book_day"] += bday; tot["week"] += (wk or 0.0); tot["month"] += mo
        tot["since_seed"] += eq - seed; tot["commissions"] += row["commissions"]; tot["closed_net"] += cnet; tot["closed_costs"] += ccost; tot["open_risk"] += orisk
        tot["n_open"] += len(opn); tot["n_armed"] += len(armed); tot["n_closed"] += len(closed)
    tot = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in tot.items()}
    start = _ts(B.get("created_utc") or B.get("start")) or None
    days = max(1.0, (time.time() - start) / 86400.0) if start else None
    ret = (tot["since_seed"] / tot["seed"]) if tot["seed"] else 0.0
    pace = dict(return_pct=round(100 * ret, 3), days=round(days, 1) if days else None, annualised_pct=round(100 * ret * 365.0 / days, 2) if days else None, benchmark_pct=round(100 * BENCH_PA, 1),
                on_pace=(ret * 365.0 / days >= BENCH_PA) if days else None, needed_per_day_aud=round(tot["seed"] * BENCH_PA / 365.0, 0) if tot["seed"] else None)
    heat = dict(open_risk_aud=tot["open_risk"], heat_pct=round(100 * tot["open_risk"] / tot["equity"], 2) if tot["equity"] else None, cap_pct=round(100 * HEAT_CAP, 0),
                level="HOT" if tot["equity"] and tot["open_risk"] / tot["equity"] >= HEAT_CAP else "WARM" if tot["equity"] and tot["open_risk"] / tot["equity"] >= HEAT_WARN else "COOL",
                book_drawdown_pct=B.get("drawdown_pct"), book_peak_aud=B.get("peak_equity_aud"), tfo_risk_level=firm.get("level"), tfo_firm_drawdown_pct=firm.get("firm_drawdown_pct"),
                new_entries_halted=firm.get("new_entries_halted"), firm_equity_usd=firm.get("firm_equity_usd"))
    league = sorted(seats.items(), key=lambda kv: -kv[1]["since_seed"])
    adv = []
    for s, r in league[:2]:
        if r["since_seed"] > 0 and r["closed_filled"] > 0: adv.append("%s is booking real profits (A$%.0f net since seed): candidate for +10%% bank (ADVISORY, Head Trader/Marcus decide)." % (s, r["since_seed"]))
    for s, r in league[-2:]:
        if r["since_seed"] < -0.01 * max(r["seed"], 1): adv.append("%s is down %.1f%% of seed (A$%.0f): keep at current bank, no size-up until it books a winner (ADVISORY)." % (s, 100 * r["since_seed"] / r["seed"], r["since_seed"]))
    if not adv: adv.append("No seat has booked enough live results to move bank yet: keep every seat at its seed bank (ADVISORY).")
    return dict(wita=now.strftime("%Y-%m-%d %H:%M WITA"), book=B.get("label"), paper_only=True, today_since=(dayc.get("window_start_wita") if dayc.get("clock") == "06:00" else "00:00 WITA (book day: opening marks unavailable)") if dayc else "00:00 WITA (book day)", totals=tot, seats=seats, league=[s for s, _ in league], pace=pace, heat=heat, advisory=adv,
                costs_note="commissions/spreads/funding/FX sit inside the book's net figures; AI cost = Claude/seat-brain calls + prompt chars in 24h (no $ price invented)",
                week_basis="Monday 00:00 WITA snapshot" if eq_w else ("leaderboard week_open" if week_open else "n/a"), month_basis="1st of month snapshot" if eq_m else "since seed (book younger than the month)")
def eli10(d):
    t = d["totals"]; p = d["pace"]; h = d["heat"]; L = []
    L.append("CFO REPORT (Helen Park, PAPER) %s" % d["wita"])
    L.append("WHAT WE HAVE: A$%.2fm across %d seats (seed A$%.2fm). Cash A$%.2fm, invested A$%.2fm. %d open, %d armed, %d closed rows." % (t["equity"] / 1e6, len(d["seats"]), t["seed"] / 1e6, t["cash"] / 1e6, t["invested"] / 1e6, t["n_open"], t["n_armed"], t["n_closed"]))
    L.append("MADE OR LOST (net of all trading costs): today %s (since %s, the dashboard clock; book day (rails) since 00:00 WITA %s), this week %s (%s), this month %s (%s), since seed %s (%.2f%%). Commissions so far A$%.0f; booked closes net A$%.0f." % (
        _k(t["today"]), d.get("today_since") or "06:00 WITA", _k(t.get("book_day", t["today"])), _k(t["week"]), d["week_basis"], _k(t["month"]), d["month_basis"], _k(t["since_seed"]), 100 * t["since_seed"] / t["seed"] if t["seed"] else 0, t["commissions"], t["closed_net"]))
    L.append("FEE PACE: %s%% annualised after %s days vs the 19%%/yr benchmark before fee -> %s. To be on pace the desk needs about A$%s a day." % (
        p["annualised_pct"], p["days"], "ON PACE" if p["on_pace"] else "BEHIND", "{:,.0f}".format(p["needed_per_day_aud"] or 0)))
    L.append("RISK: open risk A$%.0f = %.1f%% heat (%s; cap %.0f%%). Book drawdown %s%% from peak A$%.2fm. TFO Risk: %s, firm drawdown %s%%, new entries halted: %s." % (
        h["open_risk_aud"], h["heat_pct"] or 0, h["level"], h["cap_pct"], h["book_drawdown_pct"], _f(h["book_peak_aud"]) / 1e6, h["tfo_risk_level"], h["tfo_firm_drawdown_pct"], h["new_entries_halted"]))
    L.append("LEAGUE (since seed, today, AI calls/24h): " + "; ".join("%d. %s %s (today %s, AI %d calls/%.0fk chars)" % (i + 1, s, _k(d["seats"][s]["since_seed"]), _k(d["seats"][s]["today"]), d["seats"][s]["ai_calls"], d["seats"][s]["ai_prompt_kchars"]) for i, s in enumerate(d["league"])))
    L.append("ADVICE (I never move money; Head Trader/Marcus decide): " + " ".join(d["advisory"]))
    L.append("Costs note: " + d["costs_note"] + ".")
    return "\n".join(L)
def summary_block(d=None, cap=1400):
    """Short money block for the CFO's chat prompt."""
    try:
        d = d or compute(); t = d["totals"]
        s = "MONEY NOW (A$ net, read-only ledgers, %s): equity %s seed %s cash %s invested %s | today %s (since 06:00 WITA, dashboard clock; book day (rails) %s) week %s month %s since-seed %s | heat %.1f%% %s dd %s%% | pace %s%%/yr vs 19%% | league: %s" % (
            d["wita"], _m(t["equity"]), _m(t["seed"]), _m(t["cash"]), _m(t["invested"]), _k(t["today"]), _k(t.get("book_day", t["today"])), _k(t["week"]), _k(t["month"]), _k(t["since_seed"]), d["heat"]["heat_pct"] or 0, d["heat"]["level"], d["heat"]["book_drawdown_pct"], d["pace"]["annualised_pct"],
            ", ".join("%s %s" % (x, _k(d["seats"][x]["since_seed"])) for x in d["league"]))
        return s[:cap]
    except Exception as x: return "MONEY NOW: unavailable (%s)" % type(x).__name__
def post(ai=False, root=None):
    """Compute, write state/thf/cfo_report.json + seat_ai_cost.json, post to chat as 'cfo' (to Brett) and mail Marcus (reporting chain)."""
    d = compute(); txt = eli10(d); mode = "code"
    if ai and not os.environ.get("PYTEST_CURRENT_TEST"):
        try:
            from veteran_desk import claude_brain as CB
            q = ("You are Helen Park, CFO of the Taylor Hedge Fund PAPER desk. Below is today's code-built money report (real ledger numbers). Add a 3-sentence plain ELI10 commentary for Brett: the one thing that matters, the one risk, the one action (advisory only, you never move money). Do not restate every number; never invent a number.\n" + txt + "\nAnswer with the 3 sentences only.")
            c, lab = CB.ask(q, seat="cfo", timeout=300, kind="chat", prio="low")   # capq_20261103 tiers: a scheduled CFO note never takes a round/Brett slot
            if c and c.strip(): txt += "\nHELEN SAYS: " + c.strip()[:700]; mode = lab
        except Exception as x: txt += "\n(AI commentary unavailable: %s)" % str(x)[:80]
    try:
        tmp = OUT + ".tmp"; json.dump(dict(d, text=txt, mode=mode), open(tmp, "w"), indent=1, default=str); os.replace(tmp, OUT)
        write_aicost()   # seatagents_20261103: every seat incl. Marcus/HT/staff, with est tokens
    except Exception: pass
    if os.environ.get("PYTEST_CURRENT_TEST"): return txt
    from veteran_desk import thf_msgdesk as MD, seat_mail as SM, brain_hb as HB
    MD.tadd(root=root or "cfo-%s" % dt.datetime.now(WITA).strftime("%Y%m%d-%H%M"), **{"from": "cfo", "to": "brett"}, text=txt[:4000], mode=mode, kind="cfo_report")
    SM.deliver("cfo", SM.MARCUS, "CFO report posted to Brett: " + txt[:500], hop=1, kind="report")
    try: HB.update("cfo", last_report=d["wita"], via="report")
    except Exception: pass
    try:
        from veteran_desk import seat_brain as SB; SB.journal("cfo", "report", "posted CFO report (%s): today %s, since seed %s" % (mode, _k(d["totals"]["today"]), _k(d["totals"]["since_seed"])))
    except Exception: pass
    return txt
if __name__ == "__main__":
    import sys
    a = sys.argv[1:]; cmd = a[0] if a else "print"
    if cmd == "post": print(post(ai="--ai" in a))
    elif cmd == "json": print(json.dumps(compute(), indent=1, default=str))
    elif cmd == "aicost": print(json.dumps(write_aicost(), indent=1))
    else: print(eli10(compute()))
