"""Per-asset execution rules for the veteran desk (PAPER only; no orders, no keys). Added 2026-10-28.
Pure functions; book.py may call `pre_trade(sym, side, qty, px)` (hook not wired - see DEPLOY_LOG).
- short borrow/locate from state/ibkr_borrow.json (IBKR public FTP snapshot the desk already pulls; US + ASX)
- futures contract multipliers + approximate initial margin (APPROX, verify with broker before live)
- FX pip value / lot sizing
- Kraken spot fees 0.25% maker / 0.40% taker; Kraken perp funding accrual"""
import datetime as dt, json, os
from decimal import Decimal, ROUND_DOWN
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BORROW_PATH = os.path.join(BASE, "state", "ibkr_borrow.json")
UTC = dt.timezone.utc
BORROW_MAX_AGE_H = 36          # stale snapshot -> no locate
HTB_FEE_PA = 0.10              # >=10%/yr = hard to borrow (flag, size-down upstream)

def _age_h(updated, now):
    try:
        t = dt.datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
        if t.tzinfo is None: t = t.replace(tzinfo=UTC)
        return (now - t).total_seconds() / 3600.0
    except Exception:
        return None

def borrow_check(sym, shares, data=None, now=None, path=None):
    """Locate check for a short. Returns dict(ok, fee_pa, available, htb, why). Missing/stale data -> ok False (cannot locate)."""
    now = now or dt.datetime.now(UTC)
    if data is None:
        try:
            with open(path or BORROW_PATH) as fh: data = json.load(fh)
        except Exception:
            return dict(ok=False, fee_pa=None, available=0, htb=False, why="no borrow snapshot")
    age = _age_h(data.get("updated_utc"), now)
    if age is None or age > BORROW_MAX_AGE_H:
        return dict(ok=False, fee_pa=None, available=0, htb=False, why="borrow snapshot stale/undated (%s h)" % (None if age is None else round(age, 1)))
    s = str(sym).upper().replace("KRAKEN:", "")
    mkt, key = ("asx", s[:-3]) if s.endswith(".AX") else ("us", s.replace("-", "."))
    row = ((data.get(mkt) or {}).get("names") or {}).get(key)
    if not row:
        return dict(ok=False, fee_pa=None, available=0, htb=False, why="no %s borrow row for %s" % (mkt, key))
    avail = int(row.get("available") or 0); fee = row.get("fee_pa")
    htb = fee is not None and float(fee) >= HTB_FEE_PA
    if not row.get("ok", True) or avail < int(shares):
        return dict(ok=False, fee_pa=fee, available=avail, htb=htb, why="available %d < %d shares" % (avail, int(shares)))
    return dict(ok=True, fee_pa=fee, available=avail, htb=htb, why="located" + (" (HTB)" if htb else ""))

def borrow_cost(notional, fee_pa, days):
    return float(notional) * float(fee_pa or 0) * float(days) / 360.0

# multiplier = $ per 1.0 price unit as quoted by Yahoo; margin = APPROX initial margin fraction of notional
FUTURES = {
    "CL=F": dict(mult=1000, margin=0.09, tick=0.01, ccy="USD"), "BZ=F": dict(mult=1000, margin=0.09, tick=0.01, ccy="USD"),
    "NG=F": dict(mult=10000, margin=0.15, tick=0.001, ccy="USD"), "GC=F": dict(mult=100, margin=0.07, tick=0.10, ccy="USD"),
    "SI=F": dict(mult=5000, margin=0.10, tick=0.005, ccy="USD"), "HG=F": dict(mult=25000, margin=0.08, tick=0.0005, ccy="USD"),
    "ZW=F": dict(mult=50, margin=0.08, tick=0.25, ccy="USD"),   # quoted cents/bu, 5000 bu -> $50 per cent
    "ZC=F": dict(mult=50, margin=0.07, tick=0.25, ccy="USD"),
    "ES=F": dict(mult=50, margin=0.06, tick=0.25, ccy="USD"), "NQ=F": dict(mult=20, margin=0.07, tick=0.25, ccy="USD"),
    "YM=F": dict(mult=5, margin=0.06, tick=1.0, ccy="USD"), "RTY=F": dict(mult=50, margin=0.07, tick=0.10, ccy="USD"),
    "ZN=F": dict(mult=1000, margin=0.02, tick=0.015625, ccy="USD"), "ZB=F": dict(mult=1000, margin=0.04, tick=0.03125, ccy="USD"),
}

def futures_spec(sym):
    sp = FUTURES.get(str(sym).upper())
    if not sp: raise KeyError("no futures spec for %s" % sym)
    return sp

def futures_contracts(sym, entry, stop, risk_usd):
    """Whole contracts so that |entry-stop|*mult*n <= risk_usd. Returns dict(contracts, risk_usd, notional_usd, margin_usd)."""
    sp = futures_spec(sym); per = abs(float(entry) - float(stop)) * sp["mult"]
    n = int(float(risk_usd) // per) if per > 0 else 0
    notional = n * float(entry) * sp["mult"]
    return dict(contracts=n, risk_usd=round(n * per, 2), notional_usd=round(notional, 2), margin_usd=round(notional * sp["margin"], 2), margin_is_approx=True)

def futures_margin(sym, px, contracts):
    sp = futures_spec(sym); return round(abs(contracts) * float(px) * sp["mult"] * sp["margin"], 2)

FX_STD_LOT, FX_MINI_LOT, FX_MICRO_LOT = 100000, 10000, 1000

def fx_pair(sym):
    s = str(sym).upper().replace("=X", "").replace("_", "").replace("/", "")
    if len(s) != 6: raise ValueError("bad FX pair %s" % sym)
    return s[:3], s[3:]

def pip_size(sym):
    return 0.01 if fx_pair(sym)[1] == "JPY" else 0.0001

def pip_value_usd(sym, px, units=FX_STD_LOT, usd_per_quote=None):
    """USD value of one pip for `units` of base. usd_per_quote needed when quote ccy is not USD (USDxxx pairs use 1/px)."""
    base, quote = fx_pair(sym); v = pip_size(sym) * units
    if quote == "USD": return v
    if base == "USD": return v / float(px)
    if usd_per_quote is None: raise ValueError("usd_per_quote required for cross %s" % sym)
    return v * float(usd_per_quote)

def fx_units(sym, entry, stop, risk_usd, usd_per_quote=None, lot=FX_MICRO_LOT):
    """Units (rounded down to `lot`) so stop distance risks <= risk_usd."""
    pips = round(abs(float(entry) - float(stop)) / pip_size(sym), 6)
    if pips <= 0: return dict(units=0, lots_std=0.0, pips=0.0, risk_usd=0.0)
    per_unit_pip = pip_value_usd(sym, entry, 1, usd_per_quote)
    u = int(float(risk_usd) / (pips * per_unit_pip) // lot * lot)
    return dict(units=u, lots_std=u / FX_STD_LOT, pips=round(pips, 1), risk_usd=round(u * pips * per_unit_pip, 2))

KRAKEN_MAKER, KRAKEN_TAKER = Decimal("0.0025"), Decimal("0.0040")

def kraken_fee(notional, maker=True):
    return float((Decimal(str(notional)) * (KRAKEN_MAKER if maker else KRAKEN_TAKER)).quantize(Decimal("0.01")))

def kraken_round_trip_pct(entry_maker=True, exit_maker=False):
    return float((KRAKEN_MAKER if entry_maker else KRAKEN_TAKER) + (KRAKEN_MAKER if exit_maker else KRAKEN_TAKER))

def perp_funding(notional, rate_per_hour, hours, side):
    """Kraken perp funding: relative rate per hour (feeds.kraken_futures funding_rate/markPrice). Positive rate: longs pay shorts.
    Returns cost to the position (positive = we pay)."""
    sgn = 1 if str(side).upper() in ("LONG", "BUY") else -1
    return round(sgn * float(notional) * float(rate_per_hour) * float(hours), 2)

def crypto_qty(entry, stop, risk_usd, lot_decimals=8, maker_in=True, taker_out=True):
    """Qty so stop loss + Kraken fees in/out <= risk_usd."""
    e, s = Decimal(str(entry)), Decimal(str(stop))
    per = abs(e - s) + e * (KRAKEN_MAKER if maker_in else KRAKEN_TAKER) + s * (KRAKEN_TAKER if taker_out else KRAKEN_MAKER)
    if per <= 0: return 0.0
    return float((Decimal(str(risk_usd)) / per).quantize(Decimal(1).scaleb(-lot_decimals), rounding=ROUND_DOWN))

def pre_trade(sym, side, qty, px, now=None, borrow_data=None):
    """Single hook for book.py: returns dict(ok, why, extra). Only shorts of cash equities need a locate; crypto spot shorts are refused (no margin borrow modelled)."""
    s = str(sym).upper(); short = str(side).upper() in ("SHORT", "SELL")
    if s.startswith("PF_"): s = "KRAKEN:" + s   # tfo_pfshort_20261101: bare PF_ perp symbol = Kraken Futures
    if s.startswith("KRAKEN:"):
        if short and not s.startswith("KRAKEN:PF_"): return dict(ok=False, why="crypto short blocked: Kraken spot short not allowed - only Kraken Futures PF_ perps may be shorted (Brett 2026-11-01)")
        return dict(ok=True, why="crypto", fee_rt_pct=kraken_round_trip_pct())
    if s in FUTURES: return dict(ok=True, why="futures", margin_usd=futures_margin(s, px, qty))
    if s.endswith("=X"): return dict(ok=True, why="fx", pip=pip_size(s))
    if s.startswith("^"): return dict(ok=False, why="index not tradable directly")
    if short:
        if not (s.endswith(".AX") or "." not in s.replace("-", ".") or s.replace("-", ".").count(".") and not any(s.endswith(x) for x in (".L", ".MC", ".ST", ".DE", ".PA", ".AS", ".BR", ".SW", ".MI", ".VI", ".T", ".HK", ".SI", ".TO", ".NS", ".CO", ".OL", ".HE", ".KS", ".TW", ".NZ", ".IR", ".LS", ".WA"))):
            # borrow 2026-10-29 (TFO 19:31 WITA): non-US/ASX shorts -> conservative ESTIMATED IBKR rows (min 3%/yr, 3-day min charge); unverified -> blocked
            from veteran_desk import intl_borrow as IB
            b = IB.check(s, qty, now=now) if borrow_data is None else IB.check(s, qty, data=borrow_data, now=now)
            return dict(ok=b["ok"], why=b["why"], borrow=b, borrow_estimated=True)
        b = borrow_check(s, qty, data=borrow_data, now=now); return dict(ok=b["ok"], why=b["why"], borrow=b)
    return dict(ok=True, why="cash long")
