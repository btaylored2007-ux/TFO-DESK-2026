import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import tfo_24h_target as target


class TargetTests(unittest.TestCase):
    def risk(self,when='2026-09-24T08:17:00Z',equity=1000):
        return {'utc':when,'capital_accounting_valid':True,'coverage_audit_clean':True,
                'books_total':1,'books_readable':1,
                'book_keys':['book'],'firm_equity_usd':equity,'capital_snapshot':{'book':{
                    'present':True,'native_capital':1000,'usd_per_native':1,'ccy':'USD','events':[]}}}

    def test_internal_observation_pnl_does_not_count_funding(self):
        before=self.risk();after=self.risk(equity=1100)
        with patch('firm_capital_flows.reconcile',return_value=(70,[],[])):
            self.assertEqual(target.flow_adjusted(before,after)[:2],(30,70))

    @unittest.skip("17 Oct 2026: stale - the one-time $100k/24h stretch (24 Sep) was retired 25 Sep; update() now reports the rolling $15k/Sydney-day mandate (tfo_24h_target.update docstring). Baseline/preorder rules of the retired stretch no longer exist.")
    def test_baseline_does_not_reset_and_deadline_does_not_slide(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);r=base/'reports';r.mkdir();(r/'private_wealth_cycles').mkdir()
            p=r/'firm_risk.json';p.write_text(json.dumps(self.risk()))
            first=target.update(base,True,dt.datetime.fromisoformat('2026-09-24T08:18:00+00:00'))
            p.write_text(json.dumps(self.risk('2026-09-24T08:19:00Z',1200)))
            second=target.update(base,True,dt.datetime.fromisoformat('2026-09-24T08:20:00+00:00'))
            self.assertEqual(first['baseline_equity_usd'],second['baseline_equity_usd'])
            self.assertEqual(second['pnl_usd'],200);self.assertEqual(second['deadline_utc'],target.DEADLINE_UTC)
            p.write_text(json.dumps(self.risk('2026-09-25T08:17:00Z',90000)))
            final=target.update(base,now=dt.datetime.fromisoformat('2026-09-25T08:18:00+00:00'))
            self.assertEqual(final['status'],'EXPIRED_LAST_OBSERVATION');self.assertEqual(final['pnl_usd'],200)

    @unittest.skip("17 Oct 2026: stale - the one-time $100k/24h stretch (24 Sep) was retired 25 Sep; update() now reports the rolling $15k/Sydney-day mandate (tfo_24h_target.update docstring). Baseline/preorder rules of the retired stretch no longer exist.")
    def test_rejects_preorder_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);r=base/'reports';r.mkdir()
            (r/'firm_risk.json').write_text(json.dumps(self.risk('2026-09-24T08:15:00Z')))
            with self.assertRaises(ValueError):target.update(base,True,dt.datetime.fromisoformat('2026-09-24T08:18:00+00:00'))

    def test_unknown_booked_pnl_stays_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);a=r/'private_wealth_cycles';a.mkdir()
            (a/'one.json').write_text(json.dumps({'desk_scope':'LOCAL_ONLY','paper_tickets':[{'ticket_id':'one','kind':'EXIT','paper_only':True,'verified':True,'utc':'2026-09-24T08:18:00Z','booked_pnl_usd':None}]}))
            result=target.booked_receipts(r,target.epoch(target.ORDER_UTC),target.epoch(target.DEADLINE_UTC))
            self.assertIsNone(result['booked_pnl_usd']);self.assertEqual(result['unknown_booked_ticket_count'],1)

    def test_bad_coverage_and_boolean_equity_rejected(self):
        risk=self.risk();risk['books_readable']=0;self.assertFalse(target.valid_risk(risk))
        risk=self.risk();risk['firm_equity_usd']=True;self.assertFalse(target.valid_risk(risk))


if __name__=='__main__':unittest.main()
