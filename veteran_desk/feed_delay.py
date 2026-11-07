"""2026-10-30 THF review item 2 (HT): honest feed-delay labels -> state/feed_delay.json. Engine/seats must treat any quote from a venue with
delayed=True as DELAYED (never live): quote_age_min = now - quote_time, and a venue's known delay floor applies even if a probe looks fresh.
Measured each run from Yahoo chart meta (regularMarketTime) on liquid probes. Checked 2026-10-30 17:38 WITA: no free faster source on this desk -
IBKR paper gateway has NO EU/UK real-time subscription (error 354; delayed-only, LSE 1-min hist last bar ~16 min old; IBIS/SBF no permission);
Stooq is end-of-day only. Closing this needs a paid real-time EU/UK feed (e.g. IBKR LSE/Euronext/XETRA L1 subscription on a live-funded account)."""
import json, os, datetime as dt, urllib.request
B = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); OUT = os.path.join(B, "state", "feed_delay.json")
KNOWN_FLOOR = {"LSE": 15, "Euronext": 15, "XETRA": 15, "BME": 15, "SIX": 15, "MILAN": 20, "NORDIC": 0, "US": 0, "ASX": 20, "TSE": 20, "HKEX": 15, "KRX": 20, "TWSE": 20, "SGX": 10, "NSE": 15}
PROBES = {"LSE": ["HSBA.L", "SHEL.L"], "Euronext": ["BNP.PA", "ASML.AS"], "XETRA": ["SAP.DE", "SIE.DE"], "BME": ["SAN.MC"], "SIX": ["NESN.SW"], "MILAN": ["ISP.MI"],
          "NORDIC": ["NOVO-B.CO", "VOLV-B.ST"], "US": ["SPY", "AAPL"], "ASX": ["BHP.AX"], "TSE": ["7203.T"], "HKEX": ["0700.HK"], "KRX": ["005930.KS"], "TWSE": ["2330.TW"], "SGX": ["D05.SI"], "NSE": ["RELIANCE.NS"]}
def probe(s):
    u = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=1m" % s
    m = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=15).read())["chart"]["result"][0]["meta"]
    tp = (m.get("currentTradingPeriod") or {}).get("regular") or {}; now = dt.datetime.now(dt.timezone.utc).timestamp()
    open_ = (tp.get("start") or 0) <= now <= (tp.get("end") or 0)
    return dict(sym=s, quote_utc=dt.datetime.fromtimestamp(m["regularMarketTime"], dt.timezone.utc).isoformat(), age_min=round((now - m["regularMarketTime"]) / 60, 1), session_open=open_)
def build():
    now = dt.datetime.now(dt.timezone.utc); V = {}
    for v, L in PROBES.items():
        ps = []
        for s in L:
            try: ps.append(probe(s))
            except Exception as e: ps.append(dict(sym=s, error=str(e)[:80]))
        live = [p["age_min"] for p in ps if p.get("session_open") and "age_min" in p]
        meas = min(live) if live else None; floor = KNOWN_FLOOR.get(v, 15)
        V[v] = dict(delay_min=max(floor, meas or 0), measured_age_min=meas, known_floor_min=floor, delayed=max(floor, meas or 0) > 2,
                    session_open=bool(live), probes=ps)
    J = dict(built_utc=now.isoformat(), source="yahoo chart v8 probe; EODHD Cboe Europe websocket is primary for LSE/EU when health.healthy (Yahoo fallback only)",
             rule="quote from a venue with delayed=True is DELAYED, never live: label it, use quote_utc not wall clock, and never grade a setup on a breakout the delayed tape could not have seen. Fills already use book.stale_limit_min (delay+5).",
             venues=V)
    apply_eu_ws(J)
    tmp = OUT + ".tmp"; json.dump(J, open(tmp, "w"), indent=1); os.replace(tmp, OUT); return J
EU_VENUES = {"LSE", "Euronext", "XETRA", "BME", "SIX", "MILAN", "NORDIC"}
def label(sym_venue):
    """Yahoo floor unless the EODHD Cboe Europe websocket is healthy for this venue (then delay_min 0)."""
    if sym_venue in EU_VENUES:
        try:
            from veteran_desk import eodhd_eu_ws as EU
            h = EU.health()
            if h.get("healthy") and sym_venue in (h.get("venues_live") or []):
                return dict(delayed=False, delay_min=0, src="eodhd_eu_ws")
        except Exception: pass
    try: v = json.load(open(OUT))["venues"].get(sym_venue) or {}
    except Exception: v = {}
    if sym_venue in EU_VENUES:
        delay = max(KNOWN_FLOOR.get(sym_venue, 15), float(v.get("delay_min") or 0))
        return dict(delayed=delay > 2, delay_min=delay, src="yahoo_fallback")
    return dict(delayed=v.get("delayed", True), delay_min=v.get("delay_min", 20))
def apply_eu_ws(J):
    """Overlay feed_delay.json: a healthy EU websocket is primary, so those venues are not the 15m Yahoo tape."""
    try:
        from veteran_desk import eodhd_eu_ws as EU
        h = EU.health()
        live = set(h.get("venues_live") or [])
        J["eu_ws"] = dict(healthy=bool(h.get("healthy")), venues_live=sorted(live), why=h.get("why"), age_sec=h.get("age_sec"))
        if h.get("healthy"):
            for v in live:
                slot = (J.get("venues") or {}).get(v)
                if slot is None: continue
                slot["delayed"] = False; slot["delay_min"] = 0; slot["eu_ws"] = "primary"
    except Exception: pass
    return J
if __name__ == "__main__":
    J = build(); print(json.dumps({k: (v["delay_min"], v["measured_age_min"], v["session_open"]) for k, v in J["venues"].items()}))
