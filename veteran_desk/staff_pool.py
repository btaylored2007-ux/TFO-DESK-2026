"""Veteran Desk ANCILLARY STAFF POOL (Brett 2026-10-28 20:21 WITA: "all ancillary staff share in the commissions of the whole desk.
And get 2% cut overall profit. Like a hedge fund."). PAPER, REPORTING + ACCRUAL ONLY: not P/L, never deducted from any trader seat book.
Staff = non-trading desk roles that do real work (placeholder code seats Sloane Pike / Jonah Grieve / Ada Stroud excluded; traders excluded,
so Quinn Mercer (news_filings trader + news chief) and Nico Hart (tech trader) are not staff - they already earn trader commission).
 (a) Commission share: the whole desk's trader commission pool (sum of proposer accrued_commission_aud in payouts.json, 15% of HWM
     realised net) is mirrored to staff and split EQUALLY (no split existed in code).
 (b) 2% performance cut: 2% of desk realised net profit after costs (sum of closed-trade net_aud, which is after trading costs, minus
     net commission expense), high-water mark: only cumulative profit above the prior peak earns more cut; losses must be won back.
     Accrued per UTC calendar month (same period as payouts.py), split equally."""
import datetime as dt, json, os
from decimal import Decimal, ROUND_HALF_UP
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "state", "veteran_desk", "staff_pool.json")
PAY = os.path.join(BASE, "state", "veteran_desk_payouts.json")
PERF_RATE = Decimal("0.02")
STAFF = [dict(name="Marcus Hale", role="Head of Desk (seats/head_of_desk.json)"),
         dict(name="Sam Okafor", role="Execution trader (book.EXEC_TRADER)"),
         dict(name="Ravi Sandhu", role="News desk - wire (news_desk.TEAM)"),
         dict(name="Elena Park", role="News desk - macro (news_desk.TEAM)"),
         dict(name="Tom Barrett", role="News desk - geo (news_desk.TEAM)"),
         dict(name="Mara Quist", role="News desk - credit (news_desk.TEAM)")]
def _c(x): return Decimal(str(x or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
def _rj(p):
    try: return json.load(open(p))
    except Exception: return None
def update(D, t=None, out=OUT, pay_path=PAY):
    t = t or dt.datetime.now(dt.timezone.utc); P = _rj(out) or dict(hwm_net_aud=0.0, perf_accrued_aud=0.0, months={})
    pay = _rj(pay_path) or {}; ps = pay.get("seats") or {}
    pool = sum(_c(v.get("accrued_commission_aud")) for k, v in ps.items() if v.get("role") == "proposer")
    realised = sum(_c(c.get("net_aud")) for B in D["seats"].values() for c in B.get("closed", []) if c.get("status") == "closed")
    comm = sum(_c(B.get("commission_expense_aud")) - _c(B.get("commission_forfeit_credit_aud")) for B in D["seats"].values())
    net = realised - comm; hwm = _c(P["hwm_net_aud"]); mk = "%04d-%02d" % (t.year, t.month)
    if net > hwm:
        add = _c((net - hwm) * PERF_RATE); P["perf_accrued_aud"] = float(_c(P["perf_accrued_aud"]) + add); P["hwm_net_aud"] = float(net)
        P["months"][mk] = float(_c(P["months"].get(mk, 0)) + add)
    n = len(STAFF); perf = _c(P["perf_accrued_aud"])
    P.update(asof_utc=t.isoformat(), paper_only=True, accounting="reporting/accrual only - not P/L, not deducted from trader seat books",
             staff=[dict(s, commission_share_aud=float(_c(pool / n)), perf_cut_aud=float(_c(perf / n)), total_aud=float(_c(pool / n) + _c(perf / n))) for s in STAFF],
             desk_commission_pool_aud=float(pool), desk_realised_net_after_costs_aud=float(net), below_hwm_aud=float(max(Decimal(0), hwm - net)) if net < hwm else 0.0,
             perf_rate=float(PERF_RATE), rule=__doc__.split("\n", 1)[1].strip())
    tmp = out + ".tmp"; json.dump(P, open(tmp, "w"), indent=1); os.replace(tmp, out); return P
