"""Unusual Whales options flow, put/call ratio and dark pool.

Transport is veteran_desk.thf_paid_pack (key ~/.tfo_secrets/unusual_whales.env).
A put/call ratio is computed only when call volume is non-zero. Otherwise the
ratio is missing and is not stored as zero or infinity.
"""
import urllib.parse
from features.envelope import field, from_hit
from features.vendors.common import VendorError, iso, is_us_equity, num, rows

FIELDS = ("options_flow", "put_call_ratio", "dark_pool")


def fetch(ticker, now, client, gate):
    as_of = iso(now)
    if not is_us_equity(ticker):
        return {name: field(None, as_of, source="unusual_whales", applicable=False, reason="us_listed_only") for name in FIELDS}
    sym = str(ticker).strip().upper()
    return {
        "options_flow": _flow(sym, as_of, client, gate),
        "put_call_ratio": _ratio(sym, as_of, client, gate),
        "dark_pool": _dark(sym, as_of, client, gate),
    }


def _flow(sym, as_of, client, gate):
    q = urllib.parse.urlencode({"ticker_symbol": sym, "limit": 30})

    def fn():
        raw = rows(client("/api/option-trades/flow-alerts?" + q))
        if raw is None:
            raise VendorError("flow payload unusable")
        out = []
        for r in raw[:30]:
            if not isinstance(r, dict):
                continue
            if r.get("ticker") and str(r.get("ticker")).upper() != sym:
                continue
            out.append({
                "ticker": r.get("ticker") or sym,
                "type": r.get("type"),
                "total_premium": num(r.get("total_premium")),
                "strike": num(r.get("strike")),
                "expiry": r.get("expiry"),
                "alert_rule": r.get("alert_rule"),
                "underlying_price": num(r.get("underlying_price")),
                "created_at": r.get("created_at"),
            })
        return {"rows": out}

    return from_hit(gate.call("flow:" + sym, lambda: (fn(), as_of)), "unusual_whales")


def _ratio(sym, as_of, client, gate):
    def fn():
        js = client("/api/stock/%s/options-volume" % urllib.parse.quote(sym))
        raw = rows(js)
        if raw is None and isinstance(js, dict):
            raw = [js]
        if not raw or not isinstance(raw[0], dict):
            raise VendorError("options volume unusable")
        r = raw[0]
        call = num(r.get("call_volume"))
        put = num(r.get("put_volume"))
        ratio = num(r.get("put_call_ratio") if r.get("put_call_ratio") is not None else r.get("putCallRatio"))
        if call is None and put is None and ratio is None:
            raise VendorError("options volume unusable")
        missing_ratio = ratio is None and (call in (None, 0) or put is None)
        if ratio is None and call not in (None, 0) and put is not None:
            ratio = put / call
            missing_ratio = False
        return {
            "date": r.get("date"),
            "call_volume": call,
            "put_volume": put,
            "put_call_ratio": None if missing_ratio else ratio,
            "put_call_ratio_missing": missing_ratio,
        }

    return from_hit(gate.call("pcr:" + sym, lambda: (fn(), as_of)), "unusual_whales")


def _dark(sym, as_of, client, gate):
    def fn():
        raw = rows(client("/api/darkpool/%s" % urllib.parse.quote(sym)))
        if raw is None:
            raise VendorError("dark pool payload unusable")
        out = []
        for r in raw[:30]:
            if not isinstance(r, dict):
                continue
            out.append({
                "ticker": r.get("ticker") or sym,
                "size": num(r.get("size")),
                "price": num(r.get("price")),
                "premium": num(r.get("premium")),
                "executed_at": r.get("executed_at"),
            })
        return {"rows": out}

    return from_hit(gate.call("dark:" + sym, lambda: (fn(), as_of)), "unusual_whales")
