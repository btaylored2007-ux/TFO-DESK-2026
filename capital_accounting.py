"""Accounting basis from recorded transfers; never changes cash or positions."""
import math

def net_allocated_capital(book):
    transfers = [e for e in book.get("capital_events", []) if e.get("type") == "capital_transfer"]
    if not transfers:
        return float(book.get("seed_usd") or 0)
    values = [float(e["usd"]) for e in transfers]
    if not all(math.isfinite(x) for x in values):
        raise ValueError("non-finite capital transfer")
    return round(sum(values), 2)
