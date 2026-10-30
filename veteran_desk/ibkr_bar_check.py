"""2026-10-29 PAPER read-only: IBKR daily TRADES bars for given (sym, dates) to confirm/refute stored hi/lo outliers.
readonly=True client, historical data only, no order API. Usage: python ibkr_bar_check.py in.json out.json  (in: {sym: [dates]})."""
import sys, json, datetime as dt
from ib_async import IB, Stock
NORDIC = {"ST": ("SFB", "SEK"), "CO": ("CPH", "DKK"), "HE": ("HEX", "EUR"), "OL": ("OSE", "NOK")}
def contract(s):
    if "." not in s: return Stock(s.replace("-", " "), "SMART", "USD")
    base, suf = s.rsplit(".", 1); suf = suf.upper()
    if suf == "L": return Stock(base.replace(".", " ").replace("-", " "), "LSE", "GBP")
    if suf in ("TO", "V"): return Stock(base.replace("-UN", ".UN").replace("-", "."), "SMART", "CAD", primaryExchange="TSE" if suf == "TO" else "VENTURE")
    if suf in ("NS", "BO"): return Stock(base.replace("-", "_"), "NSE", "INR")
    if suf == "AX": return Stock(base, "ASX", "AUD")
    if suf == "HK": return Stock(str(int(base)) if base.isdigit() else base, "SEHK", "HKD")
    if suf == "DE": return Stock(base, "IBIS", "EUR")
    if suf == "SI": return Stock(base, "SGX", "SGD")
    if suf in NORDIC: ex, cc = NORDIC[suf]; return Stock(base.replace("-", " "), ex, cc)
    return None
def main():
    req = json.load(open(sys.argv[1])); out = {}
    ib = IB(); ib.connect("127.0.0.1", 4002, clientId=94, readonly=True, timeout=20)
    try:
        for s, dates in req.items():
            try:
                c = contract(s)
                if c is None: out[s] = {"err": "no contract mapping"}; continue
                q = ib.qualifyContracts(c)
                if (not q or not getattr(c, "conId", 0)) and s.upper().endswith(".L") and len(s) <= 4:   # IBKR LSE 1-2 letter tickers carry a trailing dot (AV., BA., BP., RR.)
                    c = Stock(s[:-2] + ".", "LSE", "GBP"); q = ib.qualifyContracts(c)
                if not q or not getattr(c, "conId", 0): out[s] = {"err": "unqualified"}; continue
                days = (dt.date.today() - dt.date.fromisoformat(min(dates))).days + 7
                dur = "%d D" % days if days <= 365 else "1 Y"
                bs = ib.reqHistoricalData(c, endDateTime="", durationStr=dur, barSizeSetting="1 day", whatToShow="TRADES", useRTH=True, formatDate=1, timeout=60)
                by = {str(b.date): [b.open, b.high, b.low, b.close, float(b.volume)] for b in bs}
                out[s] = {"bars": {d: by.get(d) for d in dates}, "n": len(bs), "conId": c.conId, "exch": c.primaryExchange or c.exchange, "ccy": c.currency}
            except Exception as e: out[s] = {"err": str(e)[:160]}
            json.dump(out, open(sys.argv[2], "w"))
    finally: ib.disconnect()
    json.dump(out, open(sys.argv[2], "w")); print(json.dumps({"syms": len(out), "err": sum(1 for v in out.values() if "err" in v)}))
if __name__ == "__main__": main()
