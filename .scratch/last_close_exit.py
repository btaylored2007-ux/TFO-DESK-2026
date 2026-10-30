import sys, json, urllib.request, datetime as dt
sys.path.insert(0, "/Users/test/Applications/MultiVenueDesk")
import tfo_ticket as tt
def meta(sym):
    u = "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1d&range=5d" % sym
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=15))["chart"]["result"][0]["meta"]
syms = sys.argv[1].split(","); ex = "--execute" in sys.argv
fx = {"JPY": 1 / meta("JPY=X")["regularMarketPrice"], "AUD": meta("AUDUSD=X")["regularMarketPrice"], "USD": 1.0}
rows = {r["sym"]: r for r in tt.scan(only=syms) if r["book"] == "fundamental_book"}
tot = 0
for s in syms:
    r = rows.get(s); m = meta(s); c = m["regularMarketPrice"]; ccy = m.get("currency", "USD")
    t = dt.datetime.fromtimestamp(m["regularMarketTime"], dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    pxu = c * fx[ccy]; p = tt.load("fundamental_book")["positions"][s]; e0 = p.get("rail_orig_entry_usd") or float(p["entry_px_usd"])
    pnl = float(p["units"]) * (pxu - float(p["entry_px_usd"])); tot += pnl
    print("%s close %s %s @ %s -> $%.4f  entry $%.4f  ret %.2f%%  est pnl $%.2f  units %.4f" % (s, c, ccy, t, pxu, e0, 100 * (pxu / e0 - 1), pnl, float(p["units"])))
    if ex and r and "_q" in r:
        q = dict(r["_q"], px=c, px_usd=pxu, ccy=ccy, quote_utc=t, paper_price_class="LAST_CLOSE", session="close")
        r.update(_q=q, tape="LAST_CLOSE", rail="FLAT", exit_rule="FUNDER_DIRECTED_EXIT_AT_LAST_CLOSE", rail_level_usd=None, ret_orig_pct=round(100 * (pxu / e0 - 1), 2))
        print("  ->", tt.execute(r))
print("est total pnl %.2f" % tot)
