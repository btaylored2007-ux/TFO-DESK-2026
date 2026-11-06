"""THF half-bank hard rule (Brett 2026-11-06 20:36 WITA). PAPER decision only.

Bank HALF of the filled position when net open R is >= +0.7.
This is an R multiple of the immutable initial risk, not a percent of notional.
The generic sleeve rule "half at +2% of deploy basis" does not apply to THF.

Signed-policy ambiguity (do not collapse these):
- Stale GitHub veteran_desk/book.py is NOT the 2026-11-06 live snapshot and was
  not verified as the running tick. In that stale file, peak_fav_R / open_R are
  gross price R (no fees) and RAIL_HALF_R is 2.0, while bank_trigger uses net R
  at 1.0 (trend 1.5) via book.net_R. tfo_bank_rail_scanner.py half-banks a
  different sleeve at >= +2% of deploy basis.
- This module's trigger is net R only, threshold +0.7, every THF seat.
  Gross price R is reported and never triggers. Percent-of-notional never triggers.
- Net R = (signed price P&L on filled units, minus booked entry cost, minus the
  exit cost at this quote, minus borrow already accrued) / (abs(fill - initial_stop)
  * filled units). Costs must be supplied in the same unit as price*qty.
  Missing cost is "unknown" and does not bank. Nothing here invents a fee.
- Remainder, T2, giveback, full-flat and trailing constants are not modified.
  The decision does not move the stop and does not rewrite the target.

The live book tick is veteran_desk/book.py on the droplet. That file was not in
the sanitized snapshot, so this module cannot be claimed as already called by
the running engine. It is the maintained rule the tick should call. It never
writes a ledger.
"""
from decimal import Decimal, ROUND_DOWN
import datetime as dt
import hashlib
import json

VERSION = "thf_half_bank_20261106"
THRESHOLD_NET_R = Decimal("0.7")
FRACTION = Decimal("0.5")
MAX_QUOTE_AGE_S = 15 * 60
CRYPTO_DP = 8
# Learning, prompts and model output are not allowed to change these.
FROZEN = ("THRESHOLD_NET_R", "FRACTION", "trigger_basis")

def _d(value):
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or value is None:
        raise ValueError("missing number")
    return Decimal(str(value))

def _qty(position):
    raw = position.get("shares_filled")
    if raw is None:
        raw = position.get("shares")
    return _d(raw)

def immutable_basis(position):
    """Initial risk captured once. Later stop trails must not replace it."""
    if position.get("initial_stop") is None or position.get("fill") is None:
        raise ValueError("fill and initial_stop are required")
    fill = _d(position["fill"])
    stop = _d(position["initial_stop"])
    filled = _qty(position)
    if filled <= 0 or fill <= 0 or stop <= 0 or fill == stop:
        raise ValueError("invalid initial risk basis")
    side = str(position.get("side") or "")
    if side not in ("LONG", "SHORT"):
        raise ValueError("side must be LONG or SHORT")
    if side == "LONG" and not stop < fill:
        raise ValueError("LONG initial stop must be below fill")
    if side == "SHORT" and not stop > fill:
        raise ValueError("SHORT initial stop must be above fill")
    return dict(fill=fill, initial_stop=stop, filled_qty=filled,
                r0_per_unit=abs(fill - stop), side=side)

def gross_r(basis, mark):
    sign = Decimal(1) if basis["side"] == "LONG" else Decimal(-1)
    return sign * (_d(mark) - basis["fill"]) / basis["r0_per_unit"]

def net_pnl(basis, mark, entry_cost, exit_cost, borrow):
    sign = Decimal(1) if basis["side"] == "LONG" else Decimal(-1)
    gross = sign * (_d(mark) - basis["fill"]) * basis["filled_qty"]
    return gross - _d(entry_cost) - _d(exit_cost) - _d(borrow)

def net_r(basis, mark, entry_cost, exit_cost, borrow):
    risk = basis["r0_per_unit"] * basis["filled_qty"]
    if risk <= 0:
        raise ValueError("risk basis is zero")
    return net_pnl(basis, mark, entry_cost, exit_cost, borrow) / risk

def half_units(filled, crypto=False):
    """Once-only 50% of filled units, never more than half. Floor, not round up."""
    raw = filled * FRACTION
    if crypto:
        quantum = Decimal(1).scaleb(-CRYPTO_DP)
        units = raw.quantize(quantum, rounding=ROUND_DOWN)
    else:
        units = Decimal(int(raw.to_integral_value(rounding=ROUND_DOWN)))
    if units <= 0 or units >= filled:
        return None
    if (filled - units) <= 0:
        return None
    return units

def _asof(quote):
    raw = quote.get("asof")
    if isinstance(raw, dt.datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=dt.timezone.utc)
    if isinstance(raw, (int, float, Decimal)):
        return dt.datetime.fromtimestamp(float(raw), dt.timezone.utc)
    text = str(raw)
    parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)

def quote_block(quote, now):
    if not isinstance(quote, dict) or quote.get("price") is None:
        return "no_quote"
    if quote.get("stale") is True:
        return "stale_quote"
    if quote.get("session_open") is False:
        return "session_closed"
    if quote.get("asof") in (None, ""):
        return "quote_asof_missing"
    try:
        age = (now - _asof(quote)).total_seconds()
    except Exception:
        return "quote_asof_invalid"
    if age < -120 or age > MAX_QUOTE_AGE_S:
        return "stale_quote"
    return None

def _marker(position):
    marker = position.get("thf_half_bank")
    return marker if isinstance(marker, dict) else None

def _decision_id(position, basis, units):
    payload = dict(id=position.get("id"), fill=str(basis["fill"]), stop=str(basis["initial_stop"]),
                   filled=str(basis["filled_qty"]), units=str(units), rule=VERSION)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]

def decide(position, quote, now=None, entry_cost=None, exit_cost=None, borrow=None):
    """Pure decision. Does not mutate position and does not write a book."""
    now = now or dt.datetime.now(dt.timezone.utc)
    position = position if isinstance(position, dict) else {}
    out = dict(version=VERSION, action="hold", bank=False, threshold_net_r="0.7",
               trigger_basis="net_r", percent_rule_applies=False, paper_only=True,
               stop_change=None, target_change=None)
    existing = _marker(position)
    if existing and existing.get("applied") is True:
        out.update(action="already_banked", reason="once_only", marker=existing)
        return out
    if position.get("t1_done") is True or position.get("status") not in (None, "open"):
        out.update(action="already_banked" if position.get("t1_done") else "hold",
                   reason="not_open" if position.get("status") not in (None, "open") else "once_only")
        return out
    blocked = quote_block(quote if isinstance(quote, dict) else {}, now)
    if blocked:
        out.update(reason=blocked, net_r="unknown", gross_r="unknown")
        return out
    if entry_cost is None or exit_cost is None:
        out.update(reason="cost_unknown", net_r="unknown")
        return out
    try:
        basis = immutable_basis(position)
        mark = _d(quote["price"])
        gross = gross_r(basis, mark)
        realised = net_r(basis, mark, entry_cost, exit_cost or 0, borrow or 0)
    except ValueError as error:
        out.update(reason=str(error), net_r="unknown")
        return out
    out.update(gross_r=format(gross, "f"), net_r=format(realised, "f"),
               filled_shares=format(basis["filled_qty"], "f"),
               requested_shares=None if position.get("shares_requested") is None else format(_d(position["shares_requested"]), "f"))
    if position.get("shares_requested") is not None and _d(position["shares_requested"]) != basis["filled_qty"]:
        out["size_basis"] = "filled_not_requested"
    else:
        out["size_basis"] = "filled"
    # A +2% notional move is not an R trigger. Record it only as a non-trigger.
    notional = basis["fill"] * basis["filled_qty"]
    if notional > 0:
        sign = Decimal(1) if basis["side"] == "LONG" else Decimal(-1)
        out["pnl_percent_of_notional"] = format(sign * (mark - basis["fill"]) / basis["fill"], "f")
        out["percent_rule_ignored"] = True
    if realised < THRESHOLD_NET_R:
        out.update(reason="net_below_0.7R", bank=False)
        if gross >= THRESHOLD_NET_R and realised < THRESHOLD_NET_R:
            out["reason"] = "gross_reached_0.7_but_net_did_not"
        return out
    units = half_units(basis["filled_qty"], crypto=bool(position.get("crypto")))
    open_qty = _d(position.get("shares") if position.get("shares") is not None else basis["filled_qty"])
    if units is None or units > open_qty or (open_qty - units) <= 0:
        out.update(reason="cannot_split_half", bank=False)
        return out
    decision = _decision_id(position, basis, units)
    if existing and existing.get("decision_id") == decision:
        out.update(action="already_banked", reason="restart_idempotent", marker=existing, bank=False)
        return out
    out.update(action="bank_half", bank=True, reason="net_r_at_least_0.7",
               shares_to_bank=format(units, "f"), residual_shares=format(open_qty - units, "f"),
               decision_id=decision, quote_price=format(mark, "f"),
               quote_asof=_asof(quote).isoformat(),
               immutable_basis=dict(fill=format(basis["fill"], "f"), initial_stop=format(basis["initial_stop"], "f"),
                                    filled_qty=format(basis["filled_qty"], "f"),
                                    r0_per_unit=format(basis["r0_per_unit"], "f"), side=basis["side"]))
    return out

def apply_decision(position, decision):
    """Return a new position dict. Refuses a second bank. Does not write disk."""
    position = dict(position)
    if not decision.get("bank"):
        return position, dict(applied=False, reason=decision.get("reason"))
    if (_marker(position) or {}).get("applied") is True:
        return position, dict(applied=False, reason="once_only")
    units = _d(decision["shares_to_bank"])
    open_qty = _d(position.get("shares") if position.get("shares") is not None else position.get("shares_filled"))
    if units <= 0 or units >= open_qty:
        return position, dict(applied=False, reason="cannot_split_half")
    stop_before = position.get("stop")
    target_before = position.get("target")
    position["shares"] = int(open_qty - units) if not position.get("crypto") else format(open_qty - units, "f")
    position["thf_half_bank"] = dict(
        version=VERSION, applied=True, decision_id=decision["decision_id"],
        threshold_net_r="0.7", trigger_basis="net_r",
        filled_shares=decision.get("filled_shares"), requested_shares=decision.get("requested_shares"),
        shares_banked=decision["shares_to_bank"], residual_shares=decision["residual_shares"],
        net_r=decision.get("net_r"), gross_r=decision.get("gross_r"),
        quote_price=decision.get("quote_price"), quote_asof=decision.get("quote_asof"),
        immutable_basis=decision.get("immutable_basis"), size_basis=decision.get("size_basis"),
        stop_unchanged=stop_before, target_unchanged=target_before)
    if position.get("stop") != stop_before or position.get("target") != target_before:
        raise RuntimeError("half bank must not move stop or target")
    return position, dict(applied=True, shares_banked=decision["shares_to_bank"], marker=position["thf_half_bank"])

def frozen_rules():
    return dict(version=VERSION, threshold_net_r="0.7", fraction="0.5", trigger_basis="net_r",
                percent_of_notional=False, stop_change=False, target_change=False,
                learning_may_modify=False)
