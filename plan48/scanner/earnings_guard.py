"""Advisory earnings-date flag. A print inside two days is marked. It never blocks a trade."""
import datetime as dt
UTC = dt.timezone.utc

def _date(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except Exception: return None

def scan(items, calendar, now=None, window_days=2):
    """items: [{symbol, role}]. calendar: {SYMBOL: date} or None. Unknown dates stay missing, not filled."""
    now = now or dt.datetime.now(UTC)
    if now.tzinfo is None: now = now.replace(tzinfo=UTC)
    today = now.date()
    cal = {str(k).upper(): _date(v) for k, v in (calendar or {}).items()}
    out = []
    for it in items or []:
        sym = str(it.get("symbol") or it.get("sym") or it.get("ticker") or "").upper()
        if not sym: continue
        role = it.get("role") or "candidate"
        d = cal.get(sym)
        row = dict(symbol=sym, role=role, advisory=True, blocks_trade=False, window_days=window_days)
        if d is None:
            row.update(status="missing", earnings_date=None, days=None, within_2d=None, note="earnings date unknown; not a block")
        else:
            days = (d - today).days
            row.update(status="fresh", earnings_date=d.isoformat(), days=days, within_2d=0 <= days <= window_days,
                       note="earnings inside 2 days" if 0 <= days <= window_days else "earnings outside the 2-day window")
        out.append(row)
    return out
