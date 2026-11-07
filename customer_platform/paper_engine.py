"""Paper runtime. New entries and existing-stop management are separate.

Prices come only from a labelled synthetic replay print. Without that print
the worker does not invent a mark. One process holds the tenant writer.
"""

from decimal import Decimal

from customer_platform.errors import ResetBlocked
from customer_platform.files import tenant_writer


def tick_tenant(platform, tenant_id):
    with tenant_writer(platform._tenant_dir(tenant_id)) as gate:
        if gate == "busy":
            return {"engine": "paper_replay", "filled": 0, "managed": False, "reason": "writer busy"}
        return _tick(platform, tenant_id)


def _tick(platform, tenant_id):
    control = platform._control(tenant_id)
    stored = control.load()
    if not stored.get("replay_armed"):
        return {"engine": "unconnected", "filled": 0, "managed": False, "orders_sent": int(stored.get("orders_sent") or 0), "reason": "no synthetic replay"}
    price = stored.get("replay_print") or ""
    if not price:
        return {"engine": "paper_replay", "filled": 0, "managed": False, "orders_sent": int(stored.get("orders_sent") or 0), "reason": "no synthetic print"}
    mark = Decimal(price)
    book = platform._book(tenant_id)
    state = book.load()
    view = control.view()
    filled = 0
    if view["state"] == "replay":
        filled += _provider_entries(platform, tenant_id, book, control, mark, price)
        filled += _strategy_entries(platform, tenant_id, book, control, mark, price)
        state = book.load()
        for order in list(state["orders"]):
            if order.get("status") != "pending":
                continue
            limit = Decimal(order["limit_price"])
            side = order.get("side")
            if side == "buy" and mark <= limit and _fill(book, control, order, state, price):
                filled += 1
            elif side == "sell" and mark >= limit and _fill(book, control, order, state, price):
                filled += 1
            state = book.load()
    for position in list(state["positions"]):
        if position.get("status") != "open" or not position.get("stop"):
            continue
        if mark > Decimal(position["stop"]):
            continue
        key = "stop-exit-{0}-{1}".format(position["position_id"], price)
        existing = [row for row in state["orders"] if row.get("idempotency_key") == key]
        if existing and existing[0].get("status") == "filled":
            continue
        if not existing:
            book.place_order(
                symbol=position["symbol"], side="sell", qty=position["qty"],
                limit_price=price, sector=position.get("sector"), idempotency_key=key,
                trader_id=position.get("trader_id") or "unknown",
                account_id=position.get("account_id") or tenant_id,
                strategy_id=position.get("strategy_id") or "unknown",
                strategy_version=position.get("strategy_version") or "unknown",
                decision="stop_exit", feed="synthetic_replay",
            )
        order = next(row for row in book.load()["orders"] if row.get("idempotency_key") == key)
        if order.get("status") == "pending" and _fill(book, control, order, book.load(), price):
            filled += 1
        state = book.load()
    return {
        "engine": "paper_replay",
        "executable_price": price,
        "filled": filled,
        "managed": True,
        "orders_sent": control.view()["orders_sent"],
        "source": "synthetic replay print, not a market price",
    }


def _provider_entries(platform, tenant_id, book, control, mark, price):
    """One paper order from a reviewed provider schema. The model does not set the price."""
    made = 0
    if not control.load()["traders"][0]["entries_enabled"]:
        return 0
    for rule in platform._skills(tenant_id).provider_rules():
        key = "provider-{0}-v{1}".format(rule["skill_id"], rule["version"])
        state = book.load()
        if any(row.get("idempotency_key") == key for row in state["orders"]):
            continue
        if any(row.get("status") == "open" and row.get("strategy_id") == rule["skill_id"] for row in state["positions"]):
            continue
        decision = platform.provider_decide(tenant_id, rule)
        if not decision.get("ok"):
            continue
        width = Decimal(rule["percent"]) / Decimal("100")
        stop = (mark * (Decimal("1") - width)).quantize(Decimal("0.01"))
        if stop >= mark:
            continue
        try:
            order = book.place_order(
                symbol=rule["symbol"], side="buy", qty="1", limit_price=price, stop=format(stop, "f"),
                idempotency_key=key, trader_id="desk", account_id=tenant_id,
                strategy_id=rule["skill_id"], strategy_version=str(rule["version"]),
                decision="provider_decision", feed="synthetic_replay",
            )
        except ResetBlocked:
            continue
        if _fill(book, control, order, book.load(), price):
            made += 1
            fill = _latest_fill(book, order["order_id"])
            if fill:
                platform._brain(tenant_id).record_provider_lesson(
                    tenant_id=tenant_id, fill_id=fill.get("id") or order["order_id"],
                    skill_id=rule["skill_id"], version=rule["version"],
                    decision="provider_decision", feed="synthetic_replay",
                )
    return made


def _latest_fill(book, order_id):
    found = None
    for line in book.ledger():
        if line.get("kind") == "fill" and line.get("order_id") == order_id:
            found = line
    return found


def _strategy_entries(platform, tenant_id, book, control, mark, price):
    """One paper order from an active written rule. Not a model and not a live order."""
    made = 0
    for rule in platform._skills(tenant_id).executable_rules():
        key = "strategy-{0}-v{1}".format(rule["skill_id"], rule["version"])
        state = book.load()
        if any(row.get("idempotency_key") == key for row in state["orders"]):
            continue
        if any(row.get("status") == "open" and row.get("strategy_id") == rule["skill_id"] for row in state["positions"]):
            continue
        width = Decimal(rule["percent"]) / Decimal("100")
        stop = (mark * (Decimal("1") - width)).quantize(Decimal("0.01"))
        if stop >= mark or not control.load()["traders"][0]["entries_enabled"]:
            continue
        try:
            book.place_order(
                symbol=rule["symbol"], side="buy", qty="1", limit_price=price, stop=format(stop, "f"),
                idempotency_key=key, trader_id="desk", account_id=tenant_id,
                strategy_id=rule["skill_id"], strategy_version=str(rule["version"]),
                decision="strategy_rule", feed="synthetic_replay",
            )
            made += 1
        except ResetBlocked:
            continue
    return made


def _fill(book, control, order, state, price):
    try:
        book.fill_order(order["order_id"], price=price, fixture=bool(state.get("fixture")))
        control.note_engine_order()
        return True
    except ResetBlocked:
        return False
