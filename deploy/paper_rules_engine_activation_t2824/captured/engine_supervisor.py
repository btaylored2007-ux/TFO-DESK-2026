"""Static PAPER engine supervisor. Launches only the registered engine command."""
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

BASE = Path(__file__).resolve().parents[1]
UNIT = "tfo-thf-engine.service"
ENGINE = re.compile(r"(?:-m\s+veteran_desk\.vd_engine\s+daemon\b|/veteran_desk/vd_engine\.py\s+daemon\b)")
ROUND = re.compile(r"(?:-m\s+veteran_desk\.desk\s+run\b|/veteran_desk/desk\.py\s+run\b)")
CLAUDE_CLI = re.compile(r"(?:^|[ /])claude(?:\.exe)?(?:\s|$)")
BRIDGE_UNIT = "tfo-ai-bridge.service"
BRIDGE_CGROUP = "/system.slice/tfo-ai-bridge.service"

def timestamp(value):
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None: return None
        return parsed.timestamp()
    except (ValueError, TypeError, OverflowError): return None

def maintenance(base):
    lock = Path(base) / "tools/deploy.lock"
    try:
        pid = int(lock.read_text().split()[0])
        if pid <= 1: return True
        try: os.kill(pid, 0); return True
        except ProcessLookupError: return False
        except PermissionError: return True
    except FileNotFoundError: return False
    except (OSError, ValueError, IndexError): return True

def registered_bridge_group(runner):
    """Trust the loaded active systemd unit, never a process argv label."""
    try:
        result = runner(["systemctl", "show", BRIDGE_UNIT, "-p", "Id", "-p", "LoadState",
                         "-p", "ActiveState", "-p", "ControlGroup", "-p", "MainPID"],
                        capture_output=True, text=True, timeout=5)
        if result.returncode != 0: return None
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if (values.get("Id") == BRIDGE_UNIT and values.get("LoadState") == "loaded"
                and values.get("ActiveState") == "active" and int(values.get("MainPID") or 0) > 1
                and values.get("ControlGroup") == BRIDGE_CGROUP):
            return BRIDGE_CGROUP
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        pass
    return None


def registered_bridge_cli(pid, group, cgroup_reader=None):
    """Only kernel cgroup membership can exempt the registered provider daemon."""
    if group != BRIDGE_CGROUP: return False
    try:
        reader = cgroup_reader or (lambda process_id: Path("/proc", str(process_id), "cgroup").read_text())
        for line in reader(pid).splitlines():
            entry = line.split(":", 2)
            if (len(entry) == 3 and (entry[1] == "" or "name=systemd" in entry[1].split(","))
                    and entry[2] == group):
                return True
    except (OSError, ValueError, TypeError):
        pass
    return False


def probe(base=BASE, now=None, runner=None, cgroup_reader=None):
    """Fresh local evidence only. A failed process probe never means idle."""
    base = Path(base); now = time.time() if now is None else now
    kills = ("state/thf/ENGINE_KILL", "state/vd_engine/ENGINE_KILL", "ENGINE_KILL")
    killed = any((base / name).exists() for name in kills)
    pit = base / "data/pit/pit.sqlite"
    lease = (base / "SGP_PRIMARY").is_file() and pit.is_file() and pit.stat().st_size > 0
    engine_pids = []; busy_pids = []; registered_bridge_pids = []; foreign_cli_pids = []; process_known = False
    bridge_group = None; bridge_checked = False
    try:
        result = (runner or subprocess.run)(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            parsed = 0
            for row in result.stdout.splitlines():
                parts = row.strip().split(None, 1)
                if len(parts) != 2 or not parts[0].isdigit(): continue
                parsed += 1; pid = int(parts[0])
                if pid == os.getpid(): continue
                if ENGINE.search(parts[1]): engine_pids.append(pid)
                if ROUND.search(parts[1]):
                    busy_pids.append(pid)  # A real desk round is busy in EVERY cgroup.
                elif CLAUDE_CLI.search(parts[1]):
                    if not bridge_checked:
                        bridge_group = registered_bridge_group(runner or subprocess.run)
                        bridge_checked = True
                    if registered_bridge_cli(pid, bridge_group, cgroup_reader):
                        registered_bridge_pids.append(pid)
                    else:
                        foreign_cli_pids.append(pid)  # enginebusy_20261106 (master fixer): a Claude CLI that is NOT a desk round (the master fixer, code_hands, a chat answer) is reported, never "busy": on 6 Nov 16:00 WITA the supervisor refused to spawn the engine for as long as the master fixer's own CLI ran ("Known idle state is required"), leaving the desk with no engine. Spawning the engine kills nothing; only a real desk round (ROUND) is protected work.
            process_known = parsed > 0
    except (OSError, subprocess.SubprocessError): pass
    try: hb = json.loads((base / "state/vd_engine/hb/engine.json").read_text())
    except (OSError, ValueError): hb = {}
    hb_at = timestamp(hb.get("utc")); hb_pid = hb.get("pid")
    boot_at = timestamp(hb.get("boot"))
    completed_at = timestamp(hb.get("last_cycle_ok_utc"))
    failures = hb.get("fails")
    completed = (boot_at is not None and completed_at is not None
                 and boot_at <= completed_at <= now and type(failures) is int and failures == 0)
    alive = bool(engine_pids) if process_known else None
    fresh = hb_at is not None and 0 <= now - hb_at <= 300 and hb_pid in engine_pids
    return dict(observed_at=now, healthy=bool(alive and fresh and lease and not killed and completed),
                active_state="active" if alive else "inactive" if process_known else "unknown",
                busy=bool(busy_pids) if process_known else None, lease_valid=lease,
                engine_kill=killed, engine_child_alive=alive, engine_pids=engine_pids,
                busy_pids=busy_pids, registered_bridge_pids=registered_bridge_pids, foreign_cli_pids=foreign_cli_pids,
                heartbeat_at=hb_at, heartbeat_pid=hb_pid,
                engine_boot_at=boot_at, completed_cycle_at=completed_at, heartbeat_fails=failures,
                maintenance=bool(not lease or killed or maintenance(base)))

def write_status(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".%s.tmp" % os.getpid())
    with temp.open("w") as stream:
        json.dump(value, stream, default=str); stream.flush(); os.fsync(stream.fileno())
    os.replace(temp, path)

class Supervisor:
    def __init__(self, base=BASE, *, guard=None, scanner=None, starter=None, clock=None):
        self.base = Path(base); self.guard = guard
        self.scanner = scanner or probe; self.starter = starter or subprocess.Popen
        self.clock = clock or time.time; self.child = None; self.started_at = None

    def _guard(self):
        if self.guard is None:
            from veteran_desk import healing_guard
            self.guard = healing_guard
        return self.guard

    def _launch(self):
        self.base.joinpath("logs").mkdir(parents=True, exist_ok=True)
        py = "/opt/tfo/venv/bin/python"
        if not Path(py).is_file(): py = sys.executable
        command = [py, "-m", "veteran_desk.vd_engine", "daemon"]
        env = dict(os.environ, THF_DESK="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1", TFO_HOST="tfo-sgp1")
        with self.base.joinpath("logs/vd_engine.log").open("ab") as log:
            self.child = self.starter(command, cwd=str(self.base), stdout=log, stderr=subprocess.STDOUT, env=env, close_fds=True)
        self.started_at = self.clock()
        return True, "engine child submitted pid=%s" % self.child.pid

    def stop_owned(self):
        if self.child is None or self.child.poll() is not None: return True
        self.child.terminate()
        try: self.child.wait(timeout=10); return True
        except subprocess.TimeoutExpired: return False  # no blind SIGKILL of an active round

    def step(self):
        now = self.clock(); observation = self.scanner(self.base, now=now)
        owned_alive = self.child is not None and self.child.poll() is None
        if not observation.get("lease_valid") or observation.get("engine_kill"):
            stopped = self.stop_owned()
            result = dict(accepted=False, action="blocked", reason="lease/pause/data condition does not permit engine", stopped=stopped)
        else:
            if owned_alive:
                observation["engine_child_alive"] = True; observation["active_state"] = "active"
                completed_at = observation.get("completed_cycle_at")
                observation["healthy"] = bool(observation.get("healthy") and observation.get("heartbeat_pid") == self.child.pid
                    and observation.get("heartbeat_at") is not None and observation["heartbeat_at"] >= self.started_at
                    and type(completed_at) in (int, float) and math.isfinite(completed_at)
                    and self.started_at <= completed_at <= now
                    and type(observation.get("heartbeat_fails")) is int and observation["heartbeat_fails"] == 0)
            guard = self._guard(); path = str(self.base / "state/thf/recovery_guard.json")
            if observation.get("engine_child_alive"):
                result = guard.observe(UNIT, observation, owner="engine_supervisor", state_path=path, now=now)
            else:
                result = guard.guarded_attempt(UNIT, "spawn", self._launch, owner="engine_supervisor",
                    observation=observation, state_path=path, now=now)
        status = dict(result, observed_at=now, paper_only=True, child_pid=getattr(self.child, "pid", None),
                      observation=observation)
        write_status(self.base / "state/vd_engine/supervisor_health.json", status)
        return status

def main():
    if len(sys.argv) != 1: raise SystemExit("engine supervisor accepts no commands or arguments")
    controller = Supervisor()
    def stop(*_):
        controller.stop_owned()
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    while True:
        try: controller.step()
        except Exception as error:
            # Failure must never fall through to an unguarded engine launch.
            print("engine supervisor recovery check failed: %s" % type(error).__name__, flush=True)
        time.sleep(30)

if __name__ == "__main__": main()
