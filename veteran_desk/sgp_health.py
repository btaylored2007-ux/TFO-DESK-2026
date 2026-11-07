"""pushhealth_20261103 (PAPER; Brett 17:49 WITA 3 Nov, checklist A95: "We also need someone checking on Singapore's Health and fixing as it goes").

SINGAPORE HEALTH WATCHER - one pass every 5 min (tfo-thf-sgp-health.timer, User=tfo). It does not re-check what the existing keepers
already own; it reads their verdicts and watches THEM:
  guardian (state/thf/guardian_status.json, units/book/dash every 90 s), feed keeper (state/thf/feed_status.json, heals feeds),
  deadman (state/vd_engine/deadman_health.json, restarts the engine inside its lease), thf_health.sh (/var/www/tfo-hub/lease/thf_health.json),
  dash overlord (state/vd_engine/dash_overlord.json), dash numbers (tools/dash_numbers_check.py)
and adds what nobody watched: every enabled tfo-* unit, RAM/swap/disk, msgdesk + engine heartbeats, the chat retry backlog
(state/thf/chat_retry.json), the code_hands queue, seat brains (brain_down), Claude/ChatGPT, orphaned claude.exe, stale locks.

AUTO-FIX (only safe, known cases; mode "live"):
  - an ENABLED long-running tfo-* service that is dead (inactive/failed) on two passes in a row -> `systemctl --no-block restart`
    (max once per unit per 30 min); a dead enabled timer -> `systemctl --no-block start`. NEVER tfo-thf-engine (the deadman owns it,
    inside the SGP lease), never the book tick, never a oneshot (those re-run on their own timer).
  - orphaned claude.exe (ppid 1, a seat/brain call whose parent died: --no-session-persistence) seen on two passes -> SIGTERM then SIGKILL.
  - stale locks: a code_hands run marker from a dead process (thf_msgdesk._ch_recover re-queues the job), a mkdir tick lock with a dead pid,
    fixer advisory locks older than 12 h (moved to fixer_locks/stale/, not deleted).
Anything else red for two passes -> ONE code_hands job owned by Simon (software_engineer) via thf_msgdesk.code_hands, deduped, at most
one per fault per hour (and max_jobs_6h overall). It never touches the book, a ledger, an order or a seat's money.
Brett gets ONE short ELI10 chat line only when the state changes (a red appears for two passes / a red is fixed), max 4 an hour.
Writes state/thf/sgp_health.json (the dash Health tab) + state/thf/sgp_health_state.json (memory between passes) + logs/sgp_health.jsonl."""
import datetime as dt, fcntl, json, os, re, shutil, signal, subprocess, sys, time
L = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = os.path.join(L, "state", "thf")
OUT = os.path.join(S, "sgp_health.json"); STATE = os.path.join(S, "sgp_health_state.json"); CFGP = os.path.join(S, "sgp_health_cfg.json")
LOCK = os.path.join(S, ".sgp_health.lock"); LOG = os.path.join(L, "logs", "sgp_health.jsonl")
WITA = dt.timezone(dt.timedelta(hours=8)); TAG = "pushhealth_20261103"
DEFAULT_CFG = {"mode": "dry_run", "post_to_brett": True, "code_hands": True, "max_jobs_6h": 3, "max_posts_h": 4, "restart_gap_min": 30, "job_gap_min": 60,
               "note": "mode dry_run = check + write the Health tab only (says what it WOULD fix); live = also safe auto-fixes, Brett lines on change, Simon's code_hands jobs"}
NEVER_RESTART = {"tfo-thf-engine.service": "the deadman watchdog restarts the engine inside its Singapore lease",
                 "tfo-thf-tick.service": "the book tick - never touched", "tfo-dash-refresh.service": "the dash mirror refresh (oneshot every 15 s)"}
OK_FEED = {"ok", "paused", "backup_idle", "idle", "closed", "disabled", "off", "info"}
P = dict(guardian=os.path.join(S, "guardian_status.json"), feeds=os.path.join(S, "feed_status.json"), deadman=os.path.join(L, "state", "vd_engine", "deadman_health.json"),
         overlord=os.path.join(L, "state", "vd_engine", "dash_overlord.json"), thf_health="/var/www/tfo-hub/lease/thf_health.json", hb=os.path.join(S, "seat_heartbeat.json"),
         msgdesk_hb=os.path.join(S, "msgdesk_heartbeat"), engine_hb=os.path.join(L, "state", "vd_engine", "hb", "engine.json"), retry=os.path.join(S, "chat_retry.json"),
         chq=os.path.join(S, "code_hands_queue.jsonl"), chrun=os.path.join(S, "code_hands_running.json"), cooldown=os.path.join(S, "ai_cooldown.json"),
         gpt=os.path.join(S, "chatgpt_status.json"), claude_log=os.path.join(L, "logs", "claude_brain.jsonl"), fable_cd=os.path.join(S, "fable_cooldown_until"),
         fixer_locks=os.path.join(S, "fixer_locks"), tick_lock=os.path.join(S, ".tick.lock"), numbers=os.path.join(L, "tools", "dash_numbers_check.py"),
         sgp_primary=os.path.join(L, "SGP_PRIMARY"), engine_kill=os.path.join(S, "ENGINE_KILL"))
PY = "/opt/tfo/venv/bin/python" if os.path.exists("/opt/tfo/venv/bin/python") else sys.executable

def now_w(): return dt.datetime.now(WITA)
def _test_live(p): return bool(os.environ.get("PYTEST_CURRENT_TEST")) and os.path.realpath(str(p)).startswith(os.path.realpath("/opt/tfo/live/thf") + os.sep)
def _rj(p, d=None):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return d
def _wj(p, obj):
    if _test_live(p): return False
    os.makedirs(os.path.dirname(p), exist_ok=True); t = "%s.%d.tmp" % (p, os.getpid())
    with open(t, "w") as f: json.dump(obj, f, indent=1, default=str)
    os.replace(t, p); return True
def _log(**r):
    if _test_live(LOG): return
    r.setdefault("wita", now_w().strftime("%Y-%m-%d %H:%M:%S WITA"))
    try:
        with open(LOG, "a") as f: f.write(json.dumps(r, default=str) + "\n")
    except Exception: pass
def _age(p, now=None):
    try: return (now or time.time()) - os.path.getmtime(p)
    except OSError: return None
def _ts(v):
    try:
        if isinstance(v, (int, float)): return float(v)
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception: return None
def mins(s):
    if s is None: return "never"
    s = max(0, int(s))
    return "%ds" % s if s < 90 else "%d min" % (s // 60) if s < 5400 else "%.1f h" % (s / 3600.0)
def row(id_, dot, head, sub="", fix=None, job=True, dup=False, auto=None):
    """One health fact. dot red/amber/green/grey; fix = safe auto-fix spec; job = a lasting red may become a code_hands job;
    dup = the dash Health tab already shows this fact (kept out of the Singapore group, still used for posts/jobs)."""
    return dict(id=id_, dot=dot, head=head, sub=sub, fix=fix, job=job, dup=dup, auto=auto)

# ---------------- probes (each returns rows; all read-only) ----------------
def _systemctl(*a, timeout=20):
    try: return subprocess.run(["systemctl"] + list(a), capture_output=True, text=True, timeout=timeout).stdout
    except Exception: return ""
def unit_table(text=None):
    """{unit: {Type, Restart, UnitFileState, ActiveState, SubState, Result}} for every loaded tfo-* unit."""
    if text is None:
        names = [l.split()[0] for l in _systemctl("list-units", "--all", "--plain", "--no-legend", "tfo-*").splitlines() if l.strip()]
        if not names: return {}
        text = _systemctl("show", *names, "-p", "Id,Type,Restart,UnitFileState,ActiveState,SubState,Result")
    out = {}
    for blk in text.strip().split("\n\n"):
        d = dict(l.split("=", 1) for l in blk.splitlines() if "=" in l)
        if d.get("Id"): out[d["Id"]] = d
    return out
def check_units(U, failed_seen=None, engine_kill=None):
    """Dead enabled services/timers (auto-fix candidates) + failed oneshots (amber; red after 3 passes in a row = a real fault for Simon).
    failed_seen = {unit: passes seen failed} carried in the state (updated in place)."""
    rows = []; dead = []; failed = []; fs = failed_seen if failed_seen is not None else {}
    ek = os.path.exists(P["engine_kill"]) if engine_kill is None else engine_kill
    for u, d in sorted(U.items()):
        en = d.get("UnitFileState") == "enabled"; act = d.get("ActiveState"); typ = d.get("Type"); rs = d.get("Restart") or "no"
        if u.endswith(".timer"):
            if en and act in ("inactive", "failed"): dead.append((u, "start"))
        elif u.endswith(".service"):
            if en and typ != "oneshot" and rs in ("always", "on-failure", "on-abnormal", "on-abort") and act in ("inactive", "failed"): dead.append((u, "restart"))
            elif act == "failed" or (typ == "oneshot" and d.get("Result") not in (None, "", "success")): failed.append(u)   # a long-running unit that restarted once and runs is fine
    for u in list(fs):
        if u not in failed: fs.pop(u, None)
    for u, how in dead:
        never = NEVER_RESTART.get(u); short = u.replace(".service", "").replace(".timer", " timer")
        if u == "tfo-thf-engine.service" and ek:
            rows.append(row("unit:" + u, "amber", "The trading engine is stopped while ENGINE_KILL is on (paused on purpose).", "state/thf/ENGINE_KILL", job=False)); continue
        rows.append(row("unit:" + u, "red", "%s is stopped but should always run." % short, ("not auto-restarted: " + never) if never else "auto-fix: %s it" % how,
                        fix=(None if never else dict(kind="unit", unit=u, how=how)), job=True))
    for u in failed:
        fs[u] = int(fs.get(u) or 0) + 1; n = fs[u]
        rows.append(row("failed:" + u, "red" if n >= 3 else "amber", "%s failed on its last run%s." % (u.replace(".service", ""), " and is still failed after %d checks" % n if n >= 3 else " (it runs again on its own timer)"),
                        "systemctl status %s" % u, job=(n >= 3)))
    if not dead and not failed: rows.append(row("units", "green", "All %d Singapore services and timers that should run are running." % len(U), "", job=False))
    return rows
def check_resources(meminfo=None, disk=None):
    rows = []
    try:
        mi = meminfo if meminfo is not None else dict((l.split(":")[0], int(l.split()[1])) for l in open("/proc/meminfo") if ":" in l)
        av = mi.get("MemAvailable", 0) / 1024.0; tot = mi.get("MemTotal", 1) / 1024.0; st = mi.get("SwapTotal", 0) / 1024.0; sf = mi.get("SwapFree", 0) / 1024.0
        su = (st - sf) / st if st else 0.0
        dot = "red" if av < 500 else "amber" if (av < 1100 or su > 0.85) else "green"
        rows.append(row("ram", dot, ("Memory is almost full: only %.0f MB free of %.1f GB." % (av, tot / 1024)) if dot == "red" else
                        ("Memory is getting tight: %.0f MB free, swap %.0f%% used." % (av, su * 100)) if dot == "amber" else "Memory OK: %.1f GB free of %.1f GB, swap %.0f%% used." % (av / 1024, tot / 1024, su * 100),
                        "free %.0f MB · swap used %.0f of %.0f MB" % (av, st - sf, st), job=(dot == "red")))
    except Exception as x: rows.append(row("ram", "grey", "Could not read memory (%s)." % type(x).__name__, job=False))
    try:
        du = disk if disk is not None else shutil.disk_usage("/"); pct = du.used / float(du.total)
        dot = "red" if pct > 0.92 else "amber" if pct > 0.82 else "green"
        rows.append(row("disk", dot, "Disk %s: %.0f%% used, %.0f GB free." % ("almost full" if dot == "red" else "filling up" if dot == "amber" else "OK", pct * 100, du.free / 1e9), "", job=(dot == "red")))
    except Exception as x: rows.append(row("disk", "grey", "Could not read the disk (%s)." % type(x).__name__, job=False))
    return rows
def check_keepers(now=None):
    """The existing watchdogs: are they alive and what do they say? (their own units are covered by check_units)."""
    now = now or time.time(); rows = []
    g = _rj(P["guardian"]) or {}; ga = _age(P["guardian"], now)
    bad = [c for c in (g.get("checks") or []) if isinstance(c, dict) and not c.get("ok") and c.get("severity") not in ("ok", "info", "warn")]
    if ga is None or ga > 600: rows.append(row("guardian", "red", "The guardian watchdog has not reported for %s." % mins(ga), "state/thf/guardian_status.json", fix=dict(kind="unit", unit="tfo-thf-guardian.service", how="restart")))
    elif bad: rows.append(row("guardian", "amber", "The guardian flags %d problem(s): %s." % (len(bad), "; ".join("%s (%s)" % (c.get("name"), str(c.get("detail"))[:60]) for c in bad[:3])), "it heals what it owns", job=False, dup=True))
    else: rows.append(row("guardian", "green", "The guardian watchdog is happy (checked %s ago)." % mins(ga), "", job=False))
    fa = _age(P["feeds"], now); F = _rj(P["feeds"]) or {}
    if fa is None or fa > 420: rows.append(row("feedkeeper", "red", "The feed keeper has not checked the feeds for %s." % mins(fa), "state/thf/feed_status.json", fix=dict(kind="unit", unit="tfo-thf-feedkeeper.service", how="restart")))
    else:
        # healthdesk_20261103: paid data VENDOR rows (feeds['vendor:*'], veteran_desk/vendor_health.py) are judged on their own below - a vendor
        # quota / outage / Brett login is not a code fault, so it is amber with the vendor's words and never opens a Sam job
        FF = F.get("feeds") or {}; VR = {k: v for k, v in FF.items() if isinstance(v, dict) and v.get("kind") == "vendor"}
        nb = {k: v for k, v in FF.items() if isinstance(v, dict) and k not in VR and str(v.get("status")) not in OK_FEED}
        down = [k for k, v in nb.items() if str(v.get("status")) in ("down", "stale", "error", "dead")]
        heals = [k for k in down if re.search(r"\b(restart|start|fallback \w+ ok)\b", str(nb[k].get("heal") or ""))]   # healthdesk_20261103: 'healing' only when it can
        if down: rows.append(row("feeds", "red", "Data feed(s) down: %s (%s)." % (", ".join(down[:5]), "the feed keeper is healing" if len(heals) == len(down) else ("the feed keeper is healing %s; nothing here can restart the rest, Simon looks" % ", ".join(heals) if heals else "nothing on this server can restart it, Simon looks")), "; ".join("%s: %s" % (k, str(nb[k].get("detail") or nb[k].get("status"))[:60]) for k in down[:3]), dup=True))
        elif nb: rows.append(row("feeds", "amber", "Feed(s) working with a broken part: %s." % ", ".join(list(nb)[:5]), "", job=False, dup=True))
        else: rows.append(row("feeds", "green", "Every data feed is OK (feed keeper, %s ago)." % mins(fa), "", job=False, dup=True))
        vb = {k: v for k, v in VR.items() if str(v.get("status")) in ("down", "stale", "not_configured", "unknown")}
        ext = {k: v for k, v in vb.items() if v.get("vendor_side") or v.get("needs_brett") or v.get("status") == "unknown"}
        ours = {k: v for k, v in vb.items() if k not in ext}
        if ours: rows.append(row("vendors", "red", "Paid data vendor not reaching us: %s." % "; ".join("%s %s (%s)" % (v.get("vendor"), v.get("status"), str(v.get("last_error") or v.get("detail") or "")[:80]) for v in ours.values()), "feeds['vendor:*'] in state/thf/feed_status.json", dup=True))
        elif ext: rows.append(row("vendors", "amber", "Paid data vendor problem outside our code: %s." % "; ".join("%s %s - %s%s" % (v.get("vendor"), v.get("status"), str(v.get("last_error") or v.get("detail") or "")[:90], " (needs Brett)" if v.get("needs_brett") else "") for v in ext.values()), "vendor side: no code fix; shown on the Health tab 'Paid data vendors' row", job=False, dup=True))
        elif VR: rows.append(row("vendors", "green", "Paid data vendors answer: %s." % ", ".join("%s %s" % (v.get("vendor"), v.get("status")) for v in VR.values()), "", job=False, dup=True))
    d = _rj(P["deadman"]) or {}; da = _age(P["deadman"], now)
    if da is None or da > 600: rows.append(row("deadman", "red", "The engine watchdog (deadman) has not run for %s." % mins(da), "", fix=dict(kind="unit", unit="tfo-thf-deadman.timer", how="start")))
    elif d.get("state") not in (None, "OK"): rows.append(row("deadman", "red", "The engine watchdog says: %s." % str(d.get("why") or d.get("state"))[:120], "it restarts the engine itself", dup=True))
    h = _rj(P["thf_health"]) or {}; ha = _age(P["thf_health"], now)
    if ha is not None and ha > 1800: rows.append(row("thf_health", "amber", "The 10-minute desk check (thf_health) has not run for %s." % mins(ha), "", fix=dict(kind="unit", unit="tfo-thf-health.timer", how="start"), job=False))
    elif h.get("status") == "RED": rows.append(row("thf_health", "red", "The 10-minute desk check is RED: %s." % str(h.get("fails"))[:140], "it restarts the dash + tick itself"))
    o = _rj(P["overlord"]) or {}
    if o.get("mismatches"): rows.append(row("overlord", "red", "The dashboard checker found %d number(s) that do not match the book." % len(o["mismatches"]), str(o["mismatches"])[:160], dup=True))
    return rows
def check_numbers(run=None):
    """tools/dash_numbers_check.py: every number shown twice on the dash must agree (exit 0)."""
    try:
        r = run() if run else subprocess.run([PY, P["numbers"]], capture_output=True, text=True, timeout=90, cwd=L)
        rc, out = (r.returncode, r.stdout) if hasattr(r, "returncode") else r
    except Exception as x: return [row("numbers", "grey", "Could not run the dashboard numbers check (%s)." % type(x).__name__, job=False, dup=True)]
    last = (out or "").strip().splitlines()[-1:] or [""]
    if rc == 0: return [row("numbers", "green", "Dashboard numbers agree everywhere.", last[0][:120], job=False, dup=True)]
    bad = [l.strip() for l in (out or "").splitlines() if l.strip().startswith(("BAD", "FAIL", "XX", "!!"))][:3]
    return [row("numbers", "red", "Dashboard numbers do not agree: %s" % (last[0][:140]), " | ".join(bad)[:240], dup=True)]
def check_heartbeats(now=None, hb=None):
    now = now or time.time(); rows = []
    try: ma = now - float(open(P["msgdesk_hb"]).read().strip())
    except Exception: ma = None
    if ma is None or ma > 180: rows.append(row("msgdesk", "red", "The chat desk (msgdesk) stopped checking in %s ago - Brett's messages wait." % mins(ma), "the feed keeper restarts it; Simon is told if it stays down", job=True))
    else: rows.append(row("msgdesk", "green", "The chat desk is running (checked in %s ago)." % mins(ma), "", job=False))
    e = _rj(P["engine_hb"]) or {}; ea = (now - _ts(e.get("utc"))) if _ts(e.get("utc")) else _age(P["engine_hb"], now)
    if (ea is None or ea > 600) and os.path.exists(P["engine_kill"]): rows.append(row("engine", "amber", "The trading engine is paused on purpose (ENGINE_KILL is on) - last check-in %s ago." % mins(ea), "", job=False, dup=True))
    elif ea is None or ea > 600: rows.append(row("engine", "red", "The trading engine has not checked in for %s." % mins(ea), "the deadman restarts it inside the Singapore lease", dup=True))
    else: rows.append(row("engine", "green", "The trading engine is checking in (%s ago)." % mins(ea), "", job=False, dup=True))
    HB = hb if hb is not None else (_rj(P["hb"]) or {})
    down = sorted(s for s, v in HB.items() if isinstance(v, dict) and v.get("mode") == "brain_down")
    if down:
        from veteran_desk import seat_brain as SB
        missing=[seat for seat in down if not os.path.isfile(os.path.join(SB.DIR,seat,"facts.md")) or not os.access(os.path.join(SB.DIR,seat,"facts.md"),os.R_OK)]
        if missing:rows.append(row("brains","red","Persistent seat memory cannot be read: %s."%", ".join(missing),"AI fallback cannot replace missing memory; maintainer review required",job=False,dup=True))
        else:rows.append(row("brains","amber","%d seat(s) using code fallback while AI capacity is unavailable: %s."%(len(down),", ".join(down)),"Persistent seat memories remain available. Paper execution keeps its existing independent risk checks.",job=False,dup=True))
    return rows
def check_chat(now=None):
    now = now or time.time(); rows = []
    R = _rj(P["retry"]) or {}
    if not isinstance(R, dict): R = {}
    # Finished review receipts are durable history, not waiting answers. Old
    # maintenance-blocked work needs outcome review, never automatic replay.
    held = {k: v for k, v in R.items() if isinstance(v, dict) and
            (v.get("maintenance_state") in ("blocked", "uncertain") or v.get("status") in ("expired", "invalid_review"))}
    R = {k: v for k, v in R.items() if isinstance(v, dict) and k not in held and v.get("status") not in ("review_saved", "cancelled")}
    if held:
        rows.append(row("chat_outcome_review", "amber", "%d historical request(s) held for outcome review; no automatic replay." % len(held),
                        "Original instructions and receipts retained; these are separate from the active AI answer queue.", job=False))
    run_old = [k for k, v in R.items() if isinstance(v, dict) and v.get("running") and now - float(v.get("running") or 0) > 1800]
    firsts = []
    for v in R.values():
        try: firsts.append(dt.datetime.strptime(str(v.get("first"))[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=WITA).timestamp())
        except Exception: pass
    oldest = (now - min(firsts)) if firsts else None
    if run_old or (oldest and oldest > 900) or len(R) >= 20:
        rows.append(row("chat_backlog", "amber", "Chat answers are waiting: %d in the retry queue, oldest %s (active requests)." % (len(R), mins(oldest)), "%d answer(s) running over 30 min" % len(run_old), job=False))
    else: rows.append(row("chat_backlog", "green" if len(R) < 8 else "amber", "Chat queue: %d answer(s) waiting for a free AI%s." % (len(R), (", oldest " + mins(oldest)) if oldest else ""), "state/thf/chat_retry.json", job=False))
    q = []
    try: q = [json.loads(l) for l in open(P["chq"]) if l.strip()]
    except Exception: pass
    run = _rj(P["chrun"]) or {}
    from veteran_desk.communication_protocol import engineering_snapshot
    parked = engineering_snapshot(S).get("parked") or []
    if parked:
        rows.append(row("code_hands_parked","amber","%d unresolved engineering job(s) are parked: %s." % (len(parked), ", ".join(parked)),
                        "Active queue count does not include parked work; review receipts before resuming. No automatic replay.",job=False))
    def ready_at(j):
        try: t0 = dt.datetime.strptime(str(j.get("ts_wita"))[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=WITA).timestamp()
        except Exception: t0 = 0.0
        return max(t0, float(j.get("next_try") or 0))
    ready = [j for j in q if float(j.get("next_try") or 0) <= now]
    pause=_rj(os.path.join(S,"code_hands_pause.json")) or {}
    if now<float(pause.get("until_epoch") or 0):
        rows.append(row("code_hands","amber","Simon's code worker is paused for maintenance; %d job(s) retained."%len(q),str(pause.get("reason") or "")[:200],job=False))
        return rows
    if run:
        ra = now - float(run.get("started") or now); pid = run.get("pid")
        alive = bool(pid) and os.path.exists("/proc/%s" % pid)
        if not alive and ra > 1200: rows.append(row("code_hands", "red", "A code_hands run marker is left from a dead process (%s old)." % mins(ra), "auto-fix: the msgdesk's own recovery puts the job back on the queue", fix=dict(kind="chrun"), job=False))
        elif ra > 2700: rows.append(row("code_hands", "amber", "Simon's code_hands run %s has been working for %s." % (run.get("id"), mins(ra)), "", job=False))
        else: rows.append(row("code_hands", "green", "Simon's code_hands is working on %s (%s), %d more queued." % (run.get("id"), mins(ra), len(q)), "", job=False))
    elif ready and now - min(ready_at(j) for j in ready) > 900:
        rows.append(row("code_hands", "red", "%d code_hands job(s) are ready but nobody picked them up for %s." % (len(ready), mins(now - min(ready_at(j) for j in ready))), "the worker lives in tfo-thf-msgdesk", job=False))
    else: rows.append(row("code_hands", "green", "Simon's code_hands queue: %s." % (("%d job(s) queued" % len(q)) if q else "empty"), "", job=False))
    return rows
def check_ais(now=None):
    now = now or time.time(); rows = []
    cd = (_rj(P["cooldown"]) or {}).get("claude") or {}
    recent = []
    try:
        with open(P["claude_log"], "rb") as f:
            f.seek(0, 2); n = f.tell(); f.seek(max(0, n - 400000)); tail = f.read().decode("utf-8", "replace").splitlines()[1:]
        for l in tail:
            try: r = json.loads(l)
            except Exception: continue
            t = _ts(r.get("utc"))
            if t and now - t < 1800: recent.append(r)
    except Exception: pass
    ok = sum(1 for r in recent if r.get("ok"))
    if float(cd.get("until") or 0) > now and str(cd.get("kind")) in ("auth", "quota"):
        rows.append(row("claude", "amber", "Claude is blocked (%s) until %s." % (cd.get("kind"), cd.get("until_wita") or "?"), str(cd.get("why") or "")[:160], job=False))
    elif len(recent) >= 6 and ok == 0: rows.append(row("claude", "red", "Claude answered 0 of %d calls in the last 30 min." % len(recent), "logs/claude_brain.jsonl", job=False))
    else:
        try: fab = float(open(P["fable_cd"]).read().strip()) > now
        except Exception: fab = False
        rows.append(row("claude", "green" if ok or not recent else "amber", "Claude answered %d of %d calls in the last 30 min%s." % (ok, len(recent), " (Fable is out of credits, Opus max answers)" if fab else ""), "", job=False))
    g = _rj(P["gpt"]) or {}; ga = _age(P["gpt"], now)
    if g.get("state") != "logged_in": rows.append(row("chatgpt", "amber", "ChatGPT (the backup brain) is %s - it needs Brett to sign in." % (g.get("state") or "down"), "http://100.120.8.11:18890/vnc.html", job=False))
    elif ga is not None and ga > 3600: rows.append(row("chatgpt", "amber", "ChatGPT has not reported for %s." % mins(ga), "", job=False))
    else: rows.append(row("chatgpt", "green", "ChatGPT backup brain is signed in (%s)." % (g.get("model_pick") or "web"), "", job=False))
    return rows
def find_orphans(proc="/proc"):
    """claude.exe processes whose parent died (ppid 1). kill_ok only for programmatic seat/brain calls (--no-session-persistence)."""
    out = []
    for pid in os.listdir(proc):
        if not pid.isdigit(): continue
        try:
            with open(os.path.join(proc, pid, "stat")) as f: stat = f.read()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
            if ppid != 1: continue
            with open(os.path.join(proc, pid, "cmdline"), "rb") as f: cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except Exception: continue
        if "claude.exe" not in cmd: continue
        out.append(dict(pid=int(pid), cmd=cmd[:160], kill_ok=("--no-session-persistence" in cmd and "--dangerously-skip-permissions" not in cmd)))
    return out
def check_orphans(orphans=None, prev=None):
    orphans = find_orphans() if orphans is None else orphans; prev = set(prev or [])
    if not orphans: return [row("orphans", "green", "No stray Claude processes.", "", job=False)], []
    killable = [o["pid"] for o in orphans if o["kill_ok"] and o["pid"] in prev]
    keep = [o["pid"] for o in orphans if not o["kill_ok"]]
    return [row("orphans", "amber", "%d stray Claude process(es) whose caller died (~200 MB each)." % len(orphans),
                ("auto-fix: stop %s" % ", ".join(map(str, killable)) if killable else "stopped next pass if still stray") + ("; left alone (an agent run): %s" % ", ".join(map(str, keep)) if keep else ""),
                fix=(dict(kind="orphans", pids=killable) if killable else None), job=False)], [o["pid"] for o in orphans]
def check_locks(now=None):
    now = now or time.time(); rows = []; fixes = []
    fl = P["fixer_locks"]; old = []
    if os.path.isdir(fl):
        for f in os.listdir(fl):
            p = os.path.join(fl, f)
            if f.endswith(".lock") and os.path.isfile(p) and now - os.path.getmtime(p) > 12 * 3600: old.append(f)
    tl = P["tick_lock"]
    if os.path.isdir(tl):
        try: pid = int(open(os.path.join(tl, "pid")).read().strip())
        except Exception: pid = None
        if (pid is None or not os.path.exists("/proc/%d" % pid)) and now - os.path.getmtime(tl) > 720: fixes.append(dict(kind="ticklock"))
    if old: fixes.append(dict(kind="fixerlocks", files=sorted(old)))
    if fixes:   # a dead book-tick lock blocks the tick (amber); old fixer advisory locks only confuse the next fixer (grey, tidied)
        tick = any(f["kind"] == "ticklock" for f in fixes)
        rows.append(row("locks", "amber" if tick else "grey", "Stale lock file(s): %s." % ", ".join((["the book tick lock (dead process)"] if tick else []) + (["%d old fixer notes (>12 h)" % len(old)] if old else [])),
                        "auto-fix: clear them", fix=dict(kind="locks", items=fixes), job=False))
    else: rows.append(row("locks", "green", "No stale lock files.", "", job=False))
    return rows

# ---------------- fixes (live only) ----------------
def _sudo_systemctl(*a):
    try:
        r = subprocess.run(["sudo", "-n", "systemctl", "--no-block"] + list(a), capture_output=True, text=True, timeout=60); return r.returncode == 0, (r.stderr or r.stdout).strip()[:160]
    except Exception as x: return False, str(x)[:160]
def do_fix(fix, st, now=None, runner=None, gap_min=30):
    """Apply ONE safe fix. Returns (done, words). runner(kind, fix) lets tests stub the side effects."""
    now = now or time.time(); k = fix.get("kind")
    if k == "unit":
        u = fix["unit"]
        if u in NEVER_RESTART: return False, "never restarted: " + NEVER_RESTART[u]
        last = (st.get("restarts") or {}).get(u)
        if last and now - float(last) < 60 * gap_min: return False, "restarted %s ago - waiting" % mins(now - float(last))
        from veteran_desk import healing_guard as HG
        recovery_now = now if runner is not None else time.time()
        snapshot = HG.probe_unit(u, now=recovery_now)
        state_path = os.path.join(os.path.dirname(STATE), "recovery_guard.json")
        action = fix.get("how") or "restart"
        def submit():
            return runner(k, fix) if runner else _sudo_systemctl(action, u)
        decision = HG.guarded_attempt(u, action, submit, owner="sgp_health",
                                      observation=snapshot, state_path=state_path, now=recovery_now)
        if decision.get("attempt_id"):
            st.setdefault("restarts", {})[u] = recovery_now
        return decision["accepted"], decision["reason"]
    if runner: return runner(k, fix)
    if k == "orphans":
        done = []
        for pid in fix.get("pids") or []:
            try:
                os.kill(pid, signal.SIGTERM); done.append(pid)
            except ProcessLookupError: continue
            except Exception: continue
        time.sleep(3)
        for pid in done:
            try: os.kill(pid, 0); os.kill(pid, signal.SIGKILL)
            except Exception: pass
        return bool(done), "stopped stray claude.exe %s" % ",".join(map(str, done))
    if k == "chrun":
        from veteran_desk import thf_msgdesk as MD
        MD._ch_recover(); return not os.path.exists(P["chrun"]), "code_hands run marker from a dead process -> job re-queued"
    if k == "locks":
        words = []
        for it in fix.get("items") or []:
            if it["kind"] == "ticklock":
                shutil.rmtree(P["tick_lock"], ignore_errors=True); words.append("removed the dead book-tick lock")
            elif it["kind"] == "fixerlocks":
                d = os.path.join(P["fixer_locks"], "stale"); os.makedirs(d, exist_ok=True)
                for f in it.get("files") or []:
                    try: os.replace(os.path.join(P["fixer_locks"], f), os.path.join(d, f))
                    except OSError: pass
                words.append("moved %d old fixer locks to fixer_locks/stale/" % len(it.get("files") or []))
        return bool(words), "; ".join(words)
    return False, "unknown fix"
def queue_job(r, st, cfg, now=None, enqueue=None):
    """ONE code_hands job per fault per hour (owner Simon, software_engineer). Returns the queue answer or None."""
    now = now or time.time(); jobs = st.setdefault("jobs", {}); fid = r["id"]; last = (jobs.get(fid) or {}).get("t")
    if last and now - float(last) < 60 * int(cfg.get("job_gap_min") or 60): return None
    recent = [j for j in jobs.values() if now - float(j.get("t") or 0) < 6 * 3600]
    if len(recent) >= int(cfg.get("max_jobs_6h") or 3): return None
    text = ("SGP HEALTH WATCHER (pushhealth_20261103, PAPER, owner Simon / software_engineer): fault '%s' on droplet tfo-sgp1, red since %s: %s (%s). "
            "Find the cause and fix it safely: back up every file you change, never touch the book/ledger/orders, never restart tfo-thf-engine outside its "
            "lease, add a test when code changes, append one DEPLOY_LOG line. If it is not a code fault (AI capacity, a Brett login, an outside vendor), "
            "change nothing and say so in one line." % (fid, (st.get("red_since") or {}).get(fid, "?"), r.get("head"), r.get("sub")))
    try:
        if enqueue: ans = enqueue(text, fid)
        else:
            from veteran_desk import thf_msgdesk as MD
            MD._ch_ensure = lambda: None   # queue only: the worker thread lives in tfo-thf-msgdesk (never run a hands job inside this short pass)
            ans, _m = MD.code_hands(text, via="sgp_health", root="sgp-health-%s" % re.sub(r"[^a-z0-9]+", "-", fid.lower()).strip("-"), key="sgphealth:" + fid, source_kind="system_health")
    except Exception as x: ans = "queue failed: %s" % str(x)[:120]
    jobs[fid] = dict(t=now, wita=now_w().strftime("%H:%M"), ans=str(ans)[:200]); return ans
def post(text, poster=None):
    if poster: return poster(text)
    from veteran_desk import thf_msgdesk as MD
    root = "sgp-health-%s" % now_w().strftime("%Y%m%d")
    return MD.tadd(root=root, id="sgph-%d" % int(time.time()), **{"from": "software_engineer", "to": "brett"}, text=text, mode="code", kind="health", via="sgp_health", tag=TAG)

# ---------------- the pass ----------------
def load_cfg():
    c = _rj(CFGP)
    if not isinstance(c, dict):
        _wj(CFGP, DEFAULT_CFG); return dict(DEFAULT_CFG)
    return dict(DEFAULT_CFG, **c)
def check_intraday_watch(now=None):
    now=now or time.time();path=os.path.join(L,"data/intraday/health.json")
    data=_rj(path) or {};watch=data.get("watch")
    if not isinstance(watch,dict):return []
    age=_age(path,now)
    if age is not None and age < -120:return []
    if age is None or age>1800:return [row("intraday_watch","amber","Required-symbol intraday coverage report is stale.","The regular THF intraday collector publishes this report.",job=False)]
    missing=watch.get("missing_us") or [];stale=watch.get("stale_us") or []
    if missing or stale:return [row("intraday_watch","amber","THF required US intraday data: %d missing, %d stale."%(len(missing),len(stale)),"missing: %s; stale: %s; research-data warning, no global trade veto"%(", ".join(missing[:12]),", ".join(stale[:12])),job=False)]
    return [row("intraday_watch","green","THF required US intraday rows are present (%s symbols)."%watch.get("required_us"),"Regular session freshness checked per symbol; closed-session ages remain labelled.",job=False)]

def check_paid_research(now=None):
    from veteran_desk import thf_paid_pack as PAID
    now=now or time.time();data=PAID.snapshot()
    try:age=now-float(data.get("built_epoch") or 0)
    except (TypeError,ValueError):age=None
    if age is not None and age < -120:return []
    failed=list(data.get("unavailable") or []);feeds=data.get("feeds") or {}
    fresh=sum(n not in failed and not value.get("stale") for n,value in feeds.items()) if age is not None and age<=600 else 0
    if age is None or age>600 or failed:
        return [row("paid_research","amber","THF common paid research: %s fresh, %s cached/unavailable."%(fresh,max(0,len(PAID.PACK)-fresh)),"snapshot age %s; dated cache remains visible; the independent chat-service collector refreshes every 5 min"%mins(age),job=False)]
    return [row("paid_research","green","THF common paid research is fresh (%d feeds, %s ago)."%(len(feeds),mins(age)),"Refreshed independently of AI capacity; optional research is never a global trade veto.",job=False)]

def check_mac_inputs(now=None, snap=None):
    """macdata_20261106 (F30): the firm-risk snapshot (reports/firm_risk_service.json) carries the ages of its two Mac-produced inputs
    (reports/veteran_desk_book.json, reports/firm_risk.json). Stale (> 60 min) = the Mac is off or its push stopped: say so on the Health tab."""
    snap = _rj(os.path.join(L, "reports", "firm_risk_service.json"), {}) if snap is None else snap
    m = (snap or {}).get("mac_inputs") if isinstance(snap, dict) else None
    if not isinstance(m, dict): return [row("mac_inputs", "grey", "Firm-risk snapshot does not say how old its Mac inputs are yet (published by the 2-min tick).", "reports/firm_risk_service.json mac_inputs", job=False)]
    if m.get("stale_any"):
        return [row("mac_inputs", "amber", "Mac-produced risk inputs are STALE: %s." % str(m.get("note") or "")[:220],
                    "The firm drawdown level (reports/firm_risk.json) and the Veteran Desk open risk come from Brett's Mac; while it is off they are frozen. New THF entries still pass the firm rail on the last value; nothing else changes.", job=False)]
    return [row("mac_inputs", "green", "Mac-produced risk inputs are fresh (Veteran Desk book %s min, firm drawdown file %s min old)." % ((m.get("vd_book") or {}).get("age_min"), (m.get("firm_risk") or {}).get("age_min")), "", job=False)]
def check_claude_budget(now=None, cfg_=None, week=None, cooldown=None):
    """claudeweek_20261106 (G4): how much of Brett's weekly Claude budget the seats have used (state/thf/claude_week.json), the policy
    (brain_cfg claude_seat_policy) and whether Claude is in a quota cooldown (the weekly limit = everything Claude goes dark, incl. the fixer)."""
    cfg_ = cfg_ if cfg_ is not None else (_rj(os.path.join(L, "state", "thf", "brain_cfg.json"), {}) or {})
    week = week if week is not None else (_rj(os.path.join(L, "state", "thf", "claude_week.json"), {}) or {})
    cd = cooldown if cooldown is not None else ((_rj(os.path.join(L, "state", "thf", "ai_cooldown.json"), {}) or {}).get("claude") or {})
    pol = str(cfg_.get("claude_seat_policy") or "fallback"); cap = int(cfg_.get("claude_seat_week_cap") or 150); used = int(week.get("seat_calls") or 0)
    reset = str(cfg_.get("claude_week_reset") or "Fri 15:00")
    if float(cd.get("until") or 0) > (now or time.time()) and str(cd.get("kind")) == "quota":
        return [row("claude_budget", "red" if "weekly" in str(cd.get("why")).lower() else "amber", "Claude is out of credits until %s (%s): the seats run on DeepSeek/ChatGPT; code_hands and the master fixer wait too." % (cd.get("until_wita"), str(cd.get("why"))[:90]), "Weekly limit = Brett's whole Claude plan is dark until the reset (%s WITA); the seat cap exists so this never comes from seat chatter again." % reset, job=False)]
    if pol == "primary":
        return [row("claude_budget", "amber", "Seats are on Claude FIRST (brain_cfg claude_seat_policy primary): %d Claude seat calls this week, no cap - the 3 Nov setting that burned the weekly limit in ~30 h." % used, "Say 'seats back to DeepSeek first' to return to the bounded fallback.", job=False)]
    if pol == "off": return [row("claude_budget", "grey", "Seats never use Claude (claude_seat_policy off); Brett's chat and code_hands still do.", "", job=False)]
    frac = used / cap if cap else 0
    dot = "amber" if frac >= 0.8 else "green"
    return [row("claude_budget", dot, "Claude seat budget this week: %d of %d calls used (seats go DeepSeek/ChatGPT first, Claude as fallback; resets %s WITA)%s." % (used, cap, reset, " - nearly used, seats will stop falling back to Claude" if frac >= 0.8 else ""), "Brett's chat, Marcus/HT answers to Brett and code_hands are never capped.", job=False)]
def collect(now=None, U=None, orphans=None, prev_orphans=None, numbers_run=None, hb=None, meminfo=None, disk=None, failed_seen=None):
    now = now or time.time(); rows = []
    for fn in (lambda: check_units(unit_table() if U is None else U, failed_seen), lambda: check_resources(meminfo, disk), lambda: check_keepers(now), lambda: check_numbers(numbers_run),
               lambda: check_heartbeats(now, hb), lambda: check_intraday_watch(now), lambda: check_paid_research(now), lambda: check_chat(now), lambda: check_ais(now), lambda: check_locks(now), lambda: check_mac_inputs(now), lambda: check_claude_budget(now)):
        try: rows += fn()
        except Exception as x: rows.append(row("probe_err", "grey", "A health probe failed (%s: %s)." % (type(x).__name__, str(x)[:80]), job=False))
    orow, ops = check_orphans(orphans, prev_orphans)
    return rows + orow, ops
FIXWORDS = {"unit": lambda f: "%s %s" % (f.get("how"), f.get("unit")), "orphans": lambda f: "stop stray claude.exe %s" % f.get("pids"), "chrun": lambda f: "re-queue the dead code_hands run", "locks": lambda f: "clear the stale locks"}
def run_pass(now=None, cfg=None, st=None, rows=None, orphan_pids=None, runner=None, enqueue=None, poster=None, write=True):
    """One pass: rows -> safe fixes (live) -> Simon's code_hands job for a lasting red (live) -> one Brett line on a change (live) -> sgp_health.json."""
    now = now or time.time(); cfg = cfg or load_cfg(); st = st if st is not None else (_rj(STATE, {}) or {}); live = cfg.get("mode") == "live"
    if rows is None: rows, orphan_pids = collect(now, prev_orphans=st.get("orphans"), failed_seen=st.setdefault("failed_seen", {}))
    st["orphans"] = list(orphan_pids or [])
    # Verify prior submissions independently; a queued restart was never "fixed".
    if live:
        from veteran_desk import healing_guard as HG
        for unit in list(st.get("restarts") or {}):
            recovery_now = now if runner is not None else time.time()
            snapshot = HG.probe_unit(unit, now=recovery_now)
            HG.observe(unit, snapshot, owner="sgp_health",
                       state_path=os.path.join(os.path.dirname(STATE), "recovery_guard.json"), now=recovery_now)
    prev_bad = set(st.get("bad") or []); prev_red = set(st.get("red") or []); red_since = st.setdefault("red_since", {}); fixes = []; jobs = []
    for r in rows:   # ---- safe fixes: a stopped unit only after 2 passes (its keeper gets the first chance); strays/locks carry their own rule
        f = r.get("fix")
        if not f or r["dot"] not in ("red", "amber", "grey"): continue
        if f.get("kind") == "unit" and r["id"] not in prev_bad: r["auto"] = "auto-fix next pass if still stopped"; continue
        if not live: r["auto"] = "dry run: would " + FIXWORDS.get(f["kind"], lambda f: f["kind"])(f); continue
        try: ok, words = do_fix(f, st, now, runner, int(cfg.get("restart_gap_min") or 30))
        except Exception as x: ok, words = False, "fix error %s" % str(x)[:100]
        r["auto"] = words; fixes.append(dict(id=r["id"], ok=ok, words=words)); _log(event="fix", id=r["id"], ok=ok, words=words)
    reds = {r["id"]: r for r in rows if r["dot"] == "red"}
    for i in reds: red_since.setdefault(i, now_w().strftime("%H:%M"))
    for i in list(red_since):
        if i not in reds: red_since.pop(i, None)
    lasting = {i: r for i, r in reds.items() if i in prev_red}
    for i, r in lasting.items():   # ---- Simon's code_hands job: a lasting red with no safe fix, or a unit that keeps dying after a restart
        u = (r.get("fix") or {}).get("unit")
        if not r.get("job"): continue
        if u and not (u in (st.get("restarts") or {}) and now - float(st["restarts"][u]) > 300): continue
        if not live or not cfg.get("code_hands"): r["auto"] = ((r.get("auto") or "") + " | dry run: would queue one code_hands job for Simon").strip(" |"); continue
        ans = queue_job(r, st, cfg, now, enqueue)
        if ans: jobs.append(dict(id=i, ans=str(ans)[:160])); r["auto"] = ((r.get("auto") or "") + " | Simon (engineer) got a fix job: " + str(ans)[:90]).strip(" |"); _log(event="job", id=i, ans=str(ans)[:200])
        elif (st.get("jobs") or {}).get(i): r["auto"] = ((r.get("auto") or "") + " | Simon has a fix job from %s" % st["jobs"][i].get("wita")).strip(" |")
    announced = set(st.get("announced") or []); new = [r for i, r in lasting.items() if i not in announced]; gone = [i for i in announced if i not in reds]
    heads = dict(st.get("heads") or {}); heads.update({i: r["head"] for i, r in reds.items()}); msg = None
    if new or gone:   # ---- ONE short Brett line per change
        parts = ["\U0001f534 %s %s" % (r["head"], ("-> " + r["auto"]) if r.get("auto") else "-> watching it; Sam gets a fix job if it stays red") for r in new[:3]]
        parts += ["\u2705 Fixed: %s" % heads.get(i, i) for i in gone[:3]]
        msg = "Singapore server check: " + " ".join(parts)
        posts = [t for t in (st.get("posts") or []) if now - t < 3600]
        if not live: msg = "(dry run, not posted) " + msg
        elif not cfg.get("post_to_brett"): msg = "(posting off) " + msg
        elif len(posts) >= int(cfg.get("max_posts_h") or 4): msg = "(hourly post limit, sent later) " + msg
        else:
            try: post(msg[:900], poster); posts.append(now); _log(event="post", text=msg[:300]); announced |= {r["id"] for r in new}; announced -= set(gone)
            except Exception as x: _log(event="post_err", err=str(x)[:120])
        st["posts"] = posts
    st["announced"] = sorted(announced); st["heads"] = {i: h for i, h in heads.items() if i in reds or i in announced}
    st["red"] = sorted(reds); st["bad"] = sorted(r["id"] for r in rows if r["dot"] in ("red", "amber"))
    n_red = len(reds); n_amb = sum(1 for r in rows if r["dot"] == "amber"); color = "red" if n_red else "amber" if n_amb else "green"
    day = now_w().strftime("%Y-%m-%d")
    if fixes: st["fix_log"] = ((st.get("fix_log") or []) + [dict(f, wita=now_w().strftime("%Y-%m-%d %H:%M")) for f in fixes])[-60:]
    out = dict(tag=TAG, paper_only=True, mode=cfg.get("mode"), wita=now_w().strftime("%Y-%m-%d %H:%M:%S WITA"), utc=dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(timespec="seconds"),
               color=color, n_red=n_red, n_amber=n_amb, rows=rows, fixes=fixes, jobs=jobs, message=msg,
               summary=("All clear on the Singapore server." if color == "green" else "%d red, %d amber on the Singapore server." % (n_red, n_amb)),
               fixes_today=[x for x in (st.get("fix_log") or []) if str(x.get("wita", "")).startswith(day)][-12:],
               jobs_today=[dict(id=k, **v) for k, v in (st.get("jobs") or {}).items() if now - float(v.get("t") or 0) < 86400][-6:])
    if write:
        _wj(STATE, st); _wj(OUT, out)
    return out
def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv); cmd = a[0] if a else "run"
    if cmd in ("run", "dry"):
        os.makedirs(S, exist_ok=True); lk = open(LOCK, "a")
        try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError: print(json.dumps(dict(skipped="another sgp_health pass holds the lock"))); return 0
        cfg = load_cfg()
        if cmd == "dry": cfg = dict(cfg, mode="dry_run")
        out = run_pass(cfg=cfg)
        print(json.dumps(dict(color=out["color"], summary=out["summary"], mode=out["mode"], red=[r["head"] for r in out["rows"] if r["dot"] == "red"],
                              amber=[r["head"] for r in out["rows"] if r["dot"] == "amber"], fixes=out["fixes"], jobs=out["jobs"], message=out["message"]), indent=1, default=str))
        return 0
    if cmd == "show": print(json.dumps(_rj(OUT, {}), indent=1, default=str)[:6000]); return 0
    print("usage: python -m veteran_desk.sgp_health run|dry|show"); return 2
if __name__ == "__main__":
    sys.exit(main())
