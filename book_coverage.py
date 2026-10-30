#!/usr/bin/env python3
"""book_coverage.py — the firm's ONE list of books, and what each is worth.

WHY THIS EXISTS (built 2026-08-27, funder-directed)
---------------------------------------------------
`risk_engine.gather_positions()` read exactly FOUR ledgers — growth_book,
value_book, kraken_floor, replica_floor — and printed the result under the
heading "FIRM-WIDE RISK REPORT". Everything else the firm actually holds (every
tournament sleeve, both remaining floors, the carry books, the 24h punt, the
OANDA book) contributed ZERO to the firm's only risk number. A risk report that
silently omits most of the firm is worse than no risk report, because it is
believed.

This module is the single enumeration of every book with live capital. Both
`risk_engine.py` (exposure / VaR) and `firm_risk.py` (firm equity, peak,
drawdown) read it, so the two can never disagree about what the firm holds.

VALUATION RULES, STATED
-----------------------
1. EQUITY comes from each book's own marked equity where the book computes one
   (every sleeve, both weight books, the desk floors via cash+cost+unrealized).
   Nothing here re-derives a book's P&L; a book is the authority on its own
   equity, and this module only adds it up.
2. POSITION VALUE is `units * mark` wherever a mark exists. Where a book stores
   no mark, the entry cost is used and the position is TAGGED `fallback_basis`
   and counted in `cost_basis_usd`, so the size of the assumption is a published
   number rather than a hidden one. (`risk_engine` re-marks these from the price
   panel it already downloads; `firm_risk` does not need to, because it works in
   book equity.)
3. FX: books quoted in AUD (carry paper desk, CoinSpot floor, OANDA) are
   translated with `fx.py`. If a rate cannot be obtained the book is reported
   UNREADABLE — never silently taken as USD. A mis-scaled number is a false
   certainty; an unmeasured one is a known unknown.
4. FAILURE IS LOUD. Any book that cannot be read or valued is printed, listed in
   `errors`, and persisted by `firm_risk.py` to reports/firm_risk_errors.json.
   Nothing is ever silently dropped to zero.

PAPER ONLY. This module opens files read-only and writes nothing. It cannot
trade, size, close or alter any position.
"""
import glob
import hashlib
import json
import calendar
import math
import os
import time

from kraken_futures_risk_prices import VERIFIED_CONTRACTS
import binance_risk_prices

BASE = os.path.dirname(os.path.abspath(__file__))
KRAKEN_DESK = "/Users/test/Applications/KrakenQuantDesk"
OANDA_DESK = "/Users/test/Downloads/oanda-desk"

KRK = "KRK:"                        # pseudo-ticker: priced from Kraken public OHLC

# Floor/sleeve symbol -> yfinance ticker. Anything absent falls back to KRK:BASE.
CRYPTO_MAP = {
    "XLM/USD": "XLM-USD", "ADA/USD": "ADA-USD", "BNB/USD": "BNB-USD",
    "AAVE/USD": "AAVE-USD", "XBT/USD": "BTC-USD", "ETH/USD": "ETH-USD",
    "SOL/USD": "SOL-USD", "XRP/USD": "XRP-USD", "LTC/USD": "LTC-USD",
    "DOGE/USD": "DOGE-USD", "DOT/USD": "DOT-USD", "TRX/USD": "TRX-USD",
}
# CoinSpot's lowercase coin codes and the cross-asset floor's market keys.
COINSPOT_MAP = {"btc": "BTC-USD", "eth": "ETH-USD", "sol": "SOL-USD",
                "xrp": "XRP-USD", "ltc": "LTC-USD", "ada": "ADA-USD",
                "doge": "DOGE-USD", "usdt": "USDT-USD"}
CROSS_ASSET_MAP = {"aud": "AUDUSD=X", "gbp": "GBPUSD=X", "eur": "EURUSD=X",
                   "gold": "GC=F", "crude": "CL=F"}
CARRY_MAP = {"BTC": "BTC-USD", "ETH": "ETH-USD", "XMR": "XMR-USD",
             "HYPE": KRK + "HYPE"}


def yf_ticker(sym):
    """Best price series for a floor/sleeve symbol, or None if unpriceable."""
    if not sym:
        return None
    if sym in CRYPTO_MAP:
        return CRYPTO_MAP[sym]
    s = sym.strip()
    if s in VERIFIED_CONTRACTS:
        # Preserve exact perpetual identity; never route these to spot xStocks.
        return KRK + s
    if s.startswith("PF_") and s.endswith("USD"):        # Kraken perp
        return KRK + s[3:-3].upper()
    if "/" in s:                                          # SOME/USD spot pair
        return KRK + s.split("/")[0].strip().upper()
    return s                                              # already a yf ticker


def jf(path, fb=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return fb


def _usd_rates(currencies):
    """{ccy: usd_per_unit}. Raises nothing; missing rates become None (UNREADABLE)."""
    need = {c for c in currencies if c and c != "USD"}
    out = {"USD": 1.0}
    if not need:
        return out, None
    try:
        import fx as _fx
        rates, _ = _fx.fetch_usd_rates(need)
        for c in need:
            try:
                out[c] = _fx.usd_per_quote_unit(c, rates)
            except Exception:                             # noqa: BLE001
                out[c] = None
        return out, None
    except Exception as e:                                # noqa: BLE001 - reported
        for c in need:
            out[c] = None
        return out, "%s: %s" % (type(e).__name__, e)


def _book(key, label, kind, path, ccy="USD"):
    return {"key": key, "label": label, "kind": kind, "path": path, "ccy": ccy,
            "cash_usd": 0.0, "equity_usd": None, "equity_basis": "", "positions": [],
            "warnings": [], "error": None, "stale": False, "asof": None,
            "start_usd": None, "book_peak_usd": None}


def _pos(book, sym, ticker, units, fallback_usd, fallback_basis, mark_fx_usd=1.0,
         side="long"):
    return {"book": book, "sym": sym, "label": "%s:%s" % (book, sym),
            "ticker": ticker, "units": float(units or 0.0),
            "fallback_usd": float(fallback_usd or 0.0),
            "fallback_basis": fallback_basis,
            "mark_fx_usd": mark_fx_usd,
            "sign": -1.0 if side == "short" else 1.0, "side": side}


# ---------------------------------------------------------------------------
# Loaders, one per ledger SHAPE (not one per book) so a new sleeve needs no code
# ---------------------------------------------------------------------------
def _load_weights_book(key, label, kind, path, ccy="USD"):
    """cash / holdings / entry_px / equity — growth, value, every sleeve."""
    b = _book(key, label, kind, path, ccy)
    d = jf(path)
    if d is None:
        b["error"] = "unreadable or missing ledger"
        return b
    b["asof"] = d.get("updated") or d.get("last_valued_at")
    b["cash_usd"] = float(d.get("cash") or 0.0)
    eq = d.get("equity")
    if isinstance(eq, (int, float)) and not isinstance(eq, bool) and math.isfinite(eq):
        b["equity_usd"] = float(eq)
        b["equity_basis"] = "book's own marked equity"
    else:
        b["error"] = "book publishes no finite numeric equity: %s" % (
            d.get("equity_status") or "no valuation status recorded")
        # A refused/stale quote deliberately produces equity=None and null
        # mark/FX entries. Preserve that UNREADABLE state; do not crash the
        # entire registry, use an old equity as current, or assume missing FX=1.
        return b
    b["start_usd"] = d.get("start") if isinstance(d.get("start"), (int, float)) else None
    for k in ("peak", "peak_equity"):
        if isinstance(d.get(k), (int, float)):
            b["book_peak_usd"] = float(d[k])
            break
    if d.get("frozen"):
        b["warnings"].append("book reports frozen=true (%s)"
                             % (d.get("frozen_reason") or "no reason recorded"))

    entry = d.get("entry_px") or {}
    mark_usd = d.get("mark_px_usd") or {}
    fxpu = d.get("fx_usd_per_unit") or {}
    for t, u in (d.get("holdings") or {}).items():
        u = float(u or 0.0)
        tk = yf_ticker(t)
        # mark_fx_usd is the LOCAL-PRICE -> USD multiplier for this ticker. It is
        # a property of the listing, not of which basis we happened to use, so it
        # is set the same way in both branches — a downstream re-mark reads the
        # ticker's LOCAL close (005930.KS quotes in KRW, BARC.L in pence) and
        # would be off by the FX factor if this were 1.0 on the marked branch.
        raw_fx = fxpu.get(t, 1.0)
        quote_ccy = (d.get("holding_ccy") or {}).get(t, ccy)
        if ((t not in fxpu and quote_ccy != "USD")
                or not isinstance(raw_fx, (int, float))
                or isinstance(raw_fx, bool) or not math.isfinite(raw_fx) or raw_fx <= 0):
            b["error"] = "%s has no usable local-quote to USD rate — book UNREADABLE" % t
            return b
        mfx = float(raw_fx)
        mk = mark_usd.get(t)
        if isinstance(mk, (int, float)) and not isinstance(mk, bool) and math.isfinite(mk) and mk > 0:
            val, basis = u * float(mk), "mark"
        else:
            cost = entry.get(t)
            if not isinstance(cost, (int, float)) or isinstance(cost, bool) or not math.isfinite(cost) or cost <= 0:
                b["error"] = "%s has neither a usable mark nor entry cost — book UNREADABLE" % t
                return b
            val = u * float(cost) * mfx
            basis = "entry cost (book stores no usable mark)"
            if t in mark_usd:
                b["warnings"].append("%s: no usable USD mark; exposure uses declared entry-cost fallback" % t)
        b["positions"].append(_pos(key, t, tk, u, val, basis, mfx))
    return b


def _load_desk_floor(key, label, kind, path, ccy="USD", symmap=None):
    """desks -> positions with side/units/cost and (where marked) `mark`."""
    b = _book(key, label, kind, path, ccy)
    d = jf(path)
    if d is None:
        b["error"] = "unreadable or missing ledger"
        return b
    b["asof"] = d.get("updated") or d.get("updated_str")
    rate, fxerr = _usd_rates([ccy])
    f = rate.get(ccy)
    if f is None:
        b["error"] = "no USD rate for %s (%s) — book UNREADABLE, never assumed USD" % (
            ccy, fxerr or "rate missing")
        return b
    eq = 0.0
    marked, unmarked = 0, 0
    for dname, desk in (d.get("desks") or {}).items():
        cash = float(desk.get("cash") or 0.0)
        b["cash_usd"] += cash * f
        cost_sum = sum(float(p.get("cost") or 0.0) for p in (desk.get("positions") or {}).values())
        unreal = desk.get("unrealized")
        if unreal is None:
            unreal = 0.0
            if desk.get("positions"):
                b["warnings"].append(
                    "%s: ledger stores no `unrealized` — equity is cash+COST, "
                    "open P&L is NOT in this number" % dname)
        eq += (cash + cost_sum + float(unreal)) * f
        for sym, p in (desk.get("positions") or {}).items():
            side = p.get("side") or "long"
            sign = -1.0 if side == "short" else 1.0
            units = float(p.get("units") or 0.0)
            mk = p.get("mark")
            # DEFECT FIX 2026-08-27: floors were valued at ENTRY COST while a
            # live `mark` sat in the same record. Mark first, cost declared.
            if isinstance(mk, (int, float)) and mk > 0 and units > 0:
                val, basis = sign * units * float(mk) * f, "mark"
                marked += 1
            else:
                val = sign * float(p.get("cost") or 0.0) * f
                basis = "entry cost (no mark in record)"
                unmarked += 1
            tk = (symmap or {}).get(sym) or yf_ticker(sym)
            b["positions"].append(
                _pos(key, "%s/%s" % (dname, sym), tk, units, val, basis, 1.0, side))
    # CM-046: carry the book's own funded baseline when the ledger records one
    # (coinspot/cross_asset store `start_cash` at top level). Without it these
    # books report equity with no P&L denominator.
    if isinstance(d.get("start_cash"), (int, float)) and d.get("start_cash"):
        b["start_usd"] = float(d["start_cash"]) * f
    if key == "coinspot_floor":
        review = jf(os.path.join(BASE, "reports", "coinspot_entry_integrity_20260921.json"), {}) or {}
        if review.get("block_new_entries") is True:
            b["warnings"].append("CoinSpot historical entry currency mismatch: P&L unverified; new entries held pending reconciliation.")
            b["performance_verified"] = False
    b["equity_usd"] = eq
    b["equity_basis"] = "cash + Σcost + unrealized, per desk" + (
        " (×%.6f %s→USD)" % (f, ccy) if ccy != "USD" else "")
    # CM-046: CoinSpot and Cross-Asset store no per-position `mark` and no
    # `unrealized`, so the sum above is MARK-BLIND for them — firm drawdown
    # could not see an unrealised loss in those books. Since the kill rule went
    # live each floor stamps a MARKED equity into its own `book_risk`; prefer it
    # when it is fresh, and say so in the basis. Never silently: if book_risk is
    # stale the cost-basis figure is kept and a warning is raised.
    br = d.get("book_risk") or {}
    br_eq = br.get("equity")
    if unmarked and isinstance(br_eq, (int, float)) and br_eq:
        # calendar.timegm, NOT time.mktime: mktime reads the struct as LOCAL
        # time. This host runs WITA (UTC+8), which made a fresh stamp report as
        # -58 minutes old — a NEGATIVE age that slid past the staleness check by
        # accident and would have passed a genuinely stale value too.
        age = None
        try:
            age = time.time() - calendar.timegm(time.strptime(
                br.get("checked_utc") or "", "%Y-%m-%dT%H:%M:%SZ"))
        except Exception:
            age = None
        if age is not None and 0 <= age <= 6 * 3600:
            b["equity_usd"] = float(br_eq) * f
            b["equity_basis"] = ("book_risk.equity (MARKED by the floor itself, "
                                 "%.0f min old)" % (age / 60.0)) + (
                " (\u00d7%.6f %s\u2192USD)" % (f, ccy) if ccy != "USD" else "")
        else:
            b["warnings"].append(
                "book_risk.equity present but %s — keeping the mark-blind "
                "cost basis; firm drawdown cannot see unrealised P&L in this book"
                % (("stale (%.1fh)" % (age / 3600.0)) if age is not None and age > 0
                   else ("timestamped in the FUTURE (%.1fh) — clock skew" % (-age / 3600.0))
                   if age is not None
                   else "has no readable checked_utc"))
    if unmarked:
        b["warnings"].append("%d of %d open positions carry NO mark — valued at cost"
                             % (unmarked, marked + unmarked))
    return b


def _load_carry_paper(key, label, path):
    """Delta-neutral basis carry, AUD. Spot long + perp short of equal notional:
    both legs are reported so gross is honest and net is ~0, which is the truth
    about this book's directional risk."""
    b = _book(key, label, "carry", path, "AUD")
    d = jf(path)
    if d is None:
        b["error"] = "unreadable or missing ledger"
        return b
    b["asof"] = d.get("updated")
    rate, fxerr = _usd_rates(["AUD"])
    f = rate.get("AUD")
    if f is None:
        b["error"] = "no AUD/USD rate (%s) — book UNREADABLE" % (fxerr or "rate missing")
        return b
    try:
        import carry_paper_desk as _cpd
        # COO/funder may reduce allocated capital via ledger override after flatten
        # A negative override is a legitimate net-allocated basis after a paired
        # transfer out; only a MISSING override falls back to NOTIONAL_AUD.
        if (isinstance(d.get("notional_cap_aud"), (int, float))
                and not isinstance(d.get("notional_cap_aud"), bool)
                and math.isfinite(float(d["notional_cap_aud"]))):
            seed = float(d["notional_cap_aud"])
            seed_src = "carry_paper_book.json notional_cap_aud (override)"
        else:
            seed = float(_cpd.NOTIONAL_AUD)
            seed_src = "carry_paper_desk.NOTIONAL_AUD"
    except Exception as e:                                # noqa: BLE001
        b["error"] = "cannot read book capital (%s: %s)" % (type(e).__name__, e)
        return b
    eq_aud = (seed + float(d.get("realized_aud") or 0.0)
              + float(d.get("funding_accrued_aud") or 0.0)
              - float(d.get("costs_paid_aud") or 0.0))
    b["equity_usd"] = eq_aud * f
    b["start_usd"] = seed * f
    b["equity_basis"] = ("%s + realized + funding_accrued − costs_paid "
                         "(derived: the ledger publishes no equity field) ×%.6f AUD→USD"
                         % (seed_src, f))
    b["cash_usd"] = b["equity_usd"]          # no cash field; capital is unallocated margin
    for basec, p in (d.get("positions") or {}).items():
        notl = float(p.get("notional_aud") or 0.0) * f
        entry = float(p.get("entry_mark") or 0.0)
        units = (float(p.get("notional_aud") or 0.0) / entry) if entry else 0.0
        move = float(p.get("mark_move_pct") or 0.0) / 100.0
        tk = CARRY_MAP.get(basec) or yf_ticker(basec)
        b["positions"].append(_pos(key, basec + " spot(long)", tk, units,
                                   notl * (1 + move), "mark_move_pct on notional", 1.0, "long"))
        b["positions"].append(_pos(key, basec + " perp(short)", tk, units,
                                   -notl * (1 + move), "mark_move_pct on notional", 1.0, "short"))
    if d.get("positions"):
        b["warnings"].append("delta-neutral: legs net to ~0 by construction; "
                             "gross shows both legs")
    return b


def _load_simple_probe(key, label, path):
    """cash / positions{} / equity — carry_newnames_probe shape."""
    b = _book(key, label, "probe", path)
    d = jf(path)
    if d is None:
        b["error"] = "unreadable or missing ledger"
        return b
    b["asof"] = d.get("updated")
    b["cash_usd"] = float(d.get("cash") or 0.0)
    b["equity_usd"] = float(d.get("equity") or 0.0)
    b["equity_basis"] = "book's own equity field"
    # Prefer post-transfer funding basis (start / seed_usd) over original seed
    for _k in ("start", "seed_usd", "seed"):
        if (isinstance(d.get(_k), (int, float)) and not isinstance(d.get(_k), bool)
                and math.isfinite(float(d[_k])) and float(d[_k]) != 0):
            b["start_usd"] = float(d[_k])
            break
    else:
        b["start_usd"] = None
    if isinstance(d.get("peak"), (int, float)):
        b["book_peak_usd"] = float(d["peak"])
    if d.get("frozen"):
        b["warnings"].append("book reports frozen=true")
    for sym, p in (d.get("positions") or {}).items():
        side = (p or {}).get("side") or "long"
        sign = -1.0 if side == "short" else 1.0
        units = float((p or {}).get("units") or 0.0)
        mk = (p or {}).get("mark")
        val = sign * units * float(mk) if isinstance(mk, (int, float)) and mk else \
            sign * float((p or {}).get("notional_usd") or (p or {}).get("cost") or 0.0)
        basis = "mark" if isinstance(mk, (int, float)) and mk else "notional (no mark)"
        b["positions"].append(_pos(key, sym, CARRY_MAP.get(sym) or yf_ticker(sym),
                                   units, val, basis, 1.0, side))
    return b


def _load_punt(key, label, path):
    b = _book(key, label, "punt", path)
    d = jf(path)
    if d is None:
        b["error"] = "unreadable or missing ledger"
        return b
    b["asof"] = d.get("updated_utc")
    b["equity_usd"] = float(d.get("equity") or 0.0)
    b["equity_basis"] = "book's own equity field"
    b["start_usd"] = float(d.get("start") or 0.0) or None
    b["cash_usd"] = float(d.get("cash") or 0.0)
    units = float(d.get("units") or 0.0)
    mk = d.get("mark")
    if units and isinstance(mk, (int, float)):
        b["positions"].append(_pos(key, "PUMP/USD", yf_ticker("PUMP/USD"), units,
                                   units * float(mk), "mark", 1.0, "long"))
    if d.get("liquidated_utc"):
        b["warnings"].append("LIQUIDATED %s at %s — book is dead, equity 0"
                             % (d.get("liquidated_utc"), d.get("liquidated_at_price")))
    return b


def _load_engine_bot(key, label, path, http=("127.0.0.1", 18799, "/state")):
    """The LIVE Kraken paper engine (kraken_paper_bot.py). Shape: cash /
    positions{side,units,entry,cost} / start_cash / peak_equity / realized_pnl.

    CM-044: this book was MISSING from the registry entirely while the firm's own
    cross_desk_allocator.py:44 carries it as BOOKS[0]. It is 99.6% cash so the
    gross-exposure error was small, but firm EQUITY, PEAK and DRAWDOWN — the
    series that gates HALT_NEW_ENTRIES — were wrong by construction.

    The live HTTP read is preferred (it is what the allocator uses and it cannot
    be stale); the on-disk ledger is the fallback. If BOTH fail the book is
    marked unreadable — never zero, because a zero here silently shrinks firm
    equity and manufactures a drawdown."""
    b = _book(key, label, "engine", path)
    d, src_basis = None, ""
    try:
        import urllib.request
        host, port, route = http
        with urllib.request.urlopen("http://%s:%d%s" % (host, port, route),
                                    timeout=4) as r:
            d = json.loads(r.read().decode("utf-8"))
        src_basis = "live engine HTTP :%d%s" % (port, route)
    except Exception as e:
        b["warnings"].append("live engine read failed (%s); using ledger file"
                             % type(e).__name__)
        d = jf(path)
        src_basis = "on-disk ledger (engine HTTP unreachable)"
        b["stale"] = True
    if d is None:
        b["error"] = "engine unreadable via BOTH http and file — NOT counted as zero"
        return b
    b["asof"] = d.get("updated") or d.get("day_date")
    b["cash_usd"] = float(d.get("cash") or 0.0)
    b["start_usd"] = float(d.get("start_cash") or 0.0) or None
    if isinstance(d.get("peak_equity"), (int, float)):
        b["book_peak_usd"] = float(d["peak_equity"])
    if d.get("kill_switch"):
        b["warnings"].append("engine reports kill_switch=%s" % d.get("kill_switch"))
    eq = b["cash_usd"]
    for sym, pz in (d.get("positions") or {}).items():
        side = (pz or {}).get("side") or "long"
        sign = -1.0 if side == "short" else 1.0
        units = float((pz or {}).get("units") or 0.0)
        mk = (pz or {}).get("mark") or (pz or {}).get("last")
        if isinstance(mk, (int, float)) and mk:
            val, basis = sign * units * float(mk), "mark"
        else:
            val, basis = sign * float((pz or {}).get("cost") or 0.0), "entry cost (no mark)"
        eq += abs(val) if side == "long" else 0.0
        b["positions"].append(_pos(key, sym, yf_ticker(sym), units, val, basis,
                                   1.0, side))
    b["equity_usd"] = eq
    b["equity_basis"] = "cash + long position value, from " + src_basis
    return b


def _load_maker_probe(key, label, path):
    """maker_calibration_probe — runs EVERY desk_tick but was never registered.
    Shape: marked_equity / current equity / params.book_usd / position{}."""
    b = _book(key, label, "probe", path)
    d = jf(path)
    if d is None:
        b["error"] = "unreadable or missing ledger"
        return b
    b["asof"] = d.get("last_tick_utc")
    registered_book = (d.get("params") or {}).get("book_usd")
    if isinstance(registered_book, (int, float)) and registered_book > 0:
        b["start_usd"] = float(registered_book)
    else:
        b["warnings"].append(
            "probe has no positive params.book_usd — start unknown; current "
            "equity is never substituted as contributed capital")
    me = d.get("marked_equity")
    b["equity_usd"] = float(me) if isinstance(me, (int, float)) else b["start_usd"]
    b["equity_basis"] = "marked_equity" if isinstance(me, (int, float)) \
        else "entry baseline (no marked_equity)"
    if isinstance(d.get("peak"), (int, float)):
        b["book_peak_usd"] = float(d["peak"])
    pz = d.get("position")
    if pz is not None and pz != {}:
        def finite(value, positive=False):
            return (isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value) and (not positive or value > 0))

        if not isinstance(pz, dict):
            b["error"] = "maker position is not an object — exposure UNREADABLE"
            return b
        sym = pz.get("sym") or pz.get("symbol") or "XBT/USD"
        if "entry_equity" in pz:
            # Native schema from maker_calibration_probe.apply_fill/mark_book:
            # Q = equity_at_entry * (1-fee) / entry_price; marked equity is
            # N * (1 + direction * (mark/entry - 1)). For a short, equity is
            # NOT its marked exposure. Recover the same signed quantity and
            # mark algebraically; marked_equity is rounded to cents upstream.
            from maker_calibration_probe import MAKER_FEE
            entry, funded, direction = (pz.get("entry_px"), pz.get("entry_equity"),
                                        pz.get("direction"))
            if (not finite(entry, True) or not finite(funded, True)
                    or not finite(direction) or direction not in (-1, 1)
                    or not finite(me) or not finite(MAKER_FEE)
                    or not 0 <= MAKER_FEE < 1):
                b["error"] = "maker native position lacks valid funding, direction or marked equity — exposure UNREADABLE"
                return b
            notional = funded * (1.0 - MAKER_FEE)
            units = notional / entry
            mk = entry * (1.0 + (me / notional - 1.0) / direction)
            sign = float(direction)
            side = "long" if direction > 0 else "short"
            basis = "implied from probe marked equity (rounded to cents)"
        else:
            # Retain the earlier explicit-position schema, without inventing a
            # side or treating absent units/marks as zero exposure.
            side, units = pz.get("side"), pz.get("units")
            if side not in ("long", "short"):
                direction = pz.get("direction")
                if finite(direction) and direction in (-1, 1):
                    side = "long" if direction > 0 else "short"
            mk = pz.get("mark")
            if mk is None:
                mk = pz.get("entry")
                basis = "entry (no mark)"
            else:
                basis = "mark"
            if side not in ("long", "short") or not finite(units, True):
                b["error"] = "maker explicit position lacks valid units or side — exposure UNREADABLE"
                return b
            sign = -1.0 if side == "short" else 1.0
        if not finite(mk, True) or not finite(units, True) or not finite(units * mk, True):
            b["error"] = "maker position has no valid price or quantity — exposure UNREADABLE"
            return b
        b["positions"].append(_pos(key, sym, yf_ticker(sym), units,
                                   sign * units * mk, basis, 1.0, side))
    return b



def _load_short_pilot(key, label, path, now=None):
    """Separate fully collateralised PAPER SOL perp; net-liquidation equity.

    Account eligibility is unverified. PF_SOLUSD remains the position identity;
    KRK:SOL is the existing underlying spot risk proxy, never an execution quote.
    """
    if not os.path.exists(path):
        return None
    b = _book(key, label, "simulated_short", path)
    b.update({"simulated_only": True, "verified_account_access": False})
    d = jf(path)
    try:
        if not isinstance(d, dict):
            raise ValueError("present ledger is malformed or unreadable")
        if d.get("paper_only") is not True or d.get("side") != "short" or d.get("symbol") != "PF_SOLUSD":
            raise ValueError("requires PAPER short with exact PF_SOLUSD identity")

        def num(field, minimum=None, positive=False):
            value = d.get(field)
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(value) or (minimum is not None and value < minimum)
                    or (positive and value <= 0)):
                raise ValueError("invalid or missing " + field)
            return float(value)

        def same(actual, expected, identity):
            if not math.isfinite(expected) or not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=.02):
                raise ValueError(identity + " does not reconcile")

        q, entry, mark = num("quantity", 0), num("entry_price", positive=True), num("mark_price", positive=True)
        cash, collateral, equity = num("cash"), num("collateral_usd", 0), num("equity")
        seed, start = num("seed_usd"), num("start")
        funding, total_funding = num("funding_accrued_usd"), num("funding_total_usd")
        exit_fee, fee_bps = num("estimated_exit_fee_usd", 0), num("fee_bps", 0)
        if fee_bps >= 10000:
            raise ValueError("fee_bps must be less than notional")
        unrealised, realised, costs = num("unrealised_usd"), num("realised_usd"), num("costs_paid_usd", 0)
        same(start, seed, "seed/start")
        same(collateral, q * entry, "full entry collateral")
        same(unrealised, q * (entry - mark), "short P&L")
        same(exit_fee, q * mark * fee_bps / 10000., "estimated exit fee")
        same(funding, total_funding - num("realised_funding_usd"), "funding")
        same(equity, cash + collateral + unrealised + funding - exit_fee, "net-liquidation equity")
        same(equity, seed + realised + unrealised + total_funding - costs - exit_fee, "capital/P&L identity")
        from datetime import datetime
        stamp = d.get("mark_utc")
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError("mark_utc must have an explicit timezone")
        age = (time.time() if now is None else now) - parsed.timestamp()
        b["asof"], b["mark_utc"], b["mark_age_sec"] = stamp, stamp, age
        b["stale"] = not 0 <= age <= 1800
        if b["stale"]:
            b["warnings"].append("short pilot mark is stale or future-dated; age %.1fs, limit 1800s" % age)
        b["cash_usd"], b["equity_usd"], b["start_usd"] = cash, equity, seed
        b["equity_basis"] = "cash + full collateral + short P&L + accrued funding - estimated exit fee"
        b["estimated_exit_fee_usd"] = exit_fee
        b["warnings"].append("SIMULATED ONLY: Kraken account access/eligibility unverified; KRK:SOL risk uses an underlying spot proxy")
        if q:
            b["positions"].append(_pos(key, "PF_SOLUSD", yf_ticker("PF_SOLUSD"), q,
                                       -q * mark, "freshness-labelled cover VWAP; perp identity, spot risk proxy", 1.0, "short"))
        return b
    except (ValueError, TypeError, AttributeError, OverflowError) as exc:
        b["error"] = "short pilot UNREADABLE: " + str(exc)
        return b


def _load_fundamental(key, label, path):
    """TFO Fundamental Desk — $600k funder-directed global long/short book ($500k + $100k
    top-up, funder-confirmed 2026-08-29; see CM-054).

    Positions carry `mark_usd` and `units` already stated in USD, so no rate is
    applied to `fallback_usd` here: the book converts at its own entry and mark
    and publishes the FX it used. Applying a second conversion to that value
    would double-count the currency move -- the §3(c) hazard, in the aggregator
    instead of the book.

    DEFECT FIX 2026-08-28: that argument is right about the VALUE and was wrong
    about `mark_fx_usd`, which this loader hardcoded to 1.0. `mark_fx_usd` is
    not a second conversion of our USD number — it is the multiplier a DOWNSTREAM
    re-mark needs to turn the yfinance ticker's LOCAL close into USD
    (`risk_engine.remark`: units * local_close * mark_fx_usd). 33 of the 60
    positions here are foreign listings, so 1.0 valued them at their raw local
    price treated as dollars: ECOPETROL.CL (COP) $12,078 became $38.0M and
    SOL.JO (ZAc) $19,653 became $32.3M, and the firm's gross book printed
    $92.35M against $1.59M of firm equity — every VaR, vol, beta, concentration
    and stress number in risk_report.json was really about two tickers. The
    book's own ledger was correct throughout and is unchanged; this is purely
    the aggregator failing to forward what the book already publishes.

    The FX is the ticker's LOCAL-QUOTE -> USD factor, and it is DERIVED from the
    book's own two published prices for the same position rather than read from
    its `mark_fx_usd` field:

        fx = mark_usd / mark          (per-unit local price -> per-unit USD)

    That ratio is the factor which reconciles the book's own numbers, so it is
    self-consistent by construction and needs no currency table here. Reading
    the published field instead would import a second live defect, found by the
    sanity guard in `risk_engine.remark()` on 2026-08-28: for the six pence-
    quoted London listings the book publishes the **GBP** rate in `mark_fx_usd`
    (WPP.L: entry_ccy "GBp", entry_px 378.514 pence, mark_fx_usd 1.3587) while
    correctly dividing by 100 to compute `mark_usd` ($5.14). Trusting the field
    re-marked a $3,949 position at $395,156 — exactly 100x. The same book gets
    ZAc and every other cents-quoted line right, so the convention is
    inconsistent WITHIN the ledger and cannot be repaired by trusting one field.
    The ratio is immune to which convention any given row used.

    If a non-USD position publishes no usable pair to divide, the factor is
    passed as None so the re-mark REFUSES it and keeps the stored USD value —
    silently substituting 1.0 is what caused the original defect, and a wrong
    number is worse than a declared fallback.
    """
    # CM-048: a MISSING file means the book is not open yet (or is mid-rebuild
    # — it was quarantined once already as MISSIZED-do-not-use). Returning an
    # error book in that window would pin the whole firm at UNMEASURED. Absent
    # is reported and skipped; PRESENT-but-unparseable is a real error.
    if not os.path.exists(path):
        print("   NOTE: %s not open yet (%s absent) — excluded from the firm "
              "registry until it exists; it is picked up automatically."
              % (label, os.path.basename(path)))
        return None
    b = _book(key, label, "book", path)
    d = jf(path)
    if d is None:
        b["error"] = "ledger EXISTS but is unreadable — NOT counted as zero"
        return b
    b["asof"] = d.get("updated")
    b["cash_usd"] = float(d.get("cash") or 0.0)
    # CM-048: `float(d.get("equity") or 0.0)` turned "not computed yet" into
    # "$0" on a $500k book — observed live at 12:14:49Z, mid-rebuild, with 53
    # positions open and no `equity` field. A silent zero here understates firm
    # equity by the whole book AND makes the later correction read as a
    # +$500,000 PERFORMANCE gain on the firm series, because a book already in
    # book_keys is not treated as a capital event when its value appears.
    # Unmeasured is an ERROR. It is never zero.
    _eq = d.get("equity")
    if not isinstance(_eq, (int, float)):
        b["error"] = ("ledger present with %d position(s) but NO `equity` field "
                      "(book is mid-open/rebuild) — reported UNMEASURED, never "
                      "as zero" % len(d.get("positions") or {}))
        return b
    b["equity_usd"] = float(_eq)
    b["equity_basis"] = "book's own equity field (cash + long MV + short collateral + short P&L)"
    # CM-052: EQUITY IDENTITY CHECK. A book's equity must reconcile to
    # seed + realised - costs + unrealised. On 2026-08-29 this book reported
    # $600,538.83 against an identity of $500,538.52 — $100,000.31 of PHANTOM
    # equity, published as +20.1% "performance" and flowing straight into firm
    # equity. Either capital was added without updating seed_usd (a capital
    # event dressed as a gain) or longs were opened without debiting cash.
    # Never silent again.
    # CM-071 (2026-09-04): CASH INTEREST IS PART OF EQUITY AND MUST BE A TERM.
    # CM-068 wired T-bill accrual firm-wide. It credits `cash` — and therefore
    # equity — but appears in NONE of the four identity terms, so the gap grew by
    # exactly the interest earned: 0.37 -> 0.27 -> 2.52 -> 7.78 over four days on
    # the fundamental book, about $8/day. Left alone it reaches the $3,000 breach
    # bar in roughly a year and fires a CAPITAL IDENTITY BREACH **for interest
    # income** — the guard crying wolf at the one book that is behaving.
    #
    # This is a CORRECTION, not a relaxation. The credit is bounded by the desk's
    # own separately-recorded accrual field, which is auditable and was kept
    # separate precisely so rate income could never be mistaken for skill. Phantom
    # equity of the CM-052 kind ($100,000.31 appearing from nowhere) is still
    # caught in full — interest cannot explain a six-figure gap.
    try:
        _seed = float(d.get("seed_usd") or 0.0)
        _cash_int = d.get("cash_yield_total_accrued_usd")
        if _cash_int is None:
            _cash_int = d.get("cash_interest_accrued")
        if _cash_int is None:
            _cash_int = sum(float((v or {}).get("cash_interest_accrued") or 0.0)
                            for g in ("desks", "bands")
                            for v in (d.get(g) or {}).values() if isinstance(v, dict))
        _cash_int = float(_cash_int or 0.0)
        _idn = (_seed + float(d.get("realised_usd") or 0.0)
                - float(d.get("costs_paid_usd") or 0.0)
                + float(d.get("unrealised_usd") or 0.0)
                + _cash_int)
        _gap = float(_eq) - _idn
        b["equity_identity_usd"] = round(_idn, 2)
        b["equity_identity_gap_usd"] = round(_gap, 2)
        b["equity_identity_cash_interest_usd"] = round(_cash_int, 6)
        if _seed and abs(_gap) > max(500.0, 0.005 * _seed):
            b["warnings"].append(
                "EQUITY DOES NOT RECONCILE: reported $%s vs identity "
                "(seed+realised-costs+unrealised) $%s — $%s UNEXPLAINED. Treat "
                "the P&L as UNTRUSTWORTHY until this is resolved."
                % ("{:,.2f}".format(float(_eq)), "{:,.2f}".format(_idn),
                   "{:,.2f}".format(_gap)))
            print("   !! %s EQUITY IDENTITY BREACH: $%s unexplained"
                  % (label, "{:,.2f}".format(_gap)))
    except Exception:
        pass
    b["start_usd"] = float(d.get("seed_usd") or 0.0) or None
    br = d.get("book_risk") or {}
    if isinstance(br.get("peak_equity"), (int, float)):
        b["book_peak_usd"] = float(br["peak_equity"])
    if br.get("frozen"):
        b["warnings"].append("book reports frozen=true: %s" % br.get("frozen_reason", ""))
    if d.get("n_unmarked"):
        b["warnings"].append(
            "%d position(s) UNMARKED ($%s notional) — carrying last good mark"
            % (d["n_unmarked"], "{:,.0f}".format(d.get("unmarked_usd") or 0)))
    if d.get("borrow_cost_modelled") is False and d.get("n_short"):
        b["warnings"].append(
            "%d shorts with NO borrow cost modelled — the short book is flattered"
            % d.get("n_short", 0))
    if d.get("unopened"):
        b["warnings"].append("%d position(s) HELD, not opened (see ledger `unopened`)"
                             % len(d["unopened"]))
    no_fx = []
    for sym, pos in (d.get("positions") or {}).items():
        side = (pos or {}).get("side") or "long"
        sign = -1.0 if side == "short" else 1.0
        units = float((pos or {}).get("units") or 0.0)
        mk = (pos or {}).get("mark_usd")
        if isinstance(mk, (int, float)) and mk:
            val, basis = sign * units * float(mk), "mark_usd"
        else:
            val = sign * float((pos or {}).get("notional_usd") or 0.0)
            basis = "notional (NO MARK)"
        # local-quote -> USD for the yfinance ticker; see the docstring. Derived
        # from the book's own price pair, NOT read from its mark_fx_usd field.
        ccy = ((pos or {}).get("entry_ccy") or "USD").strip()
        mfx = None
        for loc, usd in (((pos or {}).get("mark"), (pos or {}).get("mark_usd")),
                         ((pos or {}).get("entry_px"), (pos or {}).get("entry_px_usd"))):
            if (isinstance(loc, (int, float)) and loc > 0
                    and isinstance(usd, (int, float)) and usd > 0):
                mfx = float(usd) / float(loc)
                break
        if mfx is None:
            mfx = 1.0 if ccy.upper() == "USD" else None
            if mfx is None:
                no_fx.append("%s (%s)" % (sym, ccy))
        b["positions"].append(_pos(key, sym, yf_ticker(sym), units, val, basis, mfx, side))
    if no_fx:
        b["warnings"].append(
            "%d non-USD position(s) publish no usable FX (%s) — NOT re-marked; "
            "carried at the book's own stored USD value"
            % (len(no_fx), ", ".join(no_fx[:6])))
    return b


def _load_dormant_desk(key, label, path, since=""):
    """kraken_meanrev_desk — $40k ledger with an OPEN short that nothing ticks.
    Registered (not hidden) and flagged DORMANT loudly, because an unticked book
    holding an open position is exactly the thing a firm risk report must not
    make invisible. Retiring it is a funder decision, not mine."""
    b = _book(key, label, "dormant", path)
    d = jf(path)
    if d is None:
        b["error"] = "unreadable or missing ledger"
        return b
    b["asof"] = d.get("updated")
    b["cash_usd"] = float(d.get("cash") or 0.0)
    b["start_usd"] = float(d.get("start_cash") or 0.0) or None
    npos = len(d.get("positions") or {})
    b["warnings"].append("DORMANT: no scheduler ticks this book%s; it still holds "
                         "%d open position(s) and %s of capital — retire or "
                         "re-activate is a FUNDER decision"
                         % ((" (last activity %s)" % since) if since else "",
                            npos, "$%.2f" % (b["start_usd"] or 0.0)))
    b["stale"] = True
    eq = b["cash_usd"]
    for sym, pz in (d.get("positions") or {}).items():
        side = (pz or {}).get("side") or "long"
        sign = -1.0 if side == "short" else 1.0
        units = float((pz or {}).get("units") or 0.0)
        cost = float((pz or {}).get("cost") or 0.0)
        mk = (pz or {}).get("mark")
        if isinstance(mk, (int, float)) and mk:
            val, basis = sign * units * float(mk), "mark"
        else:
            val, basis = sign * cost, "entry cost (no mark — book is unticked)"
        eq += cost if side == "short" else abs(val)
        b["positions"].append(_pos(key, sym, yf_ticker(sym), units, val, basis,
                                   1.0, side))
    b["equity_usd"] = eq
    b["equity_basis"] = "cash + position cost (DORMANT book, marks are frozen)"
    return b



def _load_dated_event(key, label, path):
    """Dated-Event Long Book — $75k transferred from the convexity sleeve 2026-09-06.

    Registered at birth. Equity = cash + marked positions; seed = the transferred
    capital, so the transfer is not read as P&L on either side (CM-052).
    """
    if not os.path.exists(path):
        return None
    b = _book(key, label, "book", path)
    d = jf(path)
    if d is None:
        b["error"] = "ledger EXISTS but is unreadable"; return b
    mv = 0.0
    for sym, p in (d.get("positions") or {}).items():
        v = float(p.get("mark_usd") or 0) * float(p.get("units") or 0); mv += v
        # The stored mark is USD, but the risk panel downloads LOCAL quotes.
        # DATA.L is quoted in pence; a hard-coded 1.0 manufactured a ~75x
        # exposure and was only stopped by the risk engine's sanity check.
        ccy = p.get("entry_ccy") or p.get("ccy") or "USD"
        mfx = p.get("entry_fx_usd")
        if not isinstance(mfx, (int, float)) or isinstance(mfx, bool) or not math.isfinite(mfx) or mfx <= 0:
            mfx = 1.0 if ccy == "USD" else None
        if mfx is None:
            b["warnings"].append("%s: missing %s→USD rate; keep stored USD exposure, do not re-mark in local units" % (sym, ccy))
        b["positions"].append({"book": key, "sym": sym, "label": "%s:%s" % (key, sym), "ticker": sym,
                               "units": float(p.get("units") or 0), "fallback_usd": v,
                               "fallback_basis": "book mark_usd", "mark_fx_usd": mfx, "sign": 1.0, "side": "long"})
    b["cash_usd"] = float(d.get("cash") or 0); b["equity_usd"] = b["cash_usd"] + mv
    b["equity_basis"] = "cash + marked long positions"; b["start_usd"] = __import__("capital_accounting").net_allocated_capital(d)
    b["start_source"] = "Net recorded transfers; DATED_EVENT_BOOK_PREREGISTRATION.md: $75,000 capital transfer from convexity_sleeve"
    b["asof"] = d.get("updated_utc"); b["pending"] = len(d.get("pending") or {})
    return b


def _load_exit_shadow(key, label, path):
    """Paired exit shadow of kraken_floor. $0 deployed — measurement only.

    Registered at birth so it is visible firm-wide, but it carries NO capital and
    its equity is reported as zero: it holds mirrored positions purely to measure
    what a different exit rule WOULD have realised. Counting its marks as firm
    equity would double-count the live floor's positions.
    """
    if not os.path.exists(path):
        return None
    b = _book(key, label, "shadow", path)
    d = jf(path)
    if d is None:
        b["error"] = "ledger EXISTS but is unreadable"
        return b
    b["cash_usd"] = 0.0
    b["equity_usd"] = 0.0
    b["equity_basis"] = "shadow — $0 deployed, positions are mirrors of kraken_floor and are NOT firm equity"
    b["start_usd"] = 0.0
    b["start_source"] = "FLOOR_EXIT_SHADOW_PREREGISTRATION.md: paired measurement, no capital"
    b["asof"] = d.get("updated_utc")
    b["shadow_open"] = len(d.get("positions") or {})
    b["shadow_closed"] = len(d.get("closed") or [])
    return b


def _load_convexity(key, label, path):
    """Convexity Sleeve — $150k defined-risk, funder-directed 21x target.

    Registered at BIRTH (the CM-044 lesson), before it holds a single position,
    so it can never be one of the books that sits outside "firm-wide".

    Its risk is bounded by construction: every position carries a capped
    max_loss_usd and the sum can never exceed the $150,000 seed. So the firm's
    exposure to this experiment is knowable exactly, at all times, from the
    ledger alone — which is the only reason it is allowed to exist.
    """
    if not os.path.exists(path):
        print("   NOTE: %s not open yet (%s absent) — excluded until it exists."
              % (label, os.path.basename(path)))
        return None
    b = _book(key, label, "sleeve", path)
    d = jf(path)
    if d is None:
        b["error"] = "ledger EXISTS but is unreadable — NOT counted as zero"
        return b
    cash = float(d.get("cash") or 0.0)
    pos = d.get("positions") or {}
    mv = 0.0
    for sym, q in pos.items():
        mk = float(q.get("mark_usd") or 0.0)
        mv += mk
        b["positions"].append({
            "book": key, "sym": sym, "label": "%s:%s" % (key, sym),
            "ticker": sym, "units": 1.0, "fallback_usd": mk,
            "fallback_basis": "option/defined-risk premium mark",
            "mark_fx_usd": 1.0, "sign": 1.0, "side": "long",
        })
    b["cash_usd"] = cash
    b["equity_usd"] = cash + mv
    b["equity_basis"] = "cash + marked premium on defined-risk positions"
    b["start_usd"] = float(d.get("seed_usd") or 150000.0)
    b["start_source"] = "convexity_sleeve.py SEED_USD = 150000.0 (funder-directed 2026-09-03)"
    b["asof"] = d.get("updated_utc")
    committed = sum(float(q.get("max_loss_usd") or 0.0) for q in pos.values())
    if committed > b["start_usd"] + 1e-6:
        b.setdefault("warnings", []).append(
            "committed risk $%.2f EXCEEDS the $%.2f seed cap — the sleeve guard failed"
            % (committed, b["start_usd"]))
    return b


def _load_crypto_rumour(key, label, path):
    """Crypto Rumour-Rider — the $20k declared forward test (two bands).

    Registered at BIRTH, not after a coverage audit caught it. That is the
    CM-044 lesson: three live books once sat outside "firm-wide" because the
    registry was hand-written and nobody added them.

    Shape: bands{top50,lowvol}.{cash, positions{units,entry,mark}, equity}.
    A missing ledger means the sleeve has not run yet -> excluded with a notice,
    never an error that would pin the firm at UNMEASURED.
    """
    if not os.path.exists(path):
        print("   NOTE: %s not open yet (%s absent) — excluded until it exists."
              % (label, os.path.basename(path)))
        return None
    b = _book(key, label, "sleeve", path)
    d = jf(path)
    if d is None:
        b["error"] = "ledger EXISTS but is unreadable — NOT counted as zero"
        return b
    b["asof"] = d.get("updated")
    b["start_usd"] = float(d.get("seed_usd") or 0.0) or None
    eq = d.get("equity")
    if not isinstance(eq, (int, float)):
        b["error"] = "ledger present but no `equity` — UNMEASURED, never zero"
        return b
    b["equity_usd"] = float(eq)
    b["equity_basis"] = "sum of band equities (cash + marked positions)"
    b["cash_usd"] = sum(float((v or {}).get("cash") or 0.0)
                        for v in (d.get("bands") or {}).values())
    peaks = [float((v or {}).get("peak_equity") or 0.0)
             for v in (d.get("bands") or {}).values()]
    if peaks:
        b["book_peak_usd"] = sum(peaks)
    stale = 0
    for bn, bk in (d.get("bands") or {}).items():
        if bk.get("frozen"):
            b["warnings"].append("band %s FROZEN: %s" % (bn, bk.get("frozen_reason")))
        for sym, pz in (bk.get("positions") or {}).items():
            mk = (pz or {}).get("mark") or (pz or {}).get("entry")
            units = float((pz or {}).get("units") or 0.0)
            if (pz or {}).get("mark_stale"):
                stale += 1
            ticker = (binance_risk_prices.PREFIX + sym if sym in binance_risk_prices.VERIFIED_PAIRS
                      else yf_ticker(sym.replace("USDT", "/USD")))
            b["positions"].append(_pos(key, sym, ticker,
                                       units, units * float(mk or 0.0),
                                       "mark" if pz.get("mark") else "entry", 1.0, "long"))
    if stale:
        b["warnings"].append("%d position(s) marked STALE (bar older than the gate)" % stale)
    b["warnings"].append("NO VALIDATED EDGE — backtest FAILED out of sample "
                         "(recent half mean -0.055%, t -0.06 at 40bps); declared "
                         "forward test, not an edge")
    return b


def _load_oanda(key, label, path):
    """The OANDA book. The LIVE status read is the only trustworthy position
    source (a logged 'close' there is a known no-op), and oanda_status.txt is
    empty — a sustained 401. So positions are reported UNREADABLE and the last
    ledger NAV is carried as an explicitly STALE equity line, never as measured
    exposure. Guessing positions from a ledger that is known to lie would be
    fabrication."""
    b = _book(key, label, "oanda", path, "AUD")
    status = os.path.join(OANDA_DESK, "oanda_status.txt")
    live = ""
    try:
        with open(status) as f:
            live = f.read().strip()
    except Exception:
        live = ""
    nav, ts = None, None
    try:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if isinstance(r.get("nav"), (int, float)):
                    nav, ts = float(r["nav"]), r.get("ts")
    except Exception as e:                                # noqa: BLE001
        b["error"] = "ledger unreadable (%s: %s)" % (type(e).__name__, e)
        return b
    if nav is None:
        b["error"] = "no NAV in ledger and live status empty — book UNREADABLE"
        return b
    rate, fxerr = _usd_rates(["AUD"])
    f = rate.get("AUD")
    if f is None:
        b["error"] = "no AUD/USD rate (%s) — book UNREADABLE" % (fxerr or "rate missing")
        return b
    b["equity_usd"] = nav * f
    b["asof"] = ts
    b["stale"] = not live
    b["equity_basis"] = "last ledger NAV %s AUD @ %s ×%.6f AUD→USD" % (round(nav, 2), ts, f)
    if not live:
        b["warnings"].append(
            "oanda_status.txt is EMPTY (sustained 401) — equity is a STALE ledger "
            "NAV, positions are UNREADABLE and contribute ZERO measured exposure")
    if os.path.exists(os.path.join(OANDA_DESK, "HALT")):
        b["warnings"].append("HALT file present on the OANDA desk")
    return b


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# CM-057: DOCUMENTED BASELINES for books whose ledger carries no `start` field.
# Six books ($148,348 of equity, ~10% of the firm) could not be scored AT ALL --
# their P&L was simply unstateable. The four sleeves predate the `start` field
# that `tournament.load_sleeve` now writes; the two weights books never had one.
#
# Every figure is cited to an AUTHORITATIVE source -- a code constant or a frozen
# registration -- and NEVER inferred from current equity, which would be circular:
# a book that lost money would be handed a smaller "baseline" and appear flat.
# The ledgers are NOT mutated: they are registered artefacts, and a baseline the
# aggregator needs belongs in the aggregator.
# ---------------------------------------------------------------------------
DOCUMENTED_START_USD = {
    "tour_fng_momentum":     (10000.0, "tournament.py START = 10000.0; no SLEEVE_START override"),
    "growth_book":           (50000.0, "GROWTH_BOOK_PREREGISTRATION.md 5: 'Start: $50,000'"),
    "value_book":            (50000.0, "VALUE_BOOK_PREREGISTRATION.md 4: 'Start $50,000'"),
    "kraken_floor":         (120000.0, "kraken_floor.py START_CASH 40000.0 x 3 desks"),
    "replica_floor":        (120000.0, "replica_floor.py START_CASH 40000.0 x 3 desks"),
}


def _apply_documented_starts(books):
    """Fill start_usd ONLY where the ledger supplied none. A ledger that states
    its own start always wins -- this never overrides a book's own record."""
    for b in books:
        if b.get("start_usd") or b.get("error"):
            continue
        ent = DOCUMENTED_START_USD.get(b.get("key"))
        if not ent:
            continue
        b["start_usd"] = ent[0]
        b["start_source"] = "DOCUMENTED: " + ent[1]
    return books


def registry():
    """Every book with live capital. Tournament sleeves are globbed, so a new
    sleeve is covered the moment it writes a ledger — coverage cannot silently
    fall behind the tournament again."""
    r = [
        # growth_book ARCHIVED 20260919-191449 — residual → GCL
        ("value_book", "Value Book", "weights",
         os.path.join(BASE, "reports", "value_book.json"), "USD"),
    ]
    out = [_load_weights_book(*x) for x in r]
    for p in sorted(glob.glob(os.path.join(BASE, "reports", "tournament", "*.json"))):
        k = os.path.splitext(os.path.basename(p))[0]
        out.append(_load_weights_book("tour_" + k, "Sleeve: " + k, "sleeve", p, "USD"))
    out.append(_load_desk_floor("kraken_floor", "Kraken Floor", "floor",
                                os.path.join(KRAKEN_DESK, "kraken_bot", "kraken_floor.json")))
    out.append(_load_desk_floor("replica_floor", "Venue Replica Floor", "floor",
                                os.path.join(BASE, "replica_floor.json")))
    from coinspot_retirement import verified_retirement
    if not verified_retirement(BASE, KRAKEN_DESK):
        out.append(_load_desk_floor("coinspot_floor", "CoinSpot AU Floor", "floor",
                                    os.path.join(KRAKEN_DESK, "reports", "coinspot_floor.json"),
                                    "AUD", COINSPOT_MAP))
    out.append(_load_desk_floor("cross_asset_floor", "Cross-Asset Floor", "floor",
                                os.path.join(KRAKEN_DESK, "reports", "cross_asset_floor.json"),
                                "USD", CROSS_ASSET_MAP))
    out.append(_load_carry_paper("carry_paper_book", "Carry Paper Desk",
                                 os.path.join(BASE, "reports", "carry_paper_book.json")))
    out.append(_load_simple_probe("carry_newnames_probe", "Carry New-Names Probe",
                                  os.path.join(BASE, "reports", "carry_newnames_probe.json")))
    _cr = _load_crypto_rumour("crypto_rumour", "Crypto Rumour-Rider",
                              os.path.join(BASE, "reports",
                                           "crypto_rumour_book.json"))
    if _cr is not None:
        out.append(_cr)
    _cx = _load_convexity("convexity_sleeve", "Convexity Sleeve — 21x shot",
                          os.path.join(BASE, "reports", "convexity_sleeve.json"))
    if _cx is not None:
        out.append(_cx)
    # floor_exit_shadow ARCHIVED 20260919-191449 — shadow not firm capital
    _fb = _load_fundamental("fundamental_book", "TFO Fundamental Desk",
                            os.path.join(BASE, "reports", "fundamental_book.json"))
    if _fb is not None:                              # CM-048: absent != error
        out.append(_fb)
    # worldwide_gap_book ARCHIVED 20260919-191449 — residual → GCL
    # GAP-CLOSE LEVERED — funder hard rule 2026-09-16 residual cash → TQQQ/SOXL
    out.append(_load_weights_book(
        "gap_close_levered_book", "Gap-Close Levered Sleeve", "weights",
        os.path.join(BASE, "reports", "gap_close_levered_book.json"), "USD"))
    # CRYPTO CLIP BOOK — idle-cash BTC/ETH/SOL opportunistic beta (2026-09-18)
    out.append(_load_weights_book(
        "crypto_clip_book", "Crypto Clip Book — UNI/ARB/NEAR/SOL", "weights",
        os.path.join(BASE, "reports", "crypto_clip_book.json"), "USD"))
    out.append(_load_weights_book(
        "soxl_weekend_probe", "SOXL weekend/Monday probe", "weights",
        os.path.join(BASE, "reports", "soxl_weekend_probe.json"), "USD"))
    out.append(_load_weights_book(
        "crypto_mid_clip_book", "Mid clip — LINK/TAO/HYPE", "weights",
        os.path.join(BASE, "reports", "crypto_mid_clip_book.json"), "USD"))
    out.append(_load_weights_book(
        "crypto_weekend_rs_probe", "Weekend RS probe — XMR/VVV", "weights",
        os.path.join(BASE, "reports", "crypto_weekend_rs_probe.json"), "USD"))
    out.append(_load_weights_book(
        "crypto_uni_sui_rs", "UNI/SUI RS clip", "weights",
        os.path.join(BASE, "reports", "crypto_uni_sui_rs.json"), "USD"))
    # HEAD TRADER crypto clips on Kraken LIVE tape (Brett mandate 2026-09-25)
    out.append(_load_weights_book(
        "tfo_ht_crypto", "HT Crypto Clips (Kraken paper)", "weights",
        os.path.join(BASE, "reports", "tfo_ht_crypto.json"), "USD"))
    out.append(_load_weights_book(
        "crypto_liq_beta_book", "Liq beta — BTC/ETH/XRP", "weights",
        os.path.join(BASE, "reports", "crypto_liq_beta_book.json"), "USD"))
    out.append(_load_weights_book(
        "crypto_avax_ondo_ltc", "Liq clip B — AVAX/ONDO/LTC", "weights",
        os.path.join(BASE, "reports", "crypto_avax_ondo_ltc.json"), "USD"))
    # TREND18 - DEEP18 levered risk-parity trend sleeve (Brett 2026-10-03 17:37 AEST; PAPER).
    # Ledger state/trend18.json (trend18.py). Funded only by paired capital_transfer with powder.
    # Registered once its ledger exists (absent != error, CM-048); after that a missing ledger is an error.
    _t18 = os.path.join(BASE, "state", "trend18.json")
    if os.path.exists(_t18):
        out.append(_load_weights_book(
            "trend18", "DEEP18 Trend Sleeve - UPRO/TQQQ/TMF/UGL/BTC", "sleeve", _t18, "USD"))
    # VETERAN DESK - 10 seat books x A$500k fresh funder seed (Brett 2026-10-28 16:49/16:57/17:06 WITA; PAPER).
    # Ledger reports/veteran_desk_book.json (veteran_desk/book.py). Arms only Brett-ticked pipeline setups.
    _vd = os.path.join(BASE, "reports", "veteran_desk_book.json")
    if os.path.exists(_vd):
        out.append(_load_weights_book(
            "veteran_desk", "Veteran Desk - 10 seat books (Marcus Hale desk)", "sleeve", _vd, "USD"))
    _short = _load_short_pilot(
        "crypto_sol_short_pilot", "SOL short pilot — SIMULATED ONLY, access unverified",
        os.path.join(BASE, "reports", "crypto_sol_short_pilot.json"))
    if _short is not None:
        out.append(_short)
    # SPECULATIVE SLEEVE — $50k of NEW capital, funder-directed 2026-08-28.
    # Registered the same day it opened. A book the firm-wide kill cannot see is
    # a book the firm-wide kill cannot stop: coverage_audit has caught three live
    # books missing from "firm-wide" before, and this one is not going to be the
    # fourth. Same loader — its ledger carries mark_usd and units already in USD.
    _fs = _load_fundamental("fundamental_spec_book",
                            "TFO Fundamental Desk — Speculative Sleeve",
                            os.path.join(BASE, "reports",
                                         "fundamental_spec_book.json"))
    if _fs is not None:
        out.append(_fs)
    # DECOMMISSIONED 2026-08-28 (funder-directed: "remove both desks entirely").
    # The OANDA Book is no longer a book of this firm. `_load_oanda` is kept
    # below, unreferenced, because the practice account it read STILL HOLDS
    # POSITIONS this desk could not close (sustained 401 since 2026-08-27 18:12,
    # and a logged close there is a known no-op) — deleting the reader would
    # destroy the only description of what was left behind. See
    # DECOMMISSION_20260828.md.
    # --- CM-044: three LIVE books that were missing from "firm-wide" entirely ---
    out.append(_load_engine_bot("kraken_paper_bot", "Kraken Engine (paper bot)",
                                os.path.join(KRAKEN_DESK, "kraken_bot",
                                             "bot_state.json")))
    out.append(_load_maker_probe("maker_calibration_probe",
                                 "Maker Calibration Probe",
                                 os.path.join(BASE, "reports",
                                              "maker_calibration.json")))
    # DECOMMISSIONED 2026-08-28 (funder-directed). Its one open short was closed
    # at the live ask FIRST — realised -$3,933.34, final equity $36,062.66 vs a
    # $40,000 start (-9.84%) — so the loss it had been hiding at cost since
    # 2026-08-05 landed in the firm record instead of leaving with the book.
    # Verified zero-balance archives (book_archive.py) leave the registry only
    # while their ledgers re-verify; firm_capital_flows carries their basis.
    import book_archive
    archived = book_archive.verified_archives()
    out = [b for b in out if b.get("key") not in archived]
    return _apply_documented_starts(out)


# ---------------------------------------------------------------------------
# CM-044 structural fix: the registry above is hand-written for everything
# outside reports/tournament/. A hand-written list is exactly how three live
# books stayed invisible. This walks both desk trees for ledger-shaped JSON and
# LOUDLY reports any file no registered book claims. It cannot auto-register
# (that would guess at a shape and risk double-counting) — it makes the gap
# impossible to miss instead of impossible to see.
# ---------------------------------------------------------------------------
LEDGER_HINTS = ("cash", "positions", "holdings", "desks", "equity", "nav",
                "marked_equity", "start_cash")
AUDIT_SKIP = ("backup", "/corpus/", ".bak", "snapshot", "_history",
              "shadow", "/tmp/", "preregistration", "/archive/",
              "archive/",
              # Revision rollback trees are archival copies of live ledgers
              # (reports/revisions/rollback/*). They are NOT new capital.
              # Funder FIX ALL 2026-09-18 — desk-fix-all repair.
              "/revisions/rollback/", "rollback/",
              # deliberately quarantined ledgers — a book someone renamed to
              # "do-not-use" is a DECISION, not an uncovered book.
              "do-not-use", "missized", ".quarantine", ".broken")
# Directory names that are by definition NOT live books. `archive_YYYYMMDD_*`
# dirs hold dated copies of bot_state.json and would otherwise drown the audit
# in 150+ false positives — and an audit that cries wolf is an audit that gets
# ignored, which is the exact failure mode this is here to prevent.
AUDIT_SKIP_DIRS = ("engineering_updates", "staging", "candidate", "archive",
                   "archives", "corpus", "backups", "old", "deprecated",
                   # Disposable Codex cutover copies are isolated snapshots,
                   # never live books.  Their repo/reports trees contain exact
                   # copies of registered ledgers and must not appear as new
                   # capital in the firm-wide coverage audit.
                   "codex_cutover_")
# Report/telemetry artefacts that are ledger-SHAPED but hold no capital.
# Books the FUNDER has retired. The audit's own advice has always been "register
# it in registry() or record a funder decision to retire it" — this is where that
# second option lives. A retired ledger is reported as RETIRED with its date and
# basis, never as UNCLAIMED and never silently skipped: an orphaned ledger and a
# deliberately closed book look identical on disk, and only one of them is fine.
RETIRED_LEDGERS = {
    os.path.join(KRAKEN_DESK, "kraken_bot", "meanrev_desk.json"): {
        "book": "Kraken Mean-Reversion Desk",
        "retired": "2026-08-28",
        "basis": 'funder direction: "remove both desks entirely"',
        "final": "closed its PUMP/USD short at the live ask first — realised "
                 "-$3,933.34, final equity $36,062.66 vs $40,000 (-9.84%)",
        "record": "DECOMMISSION_20260828.md"},
    os.path.join(OANDA_DESK, "ledger.jsonl"): {
        "book": "OANDA Book",
        "retired": "2026-08-28",
        "basis": 'funder direction: "remove both desks entirely"',
        "final": "practice account still held UNCLOSEABLE positions (sustained "
                 "401); removing the book stopped us measuring that exposure, "
                 "it did not flatten it",
        "record": "DECOMMISSION_20260828.md"},
}

AUDIT_SKIP_FILES = ("fundamental_observability.json", "firm_risk.json", "firm_risk_state.json", "risk_report.json",
                    "kraken_journal.json", "book_peaks.json", "carry_monitor.json",
                    "cross_desk_allocation.json", "desk_report.json")
# A live book gets WRITTEN. Anything untouched for this long is dormant history,
# not a hidden live book — the one genuinely-dormant book (meanrev) is
# registered explicitly rather than left to this heuristic.
AUDIT_MAX_AGE_DAYS = 7
# TFO V2 (funder-authorised 2026-09-25) is a SEPARATE paper fund with its own firm rails and
# equity series; it is deliberately NOT a V1 book and NOT V1 firm capital. All of its files live
# under reports/v2/, which the V1 coverage audit skips by exact path (nothing else is skipped).
V2_FUND_DIR = os.path.realpath(os.path.join(BASE, "reports", "v2"))

# Inspected read-only evidence, not funded books. A changed file is audited again.
AUDIT_EVIDENCE_SHA256 = {
    # Retrospective archive review, 2026-09-24: dated copies of registered books.
    # This classifies files only; it does not assert the old repairs were applied.
    'reports/repairs/fund-capital-identity-20260919-172827/fundamental_book.json': '3eb04d6b4c7a968c1425b064a910b56726c05690f874d79e07e3c61aa84bf565',
    'reports/repairs/fund-capital-identity-20260919-172850/fundamental_book.json': '1e0c879475498fa4a478606217d531b50eebbb0563837e1e9df8d0243421914e',
    'reports/repairs/clip-seed-basis-20260919-172949/crypto_clip_book.json': 'ace5307bc54b6afd640dffa7a58363c69caad58822219eedd906a458c9c6ca89',
    'reports/repairs/mid-clip-seed-restore-revert-20260919-213101/crypto_mid_clip_book.json': '7c5401d75ff9d7912b4daede07050135309f81f658d4e558d31d64d620138b81',
    'reports/repairs/weekend-rs-stop-pair-20260920-043148/gap_close_levered_book.json': '29fec994d599031bd965bda87fbdca7e90df08d078d5a9e93565e5521d07f5f6',
    'reports/repairs/weekend-rs-stop-pair-fix-20260920-043231/gap_close_levered_book.before.json': '29fec994d599031bd965bda87fbdca7e90df08d078d5a9e93565e5521d07f5f6',
    'reports/repairs/gcl-seed-follow-halfbank-20260920-103740/gap_close_levered_book.json': '4f494e7a18fa2dfc4927a4fc9ef8a90612f38aea2fea5461b7872d711eed52be',
    'reports/exit_audit_20260921/floor-audit.json':
        'c7acb427b526616604e37e7ada654cff02af6d5f82fa214ca0efdd0f7d0a996d',
    # 2026-10-03 (dashboard follow-up): research copy of trend18 status output written 19:14 AEST. Inspected:
    # cash/equity/funded 0.00, every holding qty 0.00 (the sleeve is unfunded until the Mon 5 Oct close). The
    # live trend18 ledger is the registered `trend18` book. Any change to this file re-enters the audit.
    'research/deep18/trend18_status_sample.json':
        'f54367d4033250b31ef8cd66e343fac6147bfdb2ee5b76d787948c35db057487',
}


# 2026-10-28 Brett 20:01 WITA (t1671u): dated sandbox/research COPIES, not live books. Exact relative
# prefixes only (no substring match); reported as IGNORED, never silently. Nothing is deleted or moved.
AUDIT_IGNORED_PATHS = {
    "research/full_audit_20261027/sandbox/": "27 Oct full-audit sandbox copy of live ledgers",
    "work/hunt_review_fixes_20261027/sbx/": "27 Oct hunt-review sandbox copy of live ledgers",
    "research/oanda_setups_20261027/scan.json": "27 Oct OANDA setups research scan output",
    "state/hunt48_short_gpt/snap/static/swing_u4_status.json": "frozen 24 Oct copy of swing U4 status inside hunt48 snapshot (Brett 20:05 t1672u)",
    "work/amzn_debate_2227/": "30 Oct 22:27 AMZN debate scratch: before/after copies of VD positions, not a book",
    "work/amzn_debate_2302/": "30 Oct 23:02 AMZN debate scratch: before/after copies of VD positions, not a book",
}


def _audit_ignored(full):
    rel = os.path.relpath(os.path.abspath(full), os.path.abspath(BASE))  # the copy's own location, not a symlink target
    for prefix, why in AUDIT_IGNORED_PATHS.items():
        if rel == prefix or (prefix.endswith("/") and rel.startswith(prefix)):
            return why
    return None


# 2026-10-28 Brett 20:01 WITA (t1671u): live files REGISTERED to their owning books. These books are
# deliberately OUTSIDE V1 firm capital (each file/registration says in_firm_equity False, or it holds no
# capital), so they are claimed here rather than added to registry(): adding them would double-count or
# put separate paper books into firm equity against their own registrations.
SEPARATE_BOOK_LEDGERS = {
    "state/trend_core.json": ("trend_core", "Trend Core A$1m paper book (trend_core.py)", "TREND_CORE_REGISTRATION.md"),
    "static/trend_core_status.json": ("trend_core", "Trend Core status output (trend_core.py)", "TREND_CORE_REGISTRATION.md"),
    "state/essentials.json": ("essentials", "Essentials paper book (essentials_book.py)", "ESSENTIALS_BOOK_REGISTRATION.md"),
    "static/essentials_status.json": ("essentials", "Essentials status output (essentials_book.py)", "ESSENTIALS_BOOK_REGISTRATION.md"),
    "static/swing_u4_status.json": ("tfo_swing_us_u4", "TFO-SWING-US-U4 status (symlink to ~/.tfo_swing_us/status.json)", "TFO_SWING desk"),
    "state/oanda_aggro/stats.json": ("oanda_aggro", "OANDA practice AGGRO sleeve stats (oanda_aggro.py)", "oanda_aggro.py"),
    "state/theme_exposure.json": ("theme_exposure", "Theme exposure warn-only overlay, no capital (theme_exposure.py)", "theme_exposure.py"),
    # 2026-10-30 Brett 19:16 WITA: Taylor Hedge Fund is a SEPARATE desk with its own fresh paper book (not V1 firm capital).
    "state/thf/thf_book.json": ("thf", "Taylor Hedge Fund separate paper book (THF desk)", "Brett 30 Oct 2026 19:16 WITA order"),
}


def _separate_book(full):
    rel = os.path.relpath(os.path.abspath(full), os.path.abspath(BASE))
    return SEPARATE_BOOK_LEDGERS.get(rel)


def _verified_audit_evidence(path):
    full = os.path.abspath(path)
    expected = AUDIT_EVIDENCE_SHA256.get(os.path.relpath(full, BASE))
    if not expected or os.path.realpath(full) != full:
        return False
    try:
        with open(full, 'rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest() == expected
    except OSError:
        return False


def _verified_repair_snapshot(path):
    """A specific, hash-receipted repair copy is archival, not new capital.

Do not skip the repairs directory. New/unlisted files, edited snapshots, missing
receipts and symlinks remain in the normal unclaimed-ledger audit.
"""
    try:
        full = os.path.abspath(path)
        repairs = os.path.abspath(os.path.join(BASE, "reports", "repairs"))
        if os.path.commonpath([full, repairs]) != repairs or full == repairs:
            return False
        if os.path.realpath(full) != full:
            return False
        directory = os.path.dirname(full)
        receipt_path = os.path.join(directory, "receipt.json")
        if os.path.islink(receipt_path):
            return False
        receipt = jf(receipt_path)
        if not isinstance(receipt, dict) or receipt.get("applied") is not True:
            return False
        if receipt.get("backup_directory") != directory:
            return False
        hashes = receipt.get("backup_sha256")
        expected = hashes.get(full) if isinstance(hashes, dict) else None
        if not isinstance(expected, str) or len(expected) != 64:
            return False
        digest = hashlib.sha256()
        with open(full, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == expected
    except (OSError, ValueError, TypeError):
        return False


def coverage_audit(books=None, verbose=True):
    """Return (unclaimed, claimed_paths). Never raises — an audit that can
    break the tick is an audit that gets removed."""
    books = books if books is not None else registry()
    from coinspot_retirement import verified_retirement
    coinspot_retired = verified_retirement(BASE, KRAKEN_DESK)
    claimed = set()
    for b in books:
        try:
            claimed.add(os.path.realpath(b.get("path") or ""))
        except Exception:
            pass
    unclaimed = []
    for root in (BASE, KRAKEN_DESK):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".")
                           and d not in ("node_modules", "venv", "__pycache__")]
            dirnames[:] = [d for d in dirnames
                           if not any(d.lower().startswith(sk)
                                      for sk in AUDIT_SKIP_DIRS)]
            dirnames[:] = [d for d in dirnames
                           if os.path.realpath(os.path.join(dirpath, d)) != V2_FUND_DIR]
            if os.path.realpath(dirpath) == V2_FUND_DIR:
                continue
            low_dir = dirpath.lower()
            if any(sk in low_dir + "/" for sk in AUDIT_SKIP):
                continue
            if any(part.lower().startswith(sk)
                   for part in dirpath.split(os.sep) for sk in AUDIT_SKIP_DIRS):
                continue
            for fn in filenames:
                if not fn.endswith(".json") or fn in AUDIT_SKIP_FILES:
                    continue
                full = os.path.join(dirpath, fn)
                if any(sk in full.lower() for sk in AUDIT_SKIP):
                    continue
                if os.path.realpath(full) in claimed:
                    continue
                if _audit_ignored(full):
                    continue
                if _separate_book(full):
                    continue
                if _verified_repair_snapshot(full) or _verified_audit_evidence(full):
                    continue
                try:
                    if os.path.getsize(full) > 8_000_000:
                        continue
                    d = jf(full)
                except Exception:
                    continue
                if not isinstance(d, dict):
                    continue
                if not any(h in d for h in LEDGER_HINTS):
                    continue
                # ledger-SHAPED. Is there anything actually in it?
                cash = d.get("cash") or d.get("marked_equity") or d.get("nav") or 0
                pos = d.get("positions") or d.get("holdings") or d.get("desks") or {}
                try:
                    cash = float(cash)
                except Exception:
                    cash = 0.0
                if not cash and not pos:
                    continue
                try:
                    age_d = (time.time() - os.path.getmtime(full)) / 86400.0
                except Exception:
                    age_d = 0.0
                if age_d > AUDIT_MAX_AGE_DAYS:
                    continue
                rec = RETIRED_LEDGERS.get(os.path.realpath(full)) or \
                    RETIRED_LEDGERS.get(full)
                if (coinspot_retired and not os.path.islink(full)
                        and full == os.path.join(KRAKEN_DESK, "reports", "coinspot_floor.json")):
                    rec = {"book": "CoinSpot AU Floor", "retired": "2026-09-24",
                           "basis": "funder-directed PAPER liquidation and retirement",
                           "record": "reports/coinspot_retirement.json"}
                unclaimed.append({"path": full, "cash_like": cash,
                                  "n_entries": len(pos) if hasattr(pos, "__len__") else 0,
                                  "mtime": os.path.getmtime(full),
                                  "retired": rec})
    retired = [u for u in unclaimed if u.get("retired")]
    unclaimed = [u for u in unclaimed if not u.get("retired")]
    if verbose and retired:
        for u in retired:
            r = u["retired"]
            print("   COVERAGE AUDIT: RETIRED %s — %s, %s (%s)"
                  % (r["book"], r["retired"], r["basis"], r["record"]))
    if verbose and unclaimed:
        print("   COVERAGE AUDIT (CM-044): %d ledger-shaped file(s) NOT claimed "
              "by any registered book:" % len(unclaimed))
        for u in sorted(unclaimed, key=lambda x: -x["cash_like"])[:25]:
            print("      UNCLAIMED %-58s cash~%12.2f entries=%d"
                  % (u["path"].replace(BASE, ".").replace(KRAKEN_DESK, "~kqd"),
                     u["cash_like"], u["n_entries"]))
        print("      -> register it in book_coverage.registry() or record a "
              "funder decision to retire it. Do NOT ignore.")
    elif verbose:
        print("   COVERAGE AUDIT (CM-044): clean — every ledger-shaped file is "
              "claimed by a registered book.")
    return unclaimed, claimed


def gather(verbose=True):
    """The firm, once. Returns books + a flat position list + loud errors."""
    books = registry()
    positions, errors, warnings = [], [], []
    firm_eq = firm_eq_live = stale_usd = 0.0
    cost_basis = marked_basis = 0.0
    for b in books:
        if b["error"]:
            errors.append("%s (%s): %s" % (b["label"], b["path"], b["error"]))
            continue
        for w in b["warnings"]:
            warnings.append("%s: %s" % (b["label"], w))
        b["gross_usd"] = sum(abs(p["fallback_usd"]) for p in b["positions"])
        b["net_usd"] = sum(p["fallback_usd"] for p in b["positions"])
        for p in b["positions"]:
            if p["fallback_basis"] == "mark":
                marked_basis += abs(p["fallback_usd"])
            else:
                cost_basis += abs(p["fallback_usd"])
        positions.extend(b["positions"])
        if b["equity_usd"] is not None:
            firm_eq += b["equity_usd"]
            if b["stale"]:
                stale_usd += b["equity_usd"]
            else:
                firm_eq_live += b["equity_usd"]
    if verbose:
        for e in errors:
            print("!! BOOK COVERAGE ERROR: %s" % e)
        for w in warnings:
            print("   coverage warning: %s" % w)
    return {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "books": books, "positions": positions,
        "n_books": len(books), "n_books_ok": len([b for b in books if not b["error"]]),
        "n_positions": len(positions),
        "firm_equity_usd": firm_eq, "firm_equity_live_usd": firm_eq_live,
        "stale_equity_usd": stale_usd,
        "gross_stored_usd": sum(abs(p["fallback_usd"]) for p in positions),
        "stored_marked_usd": marked_basis, "stored_cost_basis_usd": cost_basis,
        "errors": errors, "warnings": warnings,
    }


if __name__ == "__main__":
    g = gather()
    print("=" * 74)
    print("FIRM BOOK COVERAGE  [%s]" % g["generated_utc"])
    print("=" * 74)
    print("  books %d (%d readable) · positions %d"
          % (g["n_books"], g["n_books_ok"], g["n_positions"]))
    for b in sorted(g["books"], key=lambda x: -(x.get("equity_usd") or 0)):
        if b["error"]:
            print("  !! %-26s UNREADABLE — %s" % (b["label"], b["error"]))
            continue
        print("     %-26s equity $%12s  gross $%11s  pos %2d%s"
              % (b["label"], "{:,.0f}".format(b["equity_usd"]),
                 "{:,.0f}".format(b["gross_usd"]), len(b["positions"]),
                 "  [STALE]" if b["stale"] else ""))
    print("  ---- firm equity $%s  (live $%s · stale $%s)"
          % ("{:,.0f}".format(g["firm_equity_usd"]),
             "{:,.0f}".format(g["firm_equity_live_usd"]),
             "{:,.0f}".format(g["stale_equity_usd"])))
    print("  ---- stored position value: marked $%s · cost-basis fallback $%s"
          % ("{:,.0f}".format(g["stored_marked_usd"]),
             "{:,.0f}".format(g["stored_cost_basis_usd"])))
