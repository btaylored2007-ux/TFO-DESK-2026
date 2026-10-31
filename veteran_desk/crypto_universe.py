"""Kraken universe helpers for the Taylor Hedge Fund crypto seat. PAPER ONLY.

Stablecoin and fiat bases are dropped (AUSD, QCAD, USDT, EUR, ...). Every other
Kraken pair whose 24h traded value is at least A$500k is eligible. Quote FX that
cannot be priced is skipped (the A$500k floor is not invented). Hourly-bar
breakout math lives here so a missing 1h feed can fall back without a network call.
"""
STABLE = (
    "USDT", "USDC", "DAI", "PYUSD", "USDG", "RLUSD", "USDD", "TUSD", "FDUSD",
    "USDE", "USDS", "USD1", "EURC", "EURT", "USDQ", "FRAX", "LUSD", "GUSD",
    "AUSD", "QCAD", "USDP", "BUSD", "AEUR", "EURS", "XSGD", "CADC", "GBPT",
    "PAXG", "XAUT",
    "EUR", "GBP", "AUD", "CAD", "CHF", "JPY", "USD",
)
STABLE_BASES = frozenset(STABLE)
FIAT_BASES = frozenset({"EUR", "GBP", "AUD", "CAD", "CHF", "JPY", "USD"})
# Longest quote first so BTCUSDT splits as BTC/USDT, not BTCUS/T.
QUOTES = ("USDT", "USDC", "USD", "EUR", "GBP", "AUD", "CAD", "CHF", "JPY")
KRAKEN_QUOTE_CCY = frozenset({
    "ZUSD", "USD", "ZEUR", "EUR", "ZGBP", "GBP", "ZAUD", "AUD", "ZCAD", "CAD",
    "ZJPY", "JPY", "CHF", "USDT", "USDC",
})
MIN_AUD = 500_000.0
ALIAS = {"XBT": "BTC", "XDG": "DOGE"}


def norm_quote(q):
    q = str(q or "").upper()
    if q.startswith("Z") and q[1:] in ("USD", "EUR", "GBP", "AUD", "CAD", "JPY"):
        return q[1:]
    return q


def quote_ok(q):
    return str(q or "").upper() in KRAKEN_QUOTE_CCY


def split_alt(alt):
    a = str(alt or "").upper()
    for q in QUOTES:
        if a.endswith(q) and len(a) > len(q):
            return a[: -len(q)], q
    return a, None


def is_stable_pair(alt):
    """True when the base (the thing being traded) is a stablecoin or a fiat."""
    base, _q = split_alt(alt)
    return base in STABLE_BASES or base in FIAT_BASES


def quote_to_usd(quote, lasts):
    """USD value of 1 unit of quote currency, from Kraken *USD lasts. None if unknown.

    USD/USDT/USDC use 1.0 (the quote unit of the ADV screen, not a trade price).
    JPY uses 1/USDJPY. Everything else needs a <quote>USD last. Missing -> None
    so the caller skips the pair instead of inventing an ADV.
    """
    q = norm_quote(quote)
    lasts = lasts or {}
    if q in ("USD", "USDT", "USDC"):
        return 1.0
    if q == "JPY":
        px = _last(lasts, "USDJPY")
        return (1.0 / px) if px else None
    return _last(lasts, q + "USD")


def _last(lasts, alt):
    v = lasts.get(alt)
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def eligible(pairs, min_aud=MIN_AUD):
    """{KRAKEN:ALT: meta} for liquid, non-stable pairs at or above the A$500k floor."""
    out = {}
    for p in pairs or []:
        alt = str(p.get("alt") or "")
        if not alt or is_stable_pair(alt):
            continue
        if not p.get("liquid"):
            continue
        try:
            adv = float(p.get("vol24h_aud") or 0)
        except (TypeError, ValueError):
            continue
        if adv < min_aud:
            continue
        sym = "KRAKEN:" + alt
        out[sym] = dict(sym=sym, market="CRYPTO", cls="CRYPTO", adv_aud=adv, k24=p)
    return out


def hourly_signal(rows, now, direction, stale_h=3.0):
    """Breakout read on completed 1h bars, or None when the feed is missing/stale.

    rows: oldest->newest (ts_iso, o, h, l, c, v). The caller keeps the daily
    proxy when this returns None (fail open: the name is still scored).
    """
    import datetime as dt
    if not rows or len(rows) < 21:
        return None
    last = rows[-1][0]
    try:
        ts = dt.datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=dt.timezone.utc)
        age_h = (now - ts).total_seconds() / 3600.0
    except Exception:
        return None
    if age_h > stale_h:
        return dict(stale=True, age_h=round(age_h, 2), n=len(rows))
    H = [r[2] for r in rows]; L = [r[3] for r in rows]; C = [r[4] for r in rows]; V = [r[5] or 0 for r in rows]
    prior_v = sorted(V[-21:-1])
    med = prior_v[len(prior_v) // 2] if prior_v else 0
    rvol = (V[-1] / med) if med else None
    d = 1 if direction > 0 else -1
    return dict(stale=False, age_h=round(age_h, 2), n=len(rows), rvol=(round(rvol, 2) if rvol else None),
                broke_up=C[-1] > max(H[-21:-1]), broke_dn=C[-1] < min(L[-21:-1]), dir=d)
