"""Shared bounded PAPER recovery. No executor, model text, broker or risk edits.

All controllers reserve under the same flock before their fixed callback.
Queued requests remain pending until a later fresh healthy observation.
"""
import copy
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "state/thf/recovery_guard.json"
WINDOW = 1800
UNIT_LIMIT = 3
GLOBAL_LIMIT = 8
FRESH_SEC = 120
STABLE_SEC = 60
ENGINE = "tfo-thf-engine.service"
ENGINE_OWNERS = {"engine_supervisor", "engine_deadman"}
PROTECTED = {ENGINE, "tfo-thf-tick.service", "tfo-thf-tick.timer",
 "tfo-thf-msgdesk.service", "tfo-thf-queue-answerer.service",
 "tfo-chatgpt-web.service", "tfo-chatgpt-xvfb.service", "tfo-ai-bridge.service"}
SAFE_UNITS = {
 "tfo-dash-thf.service", "tfo-dash-vd.service", "tfo-thf-guardian.service",
 "tfo-thf-feedkeeper.service", "tfo-thf-wstape.service", "tfo-thf-news.service",
 "tfo-thf-catalyst-stream.service", "tfo-kraken.service", "tfo-yahoo.service",
 "tfo-coingecko.service", "tfo-health.service", "tfo-thf-feed.service",
 "tfo-thf-borrow.service", "tfo-thf-kraken-daily.service", "tfo-thf-news-desk.service",
 "tfo-thf-event-calendar.service", "tfo-thf-edgar8k.service",
 "tfo-thf-intraday-bars.service", "tfo-thf-kraken-sweep.service",
 "tfo-thf-feed-delay.service",
}
SAFE_TIMERS = {
 "tfo-thf-feed.timer", "tfo-thf-deadman.timer", "tfo-thf-health.timer",
 "tfo-thf-overlord.timer", "tfo-thf-borrow.timer", "tfo-thf-kraken-daily.timer",
 "tfo-thf-news-desk.timer", "tfo-thf-event-calendar.timer", "tfo-thf-edgar8k.timer",
 "tfo-thf-intraday-bars.timer", "tfo-thf-kraken-sweep.timer", "tfo-thf-feed-delay.timer",
 "tfo-thf-sgp-health.timer", "tfo-thf-guardian-check.timer",
}
KNOWN = SAFE_UNITS | SAFE_TIMERS | PROTECTED
ONESHOT_UNITS = {
 "tfo-thf-feed.service", "tfo-thf-borrow.service", "tfo-thf-kraken-daily.service",
 "tfo-thf-news-desk.service", "tfo-thf-event-calendar.service", "tfo-thf-edgar8k.service",
 "tfo-thf-intraday-bars.service", "tfo-thf-kraken-sweep.service", "tfo-thf-feed-delay.service",
}
ONESHOT_RECEIPT_SEC = 300
STATES = {"active", "inactive", "failed", "activating", "deactivating", "reloading"}

def new_state():
    return {"schema": 1, "units": {}, "global_attempts": []}

def _finite(v):
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)

def _answer(action, reason, accepted=False, **extra):
    return dict(action=action, reason=reason, accepted=accepted, **extra)

def _fresh(obs, now):
    return (isinstance(obs, dict) and _finite(obs.get("observed_at"))
            and 0 <= now - obs["observed_at"] <= FRESH_SEC
            and type(obs.get("healthy")) is bool and obs.get("active_state") in STATES)

def _validate(st):
    if not isinstance(st, dict) or st.get("schema") != 1:
        raise ValueError("invalid recovery schema")
    if not isinstance(st.get("units"), dict) or not isinstance(st.get("global_attempts"), list):
        raise ValueError("invalid recovery state")
    if st.get("clock") is not None and not _finite(st["clock"]):
        raise ValueError("invalid recovery clock")
    if len(st["units"]) > 128 or len(st["global_attempts"]) > 1000:
        raise ValueError("recovery state is oversized")
    for event in st["global_attempts"]:
        if not isinstance(event, dict) or not _finite(event.get("at")) or not isinstance(event.get("unit"), str):
            raise ValueError("invalid attempt history")
    for unit, row in st["units"].items():
        if not isinstance(unit, str) or not isinstance(row, dict):
            raise ValueError("invalid component state")
        for key in ("failures", "next_attempt_at", "healthy_since", "last_observed_at", "last_attempt_at", "last_failure_at"):
            if row.get(key) is not None and not _finite(row[key]):
                raise ValueError("invalid component timestamp/count")
        failures = row.get("failures")
        if failures is not None and (type(failures) is not int or not 0 <= failures <= 10):
            raise ValueError("invalid failure counter")
        pending = row.get("pending")
        if pending is not None and (not isinstance(pending, dict) or
                not _finite(pending.get("at")) or not _finite(pending.get("deadline")) or
                not isinstance(pending.get("id"), str)):
            raise ValueError("invalid pending attempt")
    return st

def _save(path, st):
    _validate(st)
    handle, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w") as stream:
            json.dump(st, stream, allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        os.chmod(name, 0o640)
        if os.geteuid() == 0:
            owner = path.stat() if path.exists() else path.parent.stat()
            os.chown(name, owner.st_uid, owner.st_gid)
        os.replace(name, path)
        directory = os.open(str(path.parent), os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(name): os.unlink(name)

def _failure(row, now):
    row["last_failure_at"] = now
    row["failures"] = min(10, int(row.get("failures") or 0) + 1)
    row["next_attempt_at"] = now + min(1800, 60 * 2 ** (row["failures"] - 1))
    row["healthy_since"] = None
    row["phase"] = "cooldown"

def successful_oneshot(unit, obs, now, after=None):
    """Strict terminal receipt; inactive alone never means successful recovery."""
    started = obs.get("started_at"); finished = obs.get("completed_at")
    return bool(unit in ONESHOT_UNITS and obs.get("unit_type") == "oneshot"
                and obs.get("active_state") in ("inactive", "active")
                and obs.get("result") == "success" and type(obs.get("exit_code")) is int and obs["exit_code"] == 1
                and type(obs.get("exit_status")) is int and obs["exit_status"] == 0
                and obs.get("busy") is False
                and _finite(started) and _finite(finished)
                and 0 < started <= finished <= now
                and now - finished <= ONESHOT_RECEIPT_SEC
                and (after is None or finished >= after))


def _observe(st, unit, obs, now):
    row = st["units"].setdefault(unit, {})
    previous_at = row.get("last_observed_at")
    if previous_at is not None and obs["observed_at"] < previous_at:
        return row
    # Preserve a legacy failure anchor before newer observations overwrite it.
    if row.get("failures", 0) > 0 and row.get("last_failure_at") is None and _finite(previous_at):
        row["last_failure_at"] = previous_at
    row["last_observed_at"] = obs["observed_at"]
    pending = row.get("pending")
    healthy = obs["healthy"] and obs["active_state"] == "active"
    if unit in ONESHOT_UNITS:
        anchors = [row.get("last_attempt_at"), row.get("last_failure_at")]
        if pending: anchors.append(pending["at"])
        anchors = [v for v in anchors if _finite(v)]
        anchored = bool(anchors) or not (pending or row.get("failures", 0))
        terminal = obs["healthy"] and anchored and successful_oneshot(
            unit, obs, now, after=max(anchors) if anchors else None)
        if terminal:
            # A completed exit-success is the stable boundary for a short oneshot.
            # Do not demand60s of active state; retain ALL rolling attempt history.
            row.update(pending=None, failures=0, next_attempt_at=0, phase="healthy",
                       healthy_since=obs["completed_at"], verified_at=obs["observed_at"],
                       verified_receipt_at=obs["completed_at"],
                       last_reason="Successful oneshot completion verified")
            return row
        healthy = False
    if unit == ENGINE:
        completed_at = obs.get("completed_cycle_at")
        boot_at = obs.get("engine_boot_at")
        healthy = (healthy and obs.get("engine_child_alive") is True
                   and type(obs.get("heartbeat_fails")) is int and obs["heartbeat_fails"] == 0
                   and _finite(completed_at) and _finite(boot_at)
                   and boot_at <= completed_at <= now
                   and (not pending or completed_at >= pending["at"]))
    if healthy:
        if pending and obs["observed_at"] > pending["at"]:
            row["pending"] = None
            row["phase"] = "recovered"
            row["verified_at"] = obs["observed_at"]
        if row.get("healthy_since") is None:
            row["healthy_since"] = obs["observed_at"]
        if obs["observed_at"] - row["healthy_since"] >= STABLE_SEC:
            row["failures"] = 0
            row["next_attempt_at"] = 0
            row["phase"] = "healthy"
    else:
        row["healthy_since"] = None
        if pending and now >= pending["deadline"]:
            row["pending"] = None
            _failure(row, now)
            row["last_reason"] = "Recovery request did not produce verified health in time"
    return row

def _eligibility(unit, action, owner, obs):
    if action not in ("restart", "start", "spawn"):
        return "Unknown recovery action"
    if unit == ENGINE:
        if owner not in ENGINE_OWNERS:
            return "Engine recovery belongs to its leased supervisor/deadman"
        expected = "spawn" if owner == "engine_supervisor" else "restart"
        if action != expected:
            return "Engine owner/action mismatch"
        if (obs.get("lease_valid") is not True or obs.get("engine_kill") is not False
                or obs.get("engine_child_alive") is not False):
            return "Engine lease, pause or existing-child evidence prevents recovery"
    elif unit in PROTECTED:
        return "Book tick and AI workers require their dedicated idle-safe owner"
    elif unit not in SAFE_UNITS | SAFE_TIMERS:
        return "Component is outside the fixed recovery allowlist"
    elif action == "spawn" or (unit in SAFE_TIMERS and action != "start"):
        return "Action does not match the component owner"
    if obs.get("busy") is not False:
        return "Known idle state is required; active or unknown work is protected"
    if obs.get("maintenance") is not False:
        return "Maintenance state is active or unknown"
    if unit != ENGINE and obs["active_state"] not in ("inactive", "failed"):
        return "The component is active or changing state; diagnose instead of restarting"
    return None

def _transaction(unit, observation, now, state_path, operation):
    now = time.time() if now is None else now
    if not _finite(now) or unit not in KNOWN:
        return _answer("blocked", "Unknown component or invalid clock")
    if not _fresh(observation, now):
        return _answer("escalate", "Fresh explicit service and health telemetry is required")
    path = Path(state_path) if state_path is not None else STATE_PATH
    if os.environ.get("PYTEST_CURRENT_TEST") and state_path is None:
        return _answer("blocked", "Tests must provide an isolated recovery state path")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = str(path) + ".lock"
        try: descriptor = os.open(lock_path, os.O_RDONLY)
        except FileNotFoundError:
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o640)
            if os.geteuid() == 0:
                owner = path.parent.stat()
                os.fchown(descriptor, owner.st_uid, owner.st_gid)
        with os.fdopen(descriptor, "r") as lock:
            try: fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return _answer("wait", "Another recovery transaction is in progress")
            try:
                if path.exists():
                    with path.open() as source: raw = source.read(1_048_577)
                    if len(raw) > 1_048_576: raise ValueError("oversized recovery state")
                    st = _validate(json.loads(raw))
                else: st = new_state()
            except (ValueError, OSError, TypeError):
                return _answer("escalate", "Recovery state is corrupt or unreadable; no action was taken")
            # A backward clock must not erase history or release a circuit.
            if st.get("clock") is not None and now < st["clock"]:
                return _answer("escalate", "Clock moved backwards; recovery is paused")
            if any(e["at"] > now for e in st["global_attempts"]):
                return _answer("escalate", "Recovery history is ahead of the current clock")
            st["clock"] = now
            st["global_attempts"] = [e for e in st["global_attempts"] if now - e["at"] < WINDOW]
            row = _observe(st, unit, observation, now)
            return operation(path, st, row, now)
    except (OSError, ValueError, TypeError):
        return _answer("escalate", "Recovery state could not be durably reserved; no new action was taken")

def observe(unit, observation, *, owner="generic", state_path=None, now=None):
    """Persist later fresh health. Never submits a repair."""
    def operation(path, st, row, when):
        _save(path, st)
        return _answer(row.get("phase") or "observing",
                       row.get("last_reason") or "Fresh component health observed",
                       unit=unit, pending=bool(row.get("pending")))
    return _transaction(unit, observation, now, state_path, operation)

def guarded_attempt(unit, action, callback, *, owner="generic", observation,
                    state_path=None, now=None):
    """Reserve before fixed callback, then retain pending verification.

    The callback must be a deterministic pre-existing action, never model text.
    Returns accepted=True only for a submitted request, not recovered health.
    """
    def operation(path, st, row, when):
        release_state = maintenance_active()
        why = ("Source release is active or unknown" if release_state is not False
               else _eligibility(unit, action, owner, observation))
        if why:
            _save(path, st)
            return _answer("blocked", why, unit=unit)
        if observation["healthy"]:
            _save(path, st)
            return _answer("observing", "Component already reports healthy; no repair submitted", unit=unit)
        if row.get("pending"):
            _save(path, st)
            return _answer("wait", "An earlier recovery is waiting for health verification", unit=unit)
        if when < float(row.get("next_attempt_at") or 0):
            _save(path, st)
            return _answer("wait", "Recovery cooldown is active", unit=unit,
                           next_attempt_at=row["next_attempt_at"])
        recent = [e for e in st["global_attempts"] if e["unit"] == unit]
        # Keep each existing controller's tighter gap even if its local memory
        # is lost: shared reservation history remains the authority.
        owner_gap = (1800 if owner == "sgp_health" else
                     (60, 180, 600)[min(len(recent) - 1, 2)] if owner == "guardian" and recent else 0)
        if recent and when - recent[-1]["at"] < owner_gap:
            _save(path, st)
            return _answer("wait", "The controller's existing recovery gap is active",
                           unit=unit, next_attempt_at=recent[-1]["at"] + owner_gap)
        if len(recent) >= UNIT_LIMIT or len(st["global_attempts"]) >= GLOBAL_LIMIT:
            row["phase"] = "circuit_open"
            row["last_reason"] = "Recovery budget exhausted; diagnose the cause instead of looping"
            _save(path, st)
            return _answer("escalate", row["last_reason"], unit=unit,
                           next_attempt_at=min(e["at"] for e in (recent if len(recent)>=UNIT_LIMIT else st["global_attempts"])) + WINDOW)
        ident = uuid.uuid4().hex
        row["pending"] = dict(id=ident, at=when, deadline=when + (600 if unit == ENGINE else 180),
                              action=action, owner=owner, status="reserved")
        row["phase"] = "reserved"
        row["last_attempt_at"] = when
        st["global_attempts"].append(dict(at=when, unit=unit, id=ident))
        _save(path, st)  # MUST finish before the callback.
        uncertain = False
        try:
            result = callback()
            reported = result[0] if isinstance(result, tuple) else result
            accepted = reported is True
            uncertain = reported is None
        except Exception:
            accepted = False
            uncertain = True
        row["pending"]["status"] = ("outcome_unknown" if uncertain else
                                     "submitted" if accepted else "submission_failed")
        row["phase"] = "pending_verification" if accepted or uncertain else "submission_failed"
        if not accepted and not uncertain:
            row["pending"] = None
            _failure(row, when)
        row["last_reason"] = ("Submission outcome unknown; verify before any retry" if uncertain else
                             "Repair request accepted; waiting for verified recovery" if accepted else
                             "Repair request failed; attempt counted and cooldown applied")
        try: _save(path, st)
        except (OSError, ValueError, TypeError):
            return _answer("submitted" if accepted else "escalate",
                           "Outcome not checkpointed; durable reservation remains and suppresses duplicate work",
                           accepted=accepted, unit=unit, attempt_id=ident)
        return _answer("submitted" if accepted else "wait", row["last_reason"],
                       accepted=accepted, unit=unit, attempt_id=ident)
    return _transaction(unit, observation, now, state_path, operation)

def probe_unit(unit, *, now=None, runner=None):
    """Bounded read-only probe, including immutable successful oneshot receipts."""
    when = time.time() if now is None else now
    paired_mono = time.monotonic()
    if unit not in KNOWN: return dict(observed_at=when, healthy=False, active_state="unknown")
    properties = ("ActiveState", "MainPID", "ControlPID", "Type", "Result", "ExecMainCode",
                  "ExecMainStatus", "ExecMainStartTimestampMonotonic", "ExecMainExitTimestampMonotonic")
    command = ["systemctl", "show", unit]
    for prop in properties: command += ["-p", prop]
    try:
        run = subprocess.run if runner is None else runner
        result = run(command, capture_output=True, text=True, timeout=10)
        if result.returncode != 0: raise ValueError("probe failed")
        data = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        active = data.get("ActiveState")
        if active not in STATES: raise ValueError("unknown state")
        main, control = int(data["MainPID"]), int(data["ControlPID"])
        # Convert systemd's boot-relative microseconds with one local wall/mono
        # pair, avoiding locale/timezone parsing. Explicit now remains strict.
        if now is None:
            paired_mono = time.monotonic()
            when = time.time()
        def epoch(prop):
            micros = int(data.get(prop) or 0)
            return when + micros / 1e6 - paired_mono if micros > 0 else None
        observation = dict(observed_at=when, healthy=active == "active", active_state=active,
                           busy=main != 0 or control != 0, maintenance=maintenance_active(),
                           unit_type=data.get("Type"), result=data.get("Result"),
                           exit_code=int(data.get("ExecMainCode") or 0),
                           exit_status=int(data.get("ExecMainStatus", "-1")),
                           started_at=epoch("ExecMainStartTimestampMonotonic"),
                           completed_at=epoch("ExecMainExitTimestampMonotonic"))
        if unit in ONESHOT_UNITS:
            observation["healthy"] = successful_oneshot(unit, observation, when)
        return observation
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        return dict(observed_at=when, healthy=False, active_state="unknown", busy=None, maintenance=None)


def status(state_path=None, now=None):
    """Bounded read-only snapshot for the runtime/dashboard. Never creates files."""
    when = now
    base = dict(status="healthy", pending_units=[], open_circuits=[], attempts30m=0,
                corrupt=False, missing=False)
    if when is not None and not _finite(when):
        return dict(base, status="blocked", reason="Invalid recovery clock")
    path = Path(state_path) if state_path is not None else STATE_PATH
    try:
        with path.open() as stream:
            raw = stream.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError("oversized recovery state")
        st = _validate(json.loads(raw))
        # Compare the atomic snapshot against observation time after its read.
        # A writer may legitimately advance state while this reader starts.
        if when is None:
            when = time.time()
        if not _finite(when):
            return dict(base, status="blocked", reason="Invalid recovery clock")
        if st.get("clock") is not None and (not _finite(st["clock"]) or st["clock"] > when):
            raise ValueError("invalid recovery clock")
        if any(e["at"] > when for e in st["global_attempts"]):
            raise ValueError("future recovery attempts")
    except FileNotFoundError:
        return dict(base, missing=True, reason="Awaiting the first guarded repair")
    except (OSError, ValueError, TypeError):
        return dict(base, status="blocked", corrupt=True,
                    reason="Recovery state is unknown, corrupt or unreadable; repairs are blocked")
    recent = [e for e in st["global_attempts"] if when - e["at"] < WINDOW]
    pending = sorted(unit for unit,row in st["units"].items() if row.get("pending"))
    opened = sorted(unit for unit,row in st["units"].items()
                    if (sum(e["unit"] == unit for e in recent) >= UNIT_LIMIT or
                        when < float(row.get("next_attempt_at") or 0)))
    failures = sorted(unit for unit,row in st["units"].items() if row.get("failures", 0) > 0)
    attention = bool(pending or opened or failures or len(recent) >= GLOBAL_LIMIT)
    return dict(base, status="attention" if attention else "healthy",
                pending_units=pending, open_circuits=opened, failed_units=failures, attempts30m=len(recent),
                global_circuit_open=len(recent) >= GLOBAL_LIMIT,
                reason=("A repair is waiting for verification or its retry budget"
                        if attention else "No recovery request is waiting"))

def maintenance_active(root=None):
    """Read existing release ownership; never clears or creates any lock."""
    base = ROOT if root is None else Path(root)
    pid_path = base / "tools/deploy.lock"
    try:
        if pid_path.exists():
            owner = int(pid_path.read_text()[:80].split()[0])
            if owner <= 1: return None
            try: os.kill(owner, 0); return True
            except ProcessLookupError: pass
        lease = base / "tools/.code_release.lock"
        if lease.exists():
            with lease.open("r") as handle:
                try: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError: return True
        return False
    except (OSError, ValueError, IndexError):
        return None

def main(argv=None):
    """One fixed legacy dashboard recovery route; accepts no command text."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["recover-dashboard"])
    parser.parse_args(argv)
    unit = "tfo-dash-thf.service"
    snapshot = probe_unit(unit)
    def submit():
        result = subprocess.run(["sudo", "-n", "systemctl", "--no-block", "restart", unit],
                                capture_output=True, text=True, timeout=20)
        return result.returncode == 0
    result = guarded_attempt(unit, "restart", submit, owner="shell_health", observation=snapshot)
    print(json.dumps(result))
    return 0 if result["accepted"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
