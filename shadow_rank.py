"""Review fix #7 (SHADOW ONLY): catalyst x relvol rank on the top ~300 US names, stored point-in-time each day so it can be scored
forward for ~20 sessions before anyone promotes it. Nothing here changes ranked.csv, the plan, clearance or arming. PAPER, offline.
shadow_score = relvol_term * move_term * (1 + cat_term), all from the day's scan row (no fabrication; missing -> neutral):
  relvol_term = min(relvol, 10); move_term = |roc5| / max(atr_pct, 0.5) (5d move in ATRs, capped 5); cat_term = cat_score (0..) if researched.
Point-in-time: the day's cat_notes / cat_score / next_earn / headlines are frozen with the row (yfinance is as-of-now, so a later
backfill would look ahead). score() joins frozen rows with edge_cohort-style daily outcomes later; not implemented here."""
import os, csv, json, datetime as dt
TOP_N = 300; UTC = dt.timezone.utc
def f(x, d=0.0):
    try: v = float(x); return d if v != v else v
    except Exception: return d
def shadow_score(r):
    rv = min(max(f(r.get("relvol"), 1.0), 0.0), 10.0); mv = min(abs(f(r.get("roc5"))) / max(f(r.get("atr_pct"), 1.0), 0.5), 5.0)
    return round(rv * mv * (1 + max(f(r.get("cat_score")), 0.0)), 4)
def build(D, ranked_csv, out_dir, top_n=TOP_N):
    os.makedirs(out_dir, exist_ok=True); p = os.path.join(out_dir, f"{D}.json")
    if os.path.exists(p): return p                       # frozen: first build of the day wins
    rows = [r for r in csv.DictReader(open(ranked_csv)) if r.get("venue") == "ibkr"]
    live = {r["sym"]: i for i, r in enumerate(sorted(rows, key=lambda x: -f(x.get("rank_score"))), 1)}
    keep = ("sym", "dir", "px", "atr_pct", "relvol", "roc5", "rank_score", "cat_score", "cat_notes", "next_earn", "earn_days", "headlines", "sector")
    S = sorted(rows, key=lambda r: -shadow_score(r))[:top_n]
    out = [dict({k: r.get(k) for k in keep}, shadow_score=shadow_score(r), shadow_rank=i, live_rank=live.get(r["sym"])) for i, r in enumerate(S, 1)]
    rec = dict(date=D, built_utc=dt.datetime.now(UTC).isoformat(timespec="seconds"), mode="SHADOW - not used for selection", n=len(out),
               researched=sum(1 for r in out if (r.get("cat_notes") or "").strip()), outside_live_top40=sum(1 for r in out[:40] if (r["live_rank"] or 999) > 40), rows=out)
    tmp = p + ".tmp"; json.dump(rec, open(tmp, "w"), indent=1); os.replace(tmp, p); return p
