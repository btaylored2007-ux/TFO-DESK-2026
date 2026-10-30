"""MAIN-BOOK US BETA CAP (tfo_usbook_cap_20261030; Brett 30 Oct 2026 19:35 WITA "have that limit on the us book as well"). PAPER ONLY.
Same rule as the Veteran Desk / THF (veteran_desk/book.py US_BETA_CAP_PCT + gate_v2 arm-time check), applied to the MAIN TFO book:
  cap_usd = US_BETA_CAP_PCT (0.60) x main-book equity (US$: desk_caps.sizing_equity() = firm equity ex veteran_desk, + TFO V2 book equity; already US$,
            so no AUDUSD conversion is needed for the base; A$-valued rows are converted back at the same AUDUSD theme_exposure used).
  exposure = net US-listed equity beta (US$) over every main-book sleeve: fundamental (TFO Equity / HT clips / FUND), fundamental spec,
            Trend18, TFO V2, US hunt48 books - HOLDINGS + ARMED + staged/queued, plus earlier passes in the
            same process run. Long +, short -, levered/inverse ETFs at their multiple. Crypto, FX, OANDA, futures, non-US venues excluded;
            non-equity ETFs (gold/bonds/commodity/vol/FX: NON_EQUITY_ASSET_CLASS) excluded (tfo_capfix_20261030, HT 20:12 WITA).
            The Veteran Desk / THF (and the VD funder queue) are NOT counted - they have their own cap. TREND-CORE and ESSENTIALS
            are separate A$1m books outside firm equity and are not counted either.
  block   = a NEW add is refused when |total| > cap AND |total| > |current| (moves that shrink |beta| always pass). Exits never blocked;
            nothing is ever force-closed. Any error -> blocked (fail closed) at the arm/pre-trade gates."""
import os
US_BETA_CAP_PCT = 0.60   # tfo_cap60_20261030 (Brett 20:06 WITA): 60% (40% -> 50% 19:58 -> 60% 20:06)
LEVERED_ETF_MULT = {"TQQQ": 3, "SQQQ": -3, "UPRO": 3, "SPXU": -3, "SPXL": 3, "SPXS": -3, "SSO": 2, "SDS": -2, "QLD": 2, "QID": -2, "SOXL": 3, "SOXS": -3,
                    "TNA": 3, "TZA": -3, "UDOW": 3, "SDOW": -3, "TECL": 3, "TECS": -3, "FAS": 3, "FAZ": -3, "LABU": 3, "LABD": -3, "SH": -1, "PSQ": -1, "DOG": -1}
EXCLUDED_BOOK_PREFIX = ("OANDA", "Crypto ", "TREND-CORE", "ESSENTIALS")   # TREND-CORE / ESSENTIALS = separate A$1m books (in_firm_equity False)
EXCLUDED_SRC = ("state/funder_queue.json",)   # Veteran Desk funder queue: VD has its own cap
_RUN_USD = [0.0]                               # earlier passes in this process run (arm loops)
# tfo_capfix_20261030 (approved by HT 20:12 WITA): NON-EQUITY US-listed ETFs are NOT US equity beta. Explicit asset-class map;
# anything not listed here is counted exactly as before (real equity ETFs unchanged).
NON_EQUITY_ASSET_CLASS = {
    **{s: "gold" for s in ("UGL", "GLD", "IAU", "GLDM", "SGOL", "AAAU", "BAR", "OUNZ", "IAUM", "DGL", "GLL", "DZZ")},
    **{s: "silver/precious" for s in ("SLV", "SIVR", "AGQ", "ZSL", "PPLT", "PALL", "GLTR")},
    **{s: "bonds" for s in ("TMF", "TMV", "TLT", "TBT", "TBF", "TTT", "UBT", "IEF", "IEI", "SHY", "SHV", "BIL", "SGOV", "GOVT", "EDV", "ZROZ", "VGLT",
                             "SPTL", "BND", "AGG", "BNDX", "LQD", "HYG", "JNK", "TIP", "SCHP", "VCIT", "VCSH", "MUB", "EMB")},
    **{s: "commodity" for s in ("USO", "UCO", "SCO", "BNO", "UNG", "BOIL", "KOLD", "DBC", "PDBC", "GSG", "DBA", "CPER", "CORN", "WEAT", "SOYB")},
    **{s: "managed_futures" for s in ("DBMF", "KMLM", "CTA", "FMF", "WTMF")},
    **{s: "volatility" for s in ("VXX", "UVXY", "SVXY", "VIXY")},
    **{s: "usd/fx" for s in ("UUP", "UDN", "FXE", "FXY", "FXA")},
}

def is_us_equity(sym):
    s = str(sym or "").upper()
    if not s or "/" in s or "_" in s or "=" in s or s.endswith("-USD") or s in ("BTC", "ETH", "SOL", "XBT"): return False
    if "." in s and not s.endswith((".US",)) and s.split(".")[-1] not in ("A", "B"): return False   # .AX/.T/.L/... (BRK.B ok)
    if s.split(".")[0] in NON_EQUITY_ASSET_CLASS: return False   # gold / bonds / commodities / vol / FX ETFs (tfo_capfix_20261030)
    return True
def mult(sym): return LEVERED_ETF_MULT.get(str(sym or "").upper(), 1)
def credit(usd):
    """Add a signed US$ amount (e.g. same-run sells, negative) to this run's running total."""
    _RUN_USD[0] += float(usd)
def leg_usd(sym, usd, side="long"):
    if not is_us_equity(sym): return 0.0
    return (-1.0 if str(side or "long").lower() in ("short", "sell") else 1.0) * abs(float(usd)) * mult(sym)

V2_BOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "v2", "tfo_v2_book.json")
def equity_parts():
    """{firm_ex_vd, tfo_v2}: main-book equity in US$. firm part fails closed (desk_caps); V2 is its own US$ book outside firm_risk
    whose US exposure is counted here, so its equity is in the base too (unreadable V2 -> 0 = smaller cap, conservative)."""
    import desk_caps, json
    out = {"firm_ex_vd": float(desk_caps.sizing_equity()["sizing_equity_usd"])}
    try: out["tfo_v2"] = max(0.0, float(json.load(open(V2_BOOK)).get("equity") or 0))
    except Exception: out["tfo_v2"] = 0.0
    return out
def equity_usd(): return sum(equity_parts().values())
def cap_usd(eq=None): return US_BETA_CAP_PCT * float(equity_usd() if eq is None else eq)

def rows(exclude=()):
    """[(book, sym, signed_usd, staged, src)] main-book US equity rows. exclude: iterable of (book_prefix, sym) staged rows to drop
    (the trade being re-levelled / filled is already counted as armed)."""
    import theme_exposure as te
    cfg = te.load_cfg(); fx = te.fx_audusd(cfg); P, _nav, errs = te.collect(cfg, fx)
    if errs: raise RuntimeError("exposure collect errors: %s" % "; ".join(errs)[:200])
    out = []
    for p in P:
        if p.get("ccy") != "USD" or str(p.get("book", "")).startswith(EXCLUDED_BOOK_PREFIX) or p.get("src") in EXCLUDED_SRC: continue
        if not is_us_equity(p.get("sym")): continue
        if p.get("staged") and any(str(p["book"]).startswith(b) and str(p["sym"]).upper() == str(s).upper() for b, s in exclude): continue
        # side: p["sign"] is theme_exposure's reading of the row's OWN side field (direction/side) - short -> -1; missing -> long (+1, conservative).
        out.append((p["book"], p["sym"], p["sign"] * float(p["value_aud"]) * fx * mult(p["sym"]), bool(p.get("staged")), p.get("src")))
    return out
def current_usd(exclude=()): return sum(r[2] for r in rows(exclude))

def check(sym, usd, side="long", book="main", exclude=(), cur=None, cap=None, others_usd=None, accumulate=True):
    """(ok, why, info). Non-US-equity -> always ok. Fail closed on any error."""
    new = leg_usd(sym, usd, side)
    if new == 0.0: return True, "us_book_cap n/a (not US equity)", {}
    try:
        c = (current_usd(exclude) if cur is None else float(cur)) + (_RUN_USD[0] if others_usd is None else float(others_usd))
        k = cap_usd() if cap is None else float(cap); tot = c + new
        info = dict(cur_usd=round(c), new_usd=round(new), total_usd=round(tot), cap_usd=round(k), cap_rule="%.0f%% main-book equity (US$)" % (100 * US_BETA_CAP_PCT))
        if abs(tot) > k and abs(tot) > abs(c):
            return False, "us_book_cap: main-book net US beta (open+armed+staged+run) US$%.0f + %s US$%.0f = US$%.0f > cap US$%.0f (%.0f%% of main-book equity) [%s]" % (c, sym, new, tot, k, 100 * US_BETA_CAP_PCT, book), info
        if accumulate and others_usd is None: _RUN_USD[0] += new
        return True, "us_book_cap ok (US$%.0f of US$%.0f)" % (tot, k), info
    except Exception as e:
        return False, "us_book_cap: check failed (%s) -> blocked (fail closed)" % str(e)[:100], {}
def reset_run(): _RUN_USD[0] = 0.0

if __name__ == "__main__":
    R = rows(); eq = equity_usd(); k = cap_usd(eq); cur = sum(r[2] for r in R)
    print(equity_parts()); print("main-book equity US$%s cap US$%s (%.0f%%) net US beta US$%s (%.0f%% of cap)" % (f"{eq:,.0f}", f"{k:,.0f}", 100 * US_BETA_CAP_PCT, f"{cur:,.0f}", 100 * cur / k))
    by = {}
    for b, s, u, st, _ in R: by[b] = by.get(b, 0) + u
    for b, u in sorted(by.items(), key=lambda x: -abs(x[1])): print("  %-34s US$%12s" % (b, f"{u:,.0f}"))
    for b, s, u, st, _ in sorted(R, key=lambda r: -abs(r[2]))[:15]: print("    %-8s %-30s US$%11s%s" % (s, b, f"{u:,.0f}", " [staged]" if st else ""))
