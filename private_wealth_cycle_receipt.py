"""Build an evidence-bound cycle receipt. Never invokes a financial writer.

Claim/run the cycle separately. Default writes a draft only; --complete submits
that draft to the existing immutable completion gate. No historical replay.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import uuid
from pathlib import Path

BASE = Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_bytes())


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def epoch(value):
    try:
        return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def fresh(value, now, age=1200):
    stamp = epoch(value)
    return stamp is not None and 0 <= now - stamp <= age


def completed_ticket_ids(reports):
    ids = set()
    for path in (reports / 'private_wealth_cycles').glob('*.json'):
        for ticket in read(path).get('paper_tickets', []):
            if ticket.get('ticket_id'):
                ids.add(ticket['ticket_id'])
    return ids


def retained_native_tickets(ledger, book, known, now):
    """Recover forward receipts from retained fills even after checks overwrite."""
    found, issues = {}, []
    def visit(value):
        if isinstance(value, list):
            for item in value: visit(item)
        elif isinstance(value, dict):
            for key,item in value.items():
                if key not in ('execution_receipt','ticket'):
                    visit(item);continue
                if not isinstance(item,dict):continue
                ident=item.get('ticket_id');stamp=epoch(item.get('utc'))
                try:valid_id=str(uuid.UUID(ident))==ident
                except (ValueError,TypeError,AttributeError):valid_id=False
                if not (valid_id and item.get('paper_only') is True and item.get('verified') is True
                        and item.get('book')==book and stamp is not None and stamp<=now):
                    issues.append({'book':book,'reason':'Malformed/unverified/future native ticket','ticket_id':ident});continue
                if ident in known:continue
                if ident in found and found[ident]!=item:
                    raise ValueError('Conflicting retained native ticket: '+ident)
                found[ident]=dict(item)
    visit(ledger)
    return list(found.values()),issues


def maker_assessment(ledger_path, evidence, now):
    ok = (evidence.get('ledger_stable') is True
          and evidence.get('ledger_path') == str(ledger_path)
          and digest(ledger_path.read_bytes()) == evidence.get('ledger_sha256')
          and fresh(evidence.get('observed_utc'), now)
          and evidence.get('latest_settled') == dt.datetime.fromtimestamp(now-86400,dt.timezone.utc).strftime('%Y-%m-%d')
          and evidence.get('latest_settled') == evidence.get('last_evaluated_settled')
          and evidence.get('pending_order') is None
          and evidence.get('registered_stop_signal') is None
          and isinstance(evidence.get('held_since_settled_bars'),int)
          and evidence['held_since_settled_bars'] >= 0)
    return {'status': 'REGISTERED_WAITING_FOR_SETTLED_BAR' if ok else 'INCOMPLETE',
            'coverage_verified': ok, 'evidence': evidence}


def latest_maker_evidence(reports):
    candidates = []
    for path in (reports / 'private_wealth_verification').glob('maker-runtime*.json'):
        try:
            evidence = read(path)
            if epoch(evidence.get('observed_utc')) is not None:
                candidates.append(evidence)
        except (OSError, ValueError):
            continue
    return max(candidates, key=lambda row: epoch(row['observed_utc'])) if candidates else {}


def admission_blockers(risk, daily, capital_ok, coverage_ok, now):
    reasons=[]
    if not capital_ok: reasons.append('Current capital/registry reconciliation is not verified')
    if not coverage_ok: reasons.append('Current per-position exit coverage or native ticket evidence is incomplete')
    if risk.get('new_entries_halted') is True: reasons.append('Firm new_entries_halted=true')
    drawdown=risk.get('firm_drawdown_pct')
    if isinstance(drawdown,(int,float)) and drawdown<=-10:
        reasons.append('Firm drawdown %.2f%% meets the -10%% new-risk freeze' % drawdown)
    approved_proxy = (daily.get('daily_risk_basis_allowed') is True
                      and daily.get('daily_risk_basis') in ('FUNDER_APPROVED_FIXED_PROXY', 'DESK_MANDATE_FIXED_PROXY')
                      and epoch(daily.get('daily_risk_authorization_expires_utc')) is not None
                      and now < epoch(daily['daily_risk_authorization_expires_utc']))
    if daily.get('current_daily_risk_verified') is not True and not approved_proxy:
        reasons.append('Current-day risk unverified: status=%s, asof=%s; %s' %
                       (daily.get('status','UNKNOWN'),daily.get('asof','UNKNOWN'),daily.get('entry_integrity_warning') or daily.get('note','No verified daily evidence')))
    if daily.get('status') in ('PROXY','ESTIMATED','UNVERIFIED') and not approved_proxy:
        reasons.append('Daily opening baseline is %s; uncertainty is not waived for new entries' % daily['status'])
    if not fresh(daily.get('asof'),now): reasons.append('Daily risk observation is missing, future-dated or older than 20 minutes')
    day=daily.get('pnl_pct')
    if isinstance(day,(int,float)) and day<=-3:
        reasons.append('Reported Sydney-day return %.3f%% meets the -3%% new-deployment freeze' % day)
    return reasons


def scanner_assessment(key, raw, scan, transaction, start, now):
    rows = [x for x in scan.get('sleeves', []) if x.get('book') == key]
    stamp = epoch(scan.get('asof'))
    entry = transaction.get('entries', {}).get(key + '.json', {})
    ok = (len(rows) == 1 and stamp is not None and start <= stamp <= now
          and scan.get('execution_enabled') is True
          and transaction.get('status') == 'COMMITTED'
          and entry.get('after_hash') == digest(raw)
          and rows[0].get('action') in ('HOLD', 'HALF_BANK', 'FULL_BANK', 'STOP'))
    return {'status': 'VERIFIED' if ok else 'INCOMPLETE', 'coverage_verified': ok,
            'reason': 'Current-cycle scanner and committed ledger hash required',
            'checks': rows, 'asof': scan.get('asof')}


def scanner_tickets(scan, reports, known):
    """Read actual ledger events; unknown economics stay unknown."""
    tickets = []
    for action in scan.get('banks_stops_executed', []):
        ident = action.get('ticket_id')
        if not ident:
            raise ValueError('Scanner fill missing stable ledger ticket_id')
        if ident in known:
            continue
        book = action['book']
        ledger = read(reports / (book + '.json'))
        events = [e for e in ledger.get('capital_events', []) if e.get('ticket_id') == ident]
        if len(events) != 1:
            raise ValueError('Scanner ticket does not uniquely bind its ledger event')
        event = events[0]
        legs = list(action.get('sold', {}).values())
        def total(field):
            vals = [leg.get(field) for leg in legs]
            return sum(vals) if vals and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vals) else None
        tickets.append({'ticket_id': ident, 'utc': event['utc'], 'book': book,
                        'strategy': book, 'action': event.get('action', action.get('action', 'SCANNER_EXIT')),
                        'verified': True, 'paper_only': True, 'kind': 'EXIT',
                        'principal_usd': total('principal_usd'), 'proceeds_usd': total('proceeds_usd'),
                        'gross_pnl_usd': total('gross_pnl_usd'), 'booked_pnl_usd': total('pnl_usd'),
                        'fees_usd': total('fees_usd'), 'slippage_estimate_usd': total('slippage_estimate_usd'),
                        'cost_source': 'Registered PAPER scanner model; not measured brokerage execution',
                        'sold': action.get('sold'), 'ledger_path': str(reports / (book + '.json'))})
    return tickets


HT_MARKET_WAITS = ('not a regular-session executable print', 'US print not under 60s', 'round-trip/double guard', 'London', 'window', 'auction',
                   'delayed bar predates entry')  # 2026-09-28: no post-fill print yet (fresh fill) is a data wait, not a fault

# 2026-09-28 (Brett/HT): exit-coverage HEALTH is evaluated on V1's real books. These registered books are
# separate desks, not V1 capital: their exit checks are still run and reported (Sleeves board,
# non_v1_exit_routes) but an incomplete check there cannot fail V1 exit coverage or raise ATTENTION.
# Keep this list explicit and short; anything not listed is treated as V1 and fails closed.
NON_V1_EXIT_SCOPE = {
    'cross_asset_floor': 'Separate Kraken-desk cross-asset floor (own ledger and runner under KrakenQuantDesk); not V1 capital',
}


def exit_scope(book_key):
    return 'NON_V1_SEPARATE_DESK' if book_key in NON_V1_EXIT_SCOPE else 'V1'


def ht_rails_assessment(key, ledger, reports, now):
    """Custodian 2026-09-25. While HEAD_TRADER_ONLY is set the engine marks but never
    tickets, so native exit evidence is intentionally empty. Exit coverage then comes
    from the Head Trader equity rails runner (tfo_ticket.py --execute, launchd every
    300s). Every held leg needs a fresh rail decision; unknowns stay INCOMPLETE."""
    try:
        ev = read(reports / 'ht_equity_rails_latest.json')
    except (OSError, ValueError) as exc:
        return {'status': 'INCOMPLETE', 'coverage_verified': False, 'reason': 'HT rails evidence unavailable: ' + str(exc)}
    stamp = epoch(ev.get('utc'))
    base_ok = ev.get('execute') is True and ev.get('paper_only') is True and fresh(ev.get('utc'), now) and key in ev.get('books', [])
    rows = {r.get('sym'): r for r in ev.get('rows', []) if r.get('book') == key}
    legs = []
    for sym, pos in (ledger.get('positions') or {}).items():
        r = rows.get(sym); err = None
        if not base_ok: err = 'HT rails evidence stale, not --execute, or book not covered'
        elif not r: err = 'No HT rail decision for held leg'
        elif r.get('status') == 'HOLD': pass
        elif r.get('status') == 'BLOCKED':
            if not (r.get('quote_utc') and r.get('session')) or not any(w in (r.get('why') or '') for w in HT_MARKET_WAITS):
                err = 'Rail signal blocked for a non-market reason: ' + str(r.get('why'))
        elif r.get('status') == 'READY':
            lt = epoch(pos.get('rail_last_ticket_utc'))
            if not (lt and stamp and lt >= stamp - 120): err = 'Rail READY without an applied Head Trader ticket'
        else:
            err = 'No valid HT rail decision: ' + str(r.get('status')) + ' ' + str(r.get('why') or '')
        legs.append({'symbol': sym, 'rail_status': (r or {}).get('status'), 'rail': (r or {}).get('rail'), 'why': (r or {}).get('why'),
                     'quote_utc': (r or {}).get('quote_utc'), 'session': (r or {}).get('session'), 'verified': err is None, 'verification_error': err})
    ok = bool(legs) and all(l['verified'] for l in legs)
    return {'status': 'VERIFIED_HT_RAILS' if ok else 'INCOMPLETE', 'coverage_verified': ok, 'route': 'tfo_ticket.py --execute (HEAD_TRADER_ONLY)',
            'completed_utc': ev.get('utc'), 'position_count': len(legs), 'legs': legs,
            'scope': 'Head Trader rail decisions from live/delayed quotes; a market wait is not an executable fill.'}



def recon_fail_reason(risk, unclaimed, gathered, now):
    """Plain reason for a FAIL reconciliation, so the alert names the cause instead of repeating the PASS text."""
    parts = []
    if risk.get('capital_accounting_valid') is not True:
        parts.append('capital accounting invalid (%s)' % '; '.join(str(e) for e in (risk.get('capital_accounting_errors') or []))[:200])
    paths = list(risk.get('unclaimed_ledgers') or []) + [u.get('path', '?') if isinstance(u, dict) else str(u) for u in (unclaimed or [])]
    if risk.get('coverage_audit_clean') is not True or unclaimed:
        uniq = sorted(set(p.replace(str(BASE) + '/', '') for p in paths))
        parts.append('coverage audit found %d unclaimed ledger-shaped file(s): %s' % (len(uniq), ', '.join(uniq)[:300]))
    if not fresh(risk.get('utc'), now):
        parts.append('risk report not fresh')
    if gathered.get('errors'):
        parts.append('%d book read error(s)' % len(gathered['errors']))
    if set(risk.get('book_keys', [])) != {b['key'] for b in gathered.get('books', [])}:
        parts.append('risk report book list differs from the live registry')
    return 'Local reconciliation FAILED: ' + ('; '.join(parts) or 'unspecified')

def build(cycle_id, base=BASE, retirement=False):
    import book_coverage as coverage
    import exit_check_evidence as native
    import private_wealth_monitor as monitor
    reports = Path(base) / 'reports'
    gate = read(reports / 'private_wealth_gate.json')
    active = gate.get('active') or {}
    if active.get('cycle_id') != cycle_id:
        raise ValueError('Cycle must already be claimed by the sole operator')
    if (reports / 'private_wealth_cycles' / (cycle_id + '.json')).exists():
        raise ValueError('Completed cycle is immutable; never rebuild it')
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    if not fresh(active.get('started_utc'), now, 600):
        raise ValueError('Expired cycle requires explicit recovery, not this builder')
    start = epoch(active['started_utc'])
    gathered = coverage.gather(False)
    unclaimed, _ = coverage.coverage_audit(gathered['books'], False)
    risk = read(reports / 'firm_risk.json')
    scan = read(reports / 'tfo_bank_rail_scan_latest.json')
    transaction = read(reports / '.tfo_bank_rail_transaction.json')
    daily = read(reports / 'daily_profit_target_status.json')
    sources, audits, snapshots, ticket_issues = [], [], {}, []
    known = completed_ticket_ids(reports)
    tickets = []
    if epoch(scan.get('asof')) is not None and start <= epoch(scan['asof']) <= now:
        tickets += scanner_tickets(scan, reports, known)
    scanner_keys = {x.get('book') for x in scan.get('sleeves', [])}
    for book in gathered['books']:
        path = Path(book['path'])
        try:
            raw = path.read_bytes(); ledger = json.loads(raw)
            # 2026-09-28: judge each ledger's checks against the moment it was READ. The 60s crypto rails runner
            # can write a fresh check after this build began; build-start `now` wrongly called it future-dated.
            read_now = dt.datetime.now(dt.timezone.utc).timestamp()
        except (OSError, ValueError) as exc:
            audits.append({'book': book['key'], 'status': 'INCOMPLETE', 'coverage_verified': False, 'reason': str(exc)})
            continue
        snapshots[path] = raw
        sources.append({'path': str(path), 'sha256': digest(raw)})
        native_tickets,native_issues=retained_native_tickets(ledger,book['key'],known,read_now)
        tickets.extend(native_tickets);ticket_issues.extend(native_issues)
        if not book['positions']:
            check = {'status': 'NO_OPEN_POSITIONS', 'coverage_verified': True}
        elif book['key'] in scanner_keys:
            check = scanner_assessment(book['key'], raw, scan, transaction, start, now)
        elif book['key'] == 'maker_calibration_probe':
            try:
                check = maker_assessment(path, latest_maker_evidence(reports), read_now)
            except (OSError, ValueError) as exc:
                check = {'status': 'INCOMPLETE', 'coverage_verified': False, 'reason': str(exc)}
        else:
            try:
                check = native.assess(ledger, now=read_now, max_age=1200)
            except (ValueError, TypeError, KeyError) as exc:
                check = {'status': 'INCOMPLETE', 'coverage_verified': False, 'reason': 'Invalid native evidence: ' + str(exc)}
            import head_trader_gate
            if check.get('coverage_verified') is not True and head_trader_gate.active() and book['key'] in ('fundamental_book', 'fundamental_spec_book', 'convexity_sleeve'):
                check = dict(ht_rails_assessment(book['key'], ledger, reports, read_now), native_check=check.get('status'))
        # Closed legs are not in assess(current holdings); retain evidence for
        # their fills and refuse to invent IDs or turn missing economics to zero.
        for leg in (ledger.get('exit_checks') or {}).get('legs', {}).values():
            if leg.get('status') != 'EXIT' or not fresh(leg.get('checked_utc'), now):
                continue
            ticket = leg.get('ticket')
            if not isinstance(ticket, dict) or not ticket.get('ticket_id'):
                ticket_issues.append({'book': book['key'], 'reason': 'Native EXIT lacks a structured ledger-bound ticket'})
        audits.append(dict(check, book=book['key'], ledger_path=str(path), exit_scope=exit_scope(book['key'])))
    if retirement:
        from coinspot_retirement import verified_retirement
        proof = verified_retirement()
        if not proof:
            raise ValueError('Retirement proof is invalid')
        for ticket in proof['tickets']:
            if ticket['ticket_id'] not in known:
                tickets.append(dict(ticket, book='coinspot_floor', strategy='OWNER_DIRECTED_RETIREMENT',
                                    verified=True, booked_pnl_usd=None, gross_pnl_usd=None))
    for path, raw in snapshots.items():
        if path.read_bytes() != raw:
            raise ValueError('Concurrent writer changed ledger during receipt build: ' + str(path))
    if read(reports / 'private_wealth_gate.json').get('active') != active:
        raise ValueError('Cycle claim changed during build')
    unique = {}
    for ticket in tickets:
        ident = ticket['ticket_id']
        if ident in unique and unique[ident] != ticket:
            raise ValueError('Conflicting ticket receipts: ' + ident)
        unique[ident] = ticket
    v1_audits = [a for a in audits if a.get('exit_scope') != 'NON_V1_SEPARATE_DESK']
    v1_ticket_issues = [t for t in ticket_issues if exit_scope(t.get('book')) == 'V1']
    complete_coverage = bool(v1_audits) and all(a.get('coverage_verified') is True for a in v1_audits) and not v1_ticket_issues
    capital_ok = (risk.get('capital_accounting_valid') is True and risk.get('coverage_audit_clean') is True
                  and fresh(risk.get('utc'), now) and not unclaimed and not gathered['errors']
                  and set(risk.get('book_keys', [])) == {b['key'] for b in gathered['books']})
    incomplete = [a for a in v1_audits if a.get('coverage_verified') is not True]
    non_v1_incomplete = [dict(a, scope_note=NON_V1_EXIT_SCOPE[a['book']]) for a in audits
                         if a.get('exit_scope') == 'NON_V1_SEPARATE_DESK' and a.get('coverage_verified') is not True]
    gate_blockers=admission_blockers(risk,daily,capital_ok,complete_coverage,now)
    new_risk_reasons=gate_blockers+['Candidate admission not performed: no fresh approved entry ticket supplied to this evidence-only builder']
    stamp = monitor.stamp(monitor.utcnow())
    def board(data):
        return {'verified_utc': stamp, 'data': data, 'sources': sources}
    result = {'cycle_id': cycle_id, 'started_utc': active['started_utc'], 'utc': stamp,
              'desk_scope': 'LOCAL_ONLY', 'paper_only': True,
              'risk': dict(risk, scope='LOCAL_BOOK_REGISTRY_ONLY'), 'tape': monitor.kraken_trade_safe(), 'rails': [],
              'paper_tickets': list(unique.values()), 'reconciliation_defect': {'status': 'PASS' if capital_ok else 'FAIL',
              # 2026-10-03: the FAIL reason used to repeat the PASS text ("...checked"), which hid the cause on the dashboard.
              'reason': 'Current local accounting and active-registry coverage checked' if capital_ok else recon_fail_reason(risk, unclaimed, gathered, now),
              'unclaimed_ledgers': unclaimed},
              'local_exit_coverage_verified': complete_coverage, 'supported_execution_verified': complete_coverage,
              'new_risk_ready': False, 'new_risk_reason': '; '.join(new_risk_reasons),
              'new_risk_blockers':new_risk_reasons,'entry_risk_gate':'BLOCKED' if gate_blockers else 'PASS',
              'native_exit_checks': audits, 'remaining_exit_routes': incomplete, 'ticket_evidence_issues': ticket_issues,
              'exit_coverage_scope': 'V1 books only; separate desks reported, not scored', 'non_v1_exit_routes': non_v1_incomplete,
              'local_sleeve_rail_checks': scan.get('sleeves', []), 'boards': {
                  'Overview': board({'status': 'VERIFIED' if complete_coverage and capital_ok else 'ATTENTION', 'scope': 'LOCAL_BOOK_REGISTRY_ONLY', 'book_count': gathered['n_books'], 'position_count': gathered['n_positions'], 'firm_equity_usd': risk.get('firm_equity_usd')}),
                  'Books': board({'status': 'VERIFIED' if capital_ok else 'ATTENTION', 'books': gathered['books']}),
                  'Positions': board({'status': 'VERIFIED' if complete_coverage else 'ATTENTION', 'positions': gathered['positions']}),
                  'Sleeves': board({'status': 'VERIFIED' if complete_coverage else 'COVERAGE_UNAVAILABLE', 'checks': audits, 'remaining_exit_routes': incomplete,
                                    'exit_coverage_scope': 'V1 books only; separate desks reported, not scored', 'non_v1_exit_routes': non_v1_incomplete}),
                  'Trade': board({'status': 'ATTENTION' if v1_ticket_issues else 'VERIFIED', 'paper_tickets': list(unique.values()), 'issues': ticket_issues}),
                  'Hunt': board({'status': 'BLOCKED' if gate_blockers else 'NOT_RUN', 'reason': '; '.join(new_risk_reasons), 'risk_gate_evaluated':True,'candidates_evaluated':False}),
                  'Research': board({'status': 'VERIFIED', 'daily_evidence': daily, 'note': 'Quote clocks and explicit waiting states retained; no strategy change.'}),
                  'Mandate': board({'status': 'VERIFIED', 'paper_only': True, 'scope': 'LOCAL_ONLY', 'retired_coinspot_not_reseeded': True})},
              'log': 'Evidence-only receipt builder; no financial writer invoked and no trade replay.', 'next': '15m'}
    result['utc'] = monitor.stamp(monitor.utcnow())
    if (reports/'tfo_24h_target_latest.json').exists():
        from tfo_24h_target import update as update_target
        try:result['target_24h']=update_target(base=base)
        except (ValueError,OSError) as exc:
            result['target_24h']={'status':'UNVERIFIED','note':str(exc)}
    return result


def main():
    import private_wealth_monitor as monitor
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cycle-id', required=True)
    parser.add_argument('--include-retirement', action='store_true')
    parser.add_argument('--complete', action='store_true')
    args = parser.parse_args()
    receipt = build(args.cycle_id, retirement=args.include_retirement)
    path = BASE / 'reports/private_wealth_verification' / (args.cycle_id + '-receipt.json')
    monitor.atomic(path, receipt)
    print(json.dumps({'receipt': str(path), 'exit_coverage_verified': receipt['local_exit_coverage_verified'],
                      'incomplete': [r['book'] for r in receipt['remaining_exit_routes']]}))
    if args.complete:
        print(json.dumps(monitor.complete(receipt)))
        if (BASE/'reports/tfo_24h_target_latest.json').exists():
            from tfo_24h_target import update as update_target
            update_target()


if __name__ == '__main__':
    main()
