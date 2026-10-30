#!/usr/bin/env python3
"""ESSENTIALS own heartbeat — PAPER ONLY.

Brett 28 Oct 14:58 WITA (pt1624u): dedicated launchd so ESSENTIALS is not only
hourly inside desk_tick. Marks the book, fills STAGED at the next live open, and
alarms if the ledger is >90 minutes stale. Never backdates fills. Not in firm equity.
"""
from __future__ import annotations
import datetime as dt, json, os, sys, tempfile, traceback

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "state")
STATIC = os.path.join(BASE, "static")
HEALTH = os.path.join(STATE, "essentials_health.json")
ALARM = os.path.join(STATE, "essentials_alarms.json")
LOGF = os.path.join(STATE, "essentials_tick.log")
LEDGER = os.path.join(STATE, "essentials.json")
STALE_SEC = 90 * 60
UTC = dt.timezone.utc
WITA = dt.timezone(dt.timedelta(hours=8))

def wita(t=None):
    return (t or dt.datetime.now(UTC)).astimezone(WITA).strftime("%Y-%m-%d %H:%M:%S WITA")

def log(msg):
    line = "%s %s" % (wita(), msg)
    with open(LOGF, "a") as f:
        f.write(line + "\n")
    print(line)

def atomic(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=2, default=str)
        f.write("\n")
    os.replace(tmp, path)

def age_sec(path):
    try:
        return max(0.0, dt.datetime.now(UTC).timestamp() - os.path.getmtime(path))
    except OSError:
        return None

def main():
    pre_age = age_sec(LEDGER)
    alarm = None
    ok = False
    err = None
    st = {}
    try:
        import essentials_book as EB
        EB.mark()  # fill-staged at live open, then re-mark + write_status
        ok = True
        try:
            st = json.load(open(os.path.join(STATIC, "essentials_status.json")))
        except Exception:
            st = {}
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)
        log("ERROR mark failed: %s" % err)
        traceback.print_exc()

    post_age = age_sec(LEDGER)
    stale = (post_age is None) or (post_age > STALE_SEC) or (not ok)
    if stale:
        alarm = dict(
            code="essentials_book_stale",
            msg="ESSENTIALS ledger age %ss > %ss (ok=%s err=%s)" % (
                None if post_age is None else round(post_age), STALE_SEC, ok, err),
            at_wita=wita(),
        )
        atomic(ALARM, dict(active=True, alarm=alarm, updated_wita=wita()))
        log("ALARM %s" % alarm["msg"])
    else:
        atomic(ALARM, dict(active=False, alarm=None, cleared_wita=wita(),
                           note="book fresh after mark"))
    health = dict(
        book="essentials",
        paper_only=True,
        ok=ok,
        stale=stale,
        stale_sec_threshold=STALE_SEC,
        ledger_age_sec_before=None if pre_age is None else round(pre_age, 1),
        ledger_age_sec=None if post_age is None else round(post_age, 1),
        nav_aud=st.get("nav_aud"),
        cash_aud=st.get("cash_aud"),
        staged_aud=st.get("staged_aud"),
        deployed_aud=st.get("deployed_aud"),
        staged_n=len((json.load(open(LEDGER)).get("staged") if os.path.exists(LEDGER) else []) or []),
        open_markets=[m for m in ("US", "ASX", "LSE", "EU", "NO", "JP", "HK", "SG", "KR", "TW")
                      if __import__("essentials_book").is_open(m)] if ok else [],
        error=err,
        alarm=alarm,
        updated_utc=dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        updated_wita=wita(),
    )
    atomic(HEALTH, health)
    # also mirror for War Room / watchdog static
    atomic(os.path.join(STATIC, "essentials_health.json"), health)
    log("HEALTH ok=%s stale=%s age=%ss nav=A$%s staged=A$%s open=%s" % (
        ok, stale, health["ledger_age_sec"], health.get("nav_aud"),
        health.get("staged_aud"), health.get("open_markets")))
    return 0 if ok and not stale else 1

def _rotate_err(path=os.path.join(BASE, "logs", "essentials-tick.err.log"), cap=5 * 1024 * 1024, keep=3):
    """2026-10-29 TFO 11:12 WITA fix 3: size-capped rotation (~5 MB x 3) of the launchd stderr log, between ticks."""
    try:
        if not os.path.exists(path) or os.path.getsize(path) < cap: return
        for i in range(keep, 1, -1):
            if os.path.exists("%s.%d" % (path, i - 1)): os.replace("%s.%d" % (path, i - 1), "%s.%d" % (path, i))
        os.replace(path, path + ".1")
    except Exception: pass

if __name__ == "__main__":
    import warnings
    try: import yfinance   # noqa: F401 - yfinance re-enables its own DeprecationWarnings at import, so our filter must come after it
    except Exception: pass
    warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"yfinance")   # fix 3: yfinance-only noise; real errors still logged
    _rotate_err()
    sys.exit(main())
