"""Ratio back-adjusted continuous futures for data/pit (PAPER research data). Built 2026-10-28 (Brett 28 Oct: overlords fix health).
WHY: Yahoo =F is the UNADJUSTED front month; on each roll the level jumps by the old/new contract spread (e.g. NG Nov->Dec 2.70->3.00),
which fakes returns. METHOD (ratio / multiplicative back-adjustment):
  * As-traded Yahoo bars are kept verbatim in table bars_fut_raw (same columns, same knowable_utc).
  * Roll days are detected per root from its exchange contract cycle: inside each roll window (ROLL table) the day with the largest
    volume jump vs the prior-5-day median is the switch day (Yahoo moves to the next contract that day). Energy/BTC roll every month (forced).
  * Gap factor f = open(roll day) / close(day before)  [estimate: includes that night's genuine move], or, when both contracts are still
    listed on Yahoo, the EXACT spread new.close(prev day)/old.close(prev day)  (method='contract_spread').
  * Every bar before a roll is multiplied (o,h,l,c) by the product of all later f, so the newest bar equals the live front contract and
    daily returns equal the true held-contract returns. Volume unchanged.
  * Rolls are logged in fut_rolls(sym, roll_date, factor, method, vol_ratio, knowable_utc).
PIT: knowable_utc of every bar is unchanged (a bar is never visible earlier). Levels of OLD bars do embed later roll factors (inherent to any
back-adjusted series) - returns are PIT-clean; anyone needing as-traded levels as of a date must read bars_fut_raw."""
import datetime as dt, statistics as st
ALL = list(range(1, 13)); Q = [3, 6, 9, 12]
# root: (roll months (calendar month the switch happens), first day, last day, forced, yahoo exchange suffix)
ROLL = {"CL": (ALL, 14, 26, True, "NYM"), "HO": (ALL, 23, 31, True, "NYM"), "RB": (ALL, 23, 31, True, "NYM"), "NG": (ALL, 21, 31, True, "NYM"),
        "BZ": (ALL, 22, 31, True, "NYM"), "BTC": (ALL, 20, 31, True, "CME"),
        "ES": (Q, 8, 21, False, "CME"), "NQ": (Q, 8, 21, False, "CME"), "YM": (Q, 8, 21, False, "CBT"), "RTY": (Q, 8, 21, False, "CME"),
        "6A": (Q, 8, 21, False, "CME"), "6B": (Q, 8, 21, False, "CME"), "6E": (Q, 8, 21, False, "CME"), "6J": (Q, 8, 21, False, "CME"),
        "ZB": (Q, 14, 25, False, "CBT"), "ZN": (Q, 14, 25, False, "CBT"), "ZF": (Q, 22, 31, False, "CBT"), "ZT": (Q, 22, 31, False, "CBT"),
        "GC": ([1, 3, 5, 7, 9, 11, 12], 20, 31, False, "CMX"), "SI": (ALL, 20, 31, False, "CMX"), "HG": (ALL, 20, 31, False, "CMX"),
        "ALI": (ALL, 20, 31, False, "CMX"), "PL": (ALL, 20, 31, False, "NYM"), "PA": (ALL, 20, 31, False, "NYM"),
        "ZC": ([3, 5, 7, 9, 12], 7, 16, False, "CBT"), "ZW": ([3, 5, 7, 9, 12], 7, 16, False, "CBT"), "KE": ([3, 5, 7, 9, 12], 7, 16, False, "CBT"),
        "ZO": ([3, 5, 7, 9, 12], 7, 16, False, "CBT"), "ZS": ([1, 3, 5, 7, 8, 9, 11], 7, 16, False, "CBT"),
        "ZM": ([1, 3, 5, 7, 8, 9, 10, 12], 7, 16, False, "CBT"), "ZL": ([1, 3, 5, 7, 8, 9, 10, 12], 7, 16, False, "CBT"),
        "KC": (Q[:0] + [3, 5, 7, 9, 12], 14, 24, False, "NYB"), "CC": ([3, 5, 7, 9, 12], 9, 18, False, "NYB"),
        "SB": ([2, 4, 6, 9], 22, 31, False, "NYB"), "CT": ([3, 5, 7, 10, 12], 1, 20, False, "NYB"), "OJ": ([1, 3, 5, 7, 9, 11], 1, 16, False, "NYB"),
        "LE": ([2, 4, 6, 8, 10, 12], 22, 31, False, "CME"), "HE": ([2, 4, 5, 6, 7, 8, 10, 12], 7, 16, False, "CME"),
        "GF": ([1, 3, 4, 5, 8, 9, 10, 11], 20, 31, False, "CME")}
MIN_VR = 1.5
MCODE = "FGHJKMNQUVXZ"
def ensure(c):
    c.executescript("""CREATE TABLE IF NOT EXISTS bars_fut_raw(sym TEXT,date TEXT,o REAL,h REAL,l REAL,c REAL,v REAL,knowable_utc TEXT,src TEXT,PRIMARY KEY(sym,date));
    CREATE TABLE IF NOT EXISTS fut_rolls(sym TEXT,roll_date TEXT,factor REAL,method TEXT,vol_ratio REAL,knowable_utc TEXT,PRIMARY KEY(sym,roll_date));""")
    cols = {r[1] for r in c.execute("PRAGMA table_info(fut_rolls)")}   # follow-up 3 (2026-10-28): roll_date is a real ISO date or NULL; status says which
    if "status" not in cols: c.execute("ALTER TABLE fut_rolls ADD COLUMN status TEXT")
    if "pending_after" not in cols: c.execute("ALTER TABLE fut_rolls ADD COLUMN pending_after TEXT")
    c.execute("UPDATE fut_rolls SET status='pending', pending_after=substr(roll_date,9), roll_date=NULL WHERE roll_date LIKE 'pending>%'")
    c.execute("UPDATE fut_rolls SET status='rolled' WHERE status IS NULL AND roll_date IS NOT NULL")
    c.execute("CREATE INDEX IF NOT EXISTS fut_rolls_sym_date ON fut_rolls(sym, roll_date)")
    c.execute("DROP VIEW IF EXISTS fut_rolls_latest")
    c.execute("CREATE VIEW fut_rolls_latest AS SELECT r.* FROM fut_rolls r WHERE r.status='rolled' AND r.roll_date=(SELECT MAX(roll_date) FROM fut_rolls x WHERE x.sym=r.sym AND x.status='rolled')")
def detect(rows, root):
    """rows: [(date,o,h,l,c,v,...)] ascending. Returns [(idx, vol_ratio)] of roll days."""
    months, d0, d1, forced, _ = ROLL[root]; win = {}
    for i in range(1, len(rows)):
        d = dt.date.fromisoformat(rows[i][0])
        if d.month in months and d0 <= d.day <= d1: win.setdefault((d.year, d.month), []).append(i)
    out = []
    for k, idx in sorted(win.items()):
        best = None
        for i in idx:
            prev = [r[5] for r in rows[max(0, i - 5):i] if r[5]]
            vr = (rows[i][5] / max(st.median(prev), 1.0)) if prev and rows[i][5] else 0.0
            if best is None or vr > best[1]: best = (i, vr)
        if best and (best[1] >= MIN_VR or (forced and best[1] > 0)): out.append(best)
    return out
def _yf_close(tk, start, end):
    import yfinance as yf
    try:
        df = yf.Ticker(tk).history(start=start, end=end, auto_adjust=False)
        out = {d.date().isoformat(): float(r.Close) for d, r in df.iterrows() if r.Close == r.Close}
    except Exception: out = {}
    if not out:   # tfo_seatpipe_yfclose_20261030: yfinance 1.5.2 history(start,end) returns empty for contracts; chart v8 fallback
        try:
            from veteran_desk import pit as _P
            out = {r[0]: float(r[4]) for r in (_P.yahoo_chart(tk, "2y") or []) if start <= r[0] < end and r[4] == r[4]}
        except Exception: out = {}
    return out
def _chain(root, start, end, ref):
    """Closes of the next 15 listed contracts on Yahoo (expired ones are gone)."""
    sfx = ROLL[root][4]; out = {}
    for k in range(0, 15):
        m = (ref.month - 1 + k) % 12; y = ref.year + (ref.month - 1 + k) // 12; tk = f"{root}{MCODE[m]}{y % 100:02d}.{sfx}"
        _t = dt.date.today(); _cur = _t.year * 12 + _t.month - 1
        if root == "BZ" and y * 12 + m < _cur: continue   # tfo_seatpipe_brent_20261030: Brent month M expires ~last biz day of M-2; expired months 404 on Yahoo - skip. seatdata_20261103 (D25): was < _cur + 2, which also dropped the JUST-expired month (Dec on 3 Nov, still served by Yahoo) -> the real Dec->Jan switch on 2 Nov went unmatched and a bogus 'open_gap' roll was guessed on 30 Oct (BZ 2 Nov read -10.9% vs -7.6% true)
        px = _yf_close(tk, start, end)
        if px: out[tk] = px
    return out
def _match(chain, d, px):
    for tk, h in chain.items():
        if d in h and abs(h[d] - px) <= 1e-6 * max(1.0, abs(px)) + 1e-9: return tk
    return None
def recent_contract_rolls(root, raw, days=60):
    """EXACT recent rolls: identify which listed contract each recent raw close is. A switch old->new on day D gives
    factor = new.close(D-1) / raw.close(D-1) (raw close of D-1 IS the old contract). Also detects a PENDING roll: the live =F
    already quotes a newer contract than our last stored bar. Returns (cutoff_date or None, [(roll_date, factor, method, knowable)])."""
    import yfinance as yf
    last = dt.date.fromisoformat(raw[-1][0]); start = (last - dt.timedelta(days=days)).isoformat(); end = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    chain = _chain(root, start, end, last - dt.timedelta(days=days))
    if not chain: return None, []
    seq = []
    for r in reversed(raw):
        if r[0] < start: break
        tk = _match(chain, r[0], r[4])
        if not tk: break
        seq.append((r, tk))
    seq.reverse()
    if len(seq) < 2: return None, []
    rolls = []
    for (p, tp), (r, tr) in zip(seq, seq[1:]):
        if tr != tp and p[0] in chain[tr]: rolls.append((r[0], chain[tr][p[0]] / p[4], "contract_spread:" + tp + "->" + tr, p[6]))
    try:
        live = yf.Ticker(root + "=F").history(period="5d", auto_adjust=False); live = [(d.date().isoformat(), float(x.Close)) for d, x in live.iterrows()]
        later = [x for x in live if x[0] > raw[-1][0]]
        if later:
            tl = _match(chain, later[-1][0], later[-1][1]); tp = seq[-1][1]
            if tl and tl != tp and raw[-1][0] in chain[tl]:
                rolls.append(("pending>" + raw[-1][0], chain[tl][raw[-1][0]] / raw[-1][4], "contract_spread_pending:" + tp + "->" + tl, raw[-1][6]))
    except Exception: pass
    return seq[0][0][0], rolls
def rebuild(c, sym, exact_last=True, pending=True):
    """Rebuild adjusted rows in bars for one =F symbol from bars_fut_raw. Returns summary dict."""
    ensure(c); root = sym[:-2]
    if root not in ROLL: raise KeyError("no roll calendar for " + sym)
    raw = c.execute("SELECT date,o,h,l,c,v,knowable_utc,src FROM bars_fut_raw WHERE sym=? ORDER BY date", (sym,)).fetchall()
    if not raw: return {"sym": sym, "rows": 0}
    det = detect(raw, root); cut, exact = recent_contract_rolls(root, raw) if exact_last else (None, [])
    if cut:   # exact contract matching covers [cut, last]: drop estimated rolls there (plus the open window of the last bar's month)
        det = [(i, vr) for i, vr in det if raw[i][0] <= cut]
    rolls = [(i, raw[i][0], (raw[i][1] / raw[i - 1][4]) if raw[i - 1][4] and raw[i][1] else 1.0, "open_gap", vr, raw[i - 1][6]) for i, vr in det]
    pos = {r[0]: i for i, r in enumerate(raw)}
    for d, f, meth, k in exact:
        if d.startswith("pending>"): rolls.append((len(raw), d, f, meth, None, k))
        elif d in pos: rolls.append((pos[d], d, f, meth, None, k))
    c.execute("DELETE FROM fut_rolls WHERE sym=?", (sym,))   # derived table, rebuilt from raw each time
    c.executemany("INSERT INTO fut_rolls(sym,roll_date,factor,method,vol_ratio,knowable_utc,status,pending_after) VALUES(?,?,?,?,?,?,?,?)",
                  [(sym, None, f, m, vr, k, "pending", d[8:]) if str(d).startswith("pending>") else (sym, d, f, m, vr, k, "rolled", None) for _, d, f, m, vr, k in rolls])
    mult = [1.0] * len(raw); m = 1.0; ri = {}
    for i, _, f, *_ in rolls: ri[i] = ri.get(i, 1.0) * f
    m = ri.get(len(raw), 1.0)
    for j in range(len(raw) - 1, -1, -1):
        mult[j] = m
        if j in ri: m *= ri[j]
    c.executemany("INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?,?,?,?)",
                  [(sym, r[0], r[1] * k, r[2] * k, r[3] * k, r[4] * k, r[5], r[6], (r[7] or "yahoo") + "+ratio_backadj") for r, k in zip(raw, mult)])
    return {"sym": sym, "rows": len(raw), "rolls": len(rolls), "exact_from": cut, "exact": [(x[1], round(x[2], 5), x[3]) for x in rolls if x[3].startswith("contract")],
            "oldest_mult": mult[0]}
def seed_raw_from_bars(c, sym):
    """One-time: copy current (unadjusted) bars rows into bars_fut_raw if raw table has none for sym."""
    ensure(c)
    if c.execute("SELECT COUNT(*) FROM bars_fut_raw WHERE sym=?", (sym,)).fetchone()[0]: return 0
    c.execute("INSERT INTO bars_fut_raw SELECT * FROM bars WHERE sym=? AND src NOT LIKE '%ratio_backadj'", (sym,)); return c.execute("SELECT changes()").fetchone()[0]
