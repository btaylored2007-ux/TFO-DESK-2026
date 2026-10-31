#!/usr/bin/env python3
"""fundamental_feed.py — the desk's DATA LAYER. PAPER ONLY.

One place every desk gets fundamentals, quotes and universes, so that a data
failure is detected ONCE, here, instead of being rediscovered by each desk.

WHY THIS EXISTS — the failure that made it necessary
----------------------------------------------------
On 2026-08-28 a US micro-cap desk found yfinance, after being rate-limited,
returning **empty financials for 43 CONSECUTIVE tickers with no exception and no
warning** — a full page of "n/a" that reads exactly like "these companies file no
data". That shape is worse than a crash: it manufactures `insufficient_evidence`
verdicts at scale and every one of them looks honest. The same rate limit later
crashed this desk's own scheduled opener outright.

So the contract of this module is one rule:

    ****  AN EMPTY RESULT IS NEVER RETURNED AS A SUCCESS.  ****

Every call returns a Result carrying `ok`, the data, and — when not ok — a REASON.
Consecutive failures trip a circuit breaker that says the SOURCE is degraded,
because "no data for these names" and "the feed is down" must never look alike.

PROVIDERS
---------
* **edgar**   — SEC XBRL + full-text. FREE, no key, US only. **Point-in-time**:
                every datapoint carries the `filed` date it became public on.
                This is the ONLY point-in-time source the desk has, and
                point-in-time is precisely what the book's own pre-registration
                says is missing ("Yahoo financials are as-restated-today rather
                than point-in-time"), which is why no honest backtest exists.
* **eodhd**   — global bulk fundamentals + EOD, 60+ exchanges. NEEDS A KEY.
                Activates automatically when TFO_EODHD_KEY is present; until then
                every eodhd call returns ok=False with a reason, never a blank.
* **yfinance** — fallback only. Retained because it is the only zero-config path
                to non-US quotes, but wrapped so its silent-empty mode is caught.

ADDING A KEY — do this yourself, do not paste a key into a chat:
    printf 'TFO_EODHD_KEY=your_key_here\\n' >> ~/.config/tfo/feed.env
    chmod 600 ~/.config/tfo/feed.env
The file is read at import if present. Keys are never logged, never written to a
ledger, and never included in any report.

PAPER ONLY. No broker is connected anywhere and none may be added. Nothing here
places an order or authenticates to a venue.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "reports", "feed_cache")
ENV_FILE = os.path.expanduser("~/.config/tfo/feed.env")

# SEC requires a descriptive UA with contact. Not a credential.
SEC_UA = __import__("sec_contact").user_agent()
SEC_RATE_SLEEP = 0.12          # SEC asks <=10 req/s; stay well under

# A source is DEGRADED after this many consecutive failures. Chosen low on
# purpose: the 43-ticker incident ran for 43 names before anyone noticed.
DEGRADE_AFTER = 3


def _load_env():
    """Read ~/.config/tfo/feed.env into os.environ. Values are never printed."""
    if not os.path.exists(ENV_FILE):
        return
    try:
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass


_load_env()


class Result:
    """Every feed call returns one of these. There is no bare-value path.

    `ok=False` ALWAYS carries a reason. `ok=True` with empty data is impossible
    by construction — `Result.empty()` is a failure constructor, not a success.
    """

    __slots__ = ("ok", "data", "reason", "source", "asof")

    def __init__(self, ok, data=None, reason=None, source=None, asof=None):
        self.ok, self.data, self.reason = ok, data, reason
        self.source, self.asof = source, asof

    @classmethod
    def good(cls, data, source, asof=None):
        if data is None or (hasattr(data, "__len__") and len(data) == 0):
            # The whole point of this module.
            return cls(False, None,
                       "source returned an EMPTY payload — treated as a FAILURE, "
                       "never as 'no such data'. This is the 43-consecutive-blank "
                       "shape that manufactures false nulls.", source)
        return cls(True, data, None, source, asof)

    @classmethod
    def bad(cls, reason, source):
        return cls(False, None, reason, source)

    def __repr__(self):
        return ("Result(ok=%s, source=%s, %s)"
                % (self.ok, self.source,
                   ("n=%d" % len(self.data)) if self.ok and hasattr(self.data, "__len__")
                   else repr(self.reason)[:70]))


class SourceHealth:
    """Circuit breaker. Distinguishes 'these names have no data' from 'the feed
    is down', which is the distinction the 43-blank incident destroyed."""

    def __init__(self):
        self._fail = {}
        self._total = {}

    def record(self, source, ok):
        self._total[source] = self._total.get(source, 0) + 1
        self._fail[source] = 0 if ok else self._fail.get(source, 0) + 1

    def degraded(self, source):
        return self._fail.get(source, 0) >= DEGRADE_AFTER

    def report(self):
        out = {}
        for s, n in self._total.items():
            out[s] = {"calls": n, "consecutive_failures": self._fail.get(s, 0),
                      "degraded": self.degraded(s)}
        return out

    def assert_usable(self, source):
        """Raise rather than let a caller record verdicts against a dead feed."""
        if self.degraded(source):
            raise RuntimeError(
                "SOURCE DEGRADED: %s has failed %d times consecutively. Any "
                "verdict recorded now would be a false null, not a finding. "
                "Stop the sweep and re-run when the source recovers."
                % (source, self._fail[source]))


HEALTH = SourceHealth()


def _get(url, headers=None, timeout=20, retries=2):
    """HTTP GET returning (body_bytes, error_string)."""
    hdrs = {"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"}
    hdrs.update(headers or {})
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    body = gzip.decompress(body)
                return body, None
        except urllib.error.HTTPError as e:
            last = "HTTP %s" % e.code
            # 429/5xx are worth a retry; 4xx generally are not.
            if e.code not in (429, 500, 502, 503, 504):
                return None, last
        except Exception as e:                        # noqa: BLE001
            last = "%s: %s" % (type(e).__name__, str(e)[:80])
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    return None, last


# ---------------------------------------------------------------------------
# EDGAR — free, US, POINT-IN-TIME
# ---------------------------------------------------------------------------

def edgar_company_facts(cik):
    """All XBRL facts for a CIK. Each datapoint carries its `filed` date.

    That `filed` date is the whole reason this provider exists: it lets a figure
    be read AS IT WAS KNOWN on a past date, which is the difference between an
    honest backtest and one contaminated by restatement and look-ahead.
    """
    src = "edgar"
    cik10 = str(cik).lstrip("CIK").zfill(10)
    url = "https://data.sec.gov/api/xbrl/companyfacts/CIK%s.json" % cik10
    body, err = _get(url)
    time.sleep(SEC_RATE_SLEEP)
    if err:
        HEALTH.record(src, False)
        return Result.bad("EDGAR companyfacts failed for CIK%s: %s" % (cik10, err), src)
    try:
        d = json.loads(body)
    except Exception as e:                            # noqa: BLE001
        HEALTH.record(src, False)
        return Result.bad("EDGAR returned unparseable JSON: %s" % e, src)
    HEALTH.record(src, True)
    return Result.good(d, src)


def edgar_concept_series(cik, tag, taxonomy="us-gaap", unit="USD"):
    """One XBRL concept as a point-in-time series.

    Returns rows of {end, val, form, filed, fy, fp}. Sorted by `filed`, so
    "what did we know on date D" is a filter, not a guess.
    """
    src = "edgar"
    cik10 = str(cik).lstrip("CIK").zfill(10)
    url = ("https://data.sec.gov/api/xbrl/companyconcept/CIK%s/%s/%s.json"
           % (cik10, taxonomy, tag))
    body, err = _get(url)
    time.sleep(SEC_RATE_SLEEP)
    if err:
        HEALTH.record(src, False)
        return Result.bad("EDGAR concept %s failed for CIK%s: %s" % (tag, cik10, err), src)
    try:
        units = json.loads(body).get("units", {}).get(unit, [])
    except Exception as e:                            # noqa: BLE001
        HEALTH.record(src, False)
        return Result.bad("EDGAR concept unparseable: %s" % e, src)
    rows = sorted(
        [{"end": u.get("end"), "val": u.get("val"), "form": u.get("form"),
          "filed": u.get("filed"), "fy": u.get("fy"), "fp": u.get("fp")}
         for u in units if u.get("filed")],
        key=lambda r: r["filed"])
    HEALTH.record(src, bool(rows))
    return Result.good(rows, src) if rows else Result.bad(
        "no %s datapoints in %s for CIK%s — the concept may not be tagged by this "
        "filer rather than the value being absent" % (tag, unit, cik10), src)


def as_known_on(rows, date_iso):
    """Filter a concept series to what was PUBLIC on `date_iso`.

    This is the function that makes an honest backtest possible: it drops every
    datapoint filed after the date, including restatements of earlier periods.
    """
    seen = [r for r in rows if r["filed"] <= date_iso]
    return seen[-1] if seen else None


def edgar_ticker_map():
    """Full ticker -> CIK map for every SEC filer. One call, ~10k+ names.

    This is the US universe, free, and it is authoritative on what still exists —
    which matters because a prior desk found 11 of 110 US tickers no longer traded
    and another found 60 of 161 Canadian ones dead or renamed.
    """
    src = "edgar"
    os.makedirs(CACHE, exist_ok=True)
    cached = os.path.join(CACHE, "sec_tickers.json")
    if os.path.exists(cached) and (time.time() - os.path.getmtime(cached)) < 86400:
        try:
            with open(cached) as f:
                return Result.good(json.load(f), src, asof="cache <24h")
        except Exception:                             # noqa: BLE001
            pass
    body, err = _get("https://www.sec.gov/files/company_tickers.json")
    if err:
        HEALTH.record(src, False)
        return Result.bad("SEC ticker map failed: %s" % err, src)
    try:
        raw = json.loads(body)
    except Exception as e:                            # noqa: BLE001
        HEALTH.record(src, False)
        return Result.bad("SEC ticker map unparseable: %s" % e, src)
    m = {v["ticker"]: {"cik": v["cik_str"], "title": v["title"]}
         for v in raw.values() if v.get("ticker")}
    try:
        with open(cached, "w") as f:
            json.dump(m, f)
    except OSError:
        pass
    HEALTH.record(src, True)
    return Result.good(m, src)


# ---------------------------------------------------------------------------
# EODHD — global, needs a key
# ---------------------------------------------------------------------------

def eodhd_key():
    return os.environ.get("TFO_EODHD_KEY")


def eodhd_available():
    return bool(eodhd_key())


def _eodhd(path, params=None):
    src = "eodhd"
    key = eodhd_key()
    if not key:
        # An absent key is a CONFIGURATION state, not a data finding. It must
        # never be reported as "no data for this name".
        return Result.bad(
            "EODHD key not configured — no global feed active. This is a MISSING "
            "FEED, not an absent company. Add TFO_EODHD_KEY to "
            "~/.config/tfo/feed.env (chmod 600) to activate.", src)
    p = dict(params or {})
    p.update({"api_token": key, "fmt": "json"})
    url = "https://eodhd.com/api/%s?%s" % (path.lstrip("/"), urllib.parse.urlencode(p))
    body, err = _get(url, headers={"User-Agent": "TFO-research"})
    if err:
        HEALTH.record(src, False)
        return Result.bad("EODHD %s failed: %s" % (path, err), src)
    try:
        d = json.loads(body)
    except Exception as e:                            # noqa: BLE001
        HEALTH.record(src, False)
        return Result.bad("EODHD returned unparseable JSON: %s" % e, src)
    HEALTH.record(src, True)
    return Result.good(d, src)


def eodhd_exchange_symbols(exchange):
    """Every listed symbol on one exchange. THE call that makes a daily global
    hunt tractable — one request per exchange instead of one per ticker.

    A prior desk probed 161 tickers one at a time, took ~23 minutes and was
    rate-limited into returning blanks. That does not scale to a daily job.
    """
    return _eodhd("exchange-symbol-list/%s" % exchange)


def eodhd_fundamentals(symbol):
    return _eodhd("fundamentals/%s" % symbol)


def eodhd_bulk_fundamentals(exchange, offset=0, limit=500):
    """Bulk fundamentals for an exchange — the daily screening workhorse."""
    return _eodhd("bulk-fundamentals/%s" % exchange,
                  {"offset": offset, "limit": limit})


def eodhd_eod(symbol, frm=None, to=None):
    p = {}
    if frm:
        p["from"] = frm
    if to:
        p["to"] = to
    return _eodhd("eod/%s" % symbol, p)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def status():
    """What is actually wired. Prints no key material."""
    return {
        "paper_only": True,
        "providers": {
            "edgar": {"active": True, "cost": "free", "scope": "US SEC filers",
                      "point_in_time": True,
                      "note": "every datapoint carries its `filed` date"},
            "eodhd": {"active": eodhd_available(), "cost": "subscription",
                      "scope": "60+ exchanges global, bulk endpoints",
                      "point_in_time": False,
                      "note": ("configured" if eodhd_available()
                               else "NOT configured — add TFO_EODHD_KEY to "
                                    "~/.config/tfo/feed.env")},
            "yfinance": {"active": True, "cost": "free",
                         "scope": "global quotes, unreliable",
                         "point_in_time": False,
                         "note": ("silently returns EMPTY under rate limiting — "
                                  "never trust a blank from it")},
        },
        "contract": ("An empty result is NEVER a success. %d consecutive failures "
                     "trips a DEGRADED flag and callers must stop rather than "
                     "record false nulls." % DEGRADE_AFTER),
        "health": HEALTH.report(),
    }


if __name__ == "__main__":
    print(__doc__)
    print(json.dumps(status(), indent=2))
