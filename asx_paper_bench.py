#!/usr/bin/env python3
"""ASX PAPER BENCH (PAPER ONLY) - simulated fills for ASX setups that win a top-3 slot in event_calendar.py.
Own ledger state/calendar/asx_paper_ledger.json; no broker, no orders, no keys. Fills on Yahoo '.AX' 1h bars (bars exist only in
ASX hours 10:00-16:00 AEDT = 07:00-13:00 WITA). Same step() rules as cal_paper_bench (close through trigger within max_fill, stop,
half at T1 -> stop to entry, rest at T2, 72h time exit). Costs = staged est_cost_usd (A$10/side + 10bp/side + half-tick/side).
MAX 3 open PER BOOK (Brett 27 Oct 2026 19:35 WITA, audit H8) = this ledger's FILLED only (CB.book_open); if full -> SHADOW."""
import datetime as dt, json, os
import cal_paper_bench as CB
import ibkr_costs as IC   # 2026-10-27: real IBKR ASX Fixed 0.08% min A$6 (was A$10/side) + IBKR per-name borrow; no borrow -> SHADOW
BASE = os.environ.get("DESK_BASE", os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(BASE, "state", "calendar", "asx_paper_ledger.json"); STATUS = os.path.join(BASE, "static", "asx_paper_bench.json")
def ibkr_est_cost_usd(s, q2u, hold_days=3):
    """staged cost with the A$10/side broker leg swapped for IBKR Fixed (0.08%, min A$6) + IBKR borrow for shorts (3-day hold)."""
    n_aud = s["shares"] * s["max_fill"]; c = s["est_cost_usd"] - q2u * 2 * 10.0 + q2u * 2 * IC.asx_side_aud(s["shares"], s["max_fill"])
    if s["direction"] == "short": c += q2u * n_aud * IC.borrow_pa(s["sym"] + ".AX", "asx") * hold_days / 365
    return round(c, 2)
def run(now=None, fetch=CB.bars_yf, cal=None, base=BASE):
    now = now or CB.now_utc(); L = CB.load(LEDGER, dict(book="ASX_PAPER_BENCH", paper_only=True, trades=[]))
    cal = cal or CB.load(os.path.join(base, "static", "event_calendar.json"), {})
    active = {t["sym"] for t in L["trades"] if t["status"] in ("ARMED", "FILLED") or t.get("shadow_reason") == "no borrow"}
    for s in cal.get("staged", []):
        if s.get("venue") != "asx" or not s.get("top3") or s["sym"] in active or not s.get("shares"): continue
        q2u = s["notional_usd"] / (s["shares"] * s["max_fill"])
        L["trades"].append(dict(id=len(L["trades"]) + 1, sym=s["sym"], yahoo=s["sym"], direction=s["direction"], status="ARMED", armed_utc=now.isoformat(),
                                trigger=s["trigger"], max_fill=s["max_fill"], stop=s["stop"], t1=s["t1"], t2=s["t2"], shares=s["shares"], q2usd=q2u,
                                est_cost_usd=ibkr_est_cost_usd(s, q2u), est_cost_usd_staged=s["est_cost_usd"], risk_aud=s["risk_aud"], events=s.get("events", [])[:3], ccy="AUD"))
        t = L["trades"][-1]
        if t["direction"] == "short" and not IC.has_borrow(t["sym"] + ".AX", t["shares"], "asx"): t.update(status="SHADOW", shadow_reason="no borrow")
    slots = CB.MAX_OPEN - CB.book_open(L["trades"])   # audit H8: per book (was: minus every hunt48 book + the FX calendar bench)
    for t in L["trades"]:
        if t["status"] not in ("ARMED", "FILLED"): continue
        try:
            was = t["status"] == "FILLED"
            slots = CB.step(t, fetch(t["yahoo"], dt.datetime.fromisoformat(t["armed_utc"])), now, slots + (1 if was else 0)) - (1 if was and t["status"] == "FILLED" else 0)
        except Exception as e: t["last_error"] = str(e)[:120]
    L["updated_utc"] = now.isoformat(); CB.atomic(LEDGER, L)
    closed = [t for t in L["trades"] if t["status"] == "CLOSED"]
    st = dict(updated_utc=now.isoformat(), paper_only=True, open_slots_left=max(slots, 0), counts={k: sum(t["status"] == k for t in L["trades"]) for k in ("ARMED", "FILLED", "CLOSED", "SHADOW", "MISSED", "EXPIRED")},
              net_aud_closed=round(sum(t.get("net_aud", 0) for t in closed), 2), trades=L["trades"][-30:])
    CB.atomic(STATUS, st); return st
if __name__ == "__main__": print(json.dumps(run()["counts"]))
