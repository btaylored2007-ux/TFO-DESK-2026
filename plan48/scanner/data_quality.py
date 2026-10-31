"""Freshness, nulls and vendor errors by feed. Writes state/data_quality.json. Display only."""
import datetime as dt, json, os
UTC = dt.timezone.utc
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE = os.path.join(ROOT, "state", "data_quality.json")
STATIC = os.path.join(ROOT, "static", "data_quality.json")

def _bump(feeds, field):
    if not isinstance(field, dict) or "status" not in field: return
    feed = str(field.get("source") or "unknown").split(":")[0].split("_")[0]
    b = feeds.setdefault(feed, dict(fresh=0, stale=0, missing=0, nulls=0, errors=[]))
    st = field.get("status")
    if st == "fresh": b["fresh"] += 1
    elif st == "stale": b["stale"] += 1
    else:
        b["missing"] += 1
        if field.get("value") is None: b["nulls"] += 1
    err = field.get("vendor_error")
    if err and err not in b["errors"]: b["errors"].append(str(err)[:160])

def summarize(store, now=None):
    now = now or dt.datetime.now(UTC)
    feeds = {}
    for fields in (store.get("tickers") or {}).values():
        if isinstance(fields, dict):
            for field in fields.values(): _bump(feeds, field)
    for field in (store.get("regime") or {}).values(): _bump(feeds, field)
    for name in ("sector_rotation", "energy_inventories"): _bump(feeds, store.get(name))
    return dict(paper_only=True, advisory=True, blocks_trade=False, generated_utc=now.isoformat(),
                note="Freshness, nulls and vendor errors. A red feed does not block a trade or an exit.", feeds=feeds)

def _atomic(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    json.dump(doc, open(tmp, "w"), indent=1, default=str)
    os.replace(tmp, path)

def write(doc, state_path=None, static_path=None):
    doc = dict(doc, blocks_trade=False, advisory=True)
    _atomic(state_path or STATE, doc)
    _atomic(static_path or STATIC, doc)
    return doc

def load(path=None):
    try: return json.load(open(path or STATE))
    except Exception: return dict(status="missing", advisory=True, blocks_trade=False, feeds={}, note="no data-quality file yet")
