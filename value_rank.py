#!/usr/bin/env python3
"""value_rank.py — rank the reconciled screen into LONG candidates. PAPER ONLY.

NO VALIDATED EDGE. This is a cross-sectional screen with a quality overlay. It has
run no backtest, no search-deflation, no CV and no kill-test, and the search count
travels with every claim it produces.

Method, stated so it is checkable:
  * Ranks ONLY on the reconciled P/B (marketCap / filed equity). Unreconciled
    names never reach this file — value_hunt.py rejects them.
  * VALUE  = mean percentile rank of (P/B, PE, EV/EBITDA), cheap = good.
  * QUALITY= mean percentile rank of (ROE up, debt/equity down).
  * A value trap is cheap AND low quality, so the composite REQUIRES both:
    score = 0.65*value + 0.35*quality, and hard floors reject the traps outright.
"""
import json, os, sys

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "reports", "value_hunt.json")
OUT = os.path.join(BASE, "reports", "value_rank.json")
PRIORITY = os.path.join(BASE, "reports", "value_priority.json")

# Hard floors — a screen that ranks a broken business as "cheap" is a value trap
MIN_ROE = 0.05           # 5% — must actually earn on its equity
MAX_DE = 200.0           # debt/equity 2.0x
MIN_PE, MAX_PE = 0.0, 40.0
MAX_PB = 5.0
# 2026-10-28 14:10 Brett fundamental auto-trade: micro caps allowed down to US$2m; tech/semis EXCLUDED;
# preferred sectors (energy, essentials, banks/financials, health) get a small score tilt.
MIN_MCAP_USD = 2_000_000.0
EXCLUDED_SECTORS = {"Technology"}
PREFERRED_SECTORS = {"Energy", "Utilities", "Consumer Defensive", "Healthcare", "Financial Services"}
PREFERRED_BONUS = 0.15        # Brett 28 Oct 14:30 WITA: +0.05 -> +0.15 (meaningful tilt)
_USD_PER = {"USD": 1.0, "AUD": 0.703, "GBP": 1.43, "GBp": 0.0143, "EUR": 1.22, "CHF": 1.34, "JPY": 0.0070, "HKD": 0.129,
            "SGD": 0.77, "KRW": 0.00075, "TWD": 0.033, "CAD": 0.72, "NZD": 0.64, "INR": 0.0118}
# 2026-10-29 Head Trader-approved (Brett's tech/neocloud wariness): explicit block on NEW ENTRIES ONLY.
# value_rank output feeds value_deploy entries only; existing positions/stops/exits are untouched.
BLOCKED_SYMBOLS = {"FVRR", "PDD", "TCOM",                                   # held; normal stops/exits continue
                   "CRWV", "NBIS", "IREN", "CORZ", "APLD", "WULF", "SMCI",   # neocloud / AI-infra
                   "BABA", "JD", "MELI", "SE", "SHOP", "BIDU",               # e-commerce / internet platforms
                   "TCEHY", "0700.HK", "9984.T", "CRTO", "CARS"}           # HT-approved 29 Oct 10:02: Tencent, SoftBank, adtech/internet
# provider industry keywords (Yahoo-style labels); gaming deliberately NOT blocked.
BLOCKED_INDUSTRY_KEYWORDS = ("internet content", "internet retail", "software", "semiconductor",
                             "information technology services", "communication equipment")
def block_reason(r):
    sym = str(r.get("symbol", "")).upper()
    if sym in BLOCKED_SYMBOLS:
        return "tech/neocloud symbol blocklist (HT-approved 29 Oct, new entries only)"
    ind = str(r.get("industry") or "").lower()
    for k in BLOCKED_INDUSTRY_KEYWORDS:
        if k in ind: return "tech/neocloud industry block '%s' (HT-approved 29 Oct, new entries only)" % r.get("industry")
    return None
def tech_reason(r):
    br = block_reason(r)
    if br: return br
    try:
        import theme_exposure as _te; tech = _te.TECH_NAMES
    except Exception:
        tech = set()
    if (r.get("sector") or "") in EXCLUDED_SECTORS: return "tech sector excluded (Brett 28 Oct 14:10)"
    if str(r.get("symbol", "")).upper() in tech: return "tech/semis/AI name excluded (Brett 28 Oct 14:10)"
    return None
def mcap_usd(r):
    f = _USD_PER.get(r.get("ccy") or "USD")
    return None if f is None or not r.get("mcap") else float(r["mcap"]) * f

def pct_rank(vals, reverse=False):
    """percentile rank in [0,1]; reverse=True means LOW is good."""
    clean = [(i, v) for i, v in enumerate(vals) if v is not None]
    out = [None] * len(vals)
    if not clean:
        return out
    # BUG FIXED: this assigned rank 0 to whatever sorted FIRST, i.e. the BEST
    # name scored ZERO. Both directions were inverted, so the "undervaluation"
    # screen ranked the most EXPENSIVE names highest — a PE-37 industrial came
    # top of a value screen. Best must score 1.0.
    clean.sort(key=lambda t: t[1], reverse=not reverse)
    n = len(clean)
    for r, (i, _) in enumerate(clean):
        out[i] = 1.0 - (r / max(n - 1, 1))
    return out

def main():
    d = json.load(open(SRC))
    rows = d["rows"]
    kept, dropped = [], []
    for r in rows:
        why = []
        tr = tech_reason(r)
        if tr: why.append(tr)
        mu = mcap_usd(r); r["mcap_usd"] = mu
        if mu is not None and mu < MIN_MCAP_USD: why.append("mcap US$%.1fm below US$2m floor" % (mu / 1e6))
        if r.get("pb") is None or r["pb"] > MAX_PB or r["pb"] <= 0:
            why.append("P/B %s outside (0,%.1f]" % (r.get("pb"), MAX_PB))
        pe = r.get("pe")
        if pe is None or pe <= MIN_PE or pe > MAX_PE:
            why.append("PE %s outside (0,%.0f] — no earnings or too dear" % (pe, MAX_PE))
        roe = r.get("roe")
        if roe is None or roe < MIN_ROE:
            why.append("ROE %s below %.0f%% — cheap for a reason" % (roe, MIN_ROE*100))
        de = r.get("de")
        if de is not None and de > MAX_DE:
            why.append("debt/equity %.0f above %.0f" % (de, MAX_DE))
        (dropped if why else kept).append(
            dict(r, drop_reason="; ".join(why)) if why else r)

    pb = pct_rank([r["pb"] for r in kept], reverse=True)
    pe = pct_rank([r.get("pe") for r in kept], reverse=True)
    ev = pct_rank([r.get("ev_ebitda") for r in kept], reverse=True)
    ro = pct_rank([r.get("roe") for r in kept], reverse=False)
    de = pct_rank([r.get("de") for r in kept], reverse=True)

    for i, r in enumerate(kept):
        vparts = [x for x in (pb[i], pe[i], ev[i]) if x is not None]
        qparts = [x for x in (ro[i], de[i]) if x is not None]
        r["value_score"] = sum(vparts)/len(vparts) if vparts else 0.0
        r["quality_score"] = sum(qparts)/len(qparts) if qparts else 0.0
        r["sector_bonus"] = PREFERRED_BONUS if (r.get("sector") or "") in PREFERRED_SECTORS else 0.0
        r["score"] = round(0.65*r["value_score"] + 0.35*r["quality_score"] + r["sector_bonus"], 4)
        r["value_score"] = round(r["value_score"], 4)
        r["quality_score"] = round(r["quality_score"], 4)
    kept.sort(key=lambda r: -r["score"])
    # 2026-10-30 HT ruling: optional next-in-line queue (reports/value_priority.json {"priority": [...]}).
    # Reorders ONLY names that already passed every floor/block above; value_deploy still applies all its gates.
    try: _pri = [str(x).upper() for x in (json.load(open(PRIORITY)).get("priority") or [])]
    except Exception: _pri = []
    if _pri:
        _pos = {s: i for i, s in enumerate(_pri)}
        for r in kept:
            if r["symbol"] in _pos: r["ht_priority"] = _pos[r["symbol"]] + 1
        kept.sort(key=lambda r: (_pos.get(r["symbol"], len(_pos)), -r["score"]))
    json.dump({"generated_utc": d["generated_utc"],
               "search_count": {"screened": d["n_screened"],
                                "reconciled": d["n_kept"],
                                "rejected_unreconciled": d["n_rejected"],
                                "passed_floors": len(kept),
                                "dropped_on_floors": len(dropped)},
               "floors": {"min_roe": MIN_ROE, "max_de": MAX_DE,
                          "max_pe": MAX_PE, "max_pb": MAX_PB,
                          "min_mcap_usd": MIN_MCAP_USD, "excluded_sectors": sorted(EXCLUDED_SECTORS),
                          "preferred_sectors": sorted(PREFERRED_SECTORS), "blocked_symbols": sorted(BLOCKED_SYMBOLS), "blocked_industry_keywords": list(BLOCKED_INDUSTRY_KEYWORDS), "preferred_bonus": PREFERRED_BONUS},
               "standing": "NO VALIDATED EDGE — cross-sectional screen, no backtest, "
                           "no deflation, no CV, no kill-test. Search count travels.",
               "ranked": kept, "dropped": dropped}, open(OUT, "w"), indent=1)
    print("ranked %d names (of %d reconciled); %d dropped on quality/value floors"
          % (len(kept), len(rows), len(dropped)))
    return kept

if __name__ == "__main__":
    main()
