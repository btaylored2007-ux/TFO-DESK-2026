#!/usr/bin/env python3
"""Read-only Private Wealth observer and receipted cycle coordination.

Only monitoring files are written. No trading module is imported or invoked.
"""
try:  # 2026-10-27 WITA Brett: ISP hijacks port-53 DNS for Kraken -> scoped DoH for Kraken public hosts only (kill: state/entry_engine/KRAKEN_DOH_OFF)
    import os as _kd_os, sys as _kd_sys
    _kd_sys.path.append(_kd_os.path.normpath(_kd_os.path.join(_kd_os.path.dirname(_kd_os.path.abspath(__file__)), '.')))
    import kraken_dns as _kraken_dns; _kraken_dns.install()
except Exception as _kd_e:  # pragma: no cover
    print("kraken_dns unavailable: %r" % _kd_e, file=__import__("sys").stderr)
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
import pickle
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request
import uuid
from zoneinfo import ZoneInfo
from private_wealth_source import reconcile

BASE = Path(__file__).resolve().parent
SKILL = Path('/Users/test/.codex/skills/tfo-sep24-private-wealth-hard-skill')
MANDATE = SKILL / 'references/pasted-mandate-2026-09-24-verbatim.md'
UTC = timezone.utc
SYDNEY = ZoneInfo('Australia/Sydney')


def utcnow():
    return datetime.now(UTC)


def stamp(dt):
    return dt.astimezone(UTC).isoformat()


def parsed(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.astimezone(UTC) if dt.tzinfo else None
    except (ValueError, TypeError):
        return None


def age(value, now):
    dt = parsed(value)
    return (now - dt).total_seconds() if dt else None


def freshness(value, now, fresh=1200, stale=21600):
    seconds = age(value, now)
    status = ('UNKNOWN' if seconds is None else 'CLOCK ERROR' if seconds < -5
              else 'FRESH' if seconds < fresh else 'LAG' if seconds < stale else 'STALE')
    return {'utc': value, 'age_seconds': seconds, 'status': status}


def new_trade_eligibility(reports, risk, now, fresh_s):
    """custodian 2026-09-26: firm_risk-based eligibility (VALID + OK + not halted), with heat room and powder. Per-ticket gates still apply."""
    try:
        f = freshness(risk.get('utc'), now, fresh_s, 3 * fresh_s)
        eq = risk.get('firm_equity_usd')
        import tfo_heat
        used = tfo_heat.clip_heat(load(reports / 'fundamental_book.json').get('positions') or {}, load(reports / 'tfo_ht_crypto.json'))[0]
        cap = tfo_heat.HEAT_CAP_PCT * eq if number(eq) else None
        room = round(cap - used, 2) if cap is not None else None
        powder = round(float(load(reports / 'gap_close_levered_book.json').get('cash') or 0), 2)
        valid, level, halted = risk.get('capital_accounting_valid') is True, risk.get('level'), risk.get('new_entries_halted')
        why = []
        if not valid: why.append('capital accounting not VALID')
        if level != 'OK': why.append('firm risk level %s' % level)
        if halted: why.append('new entries halted')
        if room is None or room <= 0: why.append('no heat room')
        if powder <= 0: why.append('no powder')
        status = 'NO CURRENT DATA' if f['status'] not in ('FRESH', 'LAG') else ('ELIGIBLE' if not why else 'NOT ELIGIBLE')
        return {'status': status, 'firm_risk': ('VALID ' if valid else 'INVALID ') + str(level), 'asof': risk.get('utc'),
                'heat_used_usd': round(used, 2), 'heat_cap_usd': round(cap, 2) if cap is not None else None,
                'heat_room_usd': room, 'powder_usd': powder, 'reasons': why,
                'basis': 'firm_risk VALID/OK/not halted; heat at live stops vs %.0f%% firm cap; powder = GCL warehouse cash. Per-ticket gates still apply.' % (100 * tfo_heat.HEAT_CAP_PCT,)}
    except Exception as exc:  # noqa: BLE001
        return {'status': 'NO CURRENT DATA', 'reasons': [str(exc)[:160]]}


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


_LOAD_CACHE = {}   # tfo_lagfix_20261031: build() re-parsed ~2,550 JSON files per call (3 calls/tick) -> 1 core pinned 24/7; reparse only when (mtime_ns, size) changes


def load(path, default=None):
    try:
        key = str(path); st = os.stat(key); sig = (st.st_mtime_ns, st.st_size)
        hit = _LOAD_CACHE.get(key)
        if hit is None or hit[0] != sig:
            hit = (sig, pickle.dumps(json.loads(Path(path).read_text()), protocol=pickle.HIGHEST_PROTOCOL)); _LOAD_CACHE[key] = hit
        return pickle.loads(hit[1])   # fresh copy each call: callers may mutate
    except (OSError, ValueError):
        return {} if default is None else default


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextmanager
def lock(path, blocking=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield


def request_json(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'TFO-Paper-Observer/1.0'})
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.load(response)


def kraken_trade():
    payload = request_json('https://api.kraken.com/0/public/Trades?pair=ETHUSD&count=1')
    if payload.get('error'):
        raise ValueError(str(payload['error']))
    rows = next(value for key, value in payload['result'].items() if key != 'last')
    row = max(rows, key=lambda item: float(item[2]))
    price = float(row[0])
    if not number(price) or price <= 0:
        raise ValueError('Invalid Kraken trade')
    return {'ETH/USD': {'price': price, 'utc': stamp(datetime.fromtimestamp(float(row[2]), UTC)),
                        'class': 'LIVE', 'venue': 'Kraken', 'source': 'Kraken public Trades',
                        'retrieved_utc': stamp(utcnow()), 'execution_evidence': False}}


def kraken_trade_safe():
    """Degrade gracefully (2026-10-30 pwrecover): a Kraken error such as 'EGeneral:No data'
    or a network failure marks the tape STALE instead of crashing the cycle."""
    try:
        return kraken_trade()
    except Exception as exc:
        return {'ETH/USD': {'price': None, 'utc': None, 'class': 'STALE', 'venue': 'Kraken',
                            'source': 'Kraken public Trades', 'retrieved_utc': stamp(utcnow()),
                            'execution_evidence': False, 'feed_error': f'{type(exc).__name__}: {exc}'[:200]}}


def quote_class(quote, now):
    seconds = age(quote.get('utc'), now)
    if seconds is None:
        return 'UNKNOWN'
    if seconds < -5:
        return 'CLOCK ERROR'
    claimed = quote.get('class', 'UNKNOWN')
    if claimed == 'LIVE':
        # An aged public print is never made live by fetching it again.
        return 'LIVE' if seconds < 60 else 'STALE'
    return claimed if seconds < 1200 else 'STALE'


def cadence(rails, tape, now):
    nearest = []
    for rail in rails:
        if rail.get('current_position_verified') is not True:
            continue
        quote = tape.get(rail.get('symbol'), {})
        px = quote.get('price')
        if quote_class(quote, now) != 'LIVE' or not number(px) or px <= 0:
            continue
        if quote.get('venue') != 'Kraken' and quote.get('session') != 'REGULAR':
            continue
        if rail.get('closed') or rail.get('dirty'):
            continue
        fill = rail.get('fill')
        if not number(fill) or fill <= 0 or not .4 <= px / fill <= 2.5:
            continue
        for kind in ('stop', 'half', 'flat'):
            if kind == 'half' and rail.get('half_done'):
                continue
            line = rail.get(kind)
            if number(line) and line > 0:
                distance = abs(px / line - 1) * 10000
                nearest.append({'book': rail.get('book'), 'symbol': rail.get('symbol'),
                                'rail': kind, 'line': line, 'price': px,
                                'distance_bp': round(distance, 2), 'quote_utc': quote.get('utc')})
    nearest.sort(key=lambda item: item['distance_bp'])
    return {'minutes': 5 if nearest and nearest[0]['distance_bp'] <= 50 else 15,
            'nearest': nearest[:6], 'rule': 'LIVE only, within 50bp; otherwise 15m'}


def performance(receipts):
    """Measure only explicitly receipted exits; unknown costs never become zero."""
    tickets, issues, conflicts = {}, [], set()
    for receipt in receipts:
        for ticket in receipt.get('paper_tickets', []):
            if not isinstance(ticket, dict):
                issues.append('Unstructured ticket excluded')
                continue
            key = ticket.get('ticket_id')
            if not key:
                issues.append('Ticket without stable ID excluded')
                continue
            if key in conflicts:
                continue
            if key in tickets and tickets[key] != ticket:
                issues.append('Conflicting ticket ID: ' + str(key))
                conflicts.add(key)
                tickets[key] = {'conflict': True}
            else:
                tickets[key] = ticket
    groups = {}
    for ticket in sorted(tickets.values(), key=lambda row: row.get('utc', '')):
        native_exit = ticket.get('kind') == 'EXIT' and ticket.get('paper_only') is True
        if not native_exit and ticket.get('action') not in (
                'SELL', 'HALF', 'FLAT', 'STOP', 'COVER',
                'stop_out', 'catalyst_expired', 'scale_out', 'close_runner'):
            continue
        if ticket.get('verified') is not True or not parsed(ticket.get('utc')):
            issues.append('Unverified or undated exit excluded')
            continue
        group = groups.setdefault(ticket.get('strategy') or ticket.get('book') or 'Unattributed', [])
        group.append(ticket)
    result = []
    for strategy, rows in groups.items():
        booked = [r.get('booked_pnl_usd') for r in rows]
        cost_known = all(number(r.get('fees_usd')) and r['fees_usd'] >= 0
                         and number(r.get('slippage_estimate_usd')) and r['slippage_estimate_usd'] >= 0
                         and number(r.get('gross_pnl_usd')) and bool(r.get('cost_source')) for r in rows)
        pnl = [r['gross_pnl_usd'] - r['fees_usd'] - r['slippage_estimate_usd'] for r in rows] if cost_known else []
        cumulative = peak = drawdown = 0
        for value in pnl:
            cumulative += value
            peak = max(peak, cumulative)
            drawdown = max(drawdown, peak - cumulative)
        result.append({'strategy': strategy, 'exit_tranches': len(rows),
                       'booked_pnl_usd': round(sum(booked), 2) if all(number(v) for v in booked) else None,
                       'fees_usd': round(sum(r['fees_usd'] for r in rows), 2) if cost_known else None,
                       'slippage_estimate_usd': round(sum(r['slippage_estimate_usd'] for r in rows), 2) if cost_known else None,
                       'net_estimate_usd': round(sum(pnl), 2) if cost_known else None,
                       'expectancy_per_exit_usd': round(sum(pnl) / len(pnl), 2) if pnl else None,
                       'max_realised_drawdown_usd': round(drawdown, 2) if pnl else None,
                       'win_rate_pct': round(100 * sum(v > 0 for v in pnl) / len(pnl), 1) if pnl else None,
                       'status': 'ESTIMATED NET' if cost_known else 'COSTS UNKNOWN'})
    return {'strategies': result, 'issues': sorted(set(issues)),
            'tickets': sorted([t for t in tickets.values() if not t.get('conflict')], key=lambda t: t.get('utc', '')),
            'scope': 'Private Wealth receipted exit tranches only; not independent trades or whole-firm NAV drawdown.'}


def historical_positions():
    # The source is a Markdown table, not a ledger. Preserve that provenance.
    rows = []
    for line in MANDATE.read_text().splitlines():
        if not line.startswith('| ') or line.startswith('| Book') or line.startswith('|---'):
            continue
        parts = [part.strip() for part in line.strip('|').split('|')]
        if len(parts) == 6:
            rows.append(dict(zip(('book', 'symbol', 'side', 'qty', 'entry', 'note'), parts)))
    return rows


def cycle_start(cycle, reports):
    """Apply receipted timing corrections without rewriting historical cycles."""
    started = parsed(cycle.get('started_utc'))
    ident = cycle.get('cycle_id', '')
    correction = load(reports / 'private_wealth_timing_corrections.json').get(ident)
    if not isinstance(correction, dict):
        return started
    try:
        if str(uuid.UUID(ident)) != ident:
            return started
        archive = reports / 'private_wealth_cycles' / (ident + '.json')
        corrected = parsed(correction.get('claimed_started_utc'))
        completed = parsed(cycle.get('utc'))
        if (corrected and completed and corrected <= completed
                and correction.get('original_started_utc') == cycle.get('started_utc')
                and correction.get('evidence') and load(archive) == cycle
                and hashlib.sha256(archive.read_bytes()).hexdigest() == correction.get('archive_sha256')):
            return corrected
    except (OSError, ValueError, TypeError):
        pass
    return started


def account_summary(reports, cycle, risk, perf, now):
    from tfo_daily_pnl import calculate, apply_entry_integrity, apply_baseline_authorization
    board = cycle.get('boards', {}).get('Books', {})
    books = board.get('data', {}).get('books', [])
    keys = [b.get('key') for b in books]
    complete = (bool(books) and len(keys) == len(set(keys))
                and set(keys) == set(risk.get('book_keys', []))
                and risk.get('capital_accounting_valid') is True
                and all(not b.get('error') and number(b.get('start_usd'))
                        and number(b.get('equity_usd')) for b in books))
    seed = round(sum(b['start_usd'] for b in books), 2) if complete else None
    from coinspot_retirement import verified_retirement
    retired = verified_retirement(reports.parent)
    if seed is not None and retired and 'coinspot_floor' not in keys:
        seed = round(seed + retired['retained_funding_basis_usd'], 2)
    if seed is not None:
        import book_archive
        seed = round(seed + book_archive.retained_basis_usd(exclude=keys), 2)
    equity = round(sum(b['equity_usd'] for b in books), 2) if complete else None
    cash_complete = complete and all(number(b.get('cash_usd')) for b in books)
    total_cash = round(sum(b['cash_usd'] for b in books), 2) if cash_complete else None
    deployed = round(equity - total_cash, 2) if cash_complete else None
    warehouse_cash = next((b.get('cash_usd') for b in books if b.get('key') == 'gap_close_levered_book'), None)
    day = now.astimezone(SYDNEY).date().isoformat()
    baseline = load(reports / 'daily_pnl_days' / (day + '.json')).get('baseline')
    daily = calculate(baseline, risk, now)
    integrity = load(reports / 'coinspot_entry_integrity_20260921.json')
    apply_entry_integrity(daily, baseline, risk, integrity, retired)
    apply_baseline_authorization(daily, baseline,
        __import__('tfo_daily_pnl').baseline_authorization(reports, day), now)
    warning = ('CoinSpot historical entry currencies remain unverified.'
               if integrity.get('block_new_entries') is True else '')
    if retired:
        warning = ('CoinSpot is retired. Its historical P&L remains unverified; '
                   'retirement proceeds are an internal transfer, not verified profit.')
    rows = []
    daily_books = {b['book']: b for b in daily.get('book_contributions', [])}
    for b in books:
        valid = not b.get('error') and number(b.get('start_usd')) and number(b.get('equity_usd'))
        rows.append({'book': b.get('key'), 'seed_usd': b.get('start_usd'),
                     'cash_usd': b.get('cash_usd'),
                     'deployed_powder_usd': round(b['equity_usd'] - b['cash_usd'], 2)
                         if valid and number(b.get('cash_usd')) else None,
                     'equity_usd': b.get('equity_usd'),
                     'pnl_since_seed_usd': round(b['equity_usd']-b['start_usd'], 2) if valid else None,
                     'daily_pnl_usd': daily_books.get(b.get('key'), {}).get('pnl_usd')})
    tickets = [t for t in perf.get('tickets', []) if parsed(t.get('utc'))
               and parsed(t['utc']).astimezone(SYDNEY).date().isoformat() == day]
    booked = sum(t['booked_pnl_usd'] for t in tickets) if tickets and all(number(t.get('booked_pnl_usd')) for t in tickets) else None
    known_tickets = [t for t in tickets if number(t.get('booked_pnl_usd'))]
    return {'asof': board.get('verified_utc'), 'seed_usd': seed,
            'total_cash_usd': total_cash, 'warehouse_cash_usd': warehouse_cash,
            'remaining_powder_usd': total_cash, 'deployed_powder_usd': deployed,
            'powder_basis': 'Deployed is net marked capital (equity minus recorded cash), not gross exposure or entry cost. Remaining is recorded cash across all books (GCL warehouse powder plus book working cash).',
            'other_books_cash_usd': round(total_cash-warehouse_cash, 2) if total_cash is not None and number(warehouse_cash) else None,
            'cash_scope': 'All active books, USD-equivalent recorded cash; not all cash is approved for redeployment. Position valuations excluded.',
            'seed_basis': 'Net documented local funding bases, including withdrawals and paired transfers; not original lifetime deposits.',
            'equity_usd': equity, 'pnl_since_seed_usd': round(equity-seed, 2) if complete else None,
            'return_since_seed_pct': round(100*(equity-seed)/seed, 2) if complete and seed > 0 else None,
            'daily_pnl_usd': daily.get('pnl_usd'), 'daily_status': daily['status'],
            'daily_asof': daily.get('asof'), 'daily_note': daily['note'],
            'daily_risk_basis_allowed': daily.get('daily_risk_basis_allowed') is True,
            'daily_risk_basis': daily.get('daily_risk_basis'),
            'daily_risk_authorization_expires_utc': daily.get('daily_risk_authorization_expires_utc'),
            'daily_open_usd': daily.get('baseline_equity_usd'),
            'daily_target_usd': 15000.0, 'sydney_date': day,
            'booked_today_receipted_usd': round(booked, 2) if booked is not None else None,
            'booked_today_known_usd': round(sum(t['booked_pnl_usd'] for t in known_tickets), 2) if known_tickets else None,
            'booked_today_unknown_count': len(tickets) - len(known_tickets),
            'booked_scope': 'Verified local cycle receipts only; not a complete whole-desk realised P&L history.',
            'warning': warning, 'books': rows}


def _exit_gap(route):
    legs = [l for l in route.get('legs') or [] if l.get('verified') is not True]
    why = sorted({(l.get('verification_error') or l.get('why') or 'unverified') for l in legs})
    return '%s (%d of %d legs unverified: %s)' % (route.get('book') or route.get('route') or '?', len(legs),
                                                  route.get('position_count') or len(route.get('legs') or []), ', '.join(why)[:160])


def build(base=BASE, now=None):
    now = now or utcnow()
    reports = Path(base) / 'reports'
    cycle = load(reports / 'private_wealth_cycle_latest.json')
    local_only = load(reports / 'private_wealth_operating_mode.json').get('mode') == 'LOCAL_ONLY'
    if local_only and cycle.get('desk_scope') != 'LOCAL_ONLY':
        cycle = {}
    risk = load(reports / 'firm_risk.json')
    observed_risk = cycle.get('risk', {})
    # Newer receipts keep the complete risk observation in cycle-ID evidence.
    try:
        ident = cycle.get('cycle_id', '')
        if str(uuid.UUID(ident)) == ident and observed_risk.get('utc'):
            evidence_path = reports / 'private_wealth_verification' / (ident + '-evidence.json')
            if load(evidence_path).get('local_observation') == observed_risk:
                observed_risk = {**observed_risk, 'observation_utc': observed_risk['utc'],
                                 'evidence': str(evidence_path)}
    except (ValueError, TypeError, AttributeError):
        pass
    observed_time = parsed(observed_risk.get('observation_utc'))
    if (observed_risk.get('scope') in ('LOCAL_22_BOOK_REGISTRY_ONLY', 'LOCAL_BOOK_REGISTRY_ONLY')
            and observed_time and observed_time <= now + timedelta(seconds=5)
            and observed_time > (parsed(risk.get('utc')) or datetime.min.replace(tzinfo=UTC))
            and observed_risk.get('evidence')):
        evidence_path = Path(observed_risk['evidence']).resolve()
        if evidence_path.is_relative_to(reports.resolve()) and evidence_path.is_file():
            evidence = load(evidence_path)
            embedded = evidence.get('local_observation')
            matching_embedded = (isinstance(embedded, dict)
                                 and embedded == cycle.get('risk')
                                 and parsed(embedded.get('utc')) == observed_time)
            if parsed(evidence.get('verified_utc')) == observed_time or matching_embedded:
                risk = {**observed_risk, 'utc': stamp(observed_time)}
    watch = load(reports / 'private_wealth_watch.json')
    gate = load(reports / 'private_wealth_gate.json')
    # Recovery receipts may carry status metadata; only quote objects are tape.
    tape = {symbol: dict(quote) for symbol, quote in cycle.get('tape', {}).items()
            if isinstance(quote, dict)}
    for symbol, quote in watch.get('tape', {}).items():
        if (parsed(quote.get('utc')) or datetime.min.replace(tzinfo=UTC)) >= (parsed(tape.get(symbol, {}).get('utc')) or datetime.min.replace(tzinfo=UTC)):
            tape[symbol] = quote
    for symbol, quote in tape.items():
        quote['observed_class'] = quote_class(quote, now)
        quote['age_seconds'] = age(quote.get('utc'), now)
    plan = cadence(cycle.get('rails', []), tape, now)
    # Custodian 2026-09-25: under HEAD_TRADER_ONLY the desk cycle is the hourly desk_tick
    # (which builds the receipt, ~63 min spacing) while exits run continuously in the HT
    # rails runners. Cadence follows that; stale runners raise RAILS_RUNNER_STALE below.
    ht_only = (reports / 'HEAD_TRADER_ONLY.flag').exists()
    if ht_only and not plan.get('nearest'):
        plan.update({'minutes': 70, 'rule': 'HEAD_TRADER_ONLY: hourly desk_tick receipt plus HT rails runners (crypto 60s, equity 300s); 70m'})
    # custodian 2026-09-26: freshness follows the recorded cycle cadence (70m) + 10m grace, not a fixed 20m.
    # 2026-10-03 dashboard_interactive fix: computed AFTER the HEAD_TRADER_ONLY 70m override. It used to be
    # computed before it (15m + 10m = 25m), so RISK_STALE and BOARDS_UNVERIFIED fired falsely ~35 min of every hour.
    cad_fresh = int(((plan or {}).get('minutes') or 60) * 60 + 600); cad_stale = 3 * cad_fresh
    last = parsed(cycle.get('utc'))
    started = cycle_start(cycle, reports)
    anchor = started if started and last and started <= last else last
    due = anchor + timedelta(minutes=plan['minutes']) if anchor else now
    plan.update({'due_utc': stamp(due), 'due': now >= due,
                 'anchor_utc': stamp(anchor) if anchor else None,
                 'overdue_seconds': max(0, (now - due).total_seconds()),
                 'last_completed_utc': cycle.get('utc')})
    alerts = []
    def alert(code, message):
        alerts.append({'code': code, 'message': message})
    source_books = ({'status': 'RETIRED'} if local_only else reconcile(reports / 'private_wealth_source_books.json'))
    if source_books['status'] == 'SOURCE_GAP':
        alert('SOURCE_ACCOUNTING_GAP',
              f"Grok source book rows total ${source_books['book_sum_usd']:,.2f}; "
              f"headline exceeds them by ${source_books['gap_usd']:,.2f}. Full source ledger reconciliation is required.")
    elif source_books['status'] == 'SOURCE_UNAVAILABLE':
        alert('SOURCE_UNAVAILABLE', 'Private Wealth source book evidence is unavailable or invalid.')
    if not last or now > due + timedelta(minutes=2):
        alert('CYCLE_OVERDUE', 'No completed desk cycle within its cadence plus two-minute grace.')
    if last and last > now + timedelta(seconds=5):
        alert('CLOCK_ERROR', 'The cycle timestamp is in the future.')
    watch_age = age(watch.get('utc'), now)
    if watch_age is None or not 0 <= watch_age < 180:
        alert('WATCHDOG_STALE', 'Independent watchdog has no healthy receipt within three minutes.')
    if freshness(risk.get('utc'), now, cad_fresh, cad_stale)['status'] != 'FRESH':
        alert('RISK_STALE', 'Local risk report is missing, stale, or has an invalid timestamp.')
    if risk.get('capital_accounting_valid') is not True or risk.get('coverage_audit_clean') is not True:
        alert('LOCAL_ACCOUNTING', 'Local capital accounting or coverage audit is not clean.')
    if cycle.get('reconciliation_defect', {}).get('status') != 'PASS':
        alert('BOOK_RECONCILIATION', cycle.get('reconciliation_defect', {}).get('reason') or 'Private Wealth ledger reconciliation is unverified.')
    # 2026-09-28: exit-coverage health scores V1's real books only. Separate desks (NON_V1_EXIT_SCOPE, e.g. the
    # Kraken-desk cross_asset_floor) are listed as neutral notes, never ATTENTION. A V1 gap still alerts.
    from private_wealth_cycle_receipt import NON_V1_EXIT_SCOPE
    notes = []
    sleeves = cycle.get('boards', {}).get('Sleeves', {}).get('data', {}) or {}
    all_routes = list(sleeves.get('remaining_exit_routes', [])) + list(sleeves.get('non_v1_exit_routes', []))
    non_v1 = [route for route in all_routes if route.get('book') in NON_V1_EXIT_SCOPE]
    for route in non_v1:
        notes.append({'code': 'NON_V1_EXIT_CHECK_PENDING', 'book': route.get('book'),
                      'message': 'Separate desk (not V1 capital): ' + _exit_gap(route)})
    if sleeves.get('status') == 'COVERAGE_UNAVAILABLE':
        v1_routes = [route for route in sleeves.get('remaining_exit_routes', []) if route.get('book') not in NON_V1_EXIT_SCOPE]
        pending = [route for route in v1_routes if route.get('status') != 'REGISTERED_WAITING_FOR_SETTLED_BAR']
        legacy_non_v1_only = (bool(non_v1) and not v1_routes and 'non_v1_exit_routes' not in sleeves
                              and not [t for t in cycle.get('ticket_evidence_issues') or [] if t.get('book') not in NON_V1_EXIT_SCOPE])
        if not legacy_non_v1_only:  # pre-scope receipts whose only gap is a separate desk are not a V1 gap
            alert('EXIT_COVERAGE_UNAVAILABLE',
                  f'V1 exit verification incomplete for {len(pending)} book(s): ' + '; '.join(_exit_gap(route) for route in pending) + '. See Sleeves for leg-level checks.'
                  if pending else 'V1 exit verification remains incomplete; see Sleeves for details.')
    if watch.get('dashboard_ok') is not True:
        alert('DASHBOARD_DOWN', 'Dashboard health probe failed or has not run.')
    if watch.get('feed_error') or not tape or any(q['observed_class'] in ('STALE', 'UNKNOWN', 'CLOCK ERROR') for q in tape.values() if q.get('venue') == 'Kraken' or q.get('class') == 'LIVE'):
        alert('FEED_UNAVAILABLE', 'A required live feed is missing or aged; no fresh-price claim is permitted.')
    if gate.get('active'):
        expiry = parsed(gate['active'].get('expires_utc'))
        if not expiry or now > expiry:
            alert('CYCLE_INTERRUPTED', 'An unfinished cycle needs reconciliation; it must not be replayed.')
    if ht_only:
        stale_runners = []
        crypto_log = reports.parent / 'logs' / 'ht-crypto-rails.log'
        try:
            if now.timestamp() - crypto_log.stat().st_mtime > 300: stale_runners.append('ht-crypto-rails (60s)')
        except OSError:
            stale_runners.append('ht-crypto-rails (no log)')
        eq = load(reports / 'ht_equity_rails_latest.json')
        eq_age = age(eq.get('utc'), now)
        if eq.get('execute') is not True or eq_age is None or not -5 <= eq_age < 900:
            stale_runners.append('ht-equity-rails (300s)')
        if stale_runners:
            alert('RAILS_RUNNER_STALE', 'Head Trader exit rails not ticking: ' + ', '.join(stale_runners))
    if watch.get('observer_error'):
        alert('OBSERVER_ERROR', watch['observer_error'])
    boards = {}
    for name in ('Overview', 'Hunt', 'Trade', 'Sleeves', 'Books', 'Positions', 'Research', 'Mandate'):
        board = cycle.get('boards', {}).get(name, {})
        boards[name] = {'freshness': freshness(board.get('verified_utc'), now, cad_fresh, cad_stale), 'data': board.get('data')}
    stale_boards = [name for name, board in boards.items() if board['freshness']['status'] != 'FRESH']
    if stale_boards:
        alert('BOARDS_UNVERIFIED', 'Boards needing verification: ' + ', '.join(stale_boards))
    paths = sorted((reports / 'private_wealth_cycles').glob('*.json'))
    perf = performance([load(path) for path in paths] + [cycle])
    authority = load(reports / 'private_wealth_authority.json')
    local_perf = performance([row for row in (load(path) for path in paths) if row.get('desk_scope') == 'LOCAL_ONLY'] + [cycle]) if local_only else perf   # tfo_lagfix_20261031: one load per receipt (was two)
    from private_wealth_views import fill_rows, ledger_history, position_rows
    history_books = list(cycle.get('boards', {}).get('Books', {}).get('data', {}).get('books', []))
    if local_only:
        from coinspot_retirement import verified_retirement
        retirement = verified_retirement(reports.parent)
        if retirement:
            history_books = [b for b in history_books if b.get('key') != 'coinspot_floor']
            history_books.append({'key': 'coinspot_floor', 'ccy': 'AUD', 'retired': True,
                                  'path': retirement['archived_ledger_path']})
    history = ledger_history(history_books) if local_only else None
    from funder_cash_authorization import current as funding_authority
    return {'utc': stamp(now), 'sydney': now.astimezone(SYDNEY).isoformat(), 'paper_only': True,
            'authority': authority,
            'funding_authority': funding_authority(reports, now) if local_only else None,
            'hunt_latest': load(reports / 'private_wealth_hunt_latest.json') if local_only else None,
            'target_24h': load(reports / 'tfo_24h_target_latest.json') if local_only else None,
            'account_summary': account_summary(reports, cycle, risk, local_perf, now) if local_only else None,
            'fills': fill_rows(local_perf['tickets']), 'ledger_history': history,
            'position_rows': position_rows(cycle.get('boards', {}).get('Positions', {}).get('data', {}).get('positions', []), cycle.get('boards', {}).get('Books', {}).get('data', {}).get('books', [])) if local_only else [],
            'health': 'ATTENTION' if alerts else 'OK', 'alerts': alerts, 'health_notes': notes, 'cadence': plan,
            'cycle': cycle, 'cycle_freshness': freshness(cycle.get('utc'), now, cad_fresh, cad_stale),
            'watchdog': watch, 'watchdog_freshness': freshness(watch.get('utc'), now, 180, 180),
            'local_risk': {k: risk.get(k) for k in ('utc', 'firm_equity_usd', 'firm_peak_usd', 'firm_drawdown_pct', 'books_readable', 'books_total', 'positions', 'capital_accounting_valid', 'coverage_audit_clean')},
            'risk_freshness': freshness(risk.get('utc'), now, cad_fresh, cad_stale),
            'new_trade_eligibility': new_trade_eligibility(reports, risk, now, cad_fresh) if local_only else None, 'boards': boards, 'tape': tape,
            'desk_scope': 'LOCAL_ONLY' if local_only else 'SOURCE_PARALLEL',
            'performance': local_perf,
            'source_books': None if local_only else source_books, 'historical_positions': [] if local_only else historical_positions(),
            'mandate': (SKILL / 'references/local-only-amendment.md').read_text() if local_only else MANDATE.read_text(),
            'monitoring_mandate': (SKILL / 'references/local-only-amendment.md').read_text() if local_only else (SKILL / 'references/permanent-monitoring-2026-09-24.md').read_text(),
            'new_risk_ready': False if alerts else cycle.get('new_risk_ready') is True}


def tick(base=BASE, fetch=True, notify=True):
    reports = Path(base) / 'reports'
    with lock(reports / '.private_wealth_observer.lock', blocking=False):
        old = load(reports / 'private_wealth_watch.json')
        state = {'utc': stamp(utcnow()), 'tape': old.get('tape', {}), 'feed_error': None}
        if fetch:
            try:
                state['tape'] = kraken_trade()
            except Exception as exc:
                state['feed_error'] = str(exc)[:200]
        try:
            state['dashboard_ok'] = request_json('http://127.0.0.1:18812/healthz').get('ok') is True
        except Exception:
            state['dashboard_ok'] = False
        atomic(reports / 'private_wealth_watch.json', state)
        report = build(base)
        codes = sorted(item['code'] for item in report['alerts'])
        previous = old.get('alert_codes', [])
        state['alert_codes'] = codes
        state['last_transition_utc'] = old.get('last_transition_utc')
        if codes != previous:
            state['last_transition_utc'] = stamp(utcnow())
            event = {'utc': state['last_transition_utc'], 'raised': sorted(set(codes) - set(previous)),
                     'recovered': sorted(set(previous) - set(codes)), 'alerts': report['alerts']}
            with (reports / 'private_wealth_alerts.jsonl').open('a') as out:
                out.write(json.dumps(event) + '\n')
            if notify:
                message = 'Desk attention: ' + ', '.join(codes) if codes else 'Desk monitoring recovered.'
                script = 'on run argv\ndisplay notification (item 1 of argv) with title "TFO PAPER desk"\nend run'
                try:
                    delivery = subprocess.run(['/usr/bin/osascript', '-e', script, message], capture_output=True, timeout=10)
                    state['notification_accepted'] = delivery.returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    state['notification_accepted'] = False
        else:
            state['notification_accepted'] = old.get('notification_accepted')
        atomic(reports / 'private_wealth_watch.json', state)
        atomic(reports / 'private_wealth_monitor_latest.json', build(base))
        return {'health': report['health'], 'alerts': codes, 'cadence': report['cadence']}


def begin(base=BASE, now=None, force=False):
    now = now or utcnow()
    reports = Path(base) / 'reports'
    with lock(reports / '.private_wealth_gate.lock'):
        state = load(reports / 'private_wealth_gate.json')
        if state.get('active'):
            expiry = parsed(state['active'].get('expires_utc'))
            return {'run': False, 'reason': 'BUSY' if expiry and expiry > now else 'RECOVERY_REQUIRED', 'active': state['active']}
        status = build(base, now)
        if not status['cadence']['due'] and not force:  # force: custodian on-demand cycle (never bypasses BUSY/RECOVERY)
            return {'run': False, 'reason': 'NOT_DUE', 'cadence': status['cadence']}
        active = {'cycle_id': str(uuid.uuid4()), 'started_utc': stamp(now),
                  'expires_utc': stamp(now + timedelta(minutes=10))}
        atomic(reports / 'private_wealth_gate.json', {'active': active})
        return {'run': True, **active, 'cadence': status['cadence'], 'alerts': status['alerts']}


def complete(receipt, base=BASE, now=None):
    now = now or utcnow()
    reports = Path(base) / 'reports'
    ident = receipt.get('cycle_id', '')
    if not ident or str(uuid.UUID(ident)) != ident:
        raise ValueError('A valid claimed cycle_id is required')
    with lock(reports / '.private_wealth_gate.lock'):
        state = load(reports / 'private_wealth_gate.json')
        active = state.get('active') or {}
        path = reports / 'private_wealth_cycles' / (ident + '.json')
        archived = load(path)
        receipt = dict(receipt)
        receipt.setdefault('started_utc', active.get('started_utc') or archived.get('started_utc'))
        if path.exists():
            if load(path) != receipt:
                raise ValueError('Cycle ID already recorded with different contents')
            if active.get('cycle_id') == ident:
                atomic(reports / 'private_wealth_cycle_latest.json', receipt)
                atomic(reports / 'private_wealth_gate.json', {'active': None, 'last_cycle_id': ident})
            return {'status': 'ALREADY_RECORDED', 'cycle_id': ident}
        if active.get('cycle_id') != ident:
            raise ValueError('Receipt does not match active cycle')
        if receipt.get('started_utc') != active.get('started_utc'):
            raise ValueError('Receipt started_utc must match the active claimed start')
        when = parsed(receipt.get('utc'))
        if not when or not parsed(active['started_utc']) <= when <= now + timedelta(seconds=5):
            raise ValueError('Completion timestamp must follow cycle start and not be in the future')
        if receipt.get('paper_only') is not True or not isinstance(receipt.get('paper_tickets'), list):
            raise ValueError('PAPER receipt and explicit ticket list required')
        if not all(isinstance(receipt.get(k), dict) for k in ('risk', 'tape', 'reconciliation_defect')) or not isinstance(receipt.get('rails'), list):
            raise ValueError('Risk, tape, reconciliation and rails are required, even when unavailable')
        if now > parsed(active['expires_utc']) and not receipt.get('recovery_evidence'):
            raise ValueError('Interrupted cycle requires recovery_evidence before completion; never replay tickets')
        for ticket in receipt['paper_tickets']:
            if not isinstance(ticket, dict) or not ticket.get('ticket_id'):
                raise ValueError('Each ticket requires a stable ledger ticket_id')
        atomic(path, receipt)
        atomic(reports / 'private_wealth_cycle_latest.json', receipt)
        atomic(reports / 'private_wealth_gate.json', {'active': None, 'last_cycle_id': ident})
        return {'status': 'RECORDED', 'cycle_id': ident}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('status', 'tick', 'watch', 'begin', 'complete'))
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--no-notify', action='store_true')
    args = parser.parse_args()
    if args.command == 'watch':
        while True:
            try:
                tick(notify=not args.no_notify)
            except BlockingIOError:
                pass
            except Exception as exc:
                print(json.dumps({'observer_error': str(exc)}), flush=True)
                old = load(BASE / 'reports/private_wealth_watch.json')
                old['observer_error'] = str(exc)[:200]
                atomic(BASE / 'reports/private_wealth_watch.json', old)
            time.sleep(20)
    elif args.command == 'status':
        print(json.dumps(build(), indent=2))
    elif args.command == 'tick':
        print(json.dumps(tick(notify=not args.no_notify), indent=2))
    elif args.command == 'begin':
        print(json.dumps(begin(), indent=2))
    else:
        if not args.receipt:
            parser.error('--receipt is required')
        print(json.dumps(complete(load(args.receipt)), indent=2))


if __name__ == '__main__':
    main()
