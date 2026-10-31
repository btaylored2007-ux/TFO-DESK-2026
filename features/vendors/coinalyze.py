"""Coinalyze funding, open interest and liquidations.

Uses veteran_desk.thf_paid_pack.coinalyze_derivs (key ~/.tfo_secrets/coinalyze.env).
CoinGlass is retired and is not called. A coin outside the desk's mapped perps
is out of scope. A mapped coin with no row is missing, not zero.
"""
from features.envelope import field, from_hit
from features.vendors.common import VendorError, iso, num

MAP = {
    "BTC": "BTC", "XBT": "BTC", "BTCUSD": "BTC", "BTC-USD": "BTC", "KRAKEN:XBTUSD": "BTC",
    "ETH": "ETH", "ETHUSD": "ETH", "ETH-USD": "ETH", "KRAKEN:ETHUSD": "ETH",
    "SOL": "SOL", "SOLUSD": "SOL", "SOL-USD": "SOL", "KRAKEN:SOLUSD": "SOL",
}
FIELDS = ("funding", "open_interest", "liquidations")


def canon(ticker):
    s = str(ticker).strip().upper()
    if s in MAP:
        return MAP[s]
    base = s.split(":")[-1]
    for suf in ("-USDT", "-USD", "USDT", "USD"):
        if base.endswith(suf) and base[: -len(suf)] in MAP:
            return MAP[base[: -len(suf)]]
    return MAP.get(base)


def fetch(ticker, now, client, gate):
    as_of = iso(now)
    name = canon(ticker)
    if not name:
        return {k: field(None, as_of, source="coinalyze", applicable=False, reason="not_a_mapped_perp") for k in FIELDS}

    def fn():
        js = client()
        rows = js.get("rows") if isinstance(js, dict) else None
        if not isinstance(rows, dict) or name not in rows:
            raise VendorError("no coinalyze row")
        return rows[name]

    hit = gate.call("derivs", lambda: (fn(), as_of))
    if hit.missing or not isinstance(hit.value, dict):
        miss = from_hit(hit if hit.missing else type(hit)(None, hit.as_of, False, True, "no coinalyze row"), "coinalyze")
        return {k: miss for k in FIELDS}
    row = hit.value
    return {
        "funding": _one(row, "funding_rate", as_of, hit),
        "open_interest": _one(row, "oi_usd", as_of, hit),
        "liquidations": _liqs(row, as_of, hit),
    }


def _one(row, key, as_of, hit):
    v = num(row.get(key))
    if v is None:
        return field(None, as_of, source="coinalyze", missing=True, reason=key + " absent")
    return field(v, as_of, source="coinalyze", fresh=bool(hit.fresh), reason=hit.reason)


def _liqs(row, as_of, hit):
    long_v = num(row.get("liq_24h_long_usd"))
    short_v = num(row.get("liq_24h_short_usd"))
    if long_v is None and short_v is None:
        return field(None, as_of, source="coinalyze", missing=True, reason="liquidations absent")
    return field(
        {
            "liq_24h_long_usd": long_v,
            "liq_24h_short_usd": short_v,
            "long_missing": long_v is None,
            "short_missing": short_v is None,
        },
        as_of,
        source="coinalyze",
        fresh=bool(hit.fresh),
        reason=hit.reason,
    )
