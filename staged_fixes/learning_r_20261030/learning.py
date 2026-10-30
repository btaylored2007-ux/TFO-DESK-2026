"""VETERAN DESK LEARNING LOOP (Brett approved 2026-10-28 20:26-20:27 WITA). PAPER ONLY. Advisory only: never loosens rails
(stop on every trade, 2-5% risk, firm caps, kill switch stay in book.py/desk.py code). Nothing auto-applies; TFO approves.
  python3 -m veteran_desk.learning sync            # journal every fill/close (idempotent) + shadow runner
  python3 -m veteran_desk.learning daily [--no-llm] # 21:00 WITA lessons.md per seat (rolling 30d) + Claude/GPT critique
  python3 -m veteran_desk.learning weekly [--no-llm]# Sat 11:00 WITA rule-change proposals, held-out test, critique
  python3 -m veteran_desk.learning approve SEAT PID --by NAME   # TFO approval -> shadow run (>=20 paper trades)
  python3 -m veteran_desk.learning promote SEAT PID --by NAME   # only after >=20 shadow trades and shadow beat baseline
  python3 -m veteran_desk.learning card
Models: Claude via ~/.local/bin/claude (-p opus) and GPT via the Codex CLI used by hunt_clearance (/Applications/ChatGPT.app/.../codex),
both through desk._llm (secret screen, usage log). No codex->claude fallback here: a down model is tagged MISSING, never blocks."""
import concurrent.futures as cf, datetime as dt, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); BASE = os.path.dirname(HERE)
LD = os.path.join(BASE, "state", "learning"); REP = os.path.join(BASE, "reports", "learning"); WEEKLY = os.path.join(REP, "weekly_proposals.json")
BOOK = os.path.join(BASE, "reports", "veteran_desk_book.json"); RUNS = os.path.join(BASE, "state", "veteran_desk", "runs")
UTC = dt.timezone.utc; WITA = dt.timezone(dt.timedelta(hours=8)); SHADOW_MIN = 20; KEEP_DAYS = 30
MODELS = (("gpt", "codex"),)   # Brett HARD 2026-10-29: GPT/Codex only; Claude retired from all VD reviews
if os.path.exists(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "config", "claude_retired.json")) or os.environ.get("TFO_CLAUDE_OFF") == "1":
    MODELS = (("gpt", "codex"),)   # Brett 2026-10-29 08:33 WITA: Claude removed from every review
GUARD = ("You are an ADVISORY sounding board for a PAPER trading desk. You cannot place, arm, size or loosen anything. "
         "Hard rails are fixed in code and never loosen: a stop on every trade, 2-5% risk per trade, firm caps, kill switch. ")
def now(): return dt.datetime.now(UTC)
def _sd(seat):
    p = os.path.join(LD, re.sub(r"[^a-z0-9_]", "", str(seat).lower()) or "unknown"); os.makedirs(p, exist_ok=True); return p
def _rj(p, d=None):
    try: return json.load(open(p))
    except Exception: return d
def _wj(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True); t = p + ".tmp"; json.dump(o, open(t, "w"), indent=1, default=str); os.replace(t, p)
def _jl(p):
    try: return [json.loads(x) for x in open(p) if x.strip()]
    except Exception: return []
def _app(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a") as fh: fh.write(json.dumps(o, default=str) + "\n")
def seats():
    sd = os.path.join(HERE, "seats"); out = {}
    for f in sorted(os.listdir(sd)):
        if f.endswith(".json"):
            try: c = json.load(open(os.path.join(sd, f))); out[c["seat"]] = c
            except Exception: pass
    return out
def trader_seats(): return [k for k, v in seats().items() if v.get("role") == "proposer"] or list(seats())
# ---------------- model calls ----------------
def _ask(engine, prompt, timeout=240, llm=None):
    if llm is None:
        sys.path.insert(0, BASE) if BASE not in sys.path else None
        from veteran_desk import desk as D; llm = D.llm   # D.llm so test mocks apply; its codex->claude fallback is tagged MISSING for GPT
    try: out = llm(engine, prompt, timeout)
    except Exception as e: out = "__ERR__ %s" % type(e).__name__
    return out or "__ERR__ empty"
def _clean(txt):
    t = txt or ""; k = t.rfind("\ncodex\n")   # Codex transcript: answer follows the last 'codex' line, before 'tokens used'
    if k >= 0: t = t[k + 7:]
    k = t.find("\ntokens used"); return (t[:k] if k >= 0 else t).strip()
def _parse(txt):
    t = _clean(txt); dec = json.JSONDecoder()
    for m in re.finditer(r"\{", t):
        try:
            o, _ = dec.raw_decode(t[m.start():])
            if isinstance(o, dict): return o
        except Exception: pass
    return None
def ask_both(prompt, timeout=240, llm=None, models=MODELS):
    """Both models in parallel. Returns {claude:{ok,..}, gpt:{ok,..}}; a down model -> ok False, status MISSING (never blocks)."""
    def one(lab_eng):
        lab, eng = lab_eng; raw = _ask(eng, prompt, timeout, llm)
        if raw.startswith("__ERR__") or raw.startswith("__FALLBACK"): return lab, dict(ok=False, status="MISSING", why=raw[:120])
        j = _parse(raw); return lab, dict(ok=True, status="ok", view=j, text=None if j else _clean(raw)[:600])
    with cf.ThreadPoolExecutor(max(1, len(models))) as ex: return dict(ex.map(one, models))
def _verdict(v):
    x = str(((v or {}).get("view") or {}).get("verdict") or "").lower()
    return x if x in ("take", "reduce", "skip") else ("missing" if not (v or {}).get("ok") else "unclear")
# ---------------- 5) pre-stage sounding board ----------------
def sound_board(rec, llm=None, timeout=240, models=MODELS):
    """Called by desk.run for each gate-passed trade BEFORE enqueue. Advisory: never changes gate/sizing. Logs per seat."""
    p, z, f = rec.get("proposal") or {}, rec.get("sizing") or {}, rec.get("features") or {}
    brief = dict(ticker=p.get("ticker"), side=p.get("side"), entry=p.get("entry"), stop=p.get("stop"), target=p.get("target"), grade=p.get("grade"), sleeve=p.get("sleeve"),
                 horizon_days=p.get("horizon_days"), thesis=p.get("thesis"), invalidation=p.get("invalidation"), venue=f.get("venue"),
                 size=dict(shares=z.get("shares"), risk_aud=z.get("risk_aud"), win_aud=z.get("win_aud"), risk_pct=(rec.get("gate") or {}).get("risk_pct")),
                 data={k: f.get(k) for k in list(f)[:25] if not isinstance(f.get(k), (list, dict))})
    pr = (GUARD + "Critique this proposed PAPER trade: the thesis, the main risks, and the size. Reply with ONE JSON object only: "
          '{"verdict":"take"|"reduce"|"skip","thesis_critique":str,"risks":[str],"size_view":"ok"|"too_big"|"too_small","one_line":str}\nTRADE:' + json.dumps(brief, default=str))
    V = ask_both(pr, timeout, llm, models)
    out = dict(utc=now().isoformat(), id=rec.get("id"), seat=rec.get("seat"), ticker=p.get("ticker"), advisory_only=True,
               verdicts={k: dict(verdict=_verdict(v), status=v["status"], one_line=((v.get("view") or {}).get("one_line") or v.get("text") or v.get("why") or "")[:300],
                                 size_view=(v.get("view") or {}).get("size_view"), risks=((v.get("view") or {}).get("risks") or [])[:4]) for k, v in V.items()},
               tags=[f"{k}_missing" for k, v in V.items() if not v["ok"]])
    if _real_desk(): _app(os.path.join(_sd(rec.get("seat")), "sounding_board.jsonl"), out)   # tests that redirect desk.SD never write real state
    return out
def _real_desk():
    D = sys.modules.get("veteran_desk.desk")
    return D is None or os.path.abspath(D.SD) == os.path.join(BASE, "state", "veteran_desk")
def sound_board_all(recs, llm=None, max_parallel=2):
    """Never raises; returns {id: verdict record}."""
    res = {}
    try:
        with cf.ThreadPoolExecutor(max(1, min(max_parallel, len(recs) or 1))) as ex:
            for r, v in zip(recs, ex.map(lambda r: _safe(sound_board, r, llm), recs)): res[r.get("id")] = v
    except Exception as e: res["_error"] = str(e)[:200]
    return res
# 2026-10-28 20:47 WITA Brett "trim the advisory": trades queue/fire FIRST; critique runs afterwards in a detached
# process, ONE model per trade (alternating Claude/GPT; VET_SB_MODEL=claude forces Claude only), hard timeout SB_TIMEOUT_S.
SB_TIMEOUT_S = int(os.environ.get("VET_SB_TIMEOUT_S", "60"))
def _one_model(i):
    f = os.environ.get("VET_SB_MODEL", "alternate").lower()
    if f in ("claude", "gpt"): return tuple(m for m in MODELS if m[0] == f)
    return (MODELS[i % len(MODELS)],)
def critique_async(recs, runid="run"):
    """Never blocks the caller: writes recs to a pending file and spawns a detached critique worker. Returns spawn info."""
    import subprocess
    try:
        if not recs: return dict(spawned=False, n=0)
        if not _real_desk() or os.path.abspath(LD) != os.path.join(BASE, "state", "learning"): return dict(spawned=False, n=len(recs), test_mode=True)   # tests/temp desks never spawn real model CLIs
        pf = os.path.join(LD, "pending_critique", "%s.json" % re.sub(r"[^A-Za-z0-9_-]", "", str(runid))); _wj(pf, recs)
        lf = open(os.path.join(LD, "pending_critique", "worker.log"), "a")
        subprocess.Popen([sys.executable, "-m", "veteran_desk.learning", "critique", pf], cwd=BASE, stdout=lf, stderr=lf, stdin=subprocess.DEVNULL, start_new_session=True)
        return dict(spawned=True, n=len(recs), file=pf, timeout_s=SB_TIMEOUT_S, mode=os.environ.get("VET_SB_MODEL", "alternate"))
    except Exception as e: return dict(spawned=False, error="%s: %s" % (type(e).__name__, str(e)[:160]))
def critique_file(pf, llm=None):
    """Worker: one model per trade, hard timeout, sequential (one CLI at a time on the Mac). Logs to each seat's sounding_board.jsonl."""
    recs = _rj(pf, []) or []; res = {}
    for i, r in enumerate(recs):
        res[r.get("id")] = _safe(lambda x: sound_board(x, llm, SB_TIMEOUT_S, _one_model(i)), r)
    try: os.replace(pf, pf + ".done")
    except Exception: pass
    return dict(n=len(recs), ids=list(res))
def _safe(fn, *a):
    try: return fn(*a)
    except Exception as e: return dict(error="%s: %s" % (type(e).__name__, str(e)[:160]), tags=["%s_missing" % m[0] for m in MODELS])
# ---------------- 1) journal ----------------
def _run_rec(pid):
    m = re.search(r"-(\d{8}-\d{4,6})-", pid or ""); fs = [m.group(1) + ".json"] if m else []
    try: fs += sorted(os.listdir(RUNS))[-60:][::-1]
    except Exception: pass
    for f in fs:
        R = _rj(os.path.join(RUNS, f), {}) or {}; r = next((r for r in R.get("proposals") or [] if r.get("id") == pid), None)
        if r: return r
    Q = (_rj(os.path.join(BASE, "state", "funder_queue.json"), {}) or {}).get("entries") or {}; e = Q.get(pid)
    return dict(proposal=dict(thesis=e.get("eli10"), grade=(re.search(r"grade (\S+)", str((e.get("tags") or {}).get("strategy"))) or [None, None])[1])) if e else None
def _sb(seat, pid):
    L = [x for x in _jl(os.path.join(_sd(seat), "sounding_board.jsonl")) if x.get("id") == pid]; return L[-1] if L else None
def mistake_tag(p, r_mult, sb=None):
    xr = str(p.get("exit_reason") or "")
    if xr == "kill_switch_flat": return "kill_switch_flat"
    if (p.get("slippage_bps") or 0) > 5: return "chased_entry"
    if r_mult is not None and r_mult < -1.15: return "stop_slippage_or_oversize"
    if sb and r_mult is not None and r_mult < 0 and all((sb.get("verdicts") or {}).get(k, {}).get("verdict") in ("skip", "reduce") for k in tuple(m[0] for m in MODELS)): return "ignored_both_models_dissent"   # 2026-10-29 fixer: GPT-only (Claude retired)
    if xr.startswith("seat_close") and (r_mult or 0) < 0: return "early_discretionary_exit_loss"
    if xr == "stop": return "thesis_wrong_stopped"
    if (r_mult or 0) > 0: return "none"
    return "unclassified"
R_SUSPECT = 20.0   # |R| above this is a units/denominator bug, never a real paper result; excluded from avg_r
def risk0(p, au=None):
    """2026-10-30 22:15 WITA (learning overlord): 1R = THIS leg's shares x |fill - ORIGINAL initial stop| in A$ (book.risk0_aud, live AUDUSD).
    The book's risk_aud on a partial/BE-moved leg is the RESIDUAL risk (A$0.52 on the macro EURUSD tail), which inflated R to 1,796."""
    try:
        if p.get("initial_stop") is None or float(p["initial_stop"]) == float(p["stop"]): return float(p.get("risk_aud") or 0) or None
        from veteran_desk import book as BK
        return BK.risk0_aud(dict(p, fill=float(p.get("fill") or p.get("entry"))), float(au or 0.70)) or None
    except Exception: return None
def _rmult(p, net, au=None):
    try:
        risk = risk0(p, au)
        if not risk: return None
        return round(float(net) / risk, 3)
    except Exception: return None
def entry_for(p, event, run=None, sb=None, au=None):
    pr = (run or {}).get("proposal") or {}; net = p.get("net_aud") if event == "close" else None; r = _rmult(p, net, au) if net is not None else None
    ai = dict(seat_engine=(run or {}).get("engine"), challenger=((run or {}).get("challenger") or {}).get("view"),
              devils_advocate=((run or {}).get("devils_advocate") or {}).get("kill_case") if isinstance((run or {}).get("devils_advocate"), dict) else None,
              sounding_board=(sb or {}).get("verdicts") or {m[0]: dict(verdict="missing", status="NOT_ASKED") for m in MODELS})
    rr = None
    try: rr = round(abs(float(p["target"]) - float(p["entry"])) / abs(float(p["entry"]) - float(p["stop"])), 2)
    except Exception: pass
    return dict(key=f"{p.get('id')}|{event}", event=event, journal_utc=now().isoformat(), event_utc=p.get("filled_utc") if event == "fill" else p.get("closed_utc"),
                id=p.get("id"), seat=p.get("seat"), ticker=p.get("ticker"), side=p.get("side"), venue=p.get("venue"),
                setup=dict(grade=pr.get("grade"), sleeve=p.get("sleeve") or pr.get("sleeve"), horizon_days=pr.get("horizon_days"), rr_planned=rr, posture=p.get("posture")),
                thesis=pr.get("thesis"), invalidation=pr.get("invalidation"), ai_input=ai,
                entry=p.get("fill") or p.get("entry"), planned_entry=p.get("entry"), stop=p.get("stop"), target=p.get("target"), shares=p.get("shares"), risk_aud=p.get("risk_aud"), initial_stop=p.get("initial_stop"), risk0_aud=(lambda v: round(v, 2) if v else None)(risk0(p, au)),
                exit=p.get("exit"), exit_reason=p.get("exit_reason"), pnl_aud_net=net, costs_aud=None if net is None else round((p.get("gross_aud") or 0) - net, 2),
                r_multiple=r, mistake_tag=mistake_tag(p, r, sb) if event == "close" else None,
                model_missing=[k for k, v in ai["sounding_board"].items() if (v or {}).get("verdict") == "missing"], paper_only=True)
def sync(D=None):
    """Append journal lines for every fill and close not yet journaled. Idempotent by id|event. Read-only on the book."""
    D = D or _rj(BOOK, {}) or {}; n = 0
    for seat, B in (D.get("seats") or {}).items():
        jp = os.path.join(_sd(seat), "journal.jsonl"); have = {x.get("key") for x in _jl(jp)}
        items = [p for p in (B.get("positions") or {}).values()] + list(B.get("closed") or [])
        for p in items:
            p = dict(p, seat=p.get("seat") or seat)
            evs = (["fill"] if p.get("filled_utc") else []) + (["close"] if p.get("status") == "closed" else [])
            for ev in evs:
                if f"{p.get('id')}|{ev}" in have: continue
                e = entry_for(p, ev, _run_rec(p.get("id")), _sb(seat, p.get("id")), D.get("audusd")); _app(jp, e); have.add(e["key"]); n += 1
                if ev == "close": _shadow_step(seat, e)
    return dict(ok=True, new_entries=n)
def closes(seat, days=None):
    C = [x for x in _jl(os.path.join(_sd(seat), "journal.jsonl")) if x.get("event") == "close" and x.get("pnl_aud_net") is not None]
    if days:
        cut = (now() - dt.timedelta(days=days)).isoformat(); C = [x for x in C if (x.get("event_utc") or x.get("journal_utc") or "") >= cut]
    return sorted(C, key=lambda x: x.get("event_utc") or x.get("journal_utc") or "")
# ---------------- 3/4) proposals, held-out test, shadow ----------------
def _cands(C):
    out = [dict(kind="min_rr", value=v, text=f"Skip setups with planned reward:risk below {v}") for v in (1.5, 2.0, 2.5, 3.0)]
    out += [dict(kind="skip_venue", value=v, text=f"Skip {v} setups") for v in sorted({x.get("venue") for x in C if x.get("venue")})]
    out += [dict(kind="skip_side", value=v, text=f"Skip {v} setups") for v in ("LONG", "SHORT")]
    out += [dict(kind="aplus_only", value=True, text="Only take A+ grade setups"), dict(kind="skip_spec", value=True, text="Skip spec-sleeve setups")]
    return out
def keeps(rule, x):
    """True = rule still takes the trade. Every rule is a FILTER (can only remove trades): it can never loosen a rail."""
    s = x.get("setup") or {}; k, v = rule["kind"], rule["value"]
    if k == "min_rr": return (s.get("rr_planned") or 0) >= v
    if k == "skip_venue": return x.get("venue") != v
    if k == "skip_side": return x.get("side") != v
    if k == "aplus_only": return s.get("grade") == "A+"
    if k == "skip_spec": return s.get("sleeve") != "spec"
    return True
def _net(rule, C, cost_mult=1.0):
    return round(sum((x["pnl_aud_net"] - (cost_mult - 1) * (x.get("costs_aud") or 0)) for x in C if rule is None or keeps(rule, x)), 2)
def propose(seat, min_trades=10, holdout=0.3):
    C = closes(seat); n = len(C)
    if n < min_trades: return dict(seat=seat, status="insufficient_data", closed_trades=n, need=min_trades, proposals=[])
    k = max(3, int(round(n * holdout))); tr, te = C[:-k], C[-k:]; base_tr, base_te = _net(None, tr), _net(None, te); P = []
    for c in _cands(C):
        kept_te = sum(1 for x in te if keeps(c, x))
        if not any(not keeps(c, x) for x in C): continue
        r = dict(c, train_net_aud=_net(c, tr), train_baseline_aud=base_tr, heldout_net_aud=_net(c, te), heldout_baseline_aud=base_te,
                 heldout_net_doubled_costs_aud=_net(c, te, 2.0), heldout_baseline_doubled_costs_aud=_net(None, te, 2.0), heldout_trades_kept=kept_te, heldout_trades=len(te))
        r["beats_current_net_of_costs"] = r["train_net_aud"] > base_tr and r["heldout_net_aud"] > base_te and r["heldout_net_doubled_costs_aud"] > r["heldout_baseline_doubled_costs_aud"] and kept_te > 0
        r["id"] = f"{seat}-{now().astimezone(WITA):%Y%m%d}-{c['kind']}-{str(c['value']).replace('.', 'p')}"
        r["status"] = "pending_tfo_approval" if r["beats_current_net_of_costs"] else "rejected_heldout"; P.append(r)
    P.sort(key=lambda r: (not r["beats_current_net_of_costs"], -(r["heldout_net_aud"] - base_te)))
    return dict(seat=seat, status="ok", closed_trades=n, train=len(tr), heldout=len(te), proposals=P[:8])
def weekly(use_llm=True, llm=None):
    out = dict(generated_utc=now().isoformat(), generated_wita=now().astimezone(WITA).strftime("%Y-%m-%d %H:%M WITA"), paper_only=True, auto_apply=False,
               note="Proposals are FILTERS only (can only skip trades). Nothing is applied automatically; TFO approves, then >=20 shadow paper trades before promotion.", seats={})
    for s in trader_seats():
        r = propose(s)
        if use_llm and any(p["status"] == "pending_tfo_approval" for p in r["proposals"]):
            pr = GUARD + "Critique these rule-change proposals for one seat (overfitting, sample size, cost realism). Reply ONE JSON: {\"verdict\":\"support\"|\"doubt\"|\"reject\",\"critique\":str}\n" + json.dumps(r, default=str)[:6000]
            r["model_critique"] = {k: dict(status=v["status"], view=v.get("view") or v.get("text") or v.get("why")) for k, v in ask_both(pr, 300, llm).items()}
        out["seats"][s] = r
        _wj(os.path.join(_sd(s), "proposals_latest.json"), r)
    _wj(WEEKLY, out); return out
def _cand_path(seat): return os.path.join(_sd(seat), "candidates.json")
def approve(seat, pid, by):
    if not by: return dict(ok=False, why="--by NAME required (TFO approval)")
    W = _rj(WEEKLY, {}) or {}; P = next((p for p in ((W.get("seats") or {}).get(seat) or {}).get("proposals", []) if p["id"] == pid), None)
    if not P: return dict(ok=False, why="proposal not found in weekly_proposals.json")
    if P["status"] != "pending_tfo_approval": return dict(ok=False, why="only pending_tfo_approval proposals can be approved")
    C = _rj(_cand_path(seat), {}) or {}
    C[pid] = dict(rule={k: P[k] for k in ("kind", "value", "text")}, status="shadow", approved_by=by, approved_utc=now().isoformat(), shadow=[], need=SHADOW_MIN)
    _wj(_cand_path(seat), C); return dict(ok=True, seat=seat, id=pid, status="shadow")
def _shadow_step(seat, e):
    C = _rj(_cand_path(seat), {}) or {}; ch = False
    for pid, c in C.items():
        if c.get("status") != "shadow" or (e.get("event_utc") or "") < c["approved_utc"]: continue
        if any(s["id"] == e["id"] for s in c["shadow"]): continue
        tk = keeps(c["rule"], e); c["shadow"].append(dict(id=e["id"], taken=tk, actual_net_aud=e["pnl_aud_net"], shadow_net_aud=e["pnl_aud_net"] if tk else 0.0)); ch = True
        if len(c["shadow"]) >= SHADOW_MIN:
            a, b = sum(x["shadow_net_aud"] for x in c["shadow"]), sum(x["actual_net_aud"] for x in c["shadow"])
            c.update(status="promotable" if a > b else "failed_shadow", shadow_net_aud=round(a, 2), actual_net_aud=round(b, 2), decided_utc=now().isoformat())
    if ch: _wj(_cand_path(seat), C)
def promote(seat, pid, by):
    C = _rj(_cand_path(seat), {}) or {}; c = C.get(pid)
    if not c: return dict(ok=False, why="no such candidate")
    if len(c.get("shadow", [])) < SHADOW_MIN or c.get("status") != "promotable": return dict(ok=False, why=f"needs >= {SHADOW_MIN} shadow paper trades and status promotable (now {len(c.get('shadow', []))}, {c.get('status')})")
    if not by: return dict(ok=False, why="--by NAME required")
    c.update(status="promoted", promoted_by=by, promoted_utc=now().isoformat()); _wj(_cand_path(seat), C)
    A = _rj(os.path.join(_sd(seat), "active_rules.json"), []) or []; A.append(dict(id=pid, rule=c["rule"], promoted_by=by, promoted_utc=c["promoted_utc"])); _wj(os.path.join(_sd(seat), "active_rules.json"), A)
    return dict(ok=True, id=pid, status="promoted", note="filter rule only; rails unchanged")
# ---------------- 2) daily review ----------------
TAG_LESSON = dict(thesis_wrong_stopped="Stops were hit as designed: tighten the thesis check (invalidation must be specific) before staging.",
                  chased_entry="Entries filled >5 bps through plan: wait for the limit, never chase.",
                  stop_slippage_or_oversize="Losses ran past -1R: pick stops with room for gaps or size down within the 2-5% rail.",
                  ignored_both_models_dissent="Both Claude and GPT said skip/reduce on a loser: weigh joint dissent harder (advisory).",
                  early_discretionary_exit_loss="Discretionary early exits lost money: let the code stop/target do the work.",
                  kill_switch_flat="Desk kill switch flattened positions: correlated heat too high.")
def _lessons(seat):
    C = closes(seat, KEEP_DAYS); L = []
    if not C: return ["No closed paper trades in the last 30 days yet: follow the playbook, stop on every trade, nothing to learn from data yet."], dict(n=0)
    w = [x for x in C if x["pnl_aud_net"] > 0]; R = [x["r_multiple"] for x in C if x.get("r_multiple") is not None and abs(x["r_multiple"]) <= R_SUSPECT]
    tags = {}
    for x in C:
        if x.get("mistake_tag") not in (None, "none"): tags[x["mistake_tag"]] = tags.get(x["mistake_tag"], 0) + 1
    for t, k in sorted(tags.items(), key=lambda z: -z[1]): L.append(f"{TAG_LESSON.get(t, t)} ({k}x in 30d)")
    st = dict(n=len(C), wins=len(w), net_aud=round(sum(x["pnl_aud_net"] for x in C), 2), avg_r=round(sum(R) / len(R), 2) if R else None, tags=tags)
    by = {}
    for x in C: by.setdefault(x.get("venue"), []).append(x["pnl_aud_net"])
    worst = min(by.items(), key=lambda z: sum(z[1]))
    if sum(worst[1]) < 0 and len(by) > 1: L.append(f"Weakest venue last 30d: {worst[0]} (A${sum(worst[1]):,.0f} net over {len(worst[1])} trades).")
    L.append(f"30d record: {st['wins']}/{st['n']} winners, net A${st['net_aud']:,.0f} after costs, avg R {st['avg_r']}.")
    return L, st
def daily(use_llm=True, llm=None, today=None):
    today = today or now().astimezone(WITA).date(); res = {}
    for s in trader_seats():
        L, st = _lessons(s); crit = {}
        if use_llm:
            pr = GUARD + f"Daily review for seat '{s}'. Critique these lessons: are they right, what is missing? Reply ONE JSON: {{\"agree\":true|false,\"critique\":str,\"extra_lesson\":str}}\n" + json.dumps(dict(lessons=L, stats=st, recent=closes(s, KEEP_DAYS)[-10:]), default=str)[:6000]
            crit = ask_both(pr, 300, llm)
        p = os.path.join(_sd(s), "lessons.md"); old = open(p).read() if os.path.exists(p) else ""
        secs = {m.group(1): m.group(0).rstrip() for m in re.finditer(r"^## (\d{4}-\d{2}-\d{2})\n.*?(?=^## \d{4}-\d{2}-\d{2}\n|\Z)", old, re.S | re.M)}
        body = f"## {today.isoformat()}\n" + "".join(f"- {'TOP: ' if i == 0 else ''}{x}\n" for i, x in enumerate(L))
        if use_llm:
            body += "### Sounding boards (advisory)\n" + "".join(f"- {k.upper()}: " + (json.dumps(v.get("view") or v.get("text"), default=str)[:400] if v["ok"] else "MISSING (model down) - review not blocked") + "\n" for k, v in crit.items())
        secs[today.isoformat()] = body.rstrip()
        cut = (today - dt.timedelta(days=KEEP_DAYS - 1)).isoformat(); keep = sorted((d for d in secs if d >= cut), reverse=True)
        txt = f"# Lessons - {s} (rolling {KEEP_DAYS} days, PAPER, advisory; rails never loosen)\n\n" + "\n\n".join(secs[d] for d in keep) + "\n"
        tmp = p + ".tmp"; open(tmp, "w").write(txt); os.replace(tmp, p)
        res[s] = dict(top=L[0], stats=st, models={k: v["status"] for k, v in crit.items()})
    _wj(os.path.join(REP, "daily_review_latest.json"), dict(utc=now().isoformat(), seats=res)); return res
def top_lesson(seat):
    p = os.path.join(_sd(seat), "lessons.md")
    try: m = re.search(r"^- TOP: (.+)$", open(p).read(), re.M); return m.group(1) if m else None
    except Exception: return None
def lessons_for_prompt(seat, limit=900):
    """Read before every hunt (desk.prop_prompt). Advisory text; cannot change rails."""
    try:
        p = os.path.join(LD, seat, "lessons.md"); t = open(p).read() if os.path.exists(p) else ""
        sec = t.split("\n## ")[1] if "\n## " in t else ""
        A = _rj(os.path.join(LD, seat, "active_rules.json"), []) or []
        s = ("\nYOUR OWN LESSONS (latest daily review, advisory; never loosen stops/2-5% risk/caps):\n## " + sec[:limit]) if sec else ""
        if A: s += "\nTFO-PROMOTED FILTERS (apply: stand aside if a setup fails one): " + "; ".join(a["rule"]["text"] for a in A)
        return s
    except Exception: return ""
# ---------------- 6) card ----------------
def card():
    out = []
    for s in trader_seats():
        J = _jl(os.path.join(LD, s, "journal.jsonl")); SB = _jl(os.path.join(LD, s, "sounding_board.jsonl"))
        P = ((_rj(WEEKLY, {}) or {}).get("seats") or {}).get(s) or {}; C = _rj(os.path.join(LD, s, "candidates.json"), {}) or {}
        ag = {}
        for k in tuple(m[0] for m in MODELS):   # 2026-10-29 fixer: card counts only active models (Claude retired)
            v = [x["verdicts"].get(k, {}).get("verdict") for x in SB if x.get("verdicts")]; ans = [x for x in v if x not in ("missing", None)]
            ag[k] = dict(asked=len(v), answered=len(ans), missing=len(v) - len(ans), agree_pct=round(100 * sum(1 for x in ans if x == "take") / len(ans)) if ans else None)
        out.append(dict(seat=s, journal=len(J), closes=sum(1 for x in J if x.get("event") == "close"), top_lesson=top_lesson(s),
                        pending=sum(1 for p in P.get("proposals", []) if p.get("status") == "pending_tfo_approval"),
                        shadow=sum(1 for c in C.values() if c.get("status") == "shadow"), agreement=ag))
    return dict(seats=out, weekly_utc=(_rj(WEEKLY, {}) or {}).get("generated_utc"), paper_only=True)
def main(a):
    if not a: print(__doc__); return 2
    c, nl = a[0], "--no-llm" in a; by = a[a.index("--by") + 1] if "--by" in a else None
    r = dict(sync=lambda: sync(), daily=lambda: daily(not nl), weekly=lambda: weekly(not nl), card=card,
             approve=lambda: approve(a[1], a[2], by), promote=lambda: promote(a[1], a[2], by), critique=lambda: critique_file(a[1])).get(c)
    if not r: print(__doc__); return 2
    if c in ("daily", "weekly"): sync()
    print(json.dumps(r(), default=str, indent=1)[:20000]); return 0
if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
