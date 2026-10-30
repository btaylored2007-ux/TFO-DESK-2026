#!/usr/bin/env python3
"""Read-only standing-hunt preflight. Select research; never allocate or trade.

The coverage producer owns lifecycle reconciliation. This consumer requires its
explicit evidence-backed states and a reviewed content pin for the governing
prompt, without requiring unrelated user work to be committed to Git.
"""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import json
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent
PROMPT = Path('/Users/test/prospect/docs/skills/GLOBAL_RESEARCH_PROMPT.md')
PIN = BASE / 'research_controls/global_research_prompt_pin.json'
COVERAGE = BASE / 'reports/fundamental_coverage_ledger_v2.json'
MODULES = (
    'fundamental_session_queue.py', 'fundamental_filing_diff.py',
    'fundamental_sector_adapters.py', 'fundamental_event_receipt.py',
    'fundamental_transactional_aggregate.py', 'fundamental_source_budget.py',
)
STATES = {'ready', 'in_progress', 'complete', 'blocked_data', 'retry_after', 'stale_revisit'}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_prompt(pin: dict, prompt: Path) -> str:
    if pin.get('schema') != 'tfo.discovery.prompt-pin.v1':
        raise ValueError('missing or invalid reviewed prompt pin')
    if Path(str(pin.get('path', ''))).resolve() != prompt.resolve():
        raise ValueError('prompt pin is for a different file')
    expected = pin.get('sha256')
    actual = digest(prompt)
    if not isinstance(expected, str) or len(expected) != 64 or actual != expected:
        raise ValueError('governing prompt changed since its reviewed content pin')
    if not pin.get('reviewed_utc'):
        raise ValueError('prompt pin lacks a review timestamp')
    return actual


def select_frontier(coverage: dict, now: dt.datetime) -> tuple[dict, list[dict]]:
    if coverage.get('schema') != 'tfo.discovery.coverage.v2':
        raise ValueError('invalid coverage schema')
    frontiers = coverage.get('frontiers')
    if not isinstance(frontiers, list) or not frontiers:
        raise ValueError('coverage has no reconciled frontier lifecycle records')
    eligible, excluded, seen = [], [], set()
    for row in frontiers:
        if not isinstance(row, dict):
            raise ValueError('malformed frontier record')
        identifier = row.get('frontier_id')
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError('missing or duplicated frontier identifier')
        seen.add(identifier)
        state = row.get('state')
        if state not in STATES:
            raise ValueError('unknown frontier lifecycle state: %s' % state)
        reasons = []
        if row.get('reconciled') is not True:
            reasons.append('unreconciled')
        if state not in {'ready', 'stale_revisit'}:
            reasons.append('state=%s' % state)
        if row.get('eligible') is not True:
            reasons.append('not eligible')
        if not row.get('evidence'):
            reasons.append('no reconciliation evidence')
        retry = row.get('retry_after_utc')
        if retry:
            try:
                retry_at = dt.datetime.fromisoformat(str(retry).replace('Z', '+00:00'))
                if retry_at.tzinfo is None:
                    raise ValueError('timezone required')
                if retry_at > now:
                    reasons.append('retry date has not arrived')
            except (TypeError, ValueError):
                reasons.append('invalid retry timestamp')
        completed = row.get('last_completed_utc') or row.get('last_completed_date')
        if state == 'stale_revisit':
            try:
                day = dt.date.fromisoformat(str(completed)[:10])
                if day > now.date():
                    reasons.append('completion is in the future')
            except (TypeError, ValueError):
                reasons.append('no valid prior completion date')
        if reasons:
            excluded.append({'frontier_id': identifier, 'reasons': reasons})
        else:
            eligible.append(row)
    if not eligible:
        raise ValueError('no reconciled ready frontier or eligible stale revisit')
    eligible.sort(key=lambda row: (
        0 if row.get('origin') == 'never_covered' else 1,
        -(row.get('legacy_record', {}).get('pool_size', 0)
          if isinstance(row.get('legacy_record', {}).get('pool_size', 0), int) else 0)
        if row.get('origin') == 'never_covered' else 0,
        str(row.get('last_completed_utc') or row.get('last_completed_date') or ''),
        row.get('legacy_index', 0),
        row['frontier_id'],
    ))
    declared = (coverage.get('frontier_reconciliation') or {}).get('selected_frontier_id')
    if declared is not None and declared != eligible[0]['frontier_id']:
        raise ValueError('frontier selection disagrees with reconciled routing policy')
    return eligible[0], excluded


def check(coverage_path: Path = COVERAGE, pin_path: Path = PIN,
          prompt_path: Path = PROMPT) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    pin = json.loads(pin_path.read_text())
    prompt_sha = validate_prompt(pin, prompt_path)
    coverage = json.loads(coverage_path.read_text())
    # Refuse a coverage report generated against a different legacy source.
    legacy = coverage.get('legacy_coverage') or {}
    if legacy.get('error') or not legacy.get('path') or not legacy.get('sha256'):
        raise ValueError('coverage lacks a valid legacy-source binding')
    if digest(Path(legacy['path'])) != legacy['sha256']:
        raise ValueError('coverage source changed; regenerate coverage before hunting')
    lifecycle = (coverage.get('frontier_reconciliation') or {}).get('lifecycle_receipt')
    lifecycle_path = Path(str(coverage.get('root', '/Users/test/prospect/var'))) / 'hunt_coverage/frontier_lifecycle.json'
    if lifecycle:
        if digest(Path(lifecycle['path'])) != lifecycle.get('sha256'):
            raise ValueError('frontier claims changed; regenerate coverage before hunting')
    elif lifecycle_path.exists():
        raise ValueError('new frontier claims exist; regenerate coverage before hunting')
    selected, excluded = select_frontier(coverage, now)
    for evidence in selected['evidence']:
        if not isinstance(evidence, dict) or not evidence.get('path') or not evidence.get('sha256'):
            raise ValueError('selected frontier lacks exact evidence bindings')
        if digest(Path(evidence['path'])) != evidence['sha256']:
            raise ValueError('selected frontier evidence changed; regenerate coverage before hunting')
    dependencies = {}
    for name in MODULES:
        path = BASE / name
        ast.parse(path.read_text(), filename=str(path))
        dependencies[name] = digest(path)
    for path in (Path('/Users/test/prospect/var'), BASE / 'reports'):
        if not path.is_dir() or not os.access(path, os.R_OK | os.W_OK | os.X_OK):
            raise ValueError('research/report output area is unavailable: %s' % path)
    return {
        'schema': 'tfo.discovery.hunt-preflight.v1', 'status': 'READY',
        'paper_only': True, 'read_only': True,
        'checked_utc': now.isoformat(), 'prompt_sha256': prompt_sha,
        'coverage_sha256': digest(coverage_path),
        'selected_frontier': selected, 'excluded': excluded,
        'dependency_sha256': dependencies,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--coverage', type=Path, default=COVERAGE)
    parser.add_argument('--pin', type=Path, default=PIN)
    parser.add_argument('--prompt', type=Path, default=PROMPT)
    args = parser.parse_args()
    try:
        result = check(args.coverage, args.pin, args.prompt)
    except (OSError, ValueError, SyntaxError, TypeError) as exc:
        print(json.dumps({'status': 'BLOCKED', 'paper_only': True, 'reason': str(exc)}))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
