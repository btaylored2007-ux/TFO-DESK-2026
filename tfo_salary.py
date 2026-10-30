"""Dated monthly PAPER owner withdrawals. No bank connection or real payments."""
import argparse
import copy
import hashlib
import json
import math
import shutil
import urllib.request
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

import firm_risk
import tfo_sol_short

R = Path(__file__).resolve().parent/'reports'
SYD = ZoneInfo('Australia/Sydney')
FX_URL = 'https://api.kraken.com/0/public/Ticker?pair=AUDUSD'


def read(path):
    return json.loads(path.read_text())


def atomic(path, value):
    tmp = path.with_name(path.name+'.salary-tmp')
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')
    tmp.replace(path)


def next_month(value):
    value = date.fromisoformat(value)
    return date(value.year+(value.month == 12), value.month % 12+1, 21).isoformat()


def fetch_aud_fx():
    """Public midpoint for PAPER accounting only; no FX order or bank fees."""
    with urllib.request.urlopen(FX_URL, timeout=15) as response:
        data = json.load(response)
    if data.get('error') or set(data.get('result', {})) != {'AUDUSD'}:
        raise ValueError('AUD/USD quote unavailable or wrong pair')
    tick = data['result']['AUDUSD']
    bid, ask = float(tick['b'][0]), float(tick['a'][0])
    if not math.isfinite(bid+ask) or not 0 < bid <= ask or (ask-bid)/bid > .01:
        raise ValueError('AUD/USD quote invalid or spread exceeds 1%')
    return {'pair':'AUD/USD','usd_per_aud':(bid+ask)/2,'bid':bid,'ask':ask,
            'source':FX_URL,'observed_at':datetime.now(timezone.utc).isoformat(),
            'method':'quote_midpoint_valuation_only',
            'note':'PAPER accounting valuation; no FX execution or bank fee model'}


def prepare(ledger, salary, risk, now, fx=None):
    """Pure preparation: one due month, sufficient cash, verified pre-flow state."""
    if salary.get('paper_only') is not True or salary.get('payee') != 'Brent Taylor':
        raise ValueError('Salary must be PAPER-only and payable to Brent Taylor')
    currency = salary.get('currency')
    if currency not in ('USD','AUD') or salary.get('currency_confirmed') is not True:
        raise ValueError('Supported salary currency must be explicitly confirmed before posting')
    if salary.get('status') != 'ACTIVE' or salary.get('monthly_amount') != 25000:
        raise ValueError('Monthly salary mandate is not active at 25000')
    due = salary['next_payment_date']
    if date.fromisoformat(due) > now.astimezone(SYD).date():
        return None
    identity = 'salary-Brent-Taylor-'+due
    if any(p['id'] == identity for p in salary.get('payments', [])):
        raise ValueError('Duplicate monthly payment identifier')
    if ledger.get('paper_only') is not True or ledger.get('holdings'):
        raise ValueError('Salary source must be the flat PAPER cash warehouse')
    nominal = float(salary['monthly_amount'])
    amount = nominal
    if currency == 'AUD':
        if not fx or fx.get('pair') != 'AUD/USD' or fx.get('source') != FX_URL or fx.get('method') != 'quote_midpoint_valuation_only':
            raise ValueError('Verified AUD/USD valuation receipt required')
        bid, ask, rate = (float(fx[k]) for k in ['bid','ask','usd_per_aud'])
        age = (now.astimezone(timezone.utc)-datetime.fromisoformat(fx['observed_at'].replace('Z','+00:00'))).total_seconds()
        if (not math.isfinite(bid+ask+rate) or not 0 < bid <= ask or
                (ask-bid)/bid > .01 or abs(rate-(bid+ask)/2) > 1e-10 or not -5 <= age <= 60):
            raise ValueError('AUD/USD valuation stale or inconsistent')
        amount = float((Decimal(str(nominal))*Decimal(str(rate))).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
    equity, peak = risk.get('firm_equity_usd'), risk.get('firm_peak_usd')
    if (risk.get('capital_accounting_valid') is not True or risk.get('drawdown_valid') is not True
            or risk.get('books_total') != risk.get('books_readable')
            or not isinstance(equity, (int, float)) or not isinstance(peak, (int, float))
            or not math.isfinite(equity+peak) or not 0 < amount < equity <= peak):
        raise ValueError('Complete verified pre-withdrawal equity/peak required')
    age = (now.astimezone(timezone.utc)-datetime.fromisoformat(risk['utc'].replace('Z','+00:00'))).total_seconds()
    if not -5 <= age <= 60:
        raise ValueError('Pre-withdrawal risk observation is stale')
    if ledger['cash'] < amount or abs(ledger['cash']-ledger['equity']) > .005:
        raise ValueError('Insufficient or unreconciled warehouse cash')
    if any(not math.isfinite(ledger[k]) for k in ['cash','equity','seed_usd','start']):
        raise ValueError('Non-finite funding state')
    after, paid = copy.deepcopy(ledger), copy.deepcopy(salary)
    stamp = now.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
    receipt = {'method':'owner_unit_redemption_v1','pre_flow_risk_utc':risk['utc'],
               'firm_equity_before_usd':equity,'firm_peak_before_usd':peak}
    event = {'type':'capital_withdrawal','utc':stamp,'usd':-amount,
             'from':'gap_close_levered_book','to':'Brent Taylor','paper_only':True,
             'purpose':'SALARY — personal owner withdrawal','payment_id':identity,
             'payment_amount':nominal,'payment_currency':currency,'fx':copy.deepcopy(fx),
             'performance_flow':receipt}
    for key in ['cash','equity','seed_usd','start']:
        after[key] = round(after[key]-amount, 2)
    after['dry_powder_usd'] = after['cash']
    after['updated'] = stamp
    after.setdefault('capital_events', []).append(event)
    payment = {'id':identity,'date':now.astimezone(SYD).date().isoformat(),'due_date':due,
               'paid_at_sydney':now.astimezone(SYD).isoformat(),'paid_at_utc':stamp,
               'payee':'Brent Taylor','amount':nominal,'currency':currency,'amount_usd':amount,
               'fx':copy.deepcopy(fx),
               'status':'PAID_PAPER','paper_only':True,
               'note':'Simulated personal owner withdrawal; no real bank transfer',
               'source_book':'gap_close_levered_book','cash_before_usd':ledger['cash'],
               'cash_after_usd':after['cash'],'performance_flow':receipt,
               'withdrawal_only_drawdown_before_pct':100*(equity/peak-1),
               'withdrawal_only_drawdown_after_pct':100*((equity-amount)/(peak*(equity-amount)/equity)-1)}
    paid.setdefault('payments', []).append(payment)
    paid['total_paid_amount'] = round(sum(p['amount'] for p in paid['payments'] if p['status']=='PAID_PAPER'),2)
    paid['next_payment_date'] = next_month(due)
    paid['updated_at'] = stamp
    return after, paid, payment


@contextmanager
def desk_lock():
    path = R/'.tick.lock'
    path.mkdir()
    try:
        with tfo_sol_short.locked(), firm_risk.writer_lock():
            yield
    finally:
        path.rmdir()


def run():
    with desk_lock():
        salary_path, powder_path = R/'salary_payments.json', R/'gap_close_levered_book.json'
        salary = read(salary_path)
        if salary.get('currency_confirmed') is not True:
            return {'action':'PENDING_CURRENCY','paper_only':True}
        if date.fromisoformat(salary['next_payment_date']) > datetime.now(SYD).date():
            return {'action':'NO_PAYMENT_DUE','next_payment_date':salary['next_payment_date'],'paper_only':True}
        identity = 'salary-Brent-Taylor-'+salary['next_payment_date']
        journal_path = R/('.'+identity+'-transaction.json')
        if journal_path.exists():
            raise RuntimeError('Payment transaction already exists; reconcile it rather than paying again')
        before = firm_risk._run_locked(verbose=False)
        fx = fetch_aud_fx() if salary.get('currency') == 'AUD' else None
        now = datetime.now(timezone.utc)
        powder = read(powder_path)
        result = prepare(powder, salary, before, now, fx)
        if result is None:
            return {'action':'NO_PAYMENT_DUE','paper_only':True}
        after, paid, payment = result
        backup = R/'revisions/rollback'/identity
        backup.mkdir(parents=True)
        paths = [powder_path,salary_path,R/'firm_risk_state.json',R/'firm_risk.json',R/'firm_equity.csv']
        for path in paths:
            shutil.copy2(path,backup/path.name)
        expected = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        journal = {'status':'PREPARED','paper_only':True,'payment':payment,'backup':str(backup),
                   'before_hashes':expected,'prepared_at':now.isoformat()}
        atomic(journal_path,journal)
        try:
            atomic(powder_path,after)
            atomic(salary_path,paid)
            post = firm_risk._run_locked(verbose=False)
            if (not post['capital_accounting_valid'] or not post['drawdown_valid']
                    or abs(post['external_flow_usd']+payment['amount_usd']) > .005
                    or not post.get('owner_withdrawal_performance_flows')):
                raise RuntimeError('Salary external flow or performance adjustment failed reconciliation')
            payment['post_risk_utc'] = post['utc']
            payment['post_firm_equity_usd'] = post['firm_equity_usd']
            payment['post_performance_drawdown_pct'] = post['firm_drawdown_pct']
            payment['capital_verified'] = True
            # The list holds the same payment object produced by prepare().
            atomic(salary_path,paid)
            journal.update(status='COMMITTED',payment=payment,committed_at=datetime.now(timezone.utc).isoformat())
            atomic(journal_path,journal)
            atomic(R/(identity+'-receipt.json'),journal)
            return {'action':'SALARY_PAID_PAPER','payment':payment,'next_payment_date':paid['next_payment_date']}
        except Exception:
            for path in paths:
                shutil.copy2(backup/path.name,path)
            journal['status']='ROLLED_BACK'
            atomic(journal_path,journal)
            raise


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--tick',action='store_true',required=True)
    parser.parse_args()
    result = run()
    result['checked_at'] = datetime.now(timezone.utc).isoformat()
    atomic(R/'tfo_salary_latest.json', result)
    print(json.dumps(result))
