"""read-only IBKR daily bars for LSE listings (GBP, prices in pence). no order API."""
import sys, json
from ib_async import IB, Stock
ib = IB(); ib.connect("127.0.0.1", 4002, clientId=91, readonly=True, timeout=15); out = {}
try:
    for s in sys.argv[1:]:
        try:
            c = Stock(s[:-2].replace(".", " ").replace("-", " "), "LSE", "GBP"); q = ib.qualifyContracts(c)
            if not q: out[s] = {"err": "unqualified"}; continue
            bs = ib.reqHistoricalData(c, endDateTime="", durationStr="5 D", barSizeSetting="1 day", whatToShow="TRADES", useRTH=True, formatDate=1, timeout=30)
            out[s] = [(str(b.date), b.open, b.high, b.low, b.close, float(b.volume)) for b in bs]
        except Exception as e: out[s] = {"err": str(e)[:120]}
finally: ib.disconnect()
print(json.dumps(out))
