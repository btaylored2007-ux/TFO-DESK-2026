#!/usr/bin/env python3
"""TREND-CORE own heartbeat — PAPER ONLY (path B, not firm equity).

Brett 28 Oct 14:58 WITA (pt1624u): dedicated mark job so every run writes
state/trend_core_log.txt + health JSON; alarms if ledger >90 min stale.
Also probes rebalance-if-due (no-op until US open after rebook) so launchd
logs are never empty. Does not touch Trend18 funding.
"""
from __future__ import annotations
import datetime as dt, json, os, sys, tempfile, traceback

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "state")
STATIC = os.path.join(BASE, "static")
HEALTH = os.path.join(STATE, "trend_core_health.json")
ALARM = os.path.join(STATE, "trend_core_alarms.json")
TICKLOG = os.path.join(STATE, "trend_core_tick.log")
LEDGER = os.path.join(STATE, "trend_core.json")
STALE_SEC = 90 * 60
UTC = dt.timezone.utc
WITA = dt.timezone(dt.timedelta(hours=8))

def wita(t=None):
    return (t or dt.datetime.now(UTC)).astimezone(WITA).strftime("%Y-%m-%d %H:%M:%S WITA")

def log(msg):
    line = "%s %s" % (wita(), msg)
    with open(TICKLOG, "a") as f:
        f.write(line + "\n")
    print(line)

def atomic(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=2, default=str); f.write("\n")
    os.replace(tmp, path)

def age_sec(path):
    try:
        return max(0.0, dt.datetime.now(UTC).timestamp() - os.path.getmtime(path))
    except OSError:
        return None

def main():
    pre = age_sec(LEDGER); ok = False; err = None; st = {}; rebal_note = None
    try:
        import trend_core as tc
        tc.mark()
        ok = True
        try:
            st = json.load(open(os.path.join(STATIC, "trend_core_status.json")))
        except Exception:
            st = {}
        try:
            import trend_core_global as g
            g.rebalance_if_due(force_dry=False)  # logs every probe; executes only when due
            rebal_note = "probed"
        except Exception as e:
            rebal_note = "probe_err %s" % e
            log("rebalance probe: %s" % e)
        try:
            import trend_core_shadow as sh
            if hasattr(sh, 'main'):
                sh.main([])
            elif hasattr(sh, 'run'):
                sh.run()
        except Exception as e:
            log("shadow skip: %s" % e)
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)
        log("ERROR mark failed: %s" % err)
        traceback.print_exc()

    post = age_sec(LEDGER)
    stale = (post is None) or (post > STALE_SEC) or (not ok)
    alarm = None
    if stale:
        alarm = dict(code="trend_core_book_stale",
                     msg="TREND-CORE ledger age %ss > %ss (ok=%s err=%s)" % (
                         None if post is None else round(post), STALE_SEC, ok, err),
                     at_wita=wita())
        atomic(ALARM, dict(active=True, alarm=alarm, updated_wita=wita()))
        log("ALARM %s" % alarm["msg"])
    else:
        atomic(ALARM, dict(active=False, alarm=None, cleared_wita=wita()))

    health = dict(
        book="trend_core", paper_only=True, ok=ok, stale=stale,
        stale_sec_threshold=STALE_SEC,
        ledger_age_sec_before=None if pre is None else round(pre, 1),
        ledger_age_sec=None if post is None else round(post, 1),
        nav_aud=st.get("nav_aud") or st.get("equity_aud"),
        cash_aud=st.get("cash_aud"),
        next_rebalance=st.get("next_rebalance") or (json.load(open(LEDGER)).get("next_rebalance") if os.path.exists(LEDGER) else None),
        rebalance_probe=rebal_note, error=err, alarm=alarm,
        updated_utc=dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        updated_wita=wita(),
    )
    atomic(HEALTH, health)
    atomic(os.path.join(STATIC, "trend_core_health.json"), health)
    log("HEALTH ok=%s stale=%s age=%ss nav=A$%s next=%s" % (
        ok, stale, health["ledger_age_sec"], health.get("nav_aud"), health.get("next_rebalance")))
    return 0 if ok and not stale else 1

if __name__ == "__main__":
    sys.exit(main())
