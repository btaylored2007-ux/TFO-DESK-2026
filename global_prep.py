"""Review fix #10 (hunt_review_20261027), part: one global daily-bar prep batch (~04:00 WITA) so the Asia/ASX/LSE hunts never
fetch cold. PAPER ONLY, read-only market data: calls hunt48_room.daily() (3 retries + per-symbol disk cache) for every symbol the
desk has cached before plus every symbol in the latest daily plans. Writes nothing except the bar cache and a summary JSON.
Not scheduled by default: the launchd plist is staged in work/hunt_review_fixes_20261027/staged/ (load it to enable)."""
import datetime as dt, glob, json, os, sys, time
BASE = os.environ.get("DESK_BASE", os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
SUMMARY_DIR = os.path.join(BASE, "state", "hunt_orch", "global_prep")
def cache_name_to_sym(fn):
    s = os.path.basename(fn)[:-5] if fn.endswith(".json") else os.path.basename(fn)
    if s.endswith("_X"): s = s[:-2] + "=X"
    elif s.endswith("_F"): s = s[:-2] + "=F"
    if s.startswith("_"): s = "^" + s[1:]
    return s
def symbols(base=BASE):
    out = set()
    for f in glob.glob(os.path.join(base, "state", "hunt48_daily_cache", "*.json")):
        if not f.endswith(".tmp"): out.add(cache_name_to_sym(f))
    days = sorted(glob.glob(os.path.join(base, "reports", "daily_plan", "2*")))
    for f in glob.glob(os.path.join(days[-1], "*.json")) if days else []:
        try: p = json.load(open(f))
        except Exception: continue
        for k in ("open", "armed", "backups", "staged"):
            for x in p.get(k) or []:
                s = x.get("sym") or x.get("ticker")
                if s and p.get("market") not in ("fx", "crypto"): out.add(str(s))
    return sorted(out)
def run(base=BASE, daily_fn=None, syms=None, now=None):
    if daily_fn is None:
        import hunt48_room as R; daily_fn = R.daily
    now = now or dt.datetime.now(dt.timezone.utc); t0 = time.time(); ok, fail = [], []
    for s in (syms if syms is not None else symbols(base)):
        try: (ok if daily_fn(s) else fail).append(s)
        except Exception: fail.append(s)
    res = dict(paper_only=True, utc=now.isoformat(timespec="seconds"), n=len(ok) + len(fail), ok=len(ok), failed=fail[:200], secs=round(time.time() - t0, 1))
    d = os.path.join(base, "state", "hunt_orch", "global_prep"); os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, "last.json.tmp"); json.dump(res, open(tmp, "w"), indent=1); os.replace(tmp, os.path.join(d, "last.json"))
    return res
if __name__ == "__main__": print(json.dumps(run(), indent=1)[:3000])
