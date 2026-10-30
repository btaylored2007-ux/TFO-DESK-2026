"""tfo_futroll_20261030: daily futures top-up + roll job for data/pit (PAPER research data).
WHY: pit.stale_universe() excludes '=F' so 28 futures sat at 27-Oct bars; nothing re-ran fut_roll after 28 Oct.
WHAT: every FUT symbol (universe venue FUT or bars_fut_raw) that is behind its expected last session -> pit.ingest_yahoo(period=1mo)
 (raw -> bars_fut_raw, then fut_roll.rebuild re-detects rolls + ratio back-adjusts bars). Before touching a symbol its bars / bars_fut_raw /
 fut_rolls rows are dumped to data/pit/backups/futroll/<stamp>/<sym>.json (restorable). Report: data/pit/fut_roll_job_report.json.
Usage: python3 tools/fut_roll_job.py [--all] [--dry-run] [SYM ...]"""
import datetime as dt, json, os, sys, time
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, BASE)
from veteran_desk import pit as P
UTC = dt.timezone.utc
REP = os.path.join(BASE, "data", "pit", "fut_roll_job_report.json")
def fut_syms(c):
    s = {r[0] for r in c.execute("SELECT sym FROM universe WHERE venue='FUT'")} | {r[0] for r in c.execute("SELECT DISTINCT sym FROM bars_fut_raw")}
    return sorted(x for x in s if x.endswith("=F"))
def backup(c, sym, d):
    os.makedirs(d, exist_ok=True); out = {}
    for t in ("bars", "bars_fut_raw", "fut_rolls"): out[t] = c.execute("SELECT * FROM %s WHERE sym=?" % t, (sym,)).fetchall()
    json.dump(out, open(os.path.join(d, sym.replace("=", "_") + ".json"), "w"))
def main(argv):
    dry = "--dry-run" in argv; allf = "--all" in argv; want = [a for a in argv if not a.startswith("--")]
    c = P.con(); c.execute("PRAGMA busy_timeout=300000")
    syms = want or fut_syms(c)
    todo = [s for s in syms if allf or want or P.is_stale(c, s, slack_days=0)]
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S"); bdir = os.path.join(BASE, "data", "pit", "backups", "futroll", stamp)
    before = {s: (P.last_bar(c, s) or [None])[0] for s in todo}
    if dry: print(json.dumps(dict(dry_run=True, todo=before), indent=1)); return 0
    ok, fail = {}, {}
    for s in todo:
        for k in range(3):
            try:
                backup(c, s, bdir); o, f = P.refresh_symbols([s], "1mo", c); ok.update(o); fail.update(f); fail.pop(s, None) if s in o else None; break
            except Exception as e:
                fail[s] = str(e)[:120]
                if "locked" in str(e): time.sleep(20); continue
                break
        time.sleep(0.5)
    after = {s: (P.last_bar(c, s) or [None])[0] for s in todo}
    rolls = {s: c.execute("SELECT roll_date,factor,method,status FROM fut_rolls WHERE sym=? ORDER BY roll_date DESC LIMIT 1", (s,)).fetchone() for s in todo}
    rep = dict(utc=dt.datetime.now(UTC).isoformat(), checked=len(syms), stale=len(todo), ok=len(ok), fail=fail, before=before, after=after, latest_roll=rolls, backup_dir=bdir,
               still_stale=[s for s in syms if P.is_stale(c, s, slack_days=1)])
    json.dump(rep, open(REP, "w"), indent=1, default=str); print(json.dumps(dict(stale=len(todo), ok=len(ok), fail=len(fail), still_stale=rep["still_stale"])))
    return 0 if not fail else 1
if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
