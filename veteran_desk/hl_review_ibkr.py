"""2026-10-29 flagmode PAPER: batch IBKR check of pit bar_review flags. Read-only IBKR (ibkr_bar_check.py: readonly client, reqHistoricalData only).
Clips ONLY bars IBKR confirms bad (same decide() rule as hl_clip_20261029.py); bars IBKR shows traded stay raw. Crypto is never checked or clipped.
Usage:
  python -m veteran_desk.hl_review_ibkr check [--apply] [--db PATH] [--ibkr-json FILE]   pending flags -> IBKR -> clip/keep (dry-run default)
  python -m veteran_desk.hl_review_ibkr seed  [--apply] [--db PATH]                      load hl_clip_applied_20261029.json verdicts into bar_review
  python -m veteran_desk.hl_review_ibkr restored [--apply] [--db PATH]                   re-check the 27 IBKR-restored bars; restore any re-clipped
Take a pit.sqlite backup before --apply. py3.9-compatible."""
import datetime as dt, json, os, sqlite3, subprocess, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "pit", "pit.sqlite")
APPLIED = os.path.join(ROOT, "data", "pit", "hl_clip_applied_20261029.json")
IBPY = os.path.expanduser("~/ibkr/venv/bin/python"); CHECKER = os.path.join(ROOT, "veteran_desk", "ibkr_bar_check.py")
sys.path.insert(0, ROOT)
from veteran_desk.hl_clip_20261029 import decide, is_crypto
from veteran_desk.pit import REVIEW_DDL
def _now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def _close(a, b): return a is not None and b is not None and abs(a - b) <= 1e-6 * max(1.0, abs(b))
def _ddl(c):
    try: c.execute(REVIEW_DDL); return True
    except sqlite3.OperationalError: return bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='bar_review'").fetchone())   # read-only dry run
def pending(c):
    if not _ddl(c): return []
    return [r for r in c.execute("SELECT sym,date,o,h,l,c,flags FROM bar_review WHERE status='pending' ORDER BY sym,date") if not is_crypto(r[0])]
def run_ibkr(req, timeout=1800):
    d = tempfile.mkdtemp(prefix="hlreview_"); fi, fo = os.path.join(d, "in.json"), os.path.join(d, "out.json")
    json.dump(req, open(fi, "w")); subprocess.run([IBPY, CHECKER, fi, fo], capture_output=True, text=True, timeout=timeout)
    return json.load(open(fo)) if os.path.exists(fo) else {}
def check(c, ib=None, apply_=False, tag="hlreview_ibkr"):
    """ib: {sym: {'bars': {date: [o,h,l,c,v]}} | {'err':..}} as written by ibkr_bar_check.py; fetched when None."""
    rows = pending(c); req = {}
    for r in rows: req.setdefault(r[0], []).append(r[1])
    if ib is None: ib = run_ibkr(req) if req else {}
    res = dict(clipped=[], kept=[], no_ibkr=[], skipped=[])
    for s, d, o, h, l, cl, flags in rows:
        cur = c.execute("SELECT o,h,l,c FROM bars WHERE sym=? AND date=?", (s, d)).fetchone()
        if not cur or not (_close(cur[1], h) and _close(cur[2], l)): res["skipped"].append(dict(sym=s, date=d, why="bar changed since flag")); continue
        sides = set(f.split()[0] for f in json.loads(flags or "[]") if f.split()[0] in ("high", "low"))
        v = ib.get(s) or {}; bar = (v.get("bars") or {}).get(d)
        dc = decide((o, h, l, cl), sides, bar, False); rec = dict(sym=s, date=d, old=dict(h=h, l=l), new=dict(h=dc["new_h"], l=dc["new_l"]), why=dc["why"], ibkr=bar, ibkr_err=v.get("err"))
        if dc["action"] == "clip": st = "ibkr_bad"; res["clipped"].append(rec)
        elif bar and dc["ibkr_scale"]: st = "ibkr_ok"; res["kept"].append(rec)
        else: st = "no_ibkr"; res["no_ibkr"].append(rec)
        if apply_:
            if st == "ibkr_bad":
                u = c.execute("UPDATE bars SET h=?, l=?, src=COALESCE(src,'')||? WHERE sym=? AND date=? AND h=? AND l=?",
                              (dc["new_h"], dc["new_l"], "|%s(h %g->%g,l %g->%g)" % (tag, h, dc["new_h"], l, dc["new_l"]), s, d, cur[1], cur[2]))
                rec["applied"] = u.rowcount == 1
            c.execute("UPDATE bar_review SET status=?, fix_h=?, fix_l=?, ibkr=?, why=?, resolved_utc=? WHERE sym=? AND date=?",
                      (st, dc["new_h"] if st == "ibkr_bad" else None, dc["new_l"] if st == "ibkr_bad" else None, json.dumps(bar), json.dumps(dc["why"]), _now(), s, d))
    if apply_: c.commit()
    res["counts"] = {k: len(v) for k, v in res.items()}; return res
def seed(c, applied_path=APPLIED, apply_=False):
    """Record the 14:49 hlclip verdicts so flag mode re-applies IBKR fixes and does not re-queue IBKR-ok bars."""
    _ddl(c); J = json.load(open(applied_path)); n = dict(ibkr_bad=0, hand_bad=0, ibkr_ok=0, no_ibkr=0)
    def put(x, st, raw_h, raw_l, fh=None, fl=None):
        o = x["old"]; n[st] += 1
        if apply_:
            c.execute("INSERT OR REPLACE INTO bar_review(sym,date,o,h,l,c,atr,flags,src,status,fix_h,fix_l,ibkr,why,flagged_utc,resolved_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (x["sym"], x["date"], o["o"], raw_h, raw_l, o["c"], None, json.dumps(list((x.get("why") or {}).keys())), o.get("src"), st, fh, fl,
                       json.dumps(x.get("ibkr_bar")), json.dumps(x.get("why")), "2026-10-29T06:49:32+00:00", _now()))
    for x in J["clipped"]:
        if is_crypto(x["sym"]): continue
        hand_only = x.get("hand") and not any("ibkr_confirms_bad" in w for w in (x.get("why") or {}).values())
        put(x, "hand_bad" if hand_only else "ibkr_bad", x["old"]["h"], x["old"]["l"], x["new"]["h"] if x["new"]["h"] < x["old"]["h"] else None, x["new"]["l"] if x["new"]["l"] > x["old"]["l"] else None)
    for x in J["restored"]: put(x, "ibkr_ok", x["new"]["h"], x["new"]["l"])
    for x in J["kept"]:
        w = " ".join((x.get("why") or {}).values())
        if "ibkr_shows_traded" in w: put(x, "ibkr_ok", x["old"]["h"], x["old"]["l"])
        elif "no_ibkr" in w or "mismatch" in w: put(x, "no_ibkr", x["old"]["h"], x["old"]["l"])
    if apply_: c.commit()
    return n
def recheck_restored(c, applied_path=APPLIED, apply_=False):
    """The 27 bars IBKR showed really traded: if a blind ingest clamp re-clipped them (h/l back at the body edge), put the IBKR-verified extreme back."""
    J = json.load(open(applied_path)); out = dict(ok=[], reclipped=[], missing=[])
    for x in J["restored"]:
        s, d, nh, nl = x["sym"], x["date"], x["new"]["h"], x["new"]["l"]
        cur = c.execute("SELECT o,h,l,c FROM bars WHERE sym=? AND date=?", (s, d)).fetchone()
        if not cur: out["missing"].append((s, d)); continue
        if _close(cur[1], nh) and _close(cur[2], nl): out["ok"].append((s, d)); continue
        rec = dict(sym=s, date=d, cur=dict(h=cur[1], l=cur[2]), restore=dict(h=nh, l=nl)); out["reclipped"].append(rec)
        if apply_:
            u = c.execute("UPDATE bars SET h=?, l=?, src=COALESCE(src,'')||? WHERE sym=? AND date=? AND h=? AND l=?",
                          (max(nh, cur[1]), min(nl, cur[2]), "|hlrestore2_20261029(h %g->%g,l %g->%g ibkr traded)" % (cur[1], max(nh, cur[1]), cur[2], min(nl, cur[2])), s, d, cur[1], cur[2]))
            rec["applied"] = u.rowcount == 1
    if apply_: c.commit()
    out["counts"] = {k: len(v) for k, v in out.items()}; return out
def main(argv):
    cmd = argv[0]; apply_ = "--apply" in argv
    db = argv[argv.index("--db") + 1] if "--db" in argv else DB
    ibf = argv[argv.index("--ibkr-json") + 1] if "--ibkr-json" in argv else None
    c = sqlite3.connect(db, timeout=300) if apply_ else sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=300)
    if cmd == "check":
        r = check(c, json.load(open(ibf)) if ibf else None, apply_)
    elif cmd == "seed": r = seed(c, apply_=apply_)
    elif cmd == "restored": r = recheck_restored(c, apply_=apply_)
    else: raise SystemExit(__doc__)
    print(json.dumps(r, default=str)[:20000])
if __name__ == "__main__": main(sys.argv[1:])
