"""Paid and free HTTP with a cache, a breaker, and backoff. Keys stay in ~/.tfo_secrets and are never logged.

A failed call returns the last cached body marked stale, or ok=False when there is no cache. It does not raise.
"""
import datetime as dt, json, os, time, urllib.error, urllib.parse, urllib.request
UTC = dt.timezone.utc
SECRETS = os.path.expanduser("~/.tfo_secrets")
SPECS = {
    "unusual_whales": ("unusual_whales.env", "UNUSUAL_WHALES_API_KEY"),
    "fmp": ("fmp.env", "FMP_API_KEY"),
    "eodhd": ("eodhd.env", "EODHD_API_KEY"),
    "massive": ("massive.env", "MASSIVE_API_KEY"),
    "coinalyze": ("coinalyze.env", "COINALYZE_API_KEY"),
    "eia": ("eia.env", "EIA_API_KEY"),
}

def load_key(fname, var):
    """Read one key from the environment or ~/.tfo_secrets/<fname>. Never log the return value."""
    v = os.environ.get(var)
    if v and v.strip(): return v.strip().strip('"').strip("'")
    try:
        for ln in open(os.path.join(SECRETS, fname)):
            s = ln.strip()
            if s.startswith(var + "="): return s.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception: return None
    return None

def redact(text, key=None):
    s = str(text)
    if key and len(key) >= 8: s = s.replace(key, "***")
    out = []
    for part in s.split("&"):
        low = part.lower()
        if any(k in low for k in ("apikey=", "api_token=", "api_key=", "authorization")): out.append(part.split("=", 1)[0] + "=***")
        else: out.append(part)
    return "&".join(out)

class Client:
    def __init__(self, get=None, cache=None, now=None, sleep=None, min_interval=0.25, keys=None):
        self.get = get
        self.cache = cache if cache is not None else {}
        self._now = now or (lambda: dt.datetime.now(UTC))
        self.sleep = sleep if sleep is not None else time.sleep
        self.min_interval = min_interval
        self.keys = keys or {}
        self._last = {}
        self._brk = {}
        self.errors = []

    def now(self):
        n = self._now()
        if isinstance(n, dt.datetime): return n if n.tzinfo else n.replace(tzinfo=UTC)
        return dt.datetime.fromtimestamp(float(n), UTC)

    def key(self, provider):
        if provider in self.keys: return self.keys[provider] or None
        spec = SPECS.get(provider)
        return load_key(*spec) if spec else None

    def call(self, provider, url, headers=None, method="GET", json_body=None, ttl_s=3600):
        ckey = provider + "|" + method + "|" + url.split("?", 1)[0] + "|" + _body_key(json_body)
        cached = self.cache.get(ckey)
        try:
            body = self._fetch(provider, url, headers, method, json_body)
            rec = dict(body=body, fetched_utc=self.now().isoformat())
            self.cache[ckey] = rec
            return dict(ok=True, body=body, fetched_utc=rec["fetched_utc"], from_cache=False, error=None, stale=False)
        except Exception as e:
            err = redact(e, self.key(provider))[:200]
            self.errors.append((provider, err))
            if cached: return dict(ok=True, body=cached.get("body"), fetched_utc=cached.get("fetched_utc"), from_cache=True, error=err, stale=True)
            return dict(ok=False, body=None, fetched_utc=None, error=err, from_cache=False, stale=False)

    def _pace(self, provider):
        last = self._last.get(provider)
        now = time.monotonic()
        if last is not None and self.min_interval:
            wait = self.min_interval - (now - last)
            if wait > 0: self.sleep(wait)
        self._last[provider] = time.monotonic()

    def _fetch(self, provider, url, headers, method, json_body):
        if self._brk.get(provider, 0) > self.now().timestamp(): raise RuntimeError("%s breaker open" % provider)
        delay, last = 0.5, None
        for i in range(3):
            self._pace(provider)
            code, body = self._once(provider, url, headers, method, json_body)
            if code == 429:
                last = RuntimeError("%s HTTP 429" % provider)
                if i < 2:
                    self.sleep(delay); delay = min(delay * 2, 8); continue
                self._brk[provider] = self.now().timestamp() + 1800
                raise last
            if code in (401, 403):
                self._brk[provider] = self.now().timestamp() + 1800
                raise RuntimeError("%s HTTP %s" % (provider, code))
            if code != 200: raise RuntimeError("%s HTTP %s" % (provider, code))
            return body
        raise last or RuntimeError("%s failed" % provider)

    def _once(self, provider, url, headers, method, json_body):
        if self.get is not None:
            out = self.get(provider, url, headers, method, json_body)
            if isinstance(out, tuple) and len(out) == 2: return out
            return 200, out
        if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("PAID_FEEDS_DISABLE") == "1":
            raise RuntimeError("%s network disabled" % provider)
        return _http(provider, url, headers, method, json_body, self.key(provider))

def _body_key(body):
    if not body: return ""
    try: return json.dumps(body, sort_keys=True, default=str)[:180]
    except Exception: return "body"

def _with(url, extra):
    base, _, q = url.partition("?")
    pairs = urllib.parse.parse_qsl(q, keep_blank_values=True)
    pairs.extend(extra.items())
    return base + "?" + urllib.parse.urlencode(pairs)

def _http(provider, url, headers, method, json_body, key):
    if provider in SPECS and not key: raise RuntimeError("%s key missing" % provider)
    headers = dict({"User-Agent": "TFO paper desk", "Accept": "application/json"}, **(headers or {}))
    data = None
    if provider in ("massive", "unusual_whales") and key: headers["Authorization"] = "Bearer " + key
    elif provider == "coinalyze" and key: headers["api_key"] = key
    elif provider == "fmp" and key: url = _with(url, {"apikey": key})
    elif provider == "eodhd" and key: url = _with(url, {"api_token": key, "fmt": "json"})
    elif provider == "eia" and key: url = _with(url, {"api_key": key})
    if method == "POST":
        data = json.dumps(json_body or {}).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=12) as f:
            raw = f.read()
            try: return 200, json.loads(raw)
            except Exception: return 200, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, None
    except urllib.error.URLError as e:
        raise RuntimeError("%s network %s" % (provider, type(e.reason).__name__)) from None
