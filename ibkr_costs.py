#!/usr/bin/env python3
"""ibkr_costs.py - Brett's REAL IBKR cost schedule for the hunt books (PAPER ONLY; no orders, no keys, read-only data).
Built 2026-10-27 by HEAD TRADER on Brett's ask "use our IBKR account's fee structure".

PLAN: IBKR Pro FIXED. The TWS API does not expose the commission plan (accountSummary gives AccountType=INDIVIDUAL only, the
paper account has no executions to infer it from); the desk's existing cost model (desk_costs.py) is already on Fixed.
Switch with env IBKR_PLAN=TIERED if Account Settings > Configuration > Commissions shows Tiered.

US STOCKS (verified 2026-10-27 at https://www.interactivebrokers.com/en/pricing/commissions-stocks.php)
  Fixed : USD 0.005/share, min USD 1.00/order, max 1% of trade value; + regulatory pass-throughs only.
  Tiered: USD 0.0035/share (<=300k sh/month), min 0.35, max 1%; + clearing NSCC/DTC 0.00020/sh, + exchange fee (modelled
          as 0.0030/sh remove-liquidity, conservative), + NYSE pass-through 0.000175*comm + FINRA pass-through 0.00056*comm.
  Regulatory (both plans): SEC fee 0.0000206 * sale value (sells, incl. short entries); FINRA TAF 0.000195/sh max 9.79 on sells
          but USD 0.00 for trades 2026-10-01..2026-12-31 (IBKR page note 5 'Fee Holiday'); FINRA CAT 0.000003/share (all sides).
ASX (same page, Australia): Fixed 0.08% of trade value, min AUD 6.00, no third-party fees.
     Tiered (Tier I): 0.08%, min AUD 5.00 + exchange/clearing fees (not modelled; only used if IBKR_PLAN=TIERED).
SLIPPAGE is not an IBKR fee: unchanged (US 5bp/side, ASX 10bp/side) and stays in the engines.
BORROW: IBKR public short-stock file ftp://ftp2.interactivebrokers.com/{usa,australia}.txt (FEERATE = annual %, AVAILABLE)
        cross-checked with the paper gateway reqMktData generic tick 236 (shortable / shortableShares). Cache:
        state/ibkr_borrow.json. A short whose name is absent from the file, or whose available < shares -> NO BORROW.
"""
import datetime as dt, json, os, sys, tempfile, urllib.request
try:
    import desk_doh; desk_doh.install(ibkr=True, oanda=False, extra=False)  # 2026-10-27 audit: scoped DoH for IBKR FTP/web
except Exception as _doh_e:
    print("desk_doh unavailable: %r" % _doh_e, file=sys.stderr)
BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "state", "ibkr_borrow.json")
PLAN = os.environ.get("IBKR_PLAN", "FIXED").upper()
SRC_COMM = "https://www.interactivebrokers.com/en/pricing/commissions-stocks.php"
SRC_BORROW = "ftp://ftp2.interactivebrokers.com/usa.txt + australia.txt (IBKR public short-stock availability)"
SEC_RATE = 0.0000206; CAT_SH = 0.000003; TAF_SH = 0.000195; TAF_MAX = 9.79
TAF_HOLIDAY = (dt.date(2026, 10, 1), dt.date(2026, 12, 31))
FALLBACK_BORROW_PA = 0.03

def us_commission(shares, px):
    v = abs(shares) * px
    if PLAN == "TIERED":
        c = min(max(0.0035 * abs(shares), 0.35), 0.01 * v)
        return c + 0.0002 * abs(shares) + 0.003 * abs(shares) + c * (0.000175 + 0.00056)
    return min(max(0.005 * abs(shares), 1.0), 0.01 * v)

def us_reg(shares, px, side, day=None):
    day = day or dt.date.today(); q = abs(shares); f = CAT_SH * q
    if side == "sell":
        f += SEC_RATE * q * px
        if not (TAF_HOLIDAY[0] <= day <= TAF_HOLIDAY[1]): f += min(TAF_SH * q, TAF_MAX)
    return f

def us_side(shares, px, side, day=None):
    """USD cost of one US stock order (commission + pass-throughs). side = 'buy' | 'sell' (a short entry is a sell)."""
    if not shares or not px: return 0.0
    return round(us_commission(shares, px) + us_reg(shares, px, side, day), 4)

def asx_side_aud(shares, px):
    v = abs(shares) * px
    return round(max(0.0008 * v, 5.0 if PLAN == "TIERED" else 6.0), 4)

def _load():
    try: return json.load(open(CACHE))
    except Exception: return {}

def borrow(sym, venue=None):
    """dict(fee_pa, available, ok) or None if the cache has no file data for this venue."""
    s = sym.upper().replace(".AX", ""); venue = venue or ("asx" if sym.upper().endswith(".AX") else "us")
    C = _load().get(venue)
    if not C: return None
    r = C["names"].get(s)
    if r is None: return dict(fee_pa=None, available=0, ok=False, src="absent from IBKR file")
    return r

def borrow_pa(sym, venue=None):
    b = borrow(sym, venue)
    return (b["fee_pa"] if b and b.get("fee_pa") is not None else FALLBACK_BORROW_PA)

BORROW_MAX_AGE_H = 24   # audit H11 (Brett 27 Oct 2026 19:35 WITA): borrow evidence older than 24h is not evidence
def cache_age_h(C=None):
    """Hours since the borrow cache's updated_utc; None if no cache / unreadable timestamp."""
    C = _load() if C is None else C
    try:
        u = dt.datetime.fromisoformat(str(C["updated_utc"]).replace("Z", "+00:00"))
        return (dt.datetime.now(dt.timezone.utc) - (u if u.tzinfo else u.replace(tzinfo=dt.timezone.utc))).total_seconds() / 3600
    except Exception: return None

BORROW_FUTURE_TOL_H = 5 / 60   # audit retry-edge hardening (Brett 27 Oct 2026 21:11 WITA): a borrow file dated >5 min in the FUTURE is stale (not evidence)
BORROW_RETRY_AGE_H = 20         # hourly retry job refreshes while the file is older than this (or missing/future-dated)
def cache_fresh(age=None, max_h=BORROW_MAX_AGE_H):
    age = cache_age_h() if age is None else age
    return age is not None and -BORROW_FUTURE_TOL_H <= age <= max_h
def has_borrow(sym, shares, venue=None):
    """audit H11: FAILS CLOSED. True only when a borrow cache <= 24h old says shortable with enough shares.
    No cache / stale cache / no file data for the venue / name absent -> False (was: unknown -> True)."""
    if not cache_fresh(): return False   # no cache / older than 24h / future-dated -> not evidence
    b = borrow(sym, venue)
    if b is None: return False
    return bool(b.get("ok")) and (b.get("available") or 0) >= abs(shares or 0)

def _avail(x):
    x = (x or "").strip()
    if x.startswith(">"): return int(x[1:]) * 10
    try: return int(float(x))
    except Exception: return 0

def refresh(gateway=None):
    """Download IBKR's public short-stock files; merge an optional gateway tick-236 snapshot {venue:{sym:{shortable,shortableShares}}}."""
    out = dict(updated_utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), source=SRC_BORROW)
    for venue, f in (("us", "usa"), ("asx", "australia")):
        # 2026-10-27 audit: urllib FTP times out on this Wi-Fi even with DoH; ftplib + desk_doh works.
        import ftplib, io
        buf = io.BytesIO(); ftp = ftplib.FTP(); ftp.connect("ftp2.interactivebrokers.com", 21, timeout=90)
        ftp.login("shortstock", ""); ftp.retrbinary(f"RETR {f}.txt", buf.write); ftp.quit()
        raw = buf.getvalue().decode("utf-8", "replace")
        names, asof = {}, None
        for l in raw.splitlines():
            if l.startswith("#BOF"): asof = l.split("|")[1:3]
            if l.startswith("#"): continue
            p = l.split("|")
            if len(p) < 8: continue
            try: fee = float(p[6]) / 100
            except Exception: fee = None
            a = _avail(p[7]); names[p[0]] = dict(fee_pa=fee, available=a, ok=a > 0, src="ibkr_ftp")
        out[venue] = dict(file_asof=asof, n=len(names), names=names)
    for venue, G in (gateway or {}).items():
        for s, g in G.items():
            r = out[venue]["names"].setdefault(s, dict(fee_pa=None, available=0, ok=False, src="absent from IBKR file"))
            r["gw_shortable"] = g.get("shortable"); r["gw_shares"] = g.get("shortableShares")
            if g.get("shortable") is not None and g["shortable"] < 2.5 and not g.get("shortableShares"): r["ok"] = False; r["src"] += "; gateway tick236 not easy-to-borrow"
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(CACHE)); os.write(fd, json.dumps(out).encode()); os.close(fd); os.replace(tmp, CACHE)
    return out

if __name__ == "__main__":
    if sys.argv[1:2] == ["refresh-if-stale"]:   # audit retry-edge: hourly launchd retry (com.tfo.ibkr-costs-refresh-retry)
        a = cache_age_h()
        if cache_fresh(a, BORROW_RETRY_AGE_H): print(json.dumps(dict(skipped=True, age_h=round(a, 2)))); sys.exit(0)
        o = refresh(); print(json.dumps(dict(refreshed=True, prev_age_h=a, us=o["us"]["n"], asx=o["asx"]["n"])))
    elif sys.argv[1:2] == ["refresh"]:
        g = json.load(open(sys.argv[2])) if len(sys.argv) > 2 else None
        o = refresh(g); print(json.dumps({v: dict(asof=o[v]["file_asof"], n=o[v]["n"]) for v in ("us", "asx")}))
    else: print(PLAN, us_side(100, 25, "buy"), us_side(1000, 25, "sell"), asx_side_aud(400, 50))
