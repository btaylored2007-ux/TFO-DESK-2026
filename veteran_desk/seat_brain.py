"""seatagents_20261103 (PAPER): every THF seat's own persistent BRAIN (Brett URGENT BUILD 21:43 WITA 2 Nov 2026, fm-20261102134400-3e9d).
Per seat, under state/thf/seat_brain/<seat>/:
  profile.json  identity: name, role, owned universe, risk rules, schedule, reports_to, book snapshot (from the seat json + the book, read-only)
  facts.md      standing facts (code-seeded from all available data; the NIGHTLY Claude review rewrites them)
  journal.md    dated journal, append only: every round, chat reply, lesson, review
  lessons.jsonl one lesson per CLOSED trade (lesson_loop text) or WITHDRAWN/MISSED arm (why + the rule to apply next time); dedup by trade id
SELF-LEARNING loop: lessons_from_book() runs after every round (and nightly) off state/thf/thf_book.json seats[*].closed (NEVER written);
nightly() reviews each seat with AI; successful reviews update facts, while unavailable AI preserves learned facts and saves a separate deterministic snapshot.
prompt_block(seat) feeds MY FACTS / JOURNAL / LESSONS (<=1.1KB) into the round prompt (desk.prop_prompt) and the chat prompt (thf_msgdesk.prompt).
All AIs go through the existing chain; nothing here arms, places or edits a ledger."""
import datetime as dt, json, os, re, time
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(BASE, "state", "thf", "seat_brain"); BOOK = os.path.join(BASE, "state", "thf", "thf_book.json")
STATUS = os.path.join(DIR, "_nightly.json"); WITA = dt.timezone(dt.timedelta(hours=8))
MAX_FACTS = 30; FACT_CHARS = 2600; JOURNAL_MAX_LINES = 4000
def now_wita(): return dt.datetime.now(WITA).strftime("%Y-%m-%d %H:%M WITA")
def _f(x, d=0.0):
    try: return float(x) if x is not None else d
    except Exception: return d
def _d(seat):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}",str(seat)):raise ValueError("invalid brain identity")
    return os.path.join(DIR,seat)
def _seats():
    from veteran_desk import seat_registry as SR; return SR.load()
def _book(B=None):
    if B is not None: return B
    try: return json.load(open(BOOK))
    except Exception: return {}
LIVE_DIR = DIR
def _test_live(p=None):
    """seatagents_20261103 16:45: unit tests never write the LIVE brains (test chain_up runs had put fake 'roll-call summarised to Brett' lines into
    Marcus's and the Head Trader's journals, which their prompts then read). A test that redirects DIR to a temp folder may write there."""
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) and os.path.realpath(str(p or DIR)).startswith(os.path.realpath(LIVE_DIR))
def _write(p, s):
    if _test_live(p): return None
    os.makedirs(os.path.dirname(p), exist_ok=True); t = p + ".%d.%d.tmp" % (os.getpid(),time.time_ns())
    with open(t,"w") as stream:
        stream.write(s); stream.flush(); os.fsync(stream.fileno())
    os.replace(t,p)
    fd=os.open(os.path.dirname(p),os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)
# ---------------- identity / profile ----------------
def profile(seat, S=None, B=None):
    S = S or _seats(); j = S.get(seat) or {}; B = _book(B); sb = (B.get("seats") or {}).get(seat) or {}
    from veteran_desk import seat_registry as SR
    opn = [dict(id=k, t=v.get("ticker"), side=v.get("side"), st=v.get("status"), grade=v.get("grade"), risk=v.get("risk_aud")) for k, v in (sb.get("positions") or {}).items() if isinstance(v, dict)]
    _day = (None, sb.get("today_pnl_aud"), "book")
    if sb:   # seatagents_20261103 16:40 (dayclock_20261103, Brett 16:01 "Figures are not right"): 'today' = the dashboard's 06:00 WITA clock; book day labelled
        try:
            from veteran_desk import day_clock as DC; _day = DC.day_of(seat, DC.seat_days(B) if B else None)
        except Exception: pass
    return dict(seat=seat, name=j.get("trader_name") or SR.name(seat, S), role=j.get("role"), description=str(j.get("description") or "")[:400], universe_groups=j.get("universe_groups") or [],
                data_inputs=j.get("data_inputs") or [], risk_rules=j.get("sizing") or {}, rails_note=str(j.get("rails_note") or "")[:300], can_arm=bool(j.get("can_arm")), paper_only=True,
                schedule=j.get("schedule") or {}, reports_to=SR.report_target(seat), owns=j.get("owns") or [],
                book=dict(equity_aud=sb.get("equity_aud"), seed_aud=sb.get("seed_aud"), pnl_since_seed_aud=(round(_f(sb.get("equity_aud")) - _f(sb.get("seed_aud")), 2) if sb else None),
                          today_aud_since_0600_wita_dash_clock=_day[0], book_day_aud_since_0000_wita_rails=_day[1], today_clock=_day[2],
                          open=opn, closed_n=len(sb.get("closed") or [])) if sb else None, updated_wita=now_wita())
def ensure(seat, S=None, B=None):
    """Create/refresh the seat's brain folder: profile.json (always refreshed), facts.md + journal.md (seeded once)."""
    S = S or _seats(); d = _d(seat); os.makedirs(d, exist_ok=True)
    P = profile(seat, S, B); _write(os.path.join(d, "profile.json"), json.dumps(P, indent=1, default=str))
    if not os.path.exists(os.path.join(d, "facts.md")): write_facts(seat, code_facts(seat, S, B, P), "code seed")
    if not os.path.exists(os.path.join(d, "journal.md")):
        _write(os.path.join(d, "journal.md"), "# %s (%s) journal - dated, append only (seatagents_20261103, PAPER)\n" % (P["name"], seat))
        journal(seat, "init", "brain created: profile + facts seeded from the seat json and the paper book")
    return P
def code_facts(seat, S=None, B=None, P=None):
    """Deterministic standing facts from all available data (no AI)."""
    P = P or profile(seat, S, B); F = []
    F.append("I am %s, seat %s, role %s. PAPER desk only. Reports to %s." % (P["name"], seat, P["role"], P["reports_to"]))
    if P["universe_groups"]: F.append("Owned universe: %s." % ", ".join(map(str, P["universe_groups"]))[:200])
    elif P["role"] in ("engineer",): F.append("Owned: the code_hands/fixer pipeline (queue, priorities, results) and the droplet code under the guarded rules.")
    elif P["role"] in ("cfo",): F.append("Owned: the money view (P&L net of costs in A$, allocation advice, drawdown/heat, fee pace, per-seat league incl. AI cost).")
    rr = P.get("risk_rules") or {}
    if rr:
        keys = [k for k in ("A_risk_pct", "A_plus_risk_pct", "risk_default_pct", "max_risk_pct", "hard_cap_pct", "clip_notional_aud") if rr.get(k) is not None]
        if keys: F.append("Risk rules: " + ", ".join("%s=%s" % (k, rr[k]) for k in keys) + "; stop on every trade; B = half size + tight (b_tight); C = stand aside.")
    F.append("Arming: %s" % (   # seatagents_20261103 17:18 (FX helper handoff): Marcus/HT run no book but DO have head powers
        "I don't run a book; seats arm their own names; I can arm/cancel/half-bank via head_powers (seat_cli head)." if P["role"] == "head" else
        "I don't run a book; seats arm their own names; I can cancel/half-bank/cancel_if/amend via seat_cli (audited) but never arm." if P["role"] == "head_trader" else
        "I cannot arm, place or size (oversight/staff seat)." if not P["can_arm"] and P["role"] != "proposer" else "I arm my own names on PAPER with no tick; Marcus/Head Trader only veto, cancel or adjust."))
    bk = P.get("book")
    if bk: F.append("Book (at %s): equity A$%s, seed A$%s, P&L since seed A$%s, %d open/armed, %d closed rows. Today's P/L is NOT a standing fact: quote it from PER-SEAT MONEY / the dashboard (since 06:00 WITA, the leaderboard clock)." % (
        P.get("updated_wita"), bk.get("equity_aud"), bk.get("seed_aud"), bk.get("pnl_since_seed_aud"), len(bk.get("open") or []), bk.get("closed_n") or 0))   # seatagents_20261103 16:40 dayclock: no midnight 'today' frozen into facts
    try:
        from veteran_desk import lesson_loop as LL; st = (LL.read_stats(os.path.join(BASE, "state", "thf")).get("seats") or {}).get(seat) or {}   # lessonsrc_20261103: THF stats dir always (BK._state_dir() is state/veteran_desk in a process without THF_DESK=1 -> 3 Nov 15:48 crypto facts got the OLD VD seat's 'Trade stats: 1 closed, win 100%, giveback 89%')
        if st.get("trades"): F.append("Trade stats: %d closed, win %.0f%%, avg %.2fR, giveback %.0f%%, slip %.1f bps." % (st["trades"], 100 * _f(st.get("win_rate")), _f(st.get("avg_R")), 100 * _f(st.get("giveback_ratio")), _f(st.get("avg_slippage_bps"))))
    except Exception: pass
    sc = P.get("schedule") or {}
    if sc: F.append("My schedule: " + "; ".join("%s=%s" % (k, v) for k, v in sc.items() if k != "note")[:220] + ".")
    limits = "6/h, 1 per colleague per 15 min, 2 hops"
    try:
        from veteran_desk import seat_mail as SM
        limits = SM.limits_text()
    except Exception: pass
    F.append("Messaging: to_seats {seat|all|marcus|head_trader: msg} (%s); formal reports via report_to_marcus; Brett talks only to the Head Trader." % limits)
    return F
def write_facts(seat, facts, src="code"):
    facts = [re.sub(r"\s+", " ", str(x)).strip() for x in (facts or []) if str(x).strip()][:MAX_FACTS]
    body = "";
    for x in facts:
        if len(body) + len(x) + 3 > FACT_CHARS: break
        body += "- " + x + "\n"
    _write(os.path.join(_d(seat), "facts.md"), "# %s standing facts (updated %s by %s; seatagents_20261103, PAPER)\n%s" % (seat, now_wita(), src, body)); return facts
def read_facts(seat):
    try: return [l[2:].strip() for l in open(os.path.join(_d(seat), "facts.md")).read().splitlines() if l.startswith("- ")]
    except Exception: return []
# ---------------- journal / lessons ----------------
def journal(seat, kind, text):
    p=os.path.join(_d(seat),"journal.md")
    if _test_live(p):return None
    os.makedirs(_d(seat),exist_ok=True)
    line="- %s [%s] %s\n"%(now_wita(),kind,re.sub(r"\s+"," ",str(text or "")).strip()[:400])
    try:
        import fcntl
        with open(p+".lock","a") as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            with open(p,"a") as stream:stream.write(line);stream.flush()
            rows=open(p).read().splitlines()
            if len(rows)>JOURNAL_MAX_LINES+50:_write(p,"\n".join([rows[0]]+rows[-JOURNAL_MAX_LINES:])+"\n")
            fcntl.flock(lock,fcntl.LOCK_UN)
    except Exception:return None
    return line
def read_journal(seat, n=5):
    try: return [l for l in open(os.path.join(_d(seat), "journal.md")).read().splitlines() if l.startswith("- ")][-n:]
    except Exception: return []
def read_lessons(seat,n=3):
    rows=[]
    try:
        for line in open(os.path.join(_d(seat),"lessons.jsonl")):
            try:
                item=json.loads(line)
                if isinstance(item,dict):rows.append(item)
            except (ValueError,TypeError):continue
    except OSError:pass
    return rows[-n:]
def lesson(seat, kind, text, ref=None, **extra):
    """Write one lesson (dedup by ref) + journal it. kind: trade | miss | chat | review."""
    d = _d(seat); p = os.path.join(d, "lessons.jsonl")
    if _test_live(p): return None
    os.makedirs(d, exist_ok=True)
    import fcntl
    row = dict(wita=now_wita(), seat=seat, kind=kind, ref=ref, lesson=re.sub(r"\s+", " ", str(text)).strip()[:600], **extra)
    with open(p,"a+") as stream:
        fcntl.flock(stream,fcntl.LOCK_EX);stream.seek(0)
        if ref:
            for line in stream:
                try:
                    if json.loads(line).get("ref")==ref:return None
                except (ValueError,TypeError,AttributeError):continue
        stream.write(json.dumps(row,default=str)+"\n");stream.flush();os.fsync(stream.fileno())
        fcntl.flock(stream,fcntl.LOCK_UN)
    fd=os.open(d,os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)
    journal(seat, "lesson:" + kind, row["lesson"]); return row
_MISS_RULES = (("stale b arm|tight-b|2.5-7% from price", "B arms must sit within 1.5% of price with a breakout-side trigger; a resting B that drifts >1 session is cancelled - do not arm a B I cannot fill today."),
               ("correlat|cluster|duplicate|same .*beta", "One owner per theme: before arming, check the desk for the same market+side cluster; a second Euro-bank or credit short is a duplicate, not a new idea."),
               ("below .* stop|through .* stop|low .* below", "Re-check the stop against TODAY'S range before arming; if price already traded through the stop the setup is dead, not a discount."),
               ("reclaim|cancel if", "A conditional cancel means the thesis has a hard line; respect the level and move on when it is reclaimed."),
               ("expired|unfilled|never filled|not filled", "Entries must be within reach of the live quote; an unfilled arm is a missed trade - set the entry off the live price, not the prior close."))
def _miss_rule(why):
    w = str(why or "").lower()
    for rx, rule in _MISS_RULES:
        if re.search(rx, w): return rule
    return "Record why the arm was withdrawn and fix the cause at the next setup (entry distance, catalyst freshness, cluster check, stop sanity)."
def lessons_from_book(B=None, S=None, seats=None):
    """Self-learning loop: every closed row in the book -> a lesson (filled close = lesson_loop text; cancelled/expired/unfilled = MISS lesson). Read-only on the book."""
    B = _book(B); out = []
    for seat, sb in (B.get("seats") or {}).items():
        if seats and seat not in seats: continue
        for p in (sb.get("closed") or []):
            if not isinstance(p, dict) or not p.get("id"): continue
            st = str(p.get("status") or "").lower(); filled = p.get("fill") is not None and p.get("net_aud") is not None
            if filled:
                try:
                    from veteran_desk import lesson_loop as LL; L = LL.lesson_for(seat, p, sb)
                    txt = "TRADE %s %s grade %s closed %s net A$%.0f (%.2fR banked / %.2fR peak): %s" % (p.get("ticker"), p.get("side"), p.get("grade"), str(p.get("closed_utc") or "")[:10], _f(L.get("net_aud")), _f(L.get("R_banked")), _f(L.get("R_peak")), L.get("lesson"))
                    extra = dict(net_aud=L.get("net_aud"), R_banked=L.get("R_banked"), R_peak=L.get("R_peak"), exit_reason=p.get("exit_reason"), ticker=p.get("ticker"))
                except Exception:
                    txt = "TRADE %s %s grade %s closed net A$%s via %s" % (p.get("ticker"), p.get("side"), p.get("grade"), p.get("net_aud"), p.get("exit_reason")); extra = dict(ticker=p.get("ticker"))
                r = lesson(seat, "trade", txt, ref=p["id"], **extra)
            else:
                why = p.get("why") or p.get("reason") or p.get("exit_reason") or st
                txt = "MISS %s %s grade %s armed %s, %s %s: %s. Next time: %s" % (p.get("ticker"), p.get("side"), p.get("grade"), str(p.get("armed_utc") or "")[:10], st or "withdrawn", str(p.get("closed_utc") or "")[:10], str(why)[:200], _miss_rule(why))
                r = lesson(seat, "miss", txt, ref=p["id"], ticker=p.get("ticker"), status=st)
            if filled and p.get("net_aud") is not None:
                trace = os.environ.get("THF_LESSON_TRACE")
                if trace or not os.environ.get("PYTEST_CURRENT_TEST"):
                    from veteran_desk import lesson_trace as LT
                    LT.observe_book_close(seat, str(p["id"]), trace or os.path.join(BASE, "state", "thf", "lesson_trace.jsonl"),
                                          dict(source="book_close", trade_id=str(p["id"]), net_aud=p.get("net_aud"),
                                               closed_utc=p.get("closed_utc")))
            if r: out.append(r)
    return out
# ---------------- hooks ----------------
def after_round(seat, j, wake=None):
    """Cheap code hook after every engine round: journal line + lessons from any new closes/misses + profile refresh."""
    if os.environ.get("PYTEST_CURRENT_TEST"): return None
    try:
        j = j if isinstance(j, dict) else {}
        ts = j.get("to_seats") if isinstance(j.get("to_seats"), dict) else {}
        journal(seat, "round", "%s%s %s %s | %s%s%s" % (("wake %s " % wake) if wake else "", j.get("action") or ("brain_down" if j.get("_brain_down") else "no-json"), j.get("ticker") or "", j.get("grade") or "",
                str(j.get("thesis") or j.get("reason") or j.get("why_not_choppy") or "")[:160], (" | mailed %s" % ",".join(list(ts)[:4])) if ts else "", (" | report_to_marcus") if j.get("report_to_marcus") else ""))
        lessons_from_book(seats=[seat]); ensure(seat)
    except Exception: pass
def after_chat(seat, text, reply, brain=None, task_id=None):
    trace = os.environ.get("THF_LESSON_TRACE")
    if not trace and not os.environ.get("PYTEST_CURRENT_TEST"):
        trace = os.path.join(BASE, "state", "thf", "lesson_trace.jsonl")
    if trace:
        try:
            from veteran_desk import lesson_trace as LT
            LT.record_citation(seat, reply, trace, task_id)
        except Exception:
            pass
    if os.environ.get("PYTEST_CURRENT_TEST") and not trace: return None
    try: journal(seat, "chat", "%s | %s -> %s" % (brain or "ai", re.sub(r"\s+", " ", str(text))[:120], re.sub(r"\s+", " ", str(reply))[:200]))
    except Exception: pass
# ---------------- prompt block ----------------
TODAY_RX = __import__("re").compile(r",?\s*today\s+[-+]?A\$\s?[-+]?\d[\d,]*(?<=\d)(?:\.\d+)?", __import__("re").I)
def prompt_block(seat, cap=1100, task_id=None):
    """Retain the latest saved own review and newest own lesson within the cap.

    Complete lesson text/ref takes priority over optional structured fields,
    facts and journal. Reviews stay unverified hypotheses. A trace row, when
    written, only records which refs were placed in this prompt. It is not
    evidence the model cited them or that a lesson changed any decision.
    """
    try:
        cap = max(0, int(cap))
        if not cap: return ""
        F = read_facts(seat); J = read_journal(seat, 3); history = read_lessons(seat, 1000)
        F = [TODAY_RX.sub("", x) for x in F]
        review = next((row for row in reversed(history) if row.get("kind") == "review" and str(row.get("lesson") or "").strip()), None)
        if review is not None:
            Ls = [review]
            newest = next((row for row in reversed(history) if row.get("kind") != "review"), None)
            if newest is not None: Ls.append(newest)
        else: Ls = list(reversed(history[-2:]))
        if not F and not J and not Ls: return ""
        s = "MY BRAIN [THF seat=%s; advisory; risk rules unchanged]" % seat
        if len(s) >= cap: return s[:cap]
        selected = []
        used = len(s)
        for row in Ls:
            ref = re.sub(r"\s+", " ", str(row.get("ref") or "unreferenced"))[:100]
            kind = str(row.get("kind") or "note")[:20]
            label = "UNVERIFIED HYPOTHESIS" if kind == "review" or row.get("status") == "hypothesis" else "recorded observation"
            text = str(row.get("lesson") or "").strip()
            header = "\nLESSONS: [%s; %s; ref=%s] " % (kind, label, ref)
            line = header + text
            if used + len(line) <= cap:
                selected.append([row, text, line]); used += len(line)
            elif not selected:
                room = cap - used - len(header)
                if room > 20:
                    body = text if len(text) <= room else text[:max(0, room//3-2)] + " … " + text[-max(0, room-room//3-1):]
                    line = header + body[:room]
                    selected.append([row, text, line]); used += len(line)
        # Optional review details cannot crowd out a complete selected lesson.
        for item in selected:
            row, text, line = item
            for key, title, limit in (("next_check", "Next check", 160), ("hypothesis", "Hypothesis", 150)):
                value = str(row.get(key) or "").strip()[:limit]
                if not value or value in text or (key == "hypothesis" and "Hypothesis:" in text) or (key == "next_check" and ("Next:" in text or "Next check:" in text)): continue
                extra = " | " + title + ": " + value
                if used + len(extra) <= cap:
                    line += extra; used += len(extra)
            item[2] = line
        s += "".join(item[2] for item in selected)
        try:
            trace = os.environ.get("THF_LESSON_TRACE")
            if trace or not os.environ.get("PYTEST_CURRENT_TEST"):
                from veteran_desk import lesson_trace as LT
                dest = trace or os.path.join(BASE, "state", "thf", "lesson_trace.jsonl")
                refs = [item[0].get("ref") for item in selected]
                if task_id:
                    LT.record_included(seat, refs, "seat_brain.prompt_block", dest, task_id=task_id)
                elif seat in LT.ADMINS:
                    LT.record_admin_available(seat, refs, dest)
        except Exception:
            pass
        for title, values in (("FACTS", F[:8]), ("JOURNAL", [TODAY_RX.sub("", x[2:]) for x in reversed(J)])):
            chosen = []
            for value in values:
                value = re.sub(r"\s+", " ", str(value)).strip()
                trial = "\n" + title + ": " + " | ".join(chosen + [value])
                if len(s) + len(trial) <= cap: chosen.append(value)
            if chosen: s += "\n" + title + ": " + " | ".join(chosen)
        return s + ("\n" if len(s) < cap else "")
    except Exception: return ""

def review_prompt(seat, P):
    F = read_facts(seat); J = read_journal(seat, 25); Ls = read_lessons(seat, 10); rounds = ""
    try: rounds = "".join(open(os.path.join(BASE, "state", "thf", "seat_memory", seat + "_rounds.jsonl")).read().splitlines()[-8:])[-1500:]
    except Exception: pass
    return ("You are %s (seat %s, role %s) on the Taylor Hedge Fund PAPER desk doing your NIGHTLY SELF-REVIEW. Rewrite your standing facts from everything below. "
            "Keep what is still true, drop what is stale, add what you learned today (fills, closes, misses, faults, colleagues' messages). Facts are short, concrete, first person, no jargon. "
            "Never loosen a risk rail (stops, 1-2%% A / 4%% A+ / 5%% cap, B tight/half, C stand aside, PAPER only). Never put a 'today' P/L figure in a fact (it is stale tomorrow; the live figure comes from PER-SEAT MONEY on the 06:00 WITA dashboard clock).\n"
            "PROFILE: %s\nCURRENT FACTS: %s\nJOURNAL (latest): %s\nLESSONS: %s\nLAST ROUNDS: %s\n"
            "Answer ONLY a JSON object: {\"facts\": [<=%d short strings], \"journal\": \"<=60 words: what today taught me\", \"playbook_note\": \"<=40 words: one habit to keep or change tomorrow\"}"
            % (P["name"], seat, P["role"], json.dumps(P, default=str)[:1800], json.dumps(F)[:2200], "\n".join(J)[:2500], json.dumps([x.get("lesson") for x in Ls])[:1800], rounds, MAX_FACTS))
def _jparse(txt):
    m = re.search(r"\{.*\}", txt or "", re.S)
    try: d = json.loads(m.group(0)); return d if isinstance(d, dict) else None
    except Exception: return None
def nightly(seats=None, ai=True, budget_s=3000, timeout_s=300, S=None):
    """Review own records; failed AI review preserves learned standing facts."""
    S = S or _seats(); t0 = time.time(); out = {}
    from veteran_desk import seat_registry as SR
    ids = seats or SR.mail_ids(S)
    lessons_from_book(S=S)
    for seat in ids:
        if seat not in S: continue
        P = ensure(seat, S); mode = "code"; note = ""
        if ai and not os.environ.get("PYTEST_CURRENT_TEST") and time.time() - t0 < budget_s - timeout_s:
            try:
                from veteran_desk import claude_brain as CB
                prompt_block(seat, 1100)
                txt, lab = CB.ask(review_prompt(seat, P), seat=seat, timeout=timeout_s, kind="review"); d = _jparse(txt) or {}
                after_chat(seat, "nightly", txt, lab)
                facts = [str(x) for x in (d.get("facts") or []) if str(x).strip()]
                if len(facts) >= 3:
                    write_facts(seat, facts, lab); mode = lab
                    if d.get("journal"): journal(seat, "nightly", str(d["journal"])[:400])
                    if d.get("playbook_note"): note = str(d["playbook_note"])[:300]; journal(seat, "playbook", note)
                    try:
                        from veteran_desk import brain_hb as HB; HB.mark(seat, True, model=lab, via="review")
                    except Exception: pass
                else: raise ValueError("review JSON had no facts")
            except Exception as x: note = "ai review failed: %s" % str(x)[:120]
        if mode == "code":
            # ensure() seeds missing facts, but an unavailable provider must not
            # replace previously learned standing facts with a generic snapshot.
            snapshot = dict(seat=seat, desk="thf", source="deterministic own-book snapshot",
                            updated_wita=now_wita(), facts=code_facts(seat, S, None, P),
                            ai_review_completed=False, standing_facts_preserved=True)
            _write(os.path.join(_d(seat), "code_snapshot.json"), json.dumps(snapshot, indent=1))
            journal(seat, "nightly_deferred", "AI review incomplete; prior standing facts preserved; deterministic snapshot saved separately" + ((" (" + note + ")") if note else ""))
        out[seat] = dict(mode=mode, note=note[:160], facts=len(read_facts(seat)),
                         ai_review_completed=mode != "code", standing_facts_preserved=mode == "code")
    st = dict(wita=now_wita(), secs=round(time.time() - t0), seats=out); _write(STATUS, json.dumps(st, indent=1))
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        try:
            from veteran_desk import lesson_trace as LT
            LT.publish_authority(os.path.join(BASE, "state", "thf", "learning"),
                                 LT.load(os.path.join(BASE, "state", "thf", "lesson_trace.jsonl")))
        except Exception:
            pass
    return st
def card(seat, S=None, hb=None):
    """seatagents_20261103 (Brett 21:43 point 6 'a seat page showing brain, schedule, last runs, lessons and AI in use'): ONE read-only,
    ELI10 dict per seat for the dashboard Seats tab (the dash owner renders it; this module never touches the dash)."""
    S = S or _seats(); j = S.get(seat) or {}
    if hb is None:
        try: hb = json.load(open(os.path.join(BASE, "state", "thf", "seat_heartbeat.json")))
        except Exception: hb = {}
    h = hb.get(seat) or {}; sc = j.get("schedule") or {}
    from veteran_desk import seat_registry as SR
    when = "every %s min while %s" % (sc.get("every_min"), "/".join(sc.get("sessions") or [])) if sc.get("every_min") else ("inside every trading round" if "rounds" in (sc.get("sessions") or []) else "on chat + mail")
    extra = "; ".join("%s %s" % (k.replace("_wita", "").replace("_", " "), ",".join(v) if isinstance(v, list) else v) for k, v in sc.items() if str(k).endswith("_wita"))
    return dict(seat=seat, name=SR.name(seat, S), role=j.get("role"), reports_to=SR.report_target(seat),
                brain=dict(facts=len(read_facts(seat)), journal_lines=len(read_journal(seat, 10 ** 6)), lessons=len(read_lessons(seat, 10 ** 6)),
                           last_journal=[x[2:][:160] for x in read_journal(seat, 3)], last_lessons=[str(x.get("lesson"))[:160] for x in read_lessons(seat, 2)]),
                schedule=dict(text=(when + ("; " + extra if extra else "")).strip("; "), raw=sc),
                ai=dict(mode=h.get("mode"), model=h.get("model"), last_seen=h.get("last_seen"), via=h.get("via")),
                last_round_utc=h.get("last_round_utc"), paper_only=True)
def cards(S=None):
    S = S or _seats()
    try: hb = json.load(open(os.path.join(BASE, "state", "thf", "seat_heartbeat.json")))
    except Exception: hb = {}
    from veteran_desk import seat_registry as SR
    return [card(s, S, hb) for s in SR.mail_ids(S)]
def status():
    out = {}
    for s in (sorted(os.listdir(DIR)) if os.path.isdir(DIR) else []):
        d = os.path.join(DIR, s)
        if not os.path.isdir(d): continue
        out[s] = dict(facts=len(read_facts(s)), journal_lines=len(read_journal(s, 10 ** 6)), lessons=len(read_lessons(s, 10 ** 6)), updated=dt.datetime.fromtimestamp(os.path.getmtime(os.path.join(d, "facts.md")), WITA).strftime("%F %H:%M WITA") if os.path.exists(os.path.join(d, "facts.md")) else None)
    return out
if __name__ == "__main__":
    import sys
    a = sys.argv[1:]; cmd = a[0] if a else "status"
    if cmd == "seed":
        from veteran_desk import seat_registry as SR; S = SR.load(); [ensure(s, S) for s in SR.mail_ids(S)]; print(json.dumps(lessons_from_book(S=S), indent=1, default=str)); print(json.dumps(status(), indent=1))
    elif cmd == "lessons": print(json.dumps(lessons_from_book(), indent=1, default=str))
    elif cmd == "cards": print(json.dumps(cards(), indent=1, default=str))
    elif cmd == "refacts":   # seatagents_20261103 16:40: re-seed CODE-written facts only (never overwrites facts a nightly AI review wrote)
        from veteran_desk import seat_registry as SR; S = SR.load(); done = []
        for s in SR.mail_ids(S):
            try: hdr = open(os.path.join(_d(s), "facts.md")).readline()
            except Exception: hdr = ""
            if not hdr or " by code" in hdr: P = ensure(s, S); write_facts(s, code_facts(s, S, None, P), "code (dayclock refresh)"); done.append(s)
        print(json.dumps(dict(refreshed=done), indent=1))
    elif cmd == "nightly": print(json.dumps(nightly(a[1:] or None, ai=os.environ.get("SEAT_BRAIN_AI", "1") == "1"), indent=1))
    elif cmd == "show": print(open(os.path.join(_d(a[1]), "facts.md")).read()); print("\n".join(read_journal(a[1], 12))); print(json.dumps(read_lessons(a[1], 5), indent=1))
    else: print(json.dumps(status(), indent=1))
