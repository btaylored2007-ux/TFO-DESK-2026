"""HARD RULE check (Brett 2026-10-30 21:41 WITA): flag any live desk file that calls Yahoo/yfinance without going through data_hub first.
Static scan: a file 'passes' when it references data_hub (paid primary) wherever it references yfinance/Yahoo endpoints. Runtime: counts
state/data_hub/yahoo_fallback.jsonl rows per caller in the last 24h. Writes reports/yahoo_primary_audit.json. Report-only."""
import datetime as dt, json, os, re
BASE = os.path.dirname(os.path.abspath(__file__)); PAT = re.compile(r"import yfinance|finance\.yahoo\.com|yf\.(download|Ticker)")
SKIP = re.compile(r"\.bak|/backups/|staged_fixes|sandbox|codex_cutover|/tests?/|/test_|\.claude/|\.scratch|/research/|/data/pit_staging|_lab\.py$|/work/")
def scan():
    bad, ok = [], []
    for root, ds, fs in os.walk(BASE):
        ds[:] = [d for d in ds if d not in ("__pycache__", ".git", "backups", "logs", "data", "research", "work", ".claude")]
        for f in fs:
            p = os.path.join(root, f)
            if not f.endswith(".py") or SKIP.search(p): continue
            try: s = open(p, errors="ignore").read()
            except OSError: continue
            if PAT.search(s): (ok if "data_hub" in s else bad).append(os.path.relpath(p, BASE))
    return sorted(bad), sorted(ok)
def runtime():
    p = os.path.join(BASE, "state", "data_hub", "yahoo_fallback.jsonl"); out = {}
    cut = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)
    try:
        for ln in open(p):
            r = json.loads(ln)
            if dt.datetime.fromisoformat(r["utc"]) >= cut: out[r.get("caller") or "?"] = out.get(r.get("caller") or "?", 0) + 1
    except OSError: pass
    return out
if __name__ == "__main__":
    bad, ok = scan(); r = dict(utc=dt.datetime.now(dt.timezone.utc).isoformat(), yahoo_primary=bad, hub_routed=ok, yahoo_fallback_24h=runtime(), n_yahoo_primary=len(bad))
    json.dump(r, open(os.path.join(BASE, "reports", "yahoo_primary_audit.json"), "w"), indent=1)
    print("yahoo-primary live files: %d (hub-routed %d)" % (len(bad), len(ok))); [print("  FLAG", b) for b in bad]
