#!/usr/bin/env python3
"""CALENDAR PAPER BENCH (PAPER ONLY) - simulated fills for OANDA-instrument setups staged by event_calendar.py.
No OANDA API, no orders, no keys: fills simulated on Yahoo 1h bars of the proxy symbol (FX '=X', futures/indices).
MAX 3 open PER BOOK (Brett 27 Oct 2026 19:35 WITA, audit H8): this ledger's own FILLED only; if full -> SHADOW (logged, no position).
(Before 27 Oct this counted FILLED trades in every hunt48 book too; hunt48 engines always counted per book. One definition now.)
Rules mirror hunt48: entry on a completed bar CLOSE through trigger and not beyond max_fill; stop; half at T1 then stop->entry;
rest at T2; time exit 72h after fill. Costs = staged est_cost_usd (spread x1.5 + financing for 3d). P&L in USD and AUD."""
import datetime as dt, glob, json, os, sys
BASE = os.environ.get("DESK_BASE", os.path.dirname(os.path.abspath(__file__)))
CD = os.path.join(BASE, "state", "calendar"); LEDGER = os.path.join(CD, "paper_bench_ledger.json")
STATUS = os.path.join(BASE, "static", "cal_paper_bench.json"); UTC = dt.timezone.utc
MAX_OPEN = 3; ARM_HOURS = 48; HOLD_HOURS = 72; TIME_STOP_HOURS = 48; AUDUSD = 0.7022
STOP_SLIP = 0.0005   # audit H1 (Brett 27 Oct 2026 19:35 WITA): stop fills = min(open, stop) long / max(open, stop) short, plus 5bp adverse slippage
def stop_fill(stop, bar_open, sg):
    """audit H1: a stop order that is gapped through fills at the bar open (worse than the stop), then 5bp slippage against us."""
    if bar_open is None or bar_open != bar_open: bar_open = stop   # triage 2026-10-28: NaN/missing open (yfinance gap row) -> book at the stop, never NaN P&L
    px = min(bar_open, stop) if sg > 0 else max(bar_open, stop)
    return px * (1 - STOP_SLIP) if sg > 0 else px * (1 + STOP_SLIP)
FILL_RULE_CFG = os.path.join(BASE, "state", "hunt_fill_rule.json")
def hunt_fill_rule(base=None):
    """Brett 28 Oct 2026 13:15 WITA hunt fix 3: hunt48 fill config {rule: touch|close, max_fill_atr: 0.4}. Missing file -> touch / 0.4."""
    c = load(os.path.join(base, "state", "hunt_fill_rule.json") if base else FILL_RULE_CFG, {})
    return dict(rule=c.get("rule") or "touch", max_fill_atr=float(c.get("max_fill_atr") or 0.4))
def touch_fill(t, b, sg):
    """Touch fill: bar range crosses the trigger -> fill at the trigger capped at max_fill. Gap-through (open already through the trigger):
    open within the max_fill band -> fill at the OPEN; open beyond max_fill -> MISSED. Returns (px, None) | (None, 'MISSED') | (None, None)."""
    trig, mf, o = t["trigger"], t["max_fill"], b.get("o")
    if o is not None and o == o and ((o >= trig) if sg > 0 else (o <= trig)):
        return ((o, None) if ((o <= mf) if sg > 0 else (o >= mf)) else (None, "MISSED"))
    if (b["h"] >= trig) if sg > 0 else (b["l"] <= trig):
        return ((min(trig, mf) if sg > 0 else max(trig, mf)), None)
    return None, None
def now_utc(): return dt.datetime.now(UTC)
def atomic(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True); t = f"{p}.{os.getpid()}.tmp"; json.dump(o, open(t, "w"), indent=1, default=str); os.replace(t, p)
def load(p, d):
    try: return json.load(open(p))
    except Exception: return d
def book_open(trades):
    """audit H8: the ONE max-open definition (per book) shared with the hunt48 engines: FILLED/PARTIAL rows of this book only."""
    return sum(t.get("status") in ("FILLED", "PARTIAL") for t in trades)
def hunt_filled(base=BASE):   # informational only (War Room / reports); NOT used for slot gating since audit H8
    n = 0
    for p in glob.glob(os.path.join(base, "state", "hunt48_short*", "ledger.json")):
        n += sum(t.get("status") in ("FILLED", "PARTIAL") for t in load(p, {}).get("trades", []))
    return n
def yahoo_of(sym, base=BASE):
    import csv
    p = os.path.join(base, "state", "calendar", "inputs", "ranked_oanda.csv")
    try:
        for r in csv.DictReader(open(p)):
            if r["sym"] == sym: y = r.get("yahoo", ""); return None if ("*" in y or "/" in y) else y
    except Exception: return None
def bars_yf(y, start):
    import yfinance as yf
    d = yf.download(y, start=(start - dt.timedelta(hours=2)).strftime("%Y-%m-%d"), interval="1h", progress=False, auto_adjust=False)
    if d is None or d.empty: return []
    if hasattr(d.columns, "levels"): d.columns = d.columns.get_level_values(0)
    out = []
    for ts, r in d.iterrows():
        t = ts.to_pydatetime(); t = t if t.tzinfo else t.replace(tzinfo=UTC); t = t.astimezone(UTC) + dt.timedelta(hours=1)   # bar END
        out.append(dict(t=t, o=float(r["Open"]), h=float(r["High"]), l=float(r["Low"]), c=float(r["Close"]), v=(float(r["Volume"]) if "Volume" in r and r["Volume"] == r["Volume"] else None)))   # v: giveback (b)
    return out
def step(t, bars, now, open_slots, rule="close", giveback=False, news=None, engine="cal_bench", half_1r=False):
    """advance one trade over completed bars; returns open_slots after. rule='close' (calendar bench, unchanged) | 'touch' (hunt48, fix 3)."""
    sg = 1 if t["direction"] == "long" else -1
    armed_at = dt.datetime.fromisoformat(t["armed_utc"])
    if giveback:   # 2026-10-29 17:2x WITA Brett DNA: hunt giveback protection (hunt_giveback.py); calendar bench passes False (unchanged)
        import hunt_giveback as GB
        if news is None: news = GB.load_news()
    for _i, b in enumerate(bars):
        if b["t"] <= dt.datetime.fromisoformat(t.get("last_bar_utc") or t["armed_utc"]) or b["t"] > now: continue
        t["last_bar_utc"] = b["t"].isoformat()
        if t["status"] == "ARMED":
            if b["t"] > armed_at + dt.timedelta(hours=ARM_HOURS): t["status"] = "EXPIRED"; return open_slots
            if rule == "touch":
                px, miss = touch_fill(t, b, sg)
                if miss: t["status"] = "MISSED"; t["note"] = f"gapped open {b.get('o')} beyond max_fill {t['max_fill']} (touch rule)"; return open_slots
                if px is None: continue
                if open_slots <= 0: t["status"] = "SHADOW"; t["note"] = "max 3 open across hunt books"; t["shadow_fill"] = px; return open_slots
                t.update(status="FILLED", fill=px, fill_utc=b["t"].isoformat(), fill_rule="touch", stop_live=t["stop"], qty_open=t["shares"], realised_usd=0.0); open_slots -= 1
            else:
                through = (b["c"] > t["trigger"]) if sg > 0 else (b["c"] < t["trigger"])
                within = (b["c"] <= t["max_fill"]) if sg > 0 else (b["c"] >= t["max_fill"])
                if through and not within: t["status"] = "MISSED"; t["note"] = f"closed beyond max_fill {b['c']}"; return open_slots
                if through and within:
                    if open_slots <= 0: t["status"] = "SHADOW"; t["note"] = "max 3 open across hunt books"; t["shadow_fill"] = b["c"]; return open_slots
                    t.update(status="FILLED", fill=b["c"], fill_utc=b["t"].isoformat(), stop_live=t["stop"], qty_open=t["shares"], realised_usd=0.0); open_slots -= 1
                continue
            continue
        if t["status"] == "FILLED":
            e = t["fill"]; q = t["qty_open"]; q2u = t.get("q2usd", 1.0)
            hit_stop = (b["l"] <= t["stop_live"]) if sg > 0 else (b["h"] >= t["stop_live"])
            if hit_stop:
                xp = stop_fill(t["stop_live"], b.get("o", t["stop_live"]), sg); t["exit_px"] = round(xp, 6)   # audit H1: gap-through at the open + 5bp
                t["realised_usd"] += sg * (xp - e) * q * q2u; t["qty_open"] = 0; t["status"] = "CLOSED"; t["exit"] = "STOP" if t["stop_live"] != e else "BE"; break
            if not t.get("t1_done"):
                h = int(q // 2)   # audit H1: whole lots only (7 -> bank 3, run 4); 1 unit -> nothing banked, stop still to entry
                if half_1r:
                    # Brett 2026-10-29 17:30 + TFO amend 17:37: half at NET >= 1R (1.5R trend) of original risk; net = staged round-trip
                    # est_cost_usd (commission + slippage + half tick + 3d borrow for shorts). THEN stop -> fee floor (entry + RT cost/share).
                    import hunt_giveback as GB1
                    R_px = abs(e - float(t["stop"])); sh = float(t.get("shares") or q); risk_usd = R_px * sh * q2u
                    rt = float(t.get("est_cost_usd") or 0); need = GB1.half_r(t) * risk_usd
                    fav = b["h"] if sg > 0 else b["l"]
                    if h > 0 and risk_usd > 0 and (sg * (fav - e) * q * q2u - rt) >= need - 1e-9:
                        lvl = e + sg * (need + rt) / (q * q2u)
                        px = b["c"] if (sg * (b["c"] - e) * q * q2u - rt) >= need - 1e-9 else lvl
                        t["realised_usd"] += sg * (px - e) * h * q2u; t["qty_open"] = q - h; t["t1_done"] = True
                        t["stop_live"] = round(GB1.fee_floor(e, sg, rt / q2u, sh), 6)
                        t.update(half_rule="%gR_net" % GB1.half_r(t), half_px=round(px, 6), half_rt_cost_usd=round(rt, 4), stop_rule="fee_floor_after_half")
                elif ((b["h"] >= t["t1"]) if sg > 0 else (b["l"] <= t["t1"])):
                    t["realised_usd"] += sg * (t["t1"] - e) * h * q2u; t["qty_open"] = q - h; t["t1_done"] = True; t["stop_live"] = e
            if half_1r and t["qty_open"] > 0:   # bankfinal (Brett 17:38 WITA): full flat at net >= 3R of ORIGINAL risk; replaces T2/+5% on hunts
                import hunt_giveback as GB1
                _rk = abs(e - float(t["stop"])) * float(t.get("shares") or q) * q2u; _q = t["qty_open"]
                if _rk > 0:
                    _fl = GB1.flat_level(e, sg, _rk, float(t.get("est_cost_usd") or 0), float(t.get("shares") or _q), q2u)   # ORIGINAL size: 3R price move net of RT cost/share
                    if (b["h"] >= _fl) if sg > 0 else (b["l"] <= _fl):
                        _px = b["c"] if ((b["c"] >= _fl) if sg > 0 else (b["c"] <= _fl)) else _fl
                        t["realised_usd"] += sg * (_px - e) * _q * q2u; t["qty_open"] = 0; t["status"] = "CLOSED"; t["exit"] = "FLAT_3R"; t["exit_px"] = round(_px, 6); break
            elif t.get("t1_done") and ((b["h"] >= t["t2"]) if sg > 0 else (b["l"] <= t["t2"])):
                t["realised_usd"] += sg * (t["t2"] - e) * t["qty_open"] * q2u; t["qty_open"] = 0; t["status"] = "CLOSED"; t["exit"] = "T2"; break
            if giveback and t["qty_open"] > 0:   # Brett DNA 2026-10-29: early bank on (a) 50% giveback of >=0.5R peak (b) close thru prior bar on >=2x vol (c) news
                _gb = GB.check(t, b, bars[:_i], sg, e, t["stop"], t.get("fill_utc"), b["t"], news)
                if _gb:
                    GB.record(t, _gb, b["c"], t["stop_live"], b["t"], engine)
                    t["realised_usd"] += sg * (b["c"] - e) * t["qty_open"] * q2u; t["qty_open"] = 0; t["status"] = "CLOSED"; t["exit"] = "GIVEBACK_" + _gb.upper(); t["exit_px"] = round(b["c"], 6); break
            import hunt_giveback as GB2
            if half_1r and t["qty_open"] > 0 and GB2.time_stop_applies(t.get("fill_utc")):   # TIME STOP (Brett 17:44 WITA): < +0.5R net by session-2 close (fill + 48h, fixed at entry, never extended)
                t.setdefault("time_stop_utc", (dt.datetime.fromisoformat(t["fill_utc"]) + dt.timedelta(hours=TIME_STOP_HOURS)).isoformat())
                if GB2.time_stop_due(t, dt.datetime.fromisoformat(t["time_stop_utc"]), b["t"], b.get("c"), sg, e, t["stop"], t.get("shares") or q, float(t.get("est_cost_usd") or 0), q2u):
                    GB2.record(t, "time_stop", b["c"], t["stop_live"], b["t"], engine)
                    t["realised_usd"] += sg * (b["c"] - e) * t["qty_open"] * q2u; t["qty_open"] = 0; t["status"] = "CLOSED"; t["exit"] = "TIME_STOP"; t["exit_reason"] = "rail_time_stop"; t["exit_px"] = round(b["c"], 6); break
            if b["t"] >= dt.datetime.fromisoformat(t["fill_utc"]) + dt.timedelta(hours=HOLD_HOURS):
                t["realised_usd"] += sg * (b["c"] - e) * t["qty_open"] * q2u; t["qty_open"] = 0; t["status"] = "CLOSED"; t["exit"] = "TIME"; break
            t["mark"] = b["c"]
    if t["status"] == "CLOSED": t["net_usd"] = round(t["realised_usd"] - t["est_cost_usd"], 2); t["net_aud"] = round(t["net_usd"] / AUDUSD, 2)
    return open_slots
def run(now=None, fetch=bars_yf, cal=None, base=BASE):
    now = now or now_utc(); L = load(LEDGER, dict(book="CALENDAR_PAPER_BENCH", paper_only=True, trades=[]))
    cal = cal or load(os.path.join(base, "static", "event_calendar.json"), {})
    active = {t["sym"] for t in L["trades"] if t["status"] in ("ARMED", "FILLED")}
    for s in cal.get("staged", []):
        if s.get("venue") != "oanda" or not s.get("top3") or s["sym"] in active: continue
        y = yahoo_of(s["sym"], base)
        if not y: continue
        q2u = (s["notional_usd"] / (s["shares"] * s["max_fill"])) if s["shares"] else 1.0
        L["trades"].append(dict(id=len(L["trades"]) + 1, sym=s["sym"], yahoo=y, direction=s["direction"], status="ARMED", armed_utc=now.isoformat(),
                                trigger=s["trigger"], max_fill=s["max_fill"], stop=s["stop"], t1=s["t1"], t2=s["t2"], shares=s["shares"], q2usd=q2u,
                                est_cost_usd=s["est_cost_usd"], risk_aud=s["risk_aud"], events=s.get("events", [])[:3]))
    slots = MAX_OPEN - book_open(L["trades"])   # audit H8: per book
    for t in L["trades"]:
        if t["status"] not in ("ARMED", "FILLED"): continue
        try:
            was_filled = t["status"] == "FILLED"
            slots = step(t, fetch(t["yahoo"], dt.datetime.fromisoformat(t["armed_utc"])), now, slots + (1 if was_filled else 0)) - (1 if was_filled and t["status"] == "FILLED" else 0)
        except Exception as e: t["last_error"] = str(e)[:120]
    L["updated_utc"] = now.isoformat(); atomic(LEDGER, L)
    closed = [t for t in L["trades"] if t["status"] == "CLOSED"]
    st = dict(updated_utc=now.isoformat(), paper_only=True, open_slots_left=max(slots, 0), counts={k: sum(t["status"] == k for t in L["trades"]) for k in ("ARMED", "FILLED", "CLOSED", "SHADOW", "MISSED", "EXPIRED")},
              net_aud_closed=round(sum(t.get("net_aud", 0) for t in closed), 2), trades=L["trades"][-30:])
    import causal_guard; st["causal_violations"] = causal_guard.audit(L["trades"], now); st["causal_violations_count"] = len(st["causal_violations"])
    atomic(os.path.join(base, "static", "cal_paper_bench.json"), st); return st
if __name__ == "__main__":
    print(json.dumps(run()["counts"]))
