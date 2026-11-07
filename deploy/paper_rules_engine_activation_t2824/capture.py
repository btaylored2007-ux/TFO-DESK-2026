#!/usr/bin/env python3
"""Read process and unit facts. Do not signal, spawn, or read secrets."""
import argparse
import json
import os
import socket
from pathlib import Path

from deploy.paper_rules_engine_activation_t2824.ownership import ALLOW_ENV, LIVE, OwnershipError, allow_env

WATCH = (372060, 366917, 833092, 1940860, 1941386, 337359)
NEEDLES = ("veteran_desk.engine_supervisor", "veteran_desk.vd_engine", "veteran_desk.exec_speed")


def _read(path):
    try:
        return Path(path).read_bytes()
    except OSError:
        return b""


def process_from_proc(proc_root, pid):
    base = Path(proc_root) / str(pid)
    if not base.is_dir():
        return {"pid": pid, "alive": False}
    cmd = _read(base / "cmdline").replace(b"\0", b" ").decode("utf-8", "replace").split()
    status = _read(base / "status").decode("utf-8", "replace")
    ppid = None
    for line in status.splitlines():
        if line.startswith("PPid:"):
            ppid = int(line.split()[1])
    cwd = exe = None
    try:
        cwd = os.readlink(base / "cwd")
    except OSError:
        pass
    try:
        exe = os.readlink(base / "exe")
    except OSError:
        pass
    return {
        "pid": pid,
        "alive": True,
        "ppid": ppid,
        "cmdline": cmd,
        "cgroup": _read(base / "cgroup").decode("utf-8", "replace").strip(),
        "cwd": cwd,
        "exe": exe,
        "environ_allowlisted": allow_env(_read(base / "environ")),
    }


def discover(proc_root):
    found = []
    root = Path(proc_root)
    if not root.is_dir():
        return found
    for child in root.iterdir():
        if not child.name.isdigit():
            continue
        text = _read(child / "cmdline").replace(b"\0", b" ").decode("utf-8", "replace")
        if any(needle in text for needle in NEEDLES):
            found.append(int(child.name))
    for pid in WATCH:
        if pid not in found:
            found.append(pid)
    return found


def capture_tree(proc_root, service, files, tick):
    procs = [process_from_proc(proc_root, pid) for pid in discover(proc_root)]
    for proc in procs:
        env = proc.get("environ_allowlisted") or {}
        proc["environ_allowlisted"] = {key: env[key] for key in ALLOW_ENV if key in env}
    return {"processes": procs, "service": service, "files": files, "tick": tick}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--proc", default="/proc")
    parser.add_argument("--service-json")
    parser.add_argument("--files-json")
    parser.add_argument("--tick-json")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if args.proc == "/proc" and socket.gethostname() != "tfo-sgp1":
        raise OwnershipError("REFUSED_HOST")
    service = json.loads(Path(args.service_json).read_text()) if args.service_json else {}
    files = json.loads(Path(args.files_json).read_text()) if args.files_json else {}
    tick = json.loads(Path(args.tick_json).read_text()) if args.tick_json else {}
    body = capture_tree(args.proc, service, files, tick)
    out = Path(args.out)
    if out.name.startswith(".env") or "ledger" in out.parts:
        raise OwnershipError("REFUSED_CAPTURE_PATH")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(body, indent=2) + "\n")
    print(json.dumps({"wrote": str(out), "processes": len(body["processes"])}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OwnershipError as exc:
        raise SystemExit(exc.code)
