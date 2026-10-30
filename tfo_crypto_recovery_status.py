#!/usr/bin/env python3
"""Read-only ledger recovery measurement. Writes a report, never trades."""
import json
from pathlib import Path
from datetime import datetime, timezone
ROOT = Path(__file__).resolve().parent
R = ROOT / "reports"
baseline = json.loads((R / "crypto_recovery_20260920_baseline.json").read_text())
rows=[]
for name, initial in baseline["books"].items():
    b=json.loads((R/(name+".json")).read_text())
    events=b.get("capital_events",[])
    offset=initial["capital_event_offset"]
    if len(events)<offset:
        raise RuntimeError("Capital event history truncated: "+name)
    flows=sum(float(e.get("usd",0)) for e in events[offset:] if e.get("type")=="capital_transfer")
    equity=float(b["equity"])
    adjusted=equity-flows
    pnl=adjusted-initial["target_basis_usd"]
    rows.append({"book":name,"current_equity_usd":equity,"net_inflows_since_start_usd":round(flows,2),"fixed_target_usd":initial["target_basis_usd"],"flow_adjusted_value_usd":round(adjusted,2),"recovery_pnl_usd":round(pnl,2),"green":pnl>=0,"status":b.get("status"),"mark_utc":b.get("last_mark_utc")})
now=datetime.now(timezone.utc)
out={"checked_at":now.isoformat(),"deadline":baseline["deadline"],"deadline_reached":now>=datetime.fromisoformat(baseline["deadline"]),"scope":baseline["scope"],"books":rows,"combined_recovery_pnl_usd":round(sum(x["recovery_pnl_usd"] for x in rows),2),"all_sleeves_green":all(x["green"] for x in rows),"note":"Realized outflows remain counted; new deposits and basis resets do not improve recovery P&L. Ledger marks include only recorded costs; inspect fresh scanner status before relying on them."}
additional=[]
pilot=R/"crypto_sol_short_pilot.json"
if pilot.exists():
    from book_coverage import _load_short_pilot
    check=_load_short_pilot("crypto_sol_short_pilot","SOL simulated recovery pilot",str(pilot))
    if check["error"] or (check["stale"] and check["positions"]):
        raise RuntimeError("Pilot recovery result unverified: "+str(check["error"] or "stale mark"))
    b=json.loads(pilot.read_text())
    net_funding=sum(float(e["usd"]) for e in b["capital_events"] if e.get("type")=="capital_transfer")
    if abs(net_funding-float(b["seed_usd"]))>.02:
        raise RuntimeError("Pilot funding identity does not reconcile")
    additional.append({"book":"crypto_sol_short_pilot","net_profit_usd":round(float(b["equity"])-net_funding,2),
                       "net_funding_usd":round(net_funding,2),"status":b["status"],"mark_utc":b["mark_utc"],
                       "simulated_only":True,"verified_account_access":False})
out["additional_recovery_sleeves"]=additional
out["additional_sleeve_net_profit_usd"]=round(sum(x["net_profit_usd"] for x in additional),2)
out["overall_recovery_pnl_usd"]=round(out["combined_recovery_pnl_usd"]+out["additional_sleeve_net_profit_usd"],2)
out["overall_recovery_gap_usd"]=max(0,round(-out["overall_recovery_pnl_usd"],2))
out["overall_gap_closed"]=out["overall_recovery_pnl_usd"]>=0
p=R/"crypto_recovery_20260920_latest.json";tmp=p.with_suffix(".tmp");tmp.write_text(json.dumps(out,indent=2)+"\n");tmp.replace(p)
print(json.dumps(out,indent=2))
