"""Decision evidence only; never submits trades or changes strategy thresholds."""
import datetime as dt
import hashlib
import json
import math
import time
import uuid


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def identity(position):
    fields = ('side', 'units', 'entry', 'entry_px_usd', 'cost', 'notional_usd',
              'premium_usd', 'entry_epoch', 'stop_pct', 'exit_band_pct', 'exit_state')
    return hashlib.sha256(json.dumps({k: position[k] for k in fields if k in position},
                                    sort_keys=True, allow_nan=False).encode()).hexdigest()


def start(ledger, route):
    ledger['exit_checks'] = {'route': route, 'started_utc': utc(), 'legs': {}}


def record(ledger, key, position, status, reason, **evidence):
    ledger['exit_checks']['legs'][key] = {
        'checked_utc': utc(), 'position_identity': identity(position),
        'status': status, 'reason': reason, **evidence}


def finish(ledger):
    ledger['exit_checks']['completed_utc'] = utc()


def ticket(book, symbol, action, side, qty, price, principal, proceeds, pnl, **details):
    return {'ticket_id': str(uuid.uuid4()), 'utc': utc(), 'paper_only': True,
            'verified': True, 'book': book, 'symbol': symbol, 'action': action, 'kind': 'EXIT',
            'side': side, 'qty': qty, 'price': price, 'principal_usd': principal,
            'proceeds_usd': proceeds, 'booked_pnl_usd': round(pnl, 2), **details}


def current_bar(rows, interval_minutes, now=None):
    """A registered daily bar is not labelled a live tick."""
    now = time.time() if now is None else now
    if not rows:
        return False
    try:
        stamp = float(rows[-1][0])
        prices = [float(v) for row in rows for v in row[1:5]]
        return (0 <= now - stamp < interval_minutes * 60
                and all(math.isfinite(v) and v > 0 for v in prices))
    except (TypeError, ValueError, IndexError):
        return False


def assess(ledger, now=None, max_age=1200):
    """Verify current held-leg coverage, not trade eligibility or profitability."""
    now = time.time() if now is None else now
    checks = ledger.get('exit_checks') or {}
    positions = dict(ledger.get('positions') or {})
    for desk, data in (ledger.get('desks') or {}).items():
        positions.update({desk + '/' + sym: p for sym, p in data.get('positions', {}).items()})
    results = []
    for key, position in positions.items():
        row = (checks.get('legs') or {}).get(key) or {}
        failure = None
        try:
            checked = dt.datetime.fromisoformat(row['checked_utc'].replace('Z', '+00:00')).timestamp()
            if not checks.get('completed_utc') or not 0 <= now - checked <= max_age:
                failure = 'Check missing completion, expired or future-dated'
            elif row.get('position_identity') != identity(position):
                failure = 'Position changed since exit check'
            elif row.get('status') == 'WAIT_MARKET':
                quote = row.get('quote') or {}
                session = quote.get('session', quote.get('mark_session'))
                stamp = quote.get('quote_utc', quote.get('mark_quote_utc'))
                if not stamp or session not in ('closed', 'pre', 'post', 'break', 'regular'):
                    failure = 'Market wait has no timestamped session evidence'
            elif row.get('status') == 'EXIT' and row.get('applied') is not True:
                failure = 'Exit signal has no applied disposition'
            elif row.get('status') not in ('HOLD', 'EXIT'):
                failure = row.get('reason') or 'No valid exit decision'
        except (KeyError, ValueError, TypeError, OverflowError):
            failure = 'Missing or invalid exit evidence'
        results.append({'symbol': key, **row, 'verified': failure is None,
                        'verification_error': failure})
    return {'status': 'VERIFIED' if all(r['verified'] for r in results) else 'INCOMPLETE',
            'coverage_verified': all(r['verified'] for r in results),
            'route': checks.get('route'), 'completed_utc': checks.get('completed_utc'),
            'position_count': len(positions), 'legs': results,
            'scope': 'Native registered exit checks; a market/data wait is not an executable fill.'}
