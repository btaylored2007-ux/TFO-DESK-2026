"""Small file helpers. No network, no LLM."""
import datetime as dt, errno, hashlib, json, os, shutil

UTC = dt.timezone.utc
WITA = dt.timezone(dt.timedelta(hours=8))

def utcnow():
    return dt.datetime.now(UTC)

def parse_dt(s):
    if isinstance(s, dt.datetime):
        return s if s.tzinfo else s.replace(tzinfo=UTC)
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00").replace(" ", "T"))
    except Exception:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)

def wita_str(t):
    t = t if isinstance(t, dt.datetime) else utcnow()
    return t.astimezone(WITA).strftime("%Y-%m-%d %H:%M:%S WITA")

def wita_day(t):
    t = t if isinstance(t, dt.datetime) else utcnow()
    return t.astimezone(WITA).date().isoformat()

class StateError(OSError):
    pass

def disk_free(path):
    try:
        return shutil.disk_usage(os.path.dirname(os.fspath(path)) or ".").free
    except Exception:
        return None

def atomic_write(path, obj, min_free=50_000_000):
    """Write via temp file + fsync + replace. Refuses when the filesystem is too full.
    A failed write removes the temp file and leaves the previous file in place."""
    path = os.fspath(path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    free = disk_free(path)
    if free is not None and free < min_free:
        raise StateError(errno.ENOSPC, "disk full: %s bytes free under %s" % (free, directory))
    # tfo_guardianrace_20261102: unique tmp so daemon + oneshot cannot unlink each other's .tmp
    try:
        import time as _t
        tmp = path + ".tmp.%s.%s" % (os.getpid(), int(_t.time() * 1000) % 1000000)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=1, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default

def read_state(path):
    """Load a JSON state file. A corrupt file is renamed aside and reported, not parsed halfway."""
    path = os.fspath(path)
    try:
        raw = open(path, encoding="utf-8").read()
    except FileNotFoundError:
        return None, None
    if not raw.strip():
        return None, "empty"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError:
        bad = path + ".corrupt"
        try:
            os.replace(path, bad)
        except OSError:
            bad = None
        return None, "corrupt" if bad else "corrupt_unmoved"

def clock_drift(now, stamps, future_sec=90):
    """stamps: iterable of (name, iso). A timestamp sitting in the future is clock drift."""
    bad = []
    for name, raw in stamps:
        t = parse_dt(raw)
        if t is not None and (t - now).total_seconds() > future_sec:
            bad.append(dict(name=name, at=raw, ahead_sec=round((t - now).total_seconds(), 1)))
    return bad

def append_jsonl(path, obj):
    path = os.fspath(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, default=str) + "\n")
        fh.flush()

def read_jsonl(path, limit=200):
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines[-limit:]:
        if line.strip():
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()

def age_sec(t, now):
    t = parse_dt(t)
    if t is None:
        return None
    return (now - t).total_seconds()
