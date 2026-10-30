import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import carry_economic_hurdle_lab as lab


class CarryEconomicHurdleTests(unittest.TestCase):
    def setUp(self):
        self.spec=json.loads(lab.SPEC.read_text())

    def test_hourly_loader_rejects_duplicate_and_nonfinite(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"rates.json"
            row={"timestamp":"2026-01-01T00:00:00Z","relativeFundingRate":.001}
            p.write_text(json.dumps({"result":"success","rates":[row,row]}))
            with self.assertRaisesRegex(ValueError,"duplicate"): lab.load_hourly(p)
            row['relativeFundingRate']=float('nan')
            p.write_text(json.dumps({"result":"success","rates":[row]}))
            with self.assertRaisesRegex(ValueError,"non-finite"): lab.load_hourly(p)

    def test_no_missing_hour_interpolation_or_partial_day(self):
        a={i*3600:.001 for i in range(120)}
        b=dict(a);b.pop(30*3600)
        hours,data,coverage=lab.joint_complete_block([a,b])
        self.assertEqual(coverage['chosen_days'],3)
        self.assertEqual(hours[0],48*3600)
        self.assertEqual(data.shape,(72,2))

    def test_no_same_settlement_lookahead(self):
        f=np.full((24*40,2),.01/8760)
        i=24*33
        f[i]=.40/8760
        s=lab.simulate(f,'current_rule_control',1,90,self.spec)
        offset=s['warmup_hours']
        self.assertEqual(s['deployed'][i-offset],0)
        self.assertEqual(s['deployed'][i+1-offset],1)
        with self.assertRaises(ValueError): lab.simulate(f,'economic_btc',0,90,self.spec)

    def test_cash_collateral_fee_conservation(self):
        f=np.full((24*40,2),.04/8760)
        s=lab.simulate(f,'buy_hold_control',1,90,self.spec)
        hours=len(s['net'])
        self.assertAlmostEqual(s['net'].sum(),.75*(.04*hours/8760-.009),places=10)
        self.assertEqual(s['entries'],2)
        self.assertEqual(s['cycles'][-1]['complete'],False)
        self.assertAlmostEqual(s['gross'].sum()-s['net'].sum(),.75*.009,places=10)

    def test_candidate_budget_cap_under_staggered_admission(self):
        f=np.full((24*40,2),.20/8760)
        i=24*33
        f[i]=[.70/8760,.005/8760]
        f[i+1]=[.40/8760,.40/8760]
        control=lab.simulate(f,'current_rule_control',1,90,self.spec)
        candidate=lab.simulate(f,'economic_basket',1,90,self.spec)
        self.assertEqual(control['max_funding_notional_vs_budget'],2)
        self.assertLessEqual(candidate['max_funding_notional_vs_budget'],1)

    def test_zero_cycles_never_become_twenty_independent_bets(self):
        ne,detail=lab.neff_and_cycles(np.linspace(-.001,.001,100),[],[])
        self.assertEqual(detail['completed_cycles'],0)
        self.assertEqual(ne,2)

    def test_settlement_collection_append_only_rejects_revision(self):
        row={"timestamp":"2026-01-01T00:00:00Z","relativeFundingRate":.001}
        old={'result':'success','rates':[row]}
        new={'result':'success','rates':[row,dict(row,timestamp='2026-01-01T01:00:00Z')]}
        merged,added=lab.merge_settlements(old,new)
        self.assertEqual(added,1)
        self.assertEqual(len(merged['rates']),2)
        new['rates'][0]=dict(row,relativeFundingRate=.002)
        with self.assertRaisesRegex(ValueError,'revision'): lab.merge_settlements(old,new)

    def test_collector_refuses_original_cache(self):
        with self.assertRaisesRegex(ValueError,'original research cache'):
            lab.collect_settlements(lab.ROOT/'reports/carry_funding_cache')


if __name__=='__main__': unittest.main()
