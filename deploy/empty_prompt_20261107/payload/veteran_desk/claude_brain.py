"""fablefirst_20261103 (Brett P0 12:49 WITA 3 Nov, Ultra plan; A52 21:17 WITA 2 Nov): Claude is the PRIMARY brain for every THF seat.
ask(prompt, seat, timeout, ...) -> (text, label). Order: claude --model fable --effort max -> claude --model opus --effort max -> raise
(the caller then moves to ChatGPT web / SuperGrok web / Grok relay, and only when every AI is down to the code fallback).
Token: CLAUDE_CODE_OAUTH_TOKEN from env or /opt/tfo/secrets.env (value never logged). Prompt goes in via stdin (no argv size limit).
Seat answers run with --tools "" (no file access) in an empty sandbox dir; code_hands / vision callers pass tools=True (+ add_dirs / skip_perms).
Usage-limit / auth replies -> ai_cooldown "claude" until the stated reset time (else 15 min) so every seat skips straight to the next AI.
Live-tunable: state/thf/brain_cfg.json {"claude_timeout_s": 420, "claude_chat_timeout_s": 420, "claude_model": "fable", "claude_fallback": "opus", "claude_effort": "max"}.
Every call is logged to logs/claude_brain.jsonl (seat, model, secs, ok, err; never the prompt). PAPER ONLY."""
import contextlib, fcntl, json, os, re, shutil, signal, subprocess, threading, time
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(BASE, "state", "thf", "brain_cfg.json")
LOG = os.path.join(BASE, "logs", "claude_brain.jsonl")
WS = os.path.expanduser(os.environ.get("THF_CLAUDE_WS", "~/.tfo_claude_ws"))   # empty sandbox: the CLI sees no repo, no ledgers, no CLAUDE.md
LAST = threading.local()   # .model = label of the last successful call in this thread (seat heartbeat reads it)
# brainsafe_20261103: word-bounded so a PID ("231401 Killed"), a seat's own words ("bank-credit", "insufficient volume") or a stack trace can never
# be mistaken for a quota/auth outage (3 Nov 13:59 WITA: an external kill with "401" inside the PID put every seat on a 30-min Claude cooldown).
QUOTA = re.compile(r"hit your .{0,16}limit|usage.?limit|limit reached|rate.?limit|\b429\b|\bquota\b|out of (tokens|credits)|insufficient (credit|quota|balance|funds)|too many requests|exhausted", re.I)
AUTH = re.compile(r"not logged in|please run /login|login required|invalid.{0,20}token|\b401\b|unauthori[sz]ed|(token|session|credential\w*|oauth\w*|login) (has |is )?expired|setup-token|authentication.{0,12}(failed|error)", re.I)
OVERLOAD = re.compile(r"overloaded|\b529\b|\b503\b|unavailable|not found|does not exist|unknown model|invalid model|capacity", re.I)
KILLED = re.compile(r"\brc=(-9|137|-15|143)\b|\bKilled\b|SIGKILL|\bTerminated\b", re.I)   # external kill (OOM killer or someone's pkill): never a cooldown
TOOLXML = re.compile(r"<function_calls>.*?(</function_calls>|$)|<invoke\b.*?(</invoke>|$)|<function_results>.*?(</function_results>|$)|</?(antml:)?(invoke|parameter|function_calls|function_results)\b[^>]*>", re.S | re.I)
NO_TOOLS_NOTE = ("SEAT MODE. You are answering as one seat of an automated PAPER trading desk. In this call you have NO tools at all: no file system, "
    "no shell, no web, no Read/Grep/Glob/Bash. Never write tool calls, XML such as <invoke> or <function_calls>, or 'let me check' narration. "
    "Use only the facts in the prompt; if something is not in the prompt say it is not available. Reply exactly in the format the prompt asks for "
    "(valid JSON when JSON is requested, otherwise plain text) with no preamble.")
def cfg():
    try: return json.load(open(CFG))
    except Exception: return {}
def primary(): return str(cfg().get("claude_model") or os.environ.get("THF_CLAUDE_MODEL", "fable"))
def fallback(): return str(cfg().get("claude_fallback") or os.environ.get("THF_CLAUDE_FALLBACK", "opus"))
def effort(kind=None):
    """Brett HARD: max everywhere. brain_cfg.json may set claude_chat_effort / claude_hands_effort (e.g. "high") to trade depth for speed on chat/hands only."""
    c = cfg(); base = str(c.get("claude_effort") or os.environ.get("THF_CLAUDE_EFFORT", "max"))
    if kind in ("chat", "hands", "review"): return str(c.get("claude_%s_effort" % kind) or base)
    return base
def timeout_s(kind="seat"):
    k = "claude_chat_timeout_s" if kind == "chat" else "claude_timeout_s"
    try: return int(cfg().get(k) or os.environ.get("THF_CLAUDE_TIMEOUT_S", "420"))
    except Exception: return 420
def hands_timeout_s():
    """borrow_20261103: code_hands (fixer) runs need far more than a chat answer - 10 of 14 hands runs 19:00-22:10 WITA failed: 9 were killed at
    the fixed 840s budget (slot wait included, often mid-fix) and 1 never got a slot. brain_cfg claude_hands_timeout_s (default 2700); thf_msgdesk.CH_STALE stays above it."""
    try: return max(600, min(5400, int(cfg().get("claude_hands_timeout_s") or 2700)))
    except Exception: return 2700
def min_run_s():
    try: return max(0, int(cfg().get("claude_min_run_s", 150)))
    except Exception: return 150
def label(model, eff=None): return "claude %s %s" % (model, eff or effort())
def cli():
    c = shutil.which("claude")
    return c or next((x for x in ("/usr/local/bin/claude", "/usr/bin/claude", os.path.expanduser("~/.local/bin/claude")) if os.path.exists(x)), None)
def _env():
    e = dict(os.environ)
    if not e.get("CLAUDE_CODE_OAUTH_TOKEN"):   # self-load the OAuth token like msgdesk._claude (value never logged)
        try:
            for ln in open("/opt/tfo/secrets.env"):
                k, _, v = ln.strip().partition("=")
                if k.replace("export ", "").strip() == "CLAUDE_CODE_OAUTH_TOKEN": e["CLAUDE_CODE_OAUTH_TOKEN"] = v.strip().strip("\"'")
        except Exception: pass
    e.pop("ANTHROPIC_API_KEY", None)   # subscription (OAuth) only, never a metered key
    return e
SLOTS = os.path.join(BASE, "state", "thf", ".claude_slots")   # cross-process cap: 1 lock file per concurrent Claude CLI
def global_cap():
    try: return max(1, min(12, int(cfg().get("claude_global_cap") or os.environ.get("THF_CLAUDE_GLOBAL_CAP", "5"))))
    except Exception: return 5
# capq_20261103 (master fixer 3 Nov 16:15 WITA, v2 16:35): THREE priority tiers over the global_cap() slots.
#   round = trading rounds (llm_backend kind 'seat'): may use every slot, filled from the TOP (slot cap-1 down);
#   high  = Brett's own chat messages, code hands: slots [0, cap - reserve_round());
#   low   = seat-to-seat mail hops, advisor ideas, nightly reviews, Strategy Lab: slots [0, cap - reserve_round() - reserve_hi()).
# So claude_reserve_round (default 4 = desk.MAX_PARALLEL seats per round) slots can NEVER be taken by chat: at 16:30 WITA chat
# held 7 of 8 slots and five round seats timed out to ChatGPT. Brett's chats keep reserve_hi slots that seat chatter can't take.
def reserve_round():
    try: return max(0, min(global_cap() - 1, int(cfg().get("claude_reserve_round", 4))))
    except Exception: return 4
def reserve_hi():
    try: return max(0, min(global_cap() - 1 - reserve_round(), int(cfg().get("claude_reserve_hi", 2))))
    except Exception: return 2
def slot_order(prio):
    cap = global_cap(); n_hi = max(1, cap - reserve_round())
    if prio == "round": return list(range(cap - 1, -1, -1))
    if prio == "high": return list(range(n_hi - 1, -1, -1))
    return list(range(max(1, n_hi - reserve_hi())))
# borrow_20261103 (master fixer 3 Nov 22:20 WITA, Brett 20:24 "THE AI BRIDGES NEED FIXING AND NEED TO BE FASTER ... fix it permanently"):
# the static split above kept chat on 4 of 10 slots even while the 6 round slots sat idle (19:00-22:00 WITA: rounds used 1-4 slots
# ~70% of the time, yet 146 of 254 chat calls failed waiting for a slot and Marcus/HT answered Brett in code). Chat may now BORROW a
# round slot when (a) no round seat is waiting for a slot and (b) at least claude_round_headroom slots stay free after it takes one
# (seat chatter needs 2 more free than Brett/hands). A round seat that finds no free slot drops a wait marker: from then on nothing
# borrows, so every freed borrowed slot goes to the round. Rounds still fill from the top, borrowers from the bottom of the round range.
WAIT_PFX = "round_wait."
def round_headroom():
    try: return max(0, min(global_cap(), int(cfg().get("claude_round_headroom", 3))))
    except Exception: return 3
def borrow_order(prio):
    if prio == "round": return []
    cap = global_cap(); n_hi = max(1, cap - reserve_round())
    return list(range(n_hi, cap))
def _round_waiting(now=None):
    now = now or time.time()
    try: names = [n for n in os.listdir(SLOTS) if n.startswith(WAIT_PFX)]
    except OSError: return False
    for n in names:
        p = os.path.join(SLOTS, n)
        try:
            pid = int(n.split(".")[1])
            if now - os.path.getmtime(p) > 3600 or not os.path.exists("/proc/%d" % pid):
                os.remove(p); continue   # a crashed round left its marker behind
        except (ValueError, IndexError, OSError): continue
        return True
    return False
def free_slots():
    n = 0
    for i in range(global_cap()):
        try: f = open(os.path.join(SLOTS, "slot%d.lock" % i), "a+")
        except OSError: continue
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); fcntl.flock(f, fcntl.LOCK_UN); n += 1
        except OSError: pass
        finally: f.close()
    return n
def can_borrow(prio):
    if prio == "round" or _round_waiting(): return False
    return free_slots() - 1 >= round_headroom() + (2 if prio == "low" else 0)
def _try_slots(idxs):
    for i in idxs:
        f = open(os.path.join(SLOTS, "slot%d.lock" % i), "a+")
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); return f
        except OSError: f.close()
    return None
def _unlock(f):
    try: fcntl.flock(f, fcntl.LOCK_UN); f.close()
    except Exception: pass
LOW_SEATS = ("strategy_lab", "gpt2l", "cfo_report")
# claudeweek_20261106 (master fixer, checklist G4): Claude's WEEKLY limit. Seat work (kind 'seat' = desk rounds, seat-to-seat chatter, reviews) is
# bounded per weekly window; Brett's chat (kind 'chat'), code_hands ('hands') and the master fixer are never counted or blocked here.
WEEK_F = os.path.join(BASE, "state", "thf", "claude_week.json")
def seat_policy(): return str(cfg().get("claude_seat_policy") or "fallback").lower()
def seat_week_cap():
    try: return int(cfg().get("claude_seat_week_cap") or 150)
    except Exception: return 150
def week_start(now=None, reset=None):
    """Epoch of the most recent weekly reset (brain_cfg claude_week_reset, '<Day> HH:MM' WITA; default Fri 15:00 = the reset Brett's plan showed on 6 Nov 2026)."""
    import datetime as _dt
    spec = str(reset or cfg().get("claude_week_reset") or "Fri 15:00").split()
    days = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    wd = days.get(spec[0][:3].lower(), 4); hh, mm = (spec[1].split(":") + ["0"])[:2] if len(spec) > 1 else ("15", "0")
    z = _dt.timezone(_dt.timedelta(hours=8)); t = _dt.datetime.fromtimestamp(now or time.time(), z)
    cand = (t - _dt.timedelta(days=(t.weekday() - wd) % 7)).replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    if cand > t: cand -= _dt.timedelta(days=7)
    return cand.timestamp()
def _week_rw(fn, now=None):
    """Read-modify-write of the weekly counter under a file lock (many seat processes call Claude at once)."""
    os.makedirs(os.path.dirname(WEEK_F), exist_ok=True)
    with open(WEEK_F + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            try: d = json.load(open(WEEK_F))
            except Exception: d = {}
            ws = week_start(now)
            if float(d.get("window_start") or 0) != ws: d = dict(window_start=ws, window_start_wita=time.strftime("%Y-%m-%d %H:%M WITA", time.localtime(ws)), seat_calls=0, by_seat={}, by_kind={})
            r = fn(d)
            tmp = WEEK_F + ".tmp.%d" % os.getpid(); json.dump(d, open(tmp, "w"), indent=1); os.replace(tmp, WEEK_F)
            return r
        finally: fcntl.flock(lk, fcntl.LOCK_UN)
def seat_week_used(now=None): return int(_week_rw(lambda d: d.get("seat_calls", 0), now))
def _week_count(seat, kind, now=None):
    def f(d):
        d["seat_calls"] = int(d.get("seat_calls", 0)) + 1; d.setdefault("by_seat", {})[str(seat)] = int(d.get("by_seat", {}).get(str(seat), 0)) + 1
        d.setdefault("by_kind", {})[str(kind)] = int(d.get("by_kind", {}).get(str(kind), 0)) + 1; d["last_wita"] = time.strftime("%Y-%m-%d %H:%M:%S WITA"); return d["seat_calls"]
    return _week_rw(f, now)
def seat_gate(kind, seat, now=None):
    """Reason string when a seat-kind call must go to the next AI (policy off, or the weekly seat cap is used); None = allowed."""
    if kind != "seat": return None
    pol = seat_policy()
    if pol == "off": return "claude seat policy off (brain_cfg claude_seat_policy) -> next AI"
    if pol == "fallback":
        used = seat_week_used(now); cap = seat_week_cap()
        if used >= cap: return "claude weekly seat budget used (%d/%d since %s; resets %s WITA) -> next AI" % (used, cap, time.strftime("%a %H:%M", time.localtime(week_start(now))), str(cfg().get("claude_week_reset") or "Fri 15:00"))
    return None
def prio_of(kind, seat=None, prio=None):
    if prio in ("round", "high", "low"): return prio
    if kind == "review" or seat in LOW_SEATS: return "low"
    return "round" if kind == "seat" else "high"
FABLE_CD = os.path.join(BASE, "state", "thf", "fable_cooldown_until")   # written by the /usr/local/bin/claude shim on a Fable usage limit
def fable_cooling():
    try: return time.time() < float(open(FABLE_CD).read().strip() or 0)
    except Exception: return False
@contextlib.contextmanager
def _slot(deadline, prio="high"):
    """Wait (not fail) for one of global_cap() slots until deadline. yield True if acquired, False if the wait ran out (caller -> next AI).
    capq_20261103: which slots a call may take depends on its tier - see slot_order(). borrow_20261103: chat may borrow idle round slots."""
    os.makedirs(SLOTS, exist_ok=True); fh = None; marker = None; nb = 0.0
    try:
        while fh is None:
            fh = _try_slots(slot_order(prio))
            if fh is None and prio != "round" and time.time() >= nb:   # borrow_20261103: look at most once a second
                nb = time.time() + 1.0
                if can_borrow(prio):
                    fh = _try_slots(borrow_order(prio))
                    if fh is not None and (_round_waiting() or free_slots() < round_headroom() + (2 if prio == "low" else 0)):
                        _unlock(fh); fh = None   # lost a race with another borrower or a round started: give it back
            if fh is None:
                if prio == "round" and marker is None:   # borrow_20261103: tell chat to stop borrowing until this round seat has a slot
                    try:
                        marker = os.path.join(SLOTS, "%s%d.%d" % (WAIT_PFX, os.getpid(), threading.get_ident())); open(marker, "w").close()
                    except OSError: marker = None
                if time.time() >= deadline: break
                time.sleep(0.3)
    finally:
        if marker:
            try: os.remove(marker)
            except OSError: pass
    if fh is None: yield False; return
    try: yield True
    finally: _unlock(fh)
def _cd():
    from veteran_desk import ai_cooldown as _CD
    return _CD
def cooling(): return _cd().cooling("claude")
def _log(rec):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f: f.write(json.dumps(rec) + "\n")
    except Exception: pass
def classify(err):
    e = str(err or "")
    if KILLED.search(e): return "killed"   # brainsafe_20261103
    if "timeout" in e.lower() and "hard" not in e.lower() and not QUOTA.search(e): return "timeout"
    if QUOTA.search(e): return "quota"
    if AUTH.search(e): return "auth"
    if OVERLOAD.search(e): return "overload"
    return "other"
def clean(out):
    """brainsafe_20261103: drop any tool-call XML a no-tools seat run still emits (3 Nov: healthcare/news_filings chat replies were only <invoke> blocks)."""
    o = TOOLXML.sub("", out or "")
    o = re.sub(r"^[ \t]*</?(antml:)?(invoke|parameter|function_calls|function_results)\b.*$", "", o, flags=re.M)
    return o.strip()
def _killpg(p, grace=3):
    """orphanfix_20261103: kill the shim AND its claude.exe child (one process group via start_new_session), then reap."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try: os.killpg(p.pid, sig)
        except ProcessLookupError: break
        except Exception:
            try: p.kill()
            except Exception: pass
        try: p.wait(timeout=grace); break
        except Exception: continue
    try: p.communicate(timeout=grace)   # close the pipes (the grandchild held the write ends)
    except Exception: pass

def _run_group(cmd, inp, run_to, env, cwd, model="claude"):
    """subprocess.run(cmd, input=inp, capture_output=True, text=True, timeout=run_to) whose timeout kills the WHOLE process group.
    orphanfix_20261103: /usr/local/bin/claude is a bash shim that runs claude.exe as a child; subprocess.run's timeout SIGKILLed
    only the shim, so the real claude.exe kept running headless (3 Nov 15:09 WITA: two 24-min orphans, ~400MB RAM, answers written
    to the dead shim's /tmp files that nobody read, Anthropic concurrency burned). Same return/raise contract as before."""
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=cwd, start_new_session=True)
    try: out, err = p.communicate(inp, timeout=run_to)
    except subprocess.TimeoutExpired:
        _killpg(p)
        raise RuntimeError("claude %s timeout after %ss" % (model, int(run_to)))
    except BaseException:
        _killpg(p); raise
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)

def _run(model, prompt, timeout, tools=False, add_dirs=None, cwd=None, skip_perms=False, eff=None, auto_fallback=None, prio="high", slot_wait_max=None):
    if not isinstance(prompt, str) or not prompt.strip():
        raise RuntimeError("empty prompt rejected before provider call")
    c = cli()
    if not c: raise RuntimeError("claude cli missing")
    cmd = [c, "-p", "--model", model, "--effort", eff or effort(), "--output-format", "text", "--no-session-persistence"]
    if auto_fallback and auto_fallback != model: cmd += ["--fallback-model", auto_fallback]   # CLI-side hop when the primary is overloaded
    if not tools: cmd += ["--tools", "", "--append-system-prompt", NO_TOOLS_NOTE]   # brainsafe_20261103: no tool-call XML in seat answers
    for d in (add_dirs or []): cmd += ["--add-dir", d]
    if skip_perms: cmd += ["--dangerously-skip-permissions"]
    wd = cwd or WS
    os.makedirs(wd, exist_ok=True)
    t_slot = time.time()
    _sw = max(5, timeout - 8) if not slot_wait_max else max(5, min(timeout - 8, slot_wait_max))   # gptshare_20261103: callers may cap the slot wait and spill to ChatGPT
    with _slot(time.time() + _sw, prio) as got:
        if not got: raise RuntimeError("claude global cap wait %ds (cap %d, %s prio) -> next AI" % (int(time.time() - t_slot), global_cap(), prio))
        run_to = max(10, timeout - (time.time() - t_slot)); LAST.wait_s = round(time.time() - t_slot, 1); LAST.borrowed = None   # borrow_20261103: slot wait in the call log
        if time.time() - t_slot > 5 and run_to < min_run_s():   # borrow_20261103: a slot won after a long wait with <150s left only burns the slot (HT 21:16 "opus timeout after 47s")
            raise RuntimeError("claude global cap wait %ds (cap %d, %s prio): only %ds left to run -> next AI" % (int(time.time() - t_slot), global_cap(), prio, int(run_to)))
        r = _run_group(cmd, prompt or "", run_to, _env(), wd, model)   # orphanfix_20261103: timeout kills shim + claude.exe
    raw = (r.stdout or "").strip(); out = clean(raw) if not tools else raw
    if r.returncode != 0 or not out:
        # brainsafe_20261103: classify on rc + stderr; the model's own words (stdout) only count when they are a short error line, never a real answer
        se = (r.stderr or "").strip().replace("\n", " ")[-300:]; so = raw.replace("\n", " ")
        tail = se + ((" " + so[-200:]) if (r.returncode != 0 and len(so) < 400) else "")
        if r.returncode == 0 and not out: tail = (tail + " empty answer" + (" (only tool-call XML)" if raw else "")).strip()
        raise RuntimeError("claude %s rc=%s %s" % (model, r.returncode, tail.strip()))
    return out
def ask(prompt, seat="?", timeout=None, tools=False, add_dirs=None, cwd=None, skip_perms=False, eff=None, models=None, kind="seat", prio=None, slot_wait_max=None):
    """Claude fable max, then opus max. Returns (text, label). Raises RuntimeError when both fail (caller moves to the next AI).
    prio (capq_20261103): 'round' | 'high' | 'low' | None (= round for kind 'seat', low for 'review' / LOW_SEATS, else high) - see slot_order()."""
    if not isinstance(prompt, str) or not prompt.strip():
        err = "empty prompt rejected before provider call"
        _log(dict(utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), seat=seat, model=None, ok=False, secs=0, prompt_chars=0, err=err, kind=kind, prio=prio))
        raise RuntimeError(err)
    prio = prio_of(kind, seat, prio)
    # capq_20261103: while the shim's Fable cooldown is active it silently runs Opus - ask for Opus directly so the heartbeat label is true
    _fc = fable_cooling()
    CD = _cd()
    if CD.cooling("claude"):
        w = (CD._load().get("claude") or {}) if hasattr(CD, "_load") else {}
        raise RuntimeError("claude cooling down until %s (%s)" % (w.get("until_wita"), str(w.get("why"))[:80]))
    _gate = seat_gate(kind, seat)   # claudeweek_20261106 (G4): seat work is bounded per weekly window; chat/hands never
    if _gate: raise RuntimeError(_gate)
    budget = int(timeout or timeout_s(kind)); t0 = time.time(); errs = []
    try:   # rounddl_20261103: a desk round sets THF_ROUND_DEADLINE (epoch) so every call ends before vd_engine's 30-min kill
        _dl = float(os.environ.get("THF_ROUND_DEADLINE") or 0)
    except Exception: _dl = 0
    if _dl:
        budget = min(budget, int(_dl - t0))
        if budget < 60: raise RuntimeError("claude skipped: round deadline in %ds -> next AI" % max(0, int(_dl - t0)))
    queue = list(models or (primary(), fallback())); i = 0; kill_retry = 0
    if _fc and "fable" in queue: queue = [m for m in queue if m != "fable"] or ["opus"]
    while queue:
        m = queue.pop(0); i += 1
        left = budget - (time.time() - t0)
        if left < 20: errs.append("%s: no time left" % m); break
        t1 = time.time()
        if kind == "seat":
            try: _week_count(seat, kind)   # claudeweek_20261106: every attempt costs tokens, so count attempts, not successes
            except Exception: pass
        try:
            _eff = eff or effort(kind)
            out = _run(m, prompt, left, tools=tools, add_dirs=add_dirs, cwd=cwd, skip_perms=skip_perms, eff=_eff, auto_fallback=(queue[0] if queue else None), prio=prio, slot_wait_max=slot_wait_max)
            lab = label(m, _eff); LAST.model = lab; CD.mark_ok("claude")
            _log(dict(utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), seat=seat, model=m, effort=_eff, ok=True, secs=round(time.time() - t1, 1), prompt_chars=len(prompt or ""), chars=len(out), attempt=i, kind=kind, prio=prio, wait_s=getattr(LAST, "wait_s", None)))
            return out, lab
        except Exception as e:
            err = str(e)[:300]; k = classify(err); errs.append("%s: %s" % (m, err[:120]))
            _log(dict(utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), seat=seat, model=m, effort=eff or effort(kind), ok=False, secs=round(time.time() - t1, 1), prompt_chars=len(prompt or ""), err=err[:200], kind_err=k, attempt=i, kind=kind, prio=prio))
            if k in ("quota", "auth"): CD.mark_fail("claude", err, kind=k); break   # same subscription: the fallback model would fail the same way
            if k == "timeout": break   # budget spent; the next AI gets its own budget
            if k == "killed" and kill_retry < 1 and budget - (time.time() - t0) > 90:   # brainsafe_20261103: OOM/pkill is not an outage: pause, retry the same model once, never a cooldown
                kill_retry += 1; time.sleep(8); queue.insert(0, m); continue
            # overload / unknown error / second kill: the fallback model gets the remaining budget
    raise RuntimeError("claude failed: " + "; ".join(errs))
def probe(timeout=120):
    """Cheap health probe (low effort). Returns (ok, detail). Clears the cooldown when it answers."""
    try:
        out = _run(primary(), "Reply with the single word OK", timeout, eff="low")
        _cd().mark_ok("claude"); return True, out[:80]
    except Exception as e:
        err = str(e)[:200]
        if classify(err) in ("quota", "auth"): _cd().mark_fail("claude", err)
        return False, err
if __name__ == "__main__":
    import sys
    if sys.argv[1:] and sys.argv[1] == "probe": ok, d = probe(); print(json.dumps(dict(ok=ok, detail=d, model=primary(), effort=effort())))
    else:
        t, lab = ask(sys.stdin.read(), seat="cli", timeout=int(sys.argv[1]) if sys.argv[1:] else None); print(lab); print(t)
