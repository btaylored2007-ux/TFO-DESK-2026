"""Paper runtime. New entries and existing-stop management are separate.

Prices come only from a labelled synthetic replay print. Without that print
the worker does not invent a mark.
"""

from decimal import Decimal

from customer_platform.errors import ResetBlocked


def tick_tenant(platform, tenant_id):
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
    sent = control.view()["orders_sent"]
    return {
        "engine": "paper_replay",
        "executable_price": price,
        "filled": filled,
        "managed": True,
        "orders_sent": sent,
        "source": "synthetic replay print, not a market price",
    }


def _fill(book, control, order, state, price):
    try:
        book.fill_order(order["order_id"], price=price, fixture=bool(state.get("fixture")))
        control.note_engine_order()
        return True
    except ResetBlocked:
        return False
