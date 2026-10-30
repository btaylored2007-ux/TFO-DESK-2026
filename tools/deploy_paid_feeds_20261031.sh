#!/bin/bash
# PAPER ONLY. Paid-feeds migration for the fundamental book (HT 2026-10-30 21:12 WITA).
# Run AFTER the US close (>= 04:00 WITA Sat 31 Oct) and BEFORE the 05:43 rank rebuild. Never during the US session.
# Usage: bash tools/deploy_paid_feeds_20261031.sh            (deploy + next_earnings stamp)
#        SET_CATALYST=1 bash tools/deploy_paid_feeds_20261031.sh   (ALSO set missing catalyst_date — HT decision, see report)
set -u
cd "$(dirname "$0")/.." || exit 1
export DESK=$PWD STAGE=$PWD/staged_fixes/paid_feeds_stage_20261031 TS=$(date +%Y%m%d_%H%M%S)
python3 - <<'PY'
import os, sys, time, shutil, hashlib, subprocess, datetime as dt, py_compile, traceback
D, S, TS = os.environ["DESK"], os.environ["STAGE"], os.environ["TS"]
sys.path.insert(0, D); os.chdir(D)
now = dt.datetime.now()
REHEARSAL = os.path.realpath(D).startswith(("/tmp/", "/private/tmp/"))   # /tmp rehearsal copy only: skip clock guards
def die(m): print("ABORT:", m); sys.exit(2)
# --- window: Sat 31 Oct 04:00-05:38 WITA, never within 2 min of a :11/:26/:41/:56 tick
if not REHEARSAL and not (now.date() == dt.date(2026, 10, 31) and dt.time(4, 0) <= now.time() <= dt.time(5, 38)):
    die("outside the 04:00-05:38 WITA Sat 31 Oct window (US session / rank rebuild guard)")
m = now.minute % 15
if not REHEARSAL and not (m >= 13 or m <= 9): die("too close to an autodeploy tick (:11/:26/:41/:56); retry in a few minutes")
if subprocess.run(["pgrep", "-f", "value_deploy|fundamental_rank_rebuild|value_hunt.py"], capture_output=True).stdout.strip():
    die("a value_deploy / rank-rebuild process is running — mid-tick")
# --- base files unchanged since staging
for ln in open(os.path.join(S, "BASE_SHA256")):
    h, f = ln.split()
    if hashlib.sha256(open(f, "rb").read()).hexdigest() != h: die("%s changed since staging — re-stage, do not overwrite another agent's edit" % f)
if os.path.exists("paid_feeds.py"): die("paid_feeds.py already exists on the desk")
import desk_lock
FILES = [("fundamental_book.py", "fundamental_book.py"), ("value_hunt.py", "value_hunt.py"),
         ("paid_feeds.py", "paid_feeds.py"), ("fundamental_earnings_backfill.py", "tools/fundamental_earnings_backfill.py")]
baks, created = [], []
def rollback():
    for f, b in baks: shutil.copy2(b, f)
    for f in created: os.path.exists(f) and os.remove(f)
    print("ROLLED BACK:", [f for f, _ in baks] + created)
with desk_lock.ledger_lock("fundamental", timeout=300):
    try:
        for src, dst in FILES:
            if os.path.exists(dst):
                b = "%s.bak_%s" % (dst, TS); shutil.copy2(dst, b); baks.append((dst, b))
            else:
                created.append(dst)
            tmp = dst + ".deploytmp"; shutil.copy2(os.path.join(S, src), tmp); py_compile.compile(tmp, doraise=True); os.replace(tmp, dst)
        for _, dst in FILES: py_compile.compile(dst, doraise=True)
        smoke = subprocess.run([sys.executable, "-c", r'''
import sys, json; sys.path.insert(0, "."); import fundamental_book as fb, value_hunt as vh, paid_feeds as pf
p, fail = fb.fetch_prices(["EOG", "NAB.AX", "TSCO.L"], require_executable=False)
assert len(p) >= 2 and all(v.get("px_usd") for v in p.values()), ("fetch_prices", fail)
print({k: (v.get("source"), v.get("px"), v.get("session"), fb.quote_metadata(v)["mark_source"]) for k, v in p.items()})
q = pf.fetch_quote("EOG"); assert q.get("px") and q.get("ccy") == "USD" and "paper_executable" in q, q
r, why = vh.screen_one("EOG"); assert r and r.get("industry") and r.get("sector") == "Energy", (r, why)
print("SMOKE OK", sorted(p), q.get("source"), r.get("fund_source"), r.get("industry"), json.dumps(pf.STATS))
'''], capture_output=True, text=True, timeout=240)
        print(smoke.stdout[-1500:], smoke.stderr[-1500:])
        if smoke.returncode != 0 or "SMOKE OK" not in smoke.stdout: raise RuntimeError("smoke test failed")
    except Exception:
        traceback.print_exc(); rollback()
        with open("DEPLOY_LOG.md", "a") as f:
            f.write("\n## %s WITA — paid_feeds_20261031 FAILED + ROLLED BACK — PAPER\n- see console; files restored from .bak_%s\n" % (now.strftime("%a %d %b %H:%M"), TS))
        sys.exit(1)
with open("DEPLOY_LOG.md", "a") as f:
    f.write("""
## %s WITA — paid_feeds_20261031 — PAPER
- HT 2026-10-30 21:12 order (paid feeds first). Files: fundamental_book.py (fetch_prices -> paid fetcher), value_hunt.py (FMP-first fundamentals + industry, Yahoo fallback, P/B gate unchanged), NEW paid_feeds.py, NEW tools/fundamental_earnings_backfill.py
- Quotes: US Massive 1m aggs; .AX/.L/.T/.HK FMP /stable/ 1min (+official close after session); else/any failure Yahoo chart. Same parse_chart -> eligibility semantics unchanged.
- Backups: %s
- py_compile ok; smoke ok. Kill-switch: PAID_FEEDS_DISABLE=1 (quotes) / VALUE_HUNT_YAHOO_ONLY=1 (fundamentals).
- Rollback: for f in fundamental_book.py value_hunt.py; do cp -p $f.bak_%s $f; done; rm paid_feeds.py tools/fundamental_earnings_backfill.py
""" % (now.strftime("%a %d %b %H:%M"), ", ".join(b for _, b in baks), TS))
print("DEPLOYED. backups:", baks)
PY
rc=$?; [ $rc -ne 0 ] && exit $rc
if [ "${SET_CATALYST:-0}" = "1" ]; then python3 tools/fundamental_earnings_backfill.py --write --set-catalyst; else python3 tools/fundamental_earnings_backfill.py --write; fi \
  | tee -a logs/paid_feeds_deploy_$TS.log
echo "- earnings stamp run (SET_CATALYST=${SET_CATALYST:-0}); output logs/paid_feeds_deploy_$TS.log" >> DEPLOY_LOG.md
