"""Warehouse funding authority only; never opens trades or changes ledgers.

2026-09-25 (funder ruling 18:26 AEST via Head Trader): the desk runs under the
Head Trader autonomous mandate. A time-limited funder cash override is used only
while it is inside its window (an explicit FUNDER OVERRIDE); otherwise the
standing desk mandate applies. Every warehouse check (paper-only, cash-only,
legacy-freeze scope, newer/other freezes, separate risk freezes) still applies,
and entry checks are never waived.
"""
import datetime as dt
import json
import math
from pathlib import Path

REPORTS = Path(__file__).resolve().parent / 'reports'


def assess(authorization, ledger, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    out = {'status': 'BLOCKED', 'funding_authorized': False,
           'authorized_cash_usd': 0.0, 'entry_checks_waived': False,
           'expires_utc': authorization.get('expires_utc'),
           'reason': 'No valid current funder cash authorization'}
    mandate = authorization.get('kind') == 'desk_mandate'
    if mandate:
        out['reason'] = 'Desk mandate record is invalid'
    try:
        start = dt.datetime.fromisoformat(authorization['effective_utc'].replace('Z', '+00:00'))
        if mandate:
            if authorization.get('expires_utc') is not None or now < start:
                return out
        else:
            end = dt.datetime.fromisoformat(authorization['expires_utc'].replace('Z', '+00:00'))
            if not start <= now < end:
                out['reason'] = 'Funder cash authorization is outside its fixed window'
                return out
        if (authorization.get('paper_only') is not True
                or authorization.get('source_book') != 'gap_close_levered_book'
                or authorization.get('waives_entry_checks') is not False
                or authorization.get('waives_new_freezes') is not False):
            return out
        if ledger.get('holdings') or ledger.get('positions'):
            out['reason'] = 'Warehouse is not cash-only; reconcile funding scope'
            return out
        if ledger.get('frozen') and ledger.get('frozen_reason') != authorization.get('overridden_legacy_reason'):
            out['reason'] = 'A different freeze is not covered by this override'
            return out
        if ledger.get('frozen_at_utc') and dt.datetime.fromisoformat(ledger['frozen_at_utc'].replace('Z', '+00:00')) > start:
            out['reason'] = 'A newer freeze is not covered by this override'
            return out
        if (ledger.get('book_risk') or {}).get('frozen') or (ledger.get('sleeve_risk') or {}).get('new_entries_frozen'):
            out['reason'] = 'Separate risk freeze remains in force'
            return out
        cash, cap = ledger['cash'], authorization['maximum_source_cash_usd']
        if mandate and cap is None:
            cap = cash
        if any(isinstance(v, bool) or not isinstance(v, (int, float))
               or not math.isfinite(v) or v < 0 for v in (cash, cap)):
            return out
        if mandate:
            out.update(status='DESK_AUTHORISED', funding_authorized=True,
                       authorized_cash_usd=round(min(cash, cap), 2),
                       legacy_freeze_retained=bool(ledger.get('frozen')),
                       authorization_id=authorization.get('authorization_id'),
                       authority_kind='desk_mandate',
                       authority_label=authorization.get('authority') or 'Head Trader autonomous mandate (desk-authorised)',
                       reason='Desk-authorised under the Head Trader autonomous mandate (PAPER); legacy warehouse freeze is not a funding gate; all destination, accounting and entry checks remain required')
            return out
        out.update(status='AUTHORIZED_FOR_FUNDING', funding_authorized=True,
                   authority_kind='funder_override', authority_label='Funder override (time-limited)',
                   authorized_cash_usd=round(min(cash, cap), 2),
                   legacy_freeze_retained=bool(ledger.get('frozen')),
                   authorization_id=authorization.get('authorization_id'),
                   reason='Legacy warehouse freeze overridden for PAPER funding only; all destination, accounting and entry checks remain required')
    except (KeyError, ValueError, TypeError, OverflowError):
        pass
    return out


def current(reports=REPORTS, now=None):
    try:
        reports = Path(reports)
        ledger = json.loads((reports/'gap_close_levered_book.json').read_text())
        override = None
        try:
            override = assess(json.loads((reports/'funder_cash_authorization_20260924.json').read_text()), ledger, now)
        except (OSError, ValueError):
            pass
        if override and override['funding_authorized']:
            return override
        result = assess(json.loads((reports/'desk_funding_mandate_20260925.json').read_text()), ledger, now)
        if override:
            result['funder_override_status'] = override.get('reason')
            result['funder_override_expires_utc'] = override.get('expires_utc')
        return result
    except (OSError, ValueError):
        return {'status': 'BLOCKED', 'funding_authorized': False,
                'authorized_cash_usd': 0.0, 'entry_checks_waived': False,
                'reason': 'Funding authorization or warehouse ledger unavailable'}


def require_funding(amount, reports=REPORTS, now=None):
    """Call under the execution writer lock immediately before a paired transfer."""
    result = current(reports, now)
    if (isinstance(amount, bool) or not isinstance(amount, (int, float))
            or not math.isfinite(amount) or amount <= 0
            or not result['funding_authorized'] or amount > result['authorized_cash_usd']):
        raise ValueError('Requested funding is not authorized: ' + result['reason'])
    return result
