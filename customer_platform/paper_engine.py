"""Tenant paper replay. Fills pending orders at one synthetic mark.

This is not a live feed and it does not call a broker. A tenant with no
replay armed is left untouched.
"""

from decimal import Decimal

MARK = "100.00"


def tick_tenant(platform, tenant_id):
    control = platform._control(tenant_id)
    view = control.view()
    if view["engine"] != "paper_replay" or view["state"] != "replay":
        return {"engine": view["engine"], "filled": 0, "orders_sent": view["orders_sent"]}
    book = platform._book(tenant_id)
    state = book.load()
    mark = Decimal(MARK)
    filled = 0
    for order in list(state["orders"]):
        if order.get("status") != "pending":
            continue
        limit = Decimal(order["limit_price"])
        side = order.get("side")
        if side == "buy" and mark <= limit and _fill(book, control, order, state):
            filled += 1
        elif side == "sell" and mark >= limit and _fill(book, control, order, state):
            filled += 1
        state = book.load()
    sent = control.view()["orders_sent"]
    return {"engine": "paper_replay", "filled": filled, "orders_sent": sent, "source": "synthetic replay, not a live feed"}


def _fill(book, control, order, state):
    from customer_platform.errors import ResetBlocked
    try:
        book.fill_order(order["order_id"], price=MARK, fixture=bool(state.get("fixture")))
        control.note_engine_order()
        return True
    except ResetBlocked:
        return False
