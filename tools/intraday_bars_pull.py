#!/usr/bin/env python3
"""Pull 1m/5m/15m bars into data/intraday/bars.sqlite. PAPER only. tfo_paidrt_20261031.

US names: Massive aggregates. Other names data_layer can map (LSE, EU, ASX, ...): EODHD intraday.
Also imports recent minute rows already stored in data/history/history.sqlite (Massive hour/minute backfill).
Does not write pit.sqlite. Keys stay in ~/.tfo_secrets and are never printed.

  python3 tools/intraday_bars_pull.py                 # core + held names
  python3 tools/intraday_bars_pull.py AAPL VOD.L      # explicit symbols
Exit 0 even when a key is missing (health file says so). Launchd should not crash-loop on a missing key.
"""
import datetime as dt, json, os, sys
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, BASE)
from veteran_desk import intraday_store as IB
UTC = dt.timezone.utc
CORE = ["SPY", "QQQ", "IWM", "DIA", "TLT", "GLD", "XLE", "XLF", "XLK", "SMH", "NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "TSLA", "AMD", "AVGO", "JPM"]
PROBES = ["HSBA.L", "SHEL.L", "SAP.DE", "BNP.PA", "ASML.AS", "SAN.MC", "ISP.MI", "NESN.SW"]


def _read(rel):
    try:return json.load(open(os.path.join(BASE,rel)))
    except (OSError,ValueError):return {}
def universe(extra=(), rotate=0, entitled=None, held=()):
    cap=max(1,int(os.environ.get("INTRADAY_MAX_SYMBOLS","80")));primary=[];optional=[]
    def add(out,s):
        s=str(s or "").upper().strip()
        if s and not s.startswith(("KRAKEN:","^")) and not s.endswith(("=F","=X")) and s not in out:out.append(s)
    for sym in extra:add(primary,sym)
    # This is the Singapore THF collector. Own positions and current engine candidates have first claim.
    for block in (_read("state/thf/thf_book.json").get("seats") or {}).values():
        for row in (block.get("positions") or {}).values():
            if row.get("status") in ("open","armed"):add(primary,row.get("ticker"))
    directory=os.path.join(BASE,"state/vd_engine/top10")
    for filename in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
        if not filename.endswith(".json"):continue
        for row in _read("state/vd_engine/top10/"+filename).get("top10") or []:
            sym=row.get("sym")
            if sym and IB.is_us(sym):add(primary,sym)  # US-only additions spend no EODHD calls.
    # Original bank/held-name feed probes keep enough baseline history even between engine rankings.
    for sym in ("WFC","BAC","JPM","C","GS","MS","KO","CVS","IMO","RVTY"):add(primary,sym)
    for sym in CORE:add(primary,sym)
    for sym in PROBES:
        if sym not in primary:add(optional,sym)
    us=[s for s in primary if IB.is_us(s)]
    non_us_held=[s for s in list(held)+[s for s in primary if not IB.is_us(s)]]
    research=[s for s in optional if not IB.is_us(s)]
    from veteran_desk.feed_health import allocate_rest
    eu_cap=max(0,int(os.environ.get("EODHD_REST_CAP","12")))
    plan=allocate_rest(research, non_us_held, eu_cap, rotate=rotate, entitled=entitled)
    plan["quote_stream"]=False
    universe.last_plan=plan
    return us[:cap]+plan["subscribe"]

def main(argv):
    syms = [s.upper() for s in argv] if argv else universe()
    imported = 0
    try: imported = IB.import_history()
    except Exception as e: print("history import skipped:", type(e).__name__, flush=True)
    n = IB.pull_universe(syms)
    h = IB.health(watch=syms)
    h.update(pulled_symbols=len(syms), configured_soft_cap=int(os.environ.get("INTRADAY_MAX_SYMBOLS","80")), required_names_preserved=True, rows_written=n, history_rows_imported=imported, utc=dt.datetime.now(UTC).isoformat())
    os.makedirs(os.path.dirname(IB.path()), exist_ok=True)
    rep = os.path.join(os.path.dirname(IB.path()), "health.json")
    tmp = rep + ".tmp"; json.dump({k: v for k, v in h.items() if k != "path"}, open(tmp, "w"), indent=1); os.replace(tmp, rep)
    print(json.dumps({k: h[k] for k in ("present", "n_1m", "n_5m", "n_15m", "symbols", "newest_utc", "rows_written", "history_rows_imported")}, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
