"""Pure PAPER short accounting; no I/O, quotes, orders, or production integration.

Caller owns contract identity, freshness, verified funding intervals, actual
fees, lot specifications, and atomic persistence under its shared ledger lock.
Prices passed to open/close/mark MUST be validated executable depth VWAPs.
`mark_price` is a cover VWAP, not an exchange midpoint or liquidation trigger.
Funding deltas are signed cash income to the SHORT (positive means received).
Round only presentation: accounting keeps full float precision.
"""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR
import math


def number(value, name, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(name + " must be numeric")
    value = float(value)
    if not math.isfinite(value) or (positive and value <= 0) or (nonnegative and value < 0):
        raise ValueError(name + " is out of range")
    return value


def _fee_rate(bps):
    bps = number(bps, "fee_bps", nonnegative=True)
    if bps >= 10000:
        raise ValueError("fee must be less than notional")
    return bps / 10000


def round_quantity(quantity, lot):
    """Floor to a positive contract lot; never round allocation upward."""
    number(quantity, "quantity", positive=True)
    number(lot, "lot", positive=True)
    quantity, lot = Decimal(str(quantity)), Decimal(str(lot))
    result = (quantity / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
    if result <= 0:
        raise ValueError("quantity is below one lot")
    return float(result)


def depth_vwap(book, side, quantity):
    """Full fill against sorted bids for sell, sorted asks for cover/buy.

    All displayed levels must be valid and the best bid strictly below ask.
    Never extrapolate through insufficient depth or accept a partial fill.
    """
    quantity = number(quantity, "quantity", positive=True)
    if side not in ("sell", "cover", "buy"):
        raise ValueError("side must be sell or cover/buy")
    levels = {}
    for key in ("bids", "asks"):
        rows = book.get(key)
        if not isinstance(rows, (list, tuple)) or not rows:
            raise ValueError("missing " + key)
        clean = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) != 2:
                raise ValueError("depth row must be [price, quantity]")
            clean.append((number(row[0], "price", positive=True),
                          number(row[1], "depth quantity", positive=True)))
        levels[key] = sorted(clean, reverse=(key == "bids"))
    if levels["bids"][0][0] >= levels["asks"][0][0]:
        raise ValueError("locked or crossed book")
    rows = levels["bids" if side == "sell" else "asks"]
    if math.fsum(q for _, q in rows) < quantity:
        raise ValueError("insufficient depth; partial fills forbidden")
    remaining, notional = quantity, 0.0
    for price, available in rows:
        used = min(remaining, available)
        notional += number(used * price, "depth notional", positive=True)
        number(notional, "total notional", positive=True)
        remaining -= used
        if remaining <= 0:
            break
    if remaining > max(1e-12, quantity * 1e-14):
        raise ValueError("incomplete fill")
    return {"quantity": quantity, "vwap": notional / quantity,
            "notional": notional, "side": side}


def _stamp(utc):
    parsed = datetime.fromisoformat(utc.replace("Z", "+00:00"))
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("transfer timestamp must explicitly be UTC")
    return utc


def _transfer(source, destination, amount, utc):
    """Mutate ONLY already-copied ledgers; funding never becomes profit."""
    amount = number(amount, "transfer amount", positive=True)
    _stamp(utc)
    for ledger in (source, destination):
        for key in ("cash", "equity", "seed_usd", "start"):
            number(ledger[key], key)
        if not ledger.get("book_key"):
            raise ValueError("book_key required")
    if source["cash"] + 1e-8 < amount:
        raise ValueError("insufficient cash for transfer")
    event = {"type": "capital_transfer", "utc": utc, "paper_only": True,
             "from": source["book_key"], "to": destination["book_key"]}
    for ledger, sign in ((source, -1), (destination, 1)):
        for key in ("cash", "equity", "seed_usd", "start"):
            ledger[key] += sign * amount
        ledger.setdefault("capital_events", []).append({**event, "usd": sign * amount})


def _remark(book, cover_price):
    """Mutate a copy; equity includes an explicit estimated close fee."""
    q = number(book["quantity"], "quantity", nonnegative=True)
    cover_price = number(cover_price, "cover_price", positive=True)
    collateral = number(book["collateral_usd"], "collateral", nonnegative=True)
    entry = number(book["entry_price"], "entry_price", positive=True)
    if not math.isclose(collateral, q * entry, rel_tol=1e-10, abs_tol=1e-7):
        raise ValueError("collateral/quantity identity failed")
    cash = number(book["cash"], "cash")
    funding = number(book["funding_accrued_usd"], "funding")
    book["mark_price"] = cover_price
    book["unrealised_usd"] = q * (entry - cover_price)
    book["estimated_exit_fee_usd"] = q * cover_price * _fee_rate(book["fee_bps"])
    book["equity"] = (cash + collateral + book["unrealised_usd"] + funding
                      - book["estimated_exit_fee_usd"])
    number(book["equity"], "net liquidation equity")
    book["equity_basis"] = "net liquidation: cash+collateral+short P&L+funding-estimated exit fee"
    book["insolvent"] = book["equity"] < 0
    identity = (book["seed_usd"] + book["realised_usd"] + book["unrealised_usd"]
                + book["funding_total_usd"] - book["costs_paid_usd"]
                - book["estimated_exit_fee_usd"])
    number(identity, "capital identity")
    if not math.isclose(identity, book["equity"], rel_tol=1e-10, abs_tol=1e-7):
        raise ValueError("capital/P&L identity failed")
    return book


def open_short(powder, *, book_key, symbol, funding_usd, quantity, entry_price,
               cover_price, utc, lot, fee_bps=5.0, funding_cap_usd=150000.0):
    """Return (new sleeve, new powder); reserve 100% notional plus entry fee.

    Root must supply a new unique book_key and attest the symbol is a verified
    linear USD perpetual. This pure helper cannot verify symbol or uniqueness.
    """
    funding_usd = number(funding_usd, "funding", positive=True)
    if funding_usd > min(150000.0, number(funding_cap_usd, "cap", positive=True)):
        raise ValueError("funding cap exceeded")
    quantity = number(quantity, "quantity", positive=True)
    rounded = round_quantity(quantity, lot)
    if not math.isclose(rounded, quantity, rel_tol=0, abs_tol=max(1e-12, float(lot) * 1e-9)):
        raise ValueError("quantity not a whole lot")
    quantity = rounded
    entry_price = number(entry_price, "entry_price", positive=True)
    collateral = quantity * entry_price
    fee = collateral * _fee_rate(fee_bps)
    if collateral + fee > funding_usd:
        raise ValueError("full collateral and entry fee exceed funding")
    if not book_key or not symbol or book_key == powder.get("book_key"):
        raise ValueError("distinct book and symbol required")
    book = {"book_key": book_key, "symbol": symbol, "paper_only": True,
            "side": "short", "cash": 0.0, "equity": 0.0,
            "seed_usd": 0.0, "start": 0.0, "capital_events": [],
            "quantity": quantity, "lot": float(lot), "entry_price": entry_price,
            "collateral_usd": collateral, "fee_bps": float(fee_bps),
            "funding_accrued_usd": 0.0, "funding_total_usd": 0.0,
            "realised_usd": 0.0, "realised_funding_usd": 0.0,
            "costs_paid_usd": fee, "deploy_basis_usd": funding_usd,
            "opened_at": utc, "status": "OPEN", "fills": []}
    powder = deepcopy(powder)
    _transfer(powder, book, funding_usd, utc)
    book["cash"] -= collateral + fee
    book["fills"].append({"utc": utc, "side": "sell", "quantity": quantity,
                          "price": entry_price, "fee_usd": fee})
    return _remark(book, cover_price), powder


def mark_short(book, *, cover_price, funding_delta_usd=0.0):
    """Return copy. Caller must prevent duplicated/missing funding intervals."""
    book = deepcopy(book)
    delta = number(funding_delta_usd, "funding delta")
    if book["quantity"] == 0 and delta != 0:
        raise ValueError("cannot accrue funding after full close")
    book["funding_accrued_usd"] += delta
    book["funding_total_usd"] += delta
    return _remark(book, cover_price)


def rail_action(book, *, thesis_stop_price=None):
    """Absolute short thesis stop first; +5% flat outranks +2% half-bank."""
    if number(book["quantity"], "quantity", nonnegative=True) == 0:
        return "CLOSED"
    equity = number(book["equity"], "equity")
    basis = number(book["deploy_basis_usd"], "deploy basis", positive=True)
    if thesis_stop_price is not None:
        stop = number(thesis_stop_price, "thesis stop", positive=True)
        if number(book["mark_price"], "mark", positive=True) >= stop:
            return "STOP"
    if equity <= basis * .95:
        return "STOP"
    if equity >= basis * 1.05:
        return "FULL_BANK"
    if equity >= basis * 1.02:
        return "HALF_BANK"
    return "HOLD"


def close_short(book, powder, *, quantity, exit_price, remaining_cover_price, utc):
    """Return copied (sleeve, powder), bank proceeds and rebase residual only.

    Entry collateral, accrued funding, P&L and exit fee are released pro rata.
    Full close sweeps all positive cash, including opening allocation dust.
    Negative liquidation proceeds consume cash; a remaining deficit is retained
    honestly as insolvency, never erased or funded from powder automatically.
    """
    book, powder = deepcopy(book), deepcopy(powder)
    _stamp(utc)
    quantity = number(quantity, "close quantity", positive=True)
    held = number(book["quantity"], "held quantity", positive=True)
    if quantity > held:
        raise ValueError("cannot close more than held")
    rounded = round_quantity(quantity, book["lot"])
    if not math.isclose(rounded, quantity, rel_tol=0, abs_tol=max(1e-12, book["lot"] * 1e-9)):
        raise ValueError("close quantity not a whole lot")
    quantity = rounded
    price = number(exit_price, "exit_price", positive=True)
    ratio = quantity / held
    released = book["collateral_usd"] * ratio
    funding = book["funding_accrued_usd"] * ratio
    pnl = quantity * (book["entry_price"] - price)
    fee = quantity * price * _fee_rate(book["fee_bps"])
    proceeds = released + funding + pnl - fee
    book["cash"] += proceeds
    book["quantity"] = float(Decimal(str(held)) - Decimal(str(quantity)))
    book["collateral_usd"] -= released
    book["funding_accrued_usd"] -= funding
    book["realised_usd"] += pnl
    book["realised_funding_usd"] += funding
    book["costs_paid_usd"] += fee
    full = quantity == held
    if full:
        book["quantity"] = book["collateral_usd"] = book["funding_accrued_usd"] = 0.0
        book["status"], book["closed_at"] = "CLOSED", utc
    book["fills"].append({"utc": utc, "side": "cover", "quantity": quantity,
                          "price": price, "fee_usd": fee, "price_pnl_usd": pnl,
                          "funding_released_usd": funding})
    _remark(book, remaining_cover_price)
    bank = max(0.0, book["cash"] if full else min(proceeds, book["cash"]))
    if bank > 0:
        _transfer(book, powder, bank, utc)
    _remark(book, remaining_cover_price)
    book["deploy_basis_usd"] = book["equity"] if not full else 0.0
    return book, powder
