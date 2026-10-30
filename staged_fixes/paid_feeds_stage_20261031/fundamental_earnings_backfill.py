#!/usr/bin/env python3
"""EODHD earnings dates -> fundamental book positions. PAPER ONLY. HT 2026-10-30.
Default: dry run (prints what it would write).
--write          : stamp every open position with `next_earnings` {date, timing, source, fetched_utc}
                   (informational; no exit rule reads it).
--set-catalyst   : ALSO set `catalyst_date` (+ catalyst_source) where catalyst_date is missing.
                   WARNING: fundamental_exits.catalyst_expired force-closes a position IN FULL
                   CATALYST_GRACE_TRADING_DAYS (5) trading days after catalyst_date. Needs an HT decision.
Only dates EODHD returns are used — never guessed. Takes desk_lock 'fundamental', backs up, atomic save."""
import argparse, datetime as dt, json, os, shutil, sys
BASE = os.environ.get("DESK_BASE", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)
import paid_feeds, desk_lock, fundamental_book as fb

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--write", action="store_true"); ap.add_argument("--set-catalyst", action="store_true")
    a = ap.parse_args()
    today = dt.date.today().isoformat()
    led0 = fb.load_ledger()
    ev = paid_feeds.eodhd_next_earnings(list(led0.get("positions", {})), today=today)
    with desk_lock.ledger_lock("fundamental"):
        led = fb.load_ledger(); changes = []
        for s, p in led.get("positions", {}).items():
            e = ev.get(s)
            if not e:
                changes.append((s, "no EODHD earnings date — left unchanged")); continue
            if a.write: p["next_earnings"] = e
            msg = "next_earnings %s (%s)" % (e["date"], e["timing"])
            if not p.get("catalyst_date"):
                if a.set_catalyst and a.write:
                    p["catalyst_date"] = e["date"]
                    p["catalyst_source"] = {"from": e["source"], "text": "next earnings report_date %s (%s), fiscal period %s" % (e["date"], e["timing"], e["fiscal_period_end"]),
                                            "backfilled_utc": e["fetched_utc"]}
                    msg += " -> catalyst_date SET"
                else:
                    msg += " (catalyst_date missing; would set with --set-catalyst)"
            changes.append((s, msg))
        if a.write:
            bak = fb.LEDGER + ".bak_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.copy2(fb.LEDGER, bak)
            fb.save_ledger(led); print("backup", bak)
    for c in changes: print("%-8s %s" % c)
    print("mode:", "WRITE" if a.write else "DRY-RUN", "| set-catalyst:", a.set_catalyst, "| eodhd calls:", paid_feeds.STATS["eodhd"])

if __name__ == "__main__":
    main()
