#!/usr/bin/env python3
"""convexity_autotrade.py — open surviving hunt candidates automatically. PAPER ONLY.

Funder-directed 2026-09-04: "JUST TRADE THEM AS SOON AS THEY ARE FOUND. WE DON'T
NEED A BIG EDGE - FULL AUTHORISATION."

WHAT IT DOES
------------
Reads candidates that survived BOTH adversarial killers in an undervalued-hunt
workflow journal, checks each against the sleeve's frozen gates, fetches a LIVE
price, sizes it, and opens it in the convexity sleeve. Then writes a trade notice
the funder's phone channel picks up.

WHAT IT REFUSES, AND WHY EACH REFUSAL EXISTS
--------------------------------------------
1. ANYTHING THAT DID NOT SURVIVE BOTH KILLERS. Full authorisation to trade is not
   authorisation to skip the checks — the funder authorised trading the winners,
   and a candidate one verifier called a value trap is not a winner. Every hunt
   this desk has run found that unrefuted claims die at roughly a 90% rate.
2. A CATALYST DATE WITH NO URL (CM-069). A position once carried "2026-12-31"
   traced to nothing while its own thesis said in capitals that no date existed.
   Missing data is a REFUSAL, not a pass.
3. A STALE OR ABSENT PRICE. Non-negotiable #7: all books trade on live prices.
   No mark, no trade — never a guess.
4. SHORTS. Max loss on a short is unbounded; the sleeve's gate requires it capped.
   Long equity is fine: the most you can lose is what you paid.

SIZING
------
Equal weight across survivors. Not conviction weighting — this desk has measured
that it cannot rank its own ideas (0 of 32 books showed skill), so pretending to
size by confidence would be inventing information. Capped at the sleeve's own
$22,500 per position and $150,000 total committed risk.
"""
import argparse
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import convexity_sleeve as CS                                  # noqa: E402

TARGET_POSITIONS = 8            # equal-weight target; sleeve cap still binds
NOTICE = os.path.join(BASE, "reports", "convexity_trade_notices.log")


def survivors_from_journal(path):
    """REFUSES to run. Kept as a named tombstone so the mistake is not repeated.

    The workflow journal stores {type, key, agentId, result} and NOTHING ELSE —
    there is no label tying a killer's verdict to the candidate it judged. An
    earlier version of this function returned every CANDIDATE it found and threw
    the killer verdicts away, which would have opened positions in names that had
    already been refuted. Caught before it ever ran live; the first killer record
    in the journal was a refutation of the first candidate.

    Survivorship is decided by the WORKFLOW, which pairs each candidate with its
    two killers in the pipeline and returns only the ones nobody refuted. Read
    that. Never re-derive it here.
    """
    raise SystemExit(
        "REFUSED: the journal cannot link killers to candidates, so survivorship "
        "cannot be derived from it. Use --survivors <workflow output .json> and "
        "trade result.survivor_names, which the workflow computed correctly.")


def survivors_from_workflow(path):
    """Read result.survivor_names — candidates NO killer refuted."""
    with open(path) as f:
        o = json.load(f)
    r = o.get("result") if isinstance(o, dict) else None
    if not isinstance(r, dict):
        raise SystemExit("REFUSED: %s has no result object." % path)
    surv = r.get("survivor_names") or []
    total = r.get("candidates")
    print("workflow reports %s candidate(s), %s survivor(s) after both killers"
          % (total, r.get("survivors")))
    out = []
    for s_ in surv:
        out.append({
            "candidate": s_.get("name"),
            "ticker_and_venue": s_.get("ticker"),
            "catalyst_date": s_.get("catalyst"),
            "catalyst_source": s_.get("source"),
            "why_mispriced": s_.get("why"),
            "kills_it": s_.get("kills_it") or "see workflow record",
            "upside_estimate": s_.get("upside") or "n/a",
        })
    return out


def live_price(ticker):
    """Live mark or nothing. Never a guess, never a stale carry."""
    try:
        import fundamental_book as fb
        px, failed = fb.fetch_prices([ticker])
        v = px.get(ticker)
        # fetch_prices returns {"px","ccy","fx_usd","px_usd"} per ticker, NOT a
        # bare float. Taking it as a float silently TypeErrors and every name is
        # refused for "no live price" — a bug that would have read as caution.
        # px_usd is the one to use: the sleeve books in USD and these names are
        # global (the desk has a documented GBp/ZAc minor-unit defect class).
        if isinstance(v, dict):
            u = v.get("px_usd")
            if isinstance(u, (int, float)) and u > 0:
                return float(u), "live %s%s" % (v.get("ccy") or "?",
                                                "" if (v.get("fx_usd") or 1) == 1
                                                else " fx %.4f" % v["fx_usd"])
            return None, "quote had no usable px_usd: %r" % (v,)
        if isinstance(v, (int, float)) and v > 0:
            return float(v), "live"
    except Exception as e:                                      # noqa: BLE001
        return None, "%s: %s" % (type(e).__name__, str(e)[:70])
    return None, "no live quote"



# Venue text -> yfinance suffix. The desk's own book already trades 6902.T,
# 2105.HK, BRN.AX, ERD.TO, FDEV.L, so the convention is established — the bridge
# simply was not applying it. Measured: a hunt candidate quoted as
# "7004 — Tokyo Stock Exchange Prime Market" priced as NO QUOTE, which the bridge
# would have reported as "no live price" and refused. That reads as caution and
# is actually a parsing bug on our side. TAP, EUR.AX and AHCO all priced fine, so
# the failure was silent and selective — the worst shape.
VENUE_SUFFIX = (
    ("tokyo", ".T"), ("tse", ".T"), ("japan", ".T"),
    ("australian securities", ".AX"), ("asx", ".AX"),
    ("london", ".L"), ("lse", ".L"), ("aim", ".L"),
    ("toronto", ".TO"), ("tsx venture", ".V"), ("tsx", ".TO"),
    ("hong kong", ".HK"), ("hkex", ".HK"),
    ("taiwan", ".TW"), ("korea", ".KS"), ("kosdaq", ".KQ"),
    ("stockholm", ".ST"), ("helsinki", ".HE"), ("copenhagen", ".CO"),
    ("oslo", ".OL"), ("frankfurt", ".DE"), ("xetra", ".DE"),
    ("paris", ".PA"), ("euronext amsterdam", ".AS"), ("madrid", ".MC"),
    ("milan", ".MI"), ("swiss", ".SW"), ("six", ".SW"),
    ("johannesburg", ".JO"), ("sao paulo", ".SA"), ("b3", ".SA"),
    ("santiago", ".SN"), ("national stock exchange of india", ".NS"),
)


def _resolve_ticker(ticker_and_venue):
    """Return (symbol, price, how) — trying the bare symbol, then the venue suffix.

    Never guesses a price. If neither form quotes, returns (symbol, None, why) and
    the caller refuses the trade, which is the correct outcome for a name we
    genuinely cannot price.
    """
    tv = str(ticker_and_venue)
    raw = tv.split()[0].strip().upper().rstrip(",;.")
    px, how = live_price(raw)
    if px is not None:
        return raw, px, how
    low = tv.lower()
    for needle, suf in VENUE_SUFFIX:
        if needle in low and not raw.endswith(suf):
            cand = raw + suf
            px2, how2 = live_price(cand)
            if px2 is not None:
                return cand, px2, "%s (resolved via venue '%s')" % (how2, needle)
            break
    return raw, None, how


def trade(candidates, dry_run=True, verbose=True):
    opened, refused = [], []
    avail = CS.available_to_deploy()
    if not candidates:
        print("no surviving candidates — nothing to trade. That is a result, not a failure.")
        return opened, refused
    per = min(CS.MAX_LOSS_PER_POSITION, avail / max(1, min(len(candidates), TARGET_POSITIONS)))
    print("deployable $%s across %d candidate(s) -> $%s each"
          % ("{:,.2f}".format(avail), len(candidates), "{:,.2f}".format(per)))
    for c in candidates:
        tv = str(c.get("ticker_and_venue"))
        tick = tv.split()[0].strip().upper().rstrip(",;")
        why = None
        src = str(c.get("catalyst_source") or "")
        if "http" not in src.lower():
            why = "catalyst_source carries no URL — the date traces to nothing (CM-069)"
        elif not c.get("catalyst_date") or str(c["catalyst_date"]).upper() == "NONE":
            why = "no dated catalyst — no date, no position"
        elif "short" in str(c.get("candidate", "")).lower():
            why = "short: max loss uncapped, sleeve gate refuses it"
        if why:
            refused.append((tick, why))
            print("  REFUSED %-12s %s" % (tick, why))
            continue
        # CM-073: the sleeve's SEALED registration (line 133) permits only long
        # options, debit spreads, warrants and cash-settled capped contracts. Long
        # equity was never on that list — this code accepted it because it passes
        # the capped-loss ARITHMETIC, which made the code more permissive than the
        # seal. TAP and 7004.T sit in the sleeve only as written funder exceptions.
        # Delta-one now routes to the Dated-Event Long Book, whose charter says
        # what it holds. Into the sleeve only with an explicit funder_directed flag.
        _inst = str(c.get("instrument") or c.get("candidate") or "").lower()
        _is_delta_one = not any(k in _inst for k in ("call", "put", "spread", "warrant", "option"))
        if _is_delta_one and not c.get("funder_directed"):
            refused.append((tick, "DELTA_ONE: long equity is not a permitted sleeve instrument "
                                  "(registration line 133) — route via dated_event_book.queue()"))
            print("  REFUSED %-12s delta-one: not a permitted sleeve instrument; use the "
                  "Dated-Event Long Book" % tick)
            continue
        tick, px, how = _resolve_ticker(tv)
        if px is None:
            refused.append((tick, "no live price (%s)" % how))
            print("  REFUSED %-12s no live price (%s) — never trading a stale mark" % (tick, how))
            continue
        units = round(per / px, 6)
        if dry_run:
            print("  WOULD OPEN %-10s %s units @ $%.4f = $%s  catalyst %s"
                  % (tick, units, px, "{:,.2f}".format(units * px), c.get("catalyst_date")))
            opened.append(tick)
            continue
        try:
            CS.open_position(
                symbol=tick, instrument="LONG EQUITY %s" % tv,
                venue=tv, max_loss_usd=round(units * px, 2),
                premium_usd=round(units * px, 2),
                upside_multiple=str(c.get("upside_estimate") or "n/a"),
                catalyst_date=str(c["catalyst_date"]), catalyst_source=src,
                thesis=str(c.get("why_mispriced"))[:600],
                honest_probability=str(c.get("verdict")),
                what_kills_it=str(c.get("kills_it"))[:400])
            d = CS.load()
            d["positions"][tick]["units"] = units
            d["positions"][tick]["mark_ticker"] = tick
            d["positions"][tick]["entry_px"] = px
            import desk_costs as _dc  # 2026-09-26 V1 costs HARD RULE: entry fee (same model as V2)
            _f = _dc.fill_fees(tick, "buy", units, px); _fee = round(_f["fees_usd"], 2)
            d["positions"][tick]["entry_fees_usd"] = _fee; d["positions"][tick]["entry_fee_detail"] = _f["breakdown"]
            d["cash"] = round(d["cash"] - _fee, 2); d["realised_usd"] = round(float(d.get("realised_usd") or 0) - _fee, 2)
            d["fees_paid_usd"] = round(float(d.get("fees_paid_usd") or 0) + _fee, 2)
            CS.save(d)
            opened.append(tick)
            print("  OPENED  %-12s %s units @ $%.4f = $%s"
                  % (tick, units, px, "{:,.2f}".format(units * px)))
        except SystemExit as e:
            refused.append((tick, str(e)[:120]))
            print("  REFUSED %-12s %s" % (tick, str(e).replace("\n", " ")[:110]))
    if opened and not dry_run:
        with open(NOTICE, "a") as f:
            f.write("%s  CONVEXITY SLEEVE OPENED: %s\n"
                    % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), ", ".join(opened)))
    return opened, refused


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--survivors", required=True,
                    help="workflow output .json — its result.survivor_names is the "
                         "ONLY valid source of survivorship")
    ap.add_argument("--live", action="store_true", help="actually open (default is dry run)")
    a = ap.parse_args()
    cands = survivors_from_workflow(a.survivors)
    trade(cands, dry_run=not a.live)


if __name__ == "__main__":
    main()
