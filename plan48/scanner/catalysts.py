"""Catalyst setups as scorer candidates. Each one carries a 1R stop and a 2R target suggestion. None of them is a rail."""
import datetime as dt
UTC = dt.timezone.utc

def _val(field):
    if not isinstance(field, dict) or field.get("status") == "missing" or field.get("value") is None: return None
    return field["value"]

def _stale(feats, keys):
    return [k for k in keys if (feats.get(k) or {}).get("stale")]

def r_plan(entry, atr, side):
    out = dict(stop_r=1.0, target_r=2.0, r_unit="1 ATR", advisory=True, blocks_trade=False, applied_to_rail=False)
    if entry is None or atr is None or atr <= 0:
        out.update(stop=None, target=None, note="1R stop and 2R target; price levels need an entry and an ATR")
        return out
    if side == "LONG": stop, target = entry - atr, entry + 2 * atr
    else: stop, target = entry + atr, entry - 2 * atr
    out.update(entry=entry, stop=round(stop, 6), target=round(target, 6), note="suggestion only; the seat rail is unchanged")
    return out

def _cand(setup, sym, side, price, evidence, stale):
    entry, atr = price.get("entry"), price.get("atr")
    return dict(setup=setup, ticker=sym, side=side, evidence=evidence, stale_inputs=stale, feeds_scorer="candidate_only",
                **r_plan(entry, atr, side))

def pead(sym, feats, price):
    sur, rev = _val(feats.get("earnings_surprises")), _val(feats.get("estimate_revisions"))
    if not isinstance(sur, list) or not sur or not isinstance(rev, dict): return None
    last = sur[-1]
    if not isinstance(last, dict) or last.get("surprise_pct") is None or last["surprise_pct"] <= 0: return None
    if rev.get("direction") != "up": return None
    return _cand("pead_beat_raise", sym, "LONG", price, dict(surprise_pct=last["surprise_pct"], revision="up"),
                 _stale(feats, ("earnings_surprises", "estimate_revisions")))

def gap_and_go(sym, feats, price):
    vol = _val(feats.get("unusual_volume")) or {}
    gap, hold, ratio = price.get("gap"), price.get("gap_hold"), vol.get("ratio_vs_20d") if isinstance(vol, dict) else None
    if gap is None or hold is None or ratio is None: return None
    atr_pct = price.get("atr_pct") or 0
    if abs(gap) < max(atr_pct, 0.02) or ratio < 1.5 or not hold: return None
    side = "LONG" if gap > 0 else "SHORT"
    return _cand("gap_and_go", sym, side, price, dict(gap=gap, ratio_vs_20d=ratio), _stale(feats, ("unusual_volume", "gap")))

def index_changes(sym, feats, price, now):
    rows = _val(feats.get("index_changes"))
    if not isinstance(rows, list): return None
    out = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("action") or not r.get("date"): continue
        try: d = dt.date.fromisoformat(str(r["date"])[:10])
        except Exception: continue
        if abs((d - now.date()).days) > 15: continue
        side = "LONG" if r["action"] == "add" else "SHORT" if r["action"] == "delete" else None
        if not side: continue
        setup = "index_add" if side == "LONG" else "index_delete"
        out.append(_cand(setup, sym, side, price, dict(date=d.isoformat(), index=r.get("index"), action=r["action"]),
                         _stale(feats, ("index_changes",))))
    return out

def insider_cluster(sym, feats, price):
    ins = _val(feats.get("insider_buys"))
    if not isinstance(ins, dict): return None
    distinct, count = ins.get("distinct_buyers"), ins.get("buy_count")
    if distinct is None and count is None: return None
    if (distinct or 0) < 2 and (count or 0) < 3: return None
    return _cand("insider_cluster_buy", sym, "LONG", price, dict(distinct_buyers=distinct, buy_count=count),
                 _stale(feats, ("insider_buys",)))

def quality(feats, side, now):
    """Score only the components that are present. A missing component is listed, not scored as zero."""
    parts, missing = {}, []
    sur = _val(feats.get("earnings_surprises"))
    if isinstance(sur, list) and sur and isinstance(sur[-1], dict) and sur[-1].get("surprise_pct") is not None:
        parts["surprise"] = round(min(100.0, abs(sur[-1]["surprise_pct"]) / 0.20 * 100.0), 1)
    else: missing.append("surprise")
    g = _val(feats.get("guidance"))
    change = g.get("change") if isinstance(g, dict) else None
    if change in ("up", "down", "maintain"): parts["guidance"] = {"up": 100.0, "maintain": 50.0, "down": 0.0}[change]
    else: missing.append("guidance")
    rev = _val(feats.get("estimate_revisions"))
    recent = []
    if isinstance(rev, dict):
        for x in rev.get("within_48h") or []:
            if isinstance(x, dict) and x.get("direction") in ("up", "down"): recent.append(x)
    if recent:
        ups = sum(1 for x in recent if x["direction"] == "up"); dns = len(recent) - ups
        parts["revisions_48h"] = round(100.0 * ups / len(recent), 1)
    else: missing.append("revisions_48h")
    opt = _val(feats.get("options"))
    pc = opt.get("put_call") if isinstance(opt, dict) else None
    if pc is None or side not in ("LONG", "SHORT"): missing.append("options")
    elif side == "LONG": parts["options"] = 100.0 if pc < 0.7 else 0.0 if pc > 1.3 else 50.0
    else: parts["options"] = 100.0 if pc > 1.3 else 0.0 if pc < 0.7 else 50.0
    if not parts:
        return dict(value=None, status="missing", asof=None, source="catalyst_quality", missing=True, stale=False,
                    advisory=True, blocks_trade=False, note="no quality inputs; not scored as zero")
    score = round(sum(parts.values()) / len(parts), 1)
    stale = any((feats.get(k) or {}).get("stale") for k in ("earnings_surprises", "guidance", "estimate_revisions", "options"))
    return dict(value=dict(score=score, components=parts, missing_components=missing), status="stale" if stale else "fresh",
                asof=now.isoformat(), source="catalyst_quality", missing=False, stale=stale, advisory=True, blocks_trade=False)

def detect(sym, feats, price, now=None):
    now = now or dt.datetime.now(UTC)
    if now.tzinfo is None: now = now.replace(tzinfo=UTC)
    found = []
    for item in (pead(sym, feats, price), gap_and_go(sym, feats, price), insider_cluster(sym, feats, price)):
        if item: found.append(item)
    extra = index_changes(sym, feats, price, now) or []
    found.extend(extra)
    for c in found: c["quality"] = quality(feats, c["side"], now)
    return found
