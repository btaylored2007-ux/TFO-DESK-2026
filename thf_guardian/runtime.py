"""Services, timers, lockfiles, code drift. Deterministic."""
import os

from thf_guardian.config import DRIFT_FILES, LONG_UNITS, TIMERS
from thf_guardian.util import sha256_file


def diagnose_units(snap):
    """snap units/timers map unit name -> systemd active state."""
    actions = []
    rows = []
    units = snap.get("units") or {}
    for name, unit in LONG_UNITS.items():
        st = units.get(unit)
        ok = st == "active"
        rows.append(dict(id=name, unit=unit, state=st or "unknown", ok=ok, kind="service"))
        if not ok:
            actions.append(dict(kind="restart", unit=unit, how="restart"))
    timers = snap.get("timers") or {}
    for name, unit in TIMERS.items():
        st = timers.get(unit)
        ok = st == "active"
        rows.append(dict(id=name, unit=unit, state=st or "unknown", ok=ok, kind="timer"))
        if not ok:
            actions.append(dict(kind="restart", unit=unit, how="start"))
    # oneshot feed result, if the collector itself failed
    if snap.get("feed_result") in ("failed", "exit-code"):
        rows.append(dict(id="feed_oneshot", unit="tfo-thf-feed.service", state=snap["feed_result"], ok=False, kind="service"))
        actions.append(dict(kind="restart", unit="tfo-thf-feed.service", how="start"))
    return dict(rows=rows, actions=actions)


def diagnose_locks(locks, now_ts, stale_sec=720):
    """locks: [{path, kind: 'mkdir'|'flock', age_sec, held}].
    A mkdir lock older than stale_sec is cleared (the holder died; tick timeout is 600s).
    A flock lock is on the inode: report it, do not delete the file.
    """
    actions = []
    rows = []
    for lk in locks or []:
        age = lk.get("age_sec")
        kind = lk.get("kind") or "mkdir"
        held = bool(lk.get("held"))
        stale = held and age is not None and age > stale_sec
        if not stale:
            rows.append(dict(path=lk.get("path"), ok=True, kind=kind, age_sec=age, held=held))
            continue
        if kind == "mkdir":
            rows.append(dict(path=lk.get("path"), ok=False, kind=kind, age_sec=age, action="clear"))
            actions.append(dict(kind="clear_lock", path=lk.get("path"), lock_kind="mkdir", age_sec=age))
        else:
            rows.append(dict(path=lk.get("path"), ok=False, kind=kind, age_sec=age,
                             action="escalate",
                             detail="flock held past the tick timeout; the lock is on the inode so the file is not deleted"))
            actions.append(dict(kind="escalate_lock", path=lk.get("path"), lock_kind="flock", age_sec=age))
    return dict(rows=rows, actions=actions)


def diagnose_drift(root, approved, files=DRIFT_FILES):
    """approved: {relative path: sha256}. Missing baseline -> unbaselined, not a failure.
    A mismatch is an alert. The file is not reverted (droplet-native tree; code_push is gone).
    """
    if not approved:
        return dict(ok=True, state="unbaselined", drifted=[], detail="no approved_code.json yet; install writes the baseline")
    drifted = []
    for rel in files:
        path = os.path.join(root, rel)
        want = approved.get(rel)
        if want is None:
            continue
        if not os.path.isfile(path):
            drifted.append(dict(path=rel, why="missing", approved=want, live=None))
            continue
        got = sha256_file(path)
        if got != want:
            drifted.append(dict(path=rel, why="changed", approved=want, live=got))
    return dict(ok=not drifted, state="drift" if drifted else "match", drifted=drifted,
                detail="%d file(s) differ from the approved droplet baseline" % len(drifted) if drifted else "live code matches the approved baseline")
