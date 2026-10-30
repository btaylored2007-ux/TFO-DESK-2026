#!/usr/bin/env python3
"""Desk-wide capital caps (Brett, PAPER-only desk). Single source for the numbers; pure helpers + a read-only firm snapshot.

2026-10-02 (Brett approved):
  * Desk-wide invested cap raised 80% -> 100% of firm equity ("the rails can handle exits"). 100% means FULLY INVESTED,
    never more: NO leverage, cash can never go negative.
  * $50k cash reserve floor KEPT. It is a separate rule (SHARK_SIZING_RULE.md: "on top of the tfo_buy buffer"), not part
    of the old 80% logic, so with the cap at 100% the reserve is what keeps invested <= equity - $50k.
  * HT crypto stack cap $500k -> $1,000,000 (tfo_crypto.STACK_CAP is hard-coded to the same value so temp-desk tests
    that copy tfo_crypto alone still import; tests/test_crypto_cap_value_sleeve.py asserts they match).
  * Value + profitability sleeve: 20% of firm equity (value_prof_sleeve.py; staged paper target).
Unchanged and NOT defined here: 1% firm-equity risk per clip to its stop, 5% heat cap (tfo_heat), clip caps, max 2 shark
entries per Sydney day, no averaging down, V7/C3/V0 exits, closed-name list (tfo_ticket.CLOSED_NAMES).
Revert: set INVESTED_CAP_PCT = 0.80 and CRYPTO_STACK_CAP_USD = 500000.0 (or restore backups/crypto_cap_value_<ts>/).
PAPER ONLY. No broker, no orders. firm_exposure() only READS reports/*.json."""
import json, os

INVESTED_CAP_PCT = 1.00            # 2026-10-02 Brett: 80% -> 100% (no leverage; never > 100%)
CASH_RESERVE_USD = 50000.0         # kept: separate rule, not part of the old 80% cap
CRYPTO_STACK_CAP_USD = 1000000.0   # 2026-10-02 Brett: $500k -> $1M (HT crypto clips, entry-cost basis)
VALUE_PROF_SLEEVE_PCT = 0.20       # 2026-10-02 Brett: value+profitability sleeve at 20% of firm equity
assert 0 < INVESTED_CAP_PCT <= 1.0, "invested cap above 100% would be leverage"

BASE = os.path.dirname(os.path.abspath(__file__))

# 2026-10-29 Brett (TFO, standing rule 28 Oct 20:47 WITA: every book separate, own P&L, nothing merged):
# SLEEVE SIZING never uses books that are registered into firm equity for REPORTING only. veteran_desk (~$3.88M,
# registered 28 Oct ~20:47 WITA) is excluded so sizing floats with the pre-28-Oct composition (~$1.80M).
# Firm-level RISK metrics (DD freeze/halt, Sydney-day freeze, 5% heat cap, War Room) keep using full firm equity.
# Revert: SIZING_EXCLUDED_BOOKS = ()
SIZING_EXCLUDED_BOOKS = ("veteran_desk",)
SIZING_MAX_AGE_H = 24.0


def sizing_equity(reports=None, max_age_h=None, excluded=None):
    """Sizing base = firm_equity_usd - sum(book_equity_usd[k] for k in SIZING_EXCLUDED_BOOKS), from
    reports/firm_risk_state.json. FAIL CLOSED (raises) when the file is missing/stale or has no per-book breakdown,
    so nothing can silently size off the merged total."""
    import datetime as _dt
    R = reports or os.path.join(BASE, "reports")
    excluded = SIZING_EXCLUDED_BOOKS if excluded is None else tuple(excluded)
    max_age_h = SIZING_MAX_AGE_H if max_age_h is None else max_age_h
    st = json.load(open(os.path.join(R, "firm_risk_state.json")))
    t = _dt.datetime.fromisoformat(str(st["utc"]).replace("Z", "+00:00"))
    age_h = (_dt.datetime.now(_dt.timezone.utc) - t).total_seconds() / 3600.0
    if age_h > max_age_h:
        raise RuntimeError("firm_risk_state.json is %.1fh old; refusing to size" % age_h)
    total = float(st["firm_equity_usd"])
    books = st.get("book_equity_usd")
    if not isinstance(books, dict) or not books:
        raise RuntimeError("firm_risk_state.json has no book_equity_usd breakdown; refusing to size")
    excl = {k: round(float(books[k] or 0), 2) for k in excluded if k in books}
    base = total - sum(excl.values())
    if not base > 0:
        raise RuntimeError("sizing equity %.2f <= 0 after excluding %s" % (base, sorted(excl)))
    return {"sizing_equity_usd": round(base, 2), "firm_equity_usd": round(total, 2), "excluded_usd": excl,
            "utc": st["utc"], "age_h": round(age_h, 2),
            "source": "firm_risk_state.json %s ex %s" % (st["utc"], ",".join(sorted(excl)) or "none"), "state": st}


def invested_room(firm_equity, invested, firm_cash, cap_pct=None, reserve=None):
    """Dollars that may still be invested: min(cap x equity - invested, cash - reserve), floored at 0. Never > cash (no leverage)."""
    cap_pct = INVESTED_CAP_PCT if cap_pct is None else cap_pct
    reserve = CASH_RESERVE_USD if reserve is None else reserve
    cap_pct = min(float(cap_pct), 1.0)
    return max(0.0, min(cap_pct * float(firm_equity) - float(invested), float(firm_cash) - float(reserve)))


def _book_cash(b):
    """Top-level cash of a ledger, or None when the ledger does not carry one (treated as fully invested = conservative)."""
    for k in ("cash", "cash_usd"):
        v = b.get(k) if isinstance(b, dict) else None
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def _registry_cash():
    """{book_key: cash_usd} from book_coverage.registry() (same per-book basis firm_risk uses); {} if unavailable."""
    try:
        import contextlib, io, book_coverage
        with contextlib.redirect_stdout(io.StringIO()):
            reg = book_coverage.registry()
        return {b["key"]: float(b["cash_usd"]) for b in reg if isinstance(b.get("cash_usd"), (int, float)) and not b.get("error")}
    except Exception:
        return {}


def firm_exposure(reports=None, use_registry=True, exclude_books=()):
    """Read-only snapshot: firm equity + per-book equity from reports/firm_risk.json; book cash from book_coverage.registry()
    (fallback: the ledger's top-level cash). invested = firm equity - sum(book cash); a book with no readable cash counts as
    fully invested (overstates invested = conservative)."""
    R = reports or os.path.join(BASE, "reports")
    fr = json.load(open(os.path.join(R, "firm_risk.json")))
    eq = float(fr["firm_equity_usd"]); books = dict(fr.get("book_equity_usd") or {})
    if exclude_books:   # 2026-10-29: sleeve sizing callers pass SIZING_EXCLUDED_BOOKS (books never merged)
        if not books: raise RuntimeError("firm_risk.json has no book_equity_usd; cannot exclude %s" % (list(exclude_books),))
        for k in exclude_books:
            eq -= float(books.pop(k, 0) or 0)
    reg = _registry_cash() if use_registry else {}
    cash, unknown = 0.0, {}
    for k, beq in books.items():
        c = reg.get(k)
        if c is None:
            try: c = _book_cash(json.load(open(os.path.join(R, k + ".json"))))
            except Exception: c = None
        if c is None: unknown[k] = round(float(beq or 0), 2)
        else: cash += min(c, float(beq or 0)) if c >= 0 else c
    invested = eq - cash
    return {"asof_utc": fr.get("utc"), "firm_equity_usd": round(eq, 2), "firm_cash_usd": round(cash, 2),
            "invested_usd": round(invested, 2), "invested_pct": round(100 * invested / eq, 2) if eq else None,
            "invested_cap_pct": round(100 * INVESTED_CAP_PCT, 1), "cash_reserve_usd": CASH_RESERVE_USD,
            "cap_room_usd": round(INVESTED_CAP_PCT * eq - invested, 2),
            "invested_room_usd": round(invested_room(eq, invested, cash), 2),
            "cash_basis": "book_coverage.registry cash_usd" if reg else "ledger top-level cash",
            "books_without_cash_field_counted_invested": unknown, "level": fr.get("level"),
            "new_entries_halted": fr.get("new_entries_halted")}


if __name__ == "__main__":
    print(json.dumps(firm_exposure(), indent=1))
