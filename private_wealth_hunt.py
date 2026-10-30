"""Bounded, read-only market hunt; no position sizing, orders or ledger writes."""
import datetime as dt
import argparse
import json
import math
import urllib.parse
from pathlib import Path

from tfo_bank_rail_scanner import http_get_json
from tfo_equity_refresh import parse_chart
from private_wealth_monitor import atomic

BASE = Path(__file__).resolve().parent
UNIVERSE = [('BP.L', 'energy'), ('TTE.PA', 'energy'), ('EQNR.OL', 'energy'),
            ('ENI.MI', 'energy'), ('REP.MC', 'energy'), ('GALP.LS', 'energy'),
            ('XRO.AX', 'ASX'), ('ALL.AX', 'ASX'), ('CSL.AX', 'ASX')]
US_OPEN_UNIVERSE = [(s, 'energy') for s in ('FANG', 'EXE', 'APA', 'BKR', 'CHRD')]


def held_symbols(cycle):
    positions = cycle.get('boards', {}).get('Positions', {}).get('data', {}).get('positions', [])
    return {p.get(key) for p in positions for key in ('symbol', 'ticker', 'sym') if p.get(key)}


def paper_bar_eligible(q, payload):
    """Delayed research evidence, not native-writer execution permission."""
    try:
        result = payload['chart']['result'][0]
        period = result['meta']['currentTradingPeriod']['regular']
        stamp = dt.datetime.fromisoformat(q['quote_utc'].replace('Z', '+00:00')).timestamp()
        received = dt.datetime.fromisoformat(q['fetched_at'].replace('Z', '+00:00')).timestamp()
        closes = result['indicators']['quote'][0]['close']
        matching_bar = any(t == stamp and positive(p) and abs(p-q['px']) < 1e-8
                           for t, p in zip(result['timestamp'], closes))
        return (q['session'] == 'regular' and q.get('price_session') == 'regular'
                and period['start'] <= stamp < period['end']
                and 0 <= received-stamp <= 1200 and matching_bar)
    except (KeyError, TypeError, ValueError, IndexError):
        return False


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def assess(symbol, sector, payload, held, blockers):
    q = parse_chart(symbol, payload)
    meta = payload['chart']['result'][0]['meta']
    reasons = list(blockers)
    if symbol in held: reasons.append('Already held locally')
    if q['session'] != 'regular': reasons.append('Outside regular continuous session')
    price_eligible = paper_bar_eligible(q, payload)
    if not price_eligible: reasons.append('No verified same-session regular bar within 20-minute PAPER delay allowance')
    bid, ask = meta.get('bid'), meta.get('ask')
    spread = 10000*(ask-bid)/((ask+bid)/2) if positive(bid) and positive(ask) and ask >= bid else None
    if spread is None: reasons.append('Timestamped bid/ask spread unavailable')
    else: reasons.append('Bid/ask source clock unverified; cannot admit on spread alone')
    if spread is not None and spread >= 20: reasons.append('Spread fails 20bp limit')
    # 2026-10-29 infra (Iris): prefer last daily bar; previousClose fallback with sanity check
    prior = None
    try:
        from veteran_desk import feeds as _FD
        _pc = _FD.prior_close(symbol, yahoo_prev=meta.get('previousClose') or meta.get('chartPreviousClose'))
        prior = _pc.get('close')
    except Exception:
        prior = meta.get('previousClose')
    change = 100*(q['px']/prior-1) if positive(prior) else None
    if change is None: reasons.append('Prior regular close unavailable')
    reasons.append('Ten-day session-volume pace and 5% participation capacity not verified')
    if sector == 'ASX':
        limit = {'XRO.AX':61.11, 'ALL.AX':63.18, 'CSL.AX':188.20}[symbol]
        if q['ccy'] != 'AUD' or q['px'] >= limit: reasons.append('ASX price/currency condition not met')
    return {'symbol':symbol,'sector':sector,'price':q['px'],'currency':q['ccy'],
            'quote_utc':q['quote_utc'],'fetched_at':q['fetched_at'],
            'session':q['session'],'quote_status':q['status'],
            'paper_price_eligible':price_eligible,
            'paper_price_policy':'Timestamped regular bar, up to 20 minutes delayed; research only',
            'change_from_prior_close_pct':round(change,3) if change is not None else None,
            'spread_bp':round(spread,3) if spread is not None else None,
            'session_volume':meta.get('regularMarketVolume'),
            'status':'REJECTED','reasons':reasons,
            'entry_approved':False,'cost_adjusted_edge':None}


def run(us_open=False):
    reports = BASE/'reports'
    cycle = json.loads((reports/'private_wealth_cycle_latest.json').read_text())
    daily = json.loads((reports/'daily_profit_target_status.json').read_text())
    held = held_symbols(cycle)
    blockers = []
    try:
        now = dt.datetime.now(dt.timezone.utc)
        approved_proxy = (daily.get('daily_risk_basis_allowed') is True
            and daily.get('daily_risk_basis') in ('FUNDER_APPROVED_FIXED_PROXY', 'DESK_MANDATE_FIXED_PROXY')
            and now < dt.datetime.fromisoformat(daily['daily_risk_authorization_expires_utc'])
            and 0 <= (now-dt.datetime.fromisoformat(daily['asof'].replace('Z', '+00:00'))).total_seconds() <= 1200)
    except (KeyError, TypeError, ValueError):
        approved_proxy = False
    if daily.get('current_daily_risk_verified') is not True and not approved_proxy:
        blockers.append('Current-day risk accounting unverified ('+str(daily.get('status'))+')')
    if isinstance(daily.get('pnl_pct'), (int, float)) and daily['pnl_pct'] <= -3:
        blockers.append('Daily -3% loss freeze')
    rows=[]
    for symbol, sector in (US_OPEN_UNIVERSE if us_open else UNIVERSE):
        url='https://query1.finance.yahoo.com/v8/finance/chart/'+urllib.parse.quote(symbol,safe='')+'?interval=1m&range=1d&includePrePost=true'
        try:
            payload=http_get_json(url,timeout=6)
            row=assess(symbol,sector,payload,held,blockers)
            row['source_url']=url
        except Exception as exc:
            row={'symbol':symbol,'sector':sector,'status':'NO_DATA','entry_approved':False,
                 'reasons':[str(exc)[:200]],'source_url':url}
        rows.append(row)
    result={'asof':dt.datetime.now(dt.timezone.utc).isoformat(),'paper_only':True,
            'status':'NO_ELIGIBLE_ENTRY','financial_writes':False,'candidates':rows,
            'scope':('US-open energy research watchlist: FANG, EXE, APA, BKR, CHRD; not admitted trades or verified alpha' if us_open else 'Fresh bounded screen of nine ASX/European energy names; not a full-market or validated-alpha claim'),
            'global_blockers':blockers,'ranked_candidates':[],
            'note':'Prices are native currency. No ranking or sizing without verified session-volume pace, executable spread and current-day risk. No trade submitted.'}
    atomic(reports/'private_wealth_hunt_latest.json',result)
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--us-open', action='store_true')
    run(us_open=parser.parse_args().us_open)
