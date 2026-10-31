"""Per-vendor rate limit, exponential backoff, and a disk cache.

The cache stores parsed observations only. Callers pass a cache key, never a URL,
so a vendor key sitting in a query string cannot land on disk. On failure the last
observation is returned stale. If there is no last observation the hit is missing.
Nothing is invented.
"""
import json, os, time
from features.scrub import scrub

# min spacing between calls, cache TTL, backoff base, backoff cap. Seconds.
LIMITS = {
    "fmp": (0.30, 900, 2.0, 120.0),
    "unusual_whales": (1.0, 300, 2.0, 180.0),
    "massive": (0.25, 300, 2.0, 120.0),
    "eodhd": (0.30, 6 * 3600, 2.0, 180.0),
    "coinalyze": (1.5, 300, 2.0, 180.0),
    "eia": (1.0, 6 * 3600, 2.0, 180.0),
    "fred": (0.50, 6 * 3600, 2.0, 120.0),
}


class Hit(object):
    def __init__(self, value, as_of, fresh, missing, reason=None):
        self.value = value
        self.as_of = as_of
        self.fresh = fresh
        self.missing = missing
        self.reason = reason


class Gate(object):
    def __init__(self, name, root, clock=None, sleep=None, limits=None):
        self.name = name
        spacing, ttl, base, cap = limits or LIMITS[name]
        self.spacing = float(spacing)
        self.ttl = float(ttl)
        self.backoff_base = float(base)
        self.backoff_cap = float(cap)
        self.root = os.path.join(root, name)
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self._next_ok = 0.0
        self._fails = {}
        self._blocked_until = {}

    def peek(self, key):
        """Last stored observation, ignoring TTL. None if this key was never stored."""
        return self._read(key)

    def call(self, key, fn):
        """fn() -> (value, as_of). Never raises. Stale cache is preferred to a gap; a gap is flagged."""
        now = self.clock()
        cached = self._read(key)
        blocked = self._blocked_until.get(key, 0.0)
        if now < blocked:
            return self._stale_or_missing(cached, "backoff")
        if cached and (now - cached["stored"]) < self.ttl:
            return Hit(cached["value"], cached["as_of"], True, False, None)
        wait = self._next_ok - now
        if wait > 0:
            self.sleep(wait)
            now = self.clock()
        self._next_ok = now + self.spacing
        try:
            value, as_of = fn()
        except Exception as e:
            n = self._fails.get(key, 0) + 1
            self._fails[key] = n
            delay = min(self.backoff_base * (2 ** (n - 1)), self.backoff_cap)
            self._blocked_until[key] = self.clock() + delay
            return self._stale_or_missing(cached, scrub(e) or type(e).__name__)
        self._fails[key] = 0
        self._blocked_until.pop(key, None)
        self._write(key, value, as_of, self.clock())
        return Hit(value, as_of, True, False, None)

    def _stale_or_missing(self, cached, reason):
        if cached is not None:
            return Hit(cached["value"], cached["as_of"], False, False, reason)
        return Hit(None, None, False, True, reason)

    def _path(self, key):
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in str(key))[:180]
        if not safe:
            safe = "key"
        return os.path.join(self.root, safe + ".json")

    def _read(self, key):
        try:
            with open(self._path(key)) as f:
                doc = json.load(f)
            if "value" in doc and "stored" in doc:
                return doc
        except Exception:
            return None
        return None

    def _write(self, key, value, as_of, stored):
        os.makedirs(self.root, exist_ok=True)
        path = self._path(key)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"value": value, "as_of": as_of, "stored": stored}, f, default=str)
        os.replace(tmp, path)
