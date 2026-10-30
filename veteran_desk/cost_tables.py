"""ONE SOURCE for Veteran Desk trading-cost tables (tfo_costmodel_20261030; PAPER only).
Used by book.py (fills, exits, open net R, BE floors), desk.py (pre-trade sizing / backtests) and gate_v2.py (base rates).
Pure functions here return FRACTIONS of notional; book.leg_cost_aud() turns the same tables into A$ per leg."""
CM_SUFFIX = {".L": "UK", ".PA": "FR", ".MC": "ES", ".MI": "IT", ".SW": "CH", ".DE": "EU", ".AS": "EU", ".BR": "EU", ".LS": "EU", ".VI": "EU", ".IR": "EU",
             ".ST": "EU", ".CO": "EU", ".HE": "EU", ".OL": "EU", ".AX": "AU", ".T": "JP", ".HK": "HK", ".KS": "KR", ".KQ": "KR", ".TW": "TW", ".TWO": "TW",
             ".TO": "CA", ".V": "CA", ".NZ": "NZ", ".SI": "SG", ".NS": "IN", ".BO": "IN", ".SS": "CN", ".SZ": "CN"}
VENUE_MKT = {"US": "US", "ASX": "AU", "LSE": "UK", "Euronext": "EU", "XETRA": "EU", "TSE": "JP", "HKEX": "HK", "SGX": "SG", "KRX": "KR", "TWSE": "TW",
             "NSE": "IN", "NZX": "NZ", "SIX": "CH", "BME": "ES", "MILAN": "IT", "NORDIC": "EU", "TSX": "CA", "SSE": "CN", "SZSE": "CN",
             "FX": "FX", "FUT": "FUT", "IDX": "IDX", "CRYPTO": "CRYPTO"}
CM_COMM = {"US": dict(per_share=0.0035, min_ccy=0.35, clearing_per_share=0.0002),   # IBKR tiered-style, USD
           "UK": dict(pct=.0005), "EU": dict(pct=.0005), "FR": dict(pct=.0005), "ES": dict(pct=.0005), "IT": dict(pct=.0005), "CH": dict(pct=.0005),
           "AU": dict(pct=.0008, min_aud=5.0), "JP": dict(pct=.0005), "HK": dict(pct=.0005), "FX": dict(pct=.00002), "IDX": dict(pct=.0003), "DEFAULT": dict(pct=.0008)}
CM_TAX = {"UK": dict(buy=.005, ptm_ccy=1.50, ptm_over_ccy=10000.0), "FR": dict(buy=.004), "ES": dict(buy=.002), "IT": dict(buy=.001),
          "HK": dict(buy=.001, sell=.001), "KR": dict(sell=.0018), "TW": dict(sell=.003), "US": dict(sell=20.60e-6)}
CM_SPREAD_BPS = [(200e6, 1.0), (50e6, 2.0), (10e6, 5.0), (1e6, 12.0), (0.0, 25.0)]   # half-spread bps by ADV (A$/day)
# no-ADV fallback: liquidity tier by market (tfo_costmodel_20261030 ruling 5) - replaces flat 5bp default
CM_SPREAD_TIER_BPS = {"US_ETF": 1.5, "US": 3.0, "AU": 4.0, "UK": 4.0, "EU": 4.0, "FR": 4.0, "ES": 4.0, "IT": 4.0, "CH": 4.0, "JP": 4.0, "CA": 4.0,
                      "HK": 5.0, "SG": 6.0, "KR": 6.0, "TW": 6.0, "IN": 6.0, "NZ": 8.0, "CN": 8.0,
                      "FX": 0.5, "FUT": 1.0, "IDX": 2.0, "CRYPTO": 5.0, "DEFAULT": 8.0}
US_ETFS = {"SPY", "IVV", "VOO", "QQQ", "IWM", "DIA", "XLE", "XLF", "XLK", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC", "SMH", "SOXX",
           "GLD", "SLV", "TLT", "IEF", "HYG", "LQD", "EEM", "EFA", "FXI", "MCHI", "KWEB", "EWJ", "EWZ", "GDX", "XOP", "OIH", "KRE", "XBI", "ARKK", "USO", "UNG",
           "TQQQ", "SQQQ", "UPRO", "SPXU", "SPXL", "SPXS", "SSO", "SDS", "QLD", "QID", "SOXL", "SOXS", "TNA", "TZA", "UDOW", "SDOW", "TECL", "TECS",
           "FAS", "FAZ", "LABU", "LABD", "SH", "PSQ", "DOG"}
CM_FX_CONV = 0.0003                                                                # 3bp on non-AUD trades, each leg
CM_KRAKEN = dict(entry=.0025, exit=.0040)                                          # maker in, taker out
CM_FUT = {"GC=F": dict(mult=100.0, comm=0.85, exch=1.55, alt="MGC", alt_mult=10.0, alt_comm=0.25, alt_exch=0.35),
          "MGC=F": dict(mult=10.0, comm=0.25, exch=0.35)}                         # USD per contract per leg (IBKR + CME est.)
CM_FUT_DEFAULT_PCT = .0003                                                         # per leg, unknown futures
CM_BORROW_DEFAULT_PA = {"US": .0025, "DEFAULT": .03}
US_REF_PX = 50.0                                                                   # per-share commission -> pct when px unknown
def market(ticker, venue=None):
    v = venue
    if v in ("CRYPTO", "FUT", "FX", "IDX"): return v
    tk = (ticker or "").upper()
    for sfx in sorted(CM_SUFFIX, key=len, reverse=True):
        if tk.endswith(sfx): return CM_SUFFIX[sfx]
    return VENUE_MKT.get(v, v or "DEFAULT")
def spread_bps(ticker, mk, adv_aud=None):
    """Half-spread bps: ADV bucket when ADV known, else liquidity tier (US ETF list -> US_ETF)."""
    if adv_aud:
        for lo, b in CM_SPREAD_BPS:
            if float(adv_aud) >= lo: return b
    if mk == "US" and (ticker or "").upper() in US_ETFS: return CM_SPREAD_TIER_BPS["US_ETF"]
    return CM_SPREAD_TIER_BPS.get(mk, CM_SPREAD_TIER_BPS["DEFAULT"])
def non_aud(ticker, mk):
    if mk == "AU": return False
    if mk == "FX": return False
    if mk == "CRYPTO": return not (ticker or "").upper().endswith("AUD")
    return True
def est_rt_rate(ticker=None, venue=None, px=None, adv_aud=None):
    """Round-trip cost estimate as a fraction of notional (one buy leg + one sell leg), from the tables above.
    Ignores minimum tickets (pre-trade estimate). px = listing-ccy price (US per-share commission, futures per-contract fees)."""
    mk = market(ticker, venue); sp = spread_bps(ticker, mk, adv_aud) / 1e4
    r = 2 * sp
    if mk == "CRYPTO": r += CM_KRAKEN["entry"] + CM_KRAKEN["exit"]
    elif mk == "FUT":
        F = CM_FUT.get(ticker or "")
        r += (2 * (F["comm"] + F["exch"]) / (float(px) * F["mult"])) if (F and px) else 2 * CM_FUT_DEFAULT_PCT
    else:
        C = CM_COMM.get(mk, CM_COMM["DEFAULT"])
        if "per_share" in C: r += 2 * (C["per_share"] + C.get("clearing_per_share", 0)) / float(px or US_REF_PX)
        else: r += 2 * C["pct"]
        T = CM_TAX.get(mk) or {}; r += T.get("buy", 0) + T.get("sell", 0)
    if non_aud(ticker, mk): r += 2 * CM_FX_CONV
    return r
def venue_rt_table():
    """Venue -> RT fraction (legacy COST_RT shape, now DERIVED from the tables)."""
    return {v: est_rt_rate(None, v) for v in VENUE_MKT}
