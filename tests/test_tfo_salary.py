"""Synthetic owner withdrawals and capital-flow effects; no real desk writes."""
import copy
import json
import sys
import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tfo_salary as salary
import firm_capital_flows as flows
import command_server


class SalaryTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,9,21,9,0,tzinfo=timezone.utc)
        self.risk={'utc':'2026-09-21T09:00:00Z','firm_equity_usd':90000.0,
                   'firm_peak_usd':100000.0,'capital_accounting_valid':True,
                   'drawdown_valid':True,'books_total':1,'books_readable':1}
        self.book={'paper_only':True,'holdings':{},'cash':90000.0,'equity':90000.0,
                   'seed_usd':100000.0,'start':100000.0,'capital_events':[]}
        self.ledger={'paper_only':True,'status':'ACTIVE','payee':'Brent Taylor',
                     'currency':'USD','currency_confirmed':True,'monthly_amount':25000,
                     'first_payment_date':'2026-09-21','next_payment_date':'2026-09-21','payments':[]}

    def test_dated_payment_reduces_cash_and_funding_without_erasing_loss(self):
        before=copy.deepcopy(self.book)
        book, paid, payment=salary.prepare(self.book,self.ledger,self.risk,self.now)
        self.assertEqual(self.book,before)
        self.assertEqual((book['cash'],book['equity']),(65000,65000))
        self.assertEqual((book['seed_usd'],book['start']),(75000,75000))
        self.assertEqual(book['equity']-book['start'],-10000)
        self.assertEqual(paid['total_paid_amount'],25000)
        self.assertEqual(paid['next_payment_date'],'2026-10-21')
        self.assertEqual(payment['date'],'2026-09-21')
        self.assertEqual(payment['status'],'PAID_PAPER')
        self.assertEqual(payment['payee'],'Brent Taylor')
        self.assertAlmostEqual(payment['withdrawal_only_drawdown_after_pct'],-10)
        self.assertIsNone(salary.prepare(book,paid,self.risk,self.now))

    def test_pending_currency_unfunded_and_duplicate_refused(self):
        for edit in ['currency','cash','duplicate','stale']:
            book, ledger, risk=copy.deepcopy(self.book),copy.deepcopy(self.ledger),copy.deepcopy(self.risk)
            if edit=='currency':ledger['currency_confirmed']=False
            if edit=='cash':book['cash']=1000;book['equity']=1000
            if edit=='duplicate':ledger['payments']=[{'id':'salary-Brent-Taylor-2026-09-21'}]
            if edit=='stale':risk['utc']='2026-09-20T00:00:00Z'
            with self.subTest(edit=edit),self.assertRaises(ValueError):
                salary.prepare(book,ledger,risk,self.now)

    def test_capital_reconciliation_uses_unit_redemption_exactly_once(self):
        after,_,_=salary.prepare(self.book,self.ledger,self.risk,self.now)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'book.json'
            row={'key':'gap_close_levered_book','path':str(path),'ccy':'USD','start_usd':100000}
            path.write_text(json.dumps(self.book));prior,errs=flows.snapshot([row]);self.assertFalse(errs)
            prev=dict(self.risk,capital_snapshot=prior,book_keys=[row['key']])
            row['start_usd']=75000;path.write_text(json.dumps(after))
            result=flows.capital_event(prev,[row])
            self.assertTrue(result['capital_accounting_valid'])
            self.assertEqual(result['external_flow_usd'],-25000)
            peak=100000+result['peak_shift_usd']
            self.assertAlmostEqual(100*(65000/peak-1),-10)
            # A further market loss remains visible after the withdrawal.
            self.assertAlmostEqual(100*(58500/peak-1),-19)
            again=flows.capital_event(dict(prev,capital_snapshot=result['capital_snapshot']),[row])
            self.assertEqual(again['external_flow_usd'],0)
            self.assertEqual(again['peak_shift_usd'],0)

    def test_forged_pre_flow_valuation_fails_closed(self):
        after,_,_=salary.prepare(self.book,self.ledger,self.risk,self.now)
        after['capital_events'][0]['performance_flow']['firm_equity_before_usd']=999999
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'book.json';row={'key':'gap_close_levered_book','path':str(path),'ccy':'USD','start_usd':100000}
            path.write_text(json.dumps(self.book));prior,_=flows.snapshot([row])
            path.write_text(json.dumps(after));row['start_usd']=75000
            result=flows.capital_event(dict(self.risk,capital_snapshot=prior,book_keys=[row['key']]),[row])
            self.assertFalse(result['capital_accounting_valid'])
            self.assertEqual(result['peak_shift_usd'],0)
            self.assertEqual(result['capital_snapshot'],prior)

    def test_api_reads_payment_ledger_without_altering_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'reports').mkdir();path=root/'reports/salary_payments.json'
            path.write_text(json.dumps(self.ledger));original=path.read_bytes()
            with patch.object(command_server,'BASE',str(root)):
                self.assertEqual(command_server._salary_block(),self.ledger)
            self.assertEqual(path.read_bytes(),original)

    def test_month_boundary(self):
        self.assertEqual(salary.next_month('2026-12-21'),'2027-01-21')

    def aud_quote(self):
        return {'pair':'AUD/USD','bid':.71236,'ask':.71254,'usd_per_aud':.71245,
                'source':salary.FX_URL,'observed_at':self.now.isoformat(),
                'method':'quote_midpoint_valuation_only'}

    def test_aud_nominal_and_usd_funding_remain_distinct(self):
        self.ledger['currency']='AUD'
        book, paid, payment=salary.prepare(self.book,self.ledger,self.risk,self.now,self.aud_quote())
        self.assertEqual(payment['amount'],25000)
        self.assertEqual(payment['currency'],'AUD')
        self.assertEqual(payment['amount_usd'],17811.25)
        self.assertEqual(book['cash'],72188.75)
        self.assertEqual(book['seed_usd'],82188.75)
        self.assertEqual(book['capital_events'][0]['usd'],-17811.25)
        self.assertEqual(book['equity']-book['start'],-10000)
        self.assertEqual(paid['total_paid_amount'],25000)
        self.assertAlmostEqual(payment['withdrawal_only_drawdown_after_pct'],-10)

    def test_aud_quote_missing_stale_inverted_wrong_or_inconsistent_refused(self):
        self.ledger['currency']='AUD'
        for edit in ['missing','stale','inverted','wrong_pair','bad_rate','nonfinite']:
            fx=self.aud_quote()
            if edit=='missing':fx=None
            if edit=='stale':fx['observed_at']='2026-09-20T00:00:00Z'
            if edit=='inverted':fx['bid']=1.4
            if edit=='wrong_pair':fx['pair']='USD/AUD'
            if edit=='bad_rate':fx['usd_per_aud']=1/.71245
            if edit=='nonfinite':fx['usd_per_aud']=float('nan')
            with self.subTest(edit=edit),self.assertRaises(ValueError):
                salary.prepare(self.book,self.ledger,self.risk,self.now,fx)

    def exercise_transaction(self, valid, currency='USD'):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.ledger['currency']=currency
            debit=17811.25 if currency=='AUD' else 25000
            # Controlled clock makes the registered September first payment due.
            (root/'salary_payments.json').write_text(json.dumps(self.ledger))
            (root/'gap_close_levered_book.json').write_text(json.dumps(self.book))
            for name in ['firm_risk_state.json','firm_risk.json']:
                (root/name).write_text(json.dumps(self.risk))
            (root/'firm_equity.csv').write_text('original observations\n')
            originals={p.name:p.read_bytes() for p in root.iterdir()}
            post=dict(self.risk,capital_accounting_valid=valid,external_flow_usd=-debit,
                      owner_withdrawal_performance_flows=[{}],firm_equity_usd=90000-debit,
                      firm_drawdown_pct=-10)
            with patch.object(salary,'R',root),patch.object(salary,'desk_lock',return_value=nullcontext()),\
                    patch.object(salary,'fetch_aud_fx',return_value=self.aud_quote()),\
                    patch.object(salary,'datetime') as clock,\
                    patch.object(salary.firm_risk,'_run_locked',side_effect=[self.risk,post]):
                clock.now.return_value=self.now
                clock.fromisoformat.side_effect=datetime.fromisoformat
                if valid:
                    result=salary.run()
                    self.assertEqual(result['action'],'SALARY_PAID_PAPER')
                    self.assertEqual(salary.read(root/'salary_payments.json')['total_paid_amount'],25000)
                    # Another same-day call is a no-op with no additional valuation.
                    self.assertEqual(salary.run()['action'],'NO_PAYMENT_DUE')
                else:
                    with self.assertRaises(RuntimeError):salary.run()
                    for name,raw in originals.items():self.assertEqual((root/name).read_bytes(),raw)
                    journal=salary.read(root/'.salary-Brent-Taylor-2026-09-21-transaction.json')
                    self.assertEqual(journal['status'],'ROLLED_BACK')

    def test_transaction_commits_once(self):
        self.exercise_transaction(True)

    def test_failed_reconciliation_rolls_back_all_financial_state(self):
        self.exercise_transaction(False)

    def test_aud_transaction_commits_once(self):
        self.exercise_transaction(True,'AUD')

    def test_aud_failed_reconciliation_rolls_back(self):
        self.exercise_transaction(False,'AUD')


if __name__=='__main__':unittest.main()
