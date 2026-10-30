#!/usr/bin/env python3
"""Universe-rescan hang watchdog - PAPER ONLY (TFO fix (c), approved 2026-10-29 11:41 WITA).

Every run (launchd com.tfo.rescan-watchdog, 60s): finds universe rescan steps (universe_asia.py <mkt>, universe_asx.py,
ranker run_scan.py). If one has used no extra CPU for >= STALL_S (10 min) it is HUNG:
  1. alert -> logs/rescan_watchdog.log + static/rescan_watchdog_health.json (dashboard-readable; not a War Room file)
  2. safe restart: only if no hunt engine for that market is mid-tick, SIGTERM (then SIGKILL) the hung rescan step ONLY.
     Its parent chain (intraday_rescan / preopen_<mkt>.sh) then carries on and removes its own lock as normal.
  3. orphan lock: state/hunt48_<mkt>/preopen.lock or state/intraday_rescan/lock_<mkt> with no live owning chain process
     and older than STALL_S is removed (only those rescan locks). Never touches ledgers, engines, or any other file.
--dry-run: report what would be done, change nothing.  Env (tests): WD_BASE, WD_STALL_S, WD_ONLY (substring filter on command).
"""
import datetime as dt, json, os, re, signal, subprocess, sys, time
BASE = os.environ.get("WD_BASE") or os.path.dirname(os.path.abspath(__file__))
STALL_S = int(os.environ.get("WD_STALL_S", "600")); ONLY = os.environ.get("WD_ONLY")
SD = os.path.join(BASE, "state", "rescan_watchdog"); STATEF = os.path.join(SD, "state.json")
HEALTH = os.path.join(BASE, "static", "rescan_watchdog_health.json"); LOGF = os.path.join(BASE, "logs", "rescan_watchdog.log")
WITA = dt.timezone(dt.timedelta(hours=8)); DRY = "--dry-run" in sys.argv
RESCAN = [(re.compile(r"universe_asia\.py\s+(\w+)"), None), (re.compile(r"universe_asx\.py"), "asx"), (re.compile(r"ranker/run_scan\.py"), "us")]
ENGINE = {"asx": r"asx_hunt48\.py", "us": r"hunt48_short_paper\.py"}
def wita(): return dt.datetime.now(WITA).strftime("%Y-%m-%d %H:%M:%S WITA")
def log(msg):
    os.makedirs(os.path.dirname(LOGF), exist_ok=True)
    with open(LOGF, "a") as f: f.write(f"{wita()} {'[DRY] ' if DRY else ''}{msg}\n")
def atomic(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True); t = p + ".tmp"; json.dump(o, open(t, "w"), indent=1); os.replace(t, p)
def cpu_s(s):   # ps TIME: [[dd-]hh:]mm:ss.xx
    d = 0
    if "-" in s: d, s = s.split("-", 1); d = int(d)
    p = [float(x) for x in s.split(":")]; v = 0.0
    for x in p: v = v * 60 + x
    return d * 86400 + v
def procs():
    out = subprocess.run(["ps", "-axo", "pid=,ppid=,time=,command="], capture_output=True, text=True).stdout.splitlines(); P = []
    for ln in out:
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(\S+)\s+(.*)", ln)
        if m: P.append(dict(pid=int(m[1]), ppid=int(m[2]), cpu=cpu_s(m[3]), cmd=m[4]))
    return P
def rescan_mkt(cmd):
    if "rescan_watchdog" in cmd: return None
    if ONLY and ONLY not in cmd: return None
    for rx, mk in RESCAN:
        m = rx.search(cmd)
        if m: return mk or m.group(1)
    return None
def kill(pid):
    try: os.kill(pid, signal.SIGTERM)
    except ProcessLookupError: return "gone"
    for _ in range(20):
        time.sleep(0.5)
        try: os.kill(pid, 0)
        except ProcessLookupError: return "SIGTERM"
    try: os.kill(pid, signal.SIGKILL); return "SIGKILL"
    except ProcessLookupError: return "SIGTERM"
def main():
    now = time.time(); P = procs(); prev = {}
    try: prev = json.load(open(STATEF)).get("seen", {})
    except Exception: pass
    seen, alarms, actions = {}, [], []
    for p in P:
        mk = rescan_mkt(p["cmd"])
        if not mk: continue
        k = str(p["pid"]); o = prev.get(k)
        if o and o.get("cmd") == p["cmd"] and abs(o["cpu"] - p["cpu"]) < 0.05: since = o["since"]       # no CPU progress
        else: since = now
        seen[k] = dict(cmd=p["cmd"], mkt=mk, cpu=p["cpu"], since=since)
        stall = now - since
        if stall < STALL_S: continue
        a = dict(pid=p["pid"], mkt=mk, cmd=p["cmd"][:160], stalled_s=round(stall), cpu_s=p["cpu"]); alarms.append(a)
        log(f"ALARM hung rescan pid={p['pid']} mkt={mk} no CPU progress {round(stall)}s: {p['cmd'][:160]}")
        eng = ENGINE.get(mk, r"asia_hunt48\.py\s+" + re.escape(mk) + r"\b")
        busy = [q["pid"] for q in P if re.search(eng, q["cmd"]) and "rescan_watchdog" not in q["cmd"]]
        if busy: actions.append(dict(pid=p["pid"], action="deferred", why=f"engine mid-tick pids {busy}")); log(f"DEFER kill pid={p['pid']}: engine mid-tick {busy}"); continue
        r = "would kill" if DRY else kill(p["pid"])
        actions.append(dict(pid=p["pid"], mkt=mk, action=r)); log(f"RESTART pid={p['pid']} mkt={mk}: {r} (parent chain continues; next scheduled run goes ahead)")
        if not DRY: seen.pop(k, None)
    # orphan rescan locks (no live chain process for that market), only after STALL_S
    owners = lambda mk: [q["pid"] for q in P if (f"intraday_rescan.py run {mk}" in q["cmd"] or f"preopen_{mk}.sh" in q["cmd"] or rescan_mkt(q["cmd"]) == mk
                                                 or ("hunt_orchestrator" in q["cmd"] and mk in q["cmd"].split()) or f"hunt48_{mk}/" in q["cmd"]
                                                 or re.search(ENGINE.get(mk, r"asia_hunt48\.py\s+" + re.escape(mk) + r"\b"), q["cmd"]))]
    locks = [(os.path.join(BASE, "state", d, "preopen.lock"), d[len("hunt48_"):]) for d in (os.listdir(os.path.join(BASE, "state")) if os.path.isdir(os.path.join(BASE, "state")) else []) if d.startswith("hunt48_")]
    ird = os.path.join(BASE, "state", "intraday_rescan")
    locks += [(os.path.join(ird, f), f[5:]) for f in (os.listdir(ird) if os.path.isdir(ird) else []) if f.startswith("lock_")]
    for lk, mk in locks:
        if not os.path.exists(lk) or now - os.path.getmtime(lk) < STALL_S or owners(mk): continue
        alarms.append(dict(lock=lk, mkt=mk, age_s=round(now - os.path.getmtime(lk)), why="orphan rescan lock (no live chain)"))
        log(f"ALARM orphan lock {lk} age {round(now - os.path.getmtime(lk))}s, no live chain for {mk}")
        if not DRY:
            try: os.remove(lk); actions.append(dict(lock=lk, action="removed")); log(f"REMOVED orphan lock {lk}")
            except Exception as e: actions.append(dict(lock=lk, action=f"remove failed {e}"))
        else: actions.append(dict(lock=lk, action="would remove"))
    if not DRY: atomic(STATEF, dict(seen=seen, updated=wita()))
    h = dict(book="rescan_watchdog", paper_only=True, ok=not alarms, alarm=bool(alarms), alarms=alarms, actions=actions, dry_run=DRY,
             watching=[dict(pid=int(k), mkt=v["mkt"], no_progress_s=round(now - v["since"])) for k, v in seen.items()], stall_s=STALL_S, updated_wita=wita())
    try:   # keep last alarm visible for 1h after it clears
        old = json.load(open(HEALTH))
        if alarms: h["last_alarm_wita"] = wita()
        elif old.get("last_alarm_wita"): h["last_alarm_wita"] = old["last_alarm_wita"]
    except Exception:
        if alarms: h["last_alarm_wita"] = wita()
    if not DRY: atomic(HEALTH, h)
    print(json.dumps(h, indent=1))
if __name__ == "__main__": main()
