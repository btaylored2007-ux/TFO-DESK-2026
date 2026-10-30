"""Read-only asset attribution for the four handover crypto sleeves.

Uses saved opening holdings/marks and actual modelled net sale receipts.
Fails closed on unmatched quantities, unknown trades or inconsistent book P&L.
"""
import copy
import json
import math
from datetime import datetime

BOOKS=('crypto_liq_beta_book','crypto_clip_book','crypto_uni_sui_rs','crypto_mid_clip_book')
# HT decision 2026-09-26 #4e: all four handover sleeves are retired (zero holdings; capital
# moved out). They must not headline the War Room's daily movers any more.
RETIRED=frozenset(BOOKS)


def active_books():
    return tuple(b for b in BOOKS if b not in RETIRED)


def retired_result():
    return {'status':'UNVERIFIED','top':[],'scope_label':'No current data',
            'note':'No current data: the tracked crypto sleeves ('+', '.join(sorted(RETIRED))+') are retired and '
                   'no live book carries verified opening asset marks.',
            'percentage_basis':'Daily holding P&L / opening holding value; includes sold portions and recorded costs.',
            'asof':None,'covered_books':[],'retired_books':sorted(RETIRED),'asset_count':0}


NAMES={'BTC/USD':'Bitcoin','ETH/USD':'Ethereum','XRP/USD':'XRP','SOL/USD':'Solana',
       'DOGE/USD':'Dogecoin','ADA/USD':'Cardano','NEAR/USD':'NEAR Protocol',
       'UNI/USD':'Uniswap','AAVE/USD':'Aave','SUI/USD':'Sui','LINK/USD':'Chainlink',
       'TAO/USD':'Bittensor','HYPE/USD':'Hyperliquid'}


def stamp(value):
    return datetime.fromisoformat(value.replace('Z','+00:00'))


def capture(root,risk):
    out={}
    for key in BOOKS:
        try:
            raw=json.loads((root/(key+'.json')).read_text())
            if abs(raw['equity']-risk['book_equity_usd'][key])>.015:
                return {}
            if stamp(raw['last_mark_utc'])>stamp(risk['utc']):
                return {}
            out[key]={k:copy.deepcopy(raw.get(k)) for k in
                ['holdings','mark_px_usd','cash','equity','capital_events','last_mark_utc']}
        except (KeyError,ValueError,TypeError,OSError):
            return {}
    return out


def calculate(openings,current,daily):
    result={'status':'UNVERIFIED','top':[],'scope_label':'Four tracked crypto sleeves',
            'note':'Other books lack verified opening asset marks; this is not a whole-desk asset ranking.',
            'percentage_basis':'Daily holding P&L / opening holding value; includes sold portions and recorded costs.',
            'asof':None,'covered_books':list(BOOKS),'asset_count':0}
    if daily.get('status') not in ('PROXY','VERIFIED') or set(openings or {})!=set(BOOKS) or set(current or {})!=set(BOOKS):
        return result
    contributions={row['book']:row['pnl_usd'] for row in daily.get('book_contributions',[])}
    assets={};timestamps=[];book_checks=[]
    try:
        for key in BOOKS:
            old,new=openings[key],current[key]
            prior=old.get('capital_events') or [];events=new.get('capital_events') or []
            if events[:len(prior)] != prior:
                raise ValueError('Opening trade history changed: '+key)
            quantities={pair:float(qty) for pair,qty in old['holdings'].items()}
            proceeds={pair:0.0 for pair in quantities}
            for event in events[len(prior):]:
                sold=event.get('sold')
                fills=event.get('fills') if event.get('action')=='REBALANCE_TRIM' else None
                if sold:
                    legs=[(pair,float(fill['qty']),float(fill['usd'])) for pair,fill in sold.items()]
                elif fills:
                    legs=[(fill['pair'],float(fill['quantity']),float(fill['net_usd'])) for fill in fills]
                else:
                    # Capital transfers are not asset profit; unknown quantity changes
                    # are detected by the remaining-quantity reconciliation below.
                    continue
                for pair,qty,net in legs:
                    if pair not in quantities or not all(math.isfinite(x) for x in [qty,net]) or qty<=0 or net<0:
                        raise ValueError('Invalid sale receipt: '+key)
                    quantities[pair]-=qty;proceeds[pair]+=net
            if set(new['holdings'])-set(quantities):
                raise ValueError('New position requires verified purchase attribution: '+key)
            subtotal=0.0
            for pair,opening_qty in old['holdings'].items():
                qty=float(new['holdings'].get(pair,0))
                if abs(quantities[pair]-qty)>max(1e-8,abs(float(opening_qty))*1e-9):
                    raise ValueError('Unmatched trade quantities: '+key+' '+pair)
                start=float(opening_qty)*float(old['mark_px_usd'][pair])
                marked=qty*float(new['mark_px_usd'][pair]) if qty else 0.0
                if not all(math.isfinite(x) for x in [start,marked,qty]) or start<=0 or qty<0:
                    raise ValueError('Invalid position valuation: '+pair)
                profit=marked+proceeds[pair]-start;subtotal+=profit
                entry=assets.setdefault(pair,{'symbol':pair,'name':NAMES.get(pair,pair),
                    'pnl_usd':0.0,'opening_value_usd':0.0,'current_value_usd':0.0,
                    'net_sale_proceeds_usd':0.0,'books':[]})
                entry['pnl_usd']+=profit;entry['opening_value_usd']+=start
                entry['current_value_usd']+=marked;entry['net_sale_proceeds_usd']+=proceeds[pair]
                entry['books'].append(key)
            if abs(subtotal-contributions[key])>.05:
                raise ValueError('Asset P&L does not reconcile to book: '+key)
            book_checks.append({'book':key,'asset_pnl_usd':round(subtotal,2),
                                'book_pnl_usd':contributions[key]})
            timestamps.append(new['last_mark_utc'])
        rows=list(assets.values())
        for row in rows:
            row['pnl_pct']=100*row['pnl_usd']/row['opening_value_usd']
            row['pnl_usd']=round(row['pnl_usd'],2)
        rows.sort(key=lambda row:(-abs(row['pnl_usd']),row['symbol']))
        result.update(status='PROXY',top=rows[:5],all_assets=rows,asset_count=len(rows),
                      scope_label='4 tracked crypto sleeves · '+str(len(rows))+' assets',
                      asof=min(timestamps,key=stamp),book_reconciliation=book_checks)
    except (KeyError,ValueError,TypeError) as exc:
        result['error']=str(exc)
    return result
