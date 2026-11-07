#!/usr/bin/env python3
"""macdata_20261106 (master fixer, F31): a consistent read-only SNAPSHOT of the droplet intraday bar store for the dashboard mirror.
The dash (tfo-dash-thf) runs under ProtectSystem=strict with /opt/tfo read-only, so it cannot open the live WAL database (needs the -shm);
the refresh service cannot either. This runs on the live side (tfo-thf-bars-snapshot.timer, every 5 min): sqlite backup API ->
data/intraday/bars_snapshot.sqlite in rollback-journal mode, atomic replace. tools/thf_dash_refresh.sh copies that file into the mirror."""
import os, sqlite3, sys, time
L = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(L, "data", "intraday", "bars.sqlite"); DST = os.path.join(L, "data", "intraday", "bars_snapshot.sqlite")

def main():
    tmp = DST + ".tmp.%d" % os.getpid(); t0 = time.time()
    try:
        s = sqlite3.connect("file:%s?mode=ro" % SRC, uri=True, timeout=30); d = sqlite3.connect(tmp)
        s.backup(d); d.execute("PRAGMA journal_mode=DELETE"); d.commit()
        n = d.execute("SELECT COUNT(*) FROM bars").fetchone()[0]; d.close(); s.close()
        os.replace(tmp, DST)
        for ext in ("-wal", "-shm"):
            try: os.remove(DST + ext)
            except FileNotFoundError: pass
        print("%s bars_snapshot ok: %d rows, %.1f MB, %.1f s" % (time.strftime("%F %T"), n, os.path.getsize(DST) / 1e6, time.time() - t0)); return 0
    except Exception as e:
        try: os.remove(tmp)
        except OSError: pass
        print("%s bars_snapshot FAILED: %s" % (time.strftime("%F %T"), e)); return 1

if __name__ == "__main__": sys.exit(main())
