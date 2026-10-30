"""Box relay fallback (PAPER, public data only, no keys). Kraken + Yahoo are blocked from this Mac's network
(api.kraken.com resolves to 202.169.44.80, an ISP block address). The Grok Bot box (Linux) fetches them and the agent copies
~/.tfo_swing/box_relay/relay_snapshot.json here (CopyFromBox). Use ONLY after a direct fetch fails:
    try: j = direct()
    except Exception: j = box_relay.kraken("Ticker", "XBTUSD") / box_relay.yahoo_chart("LOGI")
Each result carries _relay_asof_utc; callers must treat data older than max_age_s as unavailable (returns None)."""
import json, os, time, datetime as dt
SNAP = os.path.expanduser("~/.tfo_swing/box_relay/relay_snapshot.json")
def _load(max_age_s):
    try: j = json.load(open(SNAP))
    except Exception: return None
    t = dt.datetime.strptime(j["generated_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    return j if time.time() - t.timestamp() <= max_age_s else None
def kraken(endpoint, pair, interval=1440, max_age_s=900):
    j = _load(max_age_s)
    if not j: return None
    k = j["kraken"].get("ticker") if endpoint == "Ticker" else j["kraken"].get(f"ohlc_{pair}_{interval}")
    return k and dict(k, _relay_asof_utc=j["generated_utc"])
def yahoo_chart(sym, max_age_s=3600):
    j = _load(max_age_s)
    y = j and j["yahoo"].get(sym)
    return y and dict(y, _relay_asof_utc=j["generated_utc"])
def status():
    try: j = json.load(open(SNAP)); return dict(asof=j["generated_utc"], kraken=len(j["kraken"]), yahoo=sorted(j["yahoo"]), errors=j["errors"])
    except Exception as e: return dict(error=str(e))
