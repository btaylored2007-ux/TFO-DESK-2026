"""Atomic files, locks, and tree hashes for one customer store."""

import hashlib
import json
import os
import threading
from contextlib import contextmanager

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

_PROCESS = {}
_PROCESS_GUARD = threading.Lock()


def process_lock(key):
    with _PROCESS_GUARD:
        lock = _PROCESS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PROCESS[key] = lock
        return lock


def ensure_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)


def atomic_write(path, data):
    ensure_dir(os.path.dirname(path))
    temporary = path + ".tmp"
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def write_json(path, value):
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
    atomic_write(path, raw)
    return raw


def write_exclusive(path, data):
    ensure_dir(os.path.dirname(path))
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json_exclusive(path, value):
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
    write_exclusive(path, raw)
    return raw


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        if default is None:
            raise
        return default


def read_bytes(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        return b""


def jsonl_line(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"


def append_jsonl(path, value):
    ensure_dir(os.path.dirname(path))
    raw = jsonl_line(value)
    fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    return raw


def iter_jsonl(path):
    raw = read_bytes(path)
    rows = []
    for line in raw.splitlines():
        if line.strip():
            rows.append(json.loads(line.decode("utf-8")))
    return rows


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def hash_tree(root):
    digest = hashlib.sha256()
    if not os.path.isdir(root):
        digest.update(b"missing")
        return digest.hexdigest()
    for dirpath, dirs, names in os.walk(root):
        dirs.sort()
        for name in sorted(names):
            if name.endswith(".tmp"):
                continue
            full = os.path.join(dirpath, name)
            if not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, root).encode("utf-8")
            digest.update(rel)
            digest.update(b"\0")
            with open(full, "rb") as handle:
                digest.update(handle.read())
            digest.update(b"\0")
    return digest.hexdigest()


@contextmanager
def file_lock(path):
    ensure_dir(os.path.dirname(path))
    handle = open(path, "a+")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
