#!/usr/bin/env python3
"""Watchdog for the Claude bridge. PAPER ONLY.

Health-checks every 60s. Restarts the bridge when the process is gone OR the
process is alive but hung (stale heartbeat, or a CLI call stuck past the hang
threshold). A login-expiry alert does not restart: that is a status flag, not a hang.
Launchd KeepAlive restarts this watchdog if it dies.

  python3 tools/claude_bridge_watchdog.py            # loop
  python3 tools/claude_bridge_watchdog.py --once     # single check
"""
import json, os, signal, subprocess, sys, time, urllib.request
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LABEL = "com.tfo.claude-bridge"
HANG_S = float(os.environ.get("TFO_CLAUDE_HANG_S", "90"))
STALE_S = float(os.environ.get("TFO_CLAUDE_HEARTBEAT_STALE_S", "45"))
INTERVAL = float(os.environ.get("TFO_CLAUDE_WATCHDOG_S", "60"))
def bridge_dir():
    return os.environ.get("TFO_CLAUDE_BRIDGE_DIR") or os.path.join(BASE, "state", "claude_bridge")
def _port(directory):
    env = os.environ.get("TFO_CLAUDE_BRIDGE_PORT")
    if env and env != "0":
        try: return int(env)
        except ValueError: pass
    try: return int(open(os.path.join(directory, "port")).read().strip())
    except Exception: return 8766
def _parse_utc(s):
    if not s: return None
    try:
        import datetime as dt
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if t.tzinfo is None: t = t.replace(tzinfo=dt.timezone.utc)
        return t.timestamp()
    except Exception: return None
def health(directory=None, timeout=5.0):
    directory = directory or bridge_dir()
    url = f"http://127.0.0.1:{_port(directory)}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = json.loads(r.read().decode() or "{}")
            return r.status == 200 and bool(body.get("up", True)), body
    except Exception as e:
        return False, {"error": type(e).__name__}
def read_status(directory=None):
    directory = directory or bridge_dir()
    try: return json.load(open(os.path.join(directory, "status.json")))
    except Exception: return None
def needs_restart(health_up, status, now=None, hang_s=None, stale_s=None, rss_limit=None):
    """True when the bridge is dead or hung. Login expired alone is not a restart."""
    hang_s = HANG_S if hang_s is None else hang_s
    stale_s = STALE_S if stale_s is None else stale_s
    now = time.time() if now is None else now
    if not health_up: return True, "health_down"
    if not isinstance(status, dict): return True, "status_missing"
    ts = _parse_utc(status.get("utc"))
    if ts is None or now - ts > stale_s: return True, "stale_heartbeat"
    try: oldest = float(status.get("inflight_oldest_s") or 0)
    except (TypeError, ValueError): oldest = 0
    if oldest > hang_s: return True, "hung_inflight"
    if rss_limit:
        try:
            if status.get("rss_mb") is not None and float(status["rss_mb"]) > float(rss_limit):
                return True, "memory"
        except (TypeError, ValueError): pass
    return False, "ok"
def _pid(directory):
    try: return int(open(os.path.join(directory, "bridge.pid")).read().strip())
    except Exception: return None
def _alive(pid):
    if not pid: return False
    try: os.kill(pid, 0); return True
    except OSError: return False
def restart(directory=None, spawn=None):
    """SIGKILL a hung pid so launchd KeepAlive respawns it. kickstart -k when launchctl exists.
    Tests set TFO_CLAUDE_WATCHDOG_SPAWN=1 to start the daemon directly (no launchd in CI)."""
    directory = directory or bridge_dir()
    pid = _pid(directory)
    if _alive(pid):
        try: os.kill(pid, signal.SIGKILL)
        except OSError: pass
    uid = os.getuid()
    try:
        subprocess.run(["launchctl", "kickstart", "-k", f"gui/{uid}/{LABEL}"], timeout=8, capture_output=True)
    except Exception: pass
    do_spawn = os.environ.get("TFO_CLAUDE_WATCHDOG_SPAWN") == "1" if spawn is None else spawn
    if not do_spawn: return "signaled"
    if _alive(_pid(directory)): return "signaled"
    env = dict(os.environ); env["TFO_CLAUDE_BRIDGE_DIR"] = directory; env.setdefault("TFO_CLAUDE_RSS_EXIT", "0")
    logp = os.environ.get("TFO_CLAUDE_WATCHDOG_LOG")
    log = open(logp, "a") if logp else subprocess.DEVNULL
    subprocess.Popen([sys.executable, os.path.join(BASE, "tools", "claude_bridge_daemon.py")], cwd=BASE, env=env,
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    return "spawned"
def check_once(directory=None):
    directory = directory or bridge_dir()
    up, _body = health(directory)
    status = read_status(directory) if up else read_status(directory)
    if not up:
        why = "health_down"; restart(directory); return why
    need, why = needs_restart(True, status)
    if need: restart(directory); return why
    return "ok"
def main(argv):
    once = "--once" in argv
    while True:
        try: print(check_once())
        except Exception as e: print("watchdog_error", type(e).__name__)
        if once: return 0
        time.sleep(INTERVAL)
if __name__ == "__main__": sys.exit(main(sys.argv[1:]) or 0)
