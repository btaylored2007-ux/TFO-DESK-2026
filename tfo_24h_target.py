"""Reporting-only PAPER target (mandate: $15,000 per Sydney day, rolling). Never sizes or submits an order."""
import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

BASE=Path(__file__).resolve().parent
ORDER_UTC='2026-09-24T08:16:11Z'
DEADLINE_UTC='2026-09-25T08:16:11Z'
TARGET_USD=100000.0
TARGET_ID='tfo-100k-20260924T081611Z'


def epoch(value):
    return dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()


def read(path):
    return json.loads(Path(path).read_bytes())


def valid_risk(risk):
    keys=risk.get('book_keys') or []
    return (risk.get('capital_accounting_valid') is True and risk.get('coverage_audit_clean') is True
            and risk.get('capital_snapshot') and risk.get('book_keys')
            and len(set(keys))==len(keys)>0
            and risk.get('books_readable')==risk.get('books_total')==len(keys)
            and ('drawdown_valid' not in risk or risk['drawdown_valid'] is True)
            and isinstance(risk.get('firm_equity_usd'),(int,float))
            and not isinstance(risk['firm_equity_usd'],bool)
            and math.isfinite(risk['firm_equity_usd']) and risk['firm_equity_usd']>0)


def flow_adjusted(baseline,current):
    from firm_capital_flows import reconcile
    if not valid_risk(baseline) or not valid_risk(current):
        raise ValueError('Target requires valid local capital and coverage')
    if set(baseline['book_keys']) != set(current['book_keys']):
        raise ValueError('Active book scope changed; target comparison needs reconciliation')
    old=baseline['capital_snapshot'];new=current['capital_snapshot']
    old_ids={e['id'] for b in old.values() for e in b.get('events',[])}
    new_ids={e['id'] for b in new.values() for e in b.get('events',[])}
    if not old_ids<=new_ids:raise ValueError('Historical capital receipts disappeared')
    for b in new.values():
        for event in b.get('events',[]):
            if event['id'] not in old_ids and not epoch(baseline['utc'])<epoch(event['utc'])<=epoch(current['utc']):
                raise ValueError('Backdated/future capital event requires reconciliation')
    net,details,errors=reconcile(old,new)
    if errors:raise ValueError('; '.join(errors))
    return round(current['firm_equity_usd']-baseline['firm_equity_usd']-net,2),net,details


def booked_receipts(reports,start,end):
    found={};conflicts=[]
    for path in (reports/'private_wealth_cycles').glob('*.json'):
        receipt=read(path)
        if receipt.get('desk_scope')!='LOCAL_ONLY':continue
        for ticket in receipt.get('paper_tickets',[]):
            if ticket.get('kind')!='EXIT' and ticket.get('action') not in ('SELL','HALF','FLAT','STOP','COVER','scale_out','close_runner','stop_out','catalyst_expired','LIQUIDATE_PAPER_RETIREMENT'):continue
            try:stamp=epoch(ticket['utc'])
            except (KeyError,ValueError,TypeError):continue
            if not start<=stamp<=end or not ticket.get('ticket_id'):continue
            if ticket.get('paper_only') is not True or ticket.get('verified') is not True:continue
            ident=ticket['ticket_id']
            if ident in found and found[ident]!=ticket:conflicts.append(ident)
            found[ident]=ticket
    values=[t.get('booked_pnl_usd') for t in found.values()]
    finite=lambda x:isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
    known=sum(v for v in values if finite(v));unknown=sum(not finite(v) for v in values)
    missing_costs=sum(not all(finite(t.get(k)) for k in ['fees_usd','slippage_estimate_usd']) for t in found.values())
    return {'booked_pnl_usd':round(known,2) if not unknown and not conflicts else None,
            'known_booked_pnl_usd':round(known,2),'booked_ticket_count':len(found),'unknown_booked_ticket_count':unknown,
            'costs_unknown_ticket_count':missing_costs,'ticket_conflicts':conflicts,
            'booked_scope':'Verified archived local cycle tickets only; not proof of complete whole-desk realised P&L or all costs.'}


MANDATE_DAILY_TARGET_USD=15000.0


def update(base=BASE,initialize=False,now=None):
    """Custodian 2026-09-25 (Head Trader, per Brett's 2026-09-25 mandate): the one-time
    $100k/24h stretch is closed. Reports the standing mandate target of $15,000 per
    Sydney day (midnight to midnight, rolling daily), using the same fixed Sydney-day
    opening baseline and flow-adjusted method as tfo_daily_pnl. Reporting only."""
    import private_wealth_monitor as monitor
    import tfo_daily_pnl as daily
    now=now or dt.datetime.now(dt.timezone.utc)
    reports=Path(base)/'reports'
    syd=daily.SYD
    day=now.astimezone(syd).date()
    opening=dt.datetime.combine(day,dt.time(),syd)
    closing=dt.datetime.combine(day+dt.timedelta(days=1),dt.time(),syd)
    record=json.loads((reports/'daily_pnl_days'/(day.isoformat()+'.json')).read_text()) if (reports/'daily_pnl_days'/(day.isoformat()+'.json')).exists() else {}
    baseline=record.get('baseline') or {}
    risk=json.loads((reports/'firm_risk_state.json').read_text())
    calc=daily.calculate(baseline,risk,now)
    pnl=calc.get('pnl_usd')
    status='UNVERIFIED'
    if pnl is not None:
        status=('STALE' if calc.get('stale') else 'TARGET_MET_ESTIMATE' if pnl>=MANDATE_DAILY_TARGET_USD else 'ACTIVE_ESTIMATE')
    result={'target_id':'tfo-mandate-daily-'+day.isoformat(),'target_usd':MANDATE_DAILY_TARGET_USD,
            'target_kind':'MANDATE_DAILY_ROLLING','sydney_date':day.isoformat(),
            'window_start_utc':opening.astimezone(dt.timezone.utc).isoformat(),
            'deadline_utc':closing.astimezone(dt.timezone.utc).isoformat(),
            'baseline_utc':calc.get('baseline_at'),'baseline_equity_usd':calc.get('baseline_equity_usd'),
            'baseline_status':calc.get('status'),'opening_offset_seconds':calc.get('opening_offset_seconds'),
            'asof':risk.get('utc'),'updated_utc':now.isoformat(),'expired':False,'paper_only':True,
            'scope':'ACTIVE_LOCAL_TFO_PAPER_BOOKS','metric':'Flow-adjusted incremental marked equity since the Sydney-day opening; not booked realised profit',
            'pnl_usd':pnl,'remaining_usd':None if pnl is None else max(0,round(MANDATE_DAILY_TARGET_USD-pnl,2)),
            'net_external_flow_usd':calc.get('net_external_flow_usd'),'current_equity_usd':risk.get('firm_equity_usd'),
            'verified_target_achieved':calc.get('target_achieved'),'status':status,
            'note':('Mandate target $15,000 per Sydney day, rolling at Sydney midnight. Remaining is not a sizing input; the target never overrides risk gates. '
                    +(calc.get('note') or ''))}
    result.update(booked_receipts(reports,opening.timestamp(),min(now.timestamp(),closing.timestamp())))
    monitor.atomic(reports/'tfo_24h_target_latest.json',result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--initialize',action='store_true')
    args=parser.parse_args();result=update(initialize=args.initialize)
    print(json.dumps(result,indent=2))
