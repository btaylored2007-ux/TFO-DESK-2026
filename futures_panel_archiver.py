#!/usr/bin/env python3
"""futures_panel_archiver — make the futures_analytics panel actually accumulate.

WHY THIS EXISTS (found 2026-08-26, reports/research_futures_panel_retention.md):
market_intelligence.py:497 runs, on EVERY successful metric write,

    DELETE FROM futures_analytics WHERE spot_symbol=? AND metric=? AND ts < now-7*86400

so the panel is a ROLLING 7-DAY WINDOW, not an archive. Every actively-collected
symbol's oldest surviving row is 7.0-7.1 days old, always. The 23-day figure the
desk had been watching as a ripeness gate is `MAX(ts)-MIN(ts)` over the WHOLE table,
which is long only because DE-ROSTERED symbols stop being written and therefore stop
being pruned — dead names, not usable panel. 3,081 rows that existed when
research_futures_panel.md ran on 2026-08-24 had already been deleted two days later.
The prune is not a bug to remove: the source DB is 1.2 GB because each snapshot
repeats a 48-bucket window, so raw retention is unaffordable.

WHAT THIS DOES: read-only on the source, hourly from desk_tick.sh, it unpacks each
snapshot into its 30-minute buckets and upserts them into a compact wide table.
Dedup collapses the ~50x snapshot redundancy: ~93 symbols x 48 buckets/day is
~500 KB/day instead of ~600 MB/day of raw payload. Nothing is ever deleted here.

PAPER/RESEARCH INFRASTRUCTURE ONLY. SELECT-only on market_intelligence.sqlite; the
engine, kraken_paper_bot.py and market_intelligence.py are NOT touched.

HOUSE LAW (the Kalshi/SBO lesson): a collector that finds nothing must RAISE, never
return quiet success. A run that parses zero buckets exits non-zero and says PROBLEM.
"""
import sqlite3, json, math, sys, datetime as dt, statistics, os, tempfile

SRC = "/Users/test/Applications/KrakenQuantDesk/kraken_bot/market_intelligence.sqlite"
ARC = "/Users/test/Applications/MultiVenueDesk/data/futures_panel_archive.sqlite"
GATE_REPORT = "/Users/test/Applications/MultiVenueDesk/reports/futures_panel_readiness.json"
FIELDS = ["oi","funding","funding_abs","basis","lsr","liq","aggr","cvd","buy_vol","sell_vol"]

def f(x):
    try:
        v = float(x); return v if math.isfinite(v) else None
    except Exception: return None

def build(conn, since_ts=None):
    """Unpack snapshots into {(fsym, bucket_ts): {field: value}}. Parser is VERBATIM
    from futures_panel_lab.py — funding timestamps are MILLISECONDS, open-interest and
    funding.rate/relativeRate are OHLC quads (close = index 3), the rest scalars."""
    cells = {}; spot_of = {}; nrow = 0
    q = ("SELECT spot_symbol,futures_symbol,metric,payload_json FROM futures_analytics "
         "WHERE metric!='availability'")
    args = ()
    if since_ts is not None:
        q += " AND ts >= ?"; args = (since_ts,)
    for spot, fsym, metric, pj in conn.execute(q, args):
        nrow += 1; spot_of[fsym] = spot
        try: d = json.loads(pj)
        except Exception: continue
        ts = d.get("timestamp") or []
        if not ts: continue
        ts = [int(t)//1000 if int(t) > 1e11 else int(t) for t in ts]
        dat = d.get("data")
        def put(field, vals, pick=None):
            if not vals or len(vals) != len(ts): return
            for t, v in zip(ts, vals):
                if pick is not None and isinstance(v, list): v = v[pick] if len(v) > pick else None
                x = f(v)
                if x is None: continue
                if not (1700000000 < t < 1800000000): continue
                cells.setdefault((fsym, t), {})[field] = x
        if metric == "open-interest": put("oi", dat, pick=3)
        elif metric == "funding":
            put("funding", (dat or {}).get("relativeRate"), pick=3)
            put("funding_abs", (dat or {}).get("rate"), pick=3)
        elif metric == "long-short-ratio": put("lsr", dat)
        elif metric == "future-basis": put("basis", (dat or {}).get("basis"))
        elif metric == "cvd":
            put("cvd", (dat or {}).get("cvd"))
            put("buy_vol", (dat or {}).get("buy_volume"))
            put("sell_vol", (dat or {}).get("sell_volume"))
        elif metric == "liquidation-volume": put("liq", dat)
        elif metric == "aggressor-differential": put("aggr", dat)
    return cells, spot_of, nrow

LIQUID_SEVEN = ["XBT/USD","XRP/USD","ETH/USD","SOL/USD","ADA/USD","LINK/USD","HYPE/USD"]

# The next predeclared study is the original 4h time-series FAMILY, not a single
# cherry-picked feature.  Readiness therefore requires the exact archived fields
# consumed by that family.  Counting a row that contains only OI while funding or
# flow collection is degraded would start the clock early and quietly change the
# test after seeing which feature survived.
REQUIRED_FIELDS_4H = (
    "oi", "lsr", "basis", "cvd", "buy_vol", "sell_vol", "liq", "aggr", "funding",
)


def _field_complete_rows(conn, required_fields=REQUIRED_FIELDS_4H):
    unknown = sorted(set(required_fields) - set(FIELDS))
    if unknown:
        raise ValueError("unknown required archive field(s): %s" % ", ".join(unknown))
    where = " AND ".join("%s IS NOT NULL" % field for field in required_fields)
    return list(conn.execute(
        "SELECT fsym,spot,ts FROM panel WHERE %s ORDER BY fsym,ts" % where))


def _readiness(rows, liquid_seven=LIQUID_SEVEN):
    """Compute readiness from field-complete rows only."""
    import collections
    runs = collections.defaultdict(float); spot_of = {}
    cur = None; prev = None; run = 1
    for fs, sp, t in rows:
        spot_of[fs] = sp
        if fs != cur:
            cur, prev, run = fs, t, 1
        else:
            run = run + 1 if t - prev == 1800 else 1
            prev = t
        runs[fs] = max(runs[fs], run * 1800 / 86400.0)
    days = collections.defaultdict(set)
    for fs, sp, t in rows:
        days[dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d")].add(fs)
    breadth30 = sum(1 for ss in days.values() if len(ss) >= 30)
    # Collapse contracts to the named spot market, then require the complete
    # predeclared seven-market panel.  Multiple contracts for one market are
    # not substitutes for missing markets and must never manufacture a median.
    liquid_runs = {}
    for fs, sp in spot_of.items():
        if sp in liquid_seven:
            liquid_runs[sp] = max(liquid_runs.get(sp, 0.0), runs[fs])
    missing_liquid = sorted(set(liquid_seven) - set(liquid_runs))
    liquid_complete = not missing_liquid and len(liquid_runs) == len(set(liquid_seven))
    liq = [liquid_runs[spot] for spot in liquid_seven] if liquid_complete else []
    return {
        "runs": runs,
        "symbols": len(runs),
        "best_days": max(runs.values()) if runs else 0.0,
        "liquid_median_days": statistics.median(liq) if liq else 0.0,
        "liquid_seven_complete": liquid_complete,
        "liquid_seven_missing": missing_liquid,
        "liquid_seven_runs": liquid_runs,
        "breadth30_days": breadth30,
    }


def _write_gate_report(report):
    """Atomically replace readiness so a stale green report cannot survive."""
    target = os.fspath(GATE_REPORT)
    parent = os.path.dirname(target) or "."
    os.makedirs(parent, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".futures_panel_readiness.", dir=parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _failed_gate_report(code, message, total_rows=None):
    return {
        "generated_utc": dt.datetime.now(dt.timezone.utc).replace(
            microsecond=0).isoformat().replace("+00:00", "Z"),
        "paper_only": True,
        "readiness_status": "ERROR",
        "error": {"code": code, "message": message},
        "required_fields": list(REQUIRED_FIELDS_4H),
        "field_complete_rows": 0,
        "total_rows": total_rows,
        "symbols": 0,
        "best_contiguous_days": 0.0,
        "liquid_seven_median_contiguous_days": 0.0,
        "liquid_seven_complete": False,
        "liquid_seven_missing": list(LIQUID_SEVEN),
        "liquid_seven_runs": {},
        "breadth_30_symbols_days": 0,
        "four_hour_ts": {"required_days": 27, "ready": False},
        "twelve_hour_xs": {"required_days": 36, "ready": False},
        "closed": {"horizons_at_or_below_2h": True},
    }

def gate():
    """Readiness check. Reports the LONGEST CONTIGUOUS RUN PER SYMBOL in the archive —
    never `MAX(ts)-MIN(ts)` over the table, which counts de-rostered symbols' orphaned
    rows and reported 24 days when no usable series exceeded 8 (see
    reports/research_futures_panel_retention.md, 2026-08-26)."""
    a = None
    try:
        a = sqlite3.connect("file:%s?mode=ro" % ARC, uri=True)
        total_rows = a.execute("SELECT COUNT(*) FROM panel").fetchone()[0]
        rows = _field_complete_rows(a)
    except Exception as exc:
        if a is not None:
            a.close()
        message = "archive unreadable (%s: %s)" % (type(exc).__name__, exc)
        print("futures panel gate: PROBLEM — %s" % message)
        try:
            _write_gate_report(_failed_gate_report("ARCHIVE_UNREADABLE", message))
        except Exception as report_exc:
            print("futures panel gate: PROBLEM — could not persist fail-closed readiness (%s: %s)"
                  % (type(report_exc).__name__, report_exc))
        return 1
    if not rows:
        if total_rows:
            message = ("archive has %d rows but ZERO are complete for the predeclared "
                       "fields (%s). This is a partial-metric outage, not readiness."
                       % (total_rows, ", ".join(REQUIRED_FIELDS_4H)))
            code = "ZERO_FIELD_COMPLETE_ROWS"
        else:
            message = ("archive is EMPTY. The hourly archiver in desk_tick.sh is not "
                       "running; the source panel self-deletes after 7 days.")
            code = "EMPTY_ARCHIVE"
        print("futures panel gate: PROBLEM — %s" % message)
        a.close()
        try:
            _write_gate_report(_failed_gate_report(code, message, total_rows))
        except Exception as report_exc:
            print("futures panel gate: PROBLEM — could not persist fail-closed readiness (%s: %s)"
                  % (type(report_exc).__name__, report_exc))
        return 1
    ready = _readiness(rows)
    breadth30 = ready["breadth30_days"]
    med = ready["liquid_median_days"]
    best = ready["best_days"]
    a.close()
    print("futures panel gate (FIELD-COMPLETE ARCHIVE, per-symbol contiguous runs — NOT table min/max):")
    print("  required fields: %s" % ", ".join(REQUIRED_FIELDS_4H))
    print("  field-complete rows %d / %d total" % (len(rows), total_rows))
    print("  symbols %d · best run %.2fd · liquid-seven median run %.2fd · days with >=30 symbols %d"
          % (ready["symbols"], best, med, breadth30))
    if not ready["liquid_seven_complete"]:
        print("  liquid-seven panel INCOMPLETE; missing: %s"
              % ", ".join(ready["liquid_seven_missing"]))
    print("  4h TS  (needs 27d contiguous on liquid names): %.2fd -> %s"
          % (med, "READY FOR STUDY" if med >= 27 else "NOT READY"))
    print("  12h XS (needs 36 days of >=30-symbol breadth): %d -> %s"
          % (breadth30, "READY FOR STUDY" if breadth30 >= 36 else "NOT READY"))
    print("  <=2h horizons: ANSWERED NO (research_futures_panel.md) — never re-test")
    report = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "paper_only": True,
        "readiness_status": "OK",
        "error": None,
        "required_fields": list(REQUIRED_FIELDS_4H),
        "field_complete_rows": len(rows),
        "total_rows": total_rows,
        "symbols": ready["symbols"],
        "best_contiguous_days": round(best, 6),
        "liquid_seven_median_contiguous_days": round(med, 6),
        "liquid_seven_complete": ready["liquid_seven_complete"],
        "liquid_seven_missing": ready["liquid_seven_missing"],
        "liquid_seven_runs": {spot: round(days, 6)
                              for spot, days in ready["liquid_seven_runs"].items()},
        "breadth_30_symbols_days": breadth30,
        "four_hour_ts": {"required_days": 27,
                           "ready": ready["liquid_seven_complete"] and med >= 27},
        "twelve_hour_xs": {"required_days": 36, "ready": breadth30 >= 36},
        "closed": {"horizons_at_or_below_2h": True},
    }
    try:
        _write_gate_report(report)
    except Exception as exc:
        print("futures panel gate: PROBLEM — could not persist readiness (%s: %s)"
              % (type(exc).__name__, exc))
        return 1
    return 0

def main():
    if "--gate" in sys.argv:
        return gate()
    full = "--full" in sys.argv
    a = sqlite3.connect(ARC)
    a.execute("""CREATE TABLE IF NOT EXISTS panel(
        fsym TEXT NOT NULL, spot TEXT, ts INTEGER NOT NULL,
        oi REAL, funding REAL, funding_abs REAL, basis REAL, lsr REAL,
        liq REAL, aggr REAL, cvd REAL, buy_vol REAL, sell_vol REAL,
        PRIMARY KEY(fsym, ts))""")
    a.execute("CREATE INDEX IF NOT EXISTS idx_panel_ts ON panel(ts)")
    a.execute("""CREATE TABLE IF NOT EXISTS runs(
        run_ts INTEGER PRIMARY KEY, rows_read INTEGER, buckets_seen INTEGER,
        inserted INTEGER, src_oldest INTEGER, src_newest INTEGER,
        arc_oldest INTEGER, arc_newest INTEGER, symbols INTEGER, note TEXT)""")
    a.commit()

    # incremental: re-read a 2-day overlap so nothing straddling a run is missed
    since = None
    if not full:
        r = a.execute("SELECT MAX(run_ts) FROM runs").fetchone()[0]
        if r: since = int(r) - 2*86400

    s = sqlite3.connect("file:%s?mode=ro" % SRC, uri=True)
    cells, spot_of, nrow = build(s, since)
    src = s.execute("SELECT MIN(ts),MAX(ts) FROM futures_analytics").fetchone()
    s.close()

    cols = ",".join(FIELDS)
    setc = ",".join("%s=COALESCE(excluded.%s,panel.%s)" % (c, c, c) for c in FIELDS)
    sql = ("INSERT INTO panel(fsym,spot,ts,%s) VALUES(?,?,?,%s) "
           "ON CONFLICT(fsym,ts) DO UPDATE SET spot=COALESCE(excluded.spot,panel.spot),%s"
           % (cols, ",".join("?"*len(FIELDS)), setc))
    before = a.execute("SELECT COUNT(*) FROM panel").fetchone()[0]
    a.executemany(sql, [(fs, spot_of.get(fs), t, *[v.get(c) for c in FIELDS])
                        for (fs, t), v in cells.items()])
    a.commit()
    after = a.execute("SELECT COUNT(*) FROM panel").fetchone()[0]
    lo, hi, nsym = a.execute("SELECT MIN(ts),MAX(ts),COUNT(DISTINCT fsym) FROM panel").fetchone()
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    a.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?,?,?,?,?,?)",
              (now, nrow, len(cells), after-before, src[0], src[1], lo, hi, nsym,
               "full" if full else "incremental"))
    a.commit(); a.close()

    U = lambda t: dt.datetime.fromtimestamp(int(t), dt.timezone.utc).strftime("%Y-%m-%d %H:%M") if t else "-"
    span = (hi-lo)/86400.0 if lo and hi else 0.0
    print("futures_panel_archiver [%s]: read %d snapshot rows -> %d buckets, +%d new"
          % ("full" if full else "incremental", nrow, len(cells), after-before))
    print("  source window (pruned to 7d)  : %s -> %s" % (U(src[0]), U(src[1])))
    print("  ARCHIVE (never pruned)        : %s -> %s  %.2f days, %d symbols, %d bucket-rows"
          % (U(lo), U(hi), span, nsym or 0, after))
    if len(cells) == 0:
        print("  PROBLEM: parsed ZERO buckets from the source panel — schema drift or "
              "collector dead. This must not read as quiet success.")
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
