"""Weekly FORWARD-ONLY scoring of each veteran seat vs its index benchmark (PAPER, 2026-10-28). Never replays history.
Weeks are ISO weeks, Mon 00:00 UTC -> Mon 00:00 UTC. Seat weekly return = (equity end / equity start) - 1, from equity snapshots
taken going forward (state/veteran_desk/seat_equity_snapshots.jsonl); before two snapshots exist in a week it falls back to realised
net P&L of trades closed that week / seat seed. Benchmark return = PIT close-to-close using only bars knowable at the snapshot time
(no lookahead). Seat is AUD, benchmarks are local-ccy price return (FX not stripped - flagged). Output state/veteran_desk/seat_weekly_scores.json
  python3 -m veteran_desk.seat_weekly_score snapshot|score"""
import datetime as dt, json, os, sqlite3, sys
from decimal import Decimal
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from veteran_desk import pit as P
BASE = P.BASE; UTC = dt.timezone.utc
SNAP = os.path.join(BASE, "state", "veteran_desk", "seat_equity_snapshots.jsonl")
OUT = os.path.join(BASE, "state", "veteran_desk", "seat_weekly_scores.json")
BOOK = os.path.join(BASE, "reports", "veteran_desk_book.json")
BENCH = {"energy": "XLE", "essentials_utilities": "XLP", "banks": "XLF", "healthcare": "XLV", "tech_ai_infra": "QQQ", "crypto": "KRAKEN:XBTUSD",
         "fx_commodities": "DBC", "macro": "^GSPC", "news_filings": "^GSPC", "price_action": "^GSPC", "trend": "^GSPC"}
SEED = 500000.0
def _p(s):
    d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")); return d if d.tzinfo else d.replace(tzinfo=UTC)
def week_start(t):
    t = t.astimezone(UTC); d = t.date() - dt.timedelta(days=t.weekday()); return dt.datetime(d.year, d.month, d.day, tzinfo=UTC)
def bench_px(c, sym, asof):
    b = P.bars_asof(c, sym, asof, 1); return (b[-1][0], b[-1][4]) if b else (None, None)
def snapshot(now=None, book_path=None, snap_path=None, db=None):
    """Append one forward snapshot (seat equity + benchmark close knowable at `now`). At most one per hour."""
    now = now or dt.datetime.now(UTC); snap_path = snap_path or SNAP
    try: B = json.load(open(book_path or BOOK))
    except Exception: return None
    try:
        last = open(snap_path).read().strip().splitlines()[-1]
        if _p(json.loads(last)["utc"]) > now - dt.timedelta(hours=1): return None
    except Exception: pass
    c = sqlite3.connect("file:%s?mode=ro" % (db or P.DB), uri=True); row = dict(utc=now.isoformat(), seats={})
    for seat, L in (B.get("seats") or {}).items():
        sym = BENCH.get(seat); d, px = bench_px(c, sym, now) if sym else (None, None)
        row["seats"][seat] = dict(equity_aud=L.get("equity_aud"), bench=sym, bench_date=d, bench_px=px)
    c.close(); os.makedirs(os.path.dirname(snap_path), exist_ok=True)
    with open(snap_path, "a") as fh: fh.write(json.dumps(row) + "\n"); fh.flush(); os.fsync(fh.fileno())
    return row
def _ret(a, b):
    try: return float((Decimal(str(b)) / Decimal(str(a)) - 1).quantize(Decimal("0.000001"))) if a and b else None
    except Exception: return None
def score(now=None, book_path=None, snap_path=None, out_path=None, write=True):
    now = now or dt.datetime.now(UTC); snaps = []
    try: snaps = [json.loads(l) for l in open(snap_path or SNAP) if l.strip()]
    except Exception: pass
    try: B = json.load(open(book_path or BOOK))
    except Exception: B = {}
    seats = sorted(set(BENCH) | set((B.get("seats") or {})))
    weeks = sorted({week_start(_p(s["utc"])) for s in snaps} | {week_start(_p(t.get("closed_utc") or t.get("closed") or now.isoformat()))
                    for L in (B.get("seats") or {}).values() for t in L.get("closed", [])})
    res = {}
    for seat in seats:
        rows = []
        for ws in weeks:
            we = ws + dt.timedelta(days=7)
            prev = [s for s in snaps if _p(s["utc"]) < ws and seat in s["seats"]]; inw = [s for s in snaps if ws <= _p(s["utc"]) < we and seat in s["seats"]]
            start = (prev[-1] if prev else (inw[0] if inw else None)); end = inw[-1] if inw else None
            src = "equity_snapshots"
            sr = _ret(start["seats"][seat]["equity_aud"], end["seats"][seat]["equity_aud"]) if start and end and start is not end else None
            if sr is None:
                L = (B.get("seats") or {}).get(seat) or {}
                net = sum(Decimal(str(t.get("net_aud") or 0)) for t in L.get("closed", []) if ws <= _p(t.get("closed_utc") or t.get("closed") or now.isoformat()) < we)
                sr = float((net / Decimal(str(L.get("seed_aud") or SEED))).quantize(Decimal("0.000001"))); src = "realised_closed_trades"
            br = _ret(start["seats"][seat].get("bench_px"), end["seats"][seat].get("bench_px")) if start and end and start is not end else None
            rows.append(dict(week_start_utc=ws.isoformat(), complete=now >= we, seat_ret=sr, seat_src=src, bench=BENCH.get(seat), bench_ret=br,
                             excess=(round(sr - br, 6) if br is not None else None), bench_note=None if br is not None else "need >=2 forward snapshots this week"))
        ex = [r["excess"] for r in rows if r["complete"] and r["excess"] is not None]
        res[seat] = dict(bench=BENCH.get(seat), weeks=rows, complete_weeks=len(ex), weeks_beating_bench=sum(1 for x in ex if x > 0),
                         cum_excess=round(sum(ex), 6) if ex else None, verdict="NO VERDICT (<4 complete weeks)" if len(ex) < 4 else ("AHEAD" if sum(ex) > 0 else "BEHIND"))
    out = dict(updated_utc=now.isoformat(), paper_only=True, forward_only=True, snapshots=len(snaps), basis="seat AUD equity vs benchmark local-ccy price return; PIT bars knowable at snapshot time only", seats=res)
    if write:
        p = out_path or OUT; os.makedirs(os.path.dirname(p), exist_ok=True); t = p + ".tmp.%d" % os.getpid()
        with open(t, "w") as fh: json.dump(out, fh, indent=1); fh.flush(); os.fsync(fh.fileno())
        os.replace(t, p)
    return out
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "score"
    if cmd == "snapshot": print(bool(snapshot())); print(json.dumps({k: v["verdict"] for k, v in score()["seats"].items()}))
    else: print(json.dumps({k: v["verdict"] for k, v in score()["seats"].items()}))
