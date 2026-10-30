import os, sys, json, tempfile, datetime as dt
sys.path.insert(0, os.path.expanduser("~/Applications/MultiVenueDesk"))
from veteran_desk import staff_pool as SP
d = tempfile.mkdtemp(); out = d + "/sp.json"; pay = d + "/pay.json"
json.dump(dict(seats=dict(banks=dict(role="proposer", accrued_commission_aud=150), risk_gate=dict(role="salaried_risk_gate", accrued_commission_aud=0), blind_challenger=dict(role="challenger", accrued_commission_aud=99))), open(pay, "w"))
D = dict(seats=dict(banks=dict(closed=[dict(status="closed", net_aud=1150)], commission_expense_aud=150, commission_forfeit_credit_aud=0)))
t = dt.datetime(2026, 11, 3, tzinfo=dt.timezone.utc)
P = SP.update(D, t, out, pay); assert P["desk_commission_pool_aud"] == 150 and P["perf_accrued_aud"] == 20 and P["hwm_net_aud"] == 1000, P
assert P["staff"][0]["commission_share_aud"] == 25 and abs(P["staff"][0]["perf_cut_aud"] - 3.33) < .01
D["seats"]["banks"]["closed"].append(dict(status="closed", net_aud=-500)); P = SP.update(D, t, out, pay); assert P["perf_accrued_aud"] == 20 and P["below_hwm_aud"] == 500
D["seats"]["banks"]["closed"].append(dict(status="closed", net_aud=700)); P = SP.update(D, t, out, pay); assert P["perf_accrued_aud"] == 24 and P["hwm_net_aud"] == 1200, P
assert P["months"] == {"2026-11": 24.0}; print("STAFF OK")
