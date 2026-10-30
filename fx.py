#!/usr/bin/env python3
"""fx.py — quote-currency detection and USD translation for the desk's books.

Why this exists (2026-08-16): the value book sized and valued every holding at
its RAW LOCAL price and summed ten currencies as though all were dollars.
005930.KS quotes in KRW (5,000 KRW ~ US$3.60 was booked as $5,000); BARC.L
quotes in PENCE, not pounds (5,000 GBp ~ US$67 booked as $5,000); BNP.PA quotes
in EUR. The book's equity was a sum of incommensurable units.

THE RULE THIS MODULE ENFORCES: never silently degrade. If a quote currency
cannot be identified, or a rate for it cannot be obtained, this module RAISES —
it never falls back to "assume USD", and it never invents a rate. A book that
cannot value a name must say so loudly and refuse to size or value it.

*** .L IS PENCE (GBp), NOT POUNDS — a factor of 100. ***
Minor-unit quotes are modelled explicitly in MINOR_UNITS; the major/minor split
is the whole reason this is a table and not a dict of floats.

PAPER ONLY. Read-only market data (yfinance), no broker, no orders.
"""
import warnings

warnings.filterwarnings("ignore")


class FXUnavailable(RuntimeError):
    """No trustworthy rate/currency for a name. Callers must refuse, not guess."""


# Yahoo listing suffix -> the currency the quote is PRINTED IN (not the country's
# major unit). ".L" is GBp = pence deliberately; that distinction is the bug.
SUFFIX_CCY = {
    ".KS": "KRW", ".KQ": "KRW",          # Korea
    ".L": "GBp", ".IL": "USD",           # London: ordinary lines quote in PENCE
    ".PA": "EUR", ".AS": "EUR", ".BR": "EUR", ".DE": "EUR", ".F": "EUR",
    ".MC": "EUR", ".MI": "EUR", ".LS": "EUR", ".VI": "EUR", ".HE": "EUR",
    ".IR": "EUR", ".AT": "EUR",          # euro area
    ".T": "JPY",                          # Tokyo
    ".TO": "CAD", ".V": "CAD",            # Canada
    ".AX": "AUD", ".NZ": "NZD",
    ".SS": "CNY", ".SZ": "CNY", ".HK": "HKD", ".TW": "TWD", ".TWO": "TWD",
    ".KL": "MYR", ".SI": "SGD", ".BK": "THB", ".JK": "IDR",
    ".NS": "INR", ".BO": "INR",
    ".SW": "CHF", ".ST": "SEK", ".OL": "NOK", ".CO": "DKK", ".IC": "ISK",
    ".SA": "BRL", ".MX": "MXN", ".BA": "ARS", ".SN": "CLP",
    ".JO": "ZAc",                        # Johannesburg quotes in CENTS
    ".TA": "ILA",                        # Tel Aviv quotes in AGOROT
    ".IS": "TRY", ".WA": "PLN", ".PR": "CZK", ".BD": "HUF", ".ME": "RUB",
    ".CA": "EGP", ".SR": "SAR", ".QA": "QAR", ".AD": "AED", ".DU": "AED",
}

# Quote code -> (major currency, majors per quoted unit).
MINOR_UNITS = {
    "GBP": ("GBP", 1.0), "GBp": ("GBP", 0.01), "GBX": ("GBP", 0.01),
    "ILS": ("ILS", 1.0), "ILA": ("ILS", 0.01), "ILX": ("ILS", 0.01),
    "ZAR": ("ZAR", 1.0), "ZAc": ("ZAR", 0.01), "ZAX": ("ZAR", 0.01),
}

# Per-ticker escape hatch for listings whose suffix lies (dual lines etc.).
OVERRIDES = {}


def suffix_of(ticker):
    """'BARC.L' -> '.L'; None when the ticker carries no listing suffix."""
    t = (ticker or "").strip()
    if "." not in t:
        return None
    return "." + t.rsplit(".", 1)[1].upper()


_suffix = suffix_of   # internal alias


def major_and_scale(ccy):
    """'GBp' -> ('GBP', 0.01). Unknown codes are assumed already-major, 1:1."""
    if not ccy:
        raise FXUnavailable("no quote currency given")
    if ccy in MINOR_UNITS:
        return MINOR_UNITS[ccy]
    # case-sensitive minor codes only; anything else is its own major unit
    return ccy.upper(), 1.0


def quote_currency(ticker, info_ccy=None):
    """The currency a ticker's price is PRINTED IN, or None if not trustworthy.

    Order: explicit override, then the listing-suffix table (authoritative — it
    knows .L is pence, which some data feeds report as 'GBP'), then whatever the
    data provider says for suffixes we do not know. A provider currency that
    disagrees with the table on the MAJOR unit returns None: a conflict we cannot
    resolve is never resolved by picking a side.
    """
    t = (ticker or "").strip()
    if not t:
        return None
    if t in OVERRIDES:
        return OVERRIDES[t]

    sfx = _suffix(t)
    table = SUFFIX_CCY.get(sfx) if sfx else None

    if table:
        if info_ccy:
            try:
                if major_and_scale(info_ccy)[0] != major_and_scale(table)[0]:
                    return None          # feed vs table conflict -> refuse
            except FXUnavailable:
                pass
        return table

    if sfx:                               # a suffix we have never seen
        return info_ccy or None

    # No suffix on Yahoo means a US listing; the feed still gets a veto.
    return info_ccy or "USD"


def usd_per_quote_unit(ccy, rates):
    """USD value of ONE quoted unit (one pence, one won, one dollar).

    `rates` maps MAJOR currency -> USD per 1 major unit. Raises FXUnavailable
    rather than defaulting; a missing rate is a refusal, not a 1.0.
    """
    major, scale = major_and_scale(ccy)
    if major == "USD":
        return 1.0 * scale
    r = rates.get(major)
    if r is None:
        raise FXUnavailable("no USD rate for %s" % major)
    try:
        r = float(r)
    except (TypeError, ValueError):
        raise FXUnavailable("bad USD rate for %s: %r" % (major, r))
    if not (r > 0) or r != r or r in (float("inf"), float("-inf")):
        raise FXUnavailable("bad USD rate for %s: %r" % (major, r))
    return r * scale


def to_usd(price_local, ccy, rates):
    """Translate a local-currency price/value into USD. Raises if it cannot."""
    if price_local is None or price_local != price_local:
        raise FXUnavailable("no local price to convert")
    return float(price_local) * usd_per_quote_unit(ccy, rates)


def currencies_needed(tickers, info_lookup=None):
    """(ticker -> quote ccy, [tickers whose currency is unknown]).

    `info_lookup(ticker)` may return the provider's currency string; it is only
    consulted for suffixes the table does not cover, and as a veto elsewhere.
    """
    got, unknown = {}, []
    for t in tickers:
        info_ccy = None
        if info_lookup is not None:
            try:
                info_ccy = info_lookup(t)
            except Exception:
                info_ccy = None
        c = quote_currency(t, info_ccy)
        if c:
            got[t] = c
        else:
            unknown.append(t)
    return got, unknown


def yf_currency(ticker):
    """Provider-reported currency for a ticker, or None. Never raises."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).get_info() or {}
        c = info.get("currency")
        return c.strip() if isinstance(c, str) and c.strip() else None
    except Exception:
        return None


def fetch_usd_rates(currencies, download=None):
    """MAJOR currency -> USD per unit, plus the as-of date.

    Currencies that cannot be priced are simply ABSENT from the result — callers
    must treat absence as a refusal (see usd_per_quote_unit). Nothing here ever
    substitutes a placeholder rate.
    """
    majors = set()
    for c in currencies:
        try:
            majors.add(major_and_scale(c)[0])
        except FXUnavailable:
            continue
    majors.discard("USD")
    rates = {"USD": 1.0}
    if not majors:
        return rates, None

    if download is None:
        download = _yf_download_closes

    direct = {"%sUSD=X" % m: m for m in sorted(majors)}
    inverse = {"USD%s=X" % m: m for m in sorted(majors)}
    closes, asof = download(list(direct) + list(inverse))

    for sym, m in direct.items():
        v = closes.get(sym)
        if v is not None and v == v and v > 0:
            rates[m] = float(v)
    for sym, m in inverse.items():
        if m in rates:
            continue
        v = closes.get(sym)
        if v is not None and v == v and v > 0:
            rates[m] = 1.0 / float(v)
    # yfinance FX often empty overnight — fill missing majors from live public
    # venues / last_marks. Never invent; only use an explicit fetched mid or cache.
    missing = [m for m in majors if m not in rates]
    if missing:
        filled, fasof = _fallback_usd_rates(missing)
        rates.update(filled)
        if filled and asof is None:
            asof = fasof
    return rates, asof


def _fallback_usd_rates(majors):
    """Public mid / desk last_marks for majors yfinance failed to price."""
    import json
    import time
    import urllib.request
    from pathlib import Path
    out = {}
    asof = None
    kraken_pair = {"AUD": "AUDUSD", "EUR": "EURUSD", "GBP": "GBPUSD"}
    need_k = [m for m in majors if m in kraken_pair]
    if need_k:
        pairs = ",".join(kraken_pair[m] for m in need_k)
        try:
            with urllib.request.urlopen(
                "https://api.kraken.com/0/public/Ticker?pair=" + pairs, timeout=10
            ) as r:
                payload = json.load(r)
            result = payload.get("result") or {}
            for m in need_k:
                code = kraken_pair[m]
                row = result.get(code)
                if row is None:
                    for k, v in result.items():
                        if code in k or k.endswith(code):
                            row = v
                            break
                if not row:
                    continue
                bid = float(row["b"][0])
                ask = float(row["a"][0])
                mid = (bid + ask) / 2.0
                if mid > 0:
                    out[m] = mid
            asof = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())
        except Exception:
            pass
    still = [m for m in majors if m not in out]
    if still:
        for cand in (Path(__file__).resolve().parent / "reports" / "last_marks.json",
                     Path("reports/last_marks.json")):
            try:
                marks = json.loads(Path(cand).read_text())
            except Exception:
                continue
            for m in list(still):
                for key in ("%sUSD=X" % m, "%s/USD" % m, "%sUSD" % m):
                    v = marks.get(key)
                    try:
                        v = float(v)
                    except (TypeError, ValueError):
                        continue
                    if v > 0:
                        out[m] = v
                        break
            break
    return out, asof


def _yf_download_closes(symbols):
    import yfinance as yf
    try:
        raw = yf.download(symbols, period="10d", interval="1d", auto_adjust=True,
                          progress=False, threads=True)["Close"]
    except Exception:
        return {}, None
    if not hasattr(raw, "iloc") or len(raw) == 0:
        return {}, None
    last = raw.ffill().iloc[-1]
    out = {}
    for s in symbols:
        try:
            v = float(last[s])
        except Exception:
            continue
        if v == v:
            out[s] = v
    try:
        asof = raw.index[-1].strftime("%Y-%m-%d")
    except Exception:
        asof = None
    return out, asof
