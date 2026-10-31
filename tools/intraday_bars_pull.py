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


def universe(extra=()):
    cap = int(os.environ.get("INTRADAY_MAX_SYMBOLS", "80")); out = []
    def add(s):
        s = str(s or "").upper().strip()
        if s and s not in out and not s.startswith("KRAKEN:") and not s.endswith("=F") and not s.endswith("=X") and not s.startswith("^"): out.append(s)
    for s in list(extra) + CORE + PROBES: add(s)
    for rel in ("reports/veteran_desk_book.json", "state/thf/thf_book.json"):
        try: book = json.load(open(os.path.join(BASE, rel)))
        except Exception: continue
        seats = book.get("seats") or {}
        blocks = list(seats.values()) if isinstance(seats, dict) else []
        for B in blocks:
            for p in (B.get("positions") or {}).values():
                if isinstance(p, dict) and p.get("ticker"): add(p["ticker"])
        if len(out) >= cap: break
    return out[:cap]


def main(argv):
    syms = [s.upper() for s in argv] if argv else universe()
    imported = 0
    try: imported = IB.import_history()
    except Exception as e: print("history import skipped:", type(e).__name__, flush=True)
    n = IB.pull_universe(syms)
    h = IB.health()
    h.update(pulled_symbols=len(syms), rows_written=n, history_rows_imported=imported, utc=dt.datetime.now(UTC).isoformat())
    os.makedirs(os.path.dirname(IB.path()), exist_ok=True)
    rep = os.path.join(os.path.dirname(IB.path()), "health.json")
    tmp = rep + ".tmp"; json.dump({k: v for k, v in h.items() if k != "path"}, open(tmp, "w"), indent=1); os.replace(tmp, rep)
    print(json.dumps({k: h[k] for k in ("present", "n_1m", "n_5m", "n_15m", "symbols", "newest_utc", "rows_written", "history_rows_imported")}, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
