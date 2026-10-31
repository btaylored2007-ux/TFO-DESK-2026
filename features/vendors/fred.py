"""FRED observations for the regime layer.

History comes from veteran_desk.feeds.fred_rows, the same public CSV as fred_last.
No key. HY OAS is BAMLH0A0HYM2. VIX term structure is VIXCLS over VXVCLS.
"""
import os
from features.envelope import from_hit
from features.vendors.common import VendorError, aged

HY_OAS = "BAMLH0A0HYM2"
VIX = "VIXCLS"
VIX3M = "VXVCLS"
DOLLAR = "DTWEXBGS"
DGS10 = "DGS10"
DGS2 = "DGS2"
# Daily FRED prints skip weekends. Five calendar days is the stale cutoff.
MAX_AGE_DAYS = 5


def series(name, n, client_rows, gate, now):
    def fn():
        got = list(client_rows(name, n) or [])
        if not got:
            raise VendorError(name + " empty")
        return got, got[-1][0]

    hit = gate.call("%s:%s" % (name, int(n)), fn)
    doc = from_hit(hit, "fred")
    obs = hit.value[-1][0] if (not doc.get("missing") and hit.value) else None
    return aged(doc, obs, now, MAX_AGE_DAYS)


def default_rows(name, n):
    if os.environ.get("PYTEST_CURRENT_TEST"):
        raise VendorError("disabled under pytest")
    from veteran_desk import feeds as F
    return F.fred_rows(name, n)
