"""Attach advisory fields to seat score rows. Does not change a score, a grade, a floor, a stop or wake_ok."""
import datetime as dt, json, os
from plan48.scanner import catalysts, earnings_guard, regime
UTC = dt.timezone.utc
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SNAP = os.path.join(ROOT, "state", "seat_features", "latest.json")
KEEP = ("score", "grade", "floors_failed", "wake_ok", "stop", "k_atr", "earnings_block")

def load_snapshot(path=None):
    try: return json.load(open(path or SNAP))
    except Exception: return None

def _price(row, feats):
    gap = (feats.get("gap") or {}).get("value") or {}
    close = row.get("close")
    atr = None
    if row.get("atr") is not None: atr = row.get("atr")
    elif close and row.get("atr_pct"): atr = close * row["atr_pct"]
    return dict(entry=close, atr=atr, atr_pct=row.get("atr_pct"), gap=gap.get("gap") if isinstance(gap, dict) else None,
                gap_hold=gap.get("gap_hold") if isinstance(gap, dict) else None)

def annotate_rows(seat, rows, store=None, now=None):
    """Add regime, candidates, earnings flags, relative strength and quality. Returns the seat regime tag."""
    now = now or dt.datetime.now(UTC)
    if now.tzinfo is None: now = now.replace(tzinfo=UTC)
    store = store if store is not None else (load_snapshot() or {})
    reg = store.get("regime") or {}
    tag = regime.tag_for(seat, reg)
    tickers = store.get("tickers") or {}
    cal = store.get("earnings_calendar") or {}
    items = []
    frozen = []
    for row in rows or []:
        frozen.append({k: row.get(k) for k in KEEP})
        sym = str(row.get("sym") or row.get("ticker") or "").upper()
        feats = tickers.get(sym) or {}
        price = _price(row, feats)
        cands = catalysts.detect(sym, feats, price, now) if sym else []
        row["catalyst_candidates"] = cands
        row["catalyst_quality"] = catalysts.quality(feats, row.get("side") or (cands[0]["side"] if cands else None), now)
        row["relative_strength"] = dict(rs_index=feats.get("rs_index"), rs_sector=feats.get("rs_sector"), advisory=True, blocks_trade=False)
        row["regime_tag"] = tag
        row["premarket"] = feats.get("premarket")
        row["advisory_only"] = True
        if sym: items.append(dict(symbol=sym, role="candidate"))
    flags = {f["symbol"]: f for f in earnings_guard.scan(items, cal, now)}
    for row, was in zip(rows or [], frozen):
        sym = str(row.get("sym") or row.get("ticker") or "").upper()
        row["earnings_flag"] = flags.get(sym) or dict(symbol=sym, role="candidate", status="missing", within_2d=None, advisory=True, blocks_trade=False)
        for k, v in was.items():
            if k in row and row[k] != v and k in ("score", "grade", "floors_failed", "wake_ok", "stop", "k_atr", "earnings_block"):
                row[k] = v
    return tag

def flag_open(positions, calendar, now=None):
    items = [dict(symbol=p.get("ticker") or p.get("symbol"), role="open") for p in positions or [] if p.get("status") in (None, "FILLED", "open", "PARTIAL")]
    return earnings_guard.scan(items, calendar, now)

def pack_note(out, symbols, seat):
    """Read the snapshot into a seat pack. No network. Missing snapshot is a note, not a failure."""
    store = load_snapshot()
    if not store:
        out["seat_features_note"] = "no feature snapshot yet; missing fields are not filled; advisory, not a block"
        out["regime_tag"] = dict(tag="unknown", seat=seat, advisory=True, blocks_trade=False, missing=True)
        return out
    want = {str(s).upper() for s in symbols or []}
    got = {s: store.get("tickers", {}).get(s) for s in want if s in (store.get("tickers") or {})}
    out["seat_features"] = got
    out["seat_features_note"] = "Advisory feature store. Missing is null. Stale keeps its as-of. Rails unchanged."
    out["regime_tag"] = regime.tag_for(seat or "", store.get("regime") or {})
    out["earnings_flags"] = earnings_guard.scan([dict(symbol=s, role="candidate") for s in want], store.get("earnings_calendar") or {})
    out["premarket_scanner"] = [h for h in (store.get("premarket_scanner") or []) if h.get("symbol") in want]
    return out
