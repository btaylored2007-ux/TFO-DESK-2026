"""Backfill Kraken PUBLIC daily OHLC for EVERY online pair (AssetPairs) into data/pit/pit.sqlite. PAPER data only; public endpoints, no keys.
DNS via desk kraken_dns.py (DoH, Kraken hosts only). Kraken returns at most ~720 most-recent daily bars per pair.
The still-forming UTC day bar is DROPPED. knowable_utc via pit.knowable (CRYPTO = next day 00:30 UTC).
Modes: full (default) | --daily (incremental, uses `since`). Resumable: progress in data/pit/kraken_backfill_progress.json.
Pair list written to data/pit/kraken_pairs.json (read by pit.UNIVERSE['crypto']). Report: data/pit/kraken_backfill_report.json"""
import datetime as dt, json, os, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from veteran_desk import pit as P
UTC = dt.timezone.utc
PIT = os.path.join(P.BASE, "data", "pit")
PROG = os.path.join(PIT, "kraken_backfill_progress.json")
PAIRS_JSON = os.path.join(PIT, "kraken_pairs.json")
SLEEP = 1.1   # Kraken public limit ~1 req/s
def _get(url):
    import kraken_dns; kraken_dns.install()
    for a in range(5):
        try:
            d = json.loads(urllib.request.urlopen(urllib.request.Request(url), timeout=30).read())
            err = d.get("error") or []
            if any("Too many" in e or "Rate limit" in e for e in err): time.sleep(5 * (a + 1)); continue
            if err: raise RuntimeError(";".join(err))
            return d.get("result") or {}
        except (OSError, ValueError) as e:
            if a == 4: raise
            time.sleep(3 * (a + 1))
    raise RuntimeError("rate limited")
def all_pairs():
    res = _get("https://api.kraken.com/0/public/AssetPairs"); out = []
    for k, v in res.items():
        alt = v.get("altname") or k
        if v.get("status", "online") != "online" or alt.endswith(".d") or P.banned("KRAKEN:" + alt): continue
        out.append(dict(alt=alt, key=k, ws=v.get("wsname"), base=v.get("base"), quote=v.get("quote")))
    out.sort(key=lambda x: x["alt"])
    tmp = PAIRS_JSON + ".tmp"; json.dump(dict(fetched_utc=dt.datetime.now(UTC).isoformat(), n=len(out), pairs=out), open(tmp, "w"), indent=0); os.replace(tmp, PAIRS_JSON)
    return out
def fetch(pair, since=None):
    url = "https://api.kraken.com/0/public/OHLC?pair=%s&interval=1440" % pair
    if since: url += "&since=%d" % since
    res = _get(url); k = [x for x in res if x != "last"]
    today = dt.datetime.now(UTC).date().isoformat(); rows = []
    for t, o, h, l, c, vw, v, n in (res[k[0]] if k else []):
        D = dt.datetime.fromtimestamp(int(t), UTC).date().isoformat()
        if D >= today: continue   # incomplete bar
        rows.append((D, float(o), float(h), float(l), float(c), float(v)))
    return rows
def leak_check(c):
    a = c.execute("SELECT count(*) FROM bars WHERE sym LIKE 'KRAKEN:%' AND knowable_utc < date").fetchone()[0]
    b = 0
    for d, k in c.execute("SELECT date, knowable_utc FROM bars WHERE sym LIKE 'KRAKEN:%'"):
        if k != P.knowable(d, "CRYPTO"): b += 1
    today = dt.datetime.now(UTC).date().isoformat()
    f = c.execute("SELECT count(*) FROM bars WHERE sym LIKE 'KRAKEN:%' AND date >= ?", (today,)).fetchone()[0]
    return dict(knowable_before_date=a, knowable_not_next_0030=b, unfinished_today=f, bad_total=a + b + f)
def main(daily=False):
    run = dt.datetime.now(UTC).date().isoformat() + ("-daily" if daily else "-full")
    prog = {}
    try: prog = json.load(open(PROG))
    except Exception: pass
    if prog.get("run") != run: prog = dict(run=run, done={}, fail={})
    pairs = all_pairs(); c = P.con()
    for i, p in enumerate(pairs):
        alt = p["alt"]
        if alt in prog["done"]: continue
        sym = "KRAKEN:" + alt; since = None
        if daily:
            r = c.execute("SELECT max(date) FROM bars WHERE sym=?", (sym,)).fetchone()[0]
            if r: since = int(dt.datetime.fromisoformat(r).replace(tzinfo=UTC).timestamp()) - 3 * 86400
        try:
            rows = fetch(alt, since)
            if rows: P.put_bars(c, sym, rows, "kraken_public")
            c.commit(); prog["done"][alt] = len(rows); prog["fail"].pop(alt, None)
        except Exception as e: prog["fail"][alt] = str(e)[:160]
        if i % 10 == 0 or i == len(pairs) - 1:
            tmp = PROG + ".tmp"; json.dump(prog, open(tmp, "w")); os.replace(tmp, PROG)
        time.sleep(SLEEP)
    tmp = PROG + ".tmp"; json.dump(prog, open(tmp, "w")); os.replace(tmp, PROG)
    syms = c.execute("SELECT count(DISTINCT sym), count(*) FROM bars WHERE sym LIKE 'KRAKEN:%'").fetchone()
    rep = dict(run=run, finished=dt.datetime.now(UTC).isoformat(), online_pairs=len(pairs), fetched_this_run=len(prog["done"]),
               fail=prog["fail"], kraken_syms_in_db=syms[0], kraken_bars_in_db=syms[1], leak=leak_check(c),
               note="Kraken public OHLC caps at ~720 daily bars; data only - pump-chasing banned")
    json.dump(rep, open(os.path.join(PIT, "kraken_backfill_report.json"), "w"), indent=1); return rep
if __name__ == "__main__":
    r = main(daily="--daily" in sys.argv); print(json.dumps({k: (v if k != "fail" else len(v)) for k, v in r.items()}))
