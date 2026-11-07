"""seatdata_20261103 (PAPER, data only): apply split / consolidation events that pit.sqlite still holds UNADJUSTED.

WHY (trend seat Dex, 3 Nov 17:07 WITA, checklist D26): 'trend scan ranks look split-distorted: lo252 vs close QQD.TO 4.76->25.6,
SLVD.TO 2.3->48 ... mom12_1/sma200 inflated, leveraged ETPs dominate the top 10'. Verified 3 Nov: QQD.TO and HND.TO jumped x4.96 /
x4.95 and SLVD.TO x7.50 on 2026-05-25 in pit - Yahoo lists those consolidations (1:5, 1:5, 1:8) but its TSX chart closes are not
split-adjusted, so every loader stored them raw. (BWET/MGRT/BUUU/CDNA have no split event: their moves are real.)

METHOD (deterministic, idempotent, audited):
  1. suspects: one-day close ratio >= 1.8x or <= 0.55x whose size is near a split ratio (bad_tick_guard.split_ratio, 8% tol).
  2. confirm with Yahoo's own split events (chart v8 events=split, one public call per suspect symbol).
  3. apply ONLY when the stored bars still show the jump on that event date (stored ratio within 25% of the event factor): every bar
     before the event date gets o/h/l/c x factor and v / factor (factor = denominator/numerator; 1:5 -> x5). knowable_utc unchanged;
     src gets '|splitadj'. One audit row per event in table split_fixes; an event already applied (no jump left) is skipped.
Equities only (never KRAKEN:, =X, =F, ^). Never touches ledgers, books, gates or the engine.
Run: python -m veteran_desk.pit_splits [--syms A,B | --scan-tops] [--apply]   (dry-run by default)"""
import argparse, datetime as dt, json, os, sys, time, urllib.parse, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from veteran_desk import pit as P

TAG = "seatdata_20261103"
L = P.BASE; UTC = dt.timezone.utc
SCAN = os.path.join(L, "state", "thf", "scan")
CHK = os.path.join(L, "state", "thf", "split_checks.json")   # {sym: {jumps, verdict applied|real_move|error, utc}} - re-checks only new jumps
RECHECK_S = 14 * 86400
JUMP_UP, JUMP_DN, RATIO_TOL, CONFIRM_TOL = 1.8, 0.55, 0.08, 0.25


def equity(sym):
    s = str(sym); return not (s.startswith("KRAKEN:") or s.endswith("=X") or s.endswith("=F") or s.startswith("^"))


def suspects(rows):
    """rows [(date,o,h,l,c,v,...)] ascending -> [(date, stored_ratio)] split-like one-day jumps."""
    try:
        if L not in sys.path: sys.path.insert(0, L)
        from bad_tick_guard import split_ratio
    except Exception: return []
    out = []
    for i in range(1, len(rows)):
        p0, p1 = rows[i - 1][4], rows[i][4]
        if p0 and p1 and (p1 / p0 >= JUMP_UP or p1 / p0 <= JUMP_DN) and split_ratio(p1, p0, RATIO_TOL): out.append((rows[i][0], p1 / p0))
    return out


def yahoo_splits(sym, rng="5y", get=None):
    """[(date_iso, numerator, denominator)] from Yahoo chart events (exchange-local dates)."""
    from veteran_desk.data_layer import yahoo_chart_url, yahoo_chart_response
    params = {"range": rng, "interval": "1d", "events": "split"}
    if get: d = get(yahoo_chart_url(sym, params))
    else: d = yahoo_chart_response(sym, params, timeout=20).json()
    r = ((d.get("chart") or {}).get("result") or [None])[0] or {}
    tz = (r.get("meta") or {}).get("exchangeTimezoneName")
    try:
        from zoneinfo import ZoneInfo; z = ZoneInfo(tz) if tz else UTC
    except Exception: z = UTC
    out = []
    for k, v in ((r.get("events") or {}).get("splits") or {}).items():
        try:
            n, dn = float(v.get("numerator")), float(v.get("denominator"))
            if n > 0 and dn > 0 and n != dn: out.append((dt.datetime.fromtimestamp(int(v.get("date") or k), z).date().isoformat(), n, dn))
        except Exception: continue
    return sorted(out)


def plan(rows, events):
    """Events that are still UNADJUSTED in rows: [(date, num, den, factor, stored_ratio)]. The stored close ratio across the event
    date must be within CONFIRM_TOL of factor (a genuine move of the same size on another day never matches a dated event)."""
    pos = {r[0]: i for i, r in enumerate(rows)}; out = []
    for d, n, dn in events:
        f = dn / n
        i = pos.get(d)
        if i is None:   # event on a non-trading/missing date: first stored bar on/after it
            later = [j for j, r in enumerate(rows) if r[0] >= d]; i = later[0] if later else None
        if not i or not rows[i - 1][4] or not rows[i][4]: continue
        sr = rows[i][4] / rows[i - 1][4]
        if abs(sr / f - 1) <= CONFIRM_TOL: out.append((rows[i][0], n, dn, f, round(sr, 4)))
    return out


def ensure(c):
    c.execute("CREATE TABLE IF NOT EXISTS split_fixes(sym TEXT, split_date TEXT, ratio TEXT, factor REAL, rows INTEGER, stored_ratio REAL, utc TEXT, src TEXT, PRIMARY KEY(sym, split_date))")


def apply_one(c, sym, ev):
    """Back-adjust every bar of sym strictly before the event date. Returns rows changed. Caller commits."""
    d, n, dn, f, sr = ev
    k = c.execute("UPDATE bars SET o=o*?, h=h*?, l=l*?, c=c*?, v=CASE WHEN v IS NULL THEN NULL ELSE v/? END, "
                  "src=CASE WHEN COALESCE(src,'') LIKE '%|splitadj' THEN src ELSE COALESCE(src,'')||'|splitadj' END WHERE sym=? AND date<?",
                  (f, f, f, f, f, sym, d)).rowcount
    c.execute("INSERT OR REPLACE INTO split_fixes VALUES(?,?,?,?,?,?,?,?)", (sym, d, "%g:%g" % (n, dn), f, k, sr, dt.datetime.now(UTC).isoformat(timespec="seconds"), "yahoo_chart_events"))
    return k


def checks(path=None):
    try:
        with open(path or CHK) as f: return json.load(f)
    except Exception: return {}


def known_real(sym, dates, ck=None):
    """True when pit_splits already found NO Yahoo split event for exactly these jump dates (a real move, not a split)."""
    x = (ck if ck is not None else checks()).get(sym) or {}
    return x.get("verdict") == "real_move" and sorted(x.get("jumps") or []) == sorted(dates)


def run(syms, do_apply=False, get=None, db=None, log=print, pause=0.4, chk_path=None):
    c = P.con(db); ensure(c); rep = dict(tag=TAG, utc=dt.datetime.now(UTC).isoformat(timespec="seconds"), apply=do_apply, checked=0, suspects={}, applied={}, no_event={}, errors={}, cached_real=0)
    ck = checks(chk_path); now_s = time.time()
    for s in [x for x in dict.fromkeys(syms) if equity(x)]:
        rows = c.execute("SELECT date,o,h,l,c,v FROM bars WHERE sym=? ORDER BY date", (s,)).fetchall(); rep["checked"] += 1
        sp = suspects(rows[-520:])
        if not sp: continue
        rep["suspects"][s] = [(d, round(r, 3)) for d, r in sp]
        js = [d for d, _ in sp]; x = ck.get(s) or {}
        if known_real(s, js, ck) and now_s - float(x.get("ts") or 0) < RECHECK_S: rep["cached_real"] += 1; continue
        try: ev = yahoo_splits(s, get=get)
        except Exception as e: rep["errors"][s] = "%s: %s" % (type(e).__name__, str(e)[:80]); continue
        finally: time.sleep(pause if not get else 0)
        pl = plan(rows, ev)
        if not pl:
            rep["no_event"][s] = "jump(s) %s have no matching Yahoo split event (real move) - left as is" % js
            ck[s] = dict(jumps=js, verdict="real_move", ts=now_s); continue
        if do_apply: ck[s] = dict(jumps=[e[0] for e in pl], verdict="applied", ts=now_s)
        if do_apply:
            try:
                for e in sorted(pl, reverse=True):   # newest first: each factor applies to bars before its own date
                    rep["applied"].setdefault(s, []).append(dict(date=e[0], ratio="%g:%g" % (e[1], e[2]), factor=e[3], stored_ratio=e[4], rows=apply_one(c, s, e)))
                c.commit()
            except Exception as x:
                c.rollback(); rep["errors"][s] = "write %s: %s" % (type(x).__name__, str(x)[:80])
        else: rep["applied"][s] = [dict(date=e[0], ratio="%g:%g" % (e[1], e[2]), factor=e[3], stored_ratio=e[4], rows="dry-run") for e in pl]
    c.close()
    try:
        t = (chk_path or CHK) + ".tmp"
        with open(t, "w") as f: json.dump(ck, f)
        os.replace(t, chk_path or CHK)
    except Exception: pass
    return rep


def scan_tops():
    """Every symbol in the seats' scan top lists (state/thf/scan/<seat>.json) - the names seats actually read."""
    out = []
    for f in sorted(os.listdir(SCAN)) if os.path.isdir(SCAN) else []:
        if f.endswith(".json") and not f.startswith("_"):
            try: out += [r["sym"] for r in (json.load(open(os.path.join(SCAN, f))).get("top") or []) if r.get("sym")]
            except Exception: pass
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0]); ap.add_argument("--syms", default=""); ap.add_argument("--scan-tops", action="store_true")
    ap.add_argument("--apply", action="store_true"); ap.add_argument("--db", default=None); a = ap.parse_args(argv)
    syms = [s for s in a.syms.split(",") if s] + (scan_tops() if a.scan_tops else [])
    rep = run(syms, do_apply=a.apply, db=a.db); print(json.dumps(rep, indent=1, default=str)); return 0


if __name__ == "__main__": sys.exit(main())
