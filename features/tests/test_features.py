"""Mocked vendors for the advisory feature store, plus a full-vendor failure."""
import datetime as dt, json
from zoneinfo import ZoneInfo
import pytest
from features.clients import Clients
from features.envelope import field
from features.scrub import scrub
from features.store import run
from features.transport import Gate
from features.vendors import coinalyze, fmp, massive
from features.vendors.common import VendorError
from features.vendors.eodhd import _slim
from features.regime import breadth_from_bars, build_regime

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 29, 15, 0, tzinfo=UTC)  # Thursday, 11:00 America/New_York
NY = ZoneInfo("America/New_York")


class Clock(object):
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _bars(n=50, start=100.0):
    rows = []
    day = dt.date(2026, 8, 1)
    for i in range(n):
        rows.append((str(day), 1, 1, 1, start + i, 1, "k"))
        day += dt.timedelta(days=1)
    return rows


def _daily_aggs():
    out = []
    day = dt.datetime(2026, 9, 1, tzinfo=UTC)
    for i in range(21):
        vol = 300 if i == 20 else 100
        o = 110 if i == 20 else 100
        c = 112 if i == 20 else 100
        out.append({"t": int(day.timestamp() * 1000), "o": o, "h": o + 1, "l": o - 1, "c": c, "v": vol})
        day += dt.timedelta(days=1)
    return out


def _minutes():
    pre = dt.datetime(2026, 10, 29, 12, 0, tzinfo=UTC)  # 08:00 EDT
    reg = dt.datetime(2026, 10, 29, 14, 0, tzinfo=UTC)  # 10:00 EDT
    return [
        {"t": int(pre.timestamp() * 1000), "o": 1, "h": 1, "l": 1, "c": 1, "v": 1000},
        {"t": int(reg.timestamp() * 1000), "o": 1, "h": 1, "l": 1, "c": 1, "v": 5000},
    ]


def _fred(vix=16.0, vix3m=18.0, hy=3.2, dgs10=4.2, dgs2=3.8):
    end = dt.date(2026, 10, 28)

    def rows(series, n):
        if series == "DTWEXBGS":
            return [((end - dt.timedelta(days=19 - i)).isoformat(), 100.0 + i * 0.01) for i in range(n)]
        value = {
            "VIXCLS": vix, "VXVCLS": vix3m, "BAMLH0A0HYM2": hy, "DGS10": dgs10, "DGS2": dgs2,
        }[series]
        return [(end.isoformat(), value)]

    return rows


def _clients(tmp, clock, fmp_client, **kw):
    def sleep(s):
        clock.t += s

    base = {
        "fmp": fmp_client,
        "uw": kw.get("uw", _uw),
        "massive": kw.get("massive", _massive),
        "eodhd": kw.get("eodhd", _eodhd),
        "eodhd_symbol": kw.get("eodhd_symbol", lambda s: "BHP.AU" if str(s).upper().endswith(".AX") else None),
        "coinalyze": kw.get("coinalyze", _coinalyze),
        "eia": kw.get("eia", _eia),
        "fred_rows": kw.get("fred_rows", _fred()),
        "news_regime": kw.get("news_regime", lambda: ("calm", {"vix": 16})),
        "breadth_bars": kw.get("breadth_bars", lambda: {"AAA": _bars(), "BBB": _bars()}),
    }
    base.update({k: v for k, v in kw.items() if k not in base or k in ("fmp",)})
    return Clients(base=str(tmp), clock=clock, sleep=sleep, **{k: base[k] for k in (
        "fmp", "uw", "massive", "eodhd", "eodhd_symbol", "coinalyze", "eia", "fred_rows", "news_regime", "breadth_bars")})


def _uw(path):
    if "flow-alerts" in path:
        return {"data": [{"ticker": "AAPL", "type": "call", "total_premium": 1000, "strike": 200,
                           "expiry": "2026-11-20", "alert_rule": "sweep", "underlying_price": 190,
                           "created_at": "2026-10-29T14:00:00Z"}]}
    if "options-volume" in path:
        return {"data": [{"date": "2026-10-29", "call_volume": 100, "put_volume": 40}]}
    if "darkpool" in path:
        return {"data": [{"size": 5000, "price": 190, "premium": 950000, "executed_at": "2026-10-29T14:00:00Z"}]}
    raise AssertionError(path)


def _massive(path, params):
    if "/range/1/day/" in path:
        return {"results": _daily_aggs()}
    if "/range/1/minute/" in path:
        return {"results": _minutes()}
    raise AssertionError(path)


def _eodhd(path, params):
    assert "BHP.AU" in path
    return {"General": {"Name": "BHP", "Exchange": "AU", "CurrencyCode": "AUD", "Sector": "Basic Materials", "UpdatedAt": "2026-10-28"},
            "Highlights": {"MarketCapitalization": 100.0, "PERatio": 12.0, "EarningsShare": 3.0},
            "Valuation": {"TrailingPE": 12.0, "ForwardPE": 11.0}}


def _coinalyze():
    return {"rows": {"BTC": {"funding_rate": 0.01, "oi_usd": 1e9, "liq_24h_long_usd": 5, "liq_24h_short_usd": 7}}}


def _eia():
    return {
        "crude": {"series": "WCESTUS1", "missing": False, "period": "2026-10-23", "value": 400000, "units": "MBBL", "reason": None},
        "gas": {"series": "NW2_EPG0_SWO_R48_BCF", "missing": False, "period": "2026-10-23", "value": 3500, "units": "BCF", "reason": None},
    }


def _fmp_factory(estimates):
    def fmp_client(path, **q):
        if path == "analyst-estimates":
            return [{"date": "2026-12-31", "estimatedEpsAvg": estimates[0]}, {"date": "2027-03-31", "estimatedEpsAvg": 2.2}]
        if path == "earnings":
            return [{"date": "2026-10-28", "epsActual": 1.2, "epsEstimated": 1.0}]
        if path == "grades":
            return [{"date": "2026-10-01", "gradingCompany": "GS", "action": "upgrade", "previousGrade": "Hold", "newGrade": "Buy"}]
        if path == "insider-trading/search":
            return [{"filingDate": "2026-10-20", "transactionDate": "2026-10-18", "reportingName": "Ada",
                     "transactionType": "P-Purchase", "securitiesTransacted": 10, "price": 100}]
        if path == "earnings-calendar":
            return [
                {"symbol": "AAPL", "date": "2026-10-31", "time": "amc", "epsEstimated": 1.5},
                {"symbol": "MSFT", "date": "2026-11-20", "time": "bmo", "epsEstimated": 3.0},
            ]
        if path.startswith("historical-"):
            return [{"date": "2026-09-01", "symbol": "AAPL", "removedTicker": "XYZ", "reason": "added"}]
        raise AssertionError(path)
    return fmp_client


def test_envelope_keeps_zero_and_false_and_flags_gaps():
    assert field(0, "t", source="s")["value"] == 0
    assert field(False, "t", source="s")["value"] is False
    gap = field(None, "t", source="s", missing=True, reason="down")
    assert gap["value"] is None and gap["freshness"] == "stale" and gap["missing"] is True
    assert "0" not in json.dumps(gap["value"])


def test_revision_uses_the_same_fiscal_date_only():
    prior = {"by_fiscal_date": {"2026-12-31": 1.8, "2027-03-31": 9.0}}
    cur = [{"date": "2026-12-31", "estimatedEpsAvg": 2.0}, {"date": "2027-03-31", "estimatedEpsAvg": 2.2}]
    got = fmp._revision(cur, prior)
    assert got["fiscal_date"] == "2026-12-31"
    assert got["eps_revision"] == pytest.approx(0.2)
    first = fmp._revision(cur, None)
    assert first["eps_revision"] is None and first["eps_revision_missing"] is True
    same = fmp._revision(cur, {"by_fiscal_date": {"2026-12-31": 2.0}})
    assert same["eps_revision"] == 0.0 and same["eps_revision_missing"] is False


def test_short_volume_history_is_not_averaged():
    bars = _daily_aggs()[:10]
    assert massive._unusual(bars) is None
    assert massive._gap(bars[:1]) is None


def test_put_call_ratio_not_invented_when_call_volume_is_zero(tmp_path):
    clock = Clock()

    def uw(path):
        if "options-volume" in path:
            return {"data": [{"call_volume": 0, "put_volume": 10}]}
        if "flow-alerts" in path or "darkpool" in path:
            return {"data": []}
        raise AssertionError(path)

    clients = _clients(tmp_path, clock, _fmp_factory([2.0]), uw=uw)
    doc = __import__("features.vendors.unusual_whales", fromlist=["fetch"]).fetch("AAPL", NOW, clients.uw, clients.gates["unusual_whales"])
    ratio = doc["put_call_ratio"]["value"]
    assert ratio["put_call_ratio"] is None and ratio["put_call_ratio_missing"] is True
    assert doc["options_flow"]["value"] == {"rows": []}


def test_zero_funding_kept_and_absent_open_interest_missing(tmp_path):
    clock = Clock()

    def cz():
        return {"rows": {"BTC": {"funding_rate": 0.0, "liq_24h_long_usd": 5}}}

    clients = _clients(tmp_path, clock, _fmp_factory([2.0]), coinalyze=cz)
    doc = coinalyze.fetch("KRAKEN:XBTUSD", NOW, clients.coinalyze, clients.gates["coinalyze"])
    assert doc["funding"]["value"] == 0.0 and doc["funding"]["missing"] is False
    assert doc["open_interest"]["missing"] is True and doc["open_interest"]["value"] is None
    assert doc["liquidations"]["value"]["short_missing"] is True
    assert doc["liquidations"]["value"]["liq_24h_short_usd"] is None


def test_eodhd_gaps_stay_null():
    slim = _slim({"General": {"Name": "BHP"}, "Highlights": {}})
    assert slim["pe"] is None and slim["market_cap"] is None
    assert "pe" in slim["gaps"] and "market_cap" in slim["gaps"]


def test_breadth_skips_short_history():
    stats = breadth_from_bars({"SHORT": _bars(10), "LONG": _bars(50)})
    assert stats["n"] == 1 and stats["pct"] == 1.0
    assert breadth_from_bars({"SHORT": _bars(10)}) is None


def test_store_writes_fresh_fields(tmp_path):
    clock = Clock()
    estimates = [2.0]
    clients = _clients(tmp_path, clock, _fmp_factory(estimates))
    out = run(["AAPL", "MSFT", "BHP.AX", "KRAKEN:XBTUSD"], base=str(tmp_path), clients=clients, now=NOW)
    assert out["errors"] == []
    aapl = json.loads(open(out["tickers"][0]).read())
    assert aapl["advisory"] is True and aapl["gates"] is False and aapl["rails"] == "unchanged"
    rev = aapl["fields"]["fmp"]["estimate_revisions"]
    assert rev["freshness"] == "fresh" and rev["value"]["eps_revision_missing"] is True
    surprise = aapl["fields"]["fmp"]["earnings_surprises"]["value"]
    assert surprise["surprise_pct"] == pytest.approx(20.0)
    assert aapl["fields"]["fmp"]["analyst_grades"]["value"]["latest"]["action"] == "upgrade"
    assert aapl["fields"]["fmp"]["insider_trades"]["value"]["rows"][0]["transaction_type"] == "P-Purchase"
    assert aapl["fields"]["fmp"]["index_changes"]["value"]["rows"]
    assert aapl["fields"]["earnings_within_2d"]["value"] is True
    assert aapl["fields"]["earnings_within_2d"]["freshness"] == "fresh"
    flow = aapl["fields"]["unusual_whales"]["options_flow"]["value"]["rows"]
    assert flow and flow[0]["alert_rule"] == "sweep"
    assert aapl["fields"]["unusual_whales"]["put_call_ratio"]["value"]["put_call_ratio"] == pytest.approx(0.4)
    assert aapl["fields"]["unusual_whales"]["dark_pool"]["value"]["rows"][0]["size"] == 5000
    vol = aapl["fields"]["massive"]["unusual_volume"]["value"]
    assert vol["avg20"] == pytest.approx(100.0) and vol["ratio"] == pytest.approx(3.0) and vol["ratio_missing"] is False
    assert aapl["fields"]["massive"]["gap"]["value"]["gap_pct"] == pytest.approx(10.0)
    pre = aapl["fields"]["massive"]["premarket_volume"]["value"]
    assert pre["volume"] == 1000 and pre["bars"] == 1 and pre["session_date"] == "2026-10-29"
    assert aapl["fields"]["eodhd"]["fundamentals"]["applicable"] is False
    assert aapl["fields"]["coinalyze"]["funding"]["applicable"] is False
    msft = json.loads(open(out["tickers"][1]).read())
    assert msft["fields"]["earnings_within_2d"]["value"] is False
    bhp = json.loads(open(out["tickers"][2]).read())
    fund = bhp["fields"]["eodhd"]["fundamentals"]
    assert fund["freshness"] == "fresh" and fund["value"]["name"] == "BHP" and fund["value"]["currency"] == "AUD"
    assert bhp["fields"]["massive"]["unusual_volume"]["applicable"] is False
    btc = json.loads(open(out["tickers"][3]).read())
    assert btc["fields"]["coinalyze"]["funding"]["value"] == pytest.approx(0.01)
    assert btc["fields"]["coinalyze"]["open_interest"]["value"] == pytest.approx(1e9)
    assert btc["fields"]["coinalyze"]["liquidations"]["value"]["liq_24h_long_usd"] == 5
    assert "KRAKEN_XBTUSD.json" in out["tickers"][3]
    reg = json.loads(open(out["regime"]).read())
    assert reg["fields"]["tag"]["value"] == "risk_on" and reg["fields"]["tag"]["missing"] is False
    assert reg["fields"]["vix_term"]["value"]["structure"] == "contango"
    assert reg["fields"]["hy_oas"]["value"]["hy_oas_pct"] == pytest.approx(3.2)
    assert reg["fields"]["dollar"]["value"]["change_missing"] is False
    assert reg["fields"]["rates"]["value"]["curve_10y2y"] == pytest.approx(0.4)
    assert reg["fields"]["eia_inventories"]["value"]["crude"]["value"] == 400000
    assert reg["fields"]["eia_inventories"]["value"]["gas"]["series"] == "NW2_EPG0_SWO_R48_BCF"
    flags = {row["ticker"]: row["value"] for row in reg["earnings_within_2d"]}
    assert flags["AAPL"] is True and flags["MSFT"] is False
    # a later print of the same fiscal date is a revision; a first print is not zero
    clock.t += 1000
    estimates[0] = 2.5
    again = run(["AAPL"], base=str(tmp_path), clients=clients, now=NOW)
    rev2 = json.loads(open(again["tickers"][0]).read())["fields"]["fmp"]["estimate_revisions"]["value"]
    assert rev2["eps_revision"] == pytest.approx(0.5) and rev2["eps_revision_missing"] is False


def test_regime_risk_off_and_missing_tag(tmp_path):
    clock = Clock()
    clients = _clients(tmp_path, clock, _fmp_factory([2.0]), news_regime=lambda: ("risk-off", {"vix": 26}))
    reg = build_regime(NOW, clients)
    assert reg["fields"]["tag"]["value"] == "risk_off"
    assert "news_risk-off" in reg["fields"]["tag"]["reason"]
    clock.t += 10
    back = _clients(tmp_path / "back", clock, _fmp_factory([2.0]), fred_rows=_fred(vix=30, vix3m=20),
                    news_regime=lambda: ("calm", {}))
    assert build_regime(NOW, back)["fields"]["tag"]["value"] == "risk_off"
    assert "vix_backwardation" in build_regime(NOW, back)["fields"]["tag"]["reason"]

    def down(series, n):
        raise VendorError("fred down")

    clock.t += 10
    blind = _clients(tmp_path / "blind", clock, _fmp_factory([2.0]), fred_rows=down)
    tag = build_regime(NOW, blind)["fields"]["tag"]
    assert tag["value"] is None and tag["missing"] is True and tag["freshness"] == "stale"


def test_transport_cache_backoff_and_scrub(tmp_path):
    clock = Clock()
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        clock.t += s

    gate = Gate("fred", str(tmp_path / "cache"), clock=clock, sleep=sleep)
    calls = {"n": 0}

    def ok():
        calls["n"] += 1
        return {"v": 1}, "2026-10-28"

    assert gate.call("k", ok).fresh and calls["n"] == 1
    assert gate.call("k", ok).fresh and calls["n"] == 1  # inside TTL
    gate.call("other", ok)
    assert sleeps and sleeps[0] == pytest.approx(0.5)  # second key waits out the vendor spacing
    clock.t += 30000

    def bad():
        calls["n"] += 1
        raise RuntimeError("blow up apikey=SUPERSECRET123")

    stale = gate.call("k", bad)
    assert calls["n"] == 3 and stale.fresh is False and stale.missing is False and stale.value == {"v": 1}
    assert "SUPERSECRET123" not in (stale.reason or "")
    gate.call("k", bad)
    assert calls["n"] == 3  # backoff, no second hit
    assert "SUPERSECRET" not in scrub("token=SUPERSECRET&x=1")


def test_chaos_every_vendor_fails_and_store_still_writes(tmp_path):
    clock = Clock()

    def boom(*a, **k):
        raise RuntimeError("vendor down apikey=SUPERSECRET123")

    clients = _clients(
        tmp_path, clock, boom,
        uw=boom, massive=boom, eodhd=boom, coinalyze=boom, eia=boom, fred_rows=boom,
        news_regime=boom, breadth_bars=boom,
        eodhd_symbol=lambda s: "BHP.AU" if str(s).upper().endswith(".AX") else "AAPL.US",
    )
    out = run(["AAPL", "BHP.AX", "KRAKEN:XBTUSD"], base=str(tmp_path), clients=clients, now=NOW)
    assert out["errors"] == []
    blob = ""
    for path in out["tickers"] + [out["regime"]]:
        text = open(path).read()
        blob += text
        json.loads(text)
    assert "SUPERSECRET123" not in blob
    aapl = json.loads(open(out["tickers"][0]).read())
    for group, names in (
        ("fmp", ("estimate_revisions", "earnings_surprises", "analyst_grades", "insider_trades", "index_changes", "earnings_calendar")),
        ("unusual_whales", ("options_flow", "put_call_ratio", "dark_pool")),
        ("massive", ("unusual_volume", "gap", "premarket_volume")),
    ):
        for name in names:
            doc = aapl["fields"][group][name]
            assert doc["applicable"] is True, name
            assert doc["missing"] is True and doc["freshness"] == "stale" and doc["value"] is None, name
    assert aapl["fields"]["earnings_within_2d"]["missing"] is True
    assert aapl["fields"]["earnings_within_2d"]["value"] is not False
    bhp = json.loads(open(out["tickers"][1]).read())
    assert bhp["fields"]["eodhd"]["fundamentals"]["missing"] is True
    assert bhp["fields"]["eodhd"]["fundamentals"]["freshness"] == "stale"
    btc = json.loads(open(out["tickers"][2]).read())
    for name in ("funding", "open_interest", "liquidations"):
        assert btc["fields"]["coinalyze"][name]["missing"] is True
        assert btc["fields"]["coinalyze"][name]["freshness"] == "stale"
    reg = json.loads(open(out["regime"]).read())
    for name in ("tag", "news_regime", "vix_term", "breadth", "hy_oas", "dollar", "rates", "eia_inventories"):
        doc = reg["fields"][name]
        assert doc["missing"] is True and doc["freshness"] == "stale" and doc["value"] is None, name
    assert reg["advisory"] is True and reg["gates"] is False


def test_package_does_not_call_coinglass_or_rails():
    import pathlib
    files = [p for p in pathlib.Path("features").rglob("*.py") if "tests" not in p.parts]
    text = "\n".join(p.read_text() for p in files)
    assert "coinglass.com" not in text.lower()
    assert "open-api" not in text.lower()
    assert "gate_v2" not in text
    assert "CoinGlass is retired" in text
