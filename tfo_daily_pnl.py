"""Recorded whole-firm daily PAPER P&L. Reads financial state; never trades.

Sydney calendar days. Fixed opening equity; external flows are excluded and
internal transfers net out. Off-midnight observations are explicitly estimates.
"""
import copy
import fcntl
import hashlib
import json
import math
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import firm_capital_flows
import tfo_asset_pnl

R = Path(__file__).resolve().parent/'reports'
SYD = ZoneInfo('Australia/Sydney')
DAILY_STRETCH_USD = 15000.0  # Brett 2026-09-25 mandate: $15k per Sydney day
BOOK_LABELS = {
    'crypto_liq_beta_book':'Major crypto sleeve',
    'crypto_clip_book':'Crypto clip sleeve',
    'crypto_uni_sui_rs':'UNI / AAVE / SUI sleeve',
    'crypto_mid_clip_book':'LINK / TAO / HYPE sleeve',
    'gap_close_levered_book':'Available cash warehouse',
    'crypto_sol_short_pilot':'Closed SOL short pilot',
    'fundamental_book':'Fundamental desk',
    'fundamental_spec_book':'Fundamental speculative sleeve',
    'kraken_floor':'Kraken floor', 'replica_floor':'Venue replica floor',
    'coinspot_floor':'CoinSpot floor', 'value_book':'Value book',
    'convexity_sleeve':'Convexity sleeve', 'soxl_weekend_probe':'SOXL probe',
}


def instant(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def read(path, default=None):
    if not path.exists():
        return copy.deepcopy(default)
    return json.loads(path.read_text())


def atomic(path, value):
    tmp = path.with_name(path.name+'.daily-tmp')
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')
    tmp.replace(path)


def observation(risk):
    return {k:copy.deepcopy(risk.get(k)) for k in ['utc','firm_equity_usd',
        'capital_accounting_valid','drawdown_valid','books_readable','books_total',
        'book_keys','capital_snapshot','book_equity_usd']}


def valid(risk):
    eq = risk.get('firm_equity_usd')
    keys = risk.get('book_keys') or []
    return (risk.get('capital_accounting_valid') is True and risk.get('drawdown_valid') is True
            and risk.get('books_readable') == risk.get('books_total') == len(keys) > 0
            and isinstance(eq, (int,float)) and math.isfinite(eq) and eq > 0
            and bool(risk.get('capital_snapshot')))


def attribute(baseline, risk, details, firm_pnl, status):
    """Per-book performance removes BOTH internal and external capital moves."""
    out={'attribution_status':'UNVERIFIED','biggest_mover':None,'top_movers':[],'book_contributions':[],
         'attribution_note':'Opening and current equity are required for every book.'}
    old = dict(baseline.get('book_equity_usd') or {})
    new = dict(risk.get('book_equity_usd') or {})
    keys=set(risk['book_keys'])
    if reconciled_retirement_scope(baseline, risk):
        # Verified zero holdings/cash and a paired internal transfer, not a lost book.
        for k in set(baseline.get('book_keys', [])) - keys:
            new[k] = 0.0
            keys.add(k)
        # Same-day book funded wholly by paired internal transfers: opening equity 0.
        snap = risk.get('capital_snapshot', {})
        for k in keys - set(baseline.get('book_keys', [])):
            if internally_funded_new_book(k, snap.get(k) or {}, set(baseline.get('book_keys', []))):
                old.setdefault(k, 0.0)
    if set(old) != keys or set(new) != keys:
        return out
    if any(not isinstance(x,(int,float)) or not math.isfinite(x)
           for x in list(old.values())+list(new.values())):
        return out
    moves={row['book']:row['usd'] for row in details}
    rows=[]
    for key in sorted(keys):
        pnl=round(new[key]-old[key]-moves.get(key,0),2)
        rows.append({'book':key,'label':BOOK_LABELS.get(key,key.replace('_',' ').title()),
                     'pnl_usd':pnl,'contribution_pct_points':100*pnl/baseline['firm_equity_usd'],
                     'direction':'GAIN' if pnl>=0 else 'DRAG',
                     'opening_equity_usd':old[key],'current_equity_usd':new[key],
                     'net_capital_flow_usd':moves.get(key,0)})
    difference=round(firm_pnl-sum(row['pnl_usd'] for row in rows),2)
    if abs(difference) > max(.02,len(keys)*.01+.005):
        out['attribution_note']='Book contributions do not reconcile to the whole desk; marker withheld.'
        return out
    rows.sort(key=lambda row:(-abs(row['pnl_usd']),row['book']))
    out.update(attribution_status=status,biggest_mover=rows[0] if rows and abs(rows[0]['pnl_usd'])>=.005 else None,
               top_movers=rows[:5],book_contributions=rows,attribution_rounding_usd=difference,
               attribution_note='Top five books ranked by absolute daily P&L contribution after removing capital transfers and owner withdrawals. Uses the same opening estimate as daily P&L.'
               if status=='PROXY' else 'Top five books ranked by absolute daily P&L contribution after removing capital transfers and owner withdrawals.')
    return out


def reconciled_retirement_scope(baseline, risk):
    """Removed books are reconciled only with verified proof: CoinSpot retirement
    or a verified zero-balance archive (book_archive.py)."""
    old, new = set(baseline.get('book_keys', [])), set(risk.get('book_keys', []))
    removed, added, snap = old - new, new - old, risk.get('capital_snapshot', {})
    if not removed and not added:
        return False
    removed_ok = all((k == 'coinspot_floor' and bool(snap.get(k, {}).get('verified_retirement')))
                     or bool(snap.get(k, {}).get('verified_archive')) for k in removed)
    return removed_ok and all(internally_funded_new_book(k, snap.get(k) or {}, old) for k in added)


def internally_funded_new_book(key, row, baseline_keys):
    """Custodian 2026-09-25: a same-day new book is reconciled only when its whole
    USD funding basis is explained by internal capital transfers with books that
    were in the day's baseline (pairing itself is verified by firm_capital_flows)."""
    events = row.get('events') or []
    if not row.get('present', True) or row.get('ccy') != 'USD' or not events:
        return False
    if any(e.get('type') != 'capital_transfer' or e.get('book') != key
           or (e['from'] if e['usd'] > 0 else e['to']) not in baseline_keys for e in events):
        return False
    capital = row.get('native_capital')
    return isinstance(capital, (int, float)) and abs(capital - sum(e['usd'] for e in events)) < 0.01


def calculate(baseline, risk, now):
    day = now.astimezone(SYD).date().isoformat()
    out = {'status':'UNVERIFIED','sydney_date':day,'currency':'USD','pnl_usd':None,
           'pnl_pct':None,'baseline_equity_usd':None,'baseline_at':None,
           'current_equity_usd':risk.get('firm_equity_usd'),'asof':risk.get('utc'),
           'net_external_flow_usd':None,'target_usd':DAILY_STRETCH_USD,'target_achieved':None,
           'target_exceeded_on_estimate':None,'note':'Opening equity is not yet verified.',
           'remaining_shortfall_usd':None,'opening_offset_seconds':None,'flow_details':[],
           'stale':True,'current_daily_risk_verified':False,
           'attribution_status':'UNVERIFIED','biggest_mover':None,'top_movers':[],'book_contributions':[],
           'attribution_note':'Daily P&L must reconcile before attributing the move.',
           'method':'(current equity - opening equity - net external deposits) / opening equity',
           'includes_unrealised':True,'owner_withdrawals_excluded':True,'paper_only':True}
    if not baseline:
        return out
    out.update(baseline_equity_usd=baseline.get('firm_equity_usd'),baseline_at=baseline.get('utc'))
    if not valid(risk) or not valid(baseline):
        out['note']='Complete equity and valid capital accounting are required.'
        return out
    if (sorted(risk['book_keys']) != sorted(baseline['book_keys'])
            and not reconciled_retirement_scope(baseline, risk)):
        out['note']='Book coverage changed; daily comparison requires reconciliation.'
        return out
    observed = instant(risk['utc'])
    opening = datetime.combine(now.astimezone(SYD).date(),time(),SYD)
    start = instant(baseline['utc'])
    offset = (opening-start).total_seconds()
    if not 0 <= offset <= 7200 or observed < opening or observed > now.astimezone(timezone.utc):
        out['note']='No comparable observation for the current Sydney day.'
        return out
    old, new = baseline['capital_snapshot'], risk['capital_snapshot']
    old_events={e['id'] for b in old.values() for e in b.get('events',[])}
    all_new_ids={e['id'] for b in new.values() for e in b.get('events',[])}
    if not old_events <= all_new_ids:
        out['note']='Historical capital receipts disappeared; daily reconciliation is required.'
        return out
    new_events=[e for b in new.values() for e in b.get('events',[]) if e['id'] not in old_events]
    # A late-entered historical receipt cannot silently become today's return.
    if any(not start < instant(e['utc']) <= observed for e in new_events):
        out['note']='A backdated or future capital event needs daily reconciliation.'
        return out
    net, details, errors = firm_capital_flows.reconcile(old, new)
    if errors:
        out['note']='Capital flows need reconciliation: '+'; '.join(errors[:2])
        return out
    pnl = round(risk['firm_equity_usd']-baseline['firm_equity_usd']-net,2)
    status = 'VERIFIED' if offset == 0 else 'PROXY'
    note = ('Midnight opening observation.' if offset == 0 else
            'Estimated: opening uses '+start.astimezone(SYD).strftime('%d %b %H:%M:%S')+
            ' Sydney, '+str(int(offset))+' seconds before midnight.')
    note += ' Percentage is against starting equity. Salary and other owner flows are excluded; market P&L includes unrealised changes.'
    stale = (now.astimezone(timezone.utc)-observed).total_seconds() > 1200
    if stale:
        note += ' The equity observation is over 20 minutes old.'
    out.update(status=status,pnl_usd=pnl,pnl_pct=100*pnl/baseline['firm_equity_usd'],
               net_external_flow_usd=net,note=note,stale=stale,
               opening_offset_seconds=offset,flow_details=details,
               target_achieved=(pnl>=DAILY_STRETCH_USD if status=='VERIFIED' else None),
               target_exceeded_on_estimate=(pnl>=DAILY_STRETCH_USD if status=='PROXY' else None),
               remaining_shortfall_usd=max(0,round(DAILY_STRETCH_USD-pnl,2)))
    out['current_daily_risk_verified'] = status == 'VERIFIED' and not stale
    out.update(attribute(baseline,risk,details,pnl,status))
    return out


def apply_entry_integrity(current, baseline, risk, integrity, retired):
    """A retired historical defect must not certify or poison a later clean day."""
    if integrity.get('block_new_entries') is not True:
        return
    current['historical_performance_verified'] = False
    current['performance_verified'] = False
    current['entry_integrity_warning'] = (
        'CoinSpot is retired; its historical P&L remains unverified. Retirement proceeds are an internal transfer, not verified profit.'
        if retired else
        'CoinSpot historical fills need currency reconciliation; combined P&L includes unverified amounts.')
    current['note'] += ' ' + current['entry_integrity_warning']
    old = (baseline or {}).get('capital_snapshot', {}).get('coinspot_floor', {})
    new = risk.get('capital_snapshot', {}).get('coinspot_floor', {})
    proof = new.get('verified_retirement') or {}
    try:
        clean_period = (retired and bool(proof) and old.get('verified_retirement') == proof
                        and old.get('present') is False and new.get('present') is False
                        and 'coinspot_floor' not in (baseline or {}).get('book_keys', [])
                        and 'coinspot_floor' not in risk.get('book_keys', [])
                        and instant(proof['transfer_utc']) <= instant(baseline['utc']))
    except (KeyError, TypeError, ValueError):
        clean_period = False
    current['current_daily_risk_verified'] = (
        current.get('current_daily_risk_verified') is True and clean_period)
    if not current['current_daily_risk_verified']:
        current['target_achieved'] = None
        current['target_exceeded_on_estimate'] = None


def apply_baseline_authorization(current, baseline, authorization, now):
    """Permission to use a pinned proxy for risk, never P&L certification."""
    current['daily_risk_basis_allowed'] = current.get('current_daily_risk_verified') is True
    current['daily_risk_basis'] = 'VERIFIED' if current['daily_risk_basis_allowed'] else 'UNVERIFIED'
    if not authorization or not baseline:
        return
    try:
        digest = hashlib.sha256(json.dumps(baseline, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        allowed = (authorization.get('paper_only') is True
            and authorization.get('performance_remains_estimated') is True
            and instant(authorization['approved_utc']) <= now < instant(authorization['expires_utc'])
            and authorization['sydney_date'] == current['sydney_date'] == now.astimezone(SYD).date().isoformat()
            and authorization['baseline_sha256'] == digest
            and authorization['baseline_utc'] == baseline['utc'] == current['baseline_at']
            and authorization['baseline_equity_usd'] == baseline['firm_equity_usd'] == current['baseline_equity_usd']
            and authorization['loss_limit_pct'] == -3.0
            and current['status'] == 'PROXY' and current['stale'] is False
            and current.get('attribution_status') == 'PROXY'
            and 0 <= (now-instant(current['asof'])).total_seconds() <= 1200
            and isinstance(current['pnl_pct'], (int, float)) and math.isfinite(current['pnl_pct']))
    except (KeyError, ValueError, TypeError, OverflowError):
        allowed = False
    if allowed and authorization.get('kind') == 'desk_mandate':
        current['daily_risk_basis_allowed'] = True
        current['daily_risk_basis'] = 'DESK_MANDATE_FIXED_PROXY'
        current['daily_risk_authorization_expires_utc'] = authorization['expires_utc']
        current['note'] += (' PROXY baseline (%d s pre-midnight snapshot) accepted for PAPER daily risk under the Head Trader desk mandate, today only, expiring at 00:00 Sydney; the -3%% loss freeze remains and performance stays estimated.'
                            % int(current.get('opening_offset_seconds') or 0))
    elif allowed:
        current['daily_risk_basis_allowed'] = True
        current['daily_risk_basis'] = 'FUNDER_APPROVED_FIXED_PROXY'
        current['daily_risk_authorization_expires_utc'] = authorization['expires_utc']
        current['note'] += ' Funder-approved fixed observed baseline for PAPER risk only; the -3% loss freeze remains and performance stays estimated.'


def baseline_authorization(root, day):
    """Desk-mandate waiver first (Head Trader autonomous mandate), else a funder file."""
    tag = day.replace('-', '')
    return (read(Path(root)/('desk_daily_baseline_authorization_'+tag+'.json'), {})
            or read(Path(root)/('funder_daily_baseline_authorization_'+tag+'.json'), {}))


def update(root=R, now=None):
    """Record at most one changing daily row; no quotes or financial mutations."""
    root=Path(root); now=now or datetime.now(timezone.utc)
    with (root/'.daily_pnl.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        risk=read(root/'firm_risk_state.json',{})
        point=observation(risk)
        # retired sleeves are dropped from daily movers (HT 2026-09-26 #4e)
        point['asset_books']=tfo_asset_pnl.capture(root,risk) if tfo_asset_pnl.active_books() else {}
        day=now.astimezone(SYD).date().isoformat()
        folder=root/'daily_pnl_days';folder.mkdir(exist_ok=True)
        path=folder/(day+'.json');record=read(path,{})
        baseline=record.get('baseline')
        last=read(root/'daily_pnl_latest_observation.json',{})
        if not baseline:
            midnight=datetime.combine(now.astimezone(SYD).date(),time(),SYD)
            choices=[p for p in [last,point] if valid(p)
                     and 0 <= (midnight-instant(p['utc'])).total_seconds() <= 7200]
            if choices:
                baseline=max(choices,key=lambda p:instant(p['utc']))
                record['baseline_source']='Last recorded complete pre-midnight risk observation; fixed for this Sydney day.'
        current=calculate(baseline,risk,now)
        integrity=read(root/'coinspot_entry_integrity_20260921.json',{})
        if integrity.get('block_new_entries') is True:
            from coinspot_retirement import verified_retirement
            apply_entry_integrity(current, baseline, risk, integrity, verified_retirement(root.parent))
        apply_baseline_authorization(current, baseline,
            baseline_authorization(root, day), now)
        current['asset_movers']=(tfo_asset_pnl.calculate((baseline or {}).get('asset_books'),point['asset_books'],current)
                                 if tfo_asset_pnl.active_books() else tfo_asset_pnl.retired_result())
        history=read(root/'daily_pnl_history.json',[])
        compact={k:v for k,v in current.items() if k not in ('flow_details','book_contributions')}
        history=[row for row in history if row['sydney_date'] != day]+[compact]
        history.sort(key=lambda row:row['sydney_date'],reverse=True)
        record.update(baseline=baseline,latest=compact)
        if read(path,{}) != record:atomic(path,record)
        if read(root/'daily_pnl_history.json',[]) != history:atomic(root/'daily_pnl_history.json',history)
        if valid(risk):
            if point != last:atomic(root/'daily_pnl_latest_observation.json',point)
        prior_status=read(root/'daily_profit_target_status.json',{})
        status=dict(current,initialized_at=prior_status.get('initialized_at',now.isoformat()))
        status.update(net_daily_profit_usd=current['pnl_usd'],daily_pnl_pct=current['pnl_pct'],
                      opening_equity_usd=current['baseline_equity_usd'],
                      reason=current['note'],timezone='Australia/Sydney')
        if current['status']!='UNVERIFIED':status.pop('next_step',None)
        if read(root/'daily_profit_target_status.json',{}) != status:atomic(root/'daily_profit_target_status.json',status)
        current['history']=history[:90]
        return current


if __name__=='__main__':
    result=update()
    print(json.dumps({k:v for k,v in result.items() if k not in ('history','flow_details')}))
