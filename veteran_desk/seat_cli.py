"""Veteran Desk seat CLI for the VD seat bots (Brett 2026-10-28 19:05 WITA). PAPER only - no broker, no live orders.
  python3 -m veteran_desk.seat_cli propose SEAT '{"ticker":..,"side":"LONG","entry":..,"stop":..,"target":..,"grade":"A","thesis":".."}' [--challenger LONG|SHORT|STAND_ASIDE]
  python3 -m veteran_desk.seat_cli confirm SEAT ID   (REQUIRED after propose: confirms you received the result; only confirmed entries arm; 10 min expiry)
  python3 -m veteran_desk.seat_cli status SEAT
  python3 -m veteran_desk.seat_cli positions [SEAT]
  python3 -m veteran_desk.seat_cli close SEAT ID REASON [--shares N | --pct P]   (partial close at live mark; residual stays open, must be >= 1 share)
  python3 -m veteran_desk.seat_cli withdraw SEAT ID REASON        (queued -> withdrawn, armed -> cancelled; audit note)
  python3 -m veteran_desk.seat_cli amend SEAT ID [--stop X] [--target X] [--shares N] REASON   (stop only tightens, never below the exit-rail floor; target not inside the mark, after T1 = residual target; shares only on armed)
  python3 -m veteran_desk.seat_cli lessons [SEAT] [--add "text"]
propose runs gate_v2 (stop + R:R + history + heat + firm rails) and, on PASS, queues the setup auto-approved; book.py arms it on its next tick
with the one sizing source (book.risk_plan, 2-5% of seat equity). Every command prints one JSON object."""
import datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from veteran_desk import desk as D, book as BK, pit as P
LESSONS = os.path.join(D.SD, "lessons.jsonl")
def _out(o): print(json.dumps(o, default=str)); return 0 if o.get("ok", True) else 1
DEBATE_TIMEOUT_S = int(os.environ.get("VET_DEBATE_TIMEOUT_S", "60"))   # 2026-10-28 20:47 WITA Brett trim advisory: hard 60s cap; timeout -> no challenger (gate handles), never hangs
def debate(seat, p, f, S):
    """Blind challenger (the OTHER CLI from the seat's engine, never sees the proposal) + devil's advocate (Claude), in parallel.
    Added 2026-10-28 (Brett 28 Oct, overlords fix health): seat_cli used to skip both, so gate_v2 always cut x0.75 -> 2% floor.
    Uses the logged-in Codex/Claude CLIs via desk.llm (codex falls back to claude). Returns (chal, da, chal_engine)."""
    import concurrent.futures as cf
    other = "codex"   # Marcus 2026-10-29 13:08 WITA: debate on GPT/Codex only, never Claude
    if not f: return None, None, other
    pend = [dict(ticker=x.get("ticker"), side=x.get("side")) for x in positions().get("positions", []) if x.get("status") in ("armed", "open")][:40]
    with cf.ThreadPoolExecutor(2) as ex:
        a = ex.submit(lambda: D.parse_json(D.llm(other, D.chal_prompt(f), DEBATE_TIMEOUT_S))); b = ex.submit(lambda: D.parse_json(D.llm("codex", D.da_prompt(p, f, pend), DEBATE_TIMEOUT_S)))
        chal, da = a.result(), b.result()
    return (chal if isinstance(chal, dict) and chal.get("call") else None), (da if isinstance(da, dict) else None), other
def propose(seat, setup, chal_call=None, queue_path=None, env=None, run_debate=False):
    S = D.seats()
    if seat not in S or S[seat]["role"] != "proposer" or seat not in BK.TRADERS: return dict(ok=False, error="unknown trading seat %s" % seat)
    p = dict(setup, action="propose"); p.setdefault("sleeve", "core"); asof = D.now(); c = P.con(); au = D.audusd(c, asof)
    for k in ("ticker", "side", "entry", "stop", "target", "grade"):
        if p.get(k) in (None, ""): return dict(ok=False, error="missing " + k)
    f = D.feats(c, p["ticker"], asof) if not P.banned(p["ticker"]) else None
    chal = dict(call=chal_call, conviction=3, reason="seat bot supplied") if chal_call else None; da = None; ch_eng = "seat_cli"
    if run_debate:
        mc, da, oth = debate(seat, p, f, S)
        if mc: chal, ch_eng = mc, oth   # model's blind call outranks a self-supplied one
        if mc is None or da is None: p = dict(p, debate_unavailable=dict(challenger=mc is None, devils_advocate=da is None, engine="codex"))   # recorded, never silent
    from veteran_desk import gate_v2 as G2
    g = G2.gate(p, f, chal, da, asof, 0, au=au, th=dict(seat_equity_aud=BK.seat_equity(seat), seat=seat), env=env)   # tfo_seatpipe_dexrail_20261030
    try:   # 2026-10-29 TFO HT: persist the gate_v2 verdict for EVERY proposal (never blocks propose)
        _ap = None
        if queue_path and os.path.abspath(queue_path) != os.path.abspath(os.path.join(BK.BASE, "state", "funder_queue.json")): _ap = os.path.join(os.path.dirname(os.path.abspath(queue_path)), "seat_cli_audit.jsonl")   # temp queue (tests) -> audit beside it, never live
        _audit(_path=_ap, cmd="propose", seat=seat, ticker=p.get("ticker"), side=p.get("side"), entry=p.get("entry"), stop=p.get("stop"), target=p.get("target"),
               verdict="PASS" if g.get("passed") else "REJECT", reasons=g.get("reasons"), warnings=g.get("warnings"),
               r_stats=dict(rr=g.get("rr"), risk_aud=g.get("risk_aud"), risk_pct=g.get("risk_pct"), size_mult=g.get("size_mult"), history=g.get("history"), gate_version=g.get("version")))
    except Exception: pass
    if not g["passed"]: return dict(ok=True, passed=False, reasons=g["reasons"], warnings=g["warnings"], challenger=chal, devils_advocate=da)
    sz = D.size(p, f or dict(), g["risk_aud"], au)
    if sz["shares"] < 1: return dict(ok=True, passed=False, reasons=["size rounds to zero"])
    runid = asof.strftime("%Y%m%d-%H%M%S"); rid = "VET-%s-%s-%s" % ("".join(ch for ch in p["ticker"].upper() if ch.isalnum()), runid, seat)
    # 2026-10-29 infra: stamp proposal with live quote as-of (from gate) so stale checks have a timestamp
    if g.get("quote_asof") and not p.get("quote_asof"):
        p = dict(p, quote_asof=g.get("quote_asof"), quote_last=g.get("quote_last"), quote_src=g.get("quote_src"))
    rec = dict(id=rid, seat=seat, trader=D.name(S, seat), proposal=p, challenger=dict(engine=ch_eng, trader=D.name(S, "blind_challenger") if ch_eng != "seat_cli" else "seat bot", view=chal), devils_advocate=da, gate=g, sizing=sz,
               features=f or dict(sym=p["ticker"], venue=P.venue_of(p["ticker"]), pit_missing=True), hash=rid)
    ids = D.enqueue([rec], au, queue_path, ack_required=queue_path is None and not os.environ.get("PYTEST_CURRENT_TEST"))   # tfo_propose_ack_20261031
    try:
        from veteran_desk import learning as LN
        LN.critique_async([rec], runid)   # 2026-10-29 item 14: detached GPT-only sounding board; never blocks propose
    except Exception: pass
    return dict(ok=True, passed=not rec.get("live_reject"), live_reject=rec.get("live_reject"), id=rid, queued=bool(ids), risk_aud=sz["risk_aud"], risk_pct=g.get("risk_pct"), shares=sz["shares"], size_mult=g["size_mult"], warnings=g["warnings"], challenger=chal, devils_advocate=(da or {}).get("kill_case"),
                note="auto-approved; book.py arms on next tick (no funder tick, PAPER)")
def status(seat):
    B = BK.load()["seats"].get(seat)
    if B is None: return dict(ok=False, error="no book for seat %s" % seat)
    V = BK.seat_view(B)   # fix 3/7: same source as leaderboard
    return dict(ok=True, seat=seat, equity_aud=V["equity_aud"], cash_aud=V["cash_aud"], today_pnl_aud=V["today_pnl_aud"], pnl_since_inception_aud=V["pnl_since_inception_aud"], costs_aud=V["costs_aud"], net_of_costs=True,
                open=[k for k, p in B["positions"].items() if p["status"] == "open"], armed=[k for k, p in B["positions"].items() if p["status"] == "armed"], closed=len(B["closed"]))
def positions(seat=None):
    D_ = BK.load(); return dict(ok=True, positions=[dict(p, seat=s, peak_fav_R=p.get("peak_fav_R"), rail_state=p.get("rail_state")) for s, B in D_["seats"].items() if not seat or s == seat for p in B["positions"].values()])
def close(seat, pid, reason, price_fn=None, shares=None, pct=None):
    """Paper close at the live mark (armed -> cancelled). --shares N / --pct P = partial close (VET exit rail v1, Brett 2026-10-29 12:44 WITA);
    the residual stays open with its stop/target/rail; refused if it would leave < 1 share. Stops still run in code regardless."""
    with _TickLock():
        D_ = BK.load(); B = D_["seats"].get(seat)
        if B is None or pid not in B["positions"]: return dict(ok=False, error="no such position %s in seat %s" % (pid, seat))
        p = B["positions"][pid]; t = BK.now()
        if p["status"] == "armed":
            if shares is not None or pct is not None: return dict(ok=False, error="partial close only on open positions (armed: amend --shares or withdraw)")
            p.update(status="cancelled", why=reason, closed_utc=t.isoformat()); B["closed"].append(dict(p)); del B["positions"][pid]; BK._wj(BK.BOOK, D_); return dict(ok=True, cancelled=pid)
        m = (price_fn or BK.live_px)(p["ticker"]) or {}; x = m.get("last") or p.get("mark") or p["fill"]; au = D_.get("audusd") or BK.audusd(); tot = int(p["shares"])
        if shares is not None or pct is not None:
            h = int(shares) if shares is not None else int(tot * float(pct) / 100.0)
            if h < 1: return dict(ok=False, error="partial close size rounds to < 1 share")
            if tot - h < 1: return dict(ok=False, error="partial close would leave %d share(s) residual (< 1) - use a full close" % (tot - h))
            n_ = sum(1 for c in B["closed"] if str(c.get("id", "")).startswith(pid + "-P"))
            part = BK.partial_close(B, p, pid, x, h, au, t, "seat_partial_close: " + str(reason)[:120], "-P%d" % (n_ + 1))
            p.setdefault("rail_log", []).append(dict(utc=t.isoformat(), ev="seat_partial_close", px=x, shares=h, residual=p["shares"], reason=str(reason)[:120]))
            BK.mark_half_banked(p, t, x, h, "seat_partial_close")   # t1sync (TFO ruling 19:11 WITA 29 Oct): manual half-bank sets the same state as the rail's
            BK._wj(BK.BOOK, D_)
            _audit(_path=_apath(), cmd="close_partial", seat=seat, id=pid, reason=reason, exit=x, shares=h, residual=p["shares"], net_aud=part["net_aud"])
            return dict(ok=True, closed=part["id"], exit=x, closed_shares=h, residual_shares=p["shares"], net_aud=part["net_aud"])
        gross, cc = BK.realise(B, p, x, tot, au)   # FX-aware (P&L only for FX pairs)
        p.update(status="closed", exit=x, exit_reason="seat_close: " + str(reason)[:120], closed_utc=t.isoformat(), gross_aud=round(gross, 2), net_aud=round(gross - p.get("cost_aud", 0) - cc, 2))
        B["closed"].append(dict(p)); del B["positions"][pid]; BK._wj(BK.BOOK, D_); return dict(ok=True, closed=pid, exit=x, net_aud=p["net_aud"])
AUDIT = os.path.join(D.SD, "seat_cli_audit.jsonl")
def _audit(_path=None, **k):
    _p = _path or AUDIT; os.makedirs(os.path.dirname(_p), exist_ok=True)
    with open(_p, "a") as fh: fh.write(json.dumps(dict(utc=D.now().isoformat(), **k), default=str) + "\n")
def _apath():   # audit beside a temp book (tests), live audit otherwise
    return None if os.path.abspath(BK.BOOK) == os.path.abspath(BK._REAL_BOOK) else os.path.join(os.path.dirname(os.path.abspath(BK.BOOK)), "seat_cli_audit.jsonl")
_HELD = [0]
class _TickLock:   # same mkdir lock as run_tick.sh: never edit the book mid-tick (re-entrant in-process; temp books lock beside themselves)
    def __enter__(self):
        import time
        if _HELD[0]: _HELD[0] += 1; return self
        self.P_ = os.path.join(BK._state_dir(), ".tick.lock")
        for _ in range(20):
            try: os.mkdir(self.P_); _HELD[0] = 1; return self
            except FileExistsError: time.sleep(1.5)
        raise RuntimeError("book tick in progress (lock held 30s) - retry after the tick")
    def __exit__(self, *a):
        _HELD[0] -= 1
        if not _HELD[0]: os.rmdir(self.P_)
def withdraw(seat, pid, reason):
    import funder_gate as FG
    out = {}
    def f(Q):
        e = Q["entries"].get(pid)
        if e and e.get("book") == "veteran" and e.get("state") == FG.WAIT and pid.endswith(seat):
            e.update(state="withdrawn", why="seat_cli withdraw by %s: %s" % (seat, str(reason)[:160]), withdrawn_utc=D.now().isoformat()); out["queue"] = "withdrawn"
    with _TickLock():   # 2026-10-29 withdraw-fix: queue + book in ONE step under the tick lock (was: queue-only when WAIT, book left ARMED)
        B = BK.load()["seats"].get(seat) or {}; p = (B.get("positions") or {}).get(pid)
        if p and p["status"] == "open": return dict(ok=False, error="position %s is open - use close (withdraw never touches an open trade)" % pid)
        FG.update(f)
        if p and p["status"] == "armed":
            q_ = out.get("queue"); out = close(seat, pid, "withdrawn: " + str(reason)); out["queue"] = "withdrawn+armed->cancelled" if q_ else "armed->cancelled"
            def g(Q):
                e = Q["entries"].get(pid)
                if e and e.get("book") == "veteran": e.update(state="withdrawn", why=e.get("why") or ("seat_cli withdraw by %s: %s" % (seat, str(reason)[:160])), withdrawn_utc=e.get("withdrawn_utc") or D.now().isoformat())
            FG.update(g)
    if not out: return dict(ok=False, error="nothing pending for %s in seat %s" % (pid, seat))
    _audit(cmd="withdraw", seat=seat, id=pid, reason=reason, result=out.get("queue")); return dict(ok=True, id=pid, **{k: v for k, v in out.items() if k != "ok"})
def amend(seat, pid, reason, stop=None, shares=None, target=None):
    with _TickLock():
        D_ = BK.load(); B = D_["seats"].get(seat) or {}; p = (B.get("positions") or {}).get(pid)
        if not p: return dict(ok=False, error="no such position %s in seat %s" % (pid, seat))
        L = p["side"] == "LONG"; ref = p.get("fill") or p["entry"]; au = D_.get("audusd") or BK.audusd(); old = dict(stop=p["stop"], shares=p["shares"], target=p.get("target"))
        if target is not None:   # VET exit rail v1: open (and armed) positions; never inside the current mark; after T1 it is the residual target
            target = float(target); mk = p.get("mark") or ref
            if p["status"] == "open" and ((L and target <= mk) or (not L and target >= mk)): return dict(ok=False, error="target %g is inside the current mark %g" % (target, mk))
            if p["status"] == "armed" and ((L and target <= p["entry"]) or (not L and target >= p["entry"])): return dict(ok=False, error="target must be beyond entry")
        if stop is not None:
            stop = float(stop)
            if (L and stop < p["stop"]) or (not L and stop > p["stop"]): return dict(ok=False, error="stop may only tighten (rails / exit-rail floor %s): %g -> %g widens risk" % (p.get("rail_state") or "initial", p["stop"], stop))
            if p["status"] == "open":
                mk = p.get("mark") or ref
                if (L and stop >= mk) or (not L and stop <= mk): return dict(ok=False, error="stop %g is through the mark %g" % (stop, mk))
            elif (L and stop >= p["entry"]) or (not L and stop <= p["entry"]): return dict(ok=False, error="stop must stay on the loss side of entry")
            p["stop"] = stop
        if shares is not None:
            if p["status"] != "armed": return dict(ok=False, error="size amend only on armed orders (open trades: close or let stops run)")
            q = int(shares); eq = BK.seat_equity(seat); per = abs(BK.aud(p["ticker"], p["entry"], au) - BK.aud(p["ticker"], p["stop"], au)) if not BK.is_fx(p) else None
            if q < 1: return dict(ok=False, error="shares must be >= 1")
            if per is not None:
                r = per * q / eq
                if r < BK.RISK_MIN - 1e-9 or r > BK.RISK_MAX + 1e-9: return dict(ok=False, error="risk %.2f%% outside 1-5%% rails (tfo_a1pct_20261031: A floor 1%%, never force up; non-A 2%%)" % (r * 100))
                if BK.aud(p["ticker"], p["entry"], au) * q > eq: return dict(ok=False, error="notional above 1x seat equity")
                p["risk_aud"] = round(per * q, 2); p["risk_pct"] = round(r, 4)
            elif q > p["shares"]: return dict(ok=False, error="FX size may only be reduced via amend")
            p["shares"] = q
        if target is not None: p["target"] = target
        if p["status"] == "open" and p.get("initial_stop") is None: p["initial_stop"] = old["stop"]   # rail R0 stays on the original stop
        # 2026-10-29 infra (Marcus/Theo): stop/size amend recalculates risk_aud from new stop distance so heat is not overstated.
        if stop is not None or shares is not None:
            try:
                eq = BK.seat_equity(seat); q = int(p["shares"]); ref_px = float(p.get("fill") or p["entry"])
                per = BK.aud(p["ticker"], abs(ref_px - float(p["stop"])), au)
                p["risk_aud"] = round(per * q, 2); p["risk_pct"] = round(per * q / eq, 4) if eq else p.get("risk_pct")
            except Exception:
                pass  # never block amend
        p.setdefault("amends", []).append(dict(utc=D.now().isoformat(), old=old, stop=p["stop"], shares=p["shares"], target=p.get("target"), reason=str(reason)[:160], risk_aud=p.get("risk_aud"))); BK._wj(BK.BOOK, D_)
    _audit(_path=_apath(), cmd="amend", seat=seat, id=pid, reason=reason, old=old, new=dict(stop=p["stop"], shares=p["shares"], target=p.get("target"), risk_aud=p.get("risk_aud"))); return dict(ok=True, id=pid, old=old, stop=p["stop"], shares=p["shares"], target=p.get("target"), risk_aud=p.get("risk_aud"), risk_pct=p.get("risk_pct"))
def lessons(seat=None, add=None):
    if add:
        os.makedirs(D.SD, exist_ok=True)
        with open(LESSONS, "a") as fh: fh.write(json.dumps(dict(utc=D.now().isoformat(), seat=seat, lesson=add[:500])) + "\n")
    L = []
    if os.path.exists(LESSONS):
        with open(LESSONS) as fh: L = [json.loads(x) for x in fh if x.strip()]
    C = [c for s, B in BK.load()["seats"].items() if not seat or s == seat for c in B["closed"] if c.get("status") == "closed"]
    stops = [c for c in C if c.get("exit_reason") == "stop"]; wins = [c for c in C if (c.get("net_aud") or 0) > 0]
    return dict(ok=True, seat=seat, closed=len(C), wins=len(wins), stops=len(stops), net_aud=round(sum(c.get("net_aud") or 0 for c in C), 2),
                recent=[dict(id=c["id"], ticker=c["ticker"], side=c["side"], exit_reason=c.get("exit_reason"), net_aud=c.get("net_aud")) for c in C[-10:]],
                notes=[x for x in L if not seat or x.get("seat") in (seat, None)][-20:], **lesson_file(seat))
LESSON_DIR = os.path.join(BK.BASE, "research", "veteran_desk", "lessons")
SEAT_LESSON_FILE = {"macro": "theo.md"}   # 2026-10-29: seat -> research/veteran_desk/lessons/<name>.md (others matched by "(<seat> seat)" header)
def lesson_file(seat):
    """Markdown lessons file for a seat (Theo/macro -> theo.md). Returns {} when none."""
    if not seat or not os.path.isdir(LESSON_DIR): return {}
    fn = SEAT_LESSON_FILE.get(seat)
    if not fn:
        for f in sorted(os.listdir(LESSON_DIR)):
            if f.endswith(".md"):
                try: h = open(os.path.join(LESSON_DIR, f)).readline()
                except OSError: continue
                if "(%s seat)" % seat in h: fn = f; break
    pth = os.path.join(LESSON_DIR, fn) if fn else None
    if not pth or not os.path.exists(pth): return {}
    return dict(lessons_file=os.path.relpath(pth, BK.BASE), lessons_md=open(pth).read())
TICK_MINUTE_MOD = 5; TICK_DEFER_MAX_S = 75
def tick_minute(t=None):
    """2026-10-29 (Theo/Marcus): book.py ticks at :x0/:x5 - never submit inside a tick minute."""
    t = t or dt.datetime.now(); return t.minute % TICK_MINUTE_MOD == 0
def defer_tick_minute(now_fn=None, sleep=None):
    """Wait (<= 75s) until we are out of a :x0/:x5 minute. Returns seconds waited; raises if still inside (caller reports, nothing written)."""
    import time
    now_fn = now_fn or dt.datetime.now; sleep = sleep or time.sleep; waited = 0
    while tick_minute(now_fn()):
        if waited >= TICK_DEFER_MAX_S: raise RuntimeError("book tick minute (:x0/:x5) - submit deferred and not written; retry next minute")
        n = now_fn(); s_ = 61 - n.second; sleep(s_); waited += s_
    return waited
def confirm(seat, pid, queue_path=None):
    """tfo_review_fix_20261031: caller-side receipt. Only after the caller READ its propose result does it run confirm; only confirmed entries arm (book.tick), unconfirmed expire after 10 min."""
    import funder_gate as FG; res = dict(ok=False, error="no such entry")
    def _cf(Q):
        e = Q["entries"].get(pid)
        if e is None: return
        if not (e.get("seat") == seat or str(pid).endswith("-" + seat) or ("veteran:%s " % seat) in str((e.get("tags") or {}).get("strategy", ""))): res.update(error="entry is not seat %s" % seat); return
        if e.get("state") != FG.WAIT: res.update(error="entry state %s - cannot confirm" % e.get("state")); return
        e.update(acked=True, acked_utc=dt.datetime.now(dt.timezone.utc).isoformat(), acked_by=seat); res.clear(); res.update(ok=True, id=pid, acked=True)
    FG.update(_cf, queue_path); return res
MUTATING = ("propose", "close", "withdraw", "amend")
def main(a):
    if not a: print(__doc__); return 2
    c = a[0]
    try:
        if c in MUTATING: defer_tick_minute()
        if c == "propose" and len(a) >= 3:
            ch = a[a.index("--challenger") + 1] if "--challenger" in a else None; r = propose(a[1], json.loads(a[2]), ch, run_debate="--no-debate" not in a)
            if r.get("queued") and r.get("id"): r["confirm_required"] = "python3 -m veteran_desk.seat_cli confirm %s %s   (within 10 min, after you READ this result; unconfirmed -> expires, never arms)" % (a[1], r["id"])   # tfo_review_fix_20261031: ack = explicit caller receipt, not a stdout flush
            return _out(r)
        if c == "confirm" and len(a) >= 3: return _out(confirm(a[1], a[2]))   # tfo_deskrun_ack_20261031 / tfo_review_fix_20261031
        if c == "status" and len(a) >= 2: return _out(status(a[1]))
        if c == "positions": return _out(positions(a[1] if len(a) > 1 else None))
        if c == "close" and len(a) >= 4:
            sh = a[a.index("--shares") + 1] if "--shares" in a else None; pc = a[a.index("--pct") + 1] if "--pct" in a else None
            if sh is not None and pc is not None: return _out(dict(ok=False, error="use --shares OR --pct, not both"))
            rest = [x for i, x in enumerate(a[3:], 3) if x not in ("--shares", "--pct") and a[i - 1] not in ("--shares", "--pct")]
            return _out(close(a[1], a[2], " ".join(rest) or "close", shares=sh, pct=pc))
        if c == "withdraw" and len(a) >= 4: return _out(withdraw(a[1], a[2], " ".join(a[3:])))
        if c == "amend" and len(a) >= 4:
            st = a[a.index("--stop") + 1] if "--stop" in a else None; sh = a[a.index("--shares") + 1] if "--shares" in a else None; tg = a[a.index("--target") + 1] if "--target" in a else None
            fl = ("--stop", "--shares", "--target"); rest = [x for i, x in enumerate(a[3:], 3) if x not in fl and a[i - 1] not in fl]
            return _out(amend(a[1], a[2], " ".join(rest) or "amend", st, sh, tg))
        if c == "lessons":
            add = a[a.index("--add") + 1] if "--add" in a else None; seat = a[1] if len(a) > 1 and not a[1].startswith("--") else None; return _out(lessons(seat, add))
    except Exception as e: return _out(dict(ok=False, error="%s: %s" % (type(e).__name__, e)))
    print(__doc__); return 2
if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
