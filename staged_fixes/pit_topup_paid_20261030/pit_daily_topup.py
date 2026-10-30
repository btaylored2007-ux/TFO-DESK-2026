"""2026-10-30 THF review item 1 (HT): daily top-up of the FULL pit.sqlite universe (21k syms).
Root cause of 'PIT stops at 28 Oct': the us_ca / europe_apac backfills were one-shot history pulls, and pit.py stale
only covers the seat universe (~300) with 2-day slack. This batches every listed sym through yfinance.download (Yahoo primary)
and writes via pit.put_bars (knowable_utc + hl sanity + no unfinished sessions). Futures/crypto untouched (own updaters).
Usage: python3 -m veteran_desk.pit_daily_topup [days_back_cutoff=21]"""
import sys, os, json, time, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from veteran_desk import pit as P
import yfinance as yf
def main(cut_days=21, chunk=60):   # tfo_pittopup_paid_20261030: <=60 so paid_first serves EODHD (Yahoo only as fallthrough)
    c = P.con(); now = dt.datetime.now(P.UTC); t0 = time.time()
    cut = (now.date() - dt.timedelta(days=cut_days)).isoformat()
    last = dict(c.execute("SELECT sym,MAX(date) FROM bars GROUP BY sym").fetchall())
    todo = sorted(s for s, d in last.items() if d and d >= cut and not s.startswith("KRAKEN:") and not s.endswith("=F")
                  and not P.banned(s) and d < P.expected_last_date(s, now))
    ok, empty, err = 0, [], {}
    for i in range(0, len(todo), chunk):
        B = todo[i:i + chunk]
        try: df = yf.download(B, period="10d", interval="1d", auto_adjust=False, actions=False, group_by="ticker", threads=True, progress=False)
        except Exception as e:
            for s in B: err[s] = str(e)[:80]
            continue
        for s in B:
            try:
                d = df[s] if len(B) > 1 else df
                rows = [(ix.date().isoformat(), float(r.Open), float(r.High), float(r.Low), float(r.Close), float(r.Volume or 0))
                        for ix, r in d.iterrows() if r.Close == r.Close and r.High == r.High and r.Low == r.Low]
                rows = [r for r in rows if r[0] > last[s]]
                if rows: P.put_bars(c, s, rows, "yahoo_batch"); ok += 1
                else: empty.append(s)
            except Exception as e: err[s] = str(e)[:80]
        c.commit()
    mx = c.execute("SELECT date,COUNT(*) FROM bars WHERE date>=? GROUP BY date ORDER BY date", ((now.date()-dt.timedelta(days=5)).isoformat(),)).fetchall()
    rep = dict(utc=now.isoformat(), secs=round(time.time()-t0), behind=len(todo), updated=ok, no_new_bar=len(empty), errors=len(err),
               no_new_bar_sample=empty[:40], error_sample=dict(list(err.items())[:20]), rows_by_date=mx)
    json.dump(rep, open(os.path.join(P.BASE, "data", "pit", "daily_topup_report.json"), "w"), indent=1); print(json.dumps({k: v for k, v in rep.items() if "sample" not in k}))
if __name__ == "__main__": main(int(sys.argv[1]) if len(sys.argv) > 1 else 21)
