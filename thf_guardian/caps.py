"""Arm screens that sit on top of the existing rails. They only reject.

Degraded trader seats may take A+ or A only, at half the planned risk, and
never a B. Half size below the existing floor is rejected rather than lifted.
Five or more degraded trader seats pause new arms for the whole firm. Theme,
sector and venue+side caps match the desk's existing crowding rules.
"""
from thf_guardian.ai_mode import degraded_traders
from thf_guardian.seats import HUNT_SEATS

FIRM_PAUSE_AT = 5
CORR_CAP = 2          # head_of_desk: max 2 per venue+side
SECTOR_CAP = 2
THEME_CAP = 1         # book._theme_clash: one open/armed name per theme+side
FLOOR = {"crypto": 0.005}
DEFAULT_FLOOR = 0.01
# Subset of book.THEMES so this module does not import the book (and its data stack).
THEMES = {"oil": {"XOM", "CVX", "COP", "EOG", "OXY", "XLE", "XOP", "VDE", "OIH", "USO", "BNO", "CL=F", "BZ=F",
                  "WDS.AX", "STO.AX", "SHEL", "SHEL.L", "BP", "BP.L"}}


def theme_of(ticker, hinted=None):
    if hinted:
        return hinted
    s = str(ticker or "").upper().replace("KRAKEN:", "")
    return next((k for k, v in THEMES.items() if s in v), None)


def firm_new_arms_paused(modes):
    n = len(degraded_traders(modes))
    return n >= FIRM_PAUSE_AT, n


def half_pct(seat, base_pct):
    """Return (pct, reason). reason set means do not arm and do not lift up to the floor."""
    half = float(base_pct) * 0.5
    floor = FLOOR.get(seat, DEFAULT_FLOOR)
    if half < floor - 1e-12:
        return None, "half size %.2f%% is below the existing %.2f%% floor; not lifted" % (half * 100, floor * 100)
    return half, None


def _held(book):
    rows = []
    for seat, sb in (book.get("seats") or {}).items():
        for pos in (sb.get("positions") or {}).values():
            if pos.get("status") in ("open", "armed"):
                rows.append(dict(seat=seat, ticker=pos.get("ticker"), side=pos.get("side"),
                                 venue=pos.get("venue"), sector=pos.get("sector"), theme=theme_of(pos.get("ticker"), pos.get("theme"))))
    return rows


def screen_arm(proposal, book, modes, batch=None, base_pct=0.01):
    """Decide whether a new arm is allowed. Does not write. batch is other arms accepted this round."""
    reasons = []
    paused, n = firm_new_arms_paused(modes)
    if paused:
        reasons.append("firm pause: %d trader seats degraded (limit %d); no new arms" % (n, FIRM_PAUSE_AT))
    seat = proposal.get("seat")
    grade = proposal.get("grade")
    degraded = seat in degraded_traders(modes)
    size_mult = 1.0
    pct = float(base_pct)
    if degraded:
        if grade not in ("A", "A+"):
            reasons.append("degraded seat takes A+/A only; %s is refused" % grade)
        else:
            size_mult = 0.5
            pct, why = half_pct(seat, base_pct)
            if why:
                reasons.append(why)
    side = proposal.get("side")
    venue = proposal.get("venue")
    sector = proposal.get("sector")
    theme = theme_of(proposal.get("ticker"), proposal.get("theme"))
    held = _held(book) + list(batch or [])
    if theme:
        clash = [h for h in held if h.get("theme") == theme and h.get("side") == side and h.get("ticker") != proposal.get("ticker")]
        if len(clash) >= THEME_CAP:
            reasons.append("theme cap: %s %s already held (%s)" % (theme, side, clash[0].get("ticker")))
    if sector:
        same = [h for h in held if h.get("sector") == sector]
        if len(same) >= SECTOR_CAP:
            reasons.append("sector cap: %s already has %d names" % (sector, len(same)))
    else:
        reasons.append("sector missing; not armed")
    if venue and side:
        same = [h for h in held if h.get("venue") == venue and h.get("side") == side]
        if len(same) >= CORR_CAP:
            reasons.append("correlation cap: %d already %s %s" % (len(same), side, venue))
    return dict(ok=not reasons, reasons=reasons, size_mult=size_mult, risk_pct=pct, degraded=degraded,
                firm_paused=paused, degraded_n=n, seat=seat, grade=grade)
