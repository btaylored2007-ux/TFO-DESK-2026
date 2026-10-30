#!/usr/bin/env python3
"""value_hunt.py — undervaluation screen for the TFO Fundamental Desk LONG book.

PAPER ONLY. NO VALIDATED EDGE — this is a cross-sectional screen, not a tested
strategy. Every claim it produces carries its search count.

THE DATA-INTEGRITY GATE IS THE POINT
------------------------------------
1301.TW taught this desk that a provider's `priceToBook` can be computed against
a DIFFERENT equity definition than the filed balance sheet — it reported 0.6995
where marketCap/CommonStockEquity said 1.1059, an error of 58%. A screen ranking
on the provider's number would have ranked that stock as cheap when it is not.

So every candidate must RECONCILE: the provider's P/B and the independently
computed marketCap/equity must agree within TOLERANCE, or the name is REJECTED
and the rejection is retained and reported. A screen that silently drops what it
cannot verify is a screen that reports only its own blind spots.

Also guarded (learned from BRK-B): a share-count/price mismatch across share
classes shows up as an absurd mcap/equity ratio, which the same gate catches.
"""
import json, math, os, sys, time, warnings
warnings.filterwarnings("ignore")
import yfinance as yf

# 2026-10-28 Brett B3: hunt scripts honour the desk base path (env DESK_BASE, default = this folder)
BASE = os.environ.get("DESK_BASE", os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "reports", "value_hunt.json")
PB_TOLERANCE = 0.10          # provider vs computed P/B must agree within 10%

_FX_CACHE = {}


def _fx(frm, to):
    """FX rate converting `frm` into `to`, or None. Cached; never guesses."""
    if frm == to:
        return 1.0
    key = (frm, to)
    if key in _FX_CACHE:
        return _FX_CACHE[key]
    for pair, inv in (("%s%s=X" % (frm, to), False), ("%s%s=X" % (to, frm), True)):
        try:
            v = float(yf.Ticker(pair).fast_info["last_price"])
            if v and v > 0:
                rate = (1.0 / v) if inv else v
                _FX_CACHE[key] = rate
                return rate
        except Exception:
            continue
    _FX_CACHE[key] = None
    return None


def _n(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) \
        and v == v and abs(v) != math.inf else None

def screen_one(sym):
    """Return (row, reject_reason). Never raises."""
    r = {"symbol": sym}
    try:
        t = yf.Ticker(sym)
        info = t.info or {}
    except Exception as e:
        return None, "info unavailable: %s" % type(e).__name__
    mc = _n(info.get("marketCap"))
    if not mc:
        return None, "no market cap"
    r.update({
        "name": (info.get("longName") or "")[:44],
        "ccy": info.get("currency"), "mcap": mc,
        "pb_provider": _n(info.get("priceToBook")),
        "pe": _n(info.get("trailingPE")), "fpe": _n(info.get("forwardPE")),
        "ev_ebitda": _n(info.get("enterpriseToEbitda")),
        "ps": _n(info.get("priceToSalesTrailing12Months")),
        "roe": _n(info.get("returnOnEquity")),
        "de": _n(info.get("debtToEquity")),
        "dy": _n(info.get("dividendYield")),
        "sector": info.get("sector"),
    })
    # --- the integrity gate --------------------------------------------------
    try:
        bs = t.balance_sheet
    except Exception:
        bs = None
    eq = None
    if bs is not None and getattr(bs, "empty", True) is False:
        col = bs.columns[0]
        for row in ("Common Stock Equity", "Stockholders Equity",
                    "Total Stockholder Equity"):
            if row in bs.index:
                try:
                    eq = float(bs.loc[row, col]); r["bs_asof"] = str(col)[:10]
                except Exception:
                    eq = None
                break
    if not eq or eq <= 0:
        return None, "no usable balance-sheet equity (cannot reconcile P/B)"
    # --- FX: the gate's OWN version of the hazard it exists to catch ---------
    # marketCap is in the TRADING currency; the balance sheet is in the
    # REPORTING currency (`financialCurrency`). For ADRs and foreign listings
    # these differ, and comparing them directly produced nonsense: Toyota
    # computed 0.0057 against a provider 15.51 — that ratio IS the yen. Nine
    # legitimate names (TM, SONY, HMC, MUFG, SMFG, NMR, TSM, NTES, DEO) were
    # rejected by MY bug, not by bad provider data. Convert before comparing.
    fin_ccy = (info.get("financialCurrency") or "").upper()
    trade_ccy = (info.get("currency") or "").upper()
    r["fin_ccy"] = fin_ccy
    fx = 1.0
    if fin_ccy and trade_ccy and fin_ccy != trade_ccy:
        fx = _fx(fin_ccy, trade_ccy)
        if fx is None:
            return r, ("currency mismatch %s financials vs %s price and no FX "
                       "rate — cannot reconcile" % (fin_ccy, trade_ccy))
        r["fx_fin_to_trade"] = fx
    pb_calc = mc / (eq * fx)
    r["pb_calc"] = pb_calc
    pbp = r["pb_provider"]
    if pbp and pbp > 0:
        disagree = abs(pb_calc - pbp) / pbp
        r["pb_disagree"] = disagree
        if disagree > PB_TOLERANCE:
            return r, ("P/B UNRECONCILED: provider %.4f vs computed %.4f (%.0f%% "
                       "apart) — the 1301.TW hazard" % (pbp, pb_calc, disagree*100))
    r["pb"] = pb_calc          # ALWAYS rank on the reconciled number
    return r, None

def main(syms):
    rows, rejects = [], []
    for i, s in enumerate(syms, 1):
        row, why = screen_one(s)
        if why:
            rejects.append({"symbol": s, "reason": why,
                            **({"pb_provider": row.get("pb_provider"),
                                "pb_calc": row.get("pb_calc")} if row else {})})
        elif row:
            rows.append(row)
        if i % 25 == 0:
            print("   ...screened %d/%d (%d kept, %d rejected)"
                  % (i, len(syms), len(rows), len(rejects)))
        time.sleep(0.08)
    json.dump({"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "n_screened": len(syms), "n_kept": len(rows),
               "n_rejected": len(rejects), "pb_tolerance": PB_TOLERANCE,
               "rows": rows, "rejects": rejects},
              open(OUT, "w"), indent=1)
    print("   wrote %s — %d screened, %d kept, %d rejected"
          % (OUT, len(syms), len(rows), len(rejects)))
    return rows, rejects

if __name__ == "__main__":
    u = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else []
    main(u)
