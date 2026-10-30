"""Read-only shadow run of the open gates on tonight's ranked US candidates (nothing armed, nothing cut)."""
import sys, json, os, datetime as dt
sys.path.insert(0, "/Users/test/Applications/MultiVenueDesk"); os.chdir("/Users/test/Applications/MultiVenueDesk")
import hunt_orchestrator as HO
OG = HO._og(); phase = sys.argv[1]
o = HO.us_open_today(HO.now_utc()); st = HO.us_backup_state("20261027") or {}
c = [b for b in st.get("main", [])][:8]; cache = {}; out = []
for b in c:
    t = dict(b, direction=b.get("direction", "short"))
    try:
        r = OG.pre_open_check(t, o, "SPY", cache) if phase == "pre" else OG.open_check(t, o, 2500 * 0.65, 30)
    except Exception as e: r = dict(error=str(e)[:150])
    r.pop("new_t", None); out.append(dict(ticker=b["ticker"], trigger=b["trigger"], stop=b["stop"], **{k: v for k, v in r.items() if k != "or_"}, or_=r.get("or_")))
p = f"state/hunt_orch/gates_shadow_20261027_{phase}.json"; HO.atomic(p, dict(phase=phase, wita=HO.wita(HO.now_utc()), open_wita=HO.wita(o), note="SHADOW: read-only, nothing armed/cut", results=out))
for x in out: print(json.dumps(x, default=str)[:300])
