"""Daily accounting checks on synthetic reporting fixtures only."""
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tfo_daily_pnl as daily


class DailyTests(unittest.TestCase):
    def test_authorized_proxy_does_not_certify_profit_or_allow_changed_baseline(self):
        self.base['utc'] = '2026-09-20T13:59:00Z'
        self.base['book_equity_usd'] = {'a': 50000, 'b': 50000}
        self.risk['book_equity_usd'] = {'a': 52000, 'b': 50000}
        auth = {'paper_only': True, 'performance_remains_estimated': True,
            'approved_utc': '2026-09-21T07:00:00Z', 'expires_utc': '2026-09-21T14:00:00Z',
            'sydney_date': '2026-09-21', 'baseline_utc': self.base['utc'],
            'baseline_equity_usd': 100000, 'loss_limit_pct': -3.0,
            'baseline_sha256': daily.hashlib.sha256(json.dumps(self.base, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()}
        got = daily.calculate(self.base, self.risk, self.now)
        daily.apply_baseline_authorization(got, self.base, auth, self.now)
        self.assertTrue(got['daily_risk_basis_allowed'])
        self.assertFalse(got['current_daily_risk_verified'])
        self.assertEqual(got['status'], 'PROXY')
        self.assertIsNone(got['target_achieved'])
        for change in ({'baseline_sha256': 'wrong'}, {'expires_utc': '2026-09-21T07:59:00Z'},
                       {'paper_only': False}, {'loss_limit_pct': -30}):
            test = daily.calculate(self.base, self.risk, self.now)
            daily.apply_baseline_authorization(test, self.base, dict(auth, **change), self.now)
            self.assertFalse(test['daily_risk_basis_allowed'])

    def setUp(self):
        self.now=datetime(2026,9,21,8,0,tzinfo=timezone.utc)
        self.base={'utc':'2026-09-20T14:00:00Z','firm_equity_usd':100000,
            'capital_accounting_valid':True,'drawdown_valid':True,
            'books_readable':2,'books_total':2,'book_keys':['a','b'],
            'capital_snapshot':{k:{'present':True,'ccy':'USD','native_capital':50000,
                'usd_per_native':1,'events':[]} for k in ['a','b']}}
        self.risk=copy.deepcopy(self.base);self.risk.update(utc='2026-09-21T07:59:00Z',firm_equity_usd=102000)

    def flow(self,book,amount,kind,identity,source=None,target=None):
        meta=self.risk['capital_snapshot'][book]
        meta['native_capital']+=amount
        meta['events'].append({'id':identity,'book':book,'usd':amount,'utc':'2026-09-21T07:00:00Z',
            'type':kind,'from':source,'to':target})

    def test_salary_is_not_loss_and_percentage_uses_opening_equity(self):
        self.flow('a',-17810.63,'capital_withdrawal','salary')
        self.risk['firm_equity_usd']-=17810.63
        got=daily.calculate(self.base,self.risk,self.now)
        self.assertEqual((got['status'],got['pnl_usd'],got['pnl_pct']),('VERIFIED',2000,2))
        self.assertEqual(got['net_external_flow_usd'],-17810.63)

    def test_verified_retirement_preserves_daily_comparison_and_attribution(self):
        for row in (self.base, self.risk):
            row['capital_snapshot'] = {
                'coinspot_floor': row['capital_snapshot']['a'],
                'gap_close_levered_book': row['capital_snapshot']['b']}
            row['book_keys'] = ['coinspot_floor', 'gap_close_levered_book']
        self.base['book_equity_usd'] = {'coinspot_floor': 50000., 'gap_close_levered_book': 50000.}
        self.flow('coinspot_floor', -50000., 'capital_transfer', 'out',
                  'coinspot_floor', 'gap_close_levered_book')
        self.flow('gap_close_levered_book', 50000., 'capital_transfer', 'in',
                  'coinspot_floor', 'gap_close_levered_book')
        meta = self.risk['capital_snapshot']['coinspot_floor']
        meta.update(present=False, verified_retirement={
            'transfer_usd': 50000., 'transfer_utc': '2026-09-21T07:00:00Z',
            'to': 'gap_close_levered_book'})
        self.risk.update(book_keys=['gap_close_levered_book'], books_readable=1, books_total=1,
                         book_equity_usd={'gap_close_levered_book': 102000.})
        got = daily.calculate(self.base, self.risk, self.now)
        self.assertEqual((got['status'], got['pnl_usd'], got['net_external_flow_usd']),
                         ('VERIFIED', 2000., 0.))
        self.assertEqual(sum(x['pnl_usd'] for x in got['book_contributions']), 2000.)
        del meta['verified_retirement']
        self.assertEqual(daily.calculate(self.base, self.risk, self.now)['status'], 'UNVERIFIED')

    def test_retired_history_does_not_certify_or_permanently_block_later_clean_day(self):
        proof = {'transfer_utc': '2026-09-20T12:00:00Z', 'transfer_usd': 100,
                 'to': 'b'}
        for row in (self.base, self.risk):
            row['capital_snapshot']['coinspot_floor'] = {
                'present': False, 'verified_retirement': proof, 'events': []}
        for case in ('clean', 'proxy', 'stale', 'missing_proof', 'unretired'):
            base, risk = copy.deepcopy(self.base), copy.deepcopy(self.risk)
            if case == 'proxy': base['utc'] = '2026-09-20T13:59:00Z'
            if case == 'stale': risk['utc'] = '2026-09-21T07:00:00Z'
            if case == 'missing_proof': del base['capital_snapshot']['coinspot_floor']['verified_retirement']
            got = daily.calculate(base, risk, self.now)
            daily.apply_entry_integrity(got, base, risk, {'block_new_entries': True}, case != 'unretired')
            with self.subTest(case=case):
                self.assertEqual(got['current_daily_risk_verified'], case == 'clean')
                self.assertFalse(got['historical_performance_verified'])
                self.assertIn('unverified', got['entry_integrity_warning'])

    def test_current_stretch_is_ten_thousand_not_retired_quota(self):
        # 17 Oct 2026: stale test. Brett's 25 Sep 2026 mandate raised the stretch to $15k/Sydney day
        # (tfo_daily_pnl.DAILY_STRETCH_USD); the name is kept for history, the assertion now pins the current stretch.
        self.risk['firm_equity_usd'] = 116000.
        got = daily.calculate(self.base, self.risk, self.now)
        self.assertEqual(got['target_usd'], 15000.)
        self.assertTrue(got['target_achieved'])
        self.assertEqual(got['remaining_shortfall_usd'], 0.)

    def test_deposit_and_internal_transfer_are_not_profit(self):
        self.flow('a',5000,'deposit','deposit');self.risk['firm_equity_usd']+=5000
        self.flow('a',-7000,'capital_transfer','a-to-b','a','b')
        self.flow('b',7000,'capital_transfer','b-from-a','a','b')
        got=daily.calculate(self.base,self.risk,self.now)
        self.assertEqual((got['pnl_usd'],got['pnl_pct'],got['net_external_flow_usd']),(2000,2,5000))

    def test_late_flow_receipt_and_partial_or_changed_coverage_fail_closed(self):
        for error in ['partial','keys','unreceipted','backdated']:
            risk=copy.deepcopy(self.risk)
            if error=='partial':risk['books_readable']=1
            if error=='keys':risk['book_keys']=['a','c']
            if error=='unreceipted':risk['capital_snapshot']['a']['native_capital']+=10
            if error=='backdated':
                risk['capital_snapshot']['a']['events'].append({'id':'late','book':'a','usd':0,
                    'utc':'2026-09-19T07:00:00Z','type':'deposit'})
            with self.subTest(error=error):
                self.assertEqual(daily.calculate(self.base,risk,self.now)['status'],'UNVERIFIED')

    def test_off_midnight_proxy_does_not_claim_target_achieved(self):
        self.base['utc']='2026-09-20T13:50:39Z';self.risk['firm_equity_usd']=125000
        got=daily.calculate(self.base,self.risk,self.now)
        self.assertEqual(got['status'],'PROXY');self.assertEqual(got['opening_offset_seconds'],561)
        self.assertIsNone(got['target_achieved']);self.assertTrue(got['target_exceeded_on_estimate'])

    def test_no_silent_midday_or_overold_baseline(self):
        for stamp in ['2026-09-20T14:00:01Z','2026-09-20T11:00:00Z']:
            self.base['utc']=stamp
            self.assertEqual(daily.calculate(self.base,self.risk,self.now)['status'],'UNVERIFIED')

    def test_new_day_freezes_prior_observation_and_keeps_one_row_per_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            prior=copy.deepcopy(self.base);prior['utc']='2026-09-20T13:55:00Z'
            daily.atomic(root/'daily_pnl_latest_observation.json',prior)
            daily.atomic(root/'firm_risk_state.json',self.risk)
            a=daily.update(root,self.now);daily.update(root,self.now)
            self.assertEqual(len(daily.read(root/'daily_pnl_history.json')),1)
            self.risk['firm_equity_usd']=101000;daily.atomic(root/'firm_risk_state.json',self.risk)
            b=daily.update(root,self.now)
            self.assertEqual(a['baseline_at'],b['baseline_at']);self.assertEqual(b['pnl_pct'],1)
            self.assertEqual(daily.read(root/'firm_risk_state.json'),self.risk)

    def test_sydney_dst_boundary(self):
        # October 5 midnight is 13:00 UTC after the DST switch.
        self.base['utc']='2026-10-04T13:00:00Z';self.risk['utc']='2026-10-04T14:00:00Z'
        got=daily.calculate(self.base,self.risk,datetime(2026,10,4,14,1,tzinfo=timezone.utc))
        self.assertEqual(got['sydney_date'],'2026-10-05');self.assertEqual(got['status'],'VERIFIED')

    def test_exact_midnight_current_beats_older_saved_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);older=copy.deepcopy(self.base);older['utc']='2026-09-20T13:40:00Z'
            daily.atomic(root/'daily_pnl_latest_observation.json',older)
            daily.atomic(root/'firm_risk_state.json',self.base)
            got=daily.update(root,datetime(2026,9,20,14,1,tzinfo=timezone.utc))
            self.assertEqual(got['baseline_at'],self.base['utc']);self.assertEqual(got['status'],'VERIFIED')

    def test_failure_clears_prior_target_and_flow_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);daily.atomic(root/'daily_pnl_latest_observation.json',self.base)
            daily.atomic(root/'firm_risk_state.json',self.risk);daily.update(root,self.now)
            self.risk['capital_accounting_valid']=False
            daily.atomic(root/'firm_risk_state.json',self.risk);daily.update(root,self.now)
            status=daily.read(root/'daily_profit_target_status.json')
            self.assertEqual(status['status'],'UNVERIFIED')
            self.assertIsNone(status['remaining_shortfall_usd']);self.assertEqual(status['flow_details'],[])

    def test_attribution_does_not_treat_banks_or_salary_as_performance(self):
        self.base['book_equity_usd']={'a':50000,'b':50000}
        self.flow('a',-10000,'capital_transfer','bankout','a','b')
        self.flow('b',10000,'capital_transfer','bankin','a','b')
        self.flow('b',-17810.63,'capital_withdrawal','salary','b','Brent Taylor')
        self.risk['book_equity_usd']={'a':41000,'b':42189.37}
        self.risk['firm_equity_usd']=83189.37
        got=daily.calculate(self.base,self.risk,self.now)
        self.assertEqual(got['biggest_mover']['book'],'a')
        self.assertEqual(got['biggest_mover']['pnl_usd'],1000)
        self.assertEqual(got['biggest_mover']['contribution_pct_points'],1)
        self.assertEqual(got['book_contributions'][1]['pnl_usd'],0)
        self.assertEqual(sum(row['pnl_usd'] for row in got['book_contributions']),got['pnl_usd'])

    def test_largest_mover_can_be_negative(self):
        self.base['book_equity_usd']={'a':50000,'b':50000}
        self.risk.update(book_equity_usd={'a':46000,'b':52000},firm_equity_usd=98000)
        got=daily.calculate(self.base,self.risk,self.now)
        self.assertEqual(got['biggest_mover']['book'],'a')
        self.assertEqual(got['biggest_mover']['pnl_usd'],-4000)
        self.assertEqual(got['biggest_mover']['direction'],'DRAG')

    def test_incomplete_or_mismatching_book_values_withhold_marker(self):
        self.base['book_equity_usd']={'a':50000,'b':50000}
        for current in [{'a':52000},{'a':80000,'b':50000}]:
            self.risk['book_equity_usd']=current
            got=daily.calculate(self.base,self.risk,self.now)
            self.assertEqual(got['attribution_status'],'UNVERIFIED')
            self.assertIsNone(got['biggest_mover'])
            self.assertEqual(got['pnl_usd'],2000)

if __name__=='__main__':unittest.main()
