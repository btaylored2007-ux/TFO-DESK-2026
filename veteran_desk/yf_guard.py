"""tfo_seatpipe_yf429_20261030: Yahoo 429 guard for yfinance (installed on `import veteran_desk.pit`, which book/desk/news_desk/fut_roll all import).
- Ticker.history / yf.download: retry with backoff (2s, 5s, 12s) on YFRateLimitError / 'Too Many Requests'.
- Short in-process cache (60s) of identical calls so a run does not hammer Yahoo for the same symbol.
- If all retries fail, return the last good frame for that exact call (<=6h old, tagged attrs['yf_guard_stale']) instead of empty; else re-raise / empty as before.
Disabled cache under pytest. Never fabricates prices."""
import os, sys, time, threading
_C = {}; _L = threading.Lock(); TTL = 60; STALE_MAX = 6 * 3600; WAITS = (2, 5, 12)
def _is429(x): return type(x).__name__ == "YFRateLimitError" or "Too Many Requests" in str(x) or "429" in str(x)
def _call(key, fn):
    now = time.time(); test = bool(os.environ.get("PYTEST_CURRENT_TEST"))
    with _L: hit = _C.get(key)
    if hit and not test and now - hit[0] < TTL: return hit[1].copy()
    err = None
    for i in range(len(WAITS) + 1):
        try:
            r = fn()
            if r is not None and len(r): 
                with _L: _C[key] = (time.time(), r)
            return r
        except Exception as x:
            err = x
            if not _is429(x) or i == len(WAITS): break
            print("yf_guard: 429 on %s, backoff %ss" % (key[0], WAITS[i]), file=sys.stderr); time.sleep(WAITS[i])
    if hit and _is429(err) and time.time() - hit[0] < STALE_MAX:
        r = hit[1].copy(); r.attrs["yf_guard_stale"] = round(time.time() - hit[0]); return r
    raise err
def install():
    try: import yfinance as yf
    except Exception: return
    if getattr(yf, "_tfo_guard", False): return
    try: yf.config.network.retries = 2   # yfinance >=0.2.6x native retry where available
    except Exception: pass
    _h = yf.Ticker.history
    def history(self, *a, **k): return _call(("hist", self.ticker, a, tuple(sorted((x, str(y)) for x, y in k.items()))), lambda: _h(self, *a, **k))
    yf.Ticker.history = history
    _d = yf.download
    def download(*a, **k): return _call(("dl", str(a), tuple(sorted((x, str(y)) for x, y in k.items()))), lambda: _d(*a, **k))
    yf.download = download; yf._tfo_guard = True
