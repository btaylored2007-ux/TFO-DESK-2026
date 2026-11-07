"""THF crypto data feed (tfo_thfcrypto_20261031). PAPER ONLY. Read-mostly helper for vd_engine's crypto seat.
- is_pegged(): stablecoin / fiat-pegged filter (name list + price-behaviour check), used BEFORE scoring.
- hourly(): Kraken PUBLIC 1h OHLC cache (state/vd_engine/crypto_1h.json), refreshed by a background thread.
- derivs(): Coinalyze funding + open interest cache (state/vd_engine/crypto_derivs.json), background thread.
Every reader fails OPEN: missing/stale/broken data -> None -> neutral pillar contribution. Nothing here can block a trade.
Key: ~/.tfo_secrets/coinalyze.env COINALYZE_API_KEY (read via thf_paid_pack._key; never logged or written)."""
import json, os, threading, time, urllib.error, urllib.request
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ST = os.path.join(BASE, "state", "vd_engine")
H1 = os.path.join(ST, "crypto_1h.json"); DV = os.path.join(ST, "crypto_derivs.json"); KF = os.path.join(ST, "kraken_futs.json")
STABLE_BASES = {"USDT", "USDC", "DAI", "PYUSD", "USDG", "RLUSD", "USDD", "TUSD", "FDUSD", "EUR", "GBP", "AUD", "CAD", "CHF", "JPY", "USD",
                "USDE", "USDS", "USD1", "EURC", "EURT", "USDQ", "FRAX", "LUSD", "GUSD", "PAXG", "XAUT", "AUSD", "QCAD", "EUROP", "EURQ", "EURR",
                "USDR", "BUSD", "UST", "USTC", "CUSD", "SUSD", "MIM", "CRVUSD", "GHO", "USR", "USDP", "USDB", "USD0", "USYC", "BOLD", "EUROC",
                "TGBP", "XGBP", "ZUSD", "ZEUR", "ZGBP", "ZCAD", "ZJPY", "ZAUD", "ZCHF"}
FIAT = ("EUR", "GBP", "AUD", "CAD", "CHF", "JPY", "USD", "NZD", "SGD", "HKD")
def is_pegged(alt, k24=None, closes=None):
    """True for stablecoins and fiat-pegged tokens. alt = Kraken altname e.g. AUSDUSD."""
    base = alt[:-3] if alt.endswith("USD") else alt
    if base.upper() in STABLE_BASES: return True
    b = base.upper()
    if any(b == "Z" + f or b == "X" + f for f in FIAT): return True
    if any(f in b for f in FIAT) and closes and len(closes) >= 30:   # names like QCAD / EURQ / USDx variants
        cs = [x for x in closes[-30:] if x]
        if cs and (max(cs) - min(cs)) / (sum(cs) / len(cs)) < 0.03: return True
    if closes and len(closes) >= 30:   # behaviour: 30 daily closes inside +-1% of a ~$1 peg
        cs = [x for x in closes[-30:] if x]
        if cs and all(0.97 <= x <= 1.03 for x in cs) and (max(cs) - min(cs)) / (sum(cs) / len(cs)) < 0.02: return True
    return False
def _rj(p):
    try: return json.load(open(p))
    except Exception: return None
def _wj(p, o):
    t = p + ".tmp%d" % os.getpid(); json.dump(o, open(t, "w")); os.replace(t, p)
# ---------------- Kraken 1h bars ----------------
def _kget(url):
    try:
        import sys; sys.path.insert(0, BASE); import kraken_dns; kraken_dns.install()
    except Exception: pass
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "TFO paper desk"}), timeout=20) as f:
        d = json.loads(f.read())
    if d.get("error"): raise RuntimeError(";".join(d["error"])[:80])
    return d.get("result") or {}
def refresh_hourly(alts, sleep=1.1):
    """alts: {sym: alt}. Pulls 1h OHLC (~720 bars). Keeps the previous cache entry on any per-pair error."""
    old = (_rj(H1) or {}).get("bars") or {}; out = dict(old); ok = 0
    for sym, alt in alts.items():
        try:
            r = _kget("https://api.kraken.com/0/public/OHLC?pair=%s&interval=60" % alt)
            rows = next((v for k, v in r.items() if k != "last"), [])
            # [time, open, high, low, close, vwap, volume, count]; drop the still-forming last bar
            out[sym] = [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[6])] for x in rows[:-1]][-240:]; ok += 1
        except Exception: pass
        time.sleep(sleep)
    _wj(H1, dict(utc_ts=time.time(), n=len(out), ok_last=ok, bars=out)); return ok
_H1MEM = [0.0, {}]
def hourly(sym, max_age_s=3 * 3600):
    """Completed 1h bars [[ts,o,h,l,c,v],...] or None (stale/missing -> fail open)."""
    try:
        m = os.path.getmtime(H1)
        if m != _H1MEM[0]: _H1MEM[1] = _rj(H1) or {}; _H1MEM[0] = m
        J = _H1MEM[1]; rows = (J.get("bars") or {}).get(sym)
        if not rows or len(rows) < 48 or time.time() - rows[-1][0] > max_age_s: return None
        return rows
    except Exception: return None
def intraday_feats(sym, d):
    """Hourly P/V add-ons, direction d (+1/-1). Returns dict or None."""
    R = hourly(sym)
    if not R: return None
    C = [r[4] for r in R]; V = [r[5] for r in R]
    try:
        r24 = C[-1] / C[-25] - 1; r72 = C[-1] / C[-73] - 1 if len(C) > 73 else r24
        tr = [max(R[i][2] - R[i][3], abs(R[i][2] - C[i - 1]), abs(R[i][3] - C[i - 1])) for i in range(len(R) - 24, len(R))]
        atr1 = (sum(tr) / len(tr)) / C[-1] or 1e-6
        k = 2 / 21; e = C[-60] if len(C) >= 60 else C[0]
        for x in C[-60:]: e = x * k + e * (1 - k)
        er = abs(C[-1] - C[-25]) / (sum(abs(C[i] - C[i - 1]) for i in range(len(C) - 24, len(C))) or 1e-9)
        base = sorted(V[-169:-6])[len(V[-169:-6]) // 2] if len(V) >= 175 else (sorted(V[:-6])[len(V[:-6]) // 2] if len(V) > 12 else 0)
        rv6 = (sum(V[-6:]) / 6 / base) if base else None
        P1 = max(-3, min(3, d * r24 / (atr1 * 5))) * .4 + max(-3, min(3, d * r72 / (atr1 * 9))) * .3 + (1 if d * (C[-1] - e) > 0 else -1) * .3 + er * d * (1 if d * r24 > 0 else -1) * .5
        V1 = (min(4, rv6) - 1) if rv6 else 0.0
        return dict(P1=round(P1, 3), V1=round(V1, 3), r24h=round(r24, 4), rvol6h=round(rv6, 2) if rv6 else None, n1h=len(R))
    except Exception: return None
# ---------------- Coinalyze funding + OI ----------------
CZ = "https://api.coinalyze.net/v1"
CZ_MAP = {"XBT": "BTC", "XDG": "DOGE"}
def cz_sym(alt):
    b = alt[:-3] if alt.endswith("USD") else alt
    return CZ_MAP.get(b, b) + "USDT_PERP.A"   # Binance USDT perp (same venue as thf_paid_pack)
def _key():
    try:
        from veteran_desk import thf_paid_pack as PP; return PP._key("coinalyze", "COINALYZE_API_KEY")
    except Exception: return None
def _czget(path, key, retry=True):
    try: return _czget1(path, key)
    except urllib.error.HTTPError as e:   # shared key: honour Retry-After once (background thread only)
        if e.code != 429 or not retry: raise
        try: w = float(e.headers.get("Retry-After") or 30)
        except Exception: w = 30
        time.sleep(min(w, 65) + 1); return _czget1(path, key)
def _czget1(path, key):
    with urllib.request.urlopen(urllib.request.Request(CZ + path, headers={"api_key": key, "User-Agent": "TFO paper desk"}), timeout=20) as f:
        return json.loads(f.read())
_MK = [0.0, []]
def refresh_derivs(alts):
    """alts: {sym: alt}. Batches of 20 symbols. Writes funding (%), OI usd now and 24h change. No key -> no-op."""
    key = _key()
    if not key: return 0
    syms = {cz_sym(a): s for s, a in alts.items()}; out = {}; now = int(time.time())
    try:   # keep only symbols Coinalyze lists (one bad symbol fails the whole batch); fallback: any listed USDT/USD perp on any venue
        if time.time() - _MK[0] > 86400 or not _MK[1]: _MK[1] = _czget("/future-markets", key) or []; _MK[0] = time.time()
        mk = _MK[1]; have = {m.get("symbol") for m in mk}
        alt_by_base = {}
        for m in mk:
            if m.get("is_perpetual") and str(m.get("quote_asset")) in ("USDT", "USD", "USDC"): alt_by_base.setdefault(str(m.get("base_asset")).upper(), m.get("symbol"))
        fixed = {}
        for cs, s in syms.items():
            if cs in have: fixed[cs] = s
            else:
                alt = alt_by_base.get(cs.split("USDT_PERP")[0].upper())
                if alt and alt not in fixed: fixed[alt] = s
        syms = fixed
    except Exception: pass
    names = list(syms)
    for i in range(0, len(names), 20):
        b = ",".join(names[i:i + 20])
        for path, fld in (("/funding-rate?symbols=" + b, "fr"), ("/open-interest-history?interval=1hour&convert_to_usd=true&from=%d&to=%d&symbols=%s" % (now - 26 * 3600, now, b), "oi")):
            try:
                for x in _czget(path, key) or []:
                    s = syms.get(x.get("symbol"))
                    if not s: continue
                    o = out.setdefault(s, {})
                    if fld == "fr" and x.get("value") is not None: o["funding_pct"] = float(x["value"])
                    if fld == "oi":
                        h = x.get("history") or []
                        if len(h) >= 2 and h[0].get("c"):
                            o["oi_usd"] = float(h[-1]["c"]); o["oi_chg24"] = float(h[-1]["c"]) / float(h[0]["c"]) - 1
            except Exception: pass
            time.sleep(1.6)   # Coinalyze free tier ~40 req/min
    old = (_rj(DV) or {}).get("rows") or {}
    if out: _wj(DV, dict(utc_ts=time.time(), n=len(out), src="coinalyze_v1 funding-rate + open-interest-history (Binance USDT perps)", rows=out))
    return len(out)
# ---------------- Kraken Futures public funding / OI / basis (tfo_krakenfuts_20261101, no key) ----------------
KFURL = "https://futures.kraken.com/derivatives/api/v3/tickers"
def refresh_kraken_futs(alts):
    """alts: {sym: alt (Kraken altname e.g. XBTUSD)}. One public call. Per sym: kf_funding_pct (8h-equivalent %, same unit as
    Coinalyze funding_pct), kf_oi_usd, kf_basis_pct (perp mark vs index). Fails open (returns 0, keeps old file)."""
    try:
        import sys; sys.path.insert(0, BASE); import kraken_dns; kraken_dns.install()
    except Exception: pass
    try:
        with urllib.request.urlopen(urllib.request.Request(KFURL, headers={"User-Agent": "TFO paper desk"}), timeout=20) as f:
            d = json.loads(f.read())
        tk = {x.get("symbol"): x for x in (d.get("tickers") or []) if x.get("tag") == "perpetual" and not x.get("suspended")}
    except Exception: return 0
    out = {}
    for s, a in (alts or {}).items():
        x = tk.get("PF_" + a) or tk.get("PF_" + CZ_MAP.get(a[:-3], a[:-3]) + "USD")
        if not x: continue
        try:
            mk, ix = float(x.get("markPrice") or 0), float(x.get("indexPrice") or 0); o = {"kf_sym": x["symbol"]}
            if x.get("relativeFundingRate") is not None: o["kf_funding_pct"] = round(float(x["relativeFundingRate"]) * 8 * 100, 6)
            if mk and x.get("openInterest") is not None: o["kf_oi_usd"] = round(float(x["openInterest"]) * mk, 2)
            if mk and ix: o["kf_basis_pct"] = round((mk / ix - 1) * 100, 4)
            out[s] = o
        except Exception: pass
    if out: _wj(KF, dict(utc_ts=time.time(), n=len(out), src="kraken_futures_v3 public tickers (PF_ perps): relativeFundingRate x8, openInterest x mark, mark/index basis", rows=out))
    return len(out)
def kraken_futs(sym, max_age_s=3 * 3600):
    J = _rj(KF) or {}
    if time.time() - float(J.get("utc_ts") or 0) > max_age_s: return None
    return (J.get("rows") or {}).get(sym)
def f_pillar(sym, d, close_ret24=None, max_age_s=3 * 3600):
    """Crypto F pillar raw value, direction-aware. None data -> 0.0 (neutral)."""
    try:
        J = _rj(DV) or {}
        o = (J.get("rows") or {}).get(sym) if time.time() - float(J.get("utc_ts") or 0) <= max_age_s else None
        k = kraken_futs(sym, max_age_s) or {}   # tfo_krakenfuts_20261101: Kraken perp funding fills Coinalyze gaps; basis adds a small crowding term
        if not o and not k: return 0.0
        o = o or {}; v = 0.0; fr = o.get("funding_pct", k.get("kf_funding_pct")); oc = o.get("oi_chg24")
        if k.get("kf_basis_pct") is not None: v += max(-0.5, min(0.5, -d * float(k["kf_basis_pct"]) / 0.2))
        if fr is not None: v += max(-1.5, min(1.5, -d * fr / 0.03))     # crowded same-side funding is a headwind, paid-to-hold a tailwind
        if oc is not None:
            conf = 1 if (close_ret24 is None or d * close_ret24 > 0) else -1   # OI building with price in our direction = confirmation
            v += max(-1.5, min(1.5, conf * oc / 0.10))
        return round(v, 3)
    except Exception: return 0.0
# ---------------- background refresher ----------------
_TH = {"t": None}
def start(alts_fn, every_s=900):
    """Daemon thread; alts_fn() -> {sym: alt}. Never raises into the caller."""
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("THF_CRYPTO_FEED") == "0": return
    if _TH["t"] and _TH["t"].is_alive(): return
    def loop():
        while True:
            try:
                a = alts_fn() or {}
                if a:
                    for fn in (refresh_kraken_futs, refresh_derivs, refresh_hourly):
                        try: fn(a)
                        except Exception: pass
            except Exception: pass
            time.sleep(every_s)
    _TH["t"] = threading.Thread(target=loop, name="thf-crypto-feed", daemon=True); _TH["t"].start()
