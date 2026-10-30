#!/usr/bin/env python3
"""Desk health monitor with bounded self-healing.

Checks every moving part of the trading floor and repairs what is SAFE to repair
automatically. Everything else is reported, loudly, for a human.

THE SAFETY BOUNDARY — this is the important part of this file
-------------------------------------------------------------
MAY be restarted automatically. These are read-only public-data collectors and
read-only viewers: if one dies, nothing about the book changes, and restarting it
cannot produce a trade.
    kraken_orderbook_collector, kraken_tape_collector,
    kraken_funding_collector, kraken_kalshi_collector,
    jarvis_server, replica_server, command_server

MUST NOT be touched automatically — reported only:
    kraken_paper_bot.py      the live engine. It hot-reloads on source change and a
                             past save/restart race wiped the paper account. It also
                             owns open positions: restarting it at a non-flat book is
                             a real risk. Its own supervisor owns that decision.
    kraken_supervisor.py     owns engine lifecycle; racing it makes things worse
    any risk parameter       stops, gross cap, kill switch, daily-loss limit.
                             A monitor that can widen a risk limit is not a monitor.
    any engine source file   see hot-reload above

The healer never places, closes or sizes a trade, never edits strategy or risk
config, and never touches secrets. Every action it takes is appended to a log.

Usage:
    python3 desk_healer.py            # check, heal what is safe, print report
    python3 desk_healer.py --dry-run  # check and report only, change nothing
    python3 desk_healer.py --json     # machine-readable status
"""
import argparse
import json
import os
import subprocess
import time
import urllib.request
from desk_risk_health import check_risk_reports

BASE = os.path.dirname(os.path.abspath(__file__))
KRAKEN_DESK = "/Users/test/Applications/KrakenQuantDesk"
STATUS_FILE = os.path.join(BASE, "reports", "desk_health.json")
HEAL_LOG = os.path.join(BASE, "reports", "heal_log.jsonl")

MICRO_DIR = os.path.join(KRAKEN_DESK, "reports", "microstructure")
def _tailnet_ip():
    """Current Tailscale IP — derived live so a re-registered IP never orphans
    the family listener (it changed once: .72.184.28 -> .127.19.45)."""
    import subprocess
    # launchd's minimal PATH cannot find the bare "tailscale" CLI, which made
    # heartbeat restarts silently fall back to 127.0.0.1 (found 2026-08-20):
    # try the app-bundle absolute path first, bare name second. Still fails
    # CLOSED to loopback — never to 0.0.0.0.
    for ts in ("/usr/local/bin/tailscale",          # Homebrew (this Mac)
               "/opt/homebrew/bin/tailscale",       # Homebrew (Apple Silicon)
               "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
               "tailscale"):
        try:
            ip = subprocess.check_output([ts, "ip", "-4"], timeout=8
                                         ).decode().split()[0].strip()
            if ip.startswith("100."):
                return ip
        except Exception:
            continue
    return "127.0.0.1"


TAILNET_IP = _tailnet_ip()          # this Mac on the family tailnet (dynamic)

# name -> (process match string, restart argv or None, cwd)
COLLECTORS = {
    "orderbook": ("kraken_orderbook_collector.py",
                  ["python3", "kraken_orderbook_collector.py", "--top", "5", "--interval", "3"],
                  KRAKEN_DESK),
    "tape": ("kraken_tape_collector.py",
             ["python3", "kraken_tape_collector.py", "--top", "5", "--interval", "20"],
             KRAKEN_DESK),
    "funding": ("kraken_funding_collector.py",
                ["python3", "kraken_funding_collector.py", "--interval", "300"],
                KRAKEN_DESK),
    "kalshi": ("kraken_kalshi_collector.py",
               ["python3", "kraken_kalshi_collector.py", "--interval", "90"],
               KRAKEN_DESK),
    # Thin-name order-book roster (added 2026-08-24, funder directive: measure the
    # real cost of the sub-$1M band instead of extrapolating it). 20 PF symbols
    # log-spaced $81k-$1.4M/day, own output stream (--tag thin), gentle 10s
    # interval. Read-only public data; restarting it cannot produce a trade.
    "orderbook_thin": ("--tag thin",
                       ["/Library/Frameworks/Python.framework/Versions/3.14/bin/python3",
                        "kraken_perp_orderbook_collector.py",
                        "--symbols", "PF_ORDIUSD,PF_SPCXXUSD,PF_XTZUSD,PF_STRKUSD,PF_SPXUSD,PF_EIGENUSD,PF_GLDXUSD,PF_KAITOUSD,PF_DYDXUSD,PF_APTUSD,PF_ETCUSD,PF_TRXUSD,PF_MORPHOUSD,PF_ETHFIUSD,PF_WIFUSD,PF_BOMEUSD,PF_PAXGUSD,PF_POLUSD,PF_BNBUSD,PF_CRVUSD",
                        "--interval", "10", "--levels", "10", "--tag", "thin"],
                       KRAKEN_DESK),
    # Thin-roster futures-analytics SIGNAL leg (added 2026-08-24). The panel is
    # written only by the engine, which feeds it the LIQUID universe; the engine
    # file is permanently off-limits, so this collects the same metrics via the
    # same code path for the thin roster. Reads its roster live from the
    # orderbook_thin entry above so signal and depth can never drift apart.
    # Read-only public data; restarting it cannot produce a trade.
    "thin_panel": ("thin_panel_collector.py",
                   ["/Library/Frameworks/Python.framework/Versions/3.14/bin/python3",
                    "thin_panel_collector.py", "--interval", "180", "--batch", "5"],
                   KRAKEN_DESK),
    # Forward-accumulating news feed. Read-only public RSS; headlines are stored
    # as DATA and never interpreted as instructions.
    "news": ("news_collector.py",
             ["python3", "news_collector.py", "--loop", "300"], BASE),
    # Kraken PUBLIC websocket trade tape (BTC/USD + ETH/USD only) feeding the
    # maker-calibration probe. Read-only public data; restarting it cannot
    # produce a trade. Interpreter is PINNED to the framework python because
    # a PATH-relative python3 under launchd lacks websocket-client.
    "ws_tape": ("ws_tape_collector.py",
                ["/Library/Frameworks/Python.framework/Versions/3.14/bin/python3",
                 "ws_tape_collector.py"], BASE),
}

VIEWERS = {
    "jarvis_dashboard": ("jarvis_server.py",
                         ["python3", "jarvis_server.py", "--port", "18808",
                          "--engine", "18799"], KRAKEN_DESK, 18808),
    "replica_dashboard": ("replica_server.py",
                        ["python3", "replica_server.py", "--port", "18810"], BASE, 18810),
    # War Room is ONE supervised process (com.taylor.warroom) on 0.0.0.0:18812.
    # Do NOT auto-restart a second :18811 — dual processes caused mismatched
    # Taylor-seed headlines (ops/com.taylor.warroom.plist). Healer only checks
    # HTTP liveness; launchd KeepAlive owns restarts.
    "warroom_dashboard": ("command_server.py --port 18812",
                          None, BASE, 18812),
}

# Reported only, never auto-restarted.
PROTECTED = {
    "engine": "kraken_paper_bot.py",
    "engine_supervisor": "kraken_supervisor.py",
    "collector_supervisor": "kraken_collector_supervisor.py",
}

DATA_STALE_SEC = {"ob_": 600, "tape_": 900, "funding_": 1800}
NEWS_DIR = os.path.join(BASE, "reports", "news")
NEWS_STALE_SEC = 3600


def _ps():
    try:
        return subprocess.run(["ps", "axo", "pid=,command="], capture_output=True,
                              text=True, timeout=20).stdout
    except Exception:
        return ""


def find_pids(match, ps_output=None):
    out = ps_output if ps_output is not None else _ps()
    pids = []
    for line in out.splitlines():
        line = line.strip()
        if not line or match not in line:
            continue
        if "desk_healer" in line:      # never match ourselves
            continue
        parts = line.split(None, 1)
        if parts and parts[0].isdigit():
            pids.append(int(parts[0]))
    return pids


def http_ok(port, path="/", timeout=5, host="127.0.0.1"):
    try:
        with urllib.request.urlopen("http://%s:%d%s" % (host, port, path),
                                    timeout=timeout) as r:
            return 200 <= r.status < 400
    except Exception:
        return False


def engine_state(timeout=8):
    try:
        with urllib.request.urlopen("http://127.0.0.1:18799/state", timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None


def data_freshness():
    """Age of the newest row-bearing file for each collector stream."""
    out = {}
    if not os.path.isdir(MICRO_DIR):
        return out
    now = time.time()
    for prefix, max_age in DATA_STALE_SEC.items():
        newest, newest_name = None, None
        for fn in os.listdir(MICRO_DIR):
            if not fn.startswith(prefix):
                continue
            p = os.path.join(MICRO_DIR, fn)
            try:
                m = os.path.getmtime(p)
            except OSError:
                continue
            if newest is None or m > newest:
                newest, newest_name = m, fn
        if newest is None:
            out[prefix.rstrip("_")] = {"file": None, "age_sec": None, "stale": True}
        else:
            age = now - newest
            out[prefix.rstrip("_")] = {
                "file": newest_name, "age_sec": round(age),
                "max_age_sec": max_age, "stale": age > max_age}
    return out


def log_action(entry):
    entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    os.makedirs(os.path.dirname(HEAL_LOG), exist_ok=True)
    with open(HEAL_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def restart(name, argv, cwd, dry_run=False):
    """Relaunch a detached, read-only helper process."""
    if dry_run:
        log_action({"action": "would_restart", "target": name, "cmd": " ".join(argv)})
        return {"restarted": False, "dry_run": True}
    try:
        logf = open(os.path.join(BASE, "reports", "%s.restart.log" % name), "a")
        subprocess.Popen(argv, cwd=cwd, stdout=logf, stderr=subprocess.STDOUT,
                         start_new_session=True)
        log_action({"action": "restarted", "target": name, "cmd": " ".join(argv)})
        return {"restarted": True}
    except Exception as e:
        log_action({"action": "restart_failed", "target": name, "error": str(e)[:200]})
        return {"restarted": False, "error": str(e)[:200]}


def check(dry_run=False):
    ps_out = _ps()
    report = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "actions_taken": [],
        "problems": [],
        "warnings": [],
        "collectors": {},
        "viewers": {},
        "protected": {},
        "data": {},
        "engine": {},
    }

    # ---- protected processes: report only ----
    engine_up = engine_state() is not None
    for name, match in PROTECTED.items():
        pids = find_pids(match, ps_out)
        report["protected"][name] = {"alive": bool(pids), "pids": pids,
                                     "policy": "report-only, never auto-restarted"}
        if not pids:
            # The engine_supervisor exits CLEAN by design whenever the engine
            # already holds :18799 (single-instance guard). So "supervisor absent
            # + engine healthy" is EXPECTED, not a problem — the heartbeat relaunches
            # it on absence, which restarts the engine only if it is actually down.
            # Escalate only if the engine ITSELF is unreachable.
            if name == "engine_supervisor" and engine_up:
                report["warnings"].append(
                    "engine_supervisor not resident (normal: exits clean while engine "
                    "holds :18799; heartbeat relaunches it on absence to cover a real "
                    "engine death)")
            else:
                report["problems"].append(
                    "%s (%s) is NOT running — needs a human; this process is never "
                    "auto-restarted because it owns the book / engine lifecycle"
                    % (name, match))

    # ---- engine responsiveness and risk state ----
    st = engine_state()
    if st is None:
        report["engine"] = {"reachable": False}
        report["problems"].append(
            "engine /state on :18799 is unreachable — NOT auto-restarting "
            "(hot-reload race + open-position risk); escalate to a human")
    else:
        pos = st.get("positions") or {}
        report["engine"] = {
            "reachable": True,
            "equity": st.get("equity"),
            "daily_pnl": st.get("daily_pnl"),
            "realized_pnl": st.get("realized_pnl"),
            "open_positions": len(pos),
            "error_count": st.get("error_count"),
            "error_streak": st.get("error_streak"),
            "kill_switch": st.get("kill_switch"),
            "trading_paused": st.get("trading_paused"),
            "data_ok": st.get("data_ok"),
            "last_error": st.get("last_error"),
            "max_dd_seen": st.get("max_dd_seen"),
        }
        if st.get("kill_switch"):
            report["problems"].append("KILL SWITCH IS ACTIVE — engine has halted trading")
        if st.get("trading_paused"):
            report["warnings"].append("trading is paused")
        if not st.get("data_ok", True):
            report["problems"].append("engine reports data_ok = false")
        streak = st.get("error_streak") or 0
        if streak >= 3:
            report["problems"].append("engine error_streak = %d and climbing" % streak)
        elif streak > 0:
            report["warnings"].append("engine error_streak = %d" % streak)

    # ---- collectors: safe to heal ----
    for name, (match, argv, cwd) in COLLECTORS.items():
        pids = find_pids(match, ps_out)
        entry = {"alive": bool(pids), "pids": pids}
        if not pids:
            entry["action"] = restart(name, argv, cwd, dry_run)
            report["actions_taken"].append(
                "%s collector was dead -> %s" % (name, "would restart (dry-run)"
                                                 if dry_run else "RESTARTED"))
        report["collectors"][name] = entry

    # ---- data freshness ----
    report["data"] = data_freshness()
    # news lives in this desk's own reports/news, not the Kraken microstructure dir
    try:
        newest = max((os.path.getmtime(os.path.join(NEWS_DIR, f))
                      for f in os.listdir(NEWS_DIR) if f.startswith("news_")),
                     default=None)
    except OSError:
        newest = None
    if newest is None:
        report["data"]["news"] = {"file": None, "age_sec": None, "stale": True}
    else:
        age = time.time() - newest
        report["data"]["news"] = {"file": "news_*.jsonl", "age_sec": round(age),
                                  "max_age_sec": NEWS_STALE_SEC,
                                  "stale": age > NEWS_STALE_SEC}
    for stream, info in report["data"].items():
        if info.get("stale"):
            report["warnings"].append(
                "%s data is stale (age %ss, threshold %ss) — collector may be alive "
                "but not writing" % (stream, info.get("age_sec"), info.get("max_age_sec")))

    # ---- viewers: safe to heal ----
    for name, (match, argv, cwd, port) in VIEWERS.items():
        pids = find_pids(match, ps_out)
        # War Room binds 0.0.0.0:18812 — probe loopback (launchd owns KeepAlive).
        ok = http_ok(port, host="127.0.0.1")
        entry = {"alive": bool(pids), "pids": pids, "port": port, "http_ok": ok}
        if not ok:
            if argv is None:
                entry["action"] = {"restarted": False, "policy": "launchd-only"}
                report["problems"].append(
                    "%s on :%d not responding — escalate (supervised by launchd, "
                    "healer will not spawn a second process)" % (name, port))
            else:
                entry["action"] = restart(name, argv, cwd, dry_run)
                report["actions_taken"].append(
                    "%s on :%d not responding -> %s" % (
                        name, port, "would restart (dry-run)" if dry_run else "RESTARTED"))
        report["viewers"][name] = entry

    # A live process is not a successful valuation/risk cycle. Check the
    # reports themselves; monitoring only, with no changes to trading gates.
    report["risk_reports"], risk_problems = check_risk_reports(os.path.join(BASE, "reports"))
    report["problems"].extend(risk_problems)

    report["status"] = ("PROBLEM" if report["problems"]
                        else "ATTENTION" if (report["warnings"] or report["actions_taken"])
                        else "OK")

    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(report, f, indent=2)
    os.replace(tmp, STATUS_FILE)
    return report


def print_report(r):
    print("=" * 72)
    print("DESK HEALTH — %s   [%s]" % (r["checked_at"], r["status"]))
    print("=" * 72)

    e = r["engine"]
    if e.get("reachable"):
        print("\nENGINE (:18799)  equity %.2f | daily %.2f | open %d | errors %s (streak %s)"
              % (e["equity"], e["daily_pnl"], e["open_positions"],
                 e["error_count"], e["error_streak"]))
        print("  kill_switch %s | paused %s | data_ok %s"
              % (e["kill_switch"], e["trading_paused"], e["data_ok"]))
    else:
        print("\nENGINE (:18799)  UNREACHABLE")

    print("\nPROTECTED (never auto-restarted)")
    for n, v in r["protected"].items():
        print("  %-22s %s" % (n, "alive pid %s" % v["pids"] if v["alive"] else "*** DOWN ***"))

    print("\nCOLLECTORS")
    for n, v in r["collectors"].items():
        s = "alive" if v["alive"] else "DEAD"
        act = v.get("action")
        if act:
            s += " -> " + ("would restart" if act.get("dry_run")
                           else "RESTARTED" if act.get("restarted") else "RESTART FAILED")
        print("  %-22s %s" % (n, s))

    print("\nDATA FRESHNESS")
    for n, v in r["data"].items():
        print("  %-22s %s (%ss old)%s" % (n, v.get("file"), v.get("age_sec"),
                                          "  *** STALE ***" if v.get("stale") else ""))

    print("\nVIEWERS")
    for n, v in r["viewers"].items():
        s = "ok" if v["http_ok"] else "NOT RESPONDING"
        act = v.get("action")
        if act:
            s += " -> " + ("would restart" if act.get("dry_run")
                           else "RESTARTED" if act.get("restarted") else "RESTART FAILED")
        print("  %-22s :%d %s" % (n, v["port"], s))

    if r["actions_taken"]:
        print("\nACTIONS TAKEN")
        for a in r["actions_taken"]:
            print("  * " + a)
    if r["problems"]:
        print("\nPROBLEMS (need a human)")
        for p in r["problems"]:
            print("  ! " + p)
    if r["warnings"]:
        print("\nWARNINGS")
        for w in r["warnings"]:
            print("  - " + w)
    if r["status"] == "OK":
        print("\nAll systems nominal.")
    print("\nstatus -> %s" % STATUS_FILE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="check and report only; restart nothing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    r = check(dry_run=args.dry_run)
    if args.json:
        print(json.dumps(r, indent=2))
    else:
        print_report(r)


if __name__ == "__main__":
    main()
