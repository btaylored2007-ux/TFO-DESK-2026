#!/usr/bin/env python3
"""V1 Head Trader clip heat at live stops (Head Trader decision 2026-09-26). PAPER ONLY. Pure functions, no I/O.

Shared by tfo_buy.gates, tfo_crypto.gates/clip_heat and tfo_watchlist.firm_snapshot so sizing sees one number.
  Pre-half clip          : unchanged -- its -5% original-fill stop on entry notional (5% x notional).
  Half-banked residual   : variant C trails with a breakeven floor (tfo_trail_rail.decide: level = max(entry, trail)).
                           Stop floored at/above breakeven (shorts: at/below) -> 1% gap buffer on CURRENT notional.
                           Stop below breakeven (should not happen under variant C) -> actual loss vs fill at the
                           live stop, never less than the 1% buffer.
  A new clip being sized : 5% x its notional (callers add this; unchanged).
The 5% firm cap (HEAT_CAP_PCT) is shared by every V1 heat gate; powder still caps real clips.
"""
PRE_HALF_PCT = 0.05
HEAT_CAP_PCT = 0.05             # funder directive 2026-09-27: 5% of firm equity
FLOORED_GAP_BUFFER_PCT = 0.01


def residual_heat(entry, qty, mark, level, side="long"):
    qty = abs(float(qty)); entry = float(entry)
    mark = float(mark) if mark else entry
    buf = FLOORED_GAP_BUFFER_PCT * qty * mark
    lvl = entry if level is None else float(level)      # variant C floors every banked residual at breakeven
    loss_per_unit = (lvl - entry) if side == "short" else (entry - lvl)
    return buf if loss_per_unit <= 0 else max(qty * loss_per_unit, buf)


def crypto_banked(p):
    return bool(p.get("rebase") or (p.get("trail") or {}).get("half_banked"))


def pre_half_pct(p):
    """Pre-half heat fraction: the clip's own stop distance for C3-tagged crypto clips (2xATR at entry, 2026-10-02), else 5%."""
    if p.get("exit_variant") == "C3":
        try:
            sd = float(p.get("c3_stop_dist"))
            if sd > 0: return sd
        except (TypeError, ValueError):
            pass
    return PRE_HALF_PCT


def crypto_position_heat(p, mark=None):
    if not crypto_banked(p):
        return pre_half_pct(p) * float(p["qty"]) * float(p["entry_px"])
    e0 = float(p.get("orig_entry") or p["entry_px"])
    return residual_heat(e0, p["qty"], mark, (p.get("trail") or {}).get("level_usd"), "long")


def equity_banked(p):
    return bool(p.get("rail_half_count") or p.get("rail_rebase_usd") or (p.get("rail_trail") or {}).get("half_banked"))


def equity_clip_heat(p):
    if not equity_banked(p):
        return PRE_HALF_PCT * float(p.get("notional_usd") or 0)
    side = p.get("side", "long")
    e0 = float(p.get("rail_orig_entry_usd") or p["entry_px_usd"])
    st = p.get("rail_trail") or {}
    return residual_heat(e0, p["units"], p.get("mark_usd") or e0, st.get("level_usd"), side)


def clip_heat(fund_positions, crypto_book):
    """-> (total heat USD, detail rows). fund_positions: fundamental_book positions (only ht_clip counted)."""
    rows, total = [], 0.0
    for sym, p in (fund_positions or {}).items():
        if not p.get("ht_clip"):
            continue
        h = equity_clip_heat(p); total += h
        rows.append({"sym": sym, "book": "fundamental_book", "banked": equity_banked(p), "heat_usd": round(h, 2)})
    cb = crypto_book or {}
    marks = cb.get("mark_px_usd") or {}
    for pair, p in (cb.get("positions") or {}).items():
        h = crypto_position_heat(p, marks.get(pair)); total += h
        rows.append({"sym": pair, "book": "tfo_ht_crypto", "banked": crypto_banked(p), "heat_usd": round(h, 2)})
    for pair, r in (cb.get("resting_entries") or {}).items():   # 17 Oct 2026: a resting post-only entry is heat at its own stop
        h = float(r.get("usd") or 0) * float(r.get("c3_stop_dist") or 0.05); total += h
        rows.append({"sym": pair, "book": "tfo_ht_crypto", "banked": False, "heat_usd": round(h, 2), "resting": True})
    return total, rows


def legacy_flat_heat(fund_positions, crypto_book):
    """The pre-2026-09-26 flat 5% basis, kept for before/after reporting only."""
    eq = sum(float(p.get("notional_usd") or 0) for p in (fund_positions or {}).values() if p.get("ht_clip"))
    cr = sum(float(p["qty"]) * float(p["entry_px"]) for p in ((crypto_book or {}).get("positions") or {}).values())
    return PRE_HALF_PCT * (eq + cr)
