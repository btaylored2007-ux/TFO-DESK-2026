#!/usr/bin/env python3
"""One bounded SOL PAPER short. Public GETs only; no broker credentials/orders.

--preview is read-only apart from a research receipt. --open permits one entry
under the dated plan; --tick only marks or exits an existing position. The long
scanner's lock serializes powder transfers. A recoverable journal protects the
two ledger files. Account access is explicitly unverified: simulated only.
"""
import argparse
import concurrent.futures
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
from pathlib import Path
import shutil
import urllib.request

from tfo_short_math import depth_vwap, round_quantity, open_short, mark_short, rail_action, close_short

ROOT = Path(__file__).resolve().parent
R = ROOT / 'reports'
KEY = 'crypto_sol_short_pilot'
SYMBOL = 'PF_SOLUSD'
BOOK = R / (KEY + '.json')
POWDER = R / 'gap_close_levered_book.json'
PLAN = R / 'crypto_recovery_20260920_sol_short_plan.json'
JOURNAL = R / '.tfo_sol_short_transaction.json'
REPORT = R / 'tfo_sol_short_latest.json'
BASE = 'https://futures.kraken.com/derivatives/api/v3/'


def stamp():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def epoch(value):
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.utcoffset() is None:
        raise ValueError('timestamp lacks timezone')
    return d.timestamp()


def finite(value, positive=False):
    if isinstance(value, bool):
        raise ValueError('boolean is not numeric')
    value = float(value)
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError('invalid numeric value')
    return value


def fresh(value, max_age, now=None):
    age = (datetime.now(timezone.utc).timestamp() if now is None else now) - epoch(value)
    if not -2 <= age <= max_age:
        raise ValueError('stale/future timestamp: ' + value)


def read(path):
    return json.loads(path.read_text())


def atomic(path, data):
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')
    temp.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


@contextmanager
def locked():
    with (R / '.tfo_bank_rail_scanner.lock').open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def recover_transaction():
    if not JOURNAL.exists():
        return
    j = read(JOURNAL)
    if j['status'] == 'COMMITTED':
        return
    for item in j['files']:
        p = R / item['name']
        if p not in (BOOK, POWDER):
            raise ValueError('unexpected journal target')
        if digest(p) not in (item['before_hash'], item['after_hash']):
            raise RuntimeError('external edit during interrupted transaction; manual reconciliation required')
    for item in j['files']:
        atomic(R / item['name'], item['after'])
    j['status'] = 'COMMITTED'
    j['recovered_at'] = stamp()
    atomic(JOURNAL, j)


def commit(book, powder, expected, action):
    for p in (BOOK, POWDER):
        if digest(p) != expected[p.name]:
            raise RuntimeError('concurrent ledger change; no commit')
    tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    rb = R / 'revisions' / 'rollback' / ('sol-short-' + tag)
    rb.mkdir(parents=True)
    files = []
    for p, after in ((BOOK, book), (POWDER, powder)):
        if p.exists():
            shutil.copy2(p, rb / p.name)
        data = json.dumps(after, indent=2, allow_nan=False) + '\n'
        files.append({'name': p.name, 'before_hash': expected[p.name],
                      'after_hash': hashlib.sha256(data.encode()).hexdigest(), 'after': after})
    j = {'status': 'PREPARED', 'action': action, 'utc': stamp(), 'rollback': str(rb), 'files': files}
    atomic(JOURNAL, j)
    recover_transaction()
    shutil.copy2(JOURNAL, rb / 'transaction.json')
    return str(rb)


def get(url):
    import kraken_dns; kraken_dns.install()   # futures.kraken.com public GETs only; DoH so DNS cannot null the book
    req = urllib.request.Request(url, headers={'User-Agent': 'TFO-PAPER-SOL/1.0'})
    with urllib.request.urlopen(req, timeout=15) as response:
        d = json.load(response)
    if isinstance(d.get('result'), str) and d['result'] != 'success':
        raise ValueError('public derivatives API error')
    if d.get('error'):
        raise ValueError('public API error: ' + str(d['error']))
    if d.get('serverTime'):
        fresh(d['serverTime'], 30)
    return d


def market(entry=False):
    urls = {'book': BASE + 'orderbook?symbol=' + SYMBOL,
            'ticker': BASE + 'tickers?symbol=' + SYMBOL,
            'funding': BASE + 'historical-funding-rates?symbol=' + SYMBOL}
    if entry:
        urls.update({'instruments': BASE + 'instruments', 'status': BASE + 'instruments/status',
                     'health': 'https://status.kraken.com/api/v2/summary.json',
                     'spot': 'https://api.kraken.com/0/public/Ticker?pair=SOLUSD',
                     'candles': 'https://futures.kraken.com/api/charts/v1/trade/PF_SOLUSD/15m?count=32'})
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {k: pool.submit(get, u) for k, u in urls.items()}
        data = {}
        for key, future in futures.items():
            try:
                data[key] = future.result()
            except Exception as exc:
                if key != 'funding' or entry:
                    raise
                # Only a history outage is recoverable. Fresh ticker rates
                # and already-observed cached hours must cover the whole hold.
                data['funding'] = {'rates': [], 'history_error': str(exc)}
    data['fetched_at'] = stamp()
    data['sources'] = urls
    ticker = next(t for t in data['ticker']['tickers'] if t['symbol'] == SYMBOL)
    if ticker.get('suspended') is not False:
        raise ValueError('market suspended or status unavailable')
    finite(ticker['markPrice'], True)
    fresh(data['ticker']['serverTime'], 30)
    finite(ticker['fundingRate'])
    if entry:
        instrument = next(t for t in data['instruments']['instruments'] if t['symbol'] == SYMBOL)
        if (instrument.get('tradeable') is not True or instrument.get('type') != 'flexible_futures'
                or instrument.get('contractSize') != 1 or instrument.get('quote') != 'USD'
                or instrument.get('postOnly') is not False or instrument.get('isExpired') is not False):
            raise ValueError('contract specifications do not permit assumed execution')
        status = next(t for t in data['status']['instrumentStatus'] if t['tradeable'] == SYMBOL)
        if (status.get('experiencingDislocation') is not False
                or status.get('experiencingExtremeVolatility') is not False):
            raise ValueError('instrument health gate')
        health = {c['id']: c['status'] for c in data['health']['components']}
        if any(health.get(c) != 'operational' for c in ('77r52jbmww8v', 'dxfwygd9wc17')):
            raise ValueError('derivatives/REST health gate')
        spot = next(iter(data['spot']['result'].values()))
        mid = (finite(spot['a'][0], True) + finite(spot['b'][0], True)) / 2
        if abs(finite(ticker['indexPrice'], True) / mid - 1) > .005:
            raise ValueError('spot/index divergence exceeds 50bps')
    fresh(data['book']['serverTime'], 30)
    return data


def observed_funding_rates(data, cached=()):
    """Merge ACTUAL fixed hourly rates; never predictions or interpolation.

    The ticker's current fundingRate applies only to the hour containing its
    fresh server timestamp. A cached past hour remains that historical rate,
    not a quote that can be rolled forward. Any disagreement fails closed.
    """
    fresh(data['book']['serverTime'], 30)
    fresh(data['ticker']['serverTime'], 30)
    ticker = next(t for t in data['ticker']['tickers'] if t['symbol'] == SYMBOL)
    current_hour = math.floor(epoch(data['ticker']['serverTime']) / 3600) * 3600
    current_rate = finite(ticker['fundingRate'])
    by_hour = {}

    def add(row, source):
        hour = epoch(row['timestamp'])
        rate = finite(row['fundingRate'])
        if hour % 3600 or hour > current_hour:
            raise ValueError('invalid observed funding hour')
        if hour in by_hour and by_hour[hour]['fundingRate'] != rate:
            raise ValueError('conflicting actual funding rates; ledger unchanged')
        if hour not in by_hour:
            by_hour[hour] = {
                'timestamp': datetime.fromtimestamp(hour, timezone.utc).isoformat().replace('+00:00', 'Z'),
                'fundingRate': rate, 'sources': []}
        if source not in by_hour[hour]['sources']:
            by_hour[hour]['sources'].append(source)

    if not isinstance(cached, (list, tuple)):
        raise ValueError('observed funding cache is malformed')
    for row in cached:
        add(row, 'persisted_observation')
    for row in data['funding']['rates']:
        add(row, 'historical_funding_rates')
    add({'timestamp': datetime.fromtimestamp(current_hour, timezone.utc).isoformat(),
         'fundingRate': current_rate}, 'fresh_ticker_actual_rate')
    rates = [by_hour[hour] for hour in sorted(by_hour)]
    cache = [row for row in rates if epoch(row['timestamp']) >= current_hour - 7 * 3600]
    return rates, cache


def funding_delta(start, end, quantity, rates):
    """Integrate published absolute USD/unit/hour rates; fail on any gap."""
    start, end, quantity = epoch(start), epoch(end), finite(quantity, True)
    if end < start:
        raise ValueError('backward funding interval')
    by_hour = {}
    for row in rates:
        t = epoch(row['timestamp'])
        if t % 3600:
            raise ValueError('funding rate not on hourly boundary')
        v = finite(row['fundingRate'])
        if t in by_hour and by_hour[t] != v:
            raise ValueError('conflicting funding rates')
        by_hour[t] = v
    value, cursor = 0.0, start
    while cursor < end:
        hour = math.floor(cursor / 3600) * 3600
        if hour not in by_hour:
            raise ValueError('missing actual funding interval; ledger unchanged')
        stop = min(end, hour + 3600)
        value += quantity * by_hour[hour] * (stop - cursor) / 3600
        cursor = stop
    return value


def entry_quote(data, funding=75000.0):
    depth = data['book']['orderBook']
    best_bid = max(finite(x[0], True) for x in depth['bids'])
    best_ask = min(finite(x[0], True) for x in depth['asks'])
    spec = next(x for x in data['instruments']['instruments'] if x['symbol'] == SYMBOL)
    lot = 10 ** -int(spec['contractValueTradePrecision'])
    q = round_quantity(funding / (best_bid * 1.0005), lot)
    sell, cover = depth_vwap(depth, 'sell', q), depth_vwap(depth, 'cover', q)
    liquidity = min(sum(float(p)*float(z) for p,z in depth['bids'] if float(p) >= best_bid*.9975),
                    sum(float(p)*float(z) for p,z in depth['asks'] if float(p) <= best_ask*1.0025))
    cost = (best_ask - best_bid + best_bid - sell['vwap']) / best_bid * 10000
    if cost > 15 or sell['notional'] > .20 * liquidity:
        raise ValueError('entry spread/slippage/depth gate')
    if not 108.20 <= sell['vwap'] < 109.25 or cover['vwap'] >= 109.25:
        raise ValueError('entry outside registered price/invalidation range')
    # The broken prior low must still cap the latest completed candle.
    now = epoch(data['fetched_at'])
    bars, seen = [], set()
    for c in data['candles']['candles']:
        t = finite(c['time'], True) / 1000
        if t % 900 or t in seen:
            raise ValueError('unaligned or duplicate candle timestamp')
        seen.add(t)
        o, h, l, cpx = (finite(c[k], True) for k in ('open', 'high', 'low', 'close'))
        if not l <= min(o, cpx) <= max(o, cpx) <= h or finite(c['volume']) < 0:
            raise ValueError('invalid candle price/volume')
        if t + 900 <= now:
            bars.append(c)
    bars.sort(key=lambda c: finite(c['time']))
    if len(bars) < 8 or now - (float(bars[-1]['time'])/1000+900) > 930:
        raise ValueError('missing fresh completed signal bars')
    if finite(bars[-1]['close'], True) > 108.50:
        raise ValueError('latest settled candle reclaimed broken level; original setup no longer eligible')
    # Do not assume actual account-specific margin discounts. Full cash cover
    # is stricter than every published initial-margin schedule at this size.
    margins = [x['initialMargin'] for x in spec['marginLevels']]
    for group in spec.get('marginSchedules', {}).values():
        for levels in group.values():
            margins.extend(x['initialMargin'] for x in levels)
    if not margins or max(margins) > 1 or q > finite(spec['maxPositionSize'], True):
        raise ValueError('collateral/contract size unsupported')
    return {'quantity': q, 'lot': lot, 'entry': sell['vwap'], 'cover': cover['vwap'],
            'notional': sell['notional'], 'spread_and_slip_bps': cost,
            'lesser_25bps_depth_usd': liquidity, 'signal_bar': bars[-1],
            'conservative_maintenance_fraction': max(x['maintenanceMargin'] for x in spec['marginLevels'])}


def entry_admission(plan, quote):
    if plan.get('status') != 'AUTHORIZED_SIMULATION' or plan.get('paper_only') is not True:
        raise ValueError('dated paper plan not authorized')
    if plan.get('proposed_concentration_exception', {}).get('activated') is not True:
        raise ValueError('prospective concentration exception not active')
    if epoch(stamp()) >= epoch('2026-09-20T10:00:00Z'):
        raise ValueError('dated entry plan expired')
    risk = read(R / 'firm_risk.json')
    fresh(risk['utc'], 180)
    if (risk.get('capital_accounting_valid') is not True or risk.get('new_entries_halted') is not False
            or risk.get('firm_drawdown_pct', -100) <= -10
            or risk.get('books_readable') != risk.get('books_total')):
        raise ValueError('firm admission gate')
    audit = read(R / 'crypto_recovery_20260920_short_admission_audit.json')
    fresh(audit['asof_utc'], 300)
    if audit.get('risk_admission_pass') is not True or audit.get('errors'):
        raise ValueError('daily/exposure admission unverified')
    daily_heat = finite(audit['daily_heat']['flow_adjusted_pct'])
    if daily_heat <= -3:
        raise ValueError('daily loss freeze')
    gross, net = finite(audit['crypto']['gross_usd'], True), finite(audit['crypto']['signed_net_usd'])
    if gross + quote['notional'] > 700000 or abs(net-quote['notional']) >= abs(net):
        raise ValueError('gross/net concentration gate')
    return {'firm_risk_utc': risk['utc'], 'audit_utc': audit['asof_utc'],
            'gross_before': gross, 'gross_after': gross + quote['notional'],
            'signed_net_before': net, 'signed_net_after': net - quote['notional'],
            'daily_heat_pct': daily_heat}


def run(mode):
    with locked():
        if mode != 'preview':
            recover_transaction()
        if mode == 'tick' and not BOOK.exists():
            return {'action': 'ABSENT', 'paper_only': True}
        if mode == 'open' and BOOK.exists():
            raise ValueError('one-shot pilot already exists; no reentry or duplicate funding')
        if mode == 'tick' and read(BOOK)['quantity'] == 0:
            return {'action': 'CLOSED', 'paper_only': True}
        expected = {p.name: digest(p) for p in (BOOK, POWDER)}
        data = market(entry=mode != 'tick')
        utc = data['book']['serverTime']
        raw = R / ('sol_short_market_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        atomic(raw, data)
        powder = read(POWDER)
        if powder.get('holdings') or abs(float(powder['cash'])-float(powder['equity'])) > .02:
            raise ValueError('powder warehouse must be flat and cash-reconciled')
        powder['book_key'] = 'gap_close_levered_book'
        if mode in ('preview', 'open'):
            quote = entry_quote(data)
            rates, rate_cache = observed_funding_rates(data)
            funding_delta(utc, utc, quote['quantity'], rates)
            # Assert a current observed rate exists even at zero held duration.
            funding_delta(datetime.fromtimestamp(epoch(utc)-1, timezone.utc).isoformat(), utc,
                          quote['quantity'], rates)
            if mode == 'preview':
                return {'action': 'PREVIEW', 'quote': quote, 'market_receipt': str(raw), 'paper_only': True}
            admission = entry_admission(read(PLAN), quote)
            book, powder = open_short(powder, book_key=KEY, symbol=SYMBOL, funding_usd=75000,
                                      quantity=quote['quantity'], entry_price=quote['entry'],
                                      cover_price=quote['cover'], utc=utc, lot=quote['lot'], funding_cap_usd=75000)
            book.update({'thesis_stop_price': 109.25, 'funding_through_utc': utc,
                         'initial_funding_usd': 75000, 'admission': admission,
                         'simulated_only': True, 'verified_account_access': False,
                         'risk_label': 'opportunistic negative crypto beta',
                         'clip_rules': {'scale': 'half_at_2_flat_by_5'},
                         'maintenance_fraction': quote['conservative_maintenance_fraction']})
            action = 'OPEN_SHORT'
        else:
            book = read(BOOK)
            q = book['quantity']
            cover = depth_vwap(data['book']['orderBook'], 'cover', q)['vwap']
            rates, rate_cache = observed_funding_rates(data, book.get('observed_hourly_funding_rates', []))
            funding = funding_delta(book['funding_through_utc'], utc, q, rates)
            book = mark_short(book, cover_price=cover, funding_delta_usd=funding)
            book['funding_through_utc'] = utc
            action = rail_action(book, thesis_stop_price=book['thesis_stop_price'])
            if book['equity'] <= q*cover*book['maintenance_fraction']:
                action = 'STOP'
            if action in ('STOP', 'FULL_BANK', 'HALF_BANK'):
                quantity = q if action != 'HALF_BANK' else round_quantity(q/2, book['lot'])
                price = depth_vwap(data['book']['orderBook'], 'cover', quantity)['vwap']
                remaining = q-quantity
                # Consume the first fill's asks before valuing what remains.
                residual_cover = (cover*q-price*quantity)/remaining if remaining else price
                book, powder = close_short(book, powder, quantity=quantity, exit_price=price,
                                            remaining_cover_price=residual_cover, utc=utc)
                book['last_exit_reason'] = action
        fresh(utc, 30)
        book['observed_hourly_funding_rates'] = rate_cache
        book['funding_history_status'] = ('ACTUAL_RATE_FALLBACK' if data['funding'].get('history_error') else 'OK')
        if data['funding'].get('history_error'):
            book['last_funding_history_error'] = data['funding']['history_error']
        book.update({'mark_utc': utc, 'updated': utc, 'last_mark_action': action, 'market_receipt': str(raw)})
        powder['updated'] = utc
        # Validate the reporting identity BEFORE either ledger is persisted.
        from book_coverage import _load_short_pilot
        temp = R / '.sol_short_validate.json'
        atomic(temp, book)
        try:
            check = _load_short_pilot(KEY, 'SOL pilot validation', str(temp))
            if check['error']:
                raise ValueError(check['error'])
        finally:
            temp.unlink(missing_ok=True)
        rollback = commit(book, powder, expected, action)
        return {'action': action, 'paper_only': True, 'utc': utc, 'quantity': book['quantity'],
                'entry_price': book['entry_price'], 'mark_price': book['mark_price'],
                'net_liquidation_equity_usd': book['equity'], 'cumulative_pnl_usd': book['equity']-book['seed_usd'],
                'funding_total_usd': book['funding_total_usd'], 'powder_usd': powder['cash'],
                'rollback': rollback, 'market_receipt': str(raw)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    for option in ('preview', 'open', 'tick'):
        group.add_argument('--' + option, action='store_true')
    args = parser.parse_args()
    mode = next(k for k, v in vars(args).items() if v)
    try:
        result = run(mode)
        result['checked_at'] = stamp()
        atomic(REPORT, result)
        print(json.dumps(result, allow_nan=False))
    except Exception as exc:
        error = {'action': 'ERROR', 'mode': mode, 'checked_at': stamp(), 'paper_only': True, 'error': str(exc)}
        atomic(REPORT, error)
        print(json.dumps(error))
        raise SystemExit(1)
