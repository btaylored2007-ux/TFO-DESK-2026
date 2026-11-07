"""Isolated paper book for one tenant. Not the owner book."""

import os
import uuid
from decimal import Decimal, ROUND_HALF_EVEN

from customer_platform.errors import InvalidAmount, LiveDisabled, ResetBlocked
from customer_platform.files import (
    append_jsonl,
    ensure_dir,
    iter_jsonl,
    read_bytes,
    read_json,
    write_json,
)
from customer_platform.money import (
    exact_notional,
    money_text,
    parse_fx,
    parse_money,
    parse_signed_money,
)

SAMPLE_SECTORS = {"XOM": "energy", "AAPL": "technology", "JPM": "financials"}


def new_id(prefix):
    return prefix + uuid.uuid4().hex[:16]


class PaperBook:
    def __init__(self, tenant_dir, clock):
        self.dir = os.path.join(tenant_dir, "paper")
        self.state_path = os.path.join(self.dir, "state.json")
        self.ledger_path = os.path.join(self.dir, "ledger.jsonl")
        self.clock = clock

    def now(self):
        return self.clock()

    def init_account(self, *, currency, starting_capital, fixture=False):
        ensure_dir(self.dir)
        if os.path.exists(self.state_path):
            raise ResetBlocked("already_open", "paper account is already open")
        amount = money_text(parse_money(starting_capital, currency), currency)
        epoch_id = new_id("ep_")
        state = {
            "broker_mode": "paper",
            "broker_permissions": {"approval": False, "auto": False, "manual": False},
            "configured_starting_capital": amount,
            "currency": currency,
            "current_epoch_id": epoch_id,
            "epochs": [
                {
                    "capital_balance": amount,
                    "closed_utc": None,
                    "ending_capital": None,
                    "epoch_id": epoch_id,
                    "opened_utc": self.now(),
                    "opening_capital": amount,
                    "session_realized_pnl": money_text(Decimal("0"), currency),
                    "status": "open",
                }
            ],
            "fixture": bool(fixture),
            "live_execution_enabled": False,
            "orders": [],
            "positions": [],
            "sector_opt_out": [],
            "venue": "paper",
            "version": 1,
        }
        write_json(self.state_path, state)
        self._append({
            "amount": amount,
            "currency": currency,
            "epoch_id": epoch_id,
            "fixture": bool(fixture),
            "id": new_id("lg_"),
            "kind": "opening_capital",
            "label": "Configured starting practice capital. Not a profit and not a broker deposit.",
            "not_pnl": True,
            "utc": self.now(),
        })
        return state

    def load(self):
        state = read_json(self.state_path, None)
        if not state:
            raise ResetBlocked("no_account", "paper account is not open")
        return state

    def save(self, state):
        write_json(self.state_path, state)

    def ledger_bytes(self):
        return read_bytes(self.ledger_path)

    def ledger(self):
        return iter_jsonl(self.ledger_path)

    def _append(self, row):
        append_jsonl(self.ledger_path, row)
        return row

    def epoch(self, state):
        for row in state["epochs"]:
            if row["epoch_id"] == state["current_epoch_id"] and row["status"] == "open":
                return row
        raise ResetBlocked("no_epoch", "open paper epoch is missing")

    def assert_paper(self, state):
        if state.get("venue") != "paper" or state.get("live_execution_enabled"):
            raise LiveDisabled("paper reset and paper orders require venue paper and live execution off")

    def equity(self, state):
        """Cash plus marked value. Unmarked positions contribute cost, not an invented price.

        The caller blocks new risk when the result is zero or negative. This function
        does not divide.
        """
        currency = state["currency"]
        cash = parse_signed_money(self.epoch(state)["capital_balance"], currency)
        market = Decimal("0")
        unmarked = False
        for position in state["positions"]:
            if position["status"] != "open":
                continue
            qty = Decimal(position["qty"])
            fx = Decimal(position["fx_to_account"])
            if position.get("mark") is None:
                unmarked = True
                unit = Decimal(position["cost_total"]) / qty
            else:
                unit = parse_money(position["mark"], currency) * fx
            market += unit * qty if position.get("mark") is None else qty * unit
        total = cash + market
        return {
            "amount": money_text(total, currency) if total >= 0 else format_signed(total, currency),
            "unmarked_uses_cost": unmarked,
        }

    def set_starting_default(self, amount):
        state = self.load()
        self.assert_paper(state)
        chosen = money_text(parse_money(amount, state["currency"]), state["currency"])
        state["configured_starting_capital"] = chosen
        self.save(state)
        return state

    def set_opt_out(self, sectors):
        state = self.load()
        self.assert_paper(state)
        clean = []
        for sector in sectors:
            text = str(sector or "").strip().lower()
            if not text or len(text) > 40 or any(ch.isspace() for ch in text):
                raise ResetBlocked("sector", "sector name is not usable")
            clean.append(text)
        state["sector_opt_out"] = sorted(set(clean))
        self.save(state)
        return state

    def place_order(self, *, symbol, side, qty, limit_price, sector=None, idempotency_key, fixture=False):
        state = self.load()
        self.assert_paper(state)
        existing = self._order_idem(state, idempotency_key)
        if existing:
            return existing
        side = str(side or "")
        if side not in {"buy", "sell"}:
            raise ResetBlocked("side", "paper orders are buy or sell-to-close")
        symbol = _symbol(symbol)
        quantity = _qty(qty)
        if quantity <= 0:
            raise InvalidAmount("quantity must be positive")
        price = parse_money(limit_price, state["currency"])
        resolved_sector = _sector(symbol, sector)
        if side == "buy" and resolved_sector in state["sector_opt_out"]:
            raise ResetBlocked("sector_opt_out", "sector is opted out of new entries; open holdings and exits stay available")
        if side == "sell":
            held = self._open_qty(state, symbol)
            if quantity > held:
                raise ResetBlocked("short_blocked", "a sell cannot exceed the open long, and shorts are off")
        else:
            notional = exact_notional(quantity, price, Decimal("1"), state["currency"])
            self._assert_new_risk(state, notional)
        order = {
            "idempotency_key": idempotency_key,
            "limit_price": money_text(price, state["currency"]),
            "order_id": new_id("ord_"),
            "qty": format(quantity, "f"),
            "sector": resolved_sector,
            "side": side,
            "status": "pending",
            "symbol": symbol,
            "utc": self.now(),
        }
        state["orders"].append(order)
        self.save(state)
        return order

    def cancel_order(self, order_id):
        state = self.load()
        self.assert_paper(state)
        for order in state["orders"]:
            if order["order_id"] == order_id and order["status"] == "pending":
                order["status"] = "canceled"
                order["canceled_utc"] = self.now()
                self.save(state)
                self._append({
                    "currency": state["currency"],
                    "epoch_id": state["current_epoch_id"],
                    "id": new_id("lg_"),
                    "kind": "order_cancel",
                    "not_pnl": True,
                    "order_id": order_id,
                    "utc": self.now(),
                })
                return order
        raise ResetBlocked("order", "pending order was not found")

    def fill_order(self, order_id, *, price, fx=None, fixture=False):
        state = self.load()
        self.assert_paper(state)
        order = None
        for candidate in state["orders"]:
            if candidate["order_id"] == order_id and candidate["status"] == "pending":
                order = candidate
                break
        if order is None:
            raise ResetBlocked("order", "pending order was not found")
        currency = state["currency"]
        fill_price = parse_money(price, currency)
        rate = parse_fx(fx)
        quantity = Decimal(order["qty"])
        notional = exact_notional(quantity, fill_price, rate, currency)
        epoch = self.epoch(state)
        realized = Decimal("0")
        if order["side"] == "buy":
            self._assert_new_risk(state, notional)
            cash = parse_signed_money(epoch["capital_balance"], currency)
            if notional > cash:
                raise ResetBlocked("cash", "practice capital is below the paper notional")
            epoch["capital_balance"] = money_text(cash - notional, currency)
            self._add_position(state, order, quantity, notional, rate, fill_price)
        else:
            realized = self._close_long(state, epoch, order, quantity, notional, rate, fill_price)
        order["status"] = "filled"
        order["fill_price"] = money_text(fill_price, currency)
        row = {
            "cost_basis": None,
            "currency": currency,
            "epoch_id": epoch["epoch_id"],
            "fixture": bool(fixture or state.get("fixture")),
            "fx_to_account": format(rate, "f"),
            "id": new_id("lg_"),
            "kind": "fill",
            "label": "caller-supplied paper price, not a market print",
            "not_pnl": False,
            "notional_account": money_text(notional, currency),
            "order_id": order_id,
            "price": money_text(fill_price, currency),
            "qty": format(quantity, "f"),
            "realized_pnl": format_signed(realized, currency),
            "sector": order["sector"],
            "side": order["side"],
            "symbol": order["symbol"],
            "utc": self.now(),
        }
        if order["side"] == "sell":
            row["cost_basis"] = format_signed(notional - realized, currency)
        else:
            row["cost_basis"] = money_text(notional, currency)
            row["realized_pnl"] = format_signed(Decimal("0"), currency)
        self.save(state)
        self._append(row)
        return row

    def mark_position(self, symbol, mark):
        state = self.load()
        self.assert_paper(state)
        found = False
        for position in state["positions"]:
            if position["status"] == "open" and position["symbol"] == _symbol(symbol):
                position["mark"] = money_text(parse_money(mark, state["currency"]), state["currency"])
                found = True
        if not found:
            raise ResetBlocked("position", "open position was not found")
        self.save(state)
        return state

    def settle_flat(self):
        """Close open longs at their marks and require a flat pending book.

        The close is a normal fill on the current epoch. It is not a reset.
        """
        state = self.load()
        self.assert_paper(state)
        pending = [order for order in state["orders"] if order["status"] == "pending"]
        if pending:
            raise ResetBlocked("pending_orders", "cancel pending orders explicitly before settling positions")
        opens = [position for position in state["positions"] if position["status"] == "open"]
        if not opens:
            return {"settled": [], "flat": True}
        for position in opens:
            if position.get("mark") is None:
                raise ResetBlocked("unmarked", "every open position needs a mark before settle; no price is invented")
        settled = []
        for position in list(opens):
            order = self.place_order(
                symbol=position["symbol"],
                side="sell",
                qty=position["qty"],
                limit_price=position["mark"],
                sector=position["sector"],
                idempotency_key="settle-" + position["position_id"],
            )
            settled.append(self.fill_order(order["order_id"], price=position["mark"], fx=position["fx_to_account"]))
        return {"settled": settled, "flat": True}

    def chart(self):
        state = self.load()
        currency = state["currency"]
        cash = Decimal("0")
        points = []
        for line in self.ledger():
            kind = line["kind"]
            if kind in {"opening_capital", "balance_adjustment"}:
                cash = parse_signed_money(line["amount"], currency)
            elif kind == "paper_fund":
                cash += parse_money(line["amount"], currency)
            elif kind == "fill" and line["side"] == "buy":
                cash -= parse_money(line["notional_account"], currency)
            elif kind == "fill" and line["side"] == "sell":
                cash += parse_money(line["notional_account"], currency)
            else:
                continue
            fixture = bool(line.get("fixture"))
            points.append({
                "cash": format_signed(cash, currency),
                "epoch_id": line["epoch_id"],
                "fixture": fixture,
                "kind": kind,
                "label": "test paper ledger fixture" if fixture else "customer paper ledger — not a market print",
                "ledger_id": line["id"],
                "not_pnl": bool(line.get("not_pnl")),
            })
        return {
            "currency": currency,
            "fixture": any(point["fixture"] for point in points),
            "points": points,
            "source": "paper ledger",
        }

    def performance(self):
        state = self.load()
        currency = state["currency"]
        by_epoch = {}
        lifetime = Decimal("0")
        for line in self.ledger():
            if line["kind"] != "fill":
                continue
            pnl = parse_signed_money(line["realized_pnl"], currency)
            lifetime += pnl
            bucket = by_epoch.setdefault(line["epoch_id"], Decimal("0"))
            by_epoch[line["epoch_id"]] = bucket + pnl
        epoch = self.epoch(state)
        return {
            "by_epoch": [
                {
                    "epoch_id": epoch_id,
                    "realized_pnl": format_signed(amount, currency),
                    "preserved": True,
                }
                for epoch_id, amount in by_epoch.items()
            ],
            "currency": currency,
            "current_epoch_session_pnl": epoch["session_realized_pnl"],
            "current_epoch_session_pnl_label": "This epoch's performance P&L. A new epoch starts at 0 because it has no fills yet.",
            "excludes": ["opening_capital", "paper_fund", "balance_adjustment", "epoch_close"],
            "historical_realized_pnl": format_signed(lifetime, currency),
            "historical_realized_pnl_label": "Sum of fill P&L on the append-only ledger. Resets do not rewrite it.",
            "practice_capital": epoch["capital_balance"],
            "practice_capital_label": "Practice capital of the open epoch. Not P&L.",
        }

    def add_funds(self, amount):
        state = self.load()
        self.assert_paper(state)
        currency = state["currency"]
        credit = parse_money(amount, currency)
        if credit <= 0:
            raise InvalidAmount("paper funds must be greater than zero")
        epoch = self.epoch(state)
        cash = parse_signed_money(epoch["capital_balance"], currency) + credit
        from customer_platform.money import MAX_PAPER
        if cash > MAX_PAPER[currency]:
            raise InvalidAmount("amount exceeds the template paper bound")
        epoch["capital_balance"] = format_signed(cash, currency)
        self.save(state)
        row = self._append({
            "amount": money_text(credit, currency),
            "currency": currency,
            "epoch_id": epoch["epoch_id"],
            "id": new_id("lg_"),
            "kind": "paper_fund",
            "label": "Explicit paper funds. Not revenue and not a broker deposit.",
            "not_pnl": True,
            "utc": self.now(),
        })
        return row

    def _assert_new_risk(self, state, notional):
        snapshot = self.equity(state)
        currency = state["currency"]
        equity = parse_signed_money(snapshot["amount"], currency)
        if equity <= 0:
            raise ResetBlocked(
                "zero_equity",
                "equity is zero or negative, so new entries are blocked; no ratio was computed",
            )
        if notional > equity:
            raise ResetBlocked("equity_cap", "paper notional cannot exceed equity")

    def _open_qty(self, state, symbol):
        total = Decimal("0")
        for position in state["positions"]:
            if position["status"] == "open" and position["symbol"] == symbol:
                total += Decimal(position["qty"])
        return total

    def _add_position(self, state, order, quantity, notional, rate, fill_price):
        for position in state["positions"]:
            if position["status"] == "open" and position["symbol"] == order["symbol"]:
                position["qty"] = format(Decimal(position["qty"]) + quantity, "f")
                position["cost_total"] = money_text(
                    parse_money(position["cost_total"], state["currency"]) + notional,
                    state["currency"],
                )
                return
        state["positions"].append({
            "cost_total": money_text(notional, state["currency"]),
            "currency": state["currency"],
            "fx_to_account": format(rate, "f"),
            "mark": money_text(fill_price, state["currency"]),
            "position_id": new_id("pos_"),
            "qty": format(quantity, "f"),
            "sector": order["sector"],
            "status": "open",
            "symbol": order["symbol"],
        })

    def _close_long(self, state, epoch, order, quantity, notional, rate, fill_price):
        currency = state["currency"]
        for position in state["positions"]:
            if position["status"] != "open" or position["symbol"] != order["symbol"]:
                continue
            held = Decimal(position["qty"])
            if quantity > held:
                raise ResetBlocked("short_blocked", "a sell cannot exceed the open long, and shorts are off")
            cost_total = parse_money(position["cost_total"], currency)
            if quantity == held:
                basis = cost_total
                position["status"] = "closed"
                position["qty"] = "0"
                position["cost_total"] = money_text(Decimal("0"), currency)
            else:
                cent = Decimal(1).scaleb(-2 if currency != "JPY" else 0)
                basis = (cost_total * quantity / held).quantize(cent, rounding=ROUND_HALF_EVEN)
                position["qty"] = format(held - quantity, "f")
                position["cost_total"] = money_text(cost_total - basis, currency)
            realized = notional - basis
            session = parse_signed_money(epoch["session_realized_pnl"], currency) + realized
            epoch["session_realized_pnl"] = format_signed(session, currency)
            cash = parse_signed_money(epoch["capital_balance"], currency) + notional
            epoch["capital_balance"] = format_signed(cash, currency)
            position["fx_to_account"] = format(rate, "f")
            return realized
        raise ResetBlocked("short_blocked", "a sell cannot exceed the open long, and shorts are off")

    def _order_idem(self, state, key):
        for order in state["orders"]:
            if order.get("idempotency_key") == key:
                return order
        return None


def format_signed(amount, currency):
    from customer_platform.money import money_text as _text
    if amount < 0:
        return "-" + _text(abs(amount), currency)
    return _text(amount, currency)


def _symbol(symbol):
    text = str(symbol or "").upper()
    if not text or len(text) > 10 or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789." for ch in text):
        raise ResetBlocked("symbol", "symbol is not usable")
    return text


def _sector(symbol, sector):
    if sector:
        text = str(sector).strip().lower()
    else:
        text = SAMPLE_SECTORS.get(symbol, "unclassified")
    if not text or len(text) > 40:
        raise ResetBlocked("sector", "sector name is not usable")
    return text


def _qty(value):
    from customer_platform.money import _as_text
    text = _as_text(value)
    if not __import__("re").fullmatch(r"(0|[1-9]\d*)(\.\d{1,8})?", text):
        raise InvalidAmount("quantity is not an exact decimal")
    quantity = Decimal(text)
    if not quantity.is_finite() or quantity <= 0 or quantity > Decimal("1000000"):
        raise InvalidAmount("quantity is out of range")
    return quantity
