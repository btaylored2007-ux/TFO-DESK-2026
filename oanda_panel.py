"""oanda_panel.py - read-only summary of the OANDA PRACTICE sleeve for the desk dashboard (2026-10-04).
Reads state/oanda/snapshot.json written by oanda_sleeve.py. PAPER / practice account: NOT desk equity, NOT firm_risk, NOT funder fee."""
import os, json, datetime as dt
BASE = os.path.dirname(os.path.abspath(__file__)); ST = os.path.join(BASE, "state", "oanda")
def summary(n_log=12):
    p = os.path.join(ST, "snapshot.json")
    if not os.path.exists(p): return {"status": "NO_SNAPSHOT", "label": "OANDA PRACTICE - PAPER", "paper_only": True}
    s = json.load(open(p)); s["status"] = "OK"; s["paper_only"] = True; s["in_desk_equity"] = False
    try:
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(s["asof_utc"])).total_seconds(); s["age_s"] = round(age)
    except Exception: s["age_s"] = None
    # 13 Oct 2026 audit #13: AEDT time, staleness, book vs trend counts, live vs shadow labels
    try:
        from zoneinfo import ZoneInfo
        s["asof_local"] = dt.datetime.fromisoformat(s["asof_utc"]).astimezone(ZoneInfo("Australia/Sydney")).strftime("%a %d %b %H:%M %Z")
    except Exception: s["asof_local"] = None
    s["stale"] = s["age_s"] is None or s["age_s"] > 1800
    try:   # 25 Oct 2026: reconcile guard -> OANDA book UNVERIFIED while failing; marks not counted
        import oanda_reconcile_guard as RG; s.update(RG.board(ST))
    except Exception: s.update(verified=False, book_label="UNVERIFIED", reconcile_status="UNKNOWN")
    tr = [t for t in s.get("trades", []) if t.get("tfo")]
    s["trend_open"] = sum(1 for t in tr if t.get("design") == "OXF1"); s["book_open"] = len(tr) - s["trend_open"]
    s.setdefault("trend_max_trades", 4)
    s["live_designs"] = list(s.get("designs") or [])
    s["shadow_only"] = ["GPT currency caps (net 1% / gross 1.5% / 8% vol)", "Part A TSMOM+carry score", "constant-0.5% net-R track"]
    lp = os.path.join(ST, "trades.jsonl"); ev = []
    if os.path.exists(lp):
        for ln in open(lp).read().splitlines()[-200:]:
            try:
                r = json.loads(ln)
                if r.get("event") == "stop_update" and (r.get("live") is False or r.get("error") or r.get("confirmed") is False): continue   # 5 Oct 2026: only broker-confirmed stops
                if r.get("event") in ("entry_order", "close_fill", "close_order", "stop_update", "stop_update_failed", "stop_reconcile", "entry_dry", "error"): ev.append(r)
            except Exception: pass
    s["recent"] = ev[-n_log:]
    try:   # 28 Oct 2026 (Brett 13:14 WITA): active OANDA paper ruleset
        s["active_ruleset"] = "AGGRO" if str(json.load(open(os.path.join(ST, "config.json"))).get("active_ruleset") or "").upper() == "AGGRO" else "DESIGN"
    except Exception: s["active_ruleset"] = "DESIGN"
    try:   # 16 Oct 2026: AGGRO sleeve (whole-account notional, +5%/week target) - weekly % vs target
        s["aggro"] = json.load(open(os.path.join(BASE, "state", "oanda_aggro", "stats.json")))
    except Exception: s["aggro"] = None
    return s
