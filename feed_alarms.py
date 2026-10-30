"""Review fix #3 (feed alarms): a scan crash, kraken_pairs 0 or empty cat_notes must not pass silently (2026-10-27: crypto dropped out
of the US scan with no alarm). Writes state/hunt_orch/feed_alarms.json (latest per market) + feed_alarms.jsonl (history) for the
dashboard / desk driver to show. Local files only: no external paging, no orders. PAPER."""
import os, json, csv, datetime as dt
UTC = dt.timezone.utc; WITA = dt.timezone(dt.timedelta(hours=8))
def _j(p, d=None):
    try: return json.load(open(p))
    except Exception: return d
def check_us_scan(D, ok, note, orch):
    """-> list of dict(code, msg). ok/note = hunt_orchestrator.us_scan() result."""
    out = os.path.join(orch, "us_scan", D); A = []
    if not ok: A.append(dict(code="scan_crash", msg=f"US scan failed: {str(note)[:160]}"))
    R = _j(os.path.join(out, "scan_report.json"))
    if R is None: A.append(dict(code="scan_report_missing", msg=f"no readable scan_report.json in {out}")); return A
    kp = (R.get("universe") or {}).get("kraken_pairs")
    if not kp: A.append(dict(code="kraken_pairs_zero", msg=f"kraken_pairs={kp}: crypto silently out of the US scan"))
    for k, v in sorted((R.get("feeds") or {}).items()):
        if isinstance(v, dict) and v.get("ok") is False: A.append(dict(code="feed_down", feed=k, msg=f"{k}: {str(v.get('note'))[:120]}"))
    try:
        rows = list(csv.DictReader(open(os.path.join(out, "ranked.csv"))))
        n = sum(1 for r in rows if (r.get("cat_notes") or "").strip())
        if rows and n == 0: A.append(dict(code="cat_notes_empty", msg=f"0 of {len(rows)} ranked rows carry cat_notes: catalyst research did not land"))
    except Exception as e: A.append(dict(code="ranked_unreadable", msg=str(e)[:120]))
    return A
def raise_alarms(orch, market, D, alarms, now=None):
    """Record (even an empty list, so a cleared alarm shows as cleared). Returns the record."""
    now = now or dt.datetime.now(UTC); os.makedirs(orch, exist_ok=True)
    rec = dict(market=market, day=D, at_wita=now.astimezone(WITA).strftime("%Y-%m-%d %H:%M:%S WITA"), n=len(alarms), alarms=alarms)
    p = os.path.join(orch, "feed_alarms.json"); S = _j(p, {}) or {}; S[market] = rec
    tmp = p + ".tmp"; json.dump(S, open(tmp, "w"), indent=1); os.replace(tmp, p)
    if alarms:
        with open(os.path.join(orch, "feed_alarms.jsonl"), "a") as f: f.write(json.dumps(rec) + "\n")
    return rec
