#!/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
"""US-universe pre-open screener (PAPER desk, IDEA SOURCE ONLY - never orders).

Stage 1 (default): build universe = Nasdaq-listed common stocks (nasdaqtrader nasdaqlisted.txt,
non-test, non-ETF, common/ordinary/ADS only) + S&P 500/400/600 constituents (Wikipedia).
Pull real data (yfinance daily 1y + 5m pre/post 5d), filter price >= $5 and 20d avg $vol >= $20M,
rank by pre-market gap vs prior close, relative volume, 52w-high proximity/breakout and dated
catalysts (Nasdaq earnings calendar, SEC EDGAR current 8-K feed, Yahoo news keywords).
Writes idea_feeds/us_universe_screen_YYYYMMDD.json/.md (+ prompt file for the model read).

Stage 2: --launch-models  -> starts detached `screen` sessions running ask.sh <claude|chatgpt>
          with the shortlist, then --ingest converts each answer to the usual
          <tool>_US_YYYYMMDD_HHMM.json/.md idea-feed files.

Usage:
  python3 us_universe_screen.py                 # screen only
  python3 us_universe_screen.py --launch-models # screen already done today -> launch model reads
  python3 us_universe_screen.py --ingest chatgpt <adhoc_md> <stem>
Never writes outside idea_feeds/. Never touches V1 ledgers, orders, sizing, rails or tfo_v2*.
"""
import argparse, datetime as dt, io, json, math, os, re, subprocess, sys, time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

FEED_DIR = Path(__file__).resolve().parent
SYD = ZoneInfo("Australia/Sydney"); NY = ZoneInfo("America/New_York")
SEC_UA = __import__("json").load(open(__import__("os").path.expanduser("~/Applications/MultiVenueDesk/research_controls/sec_contact.json")))["user_agent"]   # same UA the desk already uses
WEB_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
NEVER_REENTER = {"SOXL", "RCL", "IAG"}
HELD = {"META", "ISRG", "TMO"}
MIN_PRICE, MIN_DV20 = 5.0, 20e6
SHORTLIST_N = 30
COMMON_RX = re.compile(r"common stock|ordinary share|common share|american depositary|depositary share|"
                       r"class [a-c] (?:common )?shares?|\bshares\b", re.I)
NONCOMMON_RX = re.compile(r"warrant|\bright|\bunits?\b|preferred|\bnotes?\b|debenture|subordinat|"
                          r"perpetual|trust preferred|\bpfd\b|depositary shares? represent|cumulative", re.I)


def log(*a):
    print(f"[{dt.datetime.now(SYD):%H:%M:%S}]", *a, flush=True)


def safe_path(p: Path) -> Path:
    p = Path(p).resolve()
    assert FEED_DIR in p.parents, f"refusing to write outside idea_feeds: {p}"
    assert "tfo_v2" not in p.name
    return p


# ---------------- universe ----------------
def nasdaq_listed(errors):
    try:
        r = requests.get("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
                         headers={"User-Agent": WEB_UA}, timeout=30)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text), sep="|", dtype=str)
        df = df[~df["Symbol"].str.startswith("File Creation Time", na=True)]
        raw_n = len(df)
        df = df[(df["Test Issue"] == "N") & (df["ETF"] == "N")]
        name = df["Security Name"].fillna("")
        df = df[name.str.contains(COMMON_RX) & ~name.str.contains(NONCOMMON_RX)]
        df = df[~df["Symbol"].str.contains(r"[\$\.\^]", regex=True)]
        return sorted(df["Symbol"].str.strip()), raw_n
    except Exception as e:
        errors.append(f"nasdaqlisted.txt failed: {e}")
        return [], 0


def sp_list(url, errors, label):
    try:
        r = requests.get(url, headers={"User-Agent": WEB_UA}, timeout=30)
        r.raise_for_status()
        for t in pd.read_html(io.StringIO(r.text)):
            cols = [str(c) for c in t.columns]
            for c in ("Symbol", "Ticker symbol", "Ticker"):
                if c in cols:
                    syms = t[c].astype(str).str.strip().str.replace(".", "-", regex=False)
                    syms = [s for s in syms if re.fullmatch(r"[A-Z][A-Z0-9\-]{0,6}", s)]
                    if len(syms) > 300:
                        return sorted(set(syms))
        errors.append(f"{label}: no constituent table found")
    except Exception as e:
        errors.append(f"{label} failed: {e}")
    return []


# ---------------- data ----------------
def dl(tickers, chunk, **kw):
    frames, failed = {}, []
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        for attempt in range(2):
            try:
                df = yf.download(part, group_by="ticker", threads=True, progress=False,
                                 auto_adjust=False, **kw)
                break
            except Exception as e:
                log("download error", e); df = None; time.sleep(3)
        if df is None or df.empty:
            failed += part; continue
        for t in part:
            try:
                sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                sub = sub.dropna(how="all")
                if len(sub):
                    frames[t] = sub
                else:
                    failed.append(t)
            except KeyError:
                failed.append(t)
        log(f"  downloaded {min(i + chunk, len(tickers))}/{len(tickers)}")
        time.sleep(1.0)
    return frames, failed


def daily_metrics(frames, today_et):
    out = {}
    for t, d in frames.items():
        d = d[[c for c in ("Open", "High", "Low", "Close", "Volume") if c in d.columns]].dropna(subset=["Close"])
        d = d[pd.to_datetime(d.index).date < today_et]          # completed sessions only
        if len(d) < 25:
            continue
        c, v, h = d["Close"].astype(float), d["Volume"].astype(float), d["High"].astype(float)
        dv20 = float((c * v).tail(20).mean())
        dv10 = float((c * v).tail(10).mean())
        prev_close = float(c.iloc[-1])
        hi52 = float(h.tail(252).max())
        hi52_prior = float(h.iloc[:-1].tail(251).max()) if len(h) > 1 else hi52
        vol10_prior = float(v.iloc[-11:-1].mean())
        out[t] = dict(prev_close=round(prev_close, 4), last_session=str(pd.to_datetime(d.index[-1]).date()),
                      dv20=dv20, adv_usd_10d=dv10, vol_last=float(v.iloc[-1]),
                      vol10_avg=float(v.tail(10).mean()),
                      rvol_prior_day=round(float(v.iloc[-1]) / vol10_prior, 2) if vol10_prior > 0 else None,
                      chg_prior_day_pct=round((prev_close / float(c.iloc[-2]) - 1) * 100, 2),
                      hi52=round(hi52, 4),
                      pct_from_52w_high=round((prev_close / hi52 - 1) * 100, 2),
                      breakout_prior_day=bool(float(c.iloc[-1]) >= hi52_prior),
                      n_days=len(d))
    return out


def premarket_metrics(frames, today_et, now_et):
    out = {}
    cut = now_et.hour * 60 + now_et.minute
    for t, d in frames.items():
        d = d.dropna(subset=["Close"])
        if d.empty:
            continue
        idx = pd.to_datetime(d.index)
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
        idx = idx.tz_convert(NY)
        mins = idx.hour * 60 + idx.minute
        pre = (mins >= 240) & (mins < 570) & (mins <= cut)
        dates = idx.date
        today_mask = pre & (dates == today_et)
        if not today_mask.any():
            out[t] = dict(pm_last=None, pm_vol=0, pm_rvol=None, pm_last_time_et=None); continue
        td = d[today_mask]
        prior = [float(d["Volume"][pre & (dates == x)].sum()) for x in sorted(set(dates)) if x < today_et]
        prior = [p for p in prior if p >= 0][-4:]
        pm_vol = float(td["Volume"].sum())
        base = sum(prior) / len(prior) if prior else 0
        # Yahoo reports Volume=0 on extended-hours bars, so pm_rvol is usually None -> prior-day rvol is used
        out[t] = dict(pm_last=round(float(td["Close"].iloc[-1]), 4), pm_vol=pm_vol if pm_vol > 0 else None,
                      pm_rvol=round(pm_vol / base, 2) if base > 0 and pm_vol > 0 and len(prior) >= 2 else None,
                      pm_last_time_et=idx[today_mask][-1].strftime("%H:%M"))
    return out


# ---------------- catalysts ----------------
def nasdaq_earnings(dates, errors):
    res = {}
    for d in dates:
        try:
            r = requests.get(f"https://api.nasdaq.com/api/calendar/earnings?date={d}",
                             headers={"User-Agent": WEB_UA, "Accept": "application/json"}, timeout=30)
            rows = ((r.json().get("data") or {}).get("rows")) or []
            for row in rows:
                s = (row.get("symbol") or "").strip().replace(".", "-")
                res.setdefault(s, []).append({"date": d, "time": row.get("time"),
                                              "eps_fcst": row.get("epsForecast")})
        except Exception as e:
            errors.append(f"nasdaq earnings calendar {d} failed: {e}")
    return res


ITEM_LABEL = {"2.02": "earnings (8-K Item 2.02)", "1.01": "material agreement (8-K 1.01)",
              "2.01": "acquisition/disposition completed (8-K 2.01)", "7.01": "Reg FD disclosure (8-K 7.01)",
              "8.01": "other events (8-K 8.01)", "5.02": "officer/director change (8-K 5.02)",
              "1.02": "agreement terminated (8-K 1.02)", "3.02": "unregistered equity sale (8-K 3.02)",
              "5.07": "shareholder vote (8-K 5.07)", "2.03": "new debt obligation (8-K 2.03)"}


def edgar_8k(now_utc, errors):
    s = requests.Session(); s.headers.update({"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"})
    try:
        cik2t = {}
        for v in s.get("https://www.sec.gov/files/company_tickers.json", timeout=30).json().values():
            cik2t.setdefault(int(v["cik_str"]), []).append(v["ticker"].upper().replace(".", "-"))
    except Exception as e:
        errors.append(f"SEC company_tickers.json failed: {e}"); return {}, 0
    ns = {"a": "http://www.w3.org/2005/Atom"}
    res, n, cutoff = {}, 0, now_utc - dt.timedelta(hours=24)
    for start in range(0, 2000, 100):
        try:
            r = s.get("https://www.sec.gov/cgi-bin/browse-edgar", params=dict(
                action="getcurrent", type="8-K", company="", dateb="", owner="include",
                start=start, count=100, output="atom"), timeout=30)
            r.raise_for_status()
            entries = ET.fromstring(r.content).findall("a:entry", ns)
        except Exception as e:
            errors.append(f"EDGAR current 8-K page start={start} failed: {e}"); break
        if not entries:
            break
        oldest = None
        for e in entries:
            title = e.findtext("a:title", "", ns)
            upd = dt.datetime.fromisoformat(e.findtext("a:updated", "", ns))
            oldest = upd if oldest is None or upd < oldest else oldest
            if upd < cutoff or not title.startswith("8-K"):
                continue
            m = re.search(r"\((\d{10})\)", title)
            if not m:
                continue
            n += 1
            summ = e.findtext("a:summary", "", ns)
            items = sorted(set(re.findall(r"Item (\d\.\d\d)", summ)))
            link = (e.find("a:link", ns).get("href") if e.find("a:link", ns) is not None else None)
            for t in cik2t.get(int(m.group(1)), []):
                res.setdefault(t, []).append({"filed_utc": upd.astimezone(dt.timezone.utc).isoformat(),
                                              "items": items, "link": link})
        if oldest and oldest < cutoff:
            break
        time.sleep(0.25)
    return res, n


NEWS_RX = {"guidance": re.compile(r"guidance|outlook|raises? (?:full|fy|forecast)|forecast|lifts? (?:outlook|forecast)", re.I),
           "M&A": re.compile(r"acquir|merger|to buy (?!now)(?!or)|takeover|buyout|deal to buy|bid for|tender offer|go private|to be bought", re.I),
           "earnings": re.compile(r"earnings|quarter|results|q[1-4]\b|eps|revenue", re.I),
           "rating": re.compile(r"upgrade|downgrade|price target|initiat", re.I)}


def yahoo_news(t, now_utc):
    try:
        items = yf.Ticker(t).news or []
    except Exception:
        return None
    hits = []
    for it in items[:15]:
        c = it.get("content", it)
        title = c.get("title") or ""
        pub = c.get("pubDate") or c.get("displayTime")
        try:
            pubd = dt.datetime.fromisoformat(pub.replace("Z", "+00:00")) if isinstance(pub, str) else \
                dt.datetime.fromtimestamp(it.get("providerPublishTime"), dt.timezone.utc)
        except Exception:
            continue
        if now_utc - pubd > dt.timedelta(hours=36):
            continue
        tags = [k for k, rx in NEWS_RX.items() if rx.search(title)]
        hits.append({"pub_utc": pubd.isoformat(), "title": title[:180], "tags": tags,
                     "provider": (c.get("provider") or {}).get("displayName") if isinstance(c.get("provider"), dict) else c.get("publisher")})
    return hits


def pct_rank(s):
    return s.rank(pct=True, method="average").fillna(0)


# ---------------- main screen ----------------
def run_screen():
    t0 = time.time()
    now = dt.datetime.now(SYD); now_utc = now.astimezone(dt.timezone.utc); now_et = now.astimezone(NY)
    today_et = now_et.date()
    errors = []
    log("universe...")
    nq, nq_raw = nasdaq_listed(errors)
    sp5 = sp_list("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", errors, "S&P 500")
    sp4 = sp_list("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies", errors, "S&P 400")
    sp6 = sp_list("https://en.wikipedia.org/wiki/List_of_S%26P_600_companies", errors, "S&P 600")
    member = {}
    for lab, lst in (("NASDAQ", nq), ("SP500", sp5), ("SP400", sp4), ("SP600", sp6)):
        for t in lst:
            member.setdefault(t, []).append(lab)
    excluded = sorted(t for t in member if t in NEVER_REENTER | HELD)
    universe = sorted(t for t in member if t not in NEVER_REENTER | HELD)
    log(f"universe {len(universe)} (nasdaq common {len(nq)}/{nq_raw} rows, sp500 {len(sp5)}, sp400 {len(sp4)}, sp600 {len(sp6)}); excluded {excluded}")

    log("daily bars (1y)...")
    dfr, dfail = dl(universe, 400, period="1y", interval="1d")
    dm = daily_metrics(dfr, today_et)
    passed = [t for t, m in dm.items() if m["prev_close"] >= MIN_PRICE and m["dv20"] >= MIN_DV20]
    log(f"daily ok {len(dm)}, failed {len(dfail)}, passed filters {len(passed)}")

    log("pre-market 5m bars...")
    pfr, pfail = dl(passed, 200, period="5d", interval="5m", prepost=True)
    pm = premarket_metrics(pfr, today_et, now_et)

    log("catalysts...")
    # trading days: yesterday (after-hours = released last 24h) .. next 5 sessions
    days, d = [], today_et - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    days.append(d); d = today_et
    while len(days) < 7:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    earn = nasdaq_earnings([x.isoformat() for x in days], errors)
    k8, k8n = edgar_8k(now_utc, errors)
    yday, tday = days[0].isoformat(), today_et.isoformat()

    rows = []
    for t in passed:
        m = dict(dm[t]); m.update(pm.get(t, {"pm_last": None, "pm_vol": 0, "pm_rvol": None, "pm_last_time_et": None}))
        m["ticker"] = t; m["index_membership"] = member.get(t, [])
        m["gap_pct"] = round((m["pm_last"] / m["prev_close"] - 1) * 100, 2) if m.get("pm_last") else None
        m["rvol_used"] = m["pm_rvol"] if m.get("pm_rvol") is not None else m["rvol_prior_day"]
        m["rvol_basis"] = "premarket_vs_prior_premarket_avg" if m.get("pm_rvol") is not None else "prior_day_vs_10d_avg"
        m["breakout_premarket"] = bool(m.get("pm_last") and m["pm_last"] > m["hi52"])
        ev = earn.get(t, [])
        m["earnings_released_24h"] = [e for e in ev if (e["date"] == yday and e["time"] == "time-after-hours")
                                      or (e["date"] == tday and e["time"] in ("time-pre-market",))]
        m["earnings_next_5d"] = [e for e in ev if e not in m["earnings_released_24h"] and e["date"] >= tday]
        m["sec_8k_24h"] = k8.get(t, [])
        rows.append(m)
    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit("no names passed filters - check errors: " + "; ".join(errors))

    def cat_score(r):
        items = {i for f in r["sec_8k_24h"] for i in f["items"]}
        if r["earnings_released_24h"] or items & {"2.02", "1.01", "2.01"}:
            return 1.0
        if items & {"7.01", "8.01"}:
            return 0.5
        return 0.25 if items else 0.0
    df["cat_score"] = df.apply(cat_score, axis=1)
    df["prox_score"] = pct_rank(df["pct_from_52w_high"]) + df["breakout_prior_day"] * 0.25 + df["breakout_premarket"] * 0.25
    g = df["gap_pct"].fillna(0)
    # gap bonus: pre-market volume is unavailable from Yahoo and prior-day rvol predates today's gap,
    # so a large real pre-market gap must not be buried by a low prior-day rvol.
    gap_bonus = (g >= 3).astype(float) * 0.15 + (g >= 5).astype(float) * 0.10 + (g >= 8).astype(float) * 0.05
    df["score"] = (0.40 * pct_rank(g) + 0.20 * pct_rank(df["rvol_used"].fillna(0))
                   + 0.20 * pct_rank(df["prox_score"]) + 0.20 * df["cat_score"] + gap_bonus)
    df = df.sort_values("score", ascending=False).reset_index(drop=True)

    log("news for top candidates...")
    top = list(dict.fromkeys(df.head(80)["ticker"].tolist() + df.sort_values("gap_pct", ascending=False).head(25)["ticker"].tolist()))
    with ThreadPoolExecutor(8) as ex:
        news = dict(zip(top, ex.map(lambda x: yahoo_news(x, now_utc), top)))
    df["news_36h"] = df["ticker"].map(lambda x: news.get(x))
    bump = df["news_36h"].map(lambda h: 0.1 if h and any({"guidance", "M&A"} & set(n["tags"]) for n in h) else 0)
    df.loc[df["cat_score"] < 1.0, "score"] += bump[df["cat_score"] < 1.0]   # guidance/M&A headline = dated catalyst
    df = df.sort_values("score", ascending=False).reset_index(drop=True)

    def cat_text(r):
        parts = []
        for e in r["earnings_released_24h"]:
            parts.append(f"earnings {e['date']} {e['time'].replace('time-', '')} (Nasdaq cal" +
                         ("; scheduled today, may post after screen time)" if e["date"] == tday else ")"))
        for f in r["sec_8k_24h"][:3]:
            parts.append("8-K " + ",".join(f["items"]) + " filed " + f["filed_utc"][:16] + "Z")
        for n in (r["news_36h"] or []):
            own = re.search(rf"\b{re.escape(r['ticker'])}\b", n["title"]) is not None
            if {"guidance", "M&A"} & set(n["tags"]) or ("earnings" in n["tags"] and own):
                parts.append(f"news[{'/'.join(n['tags'])}]: {n['title']}")
                if len(parts) >= 5:
                    break
        return "; ".join(parts) if parts else "none found (technical only)"
    df["catalyst"] = df.apply(cat_text, axis=1)
    df["earnings_within_5d"] = df["earnings_next_5d"].map(lambda l: ", ".join(f"{e['date']} {e['time'].replace('time-','')}" for e in l) or None)

    keep = ["ticker", "score", "prev_close", "last_session", "pm_last", "pm_last_time_et", "gap_pct", "pm_vol",
            "pm_rvol", "rvol_prior_day", "rvol_used", "rvol_basis", "chg_prior_day_pct", "dv20", "adv_usd_10d",
            "hi52", "pct_from_52w_high", "breakout_prior_day", "breakout_premarket", "cat_score", "catalyst",
            "earnings_released_24h", "earnings_within_5d", "sec_8k_24h", "news_36h", "index_membership"]

    def clean(v):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        if hasattr(v, "item"):
            return v.item()
        return v
    recs = [{k: clean(r[k]) for k in keep} for _, r in df.iterrows()]
    for r in recs:
        r["score"] = round(r["score"], 4); r["dv20"] = round(r["dv20"]); r["adv_usd_10d"] = round(r["adv_usd_10d"])
    stamp = now.strftime("%Y%m%d")
    doc = {"schema": "us_universe_screen_v1", "watch_only": True, "paper_only": True,
           "generated_at": now.isoformat(), "generated_at_et": now_et.isoformat(),
           "note": "IDEA SOURCE ONLY - not orders. Real data only; every idea must still pass V1 gates.",
           "sources": {"universe": ["nasdaqtrader nasdaqlisted.txt", "Wikipedia S&P 500/400/600 lists"],
                       "prices": "yfinance (Yahoo) 1y daily bars + 5d 5-minute bars incl. pre-market",
                       "catalysts": ["Nasdaq earnings calendar api.nasdaq.com", "SEC EDGAR current 8-K atom feed (24h)",
                                     "Yahoo news headlines (top 80 by score + top 25 gappers, 36h)"]},
           "filters": {"min_price": MIN_PRICE, "min_avg_dollar_vol_20d": MIN_DV20},
           "ranking": "0.40*pct(gap) + gap bonus (+0.15 >=3%, +0.10 >=5%, +0.05 >=8%) + 0.20*pct(rvol: premarket vs prior-4-day premarket avg to same clock, else prior-day vs 10d) "
                      "+ 0.20*pct(52w-high proximity + breakout bonus) + 0.20*catalyst(1=earnings/8-K 2.02/1.01/2.01, 0.5=8-K 7.01/8.01) "
                      "+0.10 for guidance/M&A headline",
           "data_caveats": ["Yahoo extended-hours 5m bars carry Volume=0, so pre-market relative volume is unavailable; "
                            "rvol_used falls back to prior-day volume / prior 10-day average (rvol_basis says which).",
                            "gap_pct = last pre-market 5m close vs prior regular-session close; None = no pre-market print yet.",
                            "Nasdaq-calendar 'today pre-market' earnings may not have been released at screen time."],
           "excluded_never_reenter": sorted(NEVER_REENTER), "excluded_held": sorted(HELD), "excluded_present_in_universe": excluded,
           "counts": {"nasdaq_listed_rows": nq_raw, "nasdaq_common": len(nq), "sp500": len(sp5), "sp400": len(sp4),
                      "sp600": len(sp6), "universe_unique_after_exclusions": len(universe),
                      "daily_data_ok": len(dm), "daily_data_failed": len(dfail), "passed_filters": len(passed),
                      "premarket_data_ok": len(pfr), "premarket_volume_available": int(df["pm_vol"].fillna(0).gt(0).sum()), "premarket_print_today": int(df["pm_last"].notna().sum()),
                      "sec_8k_filings_24h": k8n, "runtime_s": round(time.time() - t0)},
           "daily_failed_sample": dfail[:50], "errors": errors,
           "shortlist": recs[:SHORTLIST_N], "reserve": recs[SHORTLIST_N:SHORTLIST_N + 30],
           "all_passed_compact": [{k: r[k] for k in ("ticker", "score", "prev_close", "pm_last", "gap_pct", "rvol_used",
                                                      "dv20", "pct_from_52w_high", "cat_score")} for r in recs]}
    out = safe_path(FEED_DIR / f"us_universe_screen_{stamp}.json")
    out.write_text(json.dumps(doc, indent=2, default=str))
    md = [f"# US universe pre-open screen {now:%Y-%m-%d %H:%M %Z} ({now_et:%H:%M} ET)", "",
          "**IDEA SOURCE ONLY - not orders. Must pass V1 gates.**", "",
          f"Universe {len(universe)} | daily data {len(dm)} | passed price>=$5 & 20d $vol>=$20M: {len(passed)} | "
          f"pre-market prints today: {doc['counts']['premarket_print_today']} | 8-Ks (24h): {k8n}", "",
          f"Errors: {'; '.join(errors) or 'none'}", "",
          "| # | Ticker | Score | Prev close | PM last (ET) | Gap % | RVol (basis) | 20d $vol M | % from 52wH | Earnings <=5d | Catalyst |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(recs[:SHORTLIST_N], 1):
        md.append(f"| {i} | {r['ticker']} | {r['score']:.3f} | {r['prev_close']} | {r['pm_last']} ({r['pm_last_time_et']}) | "
                  f"{r['gap_pct']} | {r['rvol_used']} ({'pm' if r['rvol_basis'].startswith('pre') else 'prior-day'}) | "
                  f"{r['dv20']/1e6:,.0f} | {r['pct_from_52w_high']} | {r['earnings_within_5d'] or '-'} | "
                  f"{r['catalyst'].replace('|', '/')} |")
    safe_path(FEED_DIR / f"us_universe_screen_{stamp}.md").write_text("\n".join(md) + "\n")
    log(f"wrote {out} ({doc['counts']})")
    return out


# ---------------- stage 2: model reads ----------------
def build_question(doc):
    lines = []
    for i, r in enumerate(doc["shortlist"], 1):
        lines.append(f"{i}. {r['ticker']}: prev close {r['prev_close']} ({r['last_session']}); pre-mkt last {r['pm_last']} @ {r['pm_last_time_et']} ET; "
                     f"gap {r['gap_pct']}%; rvol {r['rvol_used']} ({r['rvol_basis']}); prior-day chg {r['chg_prior_day_pct']}%; "
                     f"20d avg $vol ${r['dv20']/1e6:,.0f}M; {r['pct_from_52w_high']}% from 52w high {r['hi52']}"
                     f"{' [52w BREAKOUT]' if r['breakout_prior_day'] or r['breakout_premarket'] else ''}; "
                     f"catalyst: {r['catalyst']}; earnings within 5 trading days per Nasdaq calendar: {r['earnings_within_5d'] or 'none listed'}")
    return f"""US PRE-OPEN UNIVERSE SCREEN READ - {doc['generated_at']} (US cash opens 09:30 ET = 23:30 AEST). PAPER desk, research only.
A code screen scanned {doc['counts']['universe_unique_after_exclusions']} tickers (all Nasdaq-listed common stocks + S&P 1500); {doc['counts']['passed_filters']} passed price>=$5 and 20d avg dollar volume>=$20M.
Below is the top {len(doc['shortlist'])} by gap / relative volume / 52w-high proximity / dated catalyst. Numbers are from yfinance + Nasdaq earnings calendar + SEC EDGAR 8-K feed (real, as of {doc['generated_at_et']} ET).
Already excluded (never re-enter / already held): SOXL, RCL, IAG, META, ISRG, TMO - do not suggest them.

{chr(10).join(lines)}

TASK: Independently grade EVERY name A+ / A / B / skip for a LONG entry at/near the open with a hard -5% stop and >=2.5 R:R (i.e. a credible path to +12.5% or more).
Use web search to verify the catalyst (cite source URL and date). Flag any earnings within 5 trading days (verify; the calendar can be wrong). Be strict: most names should be B/skip; zero A+ is fine. Never invent data - write "unverified" if you cannot confirm.
Time-box: finish within 25 minutes.
OUTPUT: ONLY strict JSON (no markdown fences, no prose), shape:
{{"generated_at":"<ISO8601>","market":"US","note":"<method/caveats>","grades":[{{"ticker":"...","grade":"A+|A|B|skip","catalyst":"<specific dated catalyst + URL>","earnings_within_5d":true|false|null,"earnings_date":"<date or null>","ref_price":<number or null>,"stop":"<price, -5% from ref>","target":"<price>","rr":<number or null>,"label":"edge|beta","thesis":"<one line>"}}]}}
Include all {len(doc['shortlist'])} names in grades."""


def launch_models(stamp=None):
    now = dt.datetime.now(SYD)
    stamp = stamp or now.strftime("%Y%m%d")
    doc = json.loads((FEED_DIR / f"us_universe_screen_{stamp}.json").read_text())
    q = safe_path(FEED_DIR / f"us_universe_prompt_{stamp}.txt"); q.write_text(build_question(doc))
    hhmm = now.strftime("%H%M")
    timeout = os.environ.get("ASK_TIMEOUT_S", "3000")
    for tool in ("claude", "chatgpt"):
        stem = f"{tool}_US_{stamp}_{hhmm}"
        logf = safe_path(FEED_DIR / "logs" / f"us_universe_{tool}_{stamp}.out")
        sh = safe_path(FEED_DIR / "logs" / f"us_universe_{tool}_{stamp}.sh")
        sh.write_text(f"""#!/bin/zsh
export PATH=/Library/Frameworks/Python.framework/Versions/3.14/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
cd {FEED_DIR}
export ASK_TIMEOUT_S={timeout}
echo "start $(date)"
out=$(./ask.sh {tool} "$(cat {q})" us_universe_{stamp})
echo "ask rc=$? out=$out  $(date)"
python3 us_universe_screen.py --ingest {tool} "$out" {stem}
echo "ingest rc=$? $(date)"
""")
        sh.chmod(0o755)
        sess = f"usu_{tool}_{stamp}"
        # macOS ships screen 4.00 (no -Logfile): redirect inside the session instead
        subprocess.run(["screen", "-dmS", sess, "zsh", "-c", f"zsh '{sh}' > '{logf}' 2>&1"], check=True)
        log(f"launched screen session {sess} -> {stem} (log {logf})")


def ingest(tool, adhoc_md, stem):
    sys.path.insert(0, str(FEED_DIR))
    from idea_feed_runner import extract_json, to_md, screen_flags
    now = dt.datetime.now(SYD)
    stamp = stem.split("_")[2]
    scr = json.loads((FEED_DIR / f"us_universe_screen_{stamp}.json").read_text())
    by_t = {r["ticker"]: r for r in scr["shortlist"]}
    text = Path(adhoc_md).read_text() if adhoc_md and Path(adhoc_md).exists() else ""
    ans = text.split("## Answer", 1)[-1] if text else ""
    try:
        g = extract_json(ans)
        grades = g.get("grades") or []
        if not grades:
            raise ValueError("no grades in answer")
    except Exception as e:
        reason = "out of usage credits" if "out of usage credits" in ans else str(e)
        safe_path(FEED_DIR / f"{stem}.FAILED.txt").write_text(
            f"US universe-screen model read ({tool}) failed: {reason}\nAnswer file: {adhoc_md}\n")
        log(f"{tool} FAILED: {reason}"); return 1
    order = {"A+": 0, "A": 1, "B": 2, "skip": 3}
    grades.sort(key=lambda x: (order.get(x.get("grade"), 9), -(by_t.get(x.get("ticker"), {}).get("score") or 0)))
    ideas = []
    for x in grades:
        if x.get("grade") not in ("A+", "A") or len(ideas) >= 10:
            continue
        s = by_t.get(x.get("ticker"), {})
        ideas.append({"ticker": x.get("ticker"), "thesis": f"[{x.get('grade')}] {x.get('thesis','')}",
                      "catalyst": x.get("catalyst", ""), "adv_usd_10d": s.get("adv_usd_10d"),
                      "spread_bp_est": None,
                      "stop": f"{x.get('stop')} (-5% from ref {x.get('ref_price')}); target {x.get('target')}; R:R {x.get('rr')}",
                      "label": x.get("label") if x.get("label") in ("beta", "edge") else "edge",
                      "grade": x.get("grade"), "earnings_within_5d": x.get("earnings_within_5d"),
                      "earnings_date": x.get("earnings_date"),
                      "screen": {k: s.get(k) for k in ("gap_pct", "rvol_used", "rvol_basis", "prev_close", "pm_last", "pct_from_52w_high", "dv20")}})
    doc = {"generated_at": g.get("generated_at") or now.isoformat(),
           "source": {"chatgpt": "chatgpt-codex-cli", "claude": "claude-code-cli"}[tool] + " (us_universe_screen read)",
           "market": "US",
           "note": f"Universe-screen read: graded top {len(scr['shortlist'])} of {scr['counts']['passed_filters']} filtered names "
                   f"from {scr['counts']['universe_unique_after_exclusions']}-ticker Nasdaq+S&P1500 scan (us_universe_screen_{stamp}.json). "
                   f"ideas = A+/A only; ADV from desk screen (20d/10d yfinance), spread not measured (null). Model note: {g.get('note','')}"
                   + ("" if ideas else " NONE graded A+/A."),
           "ideas": ideas, "universe_grades": grades,
           "screen_file": f"idea_feeds/us_universe_screen_{stamp}.json"}
    flags = screen_flags(doc)
    doc["watch_only"] = True; doc["validator_flags"] = flags
    safe_path(FEED_DIR / f"{stem}.json").write_text(json.dumps(doc, indent=2))
    md = to_md(doc, stem, flags) + "\n## All grades (universe screen read)\n\n| Ticker | Grade | Earnings<=5d | R:R | Catalyst |\n|---|---|---|---|---|\n"
    md += "\n".join(f"| {x.get('ticker')} | {x.get('grade')} | {x.get('earnings_within_5d')} {x.get('earnings_date') or ''} | {x.get('rr')} | {str(x.get('catalyst','')).replace('|','/')[:200]} |" for x in grades) + "\n"
    safe_path(FEED_DIR / f"{stem}.md").write_text(md)
    log(f"{tool} ingested -> {stem}.json ({len(ideas)} A+/A)"); return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--launch-models", action="store_true", help="launch model reads on today's screen file")
    ap.add_argument("--screen-and-launch", action="store_true")
    ap.add_argument("--ingest", nargs=3, metavar=("TOOL", "ADHOC_MD", "STEM"))
    a = ap.parse_args()
    if a.ingest:
        sys.exit(ingest(*a.ingest))
    if a.launch_models:
        launch_models(); sys.exit(0)
    run_screen()
    if a.screen_and_launch:
        launch_models()
