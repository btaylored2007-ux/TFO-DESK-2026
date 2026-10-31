"""One-shot PAPER research merge of 3 PIT staging sets into data/pit/pit.sqlite (2026-10-28).
Row-level (not file-level) leak filter: available_at_utc must be >= bar date + venue's earliest plausible session close (UTC).
Drops provisional 2026-10-28 bars. Staging is read-only. Report -> data/pit/merge_20261028_report.json"""
import os, sys, glob, json, sqlite3, datetime as dt, pandas as pd
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); ST = f"{BASE}/data/pit_staging"; DB = f"{BASE}/data/pit/pit.sqlite"
PROV = "2026-10-28"
# earliest session close in UTC hours (DST-favourable), by suffix
CLOSE = {".L":15.5,".DE":15.5,".PA":15.5,".AS":15.5,".BR":15.5,".LS":15.5,".MC":15.5,".MI":15.5,".SW":15.5,".ST":15.5,".HE":15.5,".CO":15.5,".OL":14.0,".IR":15.5,
 ".T":6.0,".HK":8.0,".AX":5.0,".NZ":3.75,".SI":9.0,".TW":5.5,".TWO":5.5,".KS":6.5,".KQ":6.5,".NS":10.0,".BO":10.0,".SS":7.0,".SZ":7.0,".TO":20.0,".V":20.0,".CN":20.0,".NE":20.0}
def close_h(sym, group):
    if sym.startswith(("BINANCE:","COINGECKO:","KRAKEN:")) or sym.endswith("-USD"): return 24.0  # UTC day bar closes at next 00:00
    if sym.startswith(("ALFRED:","FRED:")): return 0.0
    for k,v in sorted(CLOSE.items(), key=lambda x:-len(x[0])):
        if sym.endswith(k): return v
    return 20.0 if group=="us_ca" and "=" not in sym and not sym.startswith("^") else 0.0
def banned(s): return s.endswith(".JK") or s.endswith("IDR=X") or s.startswith("IDR")and s.endswith("=X")
con = sqlite3.connect(DB); con.execute("PRAGMA journal_mode=WAL"); con.execute("PRAGMA synchronous=NORMAL")
R = {"run_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "groups": {}, "errors": []}
def load(df, group, file):
    G = R["groups"][group]; n0 = len(df)
    df = df[[c for c in ["symbol","date","open","high","low","close","volume","source","available_at_utc","delisted_flag"] if c in df.columns]].copy()
    df["date"] = df["date"].astype(str).str[:10]; df["symbol"] = df["symbol"].astype(str).str.strip()
    m = df["date"] >= PROV; G["provisional_dropped"] += int(m.sum()); df = df[~m]
    m = df["symbol"].map(banned); G["banned_dropped"] += int(m.sum()); df = df[~m]
    a = pd.to_datetime(df["available_at_utc"], utc=True, errors="coerce"); bad = a.isna(); G["unparseable"] += int(bad.sum()); df, a = df[~bad], a[~bad]
    if df.empty: return
    ch = df["symbol"].map(lambda s: close_h(s, group))
    need = pd.to_datetime(df["date"], utc=True) + pd.to_timedelta(ch, unit="h")
    leak = a < need; G["leak_rows"] += int(leak.sum())
    if leak.any() and len(G["leak_samples"]) < 10: G["leak_samples"].append(df[leak].iloc[0][["symbol","date","available_at_utc"]].astype(str).to_dict())
    df, a = df[~leak], a[~leak]
    fut = a > pd.Timestamp.now(tz="UTC") + pd.Timedelta(hours=1); G["future_dropped"] += int(fut.sum()); df, a = df[~fut], a[~fut]
    df = df.drop_duplicates(["symbol","date"]); o,h,l,c = (df[k].astype(float) for k in ("open","high","low","close"))
    ok = (l <= pd.concat([o,c],axis=1).min(axis=1)+1e-9) & (h >= pd.concat([o,c],axis=1).max(axis=1)-1e-9) & (l > 0) if not df["symbol"].iloc[0].startswith(("ALFRED:","FRED:")) else c.notna()
    G["bad_ohlc_dropped"] += int((~ok).sum()); df, a = df[ok], a[ok]
    ks = a.dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    if df["symbol"].iloc[0].startswith(("ALFRED:","FRED:")) if len(df) else False:
        con.executemany("INSERT OR REPLACE INTO macro VALUES(?,?,?,?,?)", zip(df.symbol, df.date, df.close.astype(float), ks, df.source)); G["macro_rows"] += len(df)
    else:
        v = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0)
        con.executemany("INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?,?,?,?)", zip(df.symbol, df.date, df.open.astype(float), df.high.astype(float), df.low.astype(float), df.close.astype(float), v.astype(float), ks, df.source))
        G["bar_rows"] += len(df)
        for s, sd in df.groupby("symbol"):
            dl = sd["delisted_flag"].astype(str).str.lower().isin(["1","true","yes"]).any() if "delisted_flag" in sd else False
            con.execute("INSERT OR REPLACE INTO universe VALUES(?,?,?,?,?)", (s, group, "delisted" if dl else "listed", sd.date.max() if dl else None, f"staging:{group}"))
    G["files_ok"] += 1
for group, pat in [("europe_apac","europe_apac/*/*.parquet"),("us_ca","us_ca/bars/*.parquet"),("macro_fx_cmdty_crypto","macro_fx_cmdty_crypto/series/*.csv")]:
    G = R["groups"][group] = dict(bar_rows=0, macro_rows=0, filings_rows=0, files_ok=0, files_err=0, provisional_dropped=0, banned_dropped=0, unparseable=0, leak_rows=0, leak_samples=[], future_dropped=0, bad_ohlc_dropped=0)
    M = json.load(open(f"{ST}/{group}/manifest.json")); G["survivorship"] = M.get("survivorship")
    files = [f for f in sorted(glob.glob(f"{ST}/{pat}")) if "/_work/" not in f]
    for i, f in enumerate(files):
        try: load(pd.read_parquet(f) if f.endswith(".parquet") else pd.read_csv(f, dtype=str), group, f)
        except Exception as e: G["files_err"] += 1; R["errors"].append(f"{os.path.relpath(f,ST)}: {e}"[:200])
        if i % 500 == 0: con.commit(); print(group, i, len(files), flush=True)
    con.commit()
    if M.get("survivorship") == "UNIVERSE_BIASED":
        con.execute("INSERT OR REPLACE INTO venues VALUES(?,?,?)", (f"staging:{group}", "UNIVERSE_BIASED", str(M.get("notes",""))[:500]))
# SEC filings (us_ca)
G = R["groups"]["us_ca"]; cik2sym = {}
for i, f in enumerate(sorted(glob.glob(f"{ST}/us_ca/filings/*.parquet"))):
    try:
        d = pd.read_parquet(f); a = pd.to_datetime(d["available_at_utc"], utc=True, errors="coerce")
        lk = a.isna() | (a.dt.strftime("%Y-%m-%d") < d["filing_date"].astype(str).str[:10]); G["filings_leak_or_unparseable"] = G.get("filings_leak_or_unparseable",0) + int(lk.sum()); d, a = d[~lk], a[~lk]
        if len(d): cik2sym[str(int(d.cik.iloc[0]))] = str(d.symbol.iloc[0])
        con.executemany("INSERT OR REPLACE INTO filings VALUES(?,?,?,?,?,?)", zip(d.cik.astype(str), d.symbol.astype(str), d.form, a.dt.strftime("%Y-%m-%dT%H:%M:%S+00:00"), d.accession, d.source)); G["filings_rows"] += len(d)
    except Exception as e: R["errors"].append(f"filings {os.path.basename(f)}: {e}"[:200])
    if i % 1000 == 0: con.commit()
ev = pd.read_csv(f"{ST}/us_ca/meta/sec_delisting_deregistration_events.csv", dtype=str); n = 0; dl = 0
for r in ev.itertuples():
    acc = os.path.basename(r.path).replace(".txt",""); sym = cik2sym.get(str(int(r.cik)), "")
    # date_filed only (no time) -> knowable end of filing day UTC (conservative)
    con.execute("INSERT OR IGNORE INTO filings VALUES(?,?,?,?,?,?)", (str(int(r.cik)), sym, r.form, f"{r.date_filed}T23:59:59+00:00", acc, "sec_full_index_form_idx")); n += 1
    if sym and r.form.startswith("25"):
        dl += con.execute("UPDATE universe SET status='delisted', delisted_date=COALESCE(delisted_date,?) WHERE sym=? AND status='listed'", (r.date_filed, sym)).rowcount
G["delisting_events_rows"] = n; G["universe_marked_delisted_from_form25"] = dl
con.commit()
# post-merge leak audit over whole db
R["audit"] = dict(
 bars_total=con.execute("select count(*) from bars").fetchone()[0],
 bars_knowable_before_date=con.execute("select count(*) from bars where substr(knowable_utc,1,10) < date").fetchone()[0],
 bars_on_or_after_prov=con.execute("select count(*) from bars where date >= ?", (PROV,)).fetchone()[0],
 macro_total=con.execute("select count(*) from macro").fetchone()[0],
 macro_knowable_before_date=con.execute("select count(*) from macro where substr(knowable_utc,1,10) < date").fetchone()[0],
 filings_total=con.execute("select count(*) from filings").fetchone()[0],
 universe_total=con.execute("select count(*) from universe").fetchone()[0],
 venues=con.execute("select venue,bias_flag from venues").fetchall(),
 integrity=con.execute("pragma quick_check").fetchone()[0])
con.execute("PRAGMA wal_checkpoint(TRUNCATE)"); con.close()
R["db_bytes"] = os.path.getsize(DB); R["done_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
json.dump(R, open(f"{BASE}/data/pit/merge_20261028_report.json","w"), indent=1, default=str); print("DONE", json.dumps(R["audit"], default=str))
