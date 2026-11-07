"""Read-only interpretation of order-age alerts. Never changes an order or gate."""
import datetime as dt,math

def age_review(alert, book, now=None):
    if alert.get('cls')!='armed_unfilled':return None
    positions=((book.get('seats') or {}).get(alert.get('seat')) or {}).get('positions') or {}
    if not isinstance(positions,dict):return None
    matches=[p for p in positions.values() if isinstance(p,dict) and p.get('ticker')==alert.get('ticker') and p.get('status')=='armed']
    if len(matches)!=1:return None
    p=matches[0]
    if any(p.get(k) for k in ('triggered_utc','filled_utc','fill')):return None
    try:
        seen=dt.datetime.fromisoformat(str(p.get('mark_utc')).replace('Z','+00:00'))
        if seen.tzinfo is None:return None
        current=now or dt.datetime.now(dt.timezone.utc)
        if not -30 <= (current-seen).total_seconds() <= 300:return None
        entry=float(p['entry']);side=p['side'];kind=p['order_type']
        quote=float(p['mark_ask'] if side=='LONG' else p['mark_bid'])
        if side not in ('LONG','SHORT') or not all(math.isfinite(x) and x>0 for x in (entry,quote)):return None
    except (KeyError,ValueError,TypeError):return None
    if kind=='stop_limit':waiting=quote<entry if side=='LONG' else quote>entry
    elif kind=='limit':waiting=quote>entry if side=='LONG' else quote<entry
    else:return None
    if not waiting:return None
    return ('Order age review: %s %s %s; current %s %.6g is on the waiting side of the %.6g %s entry. '
            'Age alone does not prove a missed fill; earlier trigger history remains unverified. Order unchanged.') % (
        alert.get('seat'),alert.get('ticker'),side,'ask' if side=='LONG' else 'bid',quote,entry,kind)
