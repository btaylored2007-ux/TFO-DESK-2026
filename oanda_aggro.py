#!/usr/bin/env python3
"""oanda_aggro.py - AGGRO sleeve on the OANDA PRACTICE account (paper money only). Brett 16 Oct 2026 21:35/21:36/21:37 AEDT (RULE_LOG.md):
chase +5%/week (HARD target, tracked, NOT guaranteed), notional = WHOLE account NAV, steady TFO trades fold into its risk accounting.
Called from oanda_sleeve.py (daily + 15-min check, launchd com.tfo.oanda-daily / com.tfo.oanda-check) under the same lock.
SETUPS: only those that passed older AND newer history after costs (work/oanda_v3/): daily commodity OXB1 v2 breakout, OXP1 v2 pullback,
OXT1 passive trend (oanda_designs / oanda_setups, identical rules to the steady sleeve). H4/H1 versions of all three on commodities, FX and
indices FAILED (work/oanda_v3/sim_intra.py) and are NOT traded. No FX/index setup passed.
RAILS (code): practice host/account only (from oanda_sleeve); tag AGGRO on every order; stopLossOnFill on every order; risk/trade 3% NAV
(rail <= 3%); account open risk (TFO frozen R + AGGRO frozen R) <= 25% NAV (28 Oct 2026 Brett, was 15%); group caps METALS 6% / OIL 3% (one oil bet) / USD 9% net incl.
TFO rows; margin used <= 60% NAV; one trade per instrument across the account; Brett's 61365/61368 never touched; spread <= min(3x median, 10bp)
and spread cost <= 15% of R; behaviour gate as the steady sleeve; weekly halt: NAV <= -15% vs the week's opening NAV -> no new entries until the
next FX week (Sun 17:00 NY); hard KILL: NAV <= 60% of start NAV -> close every TFO + AGGRO trade, write KILL files for both sleeves, pause.
CAPITAL BASE (28 Oct 2026 Brett +A$100k paper): NAV above = broker NAV + funder_topups.jsonl; margin checks use broker marginUsed/marginAvailable.
State: state/oanda_aggro/ (trades.jsonl, stats.json, weekly.jsonl, open_risk.json, pending.json, KILL)."""
import os, sys, json, datetime as dt
import oanda_sleeve as S, oanda_designs as D, oanda_setups as X, oanda_risk as RK
TAG = "AGGRO"; ST = os.path.join(S.ROOT, "state", "oanda_aggro"); os.makedirs(ST, exist_ok=True)
LOG = os.path.join(ST, "trades.jsonl")
CFG = dict(start_nav_aud=50315.52, risk_pct=0.02, open_risk_cap_pct=0.25, max_open=4, group_cap={"METALS": 0.06, "OIL": 0.03, "GAS": 0.03}, usd_cap_pct=0.09,
           margin_cap_frac=0.60, weekly_halt_pct=-0.15, kill_nav_frac=0.60, weekly_target_pct=0.05, max_cost_r=0.15,
           designs=["OXB1", "OXP1", "OXT1"], live=True)
# Brett 17 Oct 2026 14:27 AEDT (item 2): NATGAS added to AGGRO's commodity BREAKOUT (OXB1) only. AGGRO-local: the shared
# oanda_designs.CM6 (steady sleeve) is NOT changed. Same rails: 3% risk, <=25% open risk, stopLossOnFill, -15% weekly halt, kill.
# Own group GAS capped at 3% (one gas bet, like OIL). NATGAS is not in the work/oanda_v3 older+newer history pass set.
AGGRO_OXB1_EXTRA = ["NATGAS_USD"]
AGGRO_GROUP = {"NATGAS": "GAS"}
def _group(base): return S.GROUP.get(base) or AGGRO_GROUP.get(base)
def extra_signals(names, data):
    """OXB1 breakout on AGGRO_OXB1_EXTRA, built exactly like oanda_designs.signals builds OXB1 on CM6."""
    out = []
    if "OXB1" not in names: return out
    for ins in AGGRO_OXB1_EXTRA:
        cs = data.get(ins)
        if not cs or len(cs) < X.MIN_BARS + 1: continue
        I = X.Ind(cs); n = len(cs) - 1; d = X.breakout(I, n)
        if d: out.append(dict(design="OXB1", ins=ins, dir=d, score=1.0, risk_pct=D.RISK["OXB1"], trigger=X.trigger_level(I, n, d),
                              reason=f"OXB1 {'long' if d > 0 else 'short'}: close beyond prior 55-bar extreme after ATR compression, EMA50/200 aligned (bar {cs[-1]['t'][:10]}); stop-entry trigger next session [AGGRO NATGAS]"))
    return out
def cfg():
    c = dict(CFG); p = os.path.join(ST, "config.json")
    if os.path.exists(p): c.update(json.load(open(p)))
    c["risk_pct"] = min(float(c["risk_pct"]), 0.02); c["open_risk_cap_pct"] = min(float(c["open_risk_cap_pct"]), 0.25)   # rails: at most 2% per trade; account open-risk cap stays 25%
    c["max_open"] = min(int(c.get("max_open", 4)), 4)
    c["margin_cap_frac"] = min(float(c["margin_cap_frac"]), 0.60); c["weekly_halt_pct"] = max(float(c["weekly_halt_pct"]), -0.15)
    c["kill_nav_frac"] = max(float(c["kill_nav_frac"]), 0.60)
    c["start_nav_aud"] = float(c["start_nav_aud"]) + topup_total(); return c   # start base includes dated funder top-ups
def now(): return dt.datetime.now(dt.timezone.utc)
# ---- 28 Oct 2026 13:47 WITA (Brett: "increase Oanda with another $100k to play with") - FUNDER PAPER TOP-UP ----
# OANDA v20 has no deposit endpoint for practice accounts, so the top-up lives in the desk ledger state/oanda_aggro/funder_topups.jsonl
# (dated, never backdated). CAPITAL BASE = broker NAV + top-ups: AGGRO sizing (3%) and every %-cap (open risk, groups, USD, weekly halt,
# kill) use it. MARGIN stays on the broker's REAL figures (marginUsed / marginAvailable) so no order can be rejected for margin.
TOPUPS = os.path.join(ST, "funder_topups.jsonl")
def topup_total():
    tot = 0.0
    try:
        for l in open(TOPUPS):
            if l.strip():
                r = json.loads(l)
                if r.get("utc", "") <= now().isoformat(): tot += float(r.get("amount_aud") or 0)
    except FileNotFoundError: pass
    return tot
def capital_nav(a): return float(a["NAV"]) + topup_total()
def real_margin_room(a, c, cap_nav):
    """A$ of new margin allowed: min(60% x capital base - used, 90% of broker marginAvailable)."""
    return max(0.0, min(c["margin_cap_frac"] * cap_nav - float(a["marginUsed"]), 0.90 * float(a["marginAvailable"])))
def log(rec):
    rec = dict(rec, utc=now().isoformat(timespec="seconds"), sleeve="AGGRO", paper_practice=True)
    with open(LOG, "a") as f: f.write(json.dumps(rec, default=str) + "\n")
def wj(name, obj):
    p = os.path.join(ST, name); tmp = p + ".tmp"; json.dump(obj, open(tmp, "w"), indent=1, default=str); os.replace(tmp, p)
def rj(name, default):
    try: return json.load(open(os.path.join(ST, name)))
    except Exception: return default
def is_aggro(t): return t["id"] not in S.PROTECTED_TRADE_IDS and (t.get("clientExtensions") or {}).get("tag") == TAG
def fx_week(t=None):
    """FX week key: NY time + 7h, so the Sun 17:00 NY open starts a new ISO week."""
    from zoneinfo import ZoneInfo
    t = (t or now()).astimezone(ZoneInfo("America/New_York"))
    k = (t + dt.timedelta(hours=7)).isocalendar(); return "%d-W%02d" % (k[0], k[1])
def ledger():
    out = {}
    if os.path.exists(LOG):
        for ln in open(LOG):
            try: r = json.loads(ln)
            except Exception: continue
            if r.get("event") == "entry_order" and (r.get("response") or {}).get("trade_id"): out[str(r["response"]["trade_id"])] = r
    return out
def risk_rows(trades, nav, c):
    """Frozen entry R of every TFO and AGGRO trade (TFO from its own ledger; unknown -> charged full risk)."""
    tl = S.ledger_entry_risk(); al = ledger(); rows = []
    for t in trades:
        if t["id"] in S.PROTECTED_TRADE_IDS: continue
        d = 1 if float(t["currentUnits"]) > 0 else -1
        if is_aggro(t): r = (al.get(str(t["id"])) or {}).get("r0_aud"); dflt = c["risk_pct"]; owner = TAG
        elif S.is_tfo(t): r = (tl.get(str(t["id"])) or {}).get("risk_aud"); dflt = 0.01; owner = "TFO"
        else: continue
        rows.append(dict(id=t["id"], ins=t["instrument"], dir=d, risk_frac=(float(r) / nav) if r else dflt, owner=owner,
                         design=(t.get("clientExtensions") or {}).get("comment")))
    return rows
def caps(rows, ins, d, rf, c):
    if len(rows) + 1 > int(c.get("max_open", 4)): return "skip:max_open %d" % int(c.get("max_open", 4))
    tot = sum(r["risk_frac"] for r in rows) + rf
    if tot > c["open_risk_cap_pct"] + 1e-9: return "skip:open_risk_cap %.2f%% > %.0f%%" % (tot * 100, c["open_risk_cap_pct"] * 100)
    b, q = ins.split("_"); g = _group(b)
    if g:
        gn = sum(r["dir"] * r["risk_frac"] for r in rows if _group(r["ins"].split("_")[0]) == g) + d * rf
        if abs(gn) > c["group_cap"].get(g, 0.03) + 1e-9: return "skip:group_cap %s %+.2f%%" % (g, gn * 100)
    usd = sum((-r["dir"] if r["ins"].endswith("_USD") else r["dir"] if r["ins"].startswith("USD_") else 0) * r["risk_frac"] for r in rows)
    usd += (-d if ins.endswith("_USD") else d if ins.startswith("USD_") else 0) * rf
    if abs(usd) > c["usd_cap_pct"] + 1e-9: return "skip:usd_cap %+.2f%%" % (usd * 100)
    return None
# ---------------- account guards / stats ----------------
def guards(a, trades, c, live):
    nav = capital_nav(a); tu = topup_total(); wk = fx_week(); W = rj("week.json", {})
    if W.get("week") != wk:
        if W.get("week"): _append_week(W, nav)
        W = dict(week=wk, open_nav=nav, opened_utc=now().isoformat(timespec="seconds"), halted=False, topup_aud=tu); wj("week.json", W)
    if float(W.get("topup_aud") or 0) != tu:   # funder flow mid-week: lift the week's opening base so the flow is not counted as return
        W["open_nav"] = float(W["open_nav"]) + tu - float(W.get("topup_aud") or 0); W["topup_aud"] = tu; wj("week.json", W)
    wret = nav / W["open_nav"] - 1
    if not W.get("halted") and wret <= c["weekly_halt_pct"]:
        W["halted"] = True; W["halted_utc"] = now().isoformat(timespec="seconds"); wj("week.json", W)
        log(dict(event="weekly_halt", week=wk, week_ret_pct=round(wret * 100, 2), action="no new AGGRO entries until next FX week; stops stay"))
    killed = os.path.exists(os.path.join(ST, "KILL"))
    real_floor = float(a["NAV"]) <= c["kill_nav_frac"] * CFG["start_nav_aud"]   # broker-NAV backstop: the real account can't lose 40% of the topped-up base
    if not killed and (nav <= c["kill_nav_frac"] * c["start_nav_aud"] or real_floor):
        killed = True; open(os.path.join(ST, "KILL"), "w").write("hard kill %s NAV %.2f\n" % (now().isoformat(), nav))
        open(os.path.join(S.ST, "KILL"), "w").write("AGGRO hard kill %s NAV %.2f (-40%%)\n" % (now().isoformat(), nav))
        log(dict(event="hard_kill", nav=nav, start=c["start_nav_aud"], action="flatten every TFO + AGGRO trade; Brett's index trades untouched; paused for review"))
        for t in trades:
            if is_aggro(t): close(t, "hard kill -40% NAV", live)
            elif S.is_tfo(t):
                try: S.close_trade(t, "AGGRO hard kill -40% NAV", live)
                except Exception as ex: log(dict(event="kill_close_error", trade_id=t["id"], error=repr(ex)[:200]))
    stats(a, trades, W, wret, c)
    return (not killed) and (not W.get("halted")), W
def _append_week(W, nav):
    r = nav / W["open_nav"] - 1
    with open(os.path.join(ST, "weekly.jsonl"), "a") as f:
        f.write(json.dumps(dict(week=W["week"], open_nav=W["open_nav"], close_nav=nav, ret_pct=round(r * 100, 3), target_pct=5.0, hit=r >= 0.05, halted=W.get("halted"))) + "\n")
def stats(a, trades, W, wret, c):
    nav = capital_nav(a); hist = []
    p = os.path.join(ST, "weekly.jsonl")
    if os.path.exists(p): hist = [json.loads(l) for l in open(p) if l.strip()]
    rows = risk_rows(trades, nav, c)
    s = dict(label="OANDA PRACTICE - AGGRO sleeve (PAPER). Notional = whole account NAV. Target +5%/week (tracked, not guaranteed)",
             asof_utc=now().isoformat(timespec="seconds"), nav=round(nav, 2), start_nav=c["start_nav_aud"], total_ret_pct=round((nav / c["start_nav_aud"] - 1) * 100, 2),
             week=W["week"], week_open_nav=round(W["open_nav"], 2), week_ret_pct=round(wret * 100, 2), week_target_pct=5.0,
             week_gap_to_target_pct=round(5.0 - wret * 100, 2), week_halted=bool(W.get("halted")), killed=os.path.exists(os.path.join(ST, "KILL")),
             weeks_hit=sum(1 for h in hist if h.get("hit")), weeks_done=len(hist), history=hist[-26:],
             open_risk_pct=round(sum(r["risk_frac"] for r in rows) * 100, 2), open_risk_cap_pct=c["open_risk_cap_pct"] * 100,
             margin_used=round(float(a["marginUsed"]), 2), margin_pct=round(float(a["marginUsed"]) / nav * 100, 2),
             broker_nav=round(float(a["NAV"]), 2), funder_topup_aud=round(topup_total(), 2), capital_base_aud=round(nav, 2), margin_available_real=round(float(a["marginAvailable"]), 2),
             open=[dict(r, risk_pct=round(r["risk_frac"] * 100, 2)) for r in rows], aggro_upl_aud=round(sum(float(t.get("unrealizedPL") or 0) for t in trades if is_aggro(t)), 2),
             monte_carlo="work/oanda_v3/mc_aggro.py (16 Oct 2026): P(week >= +5%) ~13%, median week ~-0.5%, P(-40% kill in 13 wks) ~0.1% (iid, ignores correlation/gaps)",
             designs=c["designs"])
    wj("stats.json", s); wj("open_risk.json", dict(asof_utc=s["asof_utc"], rows=rows, total_pct=s["open_risk_pct"]))
# ---------------- orders ----------------
def close(t, reason, live):
    if not is_aggro(t): raise RuntimeError("refusing to close non-AGGRO trade " + t["id"])
    rec = dict(event="close_order", trade_id=t["id"], instrument=t["instrument"], reason=reason, live=live, design=(t.get("clientExtensions") or {}).get("comment"))
    if not live: rec["event"] = "close_dry"; log(rec); return True
    try:
        r = S.api("PUT", S.acct(f"/trades/{t['id']}/close"), {"units": "ALL"}, retry=False); f = r.get("orderFillTransaction")
        rec.update(event="close_fill" if f else "close_cancelled", price=(f or {}).get("price"), realized_pl_aud=(f or {}).get("pl"))
    except Exception as ex: rec.update(event="close_error", error=str(ex)[:300])
    log(rec); return rec["event"] == "close_fill"
def set_stop(t, price, reason, live):
    if not is_aggro(t): raise RuntimeError("refusing to modify non-AGGRO trade " + t["id"])
    d = 1 if float(t["currentUnits"]) > 0 else -1; cur = (t.get("stopLossOrder") or {}).get("price")
    if cur is not None and ((d > 0 and price <= float(cur)) or (d < 0 and price >= float(cur))): return False   # tighten-only
    rec = dict(event="stop_update", trade_id=t["id"], instrument=t["instrument"], new_stop=price, prev=cur, reason=reason, live=live)
    if not live: rec["event"] = "stop_dry"; log(rec); return False
    try:
        S.api("PUT", S.acct(f"/trades/{t['id']}/orders"), {"stopLoss": {"price": S.fmt(t["instrument"], price), "timeInForce": "GTC"}}, retry=False)
        bt = S.api("GET", S.acct(f"/trades/{t['id']}")).get("trade") or {}; rec["confirmed"] = (bt.get("stopLossOrder") or {}).get("price")
    except Exception as ex: rec.update(event="stop_update_failed", error=str(ex)[:300])
    log(rec); return rec["event"] == "stop_update"
def place(e, c, live):
    if S.RG.entries_blocked(S.ST):   # 25 Oct 2026: OANDA reconcile failing > 2 h -> no new entries (account-wide)
        log(dict(event="entry_blocked_reconcile", instrument=e.get("ins"), design=e.get("design"))); return "defer:reconcile_blocked"
    a = S.summary(); nav = capital_nav(a); tr = S.open_trades()
    if any(t["instrument"] == e["ins"] for t in tr): return "skip:instrument_already_held_on_account"
    rows = risk_rows(tr, nav, c); rf = c["risk_pct"]; why = caps(rows, e["ins"], e["dir"], rf, c)
    if why: log(dict(event="cap_skip", instrument=e["ins"], design=e["design"], reason=why)); return why
    px = S.pricing([e["ins"]]).get(e["ins"])
    if not px or not px.get("tradeable"): return "defer:not_tradeable"
    qh = S.quote_health(px, S.cfg())
    if qh: return "defer:" + qh
    if px["sp"] > S.spread_limit(e["floor_sp"], S.cfg()): return "defer:spread %.1fbp" % (px["sp"] * 1e4)
    ref = px["ask"] if e["dir"] > 0 else px["bid"]
    if e.get("trigger") is not None and ((e["dir"] > 0 and ref < e["trigger"]) or (e["dir"] < 0 and ref > e["trigger"])): return "defer:trigger_not_hit"
    if not S.behaviour_ok(dict(e, design=e["design"]), px, S.cfg(), nav, live): log(dict(event="behaviour_block", instrument=e["ins"], design=e["design"])); return "skip:behaviour"
    mt = S.meta(e["ins"]); cq = RK.c_quote_loss(px)
    if not cq: return "skip:no_conversion"
    qp = 10.0 ** -mt["displayPrecision"]; stop = RK.round_stop_outward(ref - e["dir"] * e["stop_dist"], e["dir"], qp); Dd = abs(ref - stop)
    units = RK.round_units(rf * nav / (Dd * cq), mt)
    if units <= 0: return "skip:units0"
    need = S.margin_needed(e["ins"], units, px)
    room = real_margin_room(a, c, nav)   # REAL broker margin (28 Oct 2026)
    if need > room:
        units = RK.round_units(units * room / need, mt)
        if units <= 0: return "skip:margin_cap"
        need = S.margin_needed(e["ins"], units, px)
    r0 = RK.r0_aud(units, Dd, cq); cost_r = (px["ask"] - px["bid"]) * 1.5 * units * cq / r0
    if cost_r > c["max_cost_r"]: log(dict(event="cost_skip", instrument=e["ins"], cost_r=round(cost_r, 3))); return "skip:cost %.2fR" % cost_r
    cid = "AGGRO-%s-%s-%s" % (e["design"], e["ins"], e["signal_bar"][:10])
    body = {"order": {"type": "MARKET", "instrument": e["ins"], "units": str(units if e["dir"] > 0 else -units), "timeInForce": "FOK", "positionFill": "OPEN_ONLY",
                      "stopLossOnFill": {"price": S.fmt(e["ins"], stop), "timeInForce": "GTC"},
                      "clientExtensions": {"id": cid, "tag": TAG, "comment": e["design"]}, "tradeClientExtensions": {"id": cid, "tag": TAG, "comment": e["design"]}}}
    rec = dict(event="entry_order", design=e["design"], instrument=e["ins"], direction="long" if e["dir"] > 0 else "short", units=units, ref_price=ref,
               stop=float(S.fmt(e["ins"], stop)), stop_dist=Dd, r0_aud=round(r0, 2), r0_frac=round(r0 / nav, 5), nav_aud=nav, cost_r=round(cost_r, 4),
               margin_needed_aud=round(need, 2), reason=e["reason"], signal_bar=e["signal_bar"], client_id=cid, live=live, test_status=D.STATUS.get(e["design"]))
    if not live: rec["event"] = "entry_dry"; log(rec); return "dry"
    try:
        prior = S.api("GET", S.acct(f"/orders/@{cid}")) if True else None
        if prior.get("order"): log(dict(event="duplicate_skip", client_id=cid)); return "skip:already_sent"
    except Exception: pass
    try:
        r = S.api("POST", S.acct("/orders"), body, retry=False); f = r.get("orderFillTransaction")
        rec["response"] = dict(fill_price=(f or {}).get("price"), trade_id=((f or {}).get("tradeOpened") or {}).get("tradeID"),
                               cancel_reason=(r.get("orderCancelTransaction") or {}).get("reason"))
        if f:   # re-freeze R on the actual fill
            fp = float(f["price"]); rec["r0_aud"] = round(abs(units) * abs(fp - stop) * cq, 2)
    except Exception as ex: rec["error"] = str(ex)[:400]
    log(rec); return "filled" if (rec.get("response") or {}).get("trade_id") else "failed"
# ---------------- modes ----------------
def daily(live=True):
    c = cfg(); live = live and c["live"]; a = S.summary(); tr = S.open_trades()
    can, W = guards(a, tr, c, live)
    data = {}
    for ins in list(D.CM6) + AGGRO_OXB1_EXTRA:
        try: data[ins] = S.candles(ins)
        except Exception as ex: log(dict(event="data_error", instrument=ins, error=str(ex)[:200]))
    last = max(cs[-1]["t"] for cs in data.values() if cs)
    done = rj("last_daily.json", {}).get("last_close")
    if live and done == last: return
    for t in [t for t in tr if is_aggro(t)]:   # exits by the trade's own setup rule, then the setup's trail (tighten-only)
        ins = t["instrument"]; d = 1 if float(t["currentUnits"]) > 0 else -1; dz = (t.get("clientExtensions") or {}).get("comment"); cs = data.get(ins)
        if not cs: continue
        p = D.score_for(dz, data, ins, d)
        if p == 0 or (dz in D.TIME_EXIT_BARS and S._time_exit(dict(t), cs, dz, d)): close(t, "setup exit (%s)" % dz, live); continue
        I = X.Ind(cs); a20 = I.atr[-1]; ot = S._ny_parse(t["openTime"])
        since = [float(t["price"])] + [x["c"] for x in cs if S._ny_parse(x["t"]) + dt.timedelta(days=1) > ot]
        k = D.TRAIL_ATR.get(dz, 3.0); ns = (max(since) - k * a20) if d > 0 else (min(since) + k * a20)
        set_stop(t, ns, "%s trail %sxATR20 from best close" % (dz, k), live)
    q = []
    for p in D.signals(c["designs"], data) + extra_signals(c["designs"], data):
        cs = data[p["ins"]]
        if cs[-1]["t"] != last: continue
        I = X.Ind(cs); q.append(dict(p, stop_dist=float(p.get("stop_atr") or 2.0) * I.atr[-1], floor_sp=S.median([x["sp"] for x in cs[-260:]]),
                                     signal_bar=last, expires_utc=(now() + dt.timedelta(hours=20)).isoformat(), **S.behaviour_refs(p, cs, I.atr[-1])))
    q.sort(key=lambda e: D.PRIORITY.get(e["design"], 9))
    wj("pending.json" if live else "pending_dry.json", dict(created_utc=now().isoformat(), last_close=last, queue=q))
    log(dict(event="daily_signals", last_close=last, queued=[(e["design"], e["ins"], e["dir"]) for e in q], live=live))
    run_queue(c, live, can)
    if live: wj("last_daily.json", dict(last_close=last, at=now().isoformat()))
def run_queue(c, live, can):
    pf = "pending.json" if live else "pending_dry.json"; Q = rj(pf, {}); keep = []
    for e in Q.get("queue", []):
        if dt.datetime.fromisoformat(e["expires_utc"]) < now(): log(dict(event="entry_expired", instrument=e["ins"], design=e["design"])); continue
        if not can: keep.append(e); continue
        if S.reopen_blackout(minutes=60): keep.append(e); continue
        r = place(e, c, live); print("AGGRO", e["ins"], e["design"], r)
        if r.startswith("defer"): keep.append(e)
        elif r.startswith("skip"): log(dict(event="entry_skip", instrument=e["ins"], design=e["design"], reason=r))
    Q["queue"] = keep; wj(pf, Q)
def check(live=True):
    c = cfg(); live = live and c["live"]; a = S.summary(); tr = S.open_trades()
    for t in tr:   # every AGGRO trade must carry a broker stop
        if is_aggro(t) and not t.get("stopLossOrder"):
            L = ledger().get(str(t["id"])) or {}; d = 1 if float(t["currentUnits"]) > 0 else -1
            ns = L.get("stop") or (float(t["price"]) - d * 2 * S.atr(S.candles(t["instrument"], 120)))
            if S.stop_through_market(t["instrument"], d, float(ns)): close(t, "missing stop, level through market", live)
            else: set_stop(dict(t, stopLossOrder=None), float(ns), "missing broker stop restored", live)
    can, W = guards(a, tr, c, live); run_queue(c, live, can)
def run(mode):
    try:
        if mode == "daily": daily(True)
        elif mode == "dry": daily(False)
        elif mode == "check": check(True)
    except Exception as ex:
        log(dict(event="error", mode=mode, error=repr(ex)[:600])); print("AGGRO error", repr(ex)[:300])
if __name__ == "__main__":
    fh = S.lock()
    run(sys.argv[1] if len(sys.argv) > 1 else "dry")
