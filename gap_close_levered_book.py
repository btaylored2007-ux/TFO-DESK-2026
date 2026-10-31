#!/usr/bin/env python3
"""gap_close_levered_book.py — mark Gap-Close Levered Sleeve. PAPER ONLY.
Rules in GAP_CLOSE_LEVERED_PREREGISTRATION.md (2026-09-16).
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import desk_lock  # 2026-09-26 HT audit: powder writers share one lock (tfo_crypto / tfo_ticket / tfo_buy use "tfo_gcl_powder")
POWDER_LOCK = "tfo_gcl_powder"

BASE = os.path.dirname(os.path.abspath(__file__))
BOOK = os.path.join(BASE, "reports", "gap_close_levered_book.json")
UNIVERSE = ["TQQQ", "SOXL"]
COST_BPS_SIDE = 10.0
REBALANCE_TD = 21
LOOKBACK = 63
TOP = 2

def load():
    with open(BOOK) as f:
        return json.load(f)

def save(b):
    b["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = BOOK + ".tmp"
    with open(tmp, "w") as f:
        json.dump(b, f, indent=2)
    os.replace(tmp, BOOK)

def fetch():
    import yfinance as yf
    raw = yf.download(UNIVERSE, period="1y", interval="1d",
                      auto_adjust=True, progress=False, threads=True)
    return raw["Close"].dropna(how="all")

def equity_of(b, last):
    return float(b.get("cash") or 0) + sum(
        u * float(last.get(t, 0)) for t, u in b.get("holdings", {}).items() if t in last)

def tick(force=False):
    # Price download OUTSIDE the lock; the read-modify-write of the powder file INSIDE it, re-reading the file
    # just before the save so a Head Trader transfer landing during the download can never be overwritten.
    px = fetch()
    last = px.iloc[-1]
    with desk_lock.ledger_lock(POWDER_LOCK, timeout=300):
        b = load()
        _mark(b, last)
        save(b)
    print("marked equity", b["equity"], "dd", b["drawdown_pct"])
    return 0

GCL_FLOW_TYPES = ("capital_transfer", "BANK_IN_FROM_SLEEVE", "capital_withdrawal", "archive_absorb_note")


def net_flow(b):
    """Signed net capital flow into this book (in +, out -). Accounting repairs are losses, not flows."""
    return sum(float(e.get("usd") or 0) for e in (b.get("capital_events") or []) if e.get("type") in GCL_FLOW_TYPES)


def _mark(b, last):
    # FLOW-ADJUSTED drawdown (HT/funder 2026-09-26): paired powder transfers in/out are not P&L.
    eq = equity_of(b, last)
    F = net_flow(b)
    peak = float(b.get("peak_equity") or eq)
    pF = b.get("peak_net_flow_usd")
    pF = F if pF is None else float(pF)
    adj = eq - (F - pF)
    if adj > peak:
        peak, pF, adj = eq, F, eq
    b["peak_equity"] = round(peak, 2)
    b["peak_net_flow_usd"] = round(pF, 2)
    b["net_flow_usd"] = round(F, 2)
    b["drawdown_basis"] = "flow-adjusted: (equity - net capital transfers since peak) / peak"
    b["equity"] = round(eq, 2)
    b["drawdown_pct"] = round((adj / peak - 1) * 100, 3) if peak else 0
    # kill at book_kill.KILL_DD_PCT (KILL_THRESHOLD_AMENDMENT.md). Was a hardcoded -35.
    import book_kill
    if b["drawdown_pct"] <= book_kill.KILL_DD_PCT:
        b["frozen"] = True
        b["frozen_reason"] = "sleeve kill %.0f%%" % book_kill.KILL_DD_PCT
    return b

if __name__ == "__main__":
    raise SystemExit(tick())
