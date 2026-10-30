"""Ordered native PAPER exits under the existing hourly-desk coordination lock.

Requires a claimed cycle. Never invokes entry commands, restarts services, clears
locks, or retries an interrupted run. Native writers retain their ledger locks.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

BASE = Path(__file__).resolve().parent
KRAKEN = BASE.parent / 'KrakenQuantDesk'
PYTHON = '/Library/Frameworks/Python.framework/Versions/3.14/bin/python3'
ROUTES = (
    ('kraken_floor', KRAKEN/'kraken_floor.py', ['--exits-only'], KRAKEN/'kraken_bot/kraken_floor.json'),
    ('replica_floor', BASE/'replica_floor.py', ['--exits-only'], BASE/'replica_floor.json'),
    ('cross_asset_floor', KRAKEN/'cross_asset_floor.py', ['--exits-only'], KRAKEN/'reports/cross_asset_floor.json'),
    ('fundamental_book', BASE/'fundamental_book.py', [], BASE/'reports/fundamental_book.json'),
    ('fundamental_spec_book', BASE/'fundamental_spec_book.py', ['--mark'], BASE/'reports/fundamental_spec_book.json'),
    ('convexity_sleeve', BASE/'convexity_sleeve.py', ['--tick'], BASE/'reports/convexity_sleeve.json'),
)


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def atomic(path, value):
    raw = (json.dumps(value, indent=2, allow_nan=False)+'\n').encode()
    temp = path.with_name(path.name+'.tmp')
    with temp.open('wb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())
    os.replace(temp, path)


def command(argv, cwd, log, timeout):
    """Kill the entire launched process group on timeout, including quote workers."""
    with log.open('wb') as output:
        child = subprocess.Popen(argv, cwd=cwd, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            rc = child.wait(timeout=timeout)
            return {'returncode': rc, 'timed_out': False, 'pid': child.pid}
        except subprocess.TimeoutExpired:
            try: os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
            # A grandchild can outlive a terminating parent; clear the group.
            try: os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            return {'returncode': child.returncode, 'timed_out': True, 'pid': child.pid}


def refresh_maker_evidence(reports):
    """Read only: exact registered daily-bar decision and stable source ledger."""
    import maker_calibration_probe as maker
    ledger=Path(reports)/'maker_calibration.json'
    raw=ledger.read_bytes();state=json.loads(raw)
    bars=maker.fetch_settled_daily()
    position=state.get('position')
    since=[b for b in bars if position and b['date']>=position['entry_date']]
    stop=maker.exit_wanted(since,position['entry_px'],position['direction']) if position else None
    tape=None
    with sqlite3.connect('file:'+maker.TAPE_DB+'?mode=ro',uri=True,timeout=5) as conn:
        tape=conn.execute('SELECT MAX(utc_ts) FROM trades WHERE pair=?',(maker.TAPE_PAIR,)).fetchone()[0]
    report={'observed_utc':utc(),'paper_only':True,'read_only':True,'ledger_path':str(ledger),
            'ledger_sha256':sha(raw),'ledger_stable':ledger.read_bytes()==raw,
            'last_tick_utc':state.get('last_tick_utc'),'latest_settled':bars[-1]['date'],
            'last_evaluated_settled':state.get('last_settled_date'),'pending_order':state.get('pending_order'),
            'held_since_settled_bars':len(since),'registered_stop_signal':stop,
            'tape_latest':tape,'bars':bars[-10:]}
    atomic(Path(reports)/'private_wealth_verification/maker-runtime-latest.json',report)
    return report


def run(cycle_id, base=BASE, archive_root=None, routes=ROUTES):
    reports = Path(base)/'reports'
    gate = json.loads((reports/'private_wealth_gate.json').read_bytes())
    active = gate.get('active') or {}
    if active.get('cycle_id') != cycle_id:
        raise ValueError('Requires current sole-operator cycle claim')
    expiry = dt.datetime.fromisoformat(active['expires_utc'].replace('Z','+00:00')).timestamp()
    if expiry-time.time() < 30:
        raise ValueError('Insufficient cycle lease; reconcile/recover instead of replaying')
    out = reports/'private_wealth_verification'/f'{cycle_id}-native-refresh.json'
    out.parent.mkdir(exist_ok=True)
    if out.exists():
        prior = json.loads(out.read_bytes())
        if prior.get('status') == 'COMPLETE':
            return prior
        raise ValueError('Existing run needs reconciliation; never retry automatically')
    lock = reports/'.tick.lock'
    try: lock.mkdir()
    except FileExistsError:
        raise RuntimeError('Hourly desk or another exit refresh holds .tick.lock; no action') from None
    report = {'cycle_id':cycle_id,'desk_scope':'LOCAL_ONLY','paper_only':True,'started_utc':utc(),
              'status':'RUNNING','routes':[],'new_entry_commands':False}
    try:
        if json.loads((reports/'private_wealth_gate.json').read_bytes()).get('active') != active:
            raise ValueError('Cycle claim changed before launch')
        folder = Path(archive_root or Path.home()/'Documents/TFOOperationalArchives/exit-refresh')/cycle_id
        folder.mkdir(parents=True, exist_ok=False)
        report['archive_dir']=str(folder)
        atomic(out,report)
        for name,script,args,ledger in routes:
            remaining = expiry-time.time()-20
            if remaining < 10:
                report['status']='INCOMPLETE_LEASE_LIMIT';break
            row={'book':name,'started_utc':utc(),'command':[PYTHON,str(script),*args],'ledger':str(ledger)}
            try:
                raw=ledger.read_bytes(); value=json.loads(raw)
                if not isinstance(value,dict): raise ValueError('Invalid existing ledger; reseeding prohibited')
                backup=folder/(name+'-before.json');backup.write_bytes(raw)
                row.update(before_sha256=sha(raw),backup=str(backup),backup_note='Observed pre-invocation snapshot; native ledger lock governs exact writer state')
                log=folder/(name+'.log');row['log']=str(log)
                row['launch_attempted']=True
                row.update(command(row['command'],script.parent,log,min(150,remaining)))
                row['after_sha256']=sha(ledger.read_bytes())
                row['status']='RECOVERY_REQUIRED' if row['timed_out'] else 'OK' if row['returncode']==0 else 'FAILED'
            except Exception as exc:
                row.update(status='RECOVERY_REQUIRED' if row.get('launch_attempted') else 'FAILED',error=type(exc).__name__+': '+str(exc))
            row['completed_utc']=utc();report['routes'].append(row);atomic(out,report)
            if row['status']=='RECOVERY_REQUIRED':
                report['status']='RECOVERY_REQUIRED';break
        if report['status']=='RUNNING':
            report['status']='COMPLETE' if len(report['routes'])==len(routes) and all(r['status']=='OK' for r in report['routes']) else 'FAILED'
        if report['status']=='COMPLETE':
            try: report['maker_evidence']=refresh_maker_evidence(reports)
            except Exception as exc:
                report['maker_evidence_error']=type(exc).__name__+': '+str(exc)
                report['status']='INCOMPLETE_MAKER_EVIDENCE'
        report['completed_utc']=utc();atomic(out,report)
        return report
    finally:
        lock.rmdir()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cycle-id',required=True)
    args=parser.parse_args()
    report=run(args.cycle_id)
    print(json.dumps(report,indent=2))
    return 0 if report['status']=='COMPLETE' else 1


if __name__=='__main__':
    sys.exit(main())
