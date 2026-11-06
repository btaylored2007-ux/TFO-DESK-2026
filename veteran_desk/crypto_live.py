"""seatdata_20261103 (PAPER, data only): live crypto block for the crypto seat's wake pack (desk.pack -> DATA.crypto_live).

WHY (Kai/crypto roll-call 3 Nov 17:02-17:38 WITA, checklist D24): the wake pack had no live bid/ask/spread/intraday volume (newest
daily bar = the last completed UTC day), so the B-breakout checks (entry <= 1.5% from price, volume >= 1.5x, spread <= 0.5%) could
not run; no Kraken futures funding/OI/basis per name and no PF_ perp flag ('a short needs a PF_ perp'). live_quotes skipped every
KRAKEN: symbol by design, and the Coinalyze rows never survived compact_pk's trimming.

SOURCES (all already on the droplet; one extra public call per crypto wake, no keys):
  * quote: ONE Kraken public REST Ticker call for the seat's kept pairs (pair codes from the 4-hourly sweep
    state/veteran_desk/kraken_24h_all.json, so every requested code is valid). Fallbacks, labelled: tfo-kraken websocket ticker
    (/opt/tfo/state/kraken_ticker.json, BTC/ETH/SOL/XRP, used when < 2 min old) -> the sweep row itself (<= 4 h old).
  * 1h bars, PF_ perp funding/OI/basis, Coinalyze funding/OI: the engine's background caches written by thf_crypto_feed
    (state/vd_engine/crypto_1h.json, kraken_futs.json, crypto_derivs.json; refreshed ~every 30 min; no extra calls here).
  * 20-day average daily volume: pit daily bars (Kraken OHLC volume = BASE units), passed in by desk.pack (feats adv20).
Never raises into the caller, never writes, never touches a ledger or key."""
import datetime as dt, json, os, time, urllib.request

TAG = "seatdata_20261103"
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SWEEP = os.path.join(BASE, "state", "veteran_desk", "kraken_24h_all.json")
VDE = os.path.join(BASE, "state", "vd_engine")
H1, KF, DV = (os.path.join(VDE, f) for f in ("crypto_1h.json", "kraken_futs.json", "crypto_derivs.json"))
WS = os.environ.get("THF_KRAKEN_WS_TICKER", "/opt/tfo/state/kraken_ticker.json")
WS_MAP = {"BTC/USD": "XBTUSD", "ETH/USD": "ETHUSD", "SOL/USD": "SOLUSD", "XRP/USD": "XRPUSD"}
CACHE_MAX_S = 4 * 3600; WS_MAX_S = 120; SWEEP_MAX_S = 5 * 3600; MAX_SYMS = 20
SLIM = ("vwap_24h", "trades_24h", "open_utc0", "h1_close", "h1_vol", "h1_bar_utc")   # computed with, not shown (prompt size)
NOTE = ("seatdata_20261103 live crypto block (PAPER). quote = Kraken public Ticker fetched this wake (src says if it fell back to the "
        "websocket or the <=4h sweep). spread_bp = (ask-bid)/mid in bp. vol_24h / vol_today = Kraken BASE-coin units (today = since "
        "00:00 UTC); adv20 = 20-day average DAILY volume in the same base units from pit daily bars; vol24h_x = vol_24h / adv20; "
        "vol_pace_x = today's hourly volume pace (vol_today / hours since 00:00 UTC) vs adv20/24 = the live intraday relvol. "
        "h1_vol_x = last COMPLETED 1h Kraken bar volume / mean of the prior 24 (engine cache, ~30 min refresh; h1_age_min = minutes since that bar closed). "
        "perp = Kraken Futures PF_ perpetual (null = none listed -> a short has no PF_ route); kf_funding_8h_pct = Kraken relative "
        "funding x8 in % (positive = longs pay); kf_basis_pct = perp mark vs index; cz_* = Coinalyze Binance USDT-perp funding %/OI. "
        "Brett's B checks: entry within 1.5% of last, volume >= 1.5x, spread <= 50 bp.")


def _rj(p):
    try: return json.load(open(p))
    except Exception: return None


def _f(x):
    try:
        v = float(x); return v if v == v else None
    except Exception: return None


def _r(v, n=5):
    return float("%.*g" % (n, v)) if isinstance(v, float) else v


def _alt(sym):
    s = str(sym); return s.split(":", 1)[1] if s.upper().startswith("KRAKEN:") else s


def _get_json(url, timeout=12):
    try:
        import sys
        if BASE not in sys.path: sys.path.insert(0, BASE)
        import kraken_dns; kraken_dns.install()   # same scoped DoH shim as feeds.kraken_spot (public Kraken hosts only)
    except Exception: pass
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "TFO paper desk"}), timeout=timeout) as f:
        return json.loads(f.read())


def kraken_ticker(pairs, get=None):
    """{pair_code: ticker row} from ONE Kraken public Ticker call. Raises on transport/API error (caller falls back)."""
    if not pairs: return {}
    d = (get or _get_json)("https://api.kraken.com/0/public/Ticker?pair=" + ",".join(pairs))
    if d.get("error"): raise RuntimeError(";".join(d["error"])[:80])
    return d.get("result") or {}


def _ws_rows(now_s):
    J = _rj(WS) or {}
    if now_s - float(J.get("ts") or 0) > WS_MAX_S: return {}
    out = {}
    for k, x in (J.get("ticker") or {}).items():
        a = WS_MAP.get(k)
        if a and isinstance(x, dict): out[a] = x
    return out


def _quote_rest(x):
    a, b, last = _f((x.get("a") or [None])[0]), _f((x.get("b") or [None])[0]), _f((x.get("c") or [None])[0])
    v, p, t, lo, hi = x.get("v") or [None, None], x.get("p") or [None, None], x.get("t") or [None, None], x.get("l") or [None, None], x.get("h") or [None, None]
    o = _f(x.get("o"))
    return dict(bid=b, ask=a, last=last, open_utc0=o, chg_pct_utc0=(round((last / o - 1) * 100, 3) if last and o else None),
                vol_today=_f(v[0]), vol_24h=_f(v[1]), vwap_24h=_f(p[1]), trades_24h=t[1], lo_24h=_f(lo[1]), hi_24h=_f(hi[1]))


def _quote_ws(x):
    last = _f(x.get("last")); chg = _f(x.get("change_pct"))
    return dict(bid=_f(x.get("bid")), ask=_f(x.get("ask")), last=last, vol_24h=_f(x.get("volume")), vwap_24h=_f(x.get("vwap")),
                trades_24h=x.get("trades"), lo_24h=_f(x.get("low")), hi_24h=_f(x.get("high")), chg_pct_24h=chg, quote_utc=x.get("timestamp"))


def _h1(rows, now_s):
    """Last completed 1h bar + its volume vs the mean of the prior 24 (rows [[ts,o,h,l,c,v],...] ascending)."""
    if not rows or len(rows) < 26: return {}
    last = rows[-1]; prev = [r[5] for r in rows[-25:-1] if r[5] is not None]
    mv = (sum(prev) / len(prev)) if prev else 0.0
    d24 = rows[-24:]
    return dict(h1_bar_utc=dt.datetime.fromtimestamp(int(last[0]), dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                h1_age_min=round((now_s - int(last[0]) - 3600) / 60.0), h1_close=_f(last[4]), h1_vol=_f(last[5]),
                h1_vol_x=(round(last[5] / mv, 2) if mv > 0 and last[5] is not None else None),
                h24_hi=max(r[2] for r in d24), h24_lo=min(r[3] for r in d24))


def _fresh(J, now_s):
    return isinstance(J, dict) and now_s - float(J.get("utc_ts") or 0) <= CACHE_MAX_S


def snapshot(syms, adv20=None, get=None, now_s=None, pinned=()):
    """{'rows': {sym: {...}}, 'src': {...}, 'note': NOTE, 'asof_utc': ...} for up to MAX_SYMS KRAKEN: symbols (pinned ones always, first). Never raises."""
    now_s = now_s or time.time(); out = dict(asof_utc=dt.datetime.fromtimestamp(now_s, dt.timezone.utc).isoformat(timespec="seconds"), src={}, note=NOTE, rows={})   # rows last: prompt trimming keeps src/note
    try:
        syms = [s for s in dict.fromkeys(syms or []) if str(s).upper().startswith("KRAKEN:")]
        _pn = [s for s in syms if s in set(pinned or ())]   # pinlive_20261103: wake/top10/own names are never cut by the MAX_SYMS cap
        syms = _pn + [s for s in syms if s not in _pn][:max(0, MAX_SYMS - len(_pn))]
        if not syms: return out
        adv20 = adv20 or {}
        S = _rj(SWEEP) or {}; sweep = {x.get("alt"): x for x in (S.get("pairs") or []) if isinstance(x, dict)}
        try: sweep_age = now_s - dt.datetime.fromisoformat(str(S.get("utc")).replace("Z", "+00:00")).timestamp()
        except Exception: sweep_age = None
        codes = {sweep[_alt(s)]["pair"]: s for s in syms if _alt(s) in sweep and sweep[_alt(s)].get("pair")}
        rest, err = {}, None
        try: rest = kraken_ticker(list(codes), get=get)
        except Exception as e: err = "%s: %s" % (type(e).__name__, str(e)[:80])
        ws = _ws_rows(now_s) if (err or len(rest) < len(codes)) else {}
        H = _rj(H1); K = _rj(KF); Z = _rj(DV)
        hb = (H.get("bars") or {}) if isinstance(H, dict) else {}
        kf = (K.get("rows") or {}) if _fresh(K, now_s) else None
        cz = (Z.get("rows") or {}) if _fresh(Z, now_s) else {}
        out["src"] = dict(quote="kraken_rest_ticker" if rest else ("kraken_ws/sweep fallback (%s)" % err if err else "none"),
                          sweep_utc=S.get("utc"), h1_cache_age_min=(round((now_s - float(H.get("utc_ts") or 0)) / 60) if isinstance(H, dict) else None),
                          perp_cache_age_min=(round((now_s - float(K.get("utc_ts") or 0)) / 60) if isinstance(K, dict) else None),
                          coinalyze_cache_age_min=(round((now_s - float(Z.get("utc_ts") or 0)) / 60) if isinstance(Z, dict) else None))
        code_of = {v: k for k, v in codes.items()}
        for s in syms:
            a = _alt(s); r = {}
            x = rest.get(code_of.get(s)) if code_of.get(s) else None
            if x: r.update(_quote_rest(x)); r["src"] = "kraken_rest"
            elif a in ws: r.update(_quote_ws(ws[a])); r["src"] = "kraken_ws"
            elif a in sweep and sweep_age is not None and sweep_age <= SWEEP_MAX_S:
                w = sweep[a]; r.update(bid=_f(w.get("bid")), ask=_f(w.get("ask")), last=_f(w.get("last")), open_utc0=_f(w.get("open")),
                                       chg_pct_utc0=_f(w.get("chg_pct_since_open"))); r["src"] = "kraken_sweep_%dmin_old" % round(sweep_age / 60)
            else: r["src"] = "no live quote (pair not in the Kraken sweep)"
            if r.get("bid") and r.get("ask"):
                mid = (r["bid"] + r["ask"]) / 2.0; r["spread_bp"] = round((r["ask"] - r["bid"]) / mid * 1e4, 1) if mid else None
            ad = _f(adv20.get(s)); r["adv20"] = ad
            if ad and r.get("vol_24h"): r["vol24h_x"] = round(r["vol_24h"] / ad, 2)
            hrs = max(1.0, (now_s % 86400) / 3600.0)   # hours since 00:00 UTC (Kraken 'today')
            if ad and r.get("vol_today") is not None: r["vol_pace_x"] = round((r["vol_today"] / hrs) / (ad / 24.0), 2)
            r.update(_h1(hb.get(s), now_s))
            if kf is None: r["perp"] = "unknown (perp cache stale)"
            else:
                k = kf.get(s) or {}
                r["perp"] = k.get("kf_sym")
                if k: r.update(kf_funding_8h_pct=k.get("kf_funding_pct"), kf_oi_usd=k.get("kf_oi_usd"), kf_basis_pct=k.get("kf_basis_pct"))
            z = cz.get(s) or {}
            if z: r.update(cz_funding_pct=z.get("funding_pct"), cz_oi_usd=z.get("oi_usd"), cz_oi_chg24_pct=(round(z["oi_chg24"] * 100, 2) if z.get("oi_chg24") is not None else None))
            out["rows"][s] = {k: _r(v) for k, v in r.items() if (v is not None or k == "perp") and k not in SLIM}
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, str(e)[:100])
    return out


def market(live_feeds):
    """BTC dominance / total-cap change (CoinGecko global, free feed already fetched by the pack) + Coinalyze BTC/ETH/SOL rows
    (paid pack) pulled out of live_feeds so compact_pk can never trim them away. {} when neither is present."""
    out = {}
    try:
        cg = ((live_feeds or {}).get("coingecko_global") or {}).get("data")
        if isinstance(cg, dict) and cg: out["coingecko_global"] = cg
        pp = (((live_feeds or {}).get("paid_pack") or {}).get("data") or {}).get("feeds") or {}
        cz = (pp.get("coinalyze_derivs") or {}).get("rows") if isinstance(pp.get("coinalyze_derivs"), dict) else None
        if cz: out["coinalyze_majors"] = cz
        if out: out["note"] = "coingecko_global = BTC/ETH dominance %, total-cap 24h change; coinalyze_majors = Binance USDT-perp OI usd, funding %, 24h liquidations, long/short ratio"
    except Exception: pass
    return out
