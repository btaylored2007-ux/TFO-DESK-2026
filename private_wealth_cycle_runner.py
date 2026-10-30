#!/usr/bin/env python3
"""Custodian 2026-09-25: claim -> evidence receipt -> complete, once per due cadence.

Run at the end of each hourly desk_tick (after books, risk and native exits are fresh).
PAPER ONLY. Invokes no financial writer and places no tickets. Never recovers or
replays an interrupted claim: RECOVERY_REQUIRED is left for the custodian, and the
board keeps showing CYCLE_INTERRUPTED until it is reconciled.
"""
import json, sys
import private_wealth_monitor as monitor
import private_wealth_cycle_receipt as receipt


def main():
    claim = monitor.begin(force='--force' in sys.argv[1:])
    if not claim.get('run'):
        print(json.dumps({'cycle_runner': 'SKIP', 'reason': claim.get('reason'), 'active': claim.get('active')}))
        return 0
    maker_note = 'OK'
    try:  # read-only evidence (settled daily bars + tape); never runs an exit route
        import private_wealth_exit_refresh as xr
        xr.refresh_maker_evidence(monitor.BASE / 'reports')
    except Exception as e:  # coverage then honestly stays INCOMPLETE for the probe
        maker_note = f'{type(e).__name__}: {e}'[:200]
    built = receipt.build(claim['cycle_id'])
    path = monitor.BASE / 'reports/private_wealth_verification' / (claim['cycle_id'] + '-receipt.json')
    monitor.atomic(path, built)
    done = monitor.complete(built)
    print(json.dumps({'cycle_runner': done.get('status'), 'cycle_id': claim['cycle_id'],
                      'exit_coverage_verified': built['local_exit_coverage_verified'],
                      'incomplete': [r['book'] for r in built['remaining_exit_routes']],
                      'maker_evidence': maker_note}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
