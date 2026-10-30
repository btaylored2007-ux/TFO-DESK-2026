"""Non-US short borrow rows (TFO 19:31 WITA 29 Oct 2026). PAPER only, no orders, no keys.
Source: IBKR public short-stock files (ftp2.interactivebrokers.com, anonymous 'shortstock' login) - one file per home market,
matched on SYMBOL + home CURRENCY (cross-listings in other files ignored). Conservative: fee = max(IBKR FEERATE, 3%/yr),
3-day minimum charge, every row and fill flagged estimated=True. Name absent / zero available / suffix unmapped / file > 24h -> BLOCKED.
Refresh: python3 -m veteran_desk.intl_borrow"""
import datetime as dt, json, os, sys
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(BASE, "state", "veteran_desk", "borrow_intl.json")
MIN_FEE_PA = 0.03; MIN_CHARGE_DAYS = 3; MAX_AGE_H = 24
SUFFIX = {".MC": ("spain", "EUR"), ".L": ("british", "GBP"), ".ST": ("swedish", "SEK"), ".DE": ("germany", "EUR"), ".PA": ("france", "EUR"),
          ".AS": ("dutch", "EUR"), ".BR": ("belgium", "EUR"), ".SW": ("swiss", "CHF"), ".MI": ("italy", "EUR"), ".VI": ("austria", "EUR"),
          ".T": ("japan", "JPY"), ".HK": ("hongkong", "HKD"), ".SI": ("singapore", "SGD"), ".TO": ("canada", "CAD"), ".NS": ("india", "INR")}
UTC = dt.timezone.utc
def key_of(sym):
    s = str(sym).upper()
    for suf, (f, ccy) in SUFFIX.items():
        if s.endswith(suf):
            root = s[: -len(suf)].replace("-", ".")
            if suf == ".HK": root = root.lstrip("0")
            return f, ccy, root
    return None
def refresh():
    sys.path.insert(0, BASE)
    try:
        import desk_doh; desk_doh.install(ibkr=True, oanda=False, extra=False)
    except Exception: pass
    import ftplib, io
    out = dict(updated_utc=dt.datetime.now(UTC).isoformat(timespec="seconds"), source="ftp2.interactivebrokers.com shortstock/<market>.txt", estimated=True,
               min_fee_pa=MIN_FEE_PA, min_charge_days=MIN_CHARGE_DAYS, markets={})
    for f, ccy in sorted(set(SUFFIX.values())):
        buf = io.BytesIO(); ftp = ftplib.FTP(); ftp.connect("ftp2.interactivebrokers.com", 21, timeout=90)
        ftp.login("shortstock", ""); ftp.retrbinary(f"RETR {f}.txt", buf.write); ftp.quit()
        names, asof = {}, None
        for l in buf.getvalue().decode("utf-8", "replace").splitlines():
            if l.startswith("#BOF"): asof = l.split("|")[1:3]
            if l.startswith("#"): continue
            p = l.split("|")
            if len(p) < 8 or p[1].upper() != ccy: continue
            try: fee = float(p[6]) / 100
            except Exception: fee = None
            a = p[7].strip(); a = 10**7 if a.startswith(">") else (int(a) if a.isdigit() else 0)
            names[p[0].upper()] = dict(fee_pa_ibkr=fee, fee_pa=max(fee or 0.0, MIN_FEE_PA), available=a, ok=a > 0 and fee is not None, isin=p[4], estimated=True)
        out["markets"][f] = dict(file_asof=asof, ccy=ccy, n=len(names), names=names)
    tmp = PATH + ".tmp"; json.dump(out, open(tmp, "w")); os.replace(tmp, PATH)
    return {k: v["n"] for k, v in out["markets"].items()}
def check(sym, shares, data=None, now=None):
    """Fails closed. dict(ok, fee_pa, available, htb, estimated, min_charge_days, why)."""
    now = now or dt.datetime.now(UTC); k = key_of(sym)
    base = dict(ok=False, fee_pa=None, available=0, htb=False, estimated=True, min_charge_days=MIN_CHARGE_DAYS)
    if not k: return dict(base, why="no verified borrow source for %s - blocked" % sym)
    if data is None:
        try: data = json.load(open(PATH))
        except Exception: return dict(base, why="no intl borrow snapshot - blocked")
    try:
        t = dt.datetime.fromisoformat(str(data.get("updated_utc")).replace("Z", "+00:00")); age = (now - t).total_seconds() / 3600
    except Exception: age = None
    if age is None or age > MAX_AGE_H or age < -0.1: return dict(base, why="intl borrow snapshot stale/undated - blocked")
    f, ccy, root = k; row = (((data.get("markets") or {}).get(f) or {}).get("names") or {}).get(root)
    if not row: return dict(base, why="no %s borrow row for %s (unverified) - blocked" % (f, root))
    if not row.get("ok") or int(row.get("available") or 0) < int(shares):
        return dict(base, fee_pa=row.get("fee_pa"), available=int(row.get("available") or 0), why="available %s < %d shares - blocked" % (row.get("available"), int(shares)))
    fee = max(float(row.get("fee_pa") or 0), MIN_FEE_PA)
    return dict(base, ok=True, fee_pa=fee, fee_pa_ibkr=row.get("fee_pa_ibkr"), available=int(row["available"]), htb=fee >= 0.10, why="located (estimated, %s file, fee %.2f%%/yr, 3-day min)" % (f, fee * 100))
if __name__ == "__main__": print(json.dumps(refresh()))
