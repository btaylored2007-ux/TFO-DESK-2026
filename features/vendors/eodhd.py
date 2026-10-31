"""EODHD fundamentals for ASX, LSE and Asia names.

Uses veteran_desk.data_layer (key ~/.tfo_secrets/eodhd.env). Absent fundamentals
are listed in gaps and left null. US names are out of scope for this adapter.
"""
import urllib.parse
from features.envelope import field, from_hit
from features.vendors.common import VendorError, iso, num

SUFFIXES = (".AX", ".L", ".T", ".HK", ".KS", ".KQ", ".TW", ".TWO", ".SI", ".NZ", ".SS", ".SZ", ".NS", ".BO")


def in_scope(ticker):
    s = str(ticker).strip().upper()
    return any(s.endswith(suf) for suf in SUFFIXES)


def fetch(ticker, now, client, gate, symbol_of):
    as_of = iso(now)
    if not in_scope(ticker):
        return {"fundamentals": field(None, as_of, source="eodhd", applicable=False, reason="asx_lse_asia_only")}
    code = symbol_of(ticker)
    if not code:
        return {"fundamentals": field(None, as_of, source="eodhd", missing=True, reason="no eodhd symbol")}

    def fn():
        js = client("/fundamentals/" + urllib.parse.quote(code), {})
        slim = _slim(js)
        if slim is None:
            raise VendorError("no fundamentals")
        return slim, slim.get("updated_at") or as_of

    return {"fundamentals": from_hit(gate.call("fund:" + code, fn), "eodhd")}


def _slim(js):
    if not isinstance(js, dict):
        return None
    g = js.get("General") if isinstance(js.get("General"), dict) else {}
    h = js.get("Highlights") if isinstance(js.get("Highlights"), dict) else {}
    v = js.get("Valuation") if isinstance(js.get("Valuation"), dict) else {}
    if not g and not h and not v:
        return None
    out = {
        "name": g.get("Name"),
        "exchange": g.get("Exchange"),
        "currency": g.get("CurrencyCode") or g.get("Currency"),
        "sector": g.get("Sector"),
        "updated_at": g.get("UpdatedAt"),
        "market_cap": num(h.get("MarketCapitalization")),
        "pe": num(h.get("PERatio") if h.get("PERatio") is not None else v.get("TrailingPE")),
        "eps": num(h.get("EarningsShare")),
        "forward_pe": num(v.get("ForwardPE")),
    }
    out["gaps"] = [k for k in ("name", "currency", "market_cap", "pe", "eps") if out.get(k) is None]
    return out
