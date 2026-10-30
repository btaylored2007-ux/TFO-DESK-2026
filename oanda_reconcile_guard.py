#!/usr/bin/env python3
"""oanda_reconcile_guard.py - OANDA fill/stop reconcile health guard (PAPER / practice). 2026-10-25.

Brett approved 25 Oct 2026 ~18:50 WITA ("Yes, all of it"), as amended by TFO Risk audit; evidence
state/post_trade/weekly_2026-10-25.md rule 3 (62 reconcile_error DNS failures 22-23 Oct, ~25 h unreadable).

  * Every oanda_sleeve check/daily run records ONE reconcile pass: clean only if BOTH reconcile_stop_fills() and
    reconcile_stops() succeeded in that run (a run that dies before reconciling counts as a failed pass).
  * Any failed pass since the last clean pass -> status FAILING: the OANDA book is UNVERIFIED on the board and in
    firm_risk (its marks are stale and are not counted).
  * Failing for > 2 h (since the first failure after the last clean pass) -> BLOCKED: no new entries (TFO sleeve and
    AGGRO); stops/exits keep running; an alert is raised once (trades.jsonl event=reconcile_alert,
    state/oanda/RECONCILE_ALERT.json, macOS notification best-effort).
  * BLOCKED / FAILING clear only after one full clean reconcile pass.
Never touches positions; never raises. State: state/oanda/reconcile_health.json.
"""
import datetime as dt, json, os, subprocess

BLOCK_AFTER_S = 2 * 3600
FILE = "reconcile_health.json"
ALERT = "RECONCILE_ALERT.json"
RECORDED = False   # set once a pass is recorded in this process (the run-error path then does not double count)


def _now(): return dt.datetime.now(dt.timezone.utc)
def _ts(s): return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def load(st):
    try:
        with open(os.path.join(st, FILE)) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(st, d):
    p = os.path.join(st, FILE); tmp = p + ".tmp"
    with open(tmp, "w") as f: json.dump(d, f, indent=1)
    os.replace(tmp, p)


def status(st, now=None):
    """CLEAN | FAILING | BLOCKED (BLOCKED is time-based, so it holds even if no run happens)."""
    d = load(st); now = now or _now()
    if not d.get("first_fail_utc"): return "CLEAN"
    try:
        if (now - _ts(d["first_fail_utc"])).total_seconds() > BLOCK_AFTER_S or d.get("blocked_since_utc"): return "BLOCKED"
    except Exception:
        return "BLOCKED"
    return "FAILING"


def entries_blocked(st, now=None): return status(st, now) == "BLOCKED"
def verified(st, now=None): return status(st, now) == "CLEAN"


def _notify(msg):
    try:
        subprocess.run(["osascript", "-e", 'display notification "%s" with title "TFO OANDA reconcile"' % msg.replace('"', "'")],
                       timeout=5, capture_output=True)
    except Exception:
        pass


def record_pass(st, ok, error=None, log=None, now=None):
    """Record one reconcile pass. Returns the new status. Never raises."""
    global RECORDED
    RECORDED = True
    try:
        now = now or _now(); d = load(st); prev = status(st, now)
        d["last_pass_utc"] = now.isoformat(timespec="seconds"); d["last_pass_ok"] = bool(ok)
        if ok:
            if prev != "CLEAN":
                d["last_cleared_utc"] = d["last_pass_utc"]; d["cleared_after_fails"] = d.get("fail_count", 0)
                if log: log(dict(event="reconcile_alert_cleared", prev_status=prev, first_fail_utc=d.get("first_fail_utc"),
                                 fails=d.get("fail_count", 0), note="one full clean reconcile pass; entries unblocked; book VERIFIED"))
                try: os.remove(os.path.join(st, ALERT))
                except OSError: pass
            d.update(first_fail_utc=None, blocked_since_utc=None, fail_count=0, last_error=None, last_clean_utc=d["last_pass_utc"])
        else:
            d.setdefault("first_fail_utc", None)
            if not d.get("first_fail_utc"): d["first_fail_utc"] = d["last_pass_utc"]
            d["fail_count"] = int(d.get("fail_count") or 0) + 1; d["last_error"] = (str(error) if error else "reconcile failed")[:300]
            if not d.get("blocked_since_utc") and (now - _ts(d["first_fail_utc"])).total_seconds() > BLOCK_AFTER_S:
                d["blocked_since_utc"] = d["last_pass_utc"]
                msg = "OANDA reconcile failing since %s (%d fails): NEW ENTRIES BLOCKED, book UNVERIFIED" % (d["first_fail_utc"], d["fail_count"])
                a = dict(event="reconcile_alert", status="BLOCKED", first_fail_utc=d["first_fail_utc"], fails=d["fail_count"],
                         last_error=d["last_error"], message=msg)
                if log: log(a)
                with open(os.path.join(st, ALERT), "w") as f: json.dump(dict(a, utc=d["last_pass_utc"]), f, indent=1)
                _notify(msg)
        _save(st, d)
        return status(st, now)
    except Exception:
        return None


def board(st, now=None):
    """Fields for the board / firm_risk."""
    d = load(st); s = status(st, now)
    return {"reconcile_status": s, "verified": s == "CLEAN", "book_label": "VERIFIED" if s == "CLEAN" else "UNVERIFIED",
            "entries_blocked": s == "BLOCKED", "reconcile_first_fail_utc": d.get("first_fail_utc"),
            "reconcile_fail_count": d.get("fail_count", 0), "reconcile_last_clean_utc": d.get("last_clean_utc"),
            "reconcile_last_error": d.get("last_error")}
