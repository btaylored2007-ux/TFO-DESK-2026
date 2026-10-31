"""Default clients. Each one delegates to a module the desk already runs.

Keys are read inside those modules from ~/.tfo_secrets/<name>.env at call time.
This file does not print them. Under pytest the defaults refuse the network;
tests pass their own clients.
"""
import os
from features.paths import cache_dir
from features.transport import LIMITS, Gate
from features.vendors.common import VendorError
from features.vendors.eia import default_client as default_eia
from features.vendors.fred import default_rows as default_fred_rows


def _pytest_off(name):
    if os.environ.get("PYTEST_CURRENT_TEST"):
        raise VendorError(name + " disabled under pytest")


def default_fmp(path, **q):
    _pytest_off("fmp")
    import paid_feeds as PF
    return PF.fmp(path, **q)


def default_uw(path):
    _pytest_off("unusual_whales")
    from veteran_desk import thf_paid_pack as PP
    key = PP._key("unusual_whales", "UNUSUAL_WHALES_API_KEY")
    if not key:
        raise VendorError("key missing")
    js = PP._get("uw_features", "https://api.unusualwhales.com" + path, {"Authorization": "Bearer " + key})
    if js is None:
        raise VendorError("no response")
    return js


def default_massive(path, params):
    _pytest_off("massive")
    from veteran_desk import massive as M
    js = M._call("features", path, params or {})
    if js is None:
        raise VendorError("no response")
    return js


def default_eodhd(path, params):
    _pytest_off("eodhd")
    from veteran_desk import data_layer as DL
    js = DL.call("features", path, params or {})
    if js is None:
        raise VendorError("no response")
    return js


def default_eodhd_symbol(sym):
    from veteran_desk import data_layer as DL
    return DL.eodhd_symbol(sym)


def default_coinalyze():
    _pytest_off("coinalyze")
    from veteran_desk import thf_paid_pack as PP
    js = PP.coinalyze_derivs()
    if not js:
        raise VendorError("no response")
    return js


def default_news_regime():
    _pytest_off("news_regime")
    from veteran_desk import news_desk as N
    return N.regime()


def default_breadth_bars():
    """Hand-list equities from pit, when the pit database is present. Read only."""
    _pytest_off("breadth")
    from veteran_desk import pit as P
    if not os.path.exists(P.DB):
        raise VendorError("pit db absent")
    skip = {"XLE", "XLP", "XLU", "XLF", "XLV", "QQQ", "SMH", "XLK"}
    syms = []
    for group in ("energy", "essentials", "banks", "healthcare", "tech"):
        for s in P.UNIVERSE.get(group, []):
            if s in skip or "." in s or s.startswith("^") or "=" in s:
                continue
            syms.append(s)
    import datetime as dt, sqlite3
    c = sqlite3.connect("file:%s?mode=ro" % P.DB, uri=True)
    now = dt.datetime.now(dt.timezone.utc)
    try:
        return {s: P.bars_asof(c, s, now, 80) for s in syms}
    finally:
        c.close()


class Clients(object):
    def __init__(self, base=None, clock=None, sleep=None, **kw):
        root = cache_dir(base)
        self.gates = {name: Gate(name, root, clock=clock, sleep=sleep) for name in LIMITS}
        self.fmp = kw.get("fmp", default_fmp)
        self.uw = kw.get("uw", default_uw)
        self.massive = kw.get("massive", default_massive)
        self.eodhd = kw.get("eodhd", default_eodhd)
        self.eodhd_symbol = kw.get("eodhd_symbol", default_eodhd_symbol)
        self.coinalyze = kw.get("coinalyze", default_coinalyze)
        self.eia = kw.get("eia", default_eia)
        self.fred_rows = kw.get("fred_rows", default_fred_rows)
        self.news_regime = kw.get("news_regime", default_news_regime)
        self.breadth_bars = kw.get("breadth_bars", default_breadth_bars)
