"""Prospective PAPER entry sizing only; never changes holdings or exit rules."""
import contextlib
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

BASE = Path(__file__).resolve().parent
MIN_USD = 50_000.0
MAX_USD = 400_000.0


def positive(value):
    if isinstance(value, bool):
        raise ValueError('Invalid entry notional')
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('Missing or invalid entry notional') from None
    if not math.isfinite(result) or result <= 0:
        raise ValueError('Entry notional must be finite and positive')
    return result


def check_range(proposed, existing):
    proposed = positive(proposed)
    amounts = [positive(x) for x in existing]
    total = math.fsum([proposed, *amounts])
    if total < MIN_USD or total > MAX_USD:
        raise ValueError('Aggregate gross entry notional %.2f outside USD50,000–400,000' % total)
    return {'policy': 'discretionary', 'proposed_usd': proposed,
            'existing_gross_entry_usd': math.fsum(amounts), 'aggregate_entry_usd': total,
            'minimum_usd': MIN_USD, 'maximum_usd': MAX_USD}


@contextlib.contextmanager
def writer_scope(policy):
    if policy == 'registered':
        yield
        return
    lock = BASE / 'reports' / '.tick.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError('Shared tick lock occupied; no discretionary entry') from None
    try:
        yield
    finally:
        lock.rmdir()


def daily_gate(report, now):
    stamp = dt.datetime.fromisoformat(report['asof'].replace('Z', '+00:00'))
    if not 0 <= (now-stamp).total_seconds() <= 1200:
        raise ValueError('Daily risk evidence stale or future')
    if report.get('sydney_date') != now.astimezone(__import__('zoneinfo').ZoneInfo('Australia/Sydney')).date().isoformat():
        raise ValueError('Daily risk evidence belongs to another Sydney day')
    verified = report.get('current_daily_risk_verified') is True
    approved = report.get('daily_risk_basis_allowed') is True and report.get('daily_risk_basis') in ('FUNDER_APPROVED_FIXED_PROXY', 'DESK_MANDATE_FIXED_PROXY')
    if approved:
        expiry = dt.datetime.fromisoformat(report['daily_risk_authorization_expires_utc'].replace('Z', '+00:00'))
        approved = now < expiry
    if not (verified or approved):
        raise ValueError('Current daily risk basis not authorized')
    pnl = float(report['pnl_pct'])
    if not math.isfinite(pnl) or pnl <= -3:
        raise ValueError('Daily loss gate refuses entry')


def aggregate_evidence(symbol, books):
    """Use registry identities, then native entry notionals; never premium/mark proxies.

    Unknown matching instrument structures fail closed. Long/short do not net.
    All registry ledger hashes are checked again before returning.
    """
    import book_coverage
    target = book_coverage.yf_ticker(symbol).upper()
    amounts, evidence, hashes = [], [], {}
    for book in books:
        if book.get('error'):
            raise ValueError('Unreadable registered book: ' + book['key'])
        path = Path(book['path'])
        raw = path.read_bytes()
        hashes[path] = hashlib.sha256(raw).hexdigest()
        ledger = json.loads(raw)
        for position in book.get('positions', []):
            ticker = position.get('ticker')
            if not ticker:
                raise ValueError('Unknown registered instrument identity')
            if ticker.upper() != target:
                continue
            native = (ledger.get('positions') or {}).get(position['sym'])
            if not isinstance(native, dict):
                raise ValueError('Matching instrument lacks supported native entry basis: ' + book['key'])
            # Defined-risk premium is not the underlying's gross entry notional.
            if book.get('kind') == 'convexity' or 'premium' in str(position.get('fallback_basis', '')):
                raise ValueError('Matching derivative requires verified underlying entry notional')
            amount = positive(native.get('notional_usd'))
            if native.get('units') is not None and native.get('entry_px_usd') is not None:
                amount = max(amount, positive(abs(float(native['units']))) * positive(native['entry_px_usd']))
            amounts.append(amount)
            evidence.append({'book': book['key'], 'symbol': position['sym'], 'gross_entry_usd': amount})
    for path, digest in hashes.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Registered ledger changed during aggregate check')
    return amounts, evidence, {str(p): h for p,h in hashes.items()}


def admit(symbol, notional):
    positive(notional)
    try:
        daily_gate(json.loads((BASE/'reports/daily_profit_target_status.json').read_text()), dt.datetime.now(dt.timezone.utc))
        import book_coverage
        books = book_coverage.registry()
        unclaimed, _ = book_coverage.coverage_audit(books, verbose=False)
        if unclaimed:
            raise ValueError('Unclaimed ledgers block aggregate exposure verification')
        before = {Path(b['path']): hashlib.sha256(Path(b['path']).read_bytes()).hexdigest() for b in books}
        books = book_coverage.registry()
        if {Path(b['path']) for b in books} != set(before):
            raise ValueError('Registry changed during sizing check')
        amounts, positions, hashes = aggregate_evidence(symbol, books)
        if any(hashes[str(p)] != h for p,h in before.items()):
            raise ValueError('Registry ledger changed during sizing check')
        result = check_range(notional, amounts)
        result.update(symbol=symbol, positions=positions, ledger_sha256=hashes,
                      checked_utc=dt.datetime.now(dt.timezone.utc).isoformat())
        return result
    except (KeyError, TypeError) as exc:
        raise ValueError('Incomplete sizing/risk evidence: %s' % exc) from exc
