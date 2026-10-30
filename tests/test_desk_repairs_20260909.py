import unittest, io
from contextlib import redirect_stdout
from unittest.mock import patch
import carry_monitor, capital_accounting, growth_book, firm_risk
import pandas as pd

class DeskRepairs(unittest.TestCase):
    def test_stale_carry_cannot_rank_or_complete_basket(self):
        rows = [dict(base=b, t=t, fundingRate=1, markPrice=100, vol24h=20000) for b,t in [('BTC',9999),('ETH',1),('BICO',1)]]
        with patch.object(carry_monitor.time,'time',return_value=10000): s=carry_monitor.snapshot(rows)
        self.assertEqual([x['base'] for x in s['top']],['BTC'])
        self.assertIsNone(s['preferred_mean_annualised_pct']);self.assertEqual(len(s['stale_assets']),2)
    def test_transfer_basis_keeps_returned_cash_out_of_loss(self):
        b={'seed_usd':75000,'capital_events':[{'type':'capital_transfer','usd':75000},{'type':'capital_transfer','usd':-67503.96}]}
        self.assertEqual(capital_accounting.net_allocated_capital(b),7496.04);self.assertEqual(b['seed_usd'],75000)
    def test_growth_full_investment_reserves_turnover_cost(self):
        b={'cash':1000,'holdings':{},'rebalances':0}
        p=pd.DataFrame({'A':[100.]},index=pd.to_datetime(['2026-09-09']))
        with patch.object(growth_book,'choose_targets',return_value=({'A':1.},[])), patch.object(growth_book,'_firm_halt_blocks',side_effect=lambda b,w,h,t:w),patch.object(growth_book,'_log'): growth_book.rebalance(b,p)
        self.assertGreaterEqual(b['cash'],-1e-10)
        self.assertAlmostEqual(b['holdings']['A']*100+b['cash']+b['costs_paid'],1000,places=2)
    def test_unmeasured_console_never_prints_ok(self):
        s=dict(utc='now',firm_equity_usd=100,firm_equity_live_usd=100,stale_equity_usd=0,firm_peak_usd=100,firm_peak_at_utc='now',firm_drawdown_pct=0,books_readable=1,books_total=2,positions=0,level='UNMEASURED',book_drawdowns=[])
        with redirect_stdout(io.StringIO()) as f:firm_risk.report(s)
        self.assertIn('UNMEASURED',f.getvalue());self.assertNotIn('level              : OK',f.getvalue())

    def test_dated_order_cannot_overdraw_cash_for_fee(self):
        import dated_event_book as de, cash_yield_books
        b=dict(cash=7500,positions={},pending={'TEST':dict(fill_session='2020-01-01',ticket_usd=7500,adv_shares_20d=1000000)},costs_paid_usd=0)
        with patch.object(de,'load',return_value=b),patch.object(de,'save'),patch.object(de,'_live',return_value={'px_usd':100}),patch.object(cash_yield_books,'accrue_book'):
            de.tick(verbose=False)
        self.assertEqual(b['cash'],7500);self.assertFalse(b['positions']);self.assertIn('TEST',b['pending'])
    def test_missed_yield_interval_not_backfilled(self):
        import cash_yield_books as cy, cash_yield
        d={'cash':100,'cash_yield_last_epoch':1}
        with patch.object(cy.time,'time',return_value=10000),patch.object(cash_yield,'current_rate',side_effect=AssertionError('must baseline, not credit')):
            self.assertEqual(cy.accrue_book(d,verbose=False),0)
        self.assertEqual(d['cash'],100);self.assertEqual(d['cash_yield_last_epoch'],10000)
