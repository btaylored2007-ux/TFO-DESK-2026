"""Validate + merge data/pit_staging/<group>/ (CSV or Parquet + manifest.json) into data/pit/pit.sqlite. PAPER research data only.
Checks per file: required columns, no Indonesian symbols, available_at_utc parseable and >= bar date (leak), not in future, OHLC sane,
duplicate (symbol,date) rows, calendar gaps > 10 days (reported, not fatal). A file failing a hard check is QUARANTINED (not merged).
  python3 -m veteran_desk.ingest_staging [--dry-run]"""
import csv, datetime as dt, glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from veteran_desk import pit as P
UTC = dt.timezone.utc; STAGE = os.path.join(P.BASE, "data", "pit_staging"); REPORT = os.path.join(P.BASE, "data", "pit", "ingest_report.json")
REQ = {"symbol", "date", "open", "high", "low", "close", "volume", "source", "available_at_utc", "delisted_flag"}
def rows_of(path):
    if path.endswith(".parquet"):
        import pandas as pd; return pd.read_parquet(path).astype(str).to_dict("records")
    return list(csv.DictReader(open(path, newline="")))
def ts(x):
    t = dt.datetime.fromisoformat(str(x).replace("Z", "+00:00")); return t if t.tzinfo else t.replace(tzinfo=UTC)
def validate(rows):
    hard, soft, good, seen = [], [], [], set(); nowt = dt.datetime.now(UTC)
    if rows and not REQ <= set(rows[0]): return [f"missing columns {sorted(REQ - set(rows[0]))}"], soft, []
    for r in rows:
        s, d = r["symbol"].strip(), str(r["date"])[:10]
        if P.banned(s): hard.append(f"Indonesian symbol {s}"); continue
        try: a = ts(r["available_at_utc"]); o, h, l, c = (float(r[k]) for k in ("open", "high", "low", "close")); v = float(r.get("volume") or 0)
        except Exception as e: soft.append(f"{s} {d} unparseable: {e}"[:120]); continue
        if a.date() < dt.date.fromisoformat(d): hard.append(f"LEAK {s} {d} available {a.isoformat()} before bar date"); continue
        if a > nowt + dt.timedelta(hours=1) or dt.date.fromisoformat(d) > nowt.date(): hard.append(f"future row {s} {d}"); continue
        if (s, d) in seen: soft.append(f"duplicate {s} {d} (first kept)"); continue
        if not (l <= min(o, c) + 1e-9 and h >= max(o, c) - 1e-9 and l > 0): soft.append(f"bad OHLC {s} {d} dropped"); continue
        seen.add((s, d)); good.append((s, d, o, h, l, c, v, a.isoformat(), r["source"], str(r["delisted_flag"]).lower() in ("1", "true", "yes")))
    return hard, soft, good
def gaps(good):
    by = {}
    for g in good: by.setdefault(g[0], []).append(g[1])
    out = {}
    for s, ds in by.items():
        ds = sorted(dt.date.fromisoformat(x) for x in ds); big = [(a.isoformat(), b.isoformat()) for a, b in zip(ds, ds[1:]) if (b - a).days > 10]
        if big: out[s] = big[:5]
    return out
def run(dry=False, stage=STAGE, db=None):
    c = P.con(db); rep = dict(run_utc=dt.datetime.now(UTC).isoformat(), groups={}, dry_run=dry)
    for g in sorted(os.listdir(stage)) if os.path.isdir(stage) else []:
        gd = os.path.join(stage, g); man = os.path.join(gd, "manifest.json"); G = rep["groups"][g] = dict(files={}, merged_rows=0, manifest=os.path.exists(man))
        try: M = json.load(open(man)); G["survivorship"] = M.get("survivorship")
        except Exception: M = {}
        for f in sorted(glob.glob(os.path.join(gd, "**", "*.csv"), recursive=True) + glob.glob(os.path.join(gd, "**", "*.parquet"), recursive=True)):
            try: hard, soft, good = validate(rows_of(f))
            except Exception as e: hard, soft, good = [f"unreadable {e}"[:150]], [], []
            st = "QUARANTINED" if hard else "OK"; G["files"][os.path.relpath(f, stage)] = dict(status=st, rows=len(good), hard=hard[:5], soft=soft[:5], n_soft=len(soft), gaps=gaps(good))
            if hard or dry: continue
            c.executemany("INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?,?,?,?)", [x[:9] for x in good])
            for s in {x[0] for x in good}:
                dl = any(x[9] for x in good if x[0] == s); last = max(x[1] for x in good if x[0] == s)
                c.execute("INSERT OR REPLACE INTO universe VALUES(?,?,?,?,?)", (s, P.venue_of(s), "delisted" if dl else "listed", last if dl else None, f"staging:{g}"))
            G["merged_rows"] += len(good); c.commit()
        if M.get("survivorship") == "UNIVERSE_BIASED" or not M: c.execute("INSERT OR REPLACE INTO venues VALUES(?,?,?)", (f"staging:{g}", "UNIVERSE_BIASED" if M else "UNKNOWN", M.get("notes", "no manifest")))
    c.commit()
    if db is None: json.dump(rep, open(REPORT, "w"), indent=1)
    return rep
if __name__ == "__main__":
    r = run("--dry-run" in sys.argv); print(json.dumps({g: dict(merged=v["merged_rows"], files=len(v["files"]), quarantined=sum(f["status"] != "OK" for f in v["files"].values())) for g, v in r["groups"].items()}))
