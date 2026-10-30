#!/usr/bin/env python3
"""fundamental_distributions.py — cash returned to holders. PAPER ONLY.

WHY THIS EXISTS
---------------
The book had NO concept of cash paid to holders. `grep -i "dividend|distribution|
corporate_action|cash_credit" fundamental_*.py` returned nothing. Both the
marking layer and the exit layer assume every price change is a repricing.

That is wrong in two directions and both are live:

1. PHANTOM P&L, ALREADY FIRING. When a name goes ex-dividend the price drops by
   roughly the dividend. On a LONG the book records a loss it did not suffer; on
   a SHORT it records a gain it did not earn and ignores the dividend it OWES.
   Measured, already booked: WOW.AX short ex 2026-09-01 +$34.62 phantom gain,
   TSN short +$26.70, ANTO.L short +$10.93, ODL.OL long -$57.45 phantom loss,
   IMO.TO short +$18.96. SQM-B.SN, a short, goes ex 2026-09-07.

2. AN ENTIRE TRADE CLASS IS UNREACHABLE. Liquidations, wind-ups, special
   dividends and return-of-capital situations pay by handing cash back, not by
   the price rising. A hunt found ARI — a dissolving REIT paying $3.70-$4.00 a
   share — and the adversarial verifier killed it on exactly this: "the payoff is
   serial return-of-capital, and both the marking layer and the exit layer of the
   fundamental book are built for price repricing... holding ARI through late
   October manufactures a false ~$26k loss on an 8%-cap position and permanently
   disables the +5% ladder for that name."
   The gate was RIGHT. The defect was ours.

THE HONESTY RULE THAT SHAPES THE DESIGN
---------------------------------------
This module NEVER invents a distribution. It applies only events recorded in
reports/distributions.json, each of which must carry an ex-date, an amount, and a
SOURCE URL — the CM-069 rule: a required field that traces to nothing is a
fabricated field. No source, no event, no adjustment. An unrecorded dividend
leaves the phantom in place, visibly, rather than being papered over with a guess.

THE ACCOUNTING
--------------
  LONG  + dividend            -> credit cash. Cost basis unchanged.
  SHORT + dividend            -> DEBIT cash. You owe it to the lender.
  LONG  + return of capital   -> credit cash AND reduce cost basis by the same
                                 amount, so the ex-date price drop is not a loss.
  SHORT + return of capital   -> debit cash and raise cost basis.
Amounts are converted at the position's mark FX -- which is the MINOR-unit rate
for minor-unit quotes (GBp 0.01352, ZAc 0.00062637), so a pence-declared dividend
on a GBp line is right. That closes the minor-unit door and NOTHING else.

CM-075 (2026-09-06): the DECLARATION-CURRENCY door was wide open. `currency` was a
required argument that was stored and never read, so any dividend declared in a
currency other than the quote currency was converted at the WRONG rate:

  SQM-B.SN  CLP-quoted, declared "US$1.43501" (SEC 6-K, CIK 909037, 2026-08-25)
            truth -$143.45  ->  would book -$0.15   (934x)
  ANTO.L    GBp-quoted, "Dividends ... are declared in US Dollars" (issuer IR)
            truth  -$10.94  ->  would book -$0.15   ( 74x)
  AAL.L     GBp-quoted, "16 US cents per ordinary share", paid 11.822 pence
            truth   -$7.33  ->  would book -$0.10   ( 74x)

Three of 63 held names, all shorts, all understating cash the desk OWES.
`apply_pending()` now compares `currency` to the position's `entry_ccy` with an
EXACT string match and REFUSES on mismatch. The match must stay case-sensitive:
"GBP" and "GBp" differ only in case and differ by 100x, which is precisely the
pence bug this desk has already paid for once.

A refused event is left PENDING and unapplied, never silently dropped -- the
module's standing rule is that a phantom stays visible rather than being papered
over with a guess.
"""
import json
import os
import time

BASE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.path.join(BASE, "reports", "distributions.json")

KIND_DIVIDEND = "dividend"
KIND_RETURN_OF_CAPITAL = "return_of_capital"
KINDS = (KIND_DIVIDEND, KIND_RETURN_OF_CAPITAL)


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_registry():
    if not os.path.exists(REGISTRY):
        return {"events": [], "note": "sourced distribution events only — no URL, no event"}
    with open(REGISTRY) as f:
        return json.load(f)


def save_registry(r):
    tmp = REGISTRY + ".tmp"
    with open(tmp, "w") as f:
        json.dump(r, f, indent=2)
    os.replace(tmp, REGISTRY)


def record(symbol, ex_date, amount_per_share, currency, kind, source_url, note=""):
    """Register ONE sourced distribution. Refuses anything it cannot trace."""
    if kind not in KINDS:
        raise SystemExit("REFUSED: kind must be one of %s" % (KINDS,))
    if not source_url or "http" not in str(source_url).lower():
        raise SystemExit(
            "REFUSED: %s %s has no source URL. A distribution that traces to "
            "nothing is a fabricated field (CM-069). No source, no event."
            % (symbol, ex_date))
    try:
        amt = float(amount_per_share)
    except (TypeError, ValueError):
        raise SystemExit("REFUSED: amount_per_share %r is not a number" % amount_per_share)
    if amt <= 0:
        raise SystemExit("REFUSED: amount_per_share must be positive, got %r" % amt)
    if not (isinstance(ex_date, str) and len(ex_date) == 10 and ex_date[4] == "-"):
        raise SystemExit("REFUSED: ex_date %r must be YYYY-MM-DD" % ex_date)
    # CM-075: `currency` decides the FX applied. It is not decorative.
    if not (isinstance(currency, str) and 2 <= len(currency.strip()) <= 4):
        raise SystemExit(
            "REFUSED: %s %s currency %r is not a code. It must be the currency the "
            "ISSUER DECLARED in, exactly as the quote currency is written "
            "(GBp != GBP -- they differ by 100x)." % (symbol, ex_date, currency))
    currency = currency.strip()
    r = load_registry()
    for e in r["events"]:
        if e["symbol"] == symbol and e["ex_date"] == ex_date:
            raise SystemExit("REFUSED: %s already has an event on %s" % (symbol, ex_date))
    r["events"].append({
        "symbol": symbol, "ex_date": ex_date, "amount_per_share": amt,
        "currency": currency, "kind": kind, "source_url": source_url,
        "note": note, "recorded_utc": _utc(), "applied_utc": None,
    })
    save_registry(r)
    return r["events"][-1]


def pending(led, today=None):
    """Events whose ex-date has arrived, not yet applied, for a held symbol."""
    today = today or time.strftime("%Y-%m-%d")
    r = load_registry()
    out = []
    for e in r["events"]:
        if e.get("applied_utc"):
            continue
        if e["ex_date"] > today:
            continue
        if e["symbol"] in (led.get("positions") or {}):
            out.append(e)
    return out


def apply_pending(led, today=None, verbose=True):
    """Credit/debit cash and adjust basis for every due, sourced event.

    Mutates `led` in place. Returns the list applied. Never raises into a tick.
    """
    applied = []
    refused = []
    try:
        due = pending(led, today)
    except Exception as e:                                  # noqa: BLE001
        print("   DISTRIBUTIONS unavailable (%s) — none applied" % e)
        return applied
    if not due:
        return applied
    r = load_registry()
    for e in due:
        sym = e["symbol"]
        p = (led.get("positions") or {}).get(sym)
        if not p:
            continue
        units = abs(float(p.get("units") or 0.0))
        if not units:
            continue
        is_short = str(p.get("side", "")).lower() == "short"
        # CM-075 GUARD. `fx` below is the position's QUOTE-currency rate. Applying it
        # to an amount declared in another currency is wrong by the rate itself
        # (934x on SQM-B.SN). Exact, case-SENSITIVE compare: GBp vs GBP is 100x.
        decl = str(e.get("currency") or "").strip()
        quote = str(p.get("entry_ccy") or "").strip()
        if decl != quote:
            print("   DISTRIBUTION REFUSED %-10s declared in %r but the position is "
                  "quoted in %r. Converting at the position's mark FX would be wrong "
                  "by the rate itself. Event left PENDING, not applied (CM-075)."
                  % (sym, decl, quote))
            refused.append({"symbol": sym, "ex_date": e["ex_date"],
                            "declared_ccy": decl, "quote_ccy": quote})
            continue
        fx = float(p.get("mark_fx_usd") or p.get("entry_fx_usd") or 1.0)
        gross_local = units * float(e["amount_per_share"])
        gross_usd = gross_local * fx
        # a short OWES the distribution; a long receives it
        signed_usd = -gross_usd if is_short else gross_usd
        led["cash"] = round(float(led.get("cash") or 0.0) + signed_usd, 2)
        # 2026-09-29: a cash DIVIDEND is income, so it also lands in realised_usd; every equity identity on the desk
        # is seed + realised - costs + unrealised + interest, and cash-only booking would open an identity gap of
        # exactly the dividend. Return of capital is not income (basis moves instead), so it is NOT added.
        if e["kind"] != KIND_RETURN_OF_CAPITAL:
            led["realised_usd"] = round(float(led.get("realised_usd") or 0.0) + signed_usd, 2)
            led["distributions_in_realised_usd"] = round(
                float(led.get("distributions_in_realised_usd") or 0.0) + signed_usd, 2)
        if e["kind"] == KIND_RETURN_OF_CAPITAL:
            # the price drops by the payment — reduce basis so it is not a loss
            adj = float(e["amount_per_share"])
            for f in ("entry_px",):
                if isinstance(p.get(f), (int, float)):
                    p[f] = round(float(p[f]) - adj, 6)
            if isinstance(p.get("entry_px_usd"), (int, float)):
                p["entry_px_usd"] = round(float(p["entry_px_usd"]) - adj * fx, 6)
        p.setdefault("distributions_received", [])
        p["distributions_received"].append(
            {"ex_date": e["ex_date"], "amount_per_share": e["amount_per_share"],
             "kind": e["kind"], "usd": round(signed_usd, 2), "source": e["source_url"],
             "in_realised": e["kind"] != KIND_RETURN_OF_CAPITAL})
        led["distributions_total_usd"] = round(
            float(led.get("distributions_total_usd") or 0.0) + signed_usd, 2)
        for stored in r["events"]:
            if stored["symbol"] == sym and stored["ex_date"] == e["ex_date"]:
                stored["applied_utc"] = _utc()
        applied.append({"symbol": sym, "kind": e["kind"], "usd": round(signed_usd, 2)})
        if verbose:
            print("   DISTRIBUTION %-10s %-18s %s $%s  (%s)"
                  % (sym, e["kind"], "SHORT owes" if is_short else "LONG receives",
                     "{:,.2f}".format(abs(signed_usd)), e["ex_date"]))
    if refused:
        print("   DISTRIBUTIONS: %d event(s) REFUSED on a declaration-currency "
              "mismatch and left unapplied (CM-075). They must be re-recorded with "
              "the issuer's declared currency, or the position re-stated." % len(refused))
        led["distributions_refused"] = refused
    if applied:
        save_registry(r)
    return applied
