"""Read-only dashboard adapters. Historical records never certify new fills."""
import hashlib
import json
from pathlib import Path


def first(row, *keys):
    return next((row[k] for k in keys if row.get(k) is not None), None)


def fill_rows(tickets):
    rows = []
    for ticket in tickets:
        sold = ticket.get('sold')
        if isinstance(sold, dict) and sold:
            for symbol, leg in sold.items():
                rows.append({**ticket, **leg, 'symbol': symbol,
                             'fill_id': str(ticket.get('ticket_id')) + ':' + symbol,
                             'booked_pnl_usd': leg.get('pnl_usd'),
                             'principal_usd': leg.get('principal_usd'),
                             'proceeds_usd': leg.get('proceeds_usd'),
                             'fees_usd': leg.get('fees_usd'),
                             'quantity': first(leg, 'qty', 'units'),
                             'price': first(leg, 'px', 'price')})
        else:
            rows.append({**ticket, 'fill_id': ticket.get('ticket_id'),
                         'quantity': first(ticket, 'qty', 'units', 'quantity'),
                         'price': first(ticket, 'px', 'price')})
    return rows


def position_rows(positions, books):
    ledgers = {}
    for book in books:
        try:
            ledgers[book['key']] = (json.loads(Path(book['path']).read_bytes()), book.get('ccy'))
        except (OSError, ValueError, TypeError, KeyError):
            continue
    result = []
    for position in positions:
        row = dict(position)
        ledger, currency = ledgers.get(row.get('book'), ({}, None))
        symbol = first(row, 'symbol', 'sym')
        record = (ledger.get('positions') or {}).get(symbol, {})
        if ledger.get('desks') and isinstance(symbol, str) and '/' in symbol:
            desk, instrument = symbol.split('/', 1)
            record = ledger.get('desks', {}).get(desk, {}).get('positions', {}).get(instrument, {})
        if symbol in (ledger.get('holdings') or {}):
            record = {'units': ledger['holdings'][symbol], 'entry': (ledger.get('entry_px') or {}).get(symbol)}
        units = first(record, 'units', 'qty', 'quantity')
        if units is not None and units == first(row, 'units', 'qty'):
            row['entry'] = first(record, 'entry_px_usd', 'entry', 'entry_px')
            row['entry_currency'] = 'USD' if record.get('entry_px_usd') is not None else record.get('entry_ccy') or currency
            row['mark_source_utc'] = first(record, 'mark_quote_utc', 'mark_utc')
            row['mark_stale'] = record.get('mark_stale')
        result.append(row)
    return result


def ledger_history(books):
    rows, coverage = [], []
    collections = ('closed', 'closed_trades', 'fills', 'lock_fills', 'rebalance_fills',
                   'trade_fills_this_tick', 'thesis_exits', 'recovery_closes', 'balance_closes', 'exit_fills', 'retirement_sales')
    for book in books:
        key, source = book.get('key'), book.get('path')
        before = len(rows)
        error = None
        try:
            path = Path(source)
            if path.suffix != '.json':
                raise ValueError('No supported JSON trade-history source')
            data = json.loads(path.read_bytes())
            if not isinstance(data, dict):
                raise ValueError('Ledger root is not an object')
            containers = [('', data)] + [(name, desk) for name, desk in data.get('desks', {}).items() if isinstance(desk, dict)]
            for field in ('positions', 'closed'):
                positions = data.get(field, {})
                if isinstance(positions, dict):
                    containers.extend((field + '/' + symbol, dict(position, symbol=symbol))
                                      for symbol, position in positions.items() if isinstance(position, dict))
            for desk, container in containers:
                for field in collections + ('capital_events',):
                    values = container.get(field, [])
                    if isinstance(values, dict):
                        values = [dict(v, symbol=v.get('symbol', symbol)) for symbol, v in values.items() if isinstance(v, dict)]
                    if not isinstance(values, list):
                        continue
                    for index, record in enumerate(values):
                        if not isinstance(record, dict):
                            continue
                        if field == 'capital_events' and not isinstance(record.get('sold'), dict):
                            continue  # Funding movements are not trades.
                        legs = record.get('sold') or {first(record, 'symbol', 'sym', 'pair', 'asset', 'coin') or container.get('symbol') or data.get('symbol') or 'Not recorded': record}
                        for symbol, leg in legs.items():
                            if not isinstance(leg, dict):
                                continue
                            raw = {**record, **leg}
                            fingerprint = hashlib.sha256(json.dumps([desk, symbol, raw], sort_keys=True).encode()).hexdigest()
                            usd_pnl = first(raw, 'booked_pnl_usd', 'pnl_usd', 'realised_usd', 'realized_pnl_usd')
                            rows.append({'record_id': key + ':' + fingerprint, 'book': key,
                                         'desk': desk, 'symbol': symbol,
                                         'action': first(raw, 'action', 'type') or ('CLOSE ' + str(raw.get('side', '')) if 'close' in field or field == 'thesis_exits' else raw.get('side') or 'FILL'),
                                         'quantity': first(raw, 'qty', 'units', 'quantity'),
                                         'price': first(raw, 'exit_px_usd', 'px_usd', 'exit_px', 'exit_bid_aud', 'exit', 'px', 'price'),
                                         'recorded_proceeds': first(raw, 'net_proceeds_aud', 'proceeds_usd'),
                                         'recorded_fees': first(raw, 'fee_estimate_aud', 'fees_usd'),
                                         'recorded_pnl': usd_pnl if usd_pnl is not None else first(raw, 'pnl', 'realized_pnl'),
                                         'pnl_currency': 'USD' if usd_pnl is not None else book.get('ccy', 'UNKNOWN'),
                                         'recorded_time': first(raw, 'utc', 'closed_utc', 'exit_utc', 'ts', 't'),
                                         'ticket_id': record.get('ticket_id'),
                                         'source': source, 'location': '/'.join(filter(None, [desk, field, str(index)])),
                                         'note': (first(raw, 'reason', 'note', 'cost_source') or 'Historical ledger record; execution evidence not re-certified') +
                                                 ('; retired book, historical P&L unverified' if book.get('retired') else '') +
                                                 ('; cost integrity: ' + str(raw['cost_drag_verdict']) if raw.get('cost_drag_verdict') else '')})
        except (OSError, ValueError, TypeError) as exc:
            error = str(exc)
        coverage.append({'book': key, 'records': len(rows)-before,
                         'status': 'UNAVAILABLE' if error else 'RETIRED / ARCHIVED' if book.get('retired') else 'RETAINED RECORDS' if len(rows)>before else 'NO RETAINED TRADE ROWS',
                         'source': source, 'error': error})
    # Identical representations are shown once; history is never added to receipt P&L.
    unique = {row['record_id']: row for row in rows}
    return {'rows': sorted(unique.values(), key=lambda r: str(r['recorded_time'] or ''), reverse=True),
            'coverage': coverage, 'scope': 'Retained local ledger records, not a certified complete lifetime blotter. Times and currency are shown as recorded. No historical rows enter receipt-based performance totals.'}
