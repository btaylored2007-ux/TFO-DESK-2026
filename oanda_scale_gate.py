#!/usr/bin/env python3
"""oanda_scale_gate.py - FROZEN scale-up gate for the TFO OANDA PRACTICE sleeve (Brett approved 12 Oct 2026 21:41 AEDT).
Risk per trade starts at 0.5% NAV. It steps to 0.75%, then 1.0%, ONLY when the frozen rules in state/oanda/SCALE_GATE_FROZEN.json pass:
  >= 30 closed TFO trades in the evaluation set, spanning >= 4 distinct currencies (legs of the pairs traded, XAU excluded),
  net P&L after costs > 0 (OANDA realizedPL already includes the bid/ask spread paid; broker financing is added), and
  net P&L still > 0 after removing the single best trade.
Step 1 (0.5% -> 0.75%): evaluation set = every closed TFO trade.  Step 2 (0.75% -> 1.0%): evaluation set = only trades opened at >= 0.75%
risk (a fresh >= 30), and step 1 must already be passed. Never above 1.0%. If the frozen file is missing or its hash does not match, risk = 0.5%.
Modes:  python3 oanda_scale_gate.py evaluate   (read-only OANDA GETs -> state/oanda/scale_gate_status.json; never changes orders)
        python3 oanda_scale_gate.py show"""
import os, sys, json, hashlib, datetime as dt
ROOT = os.path.dirname(os.path.abspath(__file__)); ST_DEFAULT = os.path.join(ROOT, "state", "oanda")
RULES = {"version": 1, "frozen_utc": "2026-10-12T10:41:00+00:00", "approved_by": "Brett 12 Oct 2026 21:41 AEDT",
         "base_risk_pct": 0.005, "steps": [0.0075, 0.01], "max_risk_pct": 0.01,
         "min_closed_trades": 30, "min_currencies": 4, "net_after_costs_gt": 0.0, "net_ex_best_trade_gt": 0.0,
         "costs": "net = OANDA realizedPL (spread embedded in fills) + trade financing; AUD",
         "step2_set": "closed trades whose entry risk_aud/nav_aud >= 0.0075 (opened at the 0.75% step)"}
def rules_hash(r): return hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest()
def frozen_path(st): return os.path.join(st, "SCALE_GATE_FROZEN.json")
def status_path(st): return os.path.join(st, "scale_gate_status.json")
def write_frozen(st=ST_DEFAULT):
    p = frozen_path(st)
    if os.path.exists(p): raise SystemExit("frozen gate already exists; it is never rewritten: " + p)
    json.dump(dict(rules=RULES, sha256=rules_hash(RULES)), open(p, "w"), indent=1); os.chmod(p, 0o444); return p
def load_frozen(st=ST_DEFAULT):
    p = frozen_path(st)
    if not os.path.exists(p): return None
    j = json.load(open(p))
    if j.get("sha256") != rules_hash(j.get("rules", {})) or j["rules"] != RULES: return None   # tampered or drifted -> no scaling
    return j["rules"]
def ccys(ins): return [c for c in ins.split("_") if c != "XAU"]
def evaluate(trades, r=RULES):
    """trades: list of dict(instrument, net_aud). Returns dict(pass, n, currencies, net, net_ex_best, reasons)."""
    n = len(trades); net = sum(float(t["net_aud"]) for t in trades)
    best = max((float(t["net_aud"]) for t in trades), default=0.0); ex = net - best if n else 0.0
    cs = sorted({c for t in trades for c in ccys(t["instrument"])})
    why = []
    if n < r["min_closed_trades"]: why.append("closed trades %d < %d" % (n, r["min_closed_trades"]))
    if len(cs) < r["min_currencies"]: why.append("currencies %d < %d" % (len(cs), r["min_currencies"]))
    if not net > r["net_after_costs_gt"]: why.append("net after costs %.2f <= 0" % net)
    if not ex > r["net_ex_best_trade_gt"]: why.append("net ex best trade %.2f <= 0" % ex)
    return dict(passed=not why, n=n, currencies=cs, net_aud=round(net, 2), net_ex_best_aud=round(ex, 2), best_aud=round(best, 2), reasons=why)
def decide(all_trades, step2_trades, r=RULES):
    s1 = evaluate(all_trades, r); s2 = evaluate(step2_trades, r) if s1["passed"] else None
    risk = r["base_risk_pct"]
    if s1["passed"]: risk = r["steps"][0]
    if s1["passed"] and s2 and s2["passed"]: risk = r["steps"][1]
    return dict(allowed_risk_pct=min(risk, r["max_risk_pct"]), step1=s1, step2=s2)
def allowed_risk_pct(st=ST_DEFAULT):
    """Used by oanda_sleeve.cfg(): 0.5% unless the frozen gate is intact AND the last evaluate() passed. Never raises."""
    try:
        r = load_frozen(st)
        if r is None: return RULES["base_risk_pct"]
        s = json.load(open(status_path(st)))
        if s.get("rules_sha256") != rules_hash(r): return r["base_risk_pct"]
        return min(float(s.get("allowed_risk_pct", r["base_risk_pct"])), r["max_risk_pct"])
    except Exception:
        return RULES["base_risk_pct"]
def fetch_closed(st=ST_DEFAULT):
    """Read-only: closed TFO trades from OANDA (realizedPL + financing), entry risk from the ledger."""
    import oanda_sleeve as S
    led = {}; bug = set()
    lp = os.path.join(st, "trades.jsonl")
    if os.path.exists(lp):
        for ln in open(lp):
            try: x = json.loads(ln)
            except Exception: continue
            if x.get("event") in ("close_fill", "close_order") and "score None" in str(x.get("reason") or "") and x.get("trade_id"):
                bug.add(str(x["trade_id"]))   # 13 Oct 2026 audit #2: closed by the missing-data (None score) defect, not by the strategy
            if x.get("event") == "entry_order" and x.get("live") and (x.get("response") or {}).get("trade_id") and x.get("nav_aud"):
                led[str(x["response"]["trade_id"])] = float(x["risk_aud"]) / float(x["nav_aud"])
    out = []
    rows = S.closed_tfo_trades() if hasattr(S, "closed_tfo_trades") else S.api("GET", S.acct("/trades"), params={"state": "CLOSED", "count": 500}).get("trades", [])
    for t in rows:   # 13 Oct 2026: paginated (all closed TFO trades)
        if not S.is_tfo(t): continue
        out.append(dict(trade_id=t["id"], instrument=t["instrument"], realized_aud=float(t.get("realizedPL") or 0),
                        financing_aud=float(t.get("financing") or 0), net_aud=float(t.get("realizedPL") or 0) + float(t.get("financing") or 0),
                        risk_frac=led.get(str(t["id"])), close_utc=t.get("closeTime"),
                        excluded_bug=str(t["id"]) in bug))   # kept in the list, never deleted; excluded from the evaluation set
    return out
def run_evaluate(st=ST_DEFAULT):
    r = load_frozen(st)
    if r is None: raise SystemExit("frozen gate missing or hash mismatch -> risk stays 0.5%")
    tr = fetch_closed(st); ev = [t for t in tr if not t.get("excluded_bug")]   # 13 Oct 2026 audit #2: bug-closed trades flagged, not counted
    s2 = [t for t in ev if (t["risk_frac"] or 0) >= 0.0075 - 1e-6]
    d = decide(ev, s2, r); d["excluded_bug_ids"] = [t["trade_id"] for t in tr if t.get("excluded_bug")]; d.update(asof_utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), rules_sha256=rules_hash(r), trades=tr)
    tmp = status_path(st) + ".tmp"; json.dump(d, open(tmp, "w"), indent=1); os.replace(tmp, status_path(st)); return d
if __name__ == "__main__":
    m = sys.argv[1] if len(sys.argv) > 1 else "show"
    if m == "freeze": print(write_frozen())
    elif m == "evaluate":
        d = run_evaluate(); print(json.dumps({k: v for k, v in d.items() if k != "trades"}, indent=1))
    else: print(json.dumps(dict(frozen=load_frozen(), allowed_risk_pct=allowed_risk_pct()), indent=1))
