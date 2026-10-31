#!/usr/bin/env python3
"""firm_book.py — one PAPER firm book across the desks, and a daily broker recon.

READ ONLY on every ledger. This process opens desk books to read them and writes
exactly two kinds of file: state/firm_book.json (and a static/ copy the war-room
card fetches) and reports/recon_YYYYMMDD.json. It never places an order, never
edits a ledger, and never prints a key.

AUDUSD comes from veteran_desk.book.audusd. When that helper has no live rate,
the FX weekend freeze (tfo_equity_refresh.fx_market_closed: NY Friday 17:00
through Sunday 17:00, plus Dec 25 and Jan 1) may keep the book helper's last
good rate if it is still inside the closed-market window. A missing rate is a
failed conversion. Nothing here invents 0.65.

The reconciler reads OANDA practice, Kraken (public host via kraken_dns /
desk_doh — system DNS on this network sinkholes api.kraken.com) and the IBKR
paper gateway on 127.0.0.1:4002. A broker that cannot be read is UNREACHABLE.
That is not a match.

PR #3's firm-risk service (veteran_desk.exec_speed.firm_risk_service) is queried
when that module is importable. This file does not copy it and does not publish
its state. PR #2's paid-feed files are not imported for their side effects.

Usage:
  python3 firm_book.py                 # build the book and reconcile
  python3 firm_book.py --book-only     # book only; recon status NOT_RUN
  python3 firm_book.py --no-write      # print the summary, write nothing
"""
import argparse
import base64
import datetime as dt
import glob
import hashlib
import hmac
import json
import math
import os
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
UTC = dt.timezone.utc
WITA = dt.timezone(dt.timedelta(hours=8))  # Asia/Makassar, no DST
AUDUSD_SANE = (0.4, 1.2)
CLOSED_MAX_AGE_S = 72 * 3600
QTY_ABS_TOL = 1e-4
QTY_REL_TOL = 1e-4
CASH_ABS_TOL_AUD = 1.0
CASH_REL_TOL = 0.001
IBKR_PAPER_PORT = 4002
IBKR_HOST = "127.0.0.1"
PAPER_ONLY = True

# Official firm total. OANDA practice NAV is the whole practice account (firm_risk
# already refuses to add it). V2 is a separate paper fund under reports/v2/.
IN_FIRM = {
    "main": True, "veteran": True, "thf": True, "trend18": True,
    "kraken_crypto": True, "fundamental": True, "hunts": True,
    "oanda": False, "v2": False,
}
# Which desk's positions are expected at which paper broker.
BROKER_OF_DESK = {
    "main": "ibkr", "veteran": "ibkr", "thf": "ibkr", "trend18": "ibkr",
    "fundamental": "ibkr", "hunts": "ibkr",
    "kraken_crypto": "kraken", "oanda": "oanda", "v2": None,
}
# Cash at the broker is the sum of these desks (hunts do not custody IBKR cash).
CASH_DESKS = {
    "ibkr": ("main", "veteran", "thf", "trend18", "fundamental"),
    "kraken": ("kraken_crypto",),
    "oanda": ("oanda",),
}
_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|api[_-]?secret|secret|token|authorization|bearer|password)\b\s*[=:]\s*\S+")
_OUTPUT_OK = re.compile(
    r"(^|/)((state|static)/firm_book\.json|reports/recon_\d{8}\.json)$")


def _utc(now=None):
    now = now or dt.datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def _wita(now=None):
    return _utc(now).astimezone(WITA)


def _iso(now):
    return _utc(now).strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(v):
    """Finite number. Broker payloads send quantities as strings; those count."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, str):
        try:
            v = float(v.strip())
        except ValueError:
            return None
    if not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        return None
    return float(v)


def redact(text):
    return _SECRET_RE.sub(lambda m: m.group(1) + "=***", str(text))[:400]


def read_json(path):
    """Read one JSON file. Missing -> None. Present but bad -> raises."""
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("ledger is not an object")
    return data


def assert_output_path(path):
    if not _OUTPUT_OK.search(path.replace("\\", "/")):
        raise RuntimeError("refusing to write %s — firm book never writes a ledger" % os.path.basename(path))


def atomic_json(path, payload):
    assert_output_path(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".firm-book-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def weekend_freeze(now):
    """(closed, max_age_s, source). Prefers the desk's own FX freeze helper."""
    try:
        import tfo_equity_refresh as te
        return bool(te.fx_market_closed(now)), float(te.FX_CACHE_MAX_AGE_CLOSED), "tfo_equity_refresh.fx_market_closed"
    except Exception:
        ny = _utc(now).astimezone(_ny_zone())
        if (ny.month, ny.day) in ((12, 25), (1, 1)):
            closed = True
        else:
            wd = ny.weekday()
            closed = wd == 5 or (wd == 4 and ny.hour >= 17) or (wd == 6 and ny.hour < 17)
        return closed, float(CLOSED_MAX_AGE_S), "local_fx_weekend"


def _ny_zone():
    from zoneinfo import ZoneInfo
    return ZoneInfo("America/New_York")


def _sane_audusd(v):
    v = _num(v)
    return v if v is not None and AUDUSD_SANE[0] < v < AUDUSD_SANE[1] else None


def _cache_rate(blob, now, max_age):
    """Pull one last-good AUDUSD out of the shapes the book helpers already write."""
    if not isinstance(blob, dict):
        return None
    candidates = []
    if "v" in blob:
        candidates.append((blob.get("v"), blob.get("utc") or blob.get("quote_utc")))
    if "rate" in blob and "AUDUSD=X" not in blob:
        candidates.append((blob.get("rate"), blob.get("utc") or blob.get("quote_utc")))
    for key in ("AUDUSD=X", "AUDUSD"):
        raw = blob.get(key)
        if isinstance(raw, dict):
            candidates.append((raw.get("rate", raw.get("v")), raw.get("quote_utc") or raw.get("utc")))
        elif raw is not None:
            candidates.append((raw, blob.get("utc") or blob.get("quote_utc")))
    rates = blob.get("rates") if isinstance(blob.get("rates"), dict) else None
    if rates:
        for key in ("AUD", "AUDUSD", "AUDUSD=X"):
            raw = rates.get(key)
            if isinstance(raw, dict):
                candidates.append((raw.get("rate"), raw.get("quote_utc") or raw.get("utc")))
            elif raw is not None:
                candidates.append((raw, None))
    now = _utc(now)
    for rate, stamp in candidates:
        rate = _sane_audusd(rate)
        if rate is None or not stamp:
            continue
        try:
            then = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            if then.tzinfo is None:
                then = then.replace(tzinfo=UTC)
            age = (now - then.astimezone(UTC)).total_seconds()
        except Exception:
            continue
        if 0 <= age <= max_age:
            return rate, age
    return None


def resolve_audusd(now=None, px_fn=None, book_mod=None, caches=None):
    """Live book.audusd, else the weekend-freeze last-good rate. Never a constant."""
    now = _utc(now)
    closed, max_age, freeze_src = weekend_freeze(now)
    err = None
    if book_mod is None:
        try:
            from veteran_desk import book as book_mod
        except Exception as exc:
            book_mod = None
            err = "%s: %s" % (type(exc).__name__, redact(exc))
    if book_mod is not None:
        try:
            rate = _sane_audusd(book_mod.audusd(px_fn=px_fn, t=now))
            if rate is None:
                raise RuntimeError("book.audusd returned a rate outside %s" % (AUDUSD_SANE,))
            return {"ok": True, "rate": rate, "frozen": False, "weekend_freeze": closed,
                    "source": "veteran_desk.book.audusd", "age_s": 0}
        except Exception as exc:
            err = "%s: %s" % (type(exc).__name__, redact(exc))
    if not closed:
        return {"ok": False, "rate": None, "frozen": False, "weekend_freeze": False,
                "source": None, "error": err or "AUDUSD unavailable",
                "note": "FX session open — last-good weekend freeze does not apply"}
    blobs = list(caches or [])
    found = None
    for blob in blobs:
        found = _cache_rate(blob, now, max_age)
        if found:
            break
    if not found:
        return {"ok": False, "rate": None, "frozen": False, "weekend_freeze": True,
                "source": freeze_src, "error": err or "no last-good AUDUSD inside the weekend window",
                "note": "weekend freeze is on; no usable last-good rate (not a fallback constant)"}
    rate, age = found
    return {"ok": True, "rate": rate, "frozen": True, "weekend_freeze": True,
            "source": "weekend_freeze:" + freeze_src, "age_s": round(age, 1),
            "live_error": err}


def load_audusd_caches(base):
    blobs = []
    for rel in ("state/audusd_last_good.json", "reports/fx_last_good.json", "reports/last_marks.json"):
        path = os.path.join(base, rel)
        try:
            blobs.append(read_json(path))
        except Exception:
            continue
    return blobs


def _signed(qty, side):
    q = _num(qty)
    if q is None:
        return None
    s = str(side or "").lower()
    if s in ("short", "sell", "s"):
        return -abs(q)
    return q if s in ("", "long", "buy", "b") else (q if q < 0 or s == "long" else abs(q))


def _pos(desk, sub, symbol, qty, price, price_ccy, value_aud, basis, broker, status="open", extra=None):
    row = {
        "desk": desk, "subbook": sub, "symbol": str(symbol), "qty": qty,
        "price": price, "price_ccy": price_ccy, "value_aud": None if value_aud is None else round(value_aud, 2),
        "value_basis": basis, "broker": broker, "status": status,
    }
    if extra:
        row.update(extra)
    return row


def _to_aud(amount, ccy, audusd):
    amount = _num(amount)
    if amount is None:
        return None
    ccy = (ccy or "USD").upper()
    if ccy == "AUD":
        return amount
    if ccy == "USD":
        if not audusd:
            return None
        return amount / float(audusd)
    return None


def _exposure(positions):
    gross = net = long = short = 0.0
    complete = True
    n = 0
    for p in positions:
        if p.get("status") not in (None, "open"):
            continue
        v = _num(p.get("value_aud"))
        if v is None:
            complete = False
            continue
        n += 1
        gross += abs(v)
        net += v
        if v >= 0:
            long += v
        else:
            short += v
    return {"gross_aud": round(gross, 2), "net_aud": round(net, 2), "long_aud": round(long, 2),
            "short_aud": round(short, 2), "complete": complete, "n_valued": n,
            "n_open": sum(1 for p in positions if p.get("status") in (None, "open"))}


def _empty_desk(key, label, reason):
    return {"key": key, "label": label, "present": False, "complete": False, "in_firm_total": IN_FIRM[key],
            "broker": BROKER_OF_DESK.get(key), "cash_aud": None, "equity_aud": None,
            "positions": [], "subbooks": [], "warnings": [], "error": reason,
            "exposure": _exposure([])}


def _finish_desk(key, label, subbooks, positions, cash_aud, equity_aud, warnings, error, ccy_note):
    present = any(s.get("present") for s in subbooks)
    failed = [s for s in subbooks if s.get("present") and s.get("error")]
    complete = present and not failed and error is None and equity_aud is not None
    if failed and error is None:
        error = "; ".join("%s: %s" % (s["name"], s["error"]) for s in failed)
        equity_aud = None
        complete = False
    return {"key": key, "label": label, "present": present, "complete": complete,
            "in_firm_total": bool(IN_FIRM[key]) and complete,
            "broker": BROKER_OF_DESK.get(key), "cash_aud": None if cash_aud is None else round(cash_aud, 2),
            "equity_aud": None if equity_aud is None else round(equity_aud, 2),
            "cash_note": ccy_note, "positions": positions, "subbooks": subbooks,
            "warnings": warnings, "error": error, "exposure": _exposure(positions)}


def _read_file(path):
    if not os.path.isfile(path):
        return None, None
    try:
        return read_json(path), None
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, redact(exc))


def _usd_equity_cash(data):
    eq = _num(data.get("equity"))
    if eq is None:
        eq = _num(data.get("equity_usd"))
    cash = _num(data.get("cash"))
    if cash is None:
        cash = _num(data.get("cash_usd"))
    return eq, cash


def parse_weights(desk, name, data, audusd, broker):
    """cash / equity / holdings / mark_px_usd. USD unless ccy says AUD."""
    warnings = []
    ccy = str(data.get("ccy") or data.get("currency") or "USD").upper()
    eq, cash = _usd_equity_cash(data)
    if ccy == "AUD":
        eq = _num(data.get("equity_aud")) or eq
        cash = _num(data.get("cash_aud")) or cash
    marks = data.get("mark_px_usd") or data.get("mark_px") or {}
    entry = data.get("entry_px") or {}
    positions = []
    for sym, qty in (data.get("holdings") or {}).items():
        q = _num(qty)
        if not q:
            continue
        px = _num(marks.get(sym))
        basis = "mark"
        if px is None:
            px = _num(entry.get(sym))
            basis = "entry (book stores no mark)"
            if px is None:
                warnings.append("%s has neither mark nor entry — position unvalued" % sym)
                positions.append(_pos(desk, name, sym, q, None, ccy, None, "unvalued", broker))
                continue
            warnings.append("%s valued at entry; book has no mark" % sym)
        val = _to_aud(q * px, "USD" if ccy != "AUD" else "AUD", audusd)
        if ccy == "AUD":
            val = q * px
        if val is None:
            warnings.append("%s not converted (no AUDUSD)" % sym)
        positions.append(_pos(desk, name, sym, q, px, "AUD" if ccy == "AUD" else "USD", val, basis, broker))
    eq_aud = _to_aud(eq, ccy, audusd) if ccy != "AUD" else eq
    cash_aud = _to_aud(cash, ccy, audusd) if ccy != "AUD" else cash
    if eq is None:
        return None, None, positions, warnings + ["no equity field — subbook unmeasured, not zero"], "no equity"
    if eq_aud is None:
        return None, None, positions, warnings, "AUDUSD unavailable — not converted"
    return cash_aud, eq_aud, positions, warnings, None


def parse_positions_dict(desk, name, data, audusd, broker):
    """fundamental / v2 style: positions{sym: {units|qty, side, mark_usd}}."""
    warnings = []
    ccy = str(data.get("ccy") or "USD").upper()
    eq, cash = _usd_equity_cash(data)
    positions = []
    raw = data.get("positions") or {}
    if not isinstance(raw, dict):
        return None, None, [], ["positions is not an object"], "unreadable positions"
    for sym, p in raw.items():
        if not isinstance(p, dict):
            warnings.append("%s position is not an object" % sym)
            continue
        if str(p.get("status") or "open").lower() in ("closed", "cancelled", "flat"):
            continue
        qty = p.get("units", p.get("qty", p.get("shares")))
        side = p.get("side") or "long"
        q = _signed(qty, side)
        if q is None or q == 0:
            continue
        px = _num(p.get("mark_usd"))
        px_ccy = "USD"
        basis = "mark_usd"
        if px is None:
            px = _num(p.get("mark"))
            px_ccy = p.get("entry_ccy") or p.get("ccy") or ccy
            basis = "mark"
        if px is None:
            warnings.append("%s has no mark — unvalued, not marked at cost" % sym)
            positions.append(_pos(desk, name, sym, q, None, px_ccy, None, "unvalued", broker))
            continue
        if str(px_ccy).upper() in ("USD", "AUD"):
            val = _to_aud(abs(q) * px, px_ccy, audusd)
            if val is not None and q < 0:
                val = -val
        else:
            # Local quote without a published USD mark. Do not treat it as USD.
            val = None
            warnings.append("%s quote %s has no USD mark — unvalued" % (sym, px_ccy))
        positions.append(_pos(desk, name, sym, q, px, px_ccy, val, basis, broker))
    eq_aud = _to_aud(eq, ccy, audusd)
    cash_aud = _to_aud(cash, ccy, audusd)
    if eq is None:
        return cash_aud, None, positions, warnings, "no equity field — unmeasured, not zero"
    if eq_aud is None:
        return None, None, positions, warnings, "AUDUSD unavailable — not converted"
    return cash_aud, eq_aud, positions, warnings, None


def parse_seats(desk, name, data, audusd, broker):
    """Veteran Desk / THF: seats of cash_aud, equity_aud, positions."""
    warnings = []
    seats = data.get("seats")
    if not isinstance(seats, dict):
        return None, None, [], ["no seats object"], "unreadable seats"
    cash = eq = 0.0
    have_eq = False
    positions = []
    for seat, led in seats.items():
        if not isinstance(led, dict):
            warnings.append("seat %s is not an object" % seat)
            continue
        c = _num(led.get("cash_aud"))
        e = _num(led.get("equity_aud"))
        if c is None and e is None:
            warnings.append("seat %s has no cash_aud or equity_aud" % seat)
        if c is not None:
            cash += c
        if e is not None:
            eq += e
            have_eq = True
        for pid, p in (led.get("positions") or {}).items():
            if not isinstance(p, dict):
                continue
            status = str(p.get("status") or "open").lower()
            if status in ("closed", "cancelled"):
                continue
            if status not in ("open", "armed"):
                continue
            ticker = p.get("ticker") or p.get("symbol") or pid
            q = _signed(p.get("shares") or p.get("qty") or p.get("units"), p.get("side"))
            if q is None:
                continue
            px = _num(p.get("mark") or p.get("fill") or p.get("entry"))
            venue = p.get("venue") or ""
            price_ccy = "AUD" if venue == "ASX" or str(ticker).endswith(".AX") else "USD"
            if venue == "FX" or str(ticker).endswith("=X"):
                fill = _num(p.get("fill") or p.get("entry"))
                if px is None or fill is None or not audusd:
                    val = None
                    warnings.append("%s FX position unvalued" % ticker)
                else:
                    # USD-quote pairs only: P&L in USD / AUDUSD. The notional is not cash.
                    val = q * (px - fill) / float(audusd)
                positions.append(_pos(desk, seat, ticker, q, px, "USD", val, "fx_upl", broker,
                                       "open" if status == "open" else "staged"))
                continue
            if price_ccy != "USD" and price_ccy != "AUD":
                val = None
            elif px is None:
                val = None
                warnings.append("%s has no mark or fill" % ticker)
            elif price_ccy == "AUD":
                val = q * px
            elif not audusd:
                val = None
            else:
                val = q * px / float(audusd)
            st = "open" if status == "open" else "staged"
            if st != "open":
                # Armed risk is not an open position. Keep it out of equity; list it.
                positions.append(_pos(desk, seat, ticker, q, px, price_ccy, None, "armed", broker, "staged"))
                continue
            positions.append(_pos(desk, seat, ticker, q, px, price_ccy, val, "mark_or_fill", broker, "open"))
    if not have_eq:
        return (cash if cash else None), None, positions, warnings, "no seat equity_aud — unmeasured, not zero"
    return cash, eq, positions, warnings, None


def parse_oanda(desk, name, data, audusd, broker):
    warnings = []
    nav = _num(data.get("nav"))
    bal = _num(data.get("balance"))
    positions = []
    for t in data.get("trades") or []:
        if not isinstance(t, dict):
            continue
        qty = _num(t.get("units"))
        if not qty:
            continue
        ins = t.get("instrument") or t.get("ins")
        px = _num(t.get("price"))
        quote = str(ins or "").split("_")[-1]
        val = None
        if px is not None and quote == "AUD":
            val = qty * px
        elif px is not None and quote == "USD" and audusd:
            val = qty * px / float(audusd)
        elif quote not in ("AUD", "USD"):
            warnings.append("%s notional left in %s — not treated as USD" % (ins, quote))
        positions.append(_pos(desk, name, ins, qty, px, quote, val, "notional", broker, "open",
                              {"tfo": bool(t.get("tfo")), "upl_aud": _num(t.get("upl_aud"))}))
    if nav is None:
        return bal, None, positions, warnings, "snapshot has no NAV — unmeasured, not zero"
    return bal, nav, positions, warnings, None


def parse_hunt(desk, name, data, audusd, broker):
    warnings = []
    cash = _num(data.get("cash_aud"))
    if cash is None:
        cash = _to_aud(_num(data.get("cash") or data.get("cash_usd")), "USD", audusd)
    positions = []
    for t in data.get("trades") or []:
        if not isinstance(t, dict):
            continue
        status = str(t.get("status") or "").upper()
        if status not in ("FILLED", "OPEN", "PARTIAL", "TRAILING"):
            continue
        sym = t.get("ticker") or t.get("symbol")
        qty = _signed(t.get("open_qty") or t.get("shares") or t.get("qty"), t.get("direction") or t.get("side"))
        if not sym or qty is None:
            continue
        px = _num(t.get("last_px") or t.get("price") or t.get("entry"))
        ccy = "AUD" if str(sym).endswith(".AX") or t.get("notional_aud") else "USD"
        if t.get("notional_aud") and _num(t.get("entry")) and px:
            # Scale the booked AUD notional by the move and by the fraction still open.
            base_qty = _num(t.get("shares")) or abs(qty)
            frac = abs(qty) / base_qty if base_qty else 1.0
            val = float(t["notional_aud"]) * (px / float(t["entry"])) * frac
            if qty < 0:
                val = -abs(val)
        elif px is None:
            val = None
            warnings.append("%s filled with no price" % sym)
        else:
            val = _to_aud(qty * px, ccy, audusd)
        positions.append(_pos(desk, name, sym, qty, px, ccy, val, "hunt_fill", broker))
    eq = _num(data.get("equity_aud"))
    if eq is None and cash is not None:
        extra = sum(_num(p.get("value_aud")) or 0.0 for p in positions)
        if all(_num(p.get("value_aud")) is not None for p in positions):
            eq = cash + extra
    if eq is None and positions and all(_num(p.get("value_aud")) is not None for p in positions):
        warnings.append("hunt ledger has no cash field — equity is position value only")
        eq = sum(p["value_aud"] for p in positions)
    if eq is None and not positions and cash is None:
        return None, None, [], warnings, None  # empty ledger, present but flat-unknown: treat as flat zero only if trades key exists
    if "trades" in data and eq is None and not positions:
        return 0.0 if cash is None else cash, 0.0 if cash is None else cash, [], warnings, None
    return cash, eq, positions, warnings, None if eq is not None else "hunt equity unmeasured"


def parse_carry(desk, name, data, audusd, broker):
    """AUD carry book. Equity is the ledger's capital identity, not a fresh mark."""
    warnings = []
    seed = _num(data.get("notional_cap_aud"))
    if seed is None:
        seed = _num(data.get("seed_aud"))
    if seed is None:
        return None, None, [], ["carry ledger has no notional_cap_aud"], "unmeasured"
    eq = seed + (_num(data.get("realized_aud")) or 0.0) + (_num(data.get("funding_accrued_aud")) or 0.0) - (_num(data.get("costs_paid_aud")) or 0.0)
    positions = []
    for base, p in (data.get("positions") or {}).items():
        if not isinstance(p, dict):
            continue
        notl = _num(p.get("notional_aud")) or 0.0
        if not notl:
            continue
        move = (_num(p.get("mark_move_pct")) or 0.0) / 100.0
        marked = notl * (1.0 + move)
        positions.append(_pos(desk, name, base + " spot", notl, None, "AUD", marked, "notional_aud", broker))
        positions.append(_pos(desk, name, base + " perp", -notl, None, "AUD", -marked, "notional_aud", broker))
    warnings.append("carry cash is unstated; capital is margin, not a cash balance")
    return None, eq, positions, warnings, None


def parse_floor(desk, name, data, audusd, broker):
    warnings = []
    ccy = str(data.get("ccy") or data.get("currency") or "USD").upper()
    cash = eq = 0.0
    positions = []
    desks = data.get("desks")
    if not isinstance(desks, dict):
        return None, None, [], ["no desks object"], "unreadable floor"
    for dname, d in desks.items():
        if not isinstance(d, dict):
            continue
        c = _num(d.get("cash")) or 0.0
        cash += c
        for sym, p in (d.get("positions") or {}).items():
            if not isinstance(p, dict):
                continue
            q = _signed(p.get("units") or p.get("qty"), p.get("side"))
            if not q:
                continue
            px = _num(p.get("mark") or p.get("entry"))
            if px is None:
                warnings.append("%s unvalued" % sym)
                positions.append(_pos(desk, dname, sym, q, None, ccy, None, "unvalued", broker))
                continue
            val = _to_aud(q * px, ccy, audusd)
            positions.append(_pos(desk, dname, sym, q, px, ccy, val, "mark", broker))
        unreal = _num(d.get("unrealized")) or 0.0
        cost = sum(_num(p.get("cost")) or 0.0 for p in (d.get("positions") or {}).values() if isinstance(p, dict))
        eq += c + cost + unreal
    return _to_aud(cash, ccy, audusd), _to_aud(eq, ccy, audusd), positions, warnings, None


def parse_one(desk, name, data, audusd, broker):
    if not isinstance(data, dict):
        return None, None, [], ["not an object"], "unreadable"
    if name == "carry_paper" or (
            data.get("notional_cap_aud") is not None and "holdings" not in data and "seats" not in data
            and ("funding_accrued_aud" in data or "realized_aud" in data)):
        return parse_carry(desk, name, data, audusd, broker)
    if "seats" in data:
        return parse_seats(desk, name, data, audusd, broker)
    if "nav" in data and "trades" in data:
        return parse_oanda(desk, name, data, audusd, broker)
    if "trades" in data and "holdings" not in data and "seats" not in data:
        return parse_hunt(desk, name, data, audusd, broker)
    if isinstance(data.get("desks"), dict):
        return parse_floor(desk, name, data, audusd, broker)
    if isinstance(data.get("positions"), dict) and data.get("positions"):
        return parse_positions_dict(desk, name, data, audusd, broker)
    if "holdings" in data or "equity" in data or "cash" in data:
        return parse_weights(desk, name, data, audusd, broker)
    if data.get("symbol") and data.get("side") and data.get("paper_only") is True:
        qty = _signed(data.get("units") or data.get("qty") or data.get("size"), data.get("side"))
        px = _num(data.get("mark") or data.get("mark_usd") or data.get("entry"))
        val = None if px is None or qty is None or not audusd else qty * px / float(audusd)
        eq = _to_aud(_num(data.get("equity") or data.get("equity_usd")), "USD", audusd)
        return _to_aud(_num(data.get("cash")), "USD", audusd), eq, [
            _pos(desk, name, data["symbol"], qty, px, "USD", val, "single", broker)
        ] if qty else [], [], None if eq is not None else "no equity"
    return None, None, [], ["unrecognised ledger shape"], "unrecognised ledger shape"


def _subbook(desk, name, path, audusd, broker):
    rel = path
    data, err = _read_file(path)
    if data is None and err is None:
        return {"name": name, "path": rel, "present": False, "error": None}
    if err:
        return {"name": name, "path": rel, "present": True, "error": err, "cash_aud": None, "equity_aud": None}
    cash, eq, positions, warnings, perr = parse_one(desk, name, data, audusd, broker)
    return {"name": name, "path": rel, "present": True, "error": perr, "warnings": warnings,
            "cash_aud": None if cash is None else round(cash, 2),
            "equity_aud": None if eq is None else round(eq, 2),
            "n_positions": len(positions), "_positions": positions}


def _merge(key, label, specs, audusd):
    broker = BROKER_OF_DESK.get(key)
    subbooks = []
    positions = []
    warnings = []
    cash = eq = 0.0
    have_cash = have_eq = cash_gap = False
    for name, path in specs:
        sub = _subbook(key, name, path, audusd, broker)
        positions.extend(sub.pop("_positions", []))
        warnings.extend(sub.get("warnings") or [])
        subbooks.append(sub)
        if sub.get("present") and not sub.get("error"):
            if sub.get("cash_aud") is None:
                cash_gap = True
            else:
                cash += sub["cash_aud"]
                have_cash = True
            if sub.get("equity_aud") is not None:
                eq += sub["equity_aud"]
                have_eq = True
    note = "AUD via book AUDUSD" if audusd else "AUDUSD unavailable"
    # A desk that is missing cash on any present book does not report a partial cash sum.
    desk_cash = cash if have_cash and not cash_gap else None
    return _finish_desk(key, label, subbooks, positions,
                        desk_cash, eq if have_eq else None, warnings, None, note)


def catalog(base):
    """(key, label, [(name, path), ...]) in desk order. Paths may be absent."""
    def p(*parts):
        return os.path.join(base, *parts)
    main = [
        ("value_book", p("reports", "value_book.json")),
        ("gap_close_levered", p("reports", "gap_close_levered_book.json")),
        ("carry_paper", p("reports", "carry_paper_book.json")),
        ("replica_floor", p("replica_floor.json")),
    ]
    for path in sorted(glob.glob(p("reports", "tournament", "*.json"))):
        main.append((os.path.splitext(os.path.basename(path))[0], path))
    crypto_names = (
        "crypto_clip_book", "crypto_small_clip_book", "crypto_mid_clip_book",
        "crypto_weekend_rs_probe", "crypto_uni_sui_rs", "crypto_liq_beta_book",
        "crypto_avax_ondo_ltc", "tfo_ht_crypto", "crypto_rumour_book", "crypto_sol_short_pilot",
    )
    crypto = [(n, p("reports", n + ".json")) for n in crypto_names]
    # External Kraken desk paths are the live Mac ledgers. A test base must not
    # pick them up, or a missing file there would be read as this book's capital.
    own = os.path.abspath(base) == os.path.abspath(BASE)
    floor = os.environ.get("TFO_KRAKEN_FLOOR") or ("/Users/test/Applications/KrakenQuantDesk/kraken_bot/kraken_floor.json" if own else "")
    if floor and os.path.isfile(floor):
        crypto.append(("kraken_floor", floor))
    bot = os.environ.get("TFO_KRAKEN_BOT") or ("/Users/test/Applications/KrakenQuantDesk/kraken_bot/bot_state.json" if own else "")
    if bot and os.path.isfile(bot):
        crypto.append(("kraken_paper_bot", bot))
    hunts = []
    for path in sorted(glob.glob(p("state", "hunt48_*", "ledger.json"))):
        hunts.append((os.path.basename(os.path.dirname(path)), path))
    return [
        ("main", "Main TFO book", main),
        ("veteran", "Veteran Desk", [("veteran_desk", p("reports", "veteran_desk_book.json"))]),
        ("thf", "Taylor Hedge Fund", [("thf", p("state", "thf", "thf_book.json"))]),
        ("trend18", "Trend18", [("trend18", p("state", "trend18.json"))]),
        ("oanda", "OANDA sleeves", [("oanda_practice", p("state", "oanda", "snapshot.json"))]),
        ("kraken_crypto", "Kraken / crypto sleeves", crypto),
        ("fundamental", "Fundamental", [
            ("fundamental_book", p("reports", "fundamental_book.json")),
            ("fundamental_spec", p("reports", "fundamental_spec_book.json")),
        ]),
        ("v2", "V2", [("v2", p("reports", "v2", "tfo_v2_book.json"))]),
        ("hunts", "Hunt books", hunts),
    ]


def _aggro_view(base):
    path = os.path.join(base, "state", "oanda_aggro", "stats.json")
    if not os.path.isfile(path):
        return {"present": False, "in_firm_total": False,
                "note": "AGGRO stats absent. OANDA practice NAV is not added twice."}
    try:
        data = read_json(path)
    except Exception as exc:
        return {"present": True, "error": "%s: %s" % (type(exc).__name__, redact(exc)),
                "in_firm_total": False, "note": "unreadable — not counted"}
    return {"present": True, "in_firm_total": False, "nav_aud": _num(data.get("nav")),
            "n_open": len(data.get("open") or []),
            "note": "Sleeve view of the same OANDA practice account. Not added to the OANDA desk or the firm total."}


def read_firm_risk(base, query=None):
    """Reuse PR #3's service when it is on the tree. Read only — never publish."""
    if query is None:
        try:
            from veteran_desk.exec_speed.firm_risk_service import query as query
        except Exception:
            query = None
    if query is not None:
        try:
            snap = query(path=os.path.join(base, "reports", "firm_risk_service.json"))
        except Exception as exc:
            return {"present": True, "ok": False, "error": type(exc).__name__,
                    "source": "veteran_desk.exec_speed.firm_risk_service.query"}
        return {"present": True, "source": "veteran_desk.exec_speed.firm_risk_service.query", "snapshot": snap}
    path = os.path.join(base, "reports", "firm_risk_service.json")
    if os.path.isfile(path):
        try:
            data = read_json(path)
        except Exception as exc:
            return {"present": True, "ok": False, "error": type(exc).__name__, "source": path}
        return {"present": True, "source": "reports/firm_risk_service.json", "snapshot": data,
                "note": "service module not importable; file was read and not refreshed"}
    return {"present": False, "source": None,
            "note": "firm risk service not on this tree (pending PR #3). No risk snapshot was invented."}


def _legacy_equity(base):
    path = os.path.join(base, "reports", "firm_risk.json")
    try:
        data = read_json(path)
    except Exception:
        return None
    return _num(data.get("firm_equity_usd"))


def build_book(base, audusd_info, now=None, firm_risk_query=None):
    now = _utc(now)
    rate = audusd_info.get("rate") if audusd_info.get("ok") else None
    desks = []
    for key, label, specs in catalog(base):
        if key == "hunts" and not specs:
            desks.append(_empty_desk(key, label, "no hunt48_*/ledger.json on disk"))
            continue
        desks.append(_merge(key, label, specs, rate))
    for d in desks:
        if d["key"] == "oanda":
            d["aggro"] = _aggro_view(base)
            d["warnings"] = list(d.get("warnings") or []) + [
                "Practice-account NAV is not in the firm total (it is the whole account, not sleeve equity)."]
    in_rows = [d for d in desks if d.get("in_firm_total")]
    withheld = [d for d in desks if IN_FIRM.get(d["key"]) and not d.get("in_firm_total")]
    valued = not withheld and audusd_info.get("ok") is True
    def _sum(field):
        vals = [d.get(field) for d in in_rows]
        if any(v is None for v in vals):
            return None
        return round(sum(vals), 2) if vals else (0.0 if valued else None)
    firm_positions = [p for d in in_rows for p in d["positions"]]
    all_positions = [p for d in desks for p in d["positions"]]
    partial_eq, partial_cash = _sum("equity_aud"), _sum("cash_aud")
    firm = {
        # A partial sum of the desks that happened to be valued is not the firm.
        "equity_aud": partial_eq if valued else None,
        "cash_aud": partial_cash if valued else None,
        "partial_equity_aud": partial_eq,
        "exposure": _exposure(firm_positions),
        "complete": valued and all(d.get("exposure", {}).get("complete", True) for d in in_rows),
        "n_desks_in_total": len(in_rows),
        "withheld": [{"desk": d["key"], "error": d.get("error") or "incomplete"} for d in withheld],
        "excluded": [
            {"desk": "oanda", "reason": "OANDA practice NAV is the whole practice account; adding it would invent capital"},
            {"desk": "v2", "reason": "V2 is a separate paper fund and is not V1 firm capital"},
        ],
    }
    shown = [d for d in desks if d.get("equity_aud") is not None]
    firm_shown = {
        "equity_aud": round(sum(d["equity_aud"] for d in shown), 2) if shown else None,
        "note": "Includes OANDA and V2 when those ledgers could be valued. Not the official firm total.",
    }
    legacy = _legacy_equity(base)
    return {
        "paper_only": True, "read_only": True, "writes_ledgers": False,
        "asof_utc": _iso(now), "asof_wita": _wita(now).strftime("%Y-%m-%d %H:%M WITA"),
        "audusd": audusd_info,
        "currency": "AUD",
        "firm": firm,
        "firm_including_separate": firm_shown,
        "legacy_firm_equity_usd": legacy,
        "legacy_note": "reports/firm_risk.json firm_equity_usd, when present. Not added to this total.",
        "firm_risk_service": read_firm_risk(base, query=firm_risk_query),
        "desks": desks,
        "n_positions": len(all_positions),
    }


# --------------------------------------------------------------------------- recon
def install_broker_dns():
    notes = []
    try:
        import kraken_dns
        kraken_dns.install()
        notes.append("kraken_dns")
    except Exception as exc:
        notes.append("kraken_dns_unavailable:%s" % type(exc).__name__)
    try:
        import desk_doh
        desk_doh.install(ibkr=True, oanda=True, extra=False, data=True)
        notes.append("desk_doh")
    except Exception as exc:
        notes.append("desk_doh_unavailable:%s" % type(exc).__name__)
    return notes


def _over(delta, abs_tol, rel_tol, scale):
    return abs(delta) > max(abs_tol, rel_tol * max(abs(scale), 1.0))


def norm_symbol(broker, symbol):
    s = str(symbol or "").upper().strip()
    if broker == "oanda":
        raw = s.replace("/", "").replace("-", "").replace("_", "")
        if len(raw) == 6 and raw.isalpha():
            return raw[:3] + "_" + raw[3:]
        return s.replace("/", "_").replace("-", "_")
    if broker == "kraken":
        s = s.replace("KRAKEN:", "")
        if s.startswith("PF_") and s.endswith("USD"):
            s = s[3:-3]
        else:
            for sep in ("/", "-", "_"):
                if sep in s:
                    s = s.split(sep, 1)[0]
                    break
            else:
                for suf in ("USDT", "ZUSD", "USD"):
                    if s.endswith(suf) and len(s) > len(suf):
                        s = s[: -len(suf)]
                        break
        return {"XBT": "BTC", "XXBT": "BTC", "XDG": "DOGE"}.get(s, s)
    return s


def compare_broker(name, book_positions, book_cash, book_cash_measured, broker):
    """broker is {ok, status, error?, positions:[{symbol,qty}], cash_aud?}."""
    if not broker or broker.get("ok") is not True:
        status = (broker or {}).get("status") or "UNREACHABLE"
        return {"broker": name, "status": status, "matched": False, "breaks": [],
                "error": redact((broker or {}).get("error") or "broker not read"),
                "book_cash_aud": book_cash, "broker_cash_aud": None,
                "note": "unreachable or unread brokers are never a match"}
    if broker.get("status") == "NO_BOOK":
        return {"broker": name, "status": "NO_BOOK", "matched": False, "breaks": [],
                "error": "no ledger for this broker — absence is not a flat book",
                "book_cash_aud": None, "broker_cash_aud": broker.get("cash_aud")}
    bqty, oqty = {}, {}
    for p in book_positions:
        if p.get("status") not in (None, "open"):
            continue
        k = norm_symbol(name, p.get("symbol"))
        if not k:
            continue
        bqty[k] = bqty.get(k, 0.0) + float(p.get("qty") or 0)
    for p in broker.get("positions") or []:
        k = norm_symbol(name, p.get("symbol"))
        if not k:
            continue
        oqty[k] = oqty.get(k, 0.0) + float(p.get("qty") or 0)
    breaks = []
    for sym in sorted(set(bqty) | set(oqty)):
        b, o = bqty.get(sym, 0.0), oqty.get(sym, 0.0)
        if _over(b - o, QTY_ABS_TOL, QTY_REL_TOL, max(abs(b), abs(o))):
            kind = "qty"
            if abs(b) <= QTY_ABS_TOL:
                kind = "broker_only"
            elif abs(o) <= QTY_ABS_TOL:
                kind = "book_only"
            breaks.append({"type": kind, "symbol": sym, "book_qty": round(b, 8), "broker_qty": round(o, 8),
                           "delta": round(b - o, 8)})
    cash_status = "OK"
    broker_cash = _num(broker.get("cash_aud"))
    if not book_cash_measured or book_cash is None or broker_cash is None:
        cash_status = "CASH_UNMEASURED"
        breaks.append({"type": "cash_unmeasured", "book_cash_aud": book_cash, "broker_cash_aud": broker_cash})
    elif _over(book_cash - broker_cash, CASH_ABS_TOL_AUD, CASH_REL_TOL, max(abs(book_cash), abs(broker_cash))):
        cash_status = "CASH_BREAK"
        breaks.append({"type": "cash", "book_cash_aud": round(book_cash, 2), "broker_cash_aud": round(broker_cash, 2),
                       "delta_aud": round(book_cash - broker_cash, 2)})
    matched = not breaks
    return {"broker": name, "status": "MATCH" if matched else "BREAK", "matched": matched,
            "breaks": breaks, "cash_status": cash_status,
            "book_cash_aud": book_cash, "broker_cash_aud": broker_cash,
            "n_book": len(bqty), "n_broker": len(oqty)}


def _book_side(book, broker_name):
    desks = [d for d in book.get("desks") or [] if BROKER_OF_DESK.get(d.get("key")) == broker_name]
    if not desks or not any(d.get("present") for d in desks):
        return [], None, False, True  # positions, cash, measured, no_book
    # Every open row on a desk mapped to this broker is expected at that broker.
    positions = [p for d in desks for p in d.get("positions") or []]
    cash_keys = CASH_DESKS[broker_name]
    cash_desks = [d for d in desks if d.get("key") in cash_keys]
    if any(d.get("present") and d.get("cash_aud") is None for d in cash_desks):
        return positions, None, False, False
    present_cash = [d for d in cash_desks if d.get("present") and d.get("cash_aud") is not None]
    if not present_cash:
        return positions, None, False, False
    return positions, round(sum(d["cash_aud"] for d in present_cash), 2), True, False


def reconcile(book, fetches, now=None):
    """fetches: {oanda, kraken, ibkr} -> broker dicts. Missing fetcher is UNREACHABLE."""
    now = _utc(now)
    out = {"paper_only": True, "date_wita": _wita(now).strftime("%Y%m%d"),
           "asof_utc": _iso(now), "asof_wita": _wita(now).strftime("%Y-%m-%d %H:%M WITA"),
           "tolerance": {"qty_abs": QTY_ABS_TOL, "qty_rel": QTY_REL_TOL,
                         "cash_abs_aud": CASH_ABS_TOL_AUD, "cash_rel": CASH_REL_TOL},
           "brokers": {}, "matched": False}
    for name in ("oanda", "kraken", "ibkr"):
        positions, cash, measured, no_book = _book_side(book, name)
        if no_book:
            live = {"ok": False, "status": "NO_BOOK", "error": "no ledger"}
            # Still record the live read when we have one, but do not call a missing book flat.
            if fetches and fetches.get(name):
                try:
                    live_read = fetches[name]()
                except Exception as exc:
                    live_read = {"ok": False, "status": "UNREACHABLE", "error": redact(exc)}
            else:
                live_read = {"ok": False, "status": "UNREACHABLE", "error": "no fetcher"}
            row = compare_broker(name, [], None, False, {"ok": False, "status": "NO_BOOK", "error": "no ledger"})
            row["live"] = {"ok": bool(live_read.get("ok")), "status": live_read.get("status"),
                           "error": redact(live_read.get("error") or ""),
                           "n_positions": len(live_read.get("positions") or [])}
            row["matched"] = False
            row["note"] = "no desk ledger — not compared, and not a match"
            out["brokers"][name] = row
            continue
        fn = (fetches or {}).get(name)
        if fn is None:
            live = {"ok": False, "status": "UNREACHABLE", "error": "no fetcher"}
        else:
            try:
                live = fn()
            except Exception as exc:
                live = {"ok": False, "status": "UNREACHABLE", "error": redact(exc)}
        if not isinstance(live, dict):
            live = {"ok": False, "status": "UNREACHABLE", "error": "broker returned %s" % type(live).__name__}
        out["brokers"][name] = compare_broker(name, positions, cash, measured, live)
    out["matched"] = all(b.get("matched") is True for b in out["brokers"].values())
    out["n_breaks"] = sum(len(b.get("breaks") or []) for b in out["brokers"].values())
    out["n_unreachable"] = sum(1 for b in out["brokers"].values() if b.get("status") in ("UNREACHABLE", "NO_CREDENTIALS", "BROKER_ERROR", "NO_BOOK"))
    return out


class CredentialsError(RuntimeError):
    pass


def load_kraken_keys(path=None):
    path = path or os.path.expanduser("~/.tfo_secrets/kraken.env")
    if not os.path.isfile(path):
        return None
    vals = {}
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except OSError as exc:
        raise CredentialsError("kraken.env unreadable (%s)" % type(exc).__name__)
    for ln in lines:
        if not ln.strip() or ln.strip().startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        vals[k.strip()] = v.strip().strip('"').strip("'")
    key = vals.get("KRAKEN_API_KEY") or vals.get("API_KEY") or vals.get("KEY")
    sec = vals.get("KRAKEN_API_SECRET") or vals.get("API_SECRET") or vals.get("SECRET")
    if not key or not sec:
        raise CredentialsError("kraken.env is missing the key or secret field")
    return key, sec


def kraken_sign(urlpath, data, secret):
    postdata = urllib.parse.urlencode(data)
    message = urlpath.encode() + hashlib.sha256((str(data["nonce"]) + postdata).encode()).digest()
    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


def _kraken_asset(asset):
    a = str(asset or "").upper()
    if a.startswith("X") and len(a) == 4:
        a = a[1:]
    if a.startswith("Z") and len(a) == 4:
        a = a[1:]
    return {"XBT": "BTC", "XDG": "DOGE"}.get(a, a)


def fetch_kraken(post=None, keyfile=None, audusd=None):
    """Private Balance + OpenPositions. post(path, body, headers) -> parsed JSON. Secrets stay out of errors."""
    try:
        keys = load_kraken_keys(keyfile)
    except CredentialsError as exc:
        return {"ok": False, "status": "NO_CREDENTIALS", "error": str(exc), "positions": []}
    if keys is None:
        return {"ok": False, "status": "NO_CREDENTIALS", "error": "no ~/.tfo_secrets/kraken.env", "positions": []}
    key, secret = keys

    def _default_post(path, body, headers):
        req = urllib.request.Request("https://api.kraken.com" + path, data=urllib.parse.urlencode(body).encode(),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())

    post = post or _default_post
    positions = []
    cash_usd = 0.0
    cash_seen = False
    try:
        for path in ("/0/private/Balance", "/0/private/OpenPositions"):
            nonce = str(int(time.time() * 1000))
            body = {"nonce": nonce}
            if path.endswith("OpenPositions"):
                body["docalcs"] = "false"
            headers = {"API-Key": key, "API-Sign": kraken_sign(path, body, secret)}
            payload = post(path, body, headers)
            if not isinstance(payload, dict):
                return {"ok": False, "status": "BROKER_ERROR", "error": "Kraken returned %s" % type(payload).__name__, "positions": []}
            errs = payload.get("error") or []
            if errs:
                return {"ok": False, "status": "BROKER_ERROR", "error": redact("; ".join(str(e) for e in errs)), "positions": []}
            result = payload.get("result") or {}
            if path.endswith("Balance"):
                stables = {"USD", "USDT", "USDC", "DAI"}
                for asset, qty in result.items():
                    base = _kraken_asset(asset)
                    q = _num(qty) or 0.0
                    if abs(q) <= QTY_ABS_TOL:
                        continue
                    if base in stables:
                        cash_usd += q
                        cash_seen = True
                    else:
                        positions.append({"symbol": base, "qty": q})
            else:
                rows = result.values() if isinstance(result, dict) else result
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    vol = (_num(row.get("vol")) or 0) - (_num(row.get("vol_closed")) or 0)
                    if str(row.get("type") or "").lower() == "sell":
                        vol = -abs(vol)
                    if abs(vol) <= QTY_ABS_TOL:
                        continue
                    positions.append({"symbol": row.get("pair") or row.get("symbol"), "qty": vol})
    except Exception as exc:
        return {"ok": False, "status": "UNREACHABLE", "error": redact(exc), "positions": []}
    cash_aud = None
    if cash_seen and audusd:
        cash_aud = round(cash_usd / float(audusd), 2)
    elif cash_seen and not audusd:
        return {"ok": False, "status": "CASH_UNMEASURED", "error": "Kraken USD cash has no AUDUSD", "positions": positions}
    return {"ok": True, "status": "OK", "positions": positions, "cash_aud": cash_aud if cash_seen else 0.0}


def fetch_oanda(get=None, audusd=None):
    """Practice host only. get(path) -> JSON. Default is oanda_sleeve.api GET."""
    if get is None:
        try:
            import oanda_sleeve as ox
        except Exception as exc:
            return {"ok": False, "status": "UNREACHABLE", "error": "oanda client unavailable (%s)" % type(exc).__name__}
        if not str(getattr(ox, "HOST", "")).startswith("https://api-fxpractice."):
            return {"ok": False, "status": "BROKER_ERROR", "error": "OANDA host is not the practice host"}

        def get(path, _ox=ox):
            return _ox.api("GET", path)

        account = ox.acct
    else:
        account = lambda path="": path

    try:
        summary = get(account("/summary"))
        trades = get(account("/openTrades"))
    except Exception as exc:
        return {"ok": False, "status": "UNREACHABLE", "error": redact(exc), "positions": []}
    try:
        acct = summary.get("account") if isinstance(summary, dict) and "account" in summary else summary
        rows = trades.get("trades") if isinstance(trades, dict) else trades
        if not isinstance(acct, dict) or not isinstance(rows, list):
            return {"ok": False, "status": "BROKER_ERROR", "error": "OANDA payload missing account or trades", "positions": []}
        ccy = str(acct.get("currency") or "")
        bal = _num(acct.get("balance"))
        if ccy == "AUD":
            cash = bal
        elif ccy == "USD" and audusd and bal is not None:
            cash = bal / float(audusd)
        else:
            return {"ok": False, "status": "BROKER_ERROR",
                    "error": "OANDA currency %s is not convertible" % (ccy or "missing"), "positions": []}
        positions = []
        for t in rows:
            if not isinstance(t, dict):
                continue
            qty = _num(t.get("currentUnits"))
            if not qty:
                continue
            positions.append({"symbol": t.get("instrument"), "qty": qty})
        return {"ok": True, "status": "OK", "positions": positions, "cash_aud": None if cash is None else round(cash, 2),
                "currency": ccy}
    except Exception as exc:
        return {"ok": False, "status": "BROKER_ERROR", "error": redact(exc), "positions": []}


def fetch_ibkr(connect=None, port=IBKR_PAPER_PORT, audusd=None):
    """Read-only paper gateway. connect() -> object with positions() and accountValues(), or None on failure."""
    if int(port) != IBKR_PAPER_PORT:
        return {"ok": False, "status": "BROKER_ERROR", "error": "IBKR recon refuses any port other than paper 4002", "positions": []}
    if connect is None:
        def connect():
            from ib_async import IB
            ib = IB()
            ib.connect(IBKR_HOST, IBKR_PAPER_PORT, clientId=91, readonly=True, timeout=8)
            return ib
    try:
        ib = connect()
    except Exception as exc:
        return {"ok": False, "status": "UNREACHABLE", "error": redact(exc), "positions": []}
    if ib is None:
        return {"ok": False, "status": "UNREACHABLE", "error": "IBKR connect returned nothing", "positions": []}
    try:
        raw_pos = ib.positions()
        try:
            values = ib.accountValues()
        except Exception:
            values = ib.accountSummary()
        positions = []
        for p in raw_pos or []:
            contract = getattr(p, "contract", None)
            sym = getattr(contract, "symbol", None) or (p.get("symbol") if isinstance(p, dict) else None)
            qty = getattr(p, "position", None)
            if qty is None and isinstance(p, dict):
                qty = p.get("qty")
            qty = _num(qty)
            if not sym or qty is None or abs(qty) <= QTY_ABS_TOL:
                continue
            local = getattr(contract, "localSymbol", None) or sym
            positions.append({"symbol": local if getattr(contract, "secType", "") == "CASH" else sym, "qty": qty})
        cash = None
        ccy = None
        for tag, currency, value in _iter_account(values):
            if tag in ("TotalCashValue", "CashBalance") and cash is None:
                cash = _num(value)
                ccy = currency
        if cash is None:
            return {"ok": False, "status": "BROKER_ERROR", "error": "IBKR returned no cash value", "positions": positions}
        if ccy == "AUD":
            cash_aud = cash
        elif ccy == "USD" and audusd:
            cash_aud = cash / float(audusd)
        elif ccy == "BASE":
            # IB 'BASE' is the account currency. This paper account is AUD.
            cash_aud = cash
        else:
            return {"ok": False, "status": "BROKER_ERROR", "error": "IBKR cash currency %s is not convertible" % ccy,
                    "positions": positions}
        return {"ok": True, "status": "OK", "positions": positions, "cash_aud": round(cash_aud, 2), "currency": ccy or "AUD"}
    except Exception as exc:
        return {"ok": False, "status": "UNREACHABLE", "error": redact(exc), "positions": []}
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


def _iter_account(values):
    for row in values or []:
        if isinstance(row, dict):
            yield row.get("tag"), row.get("currency"), row.get("value")
        else:
            yield getattr(row, "tag", None), getattr(row, "currency", None), getattr(row, "value", None)


def default_fetches(audusd):
    rate = audusd if isinstance(audusd, (int, float)) else (audusd or {}).get("rate")
    install_broker_dns()
    return {
        "oanda": lambda: fetch_oanda(audusd=rate),
        "kraken": lambda: fetch_kraken(audusd=rate),
        "ibkr": lambda: fetch_ibkr(audusd=rate),
    }


def not_run_recon(now=None):
    return {"paper_only": True, "status": "NOT_RUN", "matched": False,
            "asof_utc": _iso(now), "note": "recon was not run — this is not a match",
            "brokers": {}, "n_breaks": None, "n_unreachable": None}


def run(base, now=None, audusd_info=None, caches=None, book_mod=None, px_fn=None,
        firm_risk_query=None, fetches=None, do_recon=True, write=True, dns=None):
    now = _utc(now)
    if audusd_info is None:
        audusd_info = resolve_audusd(now, px_fn=px_fn, book_mod=book_mod,
                                     caches=caches if caches is not None else load_audusd_caches(base))
    book = build_book(base, audusd_info, now, firm_risk_query=firm_risk_query)
    if do_recon:
        if fetches is None:
            if dns is None:
                book["dns"] = install_broker_dns()
            fetches = default_fetches(audusd_info)
        recon = reconcile(book, fetches, now)
    else:
        recon = not_run_recon(now)
    book["recon"] = {k: recon[k] for k in ("matched", "n_breaks", "n_unreachable", "asof_utc", "brokers") if k in recon}
    book["recon"]["status"] = recon.get("status") or ("MATCH" if recon.get("matched") else "BREAK_OR_UNREAD")
    book["recon"]["date_wita"] = recon.get("date_wita")
    if write:
        payload_book = book
        atomic_json(os.path.join(base, "state", "firm_book.json"), payload_book)
        atomic_json(os.path.join(base, "static", "firm_book.json"), payload_book)
        if do_recon:
            day = recon["date_wita"]
            atomic_json(os.path.join(base, "reports", "recon_%s.json" % day), recon)
    return book, recon


def main(argv=None):
    ap = argparse.ArgumentParser(description="PAPER firm book and broker recon. Writes no ledger.")
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--book-only", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    book, recon = run(args.base, do_recon=not args.book_only, write=not args.no_write)
    firm = book["firm"]
    print("firm book %s equity_aud=%s complete=%s audusd=%s frozen=%s" % (
        book["asof_wita"], firm.get("equity_aud"), firm.get("complete"),
        (book.get("audusd") or {}).get("rate"), (book.get("audusd") or {}).get("frozen")))
    print("recon %s breaks=%s unreachable=%s" % (
        recon.get("status") or ("MATCH" if recon.get("matched") else "BREAK_OR_UNREAD"),
        recon.get("n_breaks"), recon.get("n_unreachable")))
    if not book.get("audusd", {}).get("ok"):
        return 2
    if recon.get("status") == "NOT_RUN":
        return 0
    return 0 if recon.get("matched") else 2


if __name__ == "__main__":
    sys.exit(main())
