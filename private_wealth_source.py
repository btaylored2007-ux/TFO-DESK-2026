"""Validate source-book observations without importing or mutating capital."""
import json
import hashlib
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


EXPLANATION_SHA256 = 'df35ae788086f699391cc1019a4470598dc0faf4dbbb0f60b5d4c52d29f8794d'


def source_explanation(path, source, total, gap):
    """Check the observed explanation's arithmetic, not the underlying ledger."""
    evidence = Path(path).with_name('private_wealth_source_explanation.json')
    if not evidence.exists():
        return None
    raw = evidence.read_bytes()
    if evidence.is_symlink() or hashlib.sha256(raw).hexdigest() != EXPLANATION_SHA256:
        raise ValueError('Source explanation changed; evidence must be reviewed')
    data = json.loads(raw)
    dec = lambda value: Decimal(str(value))
    if (datetime.fromisoformat(data['asof']) != datetime.fromisoformat(source['ledger_asof']) or
            dec(data['book_sum_usd']) != total or
            dec(data['headline_equity_usd']) != dec(source['headline_equity_usd']) or
            dec(data['headline_powder_usd']) != dec(source['headline_powder_usd']) or
            dec(data['overview_cash_usd']) != dec(source['overview_cash_row_usd'])):
        raise ValueError('Source explanation does not match this snapshot')
    proceeds = sum((dec(row['proceeds_usd']) for row in data['unallocated_proceeds']), Decimal(0))
    for row in data['unallocated_proceeds']:
        if dec(row['proceeds_usd']) - dec(row['principal_usd']) != dec(row['pnl_usd']):
            raise ValueError('Invalid proceeds identity')
    if proceeds != gap:
        raise ValueError('Unallocated proceeds do not explain the source gap')
    cash_gap = dec(data['headline_powder_usd']) - dec(data['overview_cash_usd'])
    if cash_gap != proceeds - dec(data['spec_cash_usd']):
        raise ValueError('Invalid cash scope explanation')
    sleeve = data['sleeve']
    if dec(sleeve['sleeve_equity_usd']) - dec(sleeve['book_equity_usd']) != dec(sleeve['unreflected_principal_usd']):
        raise ValueError('Invalid sleeve explanation')
    return {**data, 'arithmetic_verified': True, 'ledger_replayed': False,
            'execution_verified': False}


def reconcile(path):
    try:
        source = json.loads(Path(path).read_text())
        rows = source['books']
        if len(rows) != 30 or len({r['book'] for r in rows}) != 30:
            raise ValueError('Expected 30 unique source books')
        amounts = [Decimal(str(r['equity_usd'])) for r in rows]
        headline = Decimal(str(source['headline_equity_usd']))
        if not all(v.is_finite() and v >= 0 for v in amounts + [headline]):
            raise ValueError('Invalid source amounts')
        counts = [r['open_positions'] for r in rows]
        if not all(type(v) is int and v >= 0 for v in counts) or sum(counts) != 27:
            raise ValueError('Expected 27 source position rows')
        total = sum(amounts, Decimal(0))
        gap = headline - total
        explanation = source_explanation(path, source, total, gap)
        return {**source, 'book_sum_usd': float(total), 'gap_usd': float(gap),
                'status': 'SOURCE_GAP' if abs(gap) > Decimal('0.01') else 'TOTALS_MATCH',
                'execution_verified': False,
                'explanation': explanation,
                'note': ('Grok attributes the $7,237.77 gap to sale proceeds held outside book totals. The explanation arithmetic checks, but cash ownership and transaction replay remain unverified. No balance was changed.' if explanation else 'Source display observation only; cash ownership, exact position principal and full transaction history remain unverified. Cash is not added again to book equity.')}
    except (OSError, ValueError, KeyError, TypeError, InvalidOperation) as exc:
        return {'status': 'SOURCE_UNAVAILABLE', 'execution_verified': False,
                'books': [], 'note': str(exc)}
