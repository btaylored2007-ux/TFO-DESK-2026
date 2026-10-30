#!/usr/bin/env python3
"""PAPER floor valuation only; no entry, exit, fee debit or strategy tick.

Uses the desk tick mkdir lock plus an optimistic content check for independent
floor writers, which do not share that lock. A detected change aborts that book.
No writer-independent filesystem compare-and-swap exists: the tiny interval
between the final hash check and replace remains a documented concurrency limit.
Missing/invalid quotes leave the entire affected book unchanged.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone

BASE = Path(__file__).resolve().parent
KRAKEN = Path('/Users/test/Applications/KrakenQuantDesk')
FLOORS = {
    'kraken_floor': (KRAKEN / 'kraken_bot/kraken_floor.json', 'USD'),
    'replica_floor': (BASE / 'replica_floor.json', 'USD'),
    'coinspot_floor': (KRAKEN / 'reports/coinspot_floor.json', 'AUD'),
    'cross_asset_floor': (KRAKEN / 'reports/cross_asset_floor.json', 'USD'),
}
# Exact current cross_asset_floor.UNIVERSE identities. Unmapped assets fail closed.
CROSS_PAIRS = {'gold': 'XAUT/USD', 'eur': 'EUR/USD', 'gbp': 'GBP/USD', 'aud': 'AUD/USD'}
REPORT = BASE / 'reports/price_refresh_floors_latest.json'


def utc(epoch=None):
    return datetime.fromtimestamp(time.time() if epoch is None else epoch,
                                  timezone.utc).isoformat().replace('+00:00', 'Z')


def number(value, label, positive=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (positive and value <= 0)):
        raise ValueError('invalid ' + label)
    return float(value)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def get_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'TFO-paper-mark-refresh/1.0'})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def fetch_quotes(spot_pairs, coinspot_coins, fetch=get_json):
    """Return exact-identity native quotes and explicit per-source failures.

    Ticker APIs supply no trade timestamp. retrieved_at is a retrieval receipt,
    never a claim that the last trade occurred at that time.
    """
    result, failures = {}, []
    if spot_pairs:
        try:
            assets = fetch('https://api.kraken.com/0/public/AssetPairs')
            if assets.get('error'):
                raise ValueError(str(assets['error']))
            aliases = {}
            for key, row in assets['result'].items():
                sym = row.get('wsname')
                if sym in spot_pairs:
                    if sym in aliases:
                        raise ValueError('ambiguous exact Kraken pair ' + sym)
                    aliases[sym] = key
            for sym in sorted(set(spot_pairs) - set(aliases)):
                failures.append('Kraken exact pair unavailable: ' + sym)
            if aliases:
                url = ('https://api.kraken.com/0/public/Ticker?pair='
                       + urllib.parse.quote(','.join(aliases.values()), safe=','))
                payload = fetch(url)
                if payload.get('error'):
                    raise ValueError(str(payload['error']))
                obtained = time.time()
                for sym, exact in aliases.items():
                    try:
                        row = payload['result'][exact]
                        quote = {field: float(row[key][0]) for field, key in
                                 [('bid', 'b'), ('ask', 'a'), ('last', 'c')]}
                        result[('kraken', sym)] = {
                            **quote, 'currency': 'USD', 'symbol': sym,
                            'venue': 'kraken', 'exchange_pair': exact,
                            'retrieved_epoch': obtained, 'retrieved_at': utc(obtained),
                            'source': url, 'exchange_timestamp': None}
                    except (ValueError, KeyError, TypeError, IndexError) as exc:
                        failures.append('Kraken ' + sym + ': ' + str(exc))
        except Exception as exc:
            failures.append('Kraken public quotes: ' + str(exc))
    if coinspot_coins:
        try:
            url = 'https://www.coinspot.com.au/pubapi/v2/latest'
            payload = fetch(url)
            obtained = time.time()
            for coin in sorted(coinspot_coins):
                try:
                    row = payload['prices'][coin]
                    result[('coinspot', coin)] = {
                        'bid': float(row['bid']), 'ask': float(row['ask']),
                        'last': float(row.get('last') or row['bid']),
                        'currency': 'AUD', 'symbol': coin, 'venue': 'coinspot',
                        'retrieved_epoch': obtained, 'retrieved_at': utc(obtained),
                        'source': url, 'exchange_timestamp': None}
                except (ValueError, KeyError, TypeError) as exc:
                    failures.append('CoinSpot ' + coin + ': ' + str(exc))
        except Exception as exc:
            failures.append('CoinSpot public quotes: ' + str(exc))
    return result, failures


def quote_key(book, symbol):
    if book == 'coinspot_floor':
        return 'coinspot', symbol
    if book == 'cross_asset_floor':
        if symbol not in CROSS_PAIRS:
            raise ValueError('no verified spot mapping for cross-asset ' + symbol)
        return 'kraken', CROSS_PAIRS[symbol]
    if not symbol.endswith('/USD'):
        raise ValueError('floor symbol is not an exact USD spot pair: ' + symbol)
    return 'kraken', symbol


def mark_book(book, ledger, quotes, now=None):
    """Pure transformation. All positions must validate before a book is saved.

    Kraken/Replica use latest trade (same mark convention as the current daily
    candle close). CoinSpot uses native AUD bid; Cross-Asset uses bid/ask by side.
    Replica's canonical venue_costs model is imported without strategy modules.
    Historical entries, costs, quantities and risk freeze decisions are retained.
    """
    now = time.time() if now is None else now
    out, rows = copy.deepcopy(ledger), []
    # Legacy screen telemetry used NaN for unavailable (unheld) spreads.
    # JSON null states the same missing value without invalid JSON; never
    # normalise financial fields, which must pass the validators below.
    normalised = []
    for symbol, spread in (out.get('screen', {}).get('spreads_bps', {}) or {}).items():
        if isinstance(spread, float) and not math.isfinite(spread):
            out['screen']['spreads_bps'][symbol] = None
            normalised.append('/screen/spreads_bps/' + symbol)
    if normalised:
        out['price_refresh_metadata_repairs'] = {
            'at': utc(now), 'paths': normalised,
            'reason': 'Unavailable screen spread NaN represented as JSON null; no fill or financial value changed.'}
    currency = 'AUD' if book == 'coinspot_floor' else 'USD'
    equity = 0.0
    for desk_name, desk in out['desks'].items():
        unrealized, cost_total = 0.0, 0.0
        for symbol, pos in desk.get('positions', {}).items():
            venue, qsym = quote_key(book, symbol)
            q = quotes[(venue, qsym)]
            if q.get('currency') != currency or q.get('symbol') != qsym or q.get('venue') != venue:
                raise ValueError('quote identity/currency mismatch for ' + symbol)
            age = now - number(q.get('retrieved_epoch'), 'quote retrieval time')
            if not 0 <= age <= 120:
                raise ValueError('stale/future retrieval for ' + symbol)
            bid, ask, last = [number(q.get(k), symbol + ' ' + k, True)
                              for k in ('bid', 'ask', 'last')]
            if ask < bid:
                raise ValueError('crossed quote for ' + symbol)
            side = pos.get('side')
            if side not in ('long', 'short') or (book == 'coinspot_floor' and side != 'long'):
                raise ValueError('invalid side for ' + symbol)
            entry = number(pos.get('entry'), symbol + ' historical entry', True)
            units = number(pos.get('units'), symbol + ' units', True)
            cost = number(pos.get('cost'), symbol + ' historical cost', True)
            entry_epoch = number(pos.get('entry_epoch'), symbol + ' entry time', True)
            if entry_epoch > now:
                raise ValueError('future historical entry timestamp for ' + symbol)
            px = (bid if side == 'long' else ask) if book in ('coinspot_floor', 'cross_asset_floor') else last
            gross = (1 if side == 'long' else -1) * (px / entry - 1) * cost
            pnl = gross
            if book == 'replica_floor':
                import venue_costs
                cls, leverage = pos.get('instrument_class', 'crypto'), pos.get('leverage', 1.0)
                financing = venue_costs.financing(cls, cost, (now - entry_epoch) / 86400., side, leverage)
                exit_est = venue_costs.exit_cost(cls, units * px)
                pnl -= financing + exit_est + number(pos.get('entry_cost', 0.0), 'entry cost')
                pos.update(gross_upnl=round(gross, 2), accrued_financing=round(financing, 2), upnl=round(pnl, 2))
            elif book == 'kraken_floor':
                pos['upnl'] = round(pnl, 2)
            else:
                pos['unrealized'] = pnl
            old_mark = pos.get('mark')
            pos.update(mark=round(px, 8), mark_epoch=now, mark_utc=utc(now),
                       mark_source=q['source'], mark_currency=currency,
                       mark_quote_retrieved_at=q['retrieved_at'])
            unrealized += pnl
            cost_total += cost
            rows.append({'desk': desk_name, 'symbol': symbol, 'currency': currency,
                         'previous_mark': old_mark, 'mark': pos['mark'],
                         'unrealized_native': pnl, 'quote': q})
        desk['unrealized'] = round(unrealized, 2) if book in ('kraken_floor', 'replica_floor') else unrealized
        if 'unmarked_positions' in desk:
            desk['unmarked_positions'] = 0
        equity += number(desk.get('cash'), 'desk cash') + cost_total + desk['unrealized']
    number(equity, 'computed equity')
    out['equity'] = equity
    out['last_price_refresh_utc'] = utc(now)
    if book in ('coinspot_floor', 'cross_asset_floor'):
        out['updated'], out['updated_str'] = now, time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))
    else:
        out['updated'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))
    if isinstance(out.get('book_risk'), dict):
        # Value only. checked_utc/peak/drawdown/frozen describe the last actual
        # risk evaluation and are not relabelled as freshly evaluated here.
        out['book_risk']['equity'] = round(equity, 2)
        out['book_risk']['valuation_utc'] = utc(now)
    return out, rows


@contextmanager
def tick_lock(path):
    path.mkdir()  # Never steal an existing lock, even when it looks stale.
    try:
        yield
    finally:
        path.rmdir()


def atomic_json(path, payload, expected_sha=None):
    raw = (json.dumps(payload, indent=1, allow_nan=False) + '\n').encode()
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '.', delete=False) as f:
        temp = Path(f.name)
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    try:
        if expected_sha is not None and digest(path.read_bytes()) != expected_sha:
            raise RuntimeError('concurrent writer changed ' + str(path) + '; refused overwrite')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return digest(raw)


def create_rollback_backup(raw_snapshots, source_paths, base=BASE):
    """Durably preserve every source's exact bytes before the first ledger write.

    Book keys provide unique filenames even for ledgers outside this directory.
    A receipt binds each original path and digest to its rollback snapshot.
    Any failure aborts the run; an incomplete backup never authorises a write.
    """
    root = base / 'reports/revisions/rollback'
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    folder = root / ('floor-price-refresh-' + stamp)
    folder.mkdir()
    files = []
    for key, raw in raw_snapshots.items():
        dest = folder / (key + '.json')
        with dest.open('xb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if digest(dest.read_bytes()) != digest(raw):
            raise RuntimeError('rollback backup verification failed: ' + str(dest))
        files.append({'book': key, 'source_path': str(source_paths[key]),
                      'backup_path': str(dest), 'sha256': digest(raw), 'bytes': len(raw)})
    receipt = {'created_utc': utc(), 'paper_only': True,
               'status': 'rollback_snapshots_verified_before_any_ledger_write',
               'ledger_writes_started': False, 'files': files}
    atomic_json(folder / 'receipt.json', receipt)
    # Flush directory entries as well as file contents before production writes.
    for directory in (folder, root):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return folder


def entry_issues(base=BASE, floors=None, today=None):
    """Report stale/held entries. Never modifies entry or queue artefacts."""
    today = today or utc()[:10]
    issues = []
    for key, d in (floors or {}).items():
        br = d.get('book_risk', {})
        if d.get('frozen') is not None and d.get('frozen') != br.get('frozen') and 'frozen' in br:
            issues.append({'book': key, 'issue': 'root/nested freeze disagreement',
                           'root_frozen': d.get('frozen'), 'book_risk_frozen': br['frozen'],
                           'action': 'Preserved both; requires capital/peak reconciliation, not price reset.'})
        for name, desk in d.get('desks', {}).items():
            for symbol, p in desk.get('positions', {}).items():
                if key == 'coinspot_floor' and (p.get('meta') or {}).get('deploy') == 'idle_cash_in_desk':
                    issues.append({'book': key, 'desk': name, 'symbol': symbol,
                                   'issue': 'historical entry currency provenance requires verification',
                                   'entry': p.get('entry'), 'entry_epoch': p.get('entry_epoch'),
                                   'action': 'AUD book; imported idle-cash entries resemble USD prices. Preserved historical fill.'})
    for filename in ('fundamental_book.json', 'fundamental_spec_book.json'):
        p = base / 'reports' / filename
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        for field in ('unopened', 'queued'):
            for symbol, info in (d.get(field) or {}).items():
                issues.append({'book': filename[:-5], 'symbol': symbol, 'state': field,
                               'reason': info.get('reason'), 'catalyst_date': info.get('catalyst_date'),
                               'action': 'Preserved; requires fresh research/session/entry gate before opening.'})
    p = base / 'reports/fundamental_entry_queue_recheck_2026-09-08.json'
    if p.exists():
        d = json.loads(p.read_text())
        for row in d.get('candidates', []):
            issues.append({'book': 'fundamental_session_queue', 'symbol': row.get('symbol'),
                           'state': row.get('execution_status'), 'receipt_as_of': d.get('as_of'),
                           'review_expired': str(row.get('review_date') or '') < today,
                           'action': 'Historical receipt; rebuild/reverify current research, never reuse as current entry approval.'})
    return issues


def flat_books(base=BASE, kraken=KRAKEN):
    out = []
    paths = [base / 'reports/carry_paper_book.json', base / 'reports/carry_newnames_probe.json',
             base / 'reports/maker_calibration.json', kraken / 'kraken_bot/bot_state.json']
    # Remaining non-floor members of the current 22-book registry. This is a
    # read-only status inventory; none is passed to a writer by this program.
    paths += [base / 'reports' / name for name in (
        'value_book.json', 'tournament/capacity_probe.json', 'convexity_sleeve.json',
        'fundamental_book.json', 'fundamental_spec_book.json', 'gap_close_levered_book.json',
        'crypto_clip_book.json', 'soxl_weekend_probe.json', 'crypto_mid_clip_book.json',
        'crypto_weekend_rs_probe.json', 'crypto_uni_sui_rs.json', 'crypto_liq_beta_book.json',
        'crypto_avax_ondo_ltc.json', 'crypto_sol_short_pilot.json')]
    for p in paths:
        if p.exists():
            d = json.loads(p.read_text())
            active = bool(d.get('positions') or d.get('position') or d.get('holdings')
                          or d.get('quantity'))
            if not active:
                out.append({'path': str(p), 'flat': True,
                            'pending_order': d.get('pending_order'), 'frozen': d.get('frozen'),
                            'frozen_reason': d.get('frozen_reason'),
                            'action': 'Read only; no open holding price to refresh.'})
    return out


def run(apply=False):
    report = {'checked_at': utc(), 'paper_only': True, 'apply': apply, 'books': [],
              'failures': [], 'positions_refreshed': 0,
              'concurrency_note': 'Shared tick lock plus optimistic file-hash check; independent floor writers do not share the lock.'}
    with tick_lock(BASE / 'reports/.tick.lock'):
        snapshots, raw_snapshots, hashes, pairs, coins = {}, {}, {}, set(), set()
        for key, (path, currency) in FLOORS.items():
            raw = path.read_bytes()
            raw_snapshots[key] = raw
            snapshots[key], hashes[key] = json.loads(raw), digest(raw)
            for desk in snapshots[key]['desks'].values():
                for symbol in desk.get('positions', {}):
                    try:
                        venue, sym = quote_key(key, symbol)
                        (coins if venue == 'coinspot' else pairs).add(sym)
                    except ValueError as exc:
                        report['failures'].append(str(exc))
        quotes, errors = fetch_quotes(pairs, coins)
        report['failures'].extend(errors)
        if apply:
            report['rollback_backup_path'] = str(create_rollback_backup(
                raw_snapshots, {key: value[0] for key, value in FLOORS.items()}))
        for key, (path, currency) in FLOORS.items():
            rec = {'book': key, 'path': str(path), 'currency': currency, 'before_sha256': hashes[key]}
            try:
                if not any(d.get('positions') for d in snapshots[key]['desks'].values()):
                    rec.update(status='flat_no_refresh_needed', positions=[])
                else:
                    updated, rows = mark_book(key, snapshots[key], quotes)
                    if apply:
                        rec['after_sha256'] = atomic_json(path, updated, hashes[key])
                        report['positions_refreshed'] += len(rows)
                    rec.update(status='refreshed' if apply else 'dry_run', positions=rows,
                               equity_native=updated['equity'])
            except Exception as exc:
                rec.update(status='failed_unchanged', error=str(exc))
                report['failures'].append(key + ': ' + str(exc))
            report['books'].append(rec)
        report['entry_issues'] = entry_issues(floors=snapshots)
        report['flat_books'] = flat_books()
        atomic_json(REPORT, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Persist valuations only; default is report-only dry run.')
    args = parser.parse_args()
    try:
        report = run(args.apply)
        print(json.dumps({'report': str(REPORT), 'positions_refreshed': report['positions_refreshed'],
                          'rollback_backup_path': report.get('rollback_backup_path'),
                          'failures': report['failures'],
                          'books': [{k: v for k, v in b.items() if k in ('book', 'status', 'equity_native')}
                                    for b in report['books']]}, indent=2))
        return 1 if report['failures'] else 0
    except FileExistsError:
        print('Refresh refused: desk tick lock already exists; no ledger changed.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
