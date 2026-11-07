"""seatexec_20261103 (D22, helper fixer, PAPER): a seat's own chat decision reaches the book. Roll-call 3 Nov: news_filings said
"Cancel AMZN; I withdraw it" in chat at 16:26 WITA and VET-AMZN-20261102-2117-news_filings stayed ARMED, because chat answers had no way
to act. Brett A8 (2 Nov 20:20 WITA): every trader seat may arm, cancel, close and half-bank ITS OWN names directly via the guarded path,
no Brett tick; Marcus and the Head Trader oversee (veto/cancel/adjust).
A trader seat's chat JSON may carry "actions" (max 3), for its OWN open/armed names only:
  {"op":"withdraw","id":..,"reason":..}             armed (or still queued) order -> cancelled   = seat_cli withdraw
  {"op":"close","id":..,"reason":..}                open trade -> closed at the live mark         = seat_cli close
  {"op":"halfbank","id":..,"reason":..}             open trade -> 50% banked                      = seat_cli close --pct 50
  {"op":"amend","id":..,"stop":<float>,"reason":..} stop TIGHTER only (never wider)               = seat_cli amend --stop
"ticker" may stand in for "id" when the seat holds exactly one open/armed row in it. Never arms (arming stays in rounds + gates),
never another seat's names, PAPER book only. Every action runs veteran_desk.seat_cli in a subprocess with THF_DESK=1 (all rails stay
inside seat_cli/book), is audited to state/thf/seat_cli_audit.jsonl (cmd=chat_action, actor=<seat>) and is deduped per message, so a
re-asked answer can never half-bank twice. Head (Marcus/HT) chat actions are audited here too (cmd=chat_head_action)."""
import datetime as dt, json, os, re, subprocess, sys
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = os.path.join(BASE, "state", "thf"); BOOK = os.path.join(S, "thf_book.json"); AUDIT = os.path.join(S, "seat_cli_audit.jsonl")
_LIVE_AUDIT = AUDIT
PY = "/opt/tfo/venv/bin/python" if os.path.exists("/opt/tfo/venv/bin/python") else sys.executable
OPS = ("withdraw", "close", "halfbank", "amend"); MAX_ACTS = 3; HALFBANK_GAP_MIN = 30; TIMEOUT_S = 180
ALIASES = {"cancel": "withdraw", "pull": "withdraw", "unarm": "withdraw", "half_bank": "halfbank", "half-bank": "halfbank", "bank_half": "halfbank",
           "tighten": "amend", "tighten_stop": "amend", "move_stop": "amend", "exit": "close", "flatten": "close"}
ARM_OPS = ("arm", "propose", "buy", "sell", "open", "add", "long", "short", "enter", "rearm", "re-arm")
ID_RX = re.compile(r"^[\w.:-]{1,80}$")
_FALLBACK_TRADERS = ["banks", "crypto", "energy", "essentials_utilities", "fx_commodities", "healthcare", "macro", "news_filings", "price_action", "tech_ai_infra", "trend"]


def _now(): return dt.datetime.now(dt.timezone.utc)


def _f(x):
    try: return float(x)
    except Exception: return None


def traders():
    try:
        from veteran_desk import seat_registry as SR
        t = list(SR.trade_ids() or [])
        if t: return t
    except Exception: pass
    return list(_FALLBACK_TRADERS)


def _book(path=None):
    try: return json.load(open(path or BOOK))
    except Exception: return {}


def audit(**k):
    """One JSON line in the THF seat_cli audit. Under pytest the live file is never written (tests point AUDIT at a temp file)."""
    if os.environ.get("PYTEST_CURRENT_TEST") and os.path.abspath(AUDIT) == os.path.abspath(_LIVE_AUDIT): return
    try:
        with open(AUDIT, "a") as fh: fh.write(json.dumps(dict(utc=_now().isoformat(), **k), default=str) + "\n")
    except Exception: pass


def done_before(msg_id, actor, op, pid, cmd="chat_action"):
    """True when this message already executed this exact action (ok) - a capq re-ask or a restart never repeats it."""
    if not msg_id or not pid: return False
    try:
        with open(AUDIT) as fh:
            for ln in fh:
                if cmd not in ln or msg_id not in ln: continue
                try: r = json.loads(ln)
                except Exception: continue
                if r.get("cmd") == cmd and r.get("msg") == msg_id and r.get("actor") == actor and r.get("op") == op and r.get("id") == pid and (r.get("result") or {}).get("ok"): return True
    except Exception: pass
    return False


def _clean(x, n=240):
    return re.sub(r"\s+", " ", str(x or "").replace("--", "-")).strip()[:n]


def _resolve(seat, a, book):
    """-> (pid, position-or-None, error). Own book only; another seat's id is refused by name."""
    SEATS = (book.get("seats") or {}); P = ((SEATS.get(seat) or {}).get("positions") or {})
    pid = str(a.get("id") or a.get("position_id") or "").strip(); tk = str(a.get("ticker") or a.get("sym") or "").strip().upper()
    if pid:
        if not ID_RX.match(pid): return None, None, "bad id"
        if pid in P:
            p = P[pid]
            if str(p.get("seat") or seat) != seat: return None, None, "%s is not your position" % pid
            return pid, p, None
        for s, B in SEATS.items():
            if s != seat and pid in ((B or {}).get("positions") or {}): return None, None, "%s belongs to seat %s - you act on your OWN names only (Marcus/HT oversee)" % (pid, s)
        if pid.endswith("-" + seat): return pid, None, None   # proposal still queued (not armed in the book yet): seat_cli withdraw checks the queue
        return None, None, "no open/armed %s in your book" % pid
    if tk:
        m = [(k, p) for k, p in P.items() if isinstance(p, dict) and str(p.get("ticker") or "").upper() == tk and str(p.get("status") or "").startswith(("open", "armed"))]
        if len(m) == 1: return m[0][0], m[0][1], None
        return None, None, ("%d open/armed %s rows - give the id" % (len(m), tk)) if m else ("no open/armed %s in your book" % tk)
    return None, None, "action needs the position id (or ticker)"


def _recent_partial(p, pid, book, seat, now=None):
    """HH:MM WITA of a partial close of this position in the last HALFBANK_GAP_MIN minutes, else None."""
    now = now or _now(); hits = []
    for ev in (p.get("rail_log") if isinstance(p.get("rail_log"), list) else []):   # seat_partial_close, half_bank, quarter_bank, partial_cut, dex_halfcut
        if isinstance(ev, dict) and any(w in str(ev.get("ev") or "") for w in ("partial", "half", "quarter")): hits.append(ev.get("utc"))
    for c in (((book.get("seats") or {}).get(seat) or {}).get("closed") or []):
        if isinstance(c, dict) and str(c.get("id") or "").startswith(pid + "-P"): hits.append(c.get("closed_utc"))
    for u in hits:
        try:
            t = dt.datetime.fromisoformat(str(u).replace("Z", "+00:00"))
            if (now - t).total_seconds() < HALFBANK_GAP_MIN * 60: return t.astimezone(dt.timezone(dt.timedelta(hours=8))).strftime("%H:%M WITA")
        except Exception: continue
    return None


def run_cli(cmd):
    """seat_cli in a subprocess with THF_DESK=1 (the THF book, never the old VD book). Returns its JSON result."""
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("SEATEXEC_ALLOW_LIVE"): return dict(ok=False, error="pytest: live seat_cli disabled")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S, cwd=BASE, env=dict(os.environ, THF_DESK="1"))
        for ln in reversed((r.stdout or "").strip().splitlines()):
            ln = ln.strip()
            if ln.startswith("{"):
                try: return json.loads(ln)
                except Exception: continue
        return dict(ok=False, error="seat_cli gave no JSON (rc %s): %s" % (r.returncode, _clean((r.stderr or r.stdout or "")[-200:], 200)))
    except subprocess.TimeoutExpired: return dict(ok=False, error="seat_cli timeout %ss (book tick lock?) - nothing confirmed" % TIMEOUT_S)
    except Exception as x: return dict(ok=False, error="%s: %s" % (type(x).__name__, _clean(x, 150)))


def act(seat, a, msg_id=None, root=None, runner=None, book=None, now=None):
    """Execute ONE chat action for SEAT's own name. Returns dict(ok, op, seat, id, ticker, result|error|skipped)."""
    if not isinstance(a, dict): return dict(ok=False, op="?", seat=seat, error="bad action (not an object)")
    op = str(a.get("op") or a.get("action") or "").strip().lower(); op = ALIASES.get(op, op); base = dict(op=op, seat=seat)
    if seat not in traders(): return dict(base, ok=False, error="seat %s has no trading book - trader seats act on their own names only" % seat)
    base.update(id=str(a.get("id") or "") or None, ticker=a.get("ticker"))   # what was asked, so a refusal names it
    if op in ARM_OPS: return dict(base, ok=False, error="arming never happens from chat - propose in your round (gate + rails)")
    if op not in OPS: return dict(base, ok=False, error="op %r not allowed (withdraw|close|halfbank|amend)" % op)
    book = book if book is not None else _book()
    if book.get("paper_only") is not True: return dict(base, ok=False, error="book is not flagged paper_only - refused (PAPER only)")
    pid, p, err = _resolve(seat, a, book)
    if err: return dict(base, ok=False, error=err)
    base.update(id=pid, ticker=(p or {}).get("ticker"))
    st = str((p or {}).get("status") or "queued")
    if op == "withdraw" and p is not None and not st.startswith("armed"): return dict(base, ok=False, error="%s is %s - withdraw is for armed orders (close/halfbank an open trade)" % (pid, st))
    if op in ("close", "halfbank", "amend") and p is None: return dict(base, ok=False, error="%s is not in your book" % pid)
    if op in ("close", "halfbank") and not st.startswith("open"): return dict(base, ok=False, error="%s is %s - %s needs an OPEN trade (withdraw an armed order)" % (pid, st, op))
    new = None
    if op == "amend":
        new = _f(a.get("stop")); old = _f(p.get("stop")); longp = str(p.get("side") or "").upper() != "SHORT"
        if new is None: return dict(base, ok=False, error="amend needs a numeric stop (stop only - targets stay with the round/Marcus)")
        if old is not None and ((longp and new <= old) or (not longp and new >= old)): return dict(base, ok=False, error="stop may only TIGHTEN: %g -> %g would widen or keep the risk" % (old, new))
    if op == "halfbank":
        rp = _recent_partial(p, pid, book, seat, now)
        if rp: return dict(base, ok=False, error="already part-banked at %s - one half-bank per %d min" % (rp, HALFBANK_GAP_MIN))
    if done_before(msg_id, seat, op, pid): return dict(base, ok=True, skipped="already done for this message")
    reason = _clean(a.get("reason")) or "seat decision in chat"
    why = "chat: %s own decision%s: %s" % (seat, (" (msg %s)" % msg_id) if msg_id else "", reason)
    cmd = [PY, "-m", "veteran_desk.seat_cli"]
    if op == "withdraw": cmd += ["withdraw", seat, pid, why]
    elif op == "close": cmd += ["close", seat, pid, why]
    elif op == "halfbank": cmd += ["close", seat, pid, why, "--pct", "50"]
    else: cmd += ["amend", seat, pid, "--stop", repr(new), why]
    out = (runner or run_cli)(cmd)
    out = out if isinstance(out, dict) else dict(ok=False, error="bad seat_cli result")
    res = dict(base, ok=bool(out.get("ok")), result=out)
    if not res["ok"]: res["error"] = _clean(out.get("error") or "seat_cli refused", 200)
    audit(cmd="chat_action", actor=seat, op=op, seat=seat, id=pid, ticker=base.get("ticker"), reason=reason, msg=msg_id, root=root, via="thf_msgdesk", seat_cli=cmd[3:5], result=out)
    return res


def run(seat, actions, msg_id=None, root=None, runner=None):
    """All actions of one chat reply (max MAX_ACTS). Each one re-reads the book, so two actions see each other's effect."""
    acts = actions if isinstance(actions, list) else ([actions] if isinstance(actions, dict) else [])
    out = [act(seat, a, msg_id, root, runner) for a in acts[:MAX_ACTS]]
    if len(acts) > MAX_ACTS: out.append(dict(ok=False, op="-", seat=seat, error="max %d actions per reply - %d ignored" % (MAX_ACTS, len(acts) - MAX_ACTS)))
    return out


def _brief(r):
    o = r.get("result") or {}
    if r.get("op") == "withdraw": return str(o.get("queue") or "cancelled")
    if r.get("op") == "close": return "closed at %s, net A$%s" % (o.get("exit"), o.get("net_aud"))
    if r.get("op") == "halfbank": return "banked %s sh at %s, %s left, net A$%s" % (o.get("closed_shares"), o.get("exit"), o.get("residual_shares"), o.get("net_aud"))
    if r.get("op") == "amend": return "stop %s -> %s" % ((o.get("old") or {}).get("stop"), o.get("stop"))
    return "ok"


def fmt(res, who="My book actions"):
    """One line for the chat reply: what was done / refused and why."""
    if not res: return ""
    return "\n%s (seat_cli, PAPER, audited): " % who + "; ".join("%s %s%s -> %s" % (r.get("op"), (str(r.get("ticker")) + " ") if r.get("ticker") else "", r.get("id") or "",
        ("DONE (%s)" % _brief(r)) if r.get("ok") and not r.get("skipped") else ("already done" if r.get("skipped") else "REFUSED (%s)" % str(r.get("error"))[:160])) for r in res)


if __name__ == "__main__":   # python -m veteran_desk.seat_actions SEAT '<actions json>' [MSG_ID]  - fixer/master use: run a seat's stated decision through the same guarded path
    a = sys.argv[1:]
    if len(a) < 2: print(__doc__); sys.exit(2)
    r = run(a[0], json.loads(a[1]), msg_id=(a[2] if len(a) > 2 else None), root=(a[3] if len(a) > 3 else None))
    print(json.dumps(r, default=str)); sys.exit(0 if all(x.get("ok") for x in r) else 1)
