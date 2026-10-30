import datetime as dt, json, os, sys, copy
sys.path.insert(0, os.path.expanduser("~/Applications/MultiVenueDesk"))
from veteran_desk import leaderboard as LB, book as BK
import tempfile
d = tempfile.mkdtemp(); LB.AWARDS = d+"/a.jsonl"; LB.OPEN = d+"/o.json"; LB.LIVE = d+"/l.json"; LB.SD = d
def mk():
    D = dict(seats={s: dict(BK._seat_ledger(), equity_aud=500000.0) for s in BK.TRADERS}, capital_events=[], seed_aud=5.5e6, start=0, day_start_equity_aud=5.5e6, updated=LB._now().isoformat(), hedge={})
    return D
t0 = dt.datetime(2026,10,28,22,0,tzinfo=LB.UTC); D = mk()
LB.BK = type("X",(),dict(tick=staticmethod(lambda: D), load=staticmethod(lambda: D), TRADERS=BK.TRADERS))
LB._now = lambda: t1
t1 = t0; r = LB.cutoff(t0); assert r["action"] == "init", r
t1 = t0 + dt.timedelta(days=1)
D["seats"]["trend"]["equity_aud"] = 501200; D["seats"]["banks"]["equity_aud"] = 500300; D["seats"]["energy"]["equity_aud"] = 499000; D["updated"] = t1.isoformat()
D["seats"]["crypto"]["equity_aud"] = 500900
D["capital_events"].append(dict(seat="crypto", amount_aud=1000, utc=(t0+dt.timedelta(hours=3)).isoformat()))  # injection is not P/L -> crypto day pnl -100
r = LB.cutoff(t1); assert r["action"] == "awarded", r; w = r["record"]["winners"]
assert [x["seat"] for x in w] == ["trend","banks"] and [x["prize_aud"] for x in w] == [1000,750], w
assert LB.cutoff(t1)["action"] in ("already_done", "too_early")
LB.apply_prizes(D, .7, t1); LB.apply_prizes(D, .7, t1)
assert D["seats"]["trend"]["cash_aud"] == 501000 and D["seats"]["trend"]["seed_aud"] == 501000 and D["seed_aud"] == 5.5e6+1750
assert sum(1 for e in D["capital_events"] if e.get("type")=="leaderboard_prize") == 2
# hold on stale mark
t2 = t1 + dt.timedelta(days=1); t1 = t2; D["updated"] = t2.isoformat()
D["seats"]["macro"]["positions"]["X"] = dict(status="open", ticker="SPY", mark_missing=True); D["seats"]["macro"]["equity_aud"] = 510000
r = LB.cutoff(t2); assert r["action"] == "held" and "MARK_MISSING" in r["record"]["held_reasons"][0], r
assert sum(1 for e in D["capital_events"] if e.get("type")=="leaderboard_prize") == 2
# stale hedge
t3 = t2 + dt.timedelta(days=1); t1 = t3; D["updated"] = t3.isoformat(); D["seats"]["macro"]["positions"] = {}; D["hedge"] = dict(premium_aud=5, value_src="cost", mark_note="stale")
D["seats"]["macro"]["equity_aud"] = 520000; r = LB.cutoff(t3); assert r["action"] == "held" and "VIX" in r["record"]["held_reasons"][0]
# no winners
t4 = t3 + dt.timedelta(days=1); t1 = t4; D["updated"] = t4.isoformat(); D["hedge"] = {}
r = LB.cutoff(t4); assert r["action"] == "no_winners", r
assert all(x["seat"] not in LB.EXCLUDE for x in r["record"]["standings"]) and len(r["record"]["standings"]) == 11
print("ALL OK")
# weekly: in the run above the Friday (2026-10-30) daily cutoff was held -> weekly must be held
W = [a for a in LB._awards() if a.get("kind") == "weekly"]
assert len(W) == 1 and W[0]["status"] == "held" and W[0]["day"] == "week-2026-10-26", W
# fresh week, all clean: Mon..Fri windows 2026-11-02..06
for f in (LB.AWARDS, LB.OPEN): os.remove(f)
D = mk(); D["hedge"] = {}
t1 = dt.datetime(2026,11,1,22,0,tzinfo=LB.UTC); D["updated"] = t1.isoformat(); assert LB.cutoff(t1)["action"] == "init"
eq = {"trend": 500000.0, "banks": 500000.0}
for i, (tr, bk) in enumerate([(100, 900), (100, -800), (100, 100), (100, 0), (100, 0)]):
    t1 = t1 + dt.timedelta(days=1); D["updated"] = t1.isoformat()
    D["seats"]["trend"]["equity_aud"] += tr; D["seats"]["banks"]["equity_aud"] += bk
    r = LB.cutoff(t1)
assert r["record"]["weekly"]["status"] == "awarded" and r["record"]["weekly"]["winners"][0]["seat"] == "trend" and r["record"]["weekly"]["winners"][0]["prize_aud"] == 5000, r["record"]["weekly"]
LB.apply_prizes(D, .7, t1); assert any(e.get("award_id") == "week-2026-11-02#1" and e["amount_aud"] == 5000 for e in D["capital_events"])
print("WEEKLY OK")
# GUARDS: fresh week 2026-11-09..13. banks drops >5% Mon, then big gain Tue -> sits out daily. trend tops week but below its HWM -> crypto wins weekly.
for f in (LB.AWARDS, LB.OPEN): os.remove(f)
D = mk(); D["hedge"] = {}
t1 = dt.datetime(2026,11,8,22,0,tzinfo=LB.UTC); D["updated"] = t1.isoformat(); assert LB.cutoff(t1)["action"] == "init"
steps = [dict(banks=-30000, crypto=100, trend=200), dict(banks=5000, crypto=100, trend=200), dict(banks=0, crypto=100, trend=200), dict(crypto=100, trend=200), dict(crypto=100, trend=200)]
for i, st in enumerate(steps):
    t1 = t1 + dt.timedelta(days=1); D["updated"] = t1.isoformat()
    for k, v in st.items(): D["seats"][k]["equity_aud"] += v
    for k in D["seats"]: D["seats"][k]["pnl_since_inception_aud"] = D["seats"][k]["equity_aud"] - 500000; D["seats"][k]["pnl_hwm_aud"] = max(D["seats"][k].get("pnl_hwm_aud", 0), D["seats"][k]["pnl_since_inception_aud"])
    if i == 4: D["seats"]["trend"]["pnl_hwm_aud"] = 5000   # trend once peaked higher -> below HWM at week end
    r = LB.cutoff(t1)
    if i == 1: assert "banks" in r["record"]["sitting_out_dd"] and all(w["seat"] != "banks" for w in r["record"]["winners"]), r["record"]
    if i == 2: assert "banks" in r["record"]["sitting_out_dd"]   # sticky
wk = r["record"]["weekly"]; assert wk["status"] == "awarded" and wk["winners"][0]["seat"] == "crypto" and wk["skipped_below_hwm"][0]["seat"] == "trend", wk
print("GUARDS OK")
