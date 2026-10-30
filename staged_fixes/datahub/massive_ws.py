"""Massive real-time stocks websocket -> data_hub (PAPER infra, 2026-10-30 Brett FULL AUTH). Trades (T.*) + quotes (Q.*) for the US symbols the
desks hold/arm plus a core list; writes state/data_hub/massive_ws_last.json (atomic, ~1s) for data_hub.ws_quote(). Benzinga news has no
websocket on this plan (socket.massive.com/news = 404), so it is polled via the hub's REST limiter every 60s into state/data_hub/news.jsonl.
Key read in-process from ~/.tfo_secrets/massive.env, never printed. Exits non-zero on auth failure so launchd (KeepAlive, 30s throttle) restarts it."""
import json, os, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import websocket, data_hub as H
URL = "wss://socket.massive.com/stocks"; LAST = H.WS_LAST; NEWS = os.path.join(H.HUB, "news.jsonl"); BASE = H.BASE
CORE = ["SPY", "QQQ", "IWM", "DIA", "UPRO", "TQQQ", "UGL", "TMF", "SOXL", "SMH", "SOXX", "XLE", "GLD", "TLT", "DGRO"]
def held_symbols():
    out = set(CORE)
    for f in ("reports/veteran_desk_book.json", "state/thf/thf_book.json"):
        try:
            D = json.load(open(os.path.join(BASE, f)))
            for B in D.get("seats", {}).values():
                for p in B.get("positions", {}).values():
                    if p.get("status") in ("armed", "open") and H.is_us(p.get("ticker", "")): out.add(p["ticker"].upper())
        except Exception: pass
    try: out |= set(json.load(open(os.path.join(H.HUB, "ws_symbols.json"))))
    except Exception: pass
    return sorted(out)
last, lock, subs = {}, threading.Lock(), set()
def flush():
    while True:
        time.sleep(1)
        with lock: snap = dict(last)
        tmp = LAST + ".tmp"; json.dump(snap, open(tmp, "w")); os.replace(tmp, LAST)
def news_poll():
    seen = set()
    while True:
        try:
            j = H.fetch("massive", "/benzinga/v2/news", dict(limit=50, sort="published.desc"), ttl=50, caller="massive_ws_news")
            with open(NEWS, "a") as f:
                for r in j.get("results") or []:
                    k = r.get("benzinga_id") or r.get("id") or r.get("url")
                    if k and k not in seen: seen.add(k); f.write(json.dumps(dict(r, feed_source="massive_benzinga")) + "\n")
        except Exception as e: print("news poll:", str(e)[:120], flush=True)
        time.sleep(60)
def on_msg(ws, m):
    for e in json.loads(m):
        ev = e.get("ev")
        if ev == "status":
            print("status", e.get("status"), e.get("message", "")[:80], flush=True)
            if e.get("status") == "auth_failed": os._exit(2)
        elif ev == "T":
            with lock: d = last.setdefault(e["sym"], {}); d.update(p=e["p"], t=e["t"], src="massive_ws")
        elif ev == "Q":
            with lock: d = last.setdefault(e["sym"], {}); d.update(bp=e.get("bp"), ap=e.get("ap"), qt=e.get("t")); d.setdefault("t", e.get("t")); d.setdefault("p", (e.get("bp", 0) + e.get("ap", 0)) / 2)
def resub(ws):
    while True:
        want = set(held_symbols()); add = want - subs
        if add: ws.send(json.dumps(dict(action="subscribe", params=",".join("T.%s,Q.%s" % (s, s) for s in sorted(add))))); subs.update(add)
        time.sleep(60)
def on_open(ws):
    ws.send(json.dumps(dict(action="auth", params=H._key("massive")))); time.sleep(1)
    threading.Thread(target=resub, args=(ws,), daemon=True).start()
if __name__ == "__main__":
    threading.Thread(target=flush, daemon=True).start(); threading.Thread(target=news_poll, daemon=True).start()
    ws = websocket.WebSocketApp(URL, on_open=on_open, on_message=on_msg, on_error=lambda w, e: print("ws error", type(e).__name__, flush=True))
    ws.run_forever(ping_interval=20, ping_timeout=10)
    sys.exit(1)   # closed -> launchd restarts
