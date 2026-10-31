"""2026-10-29 infra: full Kraken 24h traded-value sweep for the crypto seat (Kai) A$500k liquidity screen. PAPER, read-only public data.
System DNS for api.kraken.com is ISP-sinkholed (202.169.44.80 -> connection refused); this ALWAYS goes through kraken_dns (DoH) like feeds.kraken_spot.
Usage: python3 -m veteran_desk.kraken_sweep [min_aud]  -> state/veteran_desk/kraken_24h_all.json (+ prints JSON summary)."""
import datetime as dt, json, os, sys, urllib.request
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, BASE)
OUT = os.path.join(BASE, "state", "veteran_desk", "kraken_24h_all.json")
def _get(u): return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "TFO paper research"}), timeout=20).read())
def sweep(min_aud=500000.0, au=None):
    import kraken_dns; kraken_dns.install()
    from veteran_desk import crypto_universe as CU
    P = _get("https://api.kraken.com/0/public/AssetPairs")["result"]
    usd = {k: v for k, v in P.items() if CU.quote_ok(v.get("quote")) and v.get("status", "online") == "online" and not k.endswith(".d")}
    T = {}; ks = list(usd)
    for i in range(0, len(ks), 100): T.update(_get("https://api.kraken.com/0/public/Ticker?pair=" + ",".join(ks[i:i + 100]))["result"])
    if au is None:
        try:
            from veteran_desk import book as BK; au = BK.audusd()
        except Exception: au = 0.65
    lasts = {}
    for k, t in T.items():
        alt0 = (usd.get(k) or {}).get("altname", k)
        try: lasts[alt0] = float(t["c"][0])
        except Exception: pass
    rows = []; unpriced = []
    for k, t in T.items():
        alt = (usd.get(k) or {}).get("altname", k)
        if CU.is_stable_pair(alt): continue
        try: last = float(t["c"][0]); v24 = float(t["v"][1]); vw = float(t["p"][1]) or last
        except Exception: continue
        qusd = CU.quote_to_usd((usd.get(k) or {}).get("quote"), lasts)
        if not qusd:
            unpriced.append(alt); continue   # cannot prove the A$500k floor; do not invent ADV
        usd24 = v24 * vw * qusd; row = dict(pair=k, alt=alt, quote=CU.norm_quote((usd.get(k) or {}).get("quote")), last=last, vol24h_usd=round(usd24), vol24h_aud=round(usd24 / au), liquid=usd24 / au >= min_aud)
        # 2026-10-29 kailiq: same Ticker call - o (today's open, UTC 00:00), b/a (best bid/ask). Missing/zero -> None, never invented.
        def _f(x):
            try: v = float(x[0] if isinstance(x, list) else x); return v if v > 0 else None
            except Exception: return None
        o, bid, ask = _f(t.get("o")), _f(t.get("b")), _f(t.get("a"))
        row.update(open=o, bid=bid, ask=ask, chg_pct_since_open=round((last / o - 1) * 100, 3) if o else None,
                   spread_bp=round((ask - bid) / ((ask + bid) / 2) * 1e4, 2) if (bid and ask and ask >= bid) else None)
        rows.append(row)
    rows.sort(key=lambda r: -r["vol24h_aud"])
    rep = dict(utc=dt.datetime.now(dt.timezone.utc).isoformat(), src="kraken REST via DoH", audusd=au, min_aud=min_aud, n=len(rows), n_liquid=sum(r["liquid"] for r in rows), pairs=rows, dns=kraken_dns.stats,
               fx_unpriced=unpriced[:40], fx_unpriced_n=len(unpriced), note="All online Kraken quotes. Stable/fiat bases dropped. Unpriced quotes skipped (A$500k floor not invented).")
    os.makedirs(os.path.dirname(OUT), exist_ok=True); tmp = OUT + ".tmp"; json.dump(rep, open(tmp, "w"), indent=1); os.replace(tmp, OUT); return rep
if __name__ == "__main__":
    r = sweep(float(sys.argv[1]) if len(sys.argv) > 1 else 500000.0)
    print(json.dumps(dict(utc=r["utc"], n=r["n"], n_liquid=r["n_liquid"], top=[(x["alt"], x["vol24h_aud"]) for x in r["pairs"][:5]], out=OUT)))
