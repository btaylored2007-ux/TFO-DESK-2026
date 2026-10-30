#!/usr/bin/env python3
"""post_trade_entry_px.py -- how the post-trade review reads ENTRY PRICE (TFO Risk audit 4 Oct 2026, Head Trader adopted 21:30 AEDT).

Problem: the week-of-4-Oct review showed 8766.T entry_px 51.25 (the BUY ticket's PRE-split px_usd; Tokio Marine went ex a
15-for-1 split on 29 Sep) next to the POST-split exit qty 74,929.66 and exit px 3.24, so the row read as a -94% trade while the
ticket P&L (-$13,130.19 on a $256k clip, i.e. the designed -5% stop) reconciles.
Rule: entry price = the ticket fill in the SAME share basis as the exit = exit ticket principal_usd / exit ticket qty (the cost
the ledger booked for the shares that were sold). The BUY ticket px_usd is used only when no exit ticket carries principal and
qty, and a split is flagged when the two differ by a near-integer ratio.

  python3 post_trade_entry_px.py fix state/post_trade/week_closes_YYYY-MM-DD.jsonl [--write]   re-derive entry_px for every close
"""
import argparse, json, os, sys
BASE = os.path.dirname(os.path.abspath(__file__))
TOL = 0.01


def _f(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def entry_px_from_tickets(entry_ticket, exit_tickets):
    """-> (entry_px_usd in the exit share basis, detail dict)."""
    ex = [t for t in (exit_tickets or []) if _f(t.get("principal_usd")) and _f(t.get("qty"))]
    raw = _f((entry_ticket or {}).get("px_usd"))
    if ex:
        px = sum(_f(t["principal_usd"]) for t in ex) / sum(_f(t["qty"]) for t in ex)
        d = {"entry_px_usd": px, "basis": "exit ticket principal_usd / qty (ticket fill in exit share basis)", "entry_ticket_px_usd_raw": raw}
        if raw and abs(raw / px - 1) > TOL:
            r = raw / px
            k = round(r) if r >= 1 else 1.0 / round(1.0 / r)
            d["split_factor"] = k if abs(r / k - 1) < 0.02 else round(r, 6)
            d["note"] = "BUY ticket px_usd %.6g is in a different share basis (ratio %.4g, likely a %s split); exit-basis fill used" % (raw, r, ("%g-for-1" % k) if r >= 1 else "reverse")
        return px, d
    return raw, {"entry_px_usd": raw, "basis": "BUY ticket px_usd (no exit ticket principal/qty)", "entry_ticket_px_usd_raw": raw}


def _tickets(book_key):
    p = os.path.join(BASE, "reports", book_key + ".json")
    try: return json.load(open(p)).get("head_trader_tickets", [])
    except Exception: return []


def fix_closes(rows, eq_tickets, cr_tickets):
    """Re-derive entry_px on review rows from ledger tickets. -> (rows, corrections)."""
    out = []
    for r in rows:
        sym = r.get("symbol"); tu = r.get("ts_utc")
        pool = cr_tickets if r.get("book") == "crypto" else eq_tickets
        key = "pair" if r.get("book") == "crypto" else "sym"
        exits = [t for t in pool if t.get(key) == sym and t.get("utc") == tu and t.get("action") != "BUY"]
        if not exits: continue
        buys = [t for t in pool if t.get(key) == sym and t.get("action") == "BUY" and t.get("utc", "") <= tu]
        px, d = entry_px_from_tickets(buys[-1] if buys else None, exits)
        old = _f(r.get("entry_px"))
        if px and old and abs(old / px - 1) > TOL:
            r["entry_px_raw_report"] = old; r["entry_px"] = px; r["entry_px_basis"] = d["basis"]
            if d.get("split_factor"): r["split_factor"] = d["split_factor"]
            r["entry_px_correction"] = "TFO Risk audit 4 Oct 2026: entry_px re-read from the ticket fill in the exit share basis (%s)" % (d.get("note") or d["basis"])
            out.append({"symbol": sym, "ts_utc": tu, "old_entry_px": old, "new_entry_px": round(px, 8), **{k: d[k] for k in ("split_factor", "note") if k in d}})
    return rows, out


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["fix"]); ap.add_argument("path"); ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    rows = [json.loads(l) for l in open(a.path) if l.strip()]
    rows, corr = fix_closes(rows, _tickets("fundamental_book"), _tickets("tfo_ht_crypto"))
    print(json.dumps(corr, indent=1))
    if a.write and corr:
        tmp = a.path + ".tmp"
        with open(tmp, "w") as f:
            for r in rows: f.write(json.dumps(r) + "\n")
        os.replace(tmp, a.path); print("wrote %d correction(s) to %s" % (len(corr), a.path))


if __name__ == "__main__":
    main()
