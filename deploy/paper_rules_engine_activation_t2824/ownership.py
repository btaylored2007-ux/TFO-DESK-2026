"""Read-only ownership verdict for the paper-policy engine.

The official unit's MainPID is the supervisor, not the trading daemon.
A matching PID is not proof that book.py or gate_v2.py was imported.
This module never signals, spawns, or stops a process.
"""
import json
from pathlib import Path

LIVE = "/opt/tfo/live/thf"
UNIT = "tfo-thf-engine.service"
HANDOVER = "state/thf/build_handover/paper_rules_engine_activation_t2824_20261107T201225"
TAKEOVER_NAME = "TAKEOVER"
BOOK = "a739477001c33b23ad87ebdf8ca5fc40c76b441eb13100daf1bc67136eb12c7b"
GATE = "1d1e8c82685e2df775d4543456f19104947fe6b24f567929707848c906f7f2a9"
FIRM_RISK_PID = 337359
QUOTE = "Finish it up"
QUOTE_ID = "t2824u"
QUOTE_WITA = "2026-11-07 20:08 WITA"
FORBIDDEN = [
    "kill",
    "systemctl restart tfo-thf-engine.service",
    "systemctl stop tfo-thf-engine.service",
    "systemctl restart tfo-thf-tick.service",
    "systemctl stop tfo-thf-tick.timer",
    "second vd_engine spawn",
    "release.py rerun",
]
CAPTURE_GAP = [
    "/opt/tfo/live/thf/veteran_desk/engine_supervisor.py",
    "/opt/tfo/dash/base/veteran_desk/engine_supervisor.py",
    "systemctl cat tfo-thf-engine.service",
    "proc cmdline, ppid, cgroup, cwd, exe, start_epoch for each engine_supervisor, vd_engine daemon, and exec_speed risk-serve",
    "explicit alive true or false for pids 372060, 366917, 833092, 1940860, 1941386, 337359",
    "sha256 and mtime_epoch of live veteran_desk/book.py and veteran_desk/gate_v2.py",
    "allowlisted environ only: PYTHONPATH, PYTHONDONTWRITEBYTECODE, PYTHONUNBUFFERED, THF_DESK, TFO_HOST, PWD",
    "tfo-thf-tick.timer ActiveState and tfo-thf-tick.service ActiveState",
]
ALLOW_ENV = ("PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "THF_DESK", "TFO_HOST", "PWD")


class OwnershipError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def role(proc):
    cmd = proc.get("cmdline") or []
    text = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
    if "veteran_desk.exec_speed" in text and "risk-serve" in text:
        return "firm_risk"
    if "veteran_desk.engine_supervisor" in text:
        return "supervisor"
    if "veteran_desk.vd_engine" in text and "daemon" in text.split():
        return "vd_engine"
    return "other"


def _alive(proc):
    return proc.get("alive") is not False


def official_supervisor(proc, service):
    """MainPID plus the unit cgroup. The number alone is not the service."""
    if role(proc) != "supervisor" or not _alive(proc):
        return False
    cgroup = str(proc.get("cgroup") or "")
    unit_cg = str((service or {}).get("ControlGroup") or "")
    if UNIT not in cgroup or not unit_cg or unit_cg not in cgroup:
        return False
    return proc.get("pid") == (service or {}).get("MainPID")


def allow_env(raw):
    """Keep the path keys. Drop every other variable, including secrets."""
    if isinstance(raw, dict):
        items = list(raw.items())
    else:
        items = []
        for part in bytes(raw).split(b"\0"):
            if b"=" not in part:
                continue
            key, value = part.split(b"=", 1)
            items.append((key.decode("utf-8", "replace"), value.decode("utf-8", "replace")))
    kept = {}
    for key, value in items:
        if key in ALLOW_ENV and "KEY" not in key and "TOKEN" not in key and "SECRET" not in key:
            kept[key] = value
    return kept


def attest_modules(proc, files, service):
    """Strongest claim is path_attested. That is still not an in-process hash."""
    book = (files or {}).get("veteran_desk/book.py") or {}
    gate = (files or {}).get("veteran_desk/gate_v2.py") or {}
    base = {"loaded_modules_proven": False, "official_cgroup_load": False, "pid_is_not_proof": True}
    if book.get("sha256") != BOOK or gate.get("sha256") != GATE:
        return dict(base, evidence_level="hash_mismatch")
    start = proc.get("start_epoch")
    if not isinstance(start, (int, float)) or isinstance(start, bool):
        return dict(base, evidence_level="unproven")
    if start <= float(book.get("mtime_epoch") or 0) or start <= float(gate.get("mtime_epoch") or 0):
        return dict(base, evidence_level="started_before_file")
    level = "start_after_mtime"
    pythonpath = str((proc.get("environ_allowlisted") or {}).get("PYTHONPATH", ""))
    cmd_ok = role(proc) == "vd_engine"
    if proc.get("cwd") == LIVE and pythonpath in ("", LIVE) and cmd_ok:
        level = "path_attested"
    cgroup = str(proc.get("cgroup") or "")
    unit_cg = str((service or {}).get("ControlGroup") or "")
    in_unit = bool(unit_cg) and unit_cg in cgroup and UNIT in cgroup
    return dict(base, evidence_level=level, daemon_in_official_cgroup=in_unit)


def _source_present(files):
    row = (files or {}).get("veteran_desk/engine_supervisor.py") or {}
    return bool(row.get("sha256")) and row.get("present") is True


def classify(capture):
    service = capture.get("service") or {}
    files = capture.get("files") or {}
    procs = [p for p in capture.get("processes") or [] if isinstance(p, dict)]
    alive = [p for p in procs if _alive(p)]
    supervisors = [p for p in alive if role(p) == "supervisor"]
    daemons = [p for p in alive if role(p) == "vd_engine"]
    firms = [p for p in alive if role(p) == "firm_risk"]
    official = [p for p in supervisors if official_supervisor(p, service)]
    leftover = [p for p in supervisors if p not in official]
    official_ids = {p.get("pid") for p in official}
    leftover_ids = {p.get("pid") for p in leftover}
    daemon_evidence = [dict(pid=p.get("pid"), ppid=p.get("ppid"), **attest_modules(p, files, service)) for p in daemons]
    gap = []
    if not _source_present(files):
        gap.extend(CAPTURE_GAP[:2])
    if not service.get("ControlGroup") or service.get("MainPID") is None or not service.get("KillMode"):
        gap.append(CAPTURE_GAP[2])
    watched = {372060, 366917, 833092, 1940860, 1941386, 337359}
    seen = {p.get("pid") for p in procs if p.get("alive") in (True, False)}
    if not watched <= seen:
        gap.append(CAPTURE_GAP[4])
    tick = capture.get("tick") or {}
    if tick.get("timer_active") is not True:
        gap.append(CAPTURE_GAP[7])
    duplicate = len(daemons) > 1
    missing = len(daemons) == 0
    split = bool(leftover) or any(p.get("ppid") in leftover_ids for p in daemons) or (official and not any(p.get("ppid") in official_ids for p in daemons))
    if duplicate:
        verdict = "DUPLICATE_DAEMON"
    elif missing:
        verdict = "MISSING_DAEMON"
    elif split or len(official) != 1 or len(supervisors) != 1:
        verdict = "SPLIT_OWNERSHIP"
    else:
        verdict = "SINGLE_OWNER"
    firm_ok = [p.get("pid") for p in firms] == [FIRM_RISK_PID] and all(p.get("ppid") not in official_ids | leftover_ids for p in firms)
    path_ok = bool(daemon_evidence) and all(row["evidence_level"] == "path_attested" for row in daemon_evidence)
    status = "blocked"
    if verdict == "SINGLE_OWNER" and path_ok and firm_ok and tick.get("timer_active") is True and not gap:
        status = "observed_single_owner"
    return {
        "export": TAKEOVER_NAME,
        "authorization_verbatim": QUOTE,
        "authorization_id": QUOTE_ID,
        "authorization_wita": QUOTE_WITA,
        "status": status,
        "verdict": verdict,
        "retire_command": None,
        "forbidden": list(FORBIDDEN),
        "loaded_modules_proven": False,
        "official_supervisor_pids": [p.get("pid") for p in official],
        "leftover_supervisor_pids": [p.get("pid") for p in leftover],
        "vd_engine_pids": [p.get("pid") for p in daemons],
        "firm_risk_pids": [p.get("pid") for p in firms],
        "firm_risk_untouched": firm_ok,
        "daemon_evidence": daemon_evidence,
        "pid_372060_alive": next((p.get("alive") for p in procs if p.get("pid") == 372060), None),
        "capture_gap": gap,
        "stops_owner": "tfo-thf-tick.timer",
        "paper_only": True,
    }


def refuse_second_daemon(live_daemon_count):
    """A supervisor must not spawn while any vd_engine daemon is alive."""
    if isinstance(live_daemon_count, bool) or not isinstance(live_daemon_count, int):
        raise OwnershipError("REFUSED_DAEMON_COUNT")
    if live_daemon_count < 0:
        raise OwnershipError("REFUSED_DAEMON_COUNT")
    return live_daemon_count != 0


def takeover_path(root):
    root = Path(root)
    path = root / HANDOVER / TAKEOVER_NAME
    resolved = path.resolve()
    if resolved.name != TAKEOVER_NAME:
        raise OwnershipError("REFUSED_TAKEOVER_PATH")
    if "ledger" in resolved.parts or ".env" in resolved.name:
        raise OwnershipError("REFUSED_TAKEOVER_PATH")
    if HANDOVER not in str(resolved):
        raise OwnershipError("REFUSED_TAKEOVER_PATH")
    return path


def write_takeover(root, capture):
    body = classify(capture)
    path = takeover_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2) + "\n")
    return path, body
