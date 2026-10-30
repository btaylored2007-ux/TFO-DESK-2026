#!/usr/bin/env python3
"""boot_notifier.py — post-boot status notifier for the Taylor Family Office desk.

Launched by com.taylor.tfo-bootnotify (LaunchAgent, RunAtLoad) after every boot.
Waits for the firm's stack to come up, then reports ONCE: a macOS notification on
this Mac plus reports/boot_report.json (rendered as a card on the /report page).
The funder's complaint this fixes (2026-08-17): a reboot showed a dead dashboard
with no explanation — the first thing seen after a boot should be the status.

STDLIB ONLY by design: launchd runs with a minimal PATH, and the desk has already
been bitten by system-python-without-numpy silently killing jobs (see desk_tick.sh
interpreter note). This script must run on ANY python3.

RunAtLoad (not StartCalendarInterval) by design: the 2026-08-15 host timezone move
silently broke every StartCalendarInterval job on this machine; RunAtLoad is
calendar-immune.

Read-only w.r.t. the firm: probes HTTP endpoints and launchctl, writes only its
own report file and log. Never restarts anything — that is the healer's job.
"""
import json
import os
import subprocess
import time
import urllib.request

BASE = "/Users/test/Applications/MultiVenueDesk"
OUT = os.path.join(BASE, "reports", "boot_report.json")
DEADLINE_SEC = int(os.environ.get("BOOT_NOTIFY_DEADLINE", 20 * 60))
POLL_SEC = 15

CHECKS = {
    "war_room": lambda: _http("http://127.0.0.1:18812/healthz"),
    "family_dashboard": lambda: _http("http://127.0.0.1:18812/healthz"),
    "engine_reachable": lambda: _engine() is not None,
    "engine_data_ok": lambda: bool((_engine() or {}).get("data_ok")),
    "heartbeat_registered": lambda: _launchd("com.taylor.familyoffice"),
    "collectors_registered": lambda: _launchd("com.local.kraken-collectors"),
}
# data_ok can lag many minutes after boot while feeds refill; everything else
# should be up quickly. A timeout with ONLY engine_data_ok missing is reported
# as WARMING (amber), not DEGRADED (red).
SLOW_OK = {"engine_data_ok"}


def _http(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


_engine_cache = [0.0, None]


def _engine():
    if time.time() - _engine_cache[0] > 5:
        try:
            with urllib.request.urlopen("http://127.0.0.1:18799/state", timeout=5) as r:
                _engine_cache[1] = json.load(r)
        except Exception:
            _engine_cache[1] = None
        _engine_cache[0] = time.time()
    return _engine_cache[1]


def _launchd(label):
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True,
                             text=True, timeout=10).stdout
        return label in out
    except Exception:
        return False


def _boot_epoch():
    try:
        out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True,
                             text=True, timeout=5).stdout
        return int(out.split("sec =")[1].split(",")[0].strip())
    except Exception:
        return None


def _notify(title, msg):
    try:
        subprocess.run(["osascript", "-e",
                        'display notification "%s" with title "%s"'
                        % (msg.replace('"', "'"), title.replace('"', "'"))],
                       timeout=10)
    except Exception:
        pass  # notification is best-effort; the JSON report is the record


def main():
    boot = _boot_epoch()
    start = time.time()
    first_ok = {}
    while True:
        state = {k: bool(f()) for k, f in CHECKS.items()}
        for k, v in state.items():
            if v and k not in first_ok:
                first_ok[k] = round(time.time() - start, 1)
        missing = [k for k, v in state.items() if not v]
        if not missing or (time.time() - start) > DEADLINE_SEC:
            break
        time.sleep(POLL_SEC)

    elapsed = time.time() - start
    since_boot = (time.time() - boot) if boot else None
    if not missing:
        status, level = "ALL GREEN", "green"
    elif set(missing) <= SLOW_OK:
        status, level = "UP — engine data still warming", "amber"
    else:
        status, level = "DEGRADED: " + ", ".join(sorted(missing)), "red"

    report = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "boot_epoch": boot,
        "minutes_since_boot": round(since_boot / 60, 1) if since_boot else None,
        "wait_seconds": round(elapsed, 0),
        "status": status,
        "level": level,
        "checks": state,
        "first_ok_after_s": first_ok,
    }
    tmp = "%s.tmp.%d" % (OUT, os.getpid())
    with open(tmp, "w") as f:
        json.dump(report, f, indent=2)
    os.replace(tmp, OUT)

    mins = ("%.0f min after boot" % (since_boot / 60)) if since_boot else ""
    _notify("TFO Boot Report", "%s %s" % (status, mins))
    print("boot report: %s (%s) -> %s" % (status, mins, OUT))
    return 0 if level != "red" else 1


if __name__ == "__main__":
    raise SystemExit(main())
