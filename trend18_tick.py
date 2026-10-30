#!/usr/bin/env python3
"""Trend18 mark + trail own heartbeat — PAPER ONLY.

Brett 28 Oct 14:58 WITA (pt1624u): pull trail + --mark out of hourly desk_tick onto
their own schedule with health logs. Does NOT stage/execute/fund (other worker owns
30 Oct funding). deep18-fwd stays on com.tfo.deep18-fwd.
"""
from __future__ import annotations
import datetime as dt, json, os, sys, tempfile, traceback

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "state")
STATIC = os.path.join(BASE, "static")
HEALTH = os.path.join(STATE, "trend18_health.json")
ALARM = os.path.join(STATE, "trend18_alarms.json")
TICKLOG = os.path.join(STATE, "trend18_tick.log")
LEDGER = os.path.join(STATE, "trend18.json")
STALE_SEC = 90 * 60
UTC = dt.timezone.utc
WITA = dt.timezone(dt.timedelta(hours=8))

def wita(t=None):
    return (t or dt.datetime.now(UTC)).astimezone(WITA).strftime("%Y-%m-%d %H:%M:%S WITA")

def log(msg):
    line = "%s %s" % (wita(), msg)
    with open(TICKLOG, "a") as f: f.write(line + "\n")
    print(line)

def atomic(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=2, default=str); f.write("\n")
    os.replace(tmp, path)

def age_sec(path):
    try: return max(0.0, dt.datetime.now(UTC).timestamp() - os.path.getmtime(path))
    except OSError: return None

def main():
    pre = age_sec(LEDGER); ok = False; err = None; trail_note = None; equity = None
    try:
        import trend18_trail as tr
        r = tr.run(execute=True)
        trail_note = "pending=%s stopped=%s" % (
            list((r or {}).get("pending") or []) if isinstance(r, dict) else "ran",
            list((r or {}).get("stopped") or []) if isinstance(r, dict) else "")
        import trend18 as t18
        # build minimal args namespace for cmd_mark
        class A: pass
        a = A()
        a.prices_file = None; a.fetch_prices = None; a.allow_yahoo = False
        a.alpaca_env = None; a.price = "last"; a.asof = None; a.force = False
        t18.cmd_mark(a)
        ok = True
        try:
            L = json.load(open(LEDGER))
            equity = L.get("equity_usd") or L.get("equity") or L.get("nav_usd")
        except Exception:
            pass
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)
        log("ERROR: %s" % err); traceback.print_exc()

    post = age_sec(LEDGER)
    stale = (post is None) or (post > STALE_SEC) or (not ok)
    alarm = None
    if stale:
        alarm = dict(code="trend18_book_stale",
                     msg="Trend18 ledger age %ss > %ss (ok=%s err=%s)" % (
                         None if post is None else round(post), STALE_SEC, ok, err),
                     at_wita=wita())
        atomic(ALARM, dict(active=True, alarm=alarm, updated_wita=wita()))
        log("ALARM %s" % alarm["msg"])
    else:
        atomic(ALARM, dict(active=False, alarm=None, cleared_wita=wita()))

    # deep18-fwd health hint (read-only)
    deep18_age = age_sec(os.path.join(BASE, "logs", "deep18-fwd.log"))
    health = dict(
        book="trend18", paper_only=True, ok=ok, stale=stale,
        stale_sec_threshold=STALE_SEC,
        ledger_age_sec_before=None if pre is None else round(pre, 1),
        ledger_age_sec=None if post is None else round(post, 1),
        equity_usd=equity, trail=trail_note, error=err, alarm=alarm,
        deep18_fwd_log_age_sec=None if deep18_age is None else round(deep18_age, 1),
        funding_note="30 Oct deploy owned by other worker — this job never stages/executes/funds",
        updated_utc=dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        updated_wita=wita(),
    )
    atomic(HEALTH, health)
    atomic(os.path.join(STATIC, "trend18_health.json"), health)
    log("HEALTH ok=%s stale=%s age=%ss equity_usd=%s trail=%s" % (
        ok, stale, health["ledger_age_sec"], equity, trail_note))
    return 0 if ok and not stale else 1

if __name__ == "__main__":
    sys.exit(main())
