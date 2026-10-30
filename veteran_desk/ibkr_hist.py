"""Read-only IBKR daily bars (paper IB Gateway :4002). Historical data ONLY - readonly=True client, no order API used. 2026-10-29 ops (Brett 08:14 WITA)."""
import sys, json
def daily_bars(syms, end_date=None, days=5, port=4002, client_id=87):
    from ib_async import IB, Stock
    ib = IB(); ib.connect("127.0.0.1", port, clientId=client_id, readonly=True, timeout=15)
    out = {}
    try:
        for s in syms:
            try:
                c = Stock(s, "SMART", "USD"); ib.qualifyContracts(c)
                bs = ib.reqHistoricalData(c, endDateTime="", durationStr=f"{days} D", barSizeSetting="1 day", whatToShow="TRADES", useRTH=True, formatDate=1, timeout=30)
                out[s] = [(str(b.date), b.open, b.high, b.low, b.close, float(b.volume)) for b in bs]
            except Exception as e: out[s] = {"err": str(e)[:120]}
    finally: ib.disconnect()
    return out
def fx_mid(pairs, port=4002, client_id=88):
    """fxsize 2026-10-29: FALLBACK ONLY (Yahoo first). Last 1-hour MIDPOINT for e.g. AUDJPY; readonly, no orders. {pair: rate|None}."""
    from ib_async import IB, Forex
    ib = IB(); ib.connect("127.0.0.1", port, clientId=client_id, readonly=True, timeout=15); out = {}
    try:
        for pr in pairs:
            try:
                bs = ib.reqHistoricalData(Forex(pr), endDateTime="", durationStr="1 D", barSizeSetting="1 hour", whatToShow="MIDPOINT", useRTH=False, formatDate=1, timeout=30)
                out[pr] = float(bs[-1].close) if bs else None
            except Exception: out[pr] = None
    finally: ib.disconnect()
    return out
if __name__ == "__main__":
    if sys.argv[1:2] == ["--fx"]: print(json.dumps(fx_mid(sys.argv[2:])))
    else: print(json.dumps(daily_bars(sys.argv[1:])))
