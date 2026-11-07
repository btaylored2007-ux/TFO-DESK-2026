"""Periodic evidence indexing and honest, read-only autonomy telemetry. PAPER only.

This observer never places orders, sends AI requests, restarts services, changes
risk limits or promotes hypotheses. Existing deterministic owners retain those roles.
"""
import argparse
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
MAX_JSON_BYTES = 4 * 1024 * 1024
LABELS = {"claude": "Claude", "chatgpt": "ChatGPT", "deepseek_api": "DeepSeek", "grok": "Grok"}
ALIASES = {"chatgpt_work": "chatgpt", "chatgpt_web": "chatgpt", "claude_direct": "claude", "seat": "grok", "deepseek": "deepseek_api"}


def _clock(now=None):
    value = dt.datetime.now(dt.timezone.utc) if now is None else now
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError("invalid clock")
        value = dt.datetime.fromtimestamp(value, dt.timezone.utc)
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise ValueError("clock must be timezone aware")
    return value.astimezone(dt.timezone.utc)


def _stamp(value):
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(dt.timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _age(value, now):
    stamp = _stamp(value)
    return max(0.0, (now-stamp).total_seconds()) if stamp and stamp <= now+dt.timedelta(seconds=30) else None


def _read(path):
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            raise ValueError("report exceeds bounded read size")
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError("expected JSON object")
        return data, None
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError, TypeError):
        return None, "unreadable"


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _providers(root, now):
    from veteran_desk.thf_http import jsonl_tail
    observed = {}
    for row in jsonl_tail(root / "logs/seat_brain.jsonl", 2000):
        if not isinstance(row, dict) or row.get("ok") is not True:
            continue
        age = _age(row.get("utc"), now)
        raw_name = row.get("ai")
        if not isinstance(raw_name, str):
            continue
        name = ALIASES.get(raw_name, raw_name)
        if age is None or age > 86400 or name not in LABELS:
            continue
        info = observed.setdefault(name, {"label": LABELS[name], "observed_successes": 0, "last_success_utc": row.get("utc")})
        info["observed_successes"] += 1
        if str(row.get("utc")) > str(info["last_success_utc"]):
            info["last_success_utc"] = row["utc"]
    return observed


def build_status(root=ROOT, learning=None, recovery=None, now=None):
    """Read current facts; missing or invalid reports never become invented success."""
    root, moment = Path(root), _clock(now)
    issues = []
    if learning is None:
        learning, error = _read(root / "state/thf/autonomy/learning_status.json")
    else:
        error = None
    learning = learning or {}
    if learning and learning.get("schema_version") != SCHEMA_VERSION:
        error = "unsupported_learning_schema"
        learning = {"runtime_error": "unsupported_learning_schema"}
    totals = learning.get("totals") if isinstance(learning.get("totals"), dict) else {}
    completed, wins, losses = (_count(totals.get(k)) for k in ("completed_trades", "wins", "losses"))
    if completed is not None and wins is not None and losses is not None and wins+losses > completed:
        completed = wins = losses = None
        issues.append("Invalid learning counts")
    learning_age = _age(learning.get("generated_utc"), moment)
    if error or learning_age is None or learning_age > 180:
        issues.append("Learning report needs a current check")
        learning_text = "Learning is waiting for a current evidence check."
    elif None in (completed, wins, losses):
        issues.append("Learning counts are not verified")
        learning_text = "Learning counts are waiting for verification."
    elif learning.get("status") != "ready":
        issues.append("Some trade evidence needs verification")
        learning_text = "%s finished practice trades indexed; some records need cost or source checks." % (completed if completed is not None else "Available")
    else:
        learning_text = "%s finished practice trades: %s winners and %s losses, with recorded costs." % (completed, wins, losses)
    if learning.get("runtime_error"):
        issues.append("Evidence index could not refresh")
        learning_text = "Learning paused for review; the last evidence is retained."

    retries, retry_error = _read(root / "state/thf/chat_retry.json")
    if retries is None:
        waiting = parked = None
        issues.append("Request queue status unavailable")
    else:
        valid = [row for row in retries.values() if isinstance(row, dict)]
        waiting = parked = unknown = 0
        terminal = {"done", "completed", "complete", "cancelled", "failed", "review_saved", "saved"}
        active = {"pending", "inflight", "brain_pending", "queued", "retry", "emitted",
                  "waiting_provider", "result_ready", "running", "waiting"}
        for row in valid:
            state = str(row.get("status") or "").lower()
            maintenance_state = str(row.get("maintenance_state") or "").lower()
            if state in terminal or (row.get("maintenance_generation") and maintenance_state in terminal):
                continue
            if row.get("maintenance_generation") and maintenance_state not in {"running", "ready", "waiting_capacity"}:
                parked += 1
                continue
            if state in {"context_blocked", "parked", "blocked", "paused"}:
                parked += 1
            elif state in active or (not state and any(key in row for key in ("running", "due", "tries", "ordinary_message"))):
                waiting += 1
            else:
                unknown += 1
        unknown += len(retries)-len(valid)
        parked += unknown
        if parked:
            issues.append("Requests retained on hold or for context review")
    providers = _providers(root, moment)
    if len(providers) < 2:
        issues.append("Fewer than two AI providers have recent verified replies")
    team_text = "%d AI providers have recent verified replies; " % len(providers)
    team_text += ("%d requests waiting, %d held for context review." % (waiting, parked)
                  if waiting is not None else "request queue status needs checking.")

    if recovery is None:
        try:
            from veteran_desk import healing_guard
            recovery = healing_guard.status(state_path=root / "state/thf/recovery_guard.json")
        except Exception:
            recovery = {"status": "blocked", "reason": "Recovery status unavailable"}
    pending_units = recovery.get("pending_units")
    circuits = recovery.get("open_circuits")
    open_count = len(circuits) if isinstance(circuits, (list, dict)) else None
    if recovery.get("status") not in ("healthy", "attention"):
        issues.append("Recovery is blocked pending review")
        recovery_text = "Automatic repairs are paused because recovery evidence needs review."
    elif open_count or recovery.get("global_circuit_open"):
        issues.append("Recovery circuit is open")
        recovery_text = "Automatic repairs are cooling down after failed attempts; requests remain recorded."
    elif pending_units:
        issues.append("Recovery is awaiting verification")
        recovery_text = "A repair was requested; recovery is being verified."
    elif recovery.get("status") == "attention":
        issues.append("Recovery evidence needs checking")
        recovery_text = "Recovery needs a current check; repair requests stay recorded."
    elif recovery.get("missing"):
        recovery_text = "Bounded recovery is armed; no guarded repair has been needed yet."
    else:
        recovery_text = "Repairs have retry limits and must prove the service recovered."

    heartbeat, hb_error = _read(root / "state/vd_engine/hb/engine.json")
    heartbeat = heartbeat or {}
    heartbeat_age = _age(heartbeat.get("utc"), moment)
    primary = (root / "SGP_PRIMARY").is_file()
    killed = any((root / name).exists() for name in ("ENGINE_KILL", "state/thf/ENGINE_KILL", "state/vd_engine/ENGINE_KILL"))
    armed = (root / "AUTONOMY_ARMED").is_file() and not (root / "AUTONOMY_HALT").exists()
    if killed:
        issues.append("Owner engine stop is active")
        trading_text = "Practice trading is paused by its existing stop switch."
    elif not primary:
        issues.append("This host is standby")
        trading_text = "Standby: the primary host owns practice trading."
    elif hb_error or heartbeat_age is None or heartbeat_age > 300 or heartbeat.get("fails", 0):
        issues.append("Engine heartbeat needs checking")
        trading_text = "The practice engine needs a fresh successful heartbeat."
    elif heartbeat.get("phase"):
        trading_text = "Practice engine is updating its market view; existing risk rules stay in force."
    else:
        trading_text = "Practice trading runs automatically inside the existing risk limits."
    severe = recovery.get("status") not in ("healthy", "attention") or learning.get("runtime_error") or (primary and not killed and (hb_error or heartbeat.get("fails", 0)))
    desks = learning.get("desks")
    known_books = len(desks) if isinstance(desks, dict) else None
    return {
        "schema_version": SCHEMA_VERSION, "updated_utc": moment.isoformat(), "paper_only": True,
        "status": "red" if severe else "amber" if issues else "green",
        "summary": {"learning": learning_text, "teamwork": team_text, "recovery": recovery_text, "trading": trading_text},
        "metrics": {"completed_trades": completed, "winning_trades": wins, "losing_trades": losses,
                    "known_books": known_books, "provider_count": len(providers),
                    "parked_requests": parked, "open_circuits": open_count},
        "details": {"issues": issues, "learning_age_s": learning_age, "sources": learning.get("sources", []),
                    "partial_parents": totals.get("partial_parents"), "providers": providers,
                    "waiting_requests": waiting, "recovery": recovery, "engine_age_s": heartbeat_age,
                    "engine_phase": heartbeat.get("phase"), "sgp_primary": primary, "engine_stop": killed,
                    "new_strategy_probes_armed": armed, "strategy_changes": "Validated paper/shadow gates remain required."}
    }


def _atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name+".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, allow_nan=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run_once(root=ROOT, now=None, syncer=None, recovery=None):
    root, moment = Path(root), _clock(now)
    output = root / "state/thf/autonomy"
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".runtime.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy"}
        learning, _ = _read(output / "learning_status.json")
        if (root / "SGP_PRIMARY").is_file():
            try:
                if syncer is None:
                    from veteran_desk.learning_evidence import sync as syncer
                learning = syncer(root=root, output_dir=output, now=moment)
            except Exception as error:
                learning = dict(learning or {}, runtime_error=type(error).__name__)
        status = build_status(root, learning=learning, recovery=recovery, now=moment)
        _atomic(output / "status.json", status)
        return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("once",))
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    status = run_once(args.root)
    print(json.dumps({"status": status.get("status"), "updated_utc": status.get("updated_utc"), "metrics": status.get("metrics")}))
    return 1 if status.get("status") == "red" else 0


if __name__ == "__main__":
    raise SystemExit(main())
