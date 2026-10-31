"""Unusual Whales + Coinglass flow for seat packs. PAPER ONLY.

Flag (first match): env TFO_FLOW_AB=with|without, else state/config/flow_ab.json {"arm": "with"|"without"}.
Default is without (today's pack, the control arm). with adds a flow_ab block for the paper comparison.

Missing keys, HTTP errors and stale cache return a warning and blocks_trade false.
The block never changes stops, size, caps or the firm halt.
"""
import json, os, time, urllib.request
_CACHE = {}
CACHE_TTL = 600


def _root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _key(fname, var):
    k = os.environ.get(var)
    if k:
        return k.strip()
    try:
        for ln in open(os.path.expanduser("~/.tfo_secrets/%s.env" % fname)):
            if ln.strip().startswith(var + "="):
                return ln.split("=", 1)[1].strip().strip("\"'")
    except Exception:
        return None
    return None


def arm(env=None, path=None):
    e = (os.environ.get("TFO_FLOW_AB") if env is None else env)
    if e is None:
        e = os.environ.get("TFO_FLOW_AB", "")
    e = str(e or "").strip().lower()
    if e in ("with", "without"):
        return e
    path = path or os.path.join(_root(), "state", "config", "flow_ab.json")
    try:
        a = str((json.load(open(path)) or {}).get("arm") or "").strip().lower()
        if a in ("with", "without"):
            return a
    except Exception:
        pass
    return "without"


def _get(url, headers, timeout=10):
    req = urllib.request.Request(url, headers=dict({"User-Agent": "TFO paper desk", "Accept": "application/json"}, **(headers or {})))
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return json.loads(f.read())


def coinglass_flow(get=None, now=None, symbols=("BTC", "ETH", "SOL")):
    """Slim funding + OI for a few coins, or None. Key from env or ~/.tfo_secrets/coinglass.env."""
    now = time.time() if now is None else now
    hit = _CACHE.get("cg")
    if hit and now - hit[0] < CACHE_TTL and get is None:
        return hit[1]
    key = _key("coinglass", "COINGLASS_API_KEY")
    if not key and get is None:
        return None
    getter = get or (lambda url, headers: _get(url, headers))
    headers = {"CG-API-KEY": key or "test"}
    out = {}
    try:
        for s in symbols:
            oi = getter("https://open-api-v4.coinglass.com/api/futures/open-interest/exchange-list?symbol=" + s, headers)
            fr = getter("https://open-api-v4.coinglass.com/api/futures/funding-rate/exchange-list?symbol=" + s, headers)
            row = {}
            data = (oi or {}).get("data") if isinstance(oi, dict) else oi
            if isinstance(data, list) and data:
                row["oi_usd"] = data[0].get("open_interest_usd") or data[0].get("openInterest")
            fdata = (fr or {}).get("data") if isinstance(fr, dict) else fr
            if isinstance(fdata, list) and fdata:
                row["funding_rate"] = fdata[0].get("funding_rate") or fdata[0].get("rate")
            if row:
                out[s] = row
    except Exception:
        return None
    if not out:
        return None
    res = dict(src="coinglass_v4_oi_funding", rows=out)
    _CACHE["cg"] = (now, res)
    return res


def uw_flow(get=None):
    try:
        if get is not None:
            return _uw_via(get)
        from veteran_desk import thf_paid_pack as P
        return P.uw_flow()
    except Exception:
        return None


def _uw_via(get):
    """Shape matches thf_paid_pack.uw_flow, using an injected getter (url, headers) -> json."""
    js = get("https://api.unusualwhales.com/api/option-trades/flow-alerts?limit=30", {"Authorization": "Bearer test"})
    rows = js if isinstance(js, list) else (js or {}).get("data") or []
    keep = ("ticker", "type", "total_premium", "strike", "expiry", "alert_rule", "created_at")
    slim = [{k: r.get(k) for k in keep if isinstance(r, dict) and r.get(k) is not None} for r in rows[:15]]
    slim = [r for r in slim if r]
    return dict(src="unusual_whales/api/option-trades/flow-alerts", rows=slim) if slim else None


def for_pack(get_cg=None, get_uw=None, now=None, env=None, path=None):
    """Seat-pack block. Never raises. blocks_trade is always false."""
    a = arm(env=env, path=path)
    base = dict(arm=a, paper_only=True, blocks_trade=False)
    if a != "with":
        return dict(base, included=False, note="control arm: Unusual Whales/Coinglass flow block omitted (paper A/B)")
    if os.environ.get("PYTEST_CURRENT_TEST") and get_cg is None and get_uw is None:
        return dict(base, included=False, ok=False, warning="flow feeds skipped under pytest; neutral", note="fail open")
    warning = None
    try:
        cg = coinglass_flow(get=get_cg, now=now)
    except Exception as e:
        cg = None
        warning = "coinglass %s" % type(e).__name__
    try:
        uw = _uw_via(get_uw) if get_uw is not None else uw_flow()
    except Exception as e:
        uw = None
        warning = (warning + "; " if warning else "") + "unusual_whales %s" % type(e).__name__
    if not cg and not uw:
        return dict(base, included=False, ok=False, warning=warning or "flow feeds missing or stale; neutral", unusual_whales=None, coinglass=None)
    return dict(base, included=True, ok=True, warning=warning, unusual_whales=uw, coinglass=cg,
                note="paper A/B treatment arm. Missing side is null and does not block.")
