"""EIA weekly oil and gas inventories.

Uses veteran_desk.thf_paid_pack for the key (~/.tfo_secrets/eia.env) and the HTTP
helper. Crude is WCESTUS1. Natural gas is Lower-48 working gas NW2_EPG0_SWO_R48_BCF.
This is not the petroleum spot feed. A series that does not arrive is missing.
"""
import os, urllib.parse
from features.envelope import field, from_hit
from features.vendors.common import VendorError, aged, iso, num

CRUDE = ("crude", "petroleum/stoc/wstk", "WCESTUS1")
GAS = ("gas", "natural-gas/stor/wkly", "NW2_EPG0_SWO_R48_BCF")


def fetch(now, client, gate):
    as_of = iso(now)

    def fn():
        doc = client()
        if not isinstance(doc, dict) or "crude" not in doc or "gas" not in doc:
            raise VendorError("inventories unavailable")
        if doc["crude"].get("missing") and doc["gas"].get("missing"):
            raise VendorError("inventories unavailable")
        return doc

    hit = gate.call("inventories", lambda: (fn(), as_of))
    doc = from_hit(hit, "eia")
    obs = None
    if isinstance(doc.get("value"), dict):
        for side in ("crude", "gas"):
            part = doc["value"].get(side) or {}
            if part.get("period") and not part.get("missing"):
                obs = part["period"]
                break
    return aged(doc, obs, now, 14)


def default_client():
    """Live EIA read. Disabled under pytest. The key stays in the request and out of errors."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        raise VendorError("disabled under pytest")
    from veteran_desk import thf_paid_pack as PP
    key = PP._key("eia", "EIA_API_KEY")
    if not key:
        raise VendorError("key missing")

    def one(name, route, series):
        q = urllib.parse.urlencode([
            ("api_key", key),
            ("frequency", "weekly"),
            ("data[0]", "value"),
            ("facets[series][]", series),
            ("sort[0][column]", "period"),
            ("sort[0][direction]", "desc"),
            ("length", "4"),
        ])
        js = PP._get("eia_" + name, "https://api.eia.gov/v2/" + route + "/data/?" + q)
        raw = PP._rows(js) if js else None
        if not raw or not isinstance(raw[0], dict):
            return {"series": series, "missing": True, "reason": "no rows", "period": None, "value": None, "units": None}
        r = raw[0]
        v = num(r.get("value"))
        if v is None or not r.get("period"):
            return {"series": series, "missing": True, "reason": "row incomplete", "period": r.get("period"), "value": None, "units": r.get("units")}
        return {"series": series, "missing": False, "reason": None, "period": r.get("period"), "value": v, "units": r.get("units")}

    return {"crude": one(*CRUDE), "gas": one(*GAS)}
