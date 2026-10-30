"""2026-10-29 PAPER one-off: backfill missing 2026-10-28 bars for US + LSE (.L) names that have a 27 Oct bar.
Yahoo first (yf.download batches -> chart v8 per-name), IBKR read-only only for US names Yahoo still misses. Inserts only date 2026-10-28; never overwrites."""
import os, sys, json, sqlite3, datetime as dt, time
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, BASE)
from veteran_desk import pit as P
D = "2026-10-28"
def todo(c):
    q = """select a.sym from bars a where a.date='2026-10-27' and not exists(select 1 from bars b where b.sym=a.sym and b.date=?)
           and (a.sym like '%.L' or a.sym not like '%.%') and a.sym not like '%=%' and a.sym not like '^%' and a.sym not like 'KRAKEN%'"""
    return [r[0] for r in c.execute(q, (D,)) if not P.banned(r[0])]
def ins(c, sym, r, src):
    if c.execute("select 1 from bars where sym=? and date=?", (sym, D)).fetchone(): return 0
    o, h, l, cl, v = r
    try:
        h, l, fl = P.hl_check(o, h, l, cl, P._atr_before(c, sym, D))
        if fl: print("hl clamp", sym, fl, flush=True)
    except Exception: pass
    if not (cl == cl and cl > 0 and h == h and l == l): return 0
    c.execute("insert into bars values(?,?,?,?,?,?,?,?,?)", (sym, D, o if o == o else cl, h, l, cl, v if v == v else 0, P.knowable(D, P.venue_of(sym), sym), src)); return 1
def main():
    import yfinance as yf
    c = sqlite3.connect(P.DB, timeout=120); syms = todo(c); got = {}; print("todo", len(syms), flush=True)
    for i in range(0, len(syms), 200):
        ch = syms[i:i+200]
        try:
            df = yf.download(ch, start="2026-10-27", end="2026-10-30", interval="1d", auto_adjust=False, actions=False, group_by="ticker", threads=True, progress=False)
            for s in ch:
                try:
                    row = df[s].loc[D] if len(ch) > 1 else df.loc[D]
                    if ins(c, s, (float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"]), float(row["Volume"])), "yahoo"): got[s] = "yahoo"
                except Exception: pass
        except Exception as e: print("batch err", i, e, flush=True)
        c.commit(); print("batch", i, "filled", len(got), flush=True); time.sleep(1)
    miss = [s for s in syms if s not in got]; print("yahoo-batch miss", len(miss), flush=True)
    for s in miss:
        try:
            for r in P.yahoo_chart(s, "5d"):
                if r[0] == D and ins(c, s, r[1:6], "yahoo_chart_v8"): got[s] = "yahoo_chart_v8"
        except Exception: pass
        c.commit(); time.sleep(0.25)
    miss = [s for s in syms if s not in got]; ib = 0
    for s in [m for m in miss if "." not in m][:300]:   # IBKR only fills days Yahoo misses (US only, readonly)
        if P._ibkr_fill(c, s, [D]): got[s] = "ibkr"; ib += 1
        c.commit()
    lse = [m for m in syms if m not in got and m.endswith(".L")]; print("LSE for IBKR", len(lse), flush=True)
    import subprocess
    for i in range(0, len(lse), 40):   # IBKR only fills days Yahoo misses (Yahoo has no LSE 28 Oct bars); readonly; prices in pence like Yahoo
        ch = lse[i:i+40]
        try:
            out = subprocess.run([os.path.expanduser("~/ibkr/venv/bin/python"), os.path.join(BASE, "veteran_desk", "ibkr_lse_hist.py"), *ch], capture_output=True, text=True, timeout=900).stdout
            J = json.loads(out.strip().splitlines()[-1])
        except Exception as e: print("ibkr lse err", i, e, flush=True); continue
        for s_, rows in J.items():
            if not isinstance(rows, list): continue
            prev = c.execute("select c from bars where sym=? and date='2026-10-27'", (s_,)).fetchone()
            for r in rows:
                if r[0] == D and prev and prev[0] and 0.8 < r[4] / prev[0] < 1.25 and ins(c, s_, r[1:6], "ibkr_hist(TRADES,RTH,LSE)"): got[s_] = "ibkr_lse"
        c.commit(); print("ibkr lse", i, "filled", len(got), flush=True)
    miss = [s for s in syms if s not in got]
    rep = dict(utc=dt.datetime.now(dt.timezone.utc).isoformat(), todo=len(syms), filled=len(got), by_src={k: list(got.values()).count(k) for k in set(got.values())}, still_missing=len(miss), missing_sample=miss[:200])
    json.dump(rep, open(os.path.join(BASE, "data", "pit", "backfill_20261028_report.json"), "w"), indent=1); print(json.dumps({k: v for k, v in rep.items() if k != "missing_sample"}))
if __name__ == "__main__": main()
