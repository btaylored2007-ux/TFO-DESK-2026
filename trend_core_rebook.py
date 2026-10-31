#!/usr/bin/env python3
"""TREND-CORE one-shot entry REBOOK at real US-session prices (PAPER, Brett pt1571u 2026-10-28).

Why: the 2026-10-28 09:23 WITA entry booked the US ETFs at 27-Oct closing marks while the US was
closed (no-backdating rule). This re-books each ETF entry fill at a live in-session price at the US
open on 28-Oct ET (~21:30 WITA). Same A$ notional + cost per fill and the same AUDUSD as the
original fill, so cash (incl. the A$50k spare) is unchanged; only shares/entry price change.
BTC (booked at a live Kraken quote) is left as booked.

Guards: runs ONCE (state/trend_core_rebook_done.json); refuses unless a US session is open now and
it is >= 09:31 ET; every ETF must have a 1-minute bar from TODAY's session <= 10 min old (fail
closed, retry at the next scheduled fire). Logs before/after to trend_core_log + DEPLOY_LOG.
"""
import datetime as dt, json, os, sys
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import trend_core as tc

DONE = os.path.join(tc.STATE, "trend_core_rebook_done.json")
DEPLOY_LOG = os.path.expanduser("~/.tfo_swing/DEPLOY_LOG.txt")
ET = tc.trend18.ET
EARLIEST_ET_DATE = dt.date(2026, 10, 28)
MAX_BAR_AGE_S = 600


def session_open(now_utc):
    et = now_utc.astimezone(ET)
    if et.date() < EARLIEST_ET_DATE:
        return False, "before %s ET" % EARLIEST_ET_DATE
    try:
        if not tc.trend18.is_session(et.date()):
            return False, "%s is not a US session" % et.date()
    except Exception as e:
        return False, "calendar error %s" % e
    if (et.hour, et.minute) < (9, 31) or (et.hour, et.minute) >= (16, 0):
        return False, "outside 09:31-16:00 ET (now %s ET)" % et.strftime("%H:%M")
    return True, "open"


def live_prices(syms, now_utc):
    """{sym: {px, bar_utc}} from today's 1-minute Yahoo bars, fail closed."""
    import yfinance as yf
    out = {}
    et_date = now_utc.astimezone(ET).date()
    for s in syms:
        h = tc.trend18._bounded(lambda: yf.Ticker(s).history(period="1d", interval="1m", auto_adjust=False),
                                cap=tc.trend18.YF_TIMEOUT_S + 10)
        h = h.dropna(subset=["Close"])
        if not len(h):
            raise RuntimeError("%s: no 1m bars" % s)
        ts = h.index[-1].to_pydatetime()
        age = (now_utc - ts.astimezone(dt.timezone.utc)).total_seconds()
        if ts.astimezone(ET).date() != et_date or age > MAX_BAR_AGE_S:
            raise RuntimeError("%s: last 1m bar %s is not today's session or %.0fs old" % (s, ts, age))
        out[s] = {"px": float(h["Close"].iloc[-1]), "bar_utc": ts.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "age_s": round(age, 1), "kind": "yahoo_1m_session"}
    return out


def plan(L, prices):
    rows = []
    for f in L.get("fills") or []:
        if f.get("reason") != "entry" or f.get("side") != "BUY" or f["inst"] == "XBT/USD":
            continue
        p = prices[f["inst"]]["px"]
        new_sh = round(float(f["notional_aud"]) * float(f["audusd"]) / p, 4)
        rows.append({"inst": f["inst"], "old_shares": f["shares"], "old_px_usd": f["px_usd"],
                     "new_shares": new_sh, "new_px_usd": round(p, 4), "notional_aud": f["notional_aud"],
                     "cost_aud": f["cost_aud"], "audusd": f["audusd"], "bar_utc": prices[f["inst"]]["bar_utc"]})
    return rows


def apply(L, rows, now_iso):
    before = {"equity_aud": L["equity_aud"], "cash_aud": L["cash_aud"], "holdings": dict(L["holdings"]),
              "entry_px_usd": dict(L["entry_px_usd"])}
    cash0 = float(L["cash_aud"])
    for r in rows:
        i = r["inst"]
        L["holdings"][i] = float(L["holdings"].get(i, 0)) - float(r["old_shares"]) + r["new_shares"]
        L["entry_px_usd"][i] = r["new_px_usd"]
        L["mark_px_usd"][i] = r["new_px_usd"]
        L.setdefault("mark_source", {})[i] = "rebook yahoo_1m_session"
        for f in L["fills"]:
            if f.get("reason") == "entry" and f["inst"] == i:
                f.update({"rebooked_utc": now_iso, "orig_shares": r["old_shares"], "orig_px_usd": r["old_px_usd"],
                          "shares": r["new_shares"], "px_usd": r["new_px_usd"],
                          "rebook_note": "re-booked at live US-session price (no-backdating fix)"})
        tc._append_fill({"id": "TC-REBOOK-%s" % i, "utc": now_iso, "wita": tc.wita(), "reason": "rebook",
                         "inst": i, "side": "REBOOK", "shares": r["new_shares"], "px_usd": r["new_px_usd"],
                         "notional_aud": r["notional_aud"], "cost_aud": 0.0, "audusd": r["audusd"],
                         "leg": "rebook", "paper_only": True})
    assert abs(float(L["cash_aud"]) - cash0) < 0.005, "cash must be unchanged"
    assert float(L["cash_aud"]) >= tc.SPARE_AUD - 1.0, "spare breach"
    L["mark_utc"] = now_iso
    L["entry_rebooked_utc"] = now_iso
    tc.restrike(L)
    after = {"equity_aud": L["equity_aud"], "cash_aud": L["cash_aud"], "holdings": dict(L["holdings"]),
             "entry_px_usd": dict(L["entry_px_usd"])}
    L["rebook"] = {"utc": now_iso, "wita": tc.wita(), "rows": rows, "before": before, "after": after,
                   "why": "27-Oct closing marks used while US closed; re-booked at live 28-Oct session prices"}
    return before, after


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="show plan with latest prices, ignore session guard, no writes")
    a = ap.parse_args(argv)
    now = tc.now_utc()
    if os.path.exists(DONE) and not a.dry_run:
        w = json.load(open(DONE)).get("wita"); tc.log("rebook: already done (%s) — no-op" % w); print("rebook: already done (%s) — no-op" % w); return 0
    ok, why = session_open(now)
    if not ok and not a.dry_run:
        tc.log("rebook: waiting — %s" % why); print("rebook: waiting —", why); return 0
    L = tc.load()
    syms = [f["inst"] for f in L.get("fills") or [] if f.get("reason") == "entry" and f["inst"] != "XBT/USD"]
    if a.dry_run:
        y = tc.trend18.yahoo_prices(syms)
        prices = {k: {"px": v["px"], "bar_utc": v["utc"]} for k, v in y.items()}
        for r in plan(L, prices):
            print("DRY", r)
        print("session guard:", ok, why); return 0
    try:
        prices = live_prices(syms, now)
    except Exception as e:
        tc.log("rebook: price guard failed, will retry next fire — %s" % e); print("rebook: price guard failed —", e); return 0
    rows = plan(L, prices)
    before, after = apply(L, rows, tc.iso(now))
    tc.save(L); tc._hist(L); tc.write_status(L)
    try:  # shadow peaks restart from the re-booked entry
        import trend_core_shadow as tcs
        S = tcs.load(); S["pos"] = {}; S["rebook_reset_wita"] = tc.wita(); tc.atomic_json(tcs.SHADOW, S); tcs.run()
    except Exception as e:
        tc.log("rebook: shadow reset failed %s" % e)
    msg = "TREND-CORE REBOOK done: equity A$%.2f -> A$%.2f, cash A$%.2f unchanged; %s" % (
        before["equity_aud"], after["equity_aud"], after["cash_aud"],
        "; ".join("%s %.4f@%.2f -> %.4f@%.4f" % (r["inst"], r["old_shares"], r["old_px_usd"], r["new_shares"], r["new_px_usd"]) for r in rows))
    tc.log(msg)
    tc.atomic_json(DONE, {"utc": tc.iso(now), "wita": tc.wita(), "rows": rows, "before": before["equity_aud"], "after": after["equity_aud"]})
    with open(DEPLOY_LOG, "a") as f:
        f.write("%s [pt1571u] %s | PAPER only | one-shot (state/trend_core_rebook_done.json)\n" % (tc.wita(), msg))
    try:
        import audit
        audit.deploy("DEPLOY_LOG.txt", tag="rebook", note=msg, desk="tfo")
    except Exception:
        pass
    print(msg); return 0


if __name__ == "__main__":
    sys.exit(main())
