#!/usr/bin/env python3
"""risk_caps.py -- TFO Risk audit 4 Oct 2026 (research/deep19/RISK_AUDIT.md), Head Trader adopted 4 Oct 2026 21:30 AEDT. PAPER ONLY.

Applies to MAIN TFO desk entry paths only: tfo_buy.py (shark/equity entries, incl. the pre-open hunt via tfo_watchlist),
tfo_crypto.py buy (HT crypto clips, incl. watchlist crypto fires) and research/overnight2k clips.
NOT the Parallel Desk (Brett's explicit rules, clips <= $40k) and NOT trend18 (separate sleeve, own kill rule).

Rule 1 - planned-loss cap per name:
    clip = min(existing size, PLANNED_LOSS_CAP_USD / (stop distance + estimated slippage))
    Stop distance is the ACTUAL stop the position will carry (e.g. the -5% shark rail, the C3 2xATR crypto stop, or a
    tighter caller stop such as overnight2k's -5% night stop), never ATR as a proxy. Slippage allowance =
    max(DEFAULT_SLIP, measured book slip if the caller has one). $5k / (5% + 0.5%) = ~$90.9k at a -5% stop.
Rule 2 - high-ATR crypto:
    if ATR14% > stop distance: clip = clip x stop / ATR14 (proportional size-down). If the result is below the desk's
    minimum clip the entry is SKIPPED (never rounded up to the minimum).
Both rules only ever shrink a clip. A clip that is not bound is returned unchanged (byte-for-byte same behaviour);
a bound clip is floored to ROUND_TO dollars so it can never exceed the cap. Bindings are logged to
state/risk_caps/cap_log.jsonl (executed entries) by log_bind().

Entry-engine override (Brett, funder, 2026-10-09 21:10 AEDT; PAPER): entries tagged with an ENTRY_ENGINE_THEMES theme
(entry_engine_equity / entry_engine_crypto, i.e. research/entry_engine/auto_fire.py clips) target $150,000 notional:
    clip = min(requested, $150,000, $7,500 / stop distance)
The $7,500 planned loss is measured AT the actual stop ($7,500 / 5% = $150k at the -5% equity rail), so the 0.5% slippage
allowance is reported (planned_loss_usd, ~$8,250 at -5%) but not added to the cap. A wider stop sizes down (crypto C3
2xATR stop of 6% -> $125k); rule 2 (ATR > stop) and the desk minimum still apply. Every other caller/theme is unchanged.
"""
import datetime as dt, json, math, os

BASE = os.path.dirname(os.path.abspath(__file__))
RULE_ID = "TFO_RISK_AUDIT_20261004"
RULE_ADOPTED = "2026-10-04 21:30 AEDT, TFO Risk audit, Head Trader adopted"
PLANNED_LOSS_CAP_USD = 5000.0      # TFO Risk: $2.5k-$5k band; Head Trader adopted the $5k top of the band
DEFAULT_SLIP_PCT = 0.005           # 0.5% allowance through the stop (8766.T -5.1% fill vs -5% rail; HBAR -6.5% on a gap)
CRYPTO_MIN_CLIP_USD = 5000.0       # desk minimum crypto clip (overnight2k MIN_CLIP); rule 2 skips below this
EQUITY_STOP_PCT = 0.05             # shark V7/V0 -5% original-fill rail stop (tfo_buy.SHARK_SIZING_STOP)
ROUND_TO = 100.0
# Brett (funder) 2026-10-09 21:10 AEDT: entry-engine auto-fire clips target $150k notional (top 3 A/B picks/day, PAPER)
ENTRY_ENGINE_THEMES = frozenset(("entry_engine_equity", "entry_engine_crypto"))
ENTRY_ENGINE_RULE_ID = "BRETT_ENTRY_ENGINE_150K_20261009"
ENTRY_ENGINE_RULE_ADOPTED = "2026-10-09 21:10 AEDT, Brett (funder)"
ENTRY_ENGINE_TARGET_USD = 150000.0
ENTRY_ENGINE_MAX_LOSS_USD = 7500.0  # planned loss AT the stop: $7,500 / 5% = $150k; slip allowance reported, not added to the cap
LOG_DIR = os.path.join(BASE, "state", "risk_caps")
LOG_PATH = os.path.join(LOG_DIR, "cap_log.jsonl")


def _f(x):
    try:
        x = float(x)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def slip_allowance(measured=None, default=DEFAULT_SLIP_PCT):
    """Fractional slippage allowance: max(default, measured book slip). measured is a FRACTION (0.003 = 0.3%)."""
    m = _f(measured)
    return max(default, m) if m is not None and m > 0 else default


def is_entry_engine(theme):
    return str(theme or "") in ENTRY_ENGINE_THEMES


def theme_params(theme):
    """Sizing kwargs for size_entry / planned_loss_cap_usd by entry theme. Entry-engine themes -> the Brett 2026-10-09
    $150k target / $7,500-at-the-stop cap; any other theme -> {} (the TFO Risk audit $5k / (stop + slip) default)."""
    if is_entry_engine(theme):
        return {"max_loss": ENTRY_ENGINE_MAX_LOSS_USD, "target_usd": ENTRY_ENGINE_TARGET_USD, "slip_in_cap": False, "rule": ENTRY_ENGINE_RULE_ID}
    return {}


def _cap(sd, slip, max_loss, target_usd, slip_in_cap):
    cap = max_loss / (sd + (slip if slip_in_cap else 0.0))
    if target_usd:
        cap = round(min(cap, float(target_usd)), 6)  # rounded so $7,500/5% is exactly $150,000 (never floored to $149,900)
    return cap


def planned_loss_cap_usd(stop_dist, slip_pct=None, max_loss=PLANNED_LOSS_CAP_USD, target_usd=None, slip_in_cap=True, rule=None):
    """Largest clip whose planned loss (clip x (stop + slippage)) is <= max_loss. None if the stop is unusable.
    Entry-engine themes (theme_params): clip x stop <= max_loss and clip <= target_usd."""
    sd = _f(stop_dist)
    if sd is None or sd <= 0:
        return None
    return _cap(sd, slip_allowance(slip_pct), max_loss, target_usd, slip_in_cap)


def size_entry(usd, stop_dist, slip_pct=None, atr_pct=None, apply_atr_rule=False, min_clip=0.0,
               max_loss=PLANNED_LOSS_CAP_USD, round_to=ROUND_TO, target_usd=None, slip_in_cap=True, rule=None):
    """Apply rule 1 (and rule 2 when apply_atr_rule) to a requested clip.
    stop_dist, slip_pct, atr_pct are FRACTIONS of entry (0.05 = 5%). -> dict with
      usd (sized, <= requested), bound (bool), binds (list of rule names), skip (bool), skip_reason, planned_loss_usd, ...
    An unusable stop distance fails CLOSED (skip) because the planned loss cannot be measured."""
    req = float(usd)
    sd, atr = _f(stop_dist), _f(atr_pct)
    slip = slip_allowance(slip_pct)
    d = {"rule": rule or RULE_ID, "usd_requested": round(req, 2), "stop_dist": sd, "slip_allowance": round(slip, 6), "atr14_pct": atr,
         "max_loss_usd": max_loss, "binds": [], "bound": False, "skip": False, "skip_reason": None}
    if sd is None or sd <= 0:
        d.update(usd=0.0, skip=True, skip_reason="no usable stop distance (planned-loss cap fails closed)")
        return d
    cap = _cap(sd, slip, max_loss, target_usd, slip_in_cap)
    d["planned_loss_cap_usd"] = round(cap, 2)
    if target_usd or not slip_in_cap:  # entry-engine override only (other callers' dicts stay byte-for-byte the same)
        d.update(target_usd=target_usd, loss_basis="at stop (slip reported, not in cap)" if not slip_in_cap else "stop + slip")
    size = req
    if size > cap + 1e-9:
        size = cap
        d["binds"].append("notional_target" if target_usd and cap >= float(target_usd) - 1e-6 else "planned_loss_cap")
    if apply_atr_rule and atr is not None and atr > sd:
        scale = sd / atr
        d["atr_scale"] = round(scale, 6)
        d["atr_scaled_usd"] = round(size * scale, 2)
        size = size * scale; d["binds"].append("atr_above_stop")
    if d["binds"]:
        size = math.floor(size / round_to) * round_to if round_to else size
        d["bound"] = True
    d["usd"] = round(size, 2)
    d["planned_loss_usd"] = round(size * (sd + slip), 2)
    if "loss_basis" in d:
        d["planned_loss_at_stop_usd"] = round(size * sd, 2)
    if min_clip and d["bound"] and size < min_clip:   # only a risk size-down can skip; an unbound request is never changed
        d["skip"] = True
        d["skip_reason"] = "risk-sized clip $%.0f < desk minimum $%.0f (%s)" % (size, min_clip, "+".join(d["binds"]) or "requested size")
    return d


def describe(d):
    if d.get("rule") == ENTRY_ENGINE_RULE_ID and not d.get("skip"):
        return _describe(d) + " [entry engine: $%.0f target, $%.0f max loss at the stop; at stop $%.0f]" % (
            d.get("target_usd") or 0, d.get("max_loss_usd") or 0, d.get("planned_loss_at_stop_usd") or 0)
    return _describe(d)


def _describe(d):
    if d.get("skip"):
        return "RISKCAP SKIP: %s" % d.get("skip_reason")
    if not d.get("bound"):
        return "RISKCAP ok: $%.0f planned loss $%.0f at stop %.2f%% + slip %.2f%% (cap $%.0f)" % (
            d["usd"], d["planned_loss_usd"], 100 * d["stop_dist"], 100 * d["slip_allowance"], d.get("planned_loss_cap_usd") or 0)
    return "RISKCAP BOUND (%s): $%.0f -> $%.0f; planned loss $%.0f at stop %.2f%% + slip %.2f%%%s" % (
        "+".join(d["binds"]), d["usd_requested"], d["usd"], d["planned_loss_usd"], 100 * d["stop_dist"], 100 * d["slip_allowance"],
        (" ; ATR14 %.2f%% > stop -> x%.3f" % (100 * d["atr14_pct"], d["atr_scale"])) if d.get("atr_scale") else "")


def log_bind(d, source, symbol, executed, path=None, extra=None):
    """Append a binding/skip record (only when the cap bound or skipped). Never raises."""
    if not (d.get("bound") or d.get("skip")):
        return None
    try:
        p = path or LOG_PATH
        os.makedirs(os.path.dirname(p), exist_ok=True)
        now = dt.datetime.now(dt.timezone.utc)
        rec = {"utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
               "syd": now.astimezone(__import__("zoneinfo").ZoneInfo("Australia/Sydney")).strftime("%Y-%m-%d %H:%M:%S %Z"),
               "source": source, "symbol": symbol, "executed": bool(executed), "rule_adopted": RULE_ADOPTED, **d, **(extra or {})}
        with open(p, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        return rec
    except Exception as e:  # logging must never block or crash an entry path
        print("RISKCAP log failed: %s" % e)
        return None
