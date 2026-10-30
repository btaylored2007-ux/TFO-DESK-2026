#!/usr/bin/env python3
"""Build/refresh static/asset_info.json for War Room info bubbles (PAPER dashboard helper).
Reads tickers from hunt48 status + cal bench files; only fetches tickers not yet cached.
Descriptions come from yfinance (longName/sector/longBusinessSummary trimmed to 2 sentences)
or a fixed factual table for Kraken coins / OANDA instruments. Unknown -> 'No description yet'."""
import json, os, re, sys, time
BASE = os.path.dirname(os.path.abspath(__file__))
ST = os.path.join(BASE, "static")
OUT = os.path.join(ST, "asset_info.json")
SRC = ["hunt48_short_status.json", "hunt48_short_claude_status.json", "hunt48_short_gpt_status.json",
       "cal_paper_bench.json", "swing_d4_status.json", "swing_u4_status.json"]
FIXED = {
 "BTC": ("Bitcoin", "Crypto", "The first and largest cryptocurrency, a decentralised digital currency with a fixed 21 million coin supply."),
 "XBT": ("Bitcoin", "Crypto", "The first and largest cryptocurrency, a decentralised digital currency with a fixed 21 million coin supply."),
 "ETH": ("Ethereum", "Crypto", "Cryptocurrency of the Ethereum blockchain, which runs smart contracts and decentralised apps."),
 "SOL": ("Solana", "Crypto", "Cryptocurrency of the Solana blockchain, a high-throughput network for smart contracts and apps."),
 "XRP": ("XRP", "Crypto", "Cryptocurrency of the XRP Ledger, designed for fast cross-border payments."),
 "ADA": ("Cardano", "Crypto", "Cryptocurrency of the Cardano proof-of-stake smart-contract blockchain."),
 "DOGE": ("Dogecoin", "Crypto", "A meme-origin cryptocurrency used for payments and tipping."),
 "LTC": ("Litecoin", "Crypto", "An early Bitcoin-derived cryptocurrency with faster block times."),
 "DOT": ("Polkadot", "Crypto", "Cryptocurrency of Polkadot, a network connecting multiple blockchains."),
 "LINK": ("Chainlink", "Crypto", "Token of Chainlink, a network that feeds outside data (oracles) to blockchains."),
 "AVAX": ("Avalanche", "Crypto", "Cryptocurrency of the Avalanche smart-contract blockchain."),
}
ETF = {"SLV":"Holds physical silver bullion, so its price tracks the silver price.",
       "GLD":"Holds physical gold bullion, so its price tracks the gold price.",
       "XLE":"Tracks the US energy stocks in the S&P 500 (oil, gas and energy services).",
       "XLK":"Tracks the US technology stocks in the S&P 500.",
       "XLV":"Tracks the US health care stocks in the S&P 500.",
       "XLF":"Tracks the US financial stocks in the S&P 500.",
       "SPY":"Tracks the S&P 500 index of large US companies.",
       "QQQ":"Tracks the Nasdaq-100 index of large non-financial Nasdaq companies.",
       "ECH":"Tracks an MSCI index of large and mid-size Chilean companies.",
       "AIPO":"Tracks US-listed companies building electrical grid, AI and power infrastructure."}
CCY = {"USD":"US dollar","AUD":"Australian dollar","EUR":"euro","GBP":"British pound","JPY":"Japanese yen","CHF":"Swiss franc",
       "CAD":"Canadian dollar","NZD":"New Zealand dollar","CNH":"offshore Chinese yuan","SGD":"Singapore dollar","HKD":"Hong Kong dollar",
       "SEK":"Swedish krona","NOK":"Norwegian krone","MXN":"Mexican peso","ZAR":"South African rand"}
MET = {"XAU":("Gold","Spot gold priced in"),"XAG":("Silver","Spot silver priced in"),"XPT":("Platinum","Spot platinum priced in"),
       "XPD":("Palladium","Spot palladium priced in"),"WTICO":("WTI crude oil","US light crude oil (WTI) priced in"),
       "BCO":("Brent crude oil","Brent crude oil priced in"),"NATGAS":("Natural gas","US natural gas priced in")}

def two(s):
    s = re.sub(r"\s+", " ", s or "").strip()
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", s)
    return " ".join(parts[:2])[:400]

def oanda(sym):
    if "_" not in sym: return None
    a, b = sym.split("_", 1)
    if a in MET and b in CCY:
        n, d = MET[a]; return {"name": f"{n} / {b}", "sector": "Commodity (OANDA CFD)", "desc": f"{d} {CCY[b]}s, traded as an OANDA CFD."}
    if a in CCY and b in CCY:
        return {"name": f"{a}/{b}", "sector": "FX (OANDA)", "desc": f"Currency pair: price of one {CCY[a]} in {CCY[b]}s. Long profits if the {CCY[a]} rises against the {CCY[b]}."}
    return None

def tickers():
    out = {}
    for f in SRC:
        try: d = json.load(open(os.path.join(ST, f)))
        except Exception: continue
        for t in (d.get("trades") or []) + (d.get("positions") or []):
            if not isinstance(t, dict): continue
            k = t.get("ticker") or t.get("sym") or t.get("pair") or t.get("symbol")
            if k: out[str(k)] = t.get("yahoo")
    return out

def main():
    try: cache = json.load(open(OUT))
    except Exception: cache = {}
    new = 0
    for k, yh in tickers().items():
        if k in cache and cache[k].get("desc") != "No description yet": continue
        info = oanda(k)
        base = re.split(r"[/_-]", k)[0].upper().removeprefix("X") if k.upper().startswith("XX") else re.split(r"[/_-]", k)[0].upper()
        if not info and base in FIXED and ("/" in k or "USD" in k.upper() or k.upper() == base):
            n, s, d = FIXED[base]; info = {"name": n, "sector": s, "desc": d}
        if not info:
            try:
                import yfinance as yf
                i = yf.Ticker(yh or k).info or {}
                nm = i.get("longName") or i.get("shortName")
                qt = i.get("quoteType", "")
                sec = i.get("sector") or ({"ETF": "ETF", "MUTUALFUND": "Fund", "CRYPTOCURRENCY": "Crypto", "FUTURE": "Futures"}.get(qt, ""))
                if i.get("industry"): sec = f"{sec} · {i['industry']}" if sec else i["industry"]
                desc = two(i.get("longBusinessSummary") or i.get("description") or "")
                if qt == "ETF":
                    cat = i.get("category")
                    desc = ("Exchange-traded fund" + (f" (category: {cat})" if cat else "") + ".")
                    if k in ETF: desc = ETF[k] + " " + desc
                if nm: info = {"name": nm, "sector": sec or "", "desc": desc or "No description yet"}
                time.sleep(0.3)
            except Exception as e:
                print("yf fail", k, e, file=sys.stderr)
        cache[k] = info or {"name": k, "sector": "", "desc": "No description yet"}
        cache[k]["updated"] = time.strftime("%Y-%m-%d")
        new += 1
    if new:
        tmp = OUT + ".tmp"; json.dump(cache, open(tmp, "w"), indent=1, sort_keys=True); os.replace(tmp, OUT)
    print(f"asset_info: {len(cache)} cached, {new} new/updated")
main()
