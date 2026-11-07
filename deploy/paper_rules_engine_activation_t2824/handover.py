#!/usr/bin/env python3
"""Ownership handover from the captured supervisor. This file never signals."""
import argparse
import datetime as dt
import hashlib
import json
import socket
from pathlib import Path

from deploy.paper_rules_engine_activation_t2824.ownership import (
    BOOK, GATE, LIVE, OwnershipError, TAKEOVER_NAME, classify, takeover_path,
)

HERE = Path(__file__).resolve().parent
CAPTURED = HERE / "captured" / "engine_supervisor.py"
SUPERVISOR_SHA = "0d0b7f640a7c9644a56f0bbe8c91b640976c1db7ecf968c328112e542de96e0e"
ARCHIVE_SHA = "f056ac1c236646c0fac148c8161467471ac0450bdb37c20a5d2486af64bad33e"
CENSUS_WITA = "2026-11-07 20:24:20 WITA"
OFFICIAL = 1940860
LEFTOVER = 833092
DAEMON = 1941386
PARENT = 833089
FIRM = 337359


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _wita(text):
    if not isinstance(text, str) or not text.endswith(" WITA"):
        return None
    try:
        parsed = dt.datetime.strptime(text[:-5], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()


def _env(row):
    raw = ((row.get("env") or {}).get("allowlisted") or {})
    return {key: value for key, value in raw.items() if value is not None}


def _proc(row):
    kind = row.get("kind") or ""
    if kind == "engine_supervisor":
        cmd = ["python", "-m", "veteran_desk.engine_supervisor"]
    elif kind == "vd_engine":
        cmd = ["python", "-m", "veteran_desk.vd_engine", "daemon"]
    elif "exec_speed" in kind or kind == "exec_speed":
        cmd = ["python", "-m", "veteran_desk.exec_speed", "risk-serve"]
    else:
        cmd = str(row.get("cmd") or "").split()
    return {
        "pid": row.get("pid"),
        "ppid": row.get("ppid"),
        "alive": row.get("alive"),
        "cmdline": cmd,
        "cgroup": row.get("cgroup"),
        "cwd": row.get("cwd"),
        "exe": row.get("exe"),
        "start_wita": row.get("start_wita"),
        "start_epoch": _wita(row.get("start_wita")),
        "environ_allowlisted": _env(row),
        "parent_cmd": row.get("parent_cmd"),
    }


def from_snapshot(snapshot, supervisor_sha):
    wanted = snapshot.get("wanted_pids") or {}
    files = snapshot.get("files") or {}
    units = snapshot.get("units") or {}
    engine = ((units.get("tfo-thf-engine.service") or {}).get("show") or {}).get("props") or {}
    tick = ((units.get("tfo-thf-tick.timer") or {}).get("show") or {}).get("props") or {}
    book = files.get("book_live") or {}
    gate = files.get("gate_live") or {}
    source = files.get("engine_supervisor_live") or {}
    procs = []
    for key, row in wanted.items():
        item = _proc(row)
        item["pid"] = item["pid"] if item["pid"] is not None else int(key)
        procs.append(item)
    return {
        "processes": procs,
        "service": {
            "MainPID": int(engine["MainPID"]) if str(engine.get("MainPID") or "").isdigit() else None,
            "ControlGroup": engine.get("ControlGroup"),
            "KillMode": engine.get("KillMode"),
            "ActiveState": engine.get("ActiveState"),
        },
        "files": {
            "veteran_desk/book.py": {"sha256": book.get("sha256"), "mtime_epoch": _wita(book.get("mtime_wita")), "mtime_wita": book.get("mtime_wita")},
            "veteran_desk/gate_v2.py": {"sha256": gate.get("sha256"), "mtime_epoch": _wita(gate.get("mtime_wita")), "mtime_wita": gate.get("mtime_wita")},
            "veteran_desk/engine_supervisor.py": {"present": source.get("exists") is True, "sha256": supervisor_sha},
        },
        "tick": {"timer_active": tick.get("ActiveState") == "active", "service_active": None},
        "supervisor_sha256": supervisor_sha,
        "archive_sha256": ARCHIVE_SHA,
    }


def _lazy_imports():
    return {
        "loaded_modules_proven": False,
        "why": "A new pid, a later start time, and the official cgroup do not show which module object a process imported.",
        "supervisor": "engine_supervisor.py does not import book.py or gate_v2.py.",
        "vd_engine_daemon": "gate_v2 is imported inside history_check, which the first scoring cycle calls. book.py is imported there only when a non-FX ADV is missing. Neither import runs at process start.",
        "desk_round": "python -m veteran_desk.desk run is a new interpreter. risk_gate imports gate_v2 and book. The 20:20:59 child 1950524 had an unreadable cwd and is not that proof.",
        "tick": "Each tick is a new python -m veteran_desk.book tick. Stops stay on tfo-thf-tick.timer.",
        "path_facts_for_1941386": {
            "cwd": LIVE,
            "exe": "/usr/bin/python3.12",
            "argv0": "/opt/tfo/venv/bin/python",
            "PYTHONPATH": "unset",
            "PYTHONDONTWRITEBYTECODE": "1",
            "start_wita": "2026-11-07 20:12:52 WITA",
            "book_and_gate_mtime_wita": "2026-11-07 20:00:58 WITA",
            "book_sha256": BOOK,
            "gate_sha256": GATE,
        },
    }


def build_handover(snapshot, supervisor_sha):
    if supervisor_sha != SUPERVISOR_SHA:
        raise OwnershipError("REFUSED_SUPERVISOR_HASH")
    capture = from_snapshot(snapshot, supervisor_sha)
    body = classify(capture)
    body["fully_clean"] = False
    body["loaded_modules_proven"] = False
    body["archive_sha256"] = ARCHIVE_SHA
    body["supervisor_sha256"] = SUPERVISOR_SHA
    body["census_wita"] = CENSUS_WITA
    body["lazy_imports"] = _lazy_imports()
    shape = (
        body["verdict"] == "SPLIT_OWNERSHIP"
        and body["official_supervisor_pids"] == [OFFICIAL]
        and body["leftover_supervisor_pids"] == [LEFTOVER]
        and body["vd_engine_pids"] == [DAEMON]
        and body["firm_risk_pids"] == [FIRM]
        and body["pid_372060_alive"] is False
        and body["daemon_evidence"][0]["evidence_level"] == "path_attested"
        and body["daemon_evidence"][0]["daemon_in_official_cgroup"] is False
    )
    if not shape:
        body["handover"] = {"status": "blocked", "signal": None, "fully_clean": False}
        return body
    body["status"] = "one_term_when_recheck_passes"
    body["handover"] = {
        "status": "one_term_when_recheck_passes",
        "fully_clean": False,
        "signal": "TERM",
        "pid": LEFTOVER,
        "command": "kill -s TERM 833092",
        "does_not_signal": [DAEMON, OFFICIAL, FIRM, PARENT, 372060, 366917],
        "does_not_run": [
            "kill -s KILL",
            "systemctl restart tfo-thf-engine.service",
            "systemctl stop tfo-thf-engine.service",
            "systemctl stop tfo-thf-tick.timer",
            "python -m veteran_desk.vd_engine daemon",
            "release.py",
        ],
        "source": {
            "sha256": SUPERVISOR_SHA,
            "live_equals_dash": True,
            "stop_owned": "SIGTERM calls stop_owned, which terminate()s only self.child and waits 10s. It does not SIGKILL.",
            "probe": "ps matches every veteran_desk.vd_engine daemon. engine_child_alive is true while any such pid exists, so the official supervisor does not spawn.",
            "spawn_guard": "healing_guard refuses spawn when engine_child_alive is not false, when a desk round makes busy true, or when maintenance is unknown.",
            "unit": "KillMode=control-group and Restart=always. A unit restart kills only the official cgroup and leaves 833092 in the user session.",
        },
        "preconditions": [
            "Host is tfo-sgp1 and this command is the only signal in the step.",
            "Re-read ps. Exactly one vd_engine daemon, ppid 833092, cgroup user.slice session-56389.",
            "1940860 is MainPID, ppid 1, cgroup /system.slice/tfo-thf-engine.service, and has no vd_engine child.",
            "833092 command is python -m veteran_desk.engine_supervisor and its ppid is 833089.",
            "Read /proc/833089/cmdline. Abort if it is unreadable or contains while, until, or engine_supervisor. Abort if 833089 has a child other than 833092.",
            "ps has no veteran_desk.desk run and no desk.py run.",
            "337359 is alive in tfo-thf-firm-risk.service. Tick timer ActiveState is active. Do not stop either.",
            "SGP_PRIMARY exists, data/pit/pit.sqlite is non-empty, and no ENGINE_KILL file exists.",
            "tools/deploy.lock is absent or its pid is dead.",
            "state/thf/recovery_guard.json has no pending engine attempt.",
            "book.py is still %s and gate_v2.py is still %s." % (BOOK, GATE),
            "engine_supervisor.py is still %s on both roots." % SUPERVISOR_SHA,
        ],
        "watch": "After the signal, poll every 5s for 45s. Do not send a second signal.",
        "success": [
            "833092 is absent and 833089 has not started another engine_supervisor.",
            "Exactly one vd_engine daemon, and its ppid is the official MainPID.",
            "That daemon's cgroup is /system.slice/tfo-thf-engine.service.",
            "337359 is still alive and the tick timer is still active.",
        ],
        "if_no_daemon_appears": "Read state/vd_engine/supervisor_health.json and state/thf/recovery_guard.json. If a desk round is still present, wait for it to exit and for one more supervisor step. Do not systemctl restart and do not start python by hand.",
        "if_bash_restarts_a_supervisor": "Stop. One daemon under the new leftover is the same split. Do not signal again in this step.",
        "if_child_survives": "833092 exits even when wait() times out. An orphaned daemon is still one writer. Do not SIGKILL it.",
        "rollback": "PID 1941386 cannot be restored. The official supervisor's existing spawn is the replacement. There is no file rollback and no second manual daemon.",
        "stops": "The tick keeps running through the step. The engine gap is the scoring daemon, not the stop path.",
    }
    return body


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--supervisor", required=True)
    parser.add_argument("--out")
    parser.add_argument("--root")
    args = parser.parse_args(argv)
    digest = sha256(args.supervisor)
    body = build_handover(json.loads(Path(args.snapshot).read_text()), digest)
    text = json.dumps(body, indent=2) + "\n"
    if args.out:
        dest = Path(args.out)
        if dest.name != TAKEOVER_NAME or "ledger" in dest.parts or dest.name.startswith(".env"):
            raise OwnershipError("REFUSED_TAKEOVER_PATH")
        if args.root and (socket.gethostname() != "tfo-sgp1") and str(args.root).startswith("/opt/tfo"):
            raise OwnershipError("REFUSED_ROOT")
        if args.root:
            dest = takeover_path(args.root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        print(json.dumps({"wrote": str(dest), "status": body["status"], "fully_clean": False, "signal_sent": False}))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OwnershipError as exc:
        raise SystemExit(exc.code)
