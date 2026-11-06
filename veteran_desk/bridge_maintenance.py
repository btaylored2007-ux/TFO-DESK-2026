"""Bounded, explicit bridge-maintenance admission. No book or service control.

A present invalid/expired control fails closed and needs an explicit release.
The deadline bounds recovery launches, not the time an interrupted original is
retained. Only the msgdesk process owns retry writes and its status receipt.
"""
import contextlib
import functools
import hashlib
import json
import math
import os
import re
import threading
import time
import uuid

VERSION = "bridge-maintenance-v1"
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTROL = os.path.join(BASE, "state/thf/bridge_maintenance.json")
STATUS = os.path.join(BASE, "state/thf/bridge_maintenance_status.json")
_LIVE_CONTROL = CONTROL
_LOCAL = threading.local()
_KEY = re.compile(r"^[A-Za-z0-9_.:@-]{1,100}\|[A-Za-z0-9_-]{1,60}$")

class MaintenanceHold(RuntimeError):
    pass

def is_hold(error):
    return isinstance(error, MaintenanceHold) or "bridge maintenance hold" in str(error).lower()

def original_hash(message):
    # Delivery/ack metadata is mutable. These fields are the original instruction.
    fields = ("id", "text", "seat", "kind", "images", "ai", "command", "ts_wita")
    data = {key: message[key] for key in fields if key in message}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()

def read_control(path=None, now=None):
    path = path or CONTROL
    if os.environ.get("PYTEST_CURRENT_TEST") and path == _LIVE_CONTROL:
        return None  # Tests must opt into a temporary control, never the live hold.
    now = time.time() if now is None else now
    try:
        with open(path) as stream: value = json.load(stream)
    except FileNotFoundError:
        return None
    except Exception as error:
        return dict(phase="blocked", generation="invalid", error="Invalid maintenance control: "+type(error).__name__, protected={})
    try:
        if not isinstance(value, dict): raise ValueError("object required")
        if value.get("version") != VERSION: raise ValueError("unsupported version")
        generation = value["generation"]
        if not isinstance(generation, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", generation): raise ValueError("generation")
        phase = value["phase"]
        if phase not in ("hold", "recover", "released"): raise ValueError("phase")
        created, deadline = float(value["created_at"]), float(value["deadline"])
        if not all(math.isfinite(x) for x in (created, deadline)) or not 0 < deadline-created <= 1800: raise ValueError("finite deadline must be within30min")
        entries = value.get("protected", [])
        if not isinstance(entries, list) or len(entries) > 100: raise ValueError("protected entries")
        protected = {}
        for entry in entries:
            if not isinstance(entry, dict) or not _KEY.fullmatch(str(entry.get("key", ""))): raise ValueError("key")
            key = entry["key"]
            if key in protected: raise ValueError("duplicate key")
            if entry.get("replay") not in ("safe", "blocked"): raise ValueError("replay classification")
            if not re.fullmatch(r"[a-f0-9]{64}", str(entry.get("original_sha256", ""))): raise ValueError("original hash")
            if not str(entry.get("review_id", "")).strip(): raise ValueError("review receipt required")
            protected[key] = dict(entry)
        result = dict(value, protected=protected)
        if phase != "released" and (now < created-5 or now >= deadline):
            result.update(phase="blocked", error="Maintenance window expired or clock predates control; explicit review/release required")
        return result
    except (ValueError, TypeError, KeyError) as error:
        return dict(phase="blocked", generation=str(value.get("generation", "invalid")) if isinstance(value, dict) else "invalid", error="Invalid maintenance control: "+str(error), protected={})

def paused(control):
    return bool(control and control.get("phase") != "released")

def attempt():
    return getattr(_LOCAL, "attempt", None)

@contextlib.contextmanager
def use_attempt(value):
    previous=attempt();_LOCAL.attempt=value
    try: yield
    finally: _LOCAL.attempt=previous

def mark_submission():
    value=attempt()
    if value is not None: value["submitted"]=True

def permit():
    return getattr(_LOCAL, "permit", None)

@contextlib.contextmanager
def use_permit(value):
    previous = permit()
    _LOCAL.permit = value
    try: yield
    finally: _LOCAL.permit = previous

def permitted(control, value=None):
    value = permit() if value is None else value
    return bool(control and control.get("phase") == "recover" and value and
                value.get("generation") == control.get("generation") and
                value.get("key") in control.get("protected", {}) and
                control["protected"][value["key"]].get("replay") == "safe" and
                value.get("original_sha256") == control["protected"][value["key"]].get("original_sha256") and
                value.get("review_id") == control["protected"][value["key"]].get("review_id"))

def client_guard():
    control = read_control()
    if paused(control) and not permitted(control):
        raise MaintenanceHold("bridge maintenance hold: "+str(control.get("error") or control.get("phase")))

def atomic_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path+".%d.%s.tmp" % (os.getpid(), uuid.uuid4().hex)
    try:
        with open(temporary, "w") as stream:
            json.dump(value, stream, indent=2); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory=os.open(os.path.dirname(path),os.O_RDONLY | getattr(os,"O_DIRECTORY",0))
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        try: os.unlink(temporary)
        except FileNotFoundError: pass

class Tracker:
    """Track queued AND running owned work, including timed-out provider futures.

    The acknowledgment is process-local, not a claim of atomic server drain.
    External producers and bootstrap interruption still need reconciliation.
    """
    def __init__(self, status=None):
        self.status = status or STATUS
        self.lock = threading.RLock()
        self.active = {}
        self.started = time.time()
        self.instance = uuid.uuid4().hex
        self.last_control = None

    def begin(self, kind, key=None, force=False):
        with self.lock:
            control = read_control()
            if not force and paused(control) and not permitted(control): return None
            token = uuid.uuid4().hex
            self.active[token] = dict(kind=kind, key=key, started=time.time())
            return token

    def end(self, token):
        if token is not None:
            with self.lock: self.active.pop(token, None)

    def count(self):
        with self.lock: return len(self.active)

    def tracked(self, kind, blocked=None, key=None):
        def decorate(function):
            @functools.wraps(function)
            def wrapped(*args, **kwargs):
                identity = key(*args, **kwargs) if key else None
                token = self.begin(kind, identity)
                if token is None: return blocked(*args, **kwargs) if blocked else None
                try: return function(*args, **kwargs)
                finally: self.end(token)
            return wrapped
        return decorate

    def submit(self, executor, function, *args, **kwargs):
        # Reserve before submit. A queued future cannot hide from quiescence.
        token = self.begin("provider_or_handler_future", force=True)
        context, budget = permit(), attempt()
        def run():
            try:
                with use_permit(context), use_attempt(budget): return function(*args, **kwargs)
            finally: self.end(token)
        try: future = executor.submit(run)
        except BaseException:
            self.end(token); raise
        future.add_done_callback(lambda completed: self.end(token) if completed.cancelled() else None)
        return future

    def thread(self, *args, **kwargs):
        tracker, context, budget = self, permit(), attempt()
        class OwnedThread(threading.Thread):
            def start(self):
                self.maintenance_token = tracker.begin("owned_thread", force=True)
                try: return super().start()
                except BaseException:
                    tracker.end(self.maintenance_token); raise
            def run(self):
                try:
                    with use_permit(context), use_attempt(budget): return super().run()
                finally: tracker.end(self.maintenance_token)
        return OwnedThread(*args, **kwargs)

    def snapshot(self, control, keys=None):
        with self.lock:
            result = dict(version=VERSION, generation=(control or {}).get("generation"),
                          phase=(control or {}).get("phase", "inactive"), pid=os.getpid(),
                          instance=self.instance, process_started=self.started, checked_at=time.time(),
                          active=list(self.active.values()), tracked_quiesced=not self.active,
                          error=(control or {}).get("error"), keys=keys or {})
            atomic_json(self.status, result)
            self.last_control = control
            return result

class Recovery:
    """Single-owner retry reconciliation. Never reconstruct a missing retry row."""
    NONFINAL = {"instant_status", "deferred", "push_receipt", "task_receipt"}
    def __init__(self, tracker, retry_rw, load_originals, replies, launch, health):
        self.tracker, self.retry_rw = tracker, retry_rw
        self.load_originals, self.replies = load_originals, replies
        self.launch, self.health = launch, health
        self.tick_lock = threading.Lock()

    def managed(self, key, value=None, control=None):
        control = read_control() if control is None else control
        return bool(key in (control or {}).get("protected", {}) or
                    (value or {}).get("maintenance_generation"))

    def handle_allowed(self, message, seats):
        control=read_control();context=permit()
        def check(state):
            for seat in seats:
                key=message["id"]+"|"+seat
                if self.managed(key,state.get(key),control):
                    if not (permitted(control,context) and context.get("key")==key
                            and context.get("original_sha256")==original_hash(message)):
                        return False
            return True
        return self.retry_rw(check)

    def receipt(self, key):
        mid, seat = key.split("|", 1)
        return next((row for row in reversed(self.replies())
                     if row.get("reply_to") == mid and row.get("from") == seat
                     and row.get("kind") not in self.NONFINAL
                     and row.get("mode") not in (None, "note", "deferred", "funder")), None)

    def defer(self, message, seat, error, capacity=False):
        key = message["id"]+"|"+seat
        control = read_control()
        maintenance = is_hold(error)
        def update(state):
            value = state.get(key)
            if not maintenance and not (capacity and self.managed(key, value, control)): return None
            value = value or dict(first=time.time(), tries=0)
            value.update(maintenance_generation=(control or {}).get("generation", "interrupted"),
                         maintenance_original=message, maintenance_original_sha256=original_hash(message),
                         running=0, due=time.time()+60, err=str(error)[:300],
                         maintenance_state="held" if maintenance else "waiting_capacity")
            state[key] = value
            # Existing caller expects a truthy retry number; do not alter tries.
            return max(1, int(value.get("tries", 0)))
        return self.retry_rw(update)

    def preserve(self, message, seats):
        for seat in seats:
            key=message["id"]+"|"+seat
            existing=self.retry_rw(lambda state:dict(state.get(key) or {}))
            if self.managed(key,existing) and (existing.get("maintenance_state") in ("blocked","completed","committing") or existing.get("maintenance_commit_pending")):
                continue
            self.defer(message, seat, MaintenanceHold("bridge maintenance hold: pickup retained"))

    def done(self, mid, seat):
        key = mid+"|"+seat
        def update(state):
            value = state.get(key)
            if not self.managed(key, value): return False
            if value is not None:
                value.update(maintenance_commit_pending=True, maintenance_state="committing")
            return True
        return self.retry_rw(update)

    def finish(self, message, seats):
        for seat in seats:
            key = message["id"]+"|"+seat
            receipt = self.receipt(key)
            def update(state):
                value = state.get(key)
                if not value or not self.managed(key, value): return
                if receipt:
                    # Keep a terminal per-key receipt through the maintenance window.
                    value.update(running=0, maintenance_state="completed", maintenance_receipt={
                        name:receipt.get(name) for name in ("reply_to", "from", "mode", "kind", "ts_wita")})
                    value.pop("maintenance_commit_pending", None)
                elif value.get("maintenance_commit_pending"):
                    entry = (read_control() or {}).get("protected", {}).get(key, {})
                    value.update(running=0, maintenance_state="blocked",
                                 maintenance_error="Outcome may have side effects but no final reply receipt",
                                 maintenance_review_required=entry.get("review_id"))
            self.retry_rw(update)

    def boot(self, state):
        control = read_control()
        for key, value in state.items():
            if value.get("learning"): continue
            if paused(control) or self.managed(key, value, control):
                value.setdefault("maintenance_generation", (control or {}).get("generation", "interrupted"))
                if value.get("running") or value.get("maintenance_commit_pending"):
                    entry = (control or {}).get("protected", {}).get(key, {})
                    value.update(running=0, maintenance_state="blocked",
                                 maintenance_error="Interrupted handler requires post-stop side-effect reconciliation",
                                 maintenance_review_required=entry.get("review_id", "unreviewed"))
                else: value.setdefault("maintenance_state", "held")

    def tick(self):
        if not self.tick_lock.acquire(blocking=False): return None
        try: return self._tick()
        finally: self.tick_lock.release()

    def _tick(self):
        control = read_control()
        if not control: return None
        originals = {row.get("id"):row for row in self.load_originals()}
        statuses = {}
        eligible = []
        def reconcile(state):
            for key, entry in control.get("protected", {}).items():
                value = state.get(key)
                receipt = self.receipt(key)
                if receipt:
                    statuses[key] = dict(state="completed", mode=receipt.get("mode"), at=receipt.get("ts_wita"))
                    if value is not None:
                        value.update(maintenance_generation=control["generation"], maintenance_state="completed", running=0)
                    continue
                if value is None:
                    statuses[key] = dict(state="blocked", error="Retry row absent; no final receipt. Never reenroll automatically")
                    continue
                value.setdefault("maintenance_generation", control["generation"])
                if value.get("maintenance_state") == "completed":
                    statuses[key] = dict(state="blocked", error="Previously recorded completion is missing from reply log; review required")
                    continue
                mid, seat = key.split("|", 1)
                message = originals.get(mid)
                error = control.get("error")
                if value.get("learning"): error = "Learning keys are not ordinary recovery work"
                elif message is None: error = "Original request missing from retained message window"
                elif original_hash(message) != entry["original_sha256"]: error = "Original request hash mismatch"
                elif entry["replay"] != "safe": error = entry.get("reason") or "Side-effect audit has not cleared replay"
                elif value.get("maintenance_review_required") == entry.get("review_id"): error = "A new post-interruption review receipt is required"
                elif value.get("maintenance_commit_pending"):
                    if (value.get("maintenance_review_required") and
                            value["maintenance_review_required"] != entry.get("review_id") and
                            entry.get("interrupted_outcome") == "not_executed"):
                        value.pop("maintenance_commit_pending",None)
                        value.pop("maintenance_review_required",None)
                    else: error = "Unfinished commit requires a new audit explicitly proving not_executed"
                if error:
                    value.update(maintenance_state="blocked", maintenance_error=error)
                    statuses[key] = dict(state="blocked", error=error)
                    continue
                if value.get("running"):
                    statuses[key] = dict(state="running", since=value["running"])
                    continue
                statuses[key] = dict(state="blocked" if control["phase"] == "released" else "held" if control["phase"] != "recover" else "ready", tries=value.get("tries",0))
                if control["phase"] == "recover" and value.get("due",0) <= time.time(): eligible.append((key,message,seat))
            for key, value in state.items():
                if self.managed(key,value,control) and key not in statuses:
                    statuses[key] = dict(state="blocked", error="Retained original is not in the exact reviewed recovery allowlist")
            # Retained terminal rows can be removed only after explicit release;
            # pending/ambiguous rows remain blocked, never enter ordinary burst.
            if control["phase"] == "released":
                for key, value in list(state.items()):
                    if self.managed(key, value, control) and value.get("maintenance_state") == "completed" and self.receipt(key): state.pop(key)
        self.retry_rw(reconcile)
        if eligible and not self.tracker.count() and not any(v.get("state")=="running" for v in statuses.values()):
            health = self.health()
            admission = health.get("admission") or {}
            healthy = (health.get("state")=="logged_in" and not health.get("busy") and
                       not health.get("inflight") and not health.get("queue") and
                       not health.get("admitted") and admission.get("capacity")==2 and
                       admission.get("protocol")=="bounded_seat_admission_v1")
            # A changed/expired generation cannot launch using an older snapshot.
            fresh = read_control()
            if healthy and fresh and fresh.get("phase")=="recover" and fresh == control:
                key,message,seat = eligible[0]
                def mark(state):
                    value = state.get(key)
                    if value is None or value.get("running"): return False
                    value.update(running=time.time(), maintenance_state="running",
                                 maintenance_release_generation=control["generation"])
                    return True
                if self.retry_rw(mark):
                    try:
                        self.launch(message, seat, dict(generation=control["generation"], key=key,
                                    original_sha256=control["protected"][key]["original_sha256"],
                                    review_id=control["protected"][key]["review_id"]))
                        statuses[key] = dict(state="running", tries="unchanged")
                    except Exception as error:
                        def failed(state):
                            if key in state: state[key].update(running=0, maintenance_state="blocked", maintenance_error="Recovery launch failed: "+str(error))
                        self.retry_rw(failed)
                        statuses[key] = dict(state="blocked", error="Recovery launch failed")
        return self.tracker.snapshot(control, statuses)
