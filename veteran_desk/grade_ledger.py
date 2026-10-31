"""Outcome ledger for THF and Veteran Desk grades. PAPER ONLY.

Every A, A+ and B is appended with timestamp, symbol, pillar scores and the
inputs that produced them. Forward marks at 1, 3 and 5 trading days are filled
later in R (vs the grade's stop) and percent. A weekly report groups hit rate
and average R by grade and by pillar.

Writing the ledger never changes a gate, a stop, a size, a cap or the firm halt.
"""
import datetime as dt, json, os
UTC = dt.timezone.utc
KEEP = ("A+", "A", "B")
HORIZONS = (1, 3, 5)


def _root():
    return os.environ.get("DESK_BASE") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def paths(root=None):
    root = root or _root()
    d = os.path.join(root, "state", "grade_ledger")
    return d, os.path.join(d, "outcomes.jsonl"), os.path.join(d, "weekly.json")


def _skip_default():
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _append(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")


def _read(path):
    out = []
    try:
        for line in open(path):
            line = line.strip()
            if line:
                out.append(json.loads(line))
    except Exception:
        return []
    return out


def _key(r):
    return (r.get("source"), r.get("seat"), str(r.get("sym") or r.get("ticker") or "").upper(), r.get("grade"), (r.get("utc") or "")[:16])


def record(rows, path):
    """Append grades that are not already stored for the same minute. Returns the new rows."""
    if not rows:
        return []
    have = {_key(r) for r in _read(path)}
    fresh = []
    for r in rows:
        if r.get("grade") not in KEEP:
            continue
        if _key(r) in have:
            continue
        fresh.append(r)
        have.add(_key(r))
    if fresh:
        _append(path, fresh)
    return fresh


def forwards_from_bars(entry, stop, side, bars):
    """bars: [(date, close), ...] strictly after the grade's last bar, oldest first.

    R is directional P&L divided by |entry - stop|. Percent is the directional
    price change. Missing stop -> R is null, percent still fills.
    """
    out = {}
    try:
        entry = float(entry)
    except (TypeError, ValueError):
        return out
    if not entry:
        return out
    try:
        risk = abs(entry - float(stop)) if stop is not None else 0.0
    except (TypeError, ValueError):
        risk = 0.0
    short = str(side or "").upper() == "SHORT"
    for h in HORIZONS:
        if len(bars) < h:
            continue
        d, px = bars[h - 1]
        try:
            px = float(px)
        except (TypeError, ValueError):
            continue
        if not px:
            continue
        diff = (entry - px) if short else (px - entry)
        pct = diff / entry
        R = (diff / risk) if risk else None
        out["d%d" % h] = dict(date=str(d)[:10], px=px, r=(round(R, 4) if R is not None else None), pct=round(pct, 6))
    return out


def apply_forwards(rows, price_fn):
    """price_fn(sym, after_date) -> [(date, close), ...] after that date. In-place."""
    for r in rows:
        if r.get("fwd") and all(("d%d" % h) in r["fwd"] for h in HORIZONS):
            continue
        after = (r.get("last_bar") or (r.get("utc") or "")[:10])
        try:
            bars = price_fn(r.get("sym") or r.get("ticker"), after) or []
        except Exception:
            continue
        fw = forwards_from_bars(r.get("close") or r.get("entry"), r.get("stop"), r.get("side"), bars)
        if fw:
            r["fwd"] = dict(r.get("fwd") or {}, **fw)
    return rows


def _resolved(r):
    fw = r.get("fwd") or {}
    for h in (5, 3, 1):
        cell = fw.get("d%d" % h)
        if cell and cell.get("r") is not None:
            return cell
    return None


def _avg(xs):
    return (sum(xs) / len(xs)) if xs else None


def _bucket(group):
    by_h = {h: [] for h in HORIZONS}
    resolved = []
    for r in group:
        fw = r.get("fwd") or {}
        for h in HORIZONS:
            cell = fw.get("d%d" % h)
            if cell and cell.get("r") is not None:
                by_h[h].append(cell["r"])
        cell = _resolved(r)
        if cell:
            resolved.append(cell["r"])
    n = len(resolved)
    return dict(n_graded=len(group), n_resolved=n,
                hit_rate=(round(sum(1 for x in resolved if x > 0) / n, 4) if n else None),
                avg_r=(round(_avg(resolved), 4) if n else None),
                avg_r_1=(round(_avg(by_h[1]), 4) if by_h[1] else None),
                avg_r_3=(round(_avg(by_h[3]), 4) if by_h[3] else None),
                avg_r_5=(round(_avg(by_h[5]), 4) if by_h[5] else None))


def _dominant(r):
    z = r.get("pillars_z") or {}
    best, val = None, None
    for p, v in z.items():
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if val is None or v > val:
            best, val = p, v
    return best


def weekly_report(rows, now=None):
    now = now or dt.datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    cut = (now - dt.timedelta(days=7)).isoformat()
    window = [r for r in rows if str(r.get("utc") or "") >= cut and r.get("grade") in KEEP]
    by_grade = {}
    for g in KEEP:
        by_grade[g] = _bucket([r for r in window if r.get("grade") == g])
    by_pillar = {}
    for p in ("P", "V", "N", "F", "X"):
        owned = []
        for r in window:
            z = r.get("pillars_z") or {}
            try:
                hot = float(z.get(p)) > 1
            except (TypeError, ValueError):
                hot = False
            if hot or _dominant(r) == p:
                owned.append(r)
        # de-dupe while keeping order
        seen, uniq = set(), []
        for r in owned:
            i = id(r)
            if i in seen:
                continue
            seen.add(i)
            uniq.append(r)
        by_pillar[p] = _bucket(uniq)
    return dict(utc=now.isoformat(), window_start=cut, paper_only=True, n=len(window),
                by_grade=by_grade, by_pillar=by_pillar,
                note="Hit = forward R > 0 at the longest available of 5/3/1 trading days. R uses the grade stop. Advisory only.")


def _rewrite(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")
    os.replace(tmp, path)


def refresh_forwards(path, price_fn):
    rows = _read(path)
    apply_forwards(rows, price_fn)
    _rewrite(path, rows)
    return rows


def write_weekly(rows, weekly_path, now=None):
    rep = weekly_report(rows, now=now)
    os.makedirs(os.path.dirname(weekly_path), exist_ok=True)
    tmp = weekly_path + ".tmp"
    json.dump(rep, open(tmp, "w"), indent=1)
    os.replace(tmp, weekly_path)
    return rep


def _pit_prices(sym, after):
    try:
        from veteran_desk import pit as P
        c = P.con()
        try:
            q = c.execute("SELECT date,c FROM bars WHERE sym=? AND date>? ORDER BY date ASC LIMIT 8", (sym, str(after or "")[:10])).fetchall()
        finally:
            c.close()
        return [(r[0], r[1]) for r in q]
    except Exception:
        return []


def maintain(path, weekly_path, now=None):
    """Fill forwards from pit (fail open) and refresh the weekly file."""
    try:
        rows = refresh_forwards(path, _pit_prices)
    except Exception:
        rows = _read(path)
    try:
        return write_weekly(rows, weekly_path, now=now)
    except Exception:
        return None


def record_thf(scored, ledger_rows, path=None, weekly_path=None):
    """scored is unused except as a hook point; ledger_rows are the A/A+/B dicts."""
    if _skip_default() and path is None:
        return []
    _d, default, weekly = paths()
    path = path or default
    fresh = record(ledger_rows, path)
    if path == default:
        try:
            maintain(path, weekly_path or weekly)
        except Exception:
            pass
    return fresh


def _vd_rows(log, packs=None):
    utc = (log or {}).get("asof_utc") or dt.datetime.now(UTC).isoformat()
    packs = packs or {}
    rows = []
    for seat, info in ((log or {}).get("seats") or {}).items():
        pk = packs.get(seat) or {}
        inst = {f.get("sym"): f for f in (pk.get("instruments") or []) if isinstance(f, dict)}
        seen = set()
        items = list(info.get("graded") or []) + list(info.get("ranked") or [])
        prop = None
        for rec in (log or {}).get("proposals") or []:
            if rec.get("seat") == seat:
                p = rec.get("proposal") or {}
                items.append(dict(ticker=p.get("ticker"), grade=p.get("grade"), side=p.get("side"),
                                  entry=p.get("entry"), stop=p.get("stop"), reason=p.get("thesis")))
                prop = rec
        for g in items:
            if not isinstance(g, dict) or g.get("grade") not in KEEP:
                continue
            sym = g.get("ticker") or g.get("sym")
            if not sym or (seat, sym, g.get("grade")) in seen:
                continue
            seen.add((seat, sym, g.get("grade")))
            feat = inst.get(sym) or ((prop or {}).get("features") if prop and (prop.get("proposal") or {}).get("ticker") == sym else None) or {}
            rows.append(dict(
                source="vd", seat=seat, sym=sym, side=g.get("side"), grade=g.get("grade"),
                utc=utc, score=None, pillars_z=g.get("pillars_z"), pillars_raw=g.get("pillars_raw"),
                close=g.get("entry") or feat.get("close"), stop=g.get("stop") or feat.get("stop"),
                last_bar=feat.get("last_date"),
                inputs=dict(reason=g.get("reason") or g.get("why"), atr14=feat.get("atr14"), ret20=feat.get("ret20"),
                            news=(pk.get("news_tags") or {}).get(sym), flow_arm=os.environ.get("TFO_FLOW_AB") or "without"),
                earnings_advisory=g.get("earnings_advisory"), paper_only=True, blocks_trade=False))
    return rows


def record_vd(log, packs=None, path=None):
    if _skip_default() and path is None:
        return []
    try:
        from veteran_desk import earnings_advisory as EA
        flags = EA.flags_for([g.get("ticker") or g.get("sym") for info in ((log or {}).get("seats") or {}).values() for g in (info.get("graded") or []) if isinstance(g, dict)])
        warn = EA.warning_for()
    except Exception:
        flags, warn = {}, "earnings advisory unavailable; fail-open"
    rows = _vd_rows(log, packs)
    for r in rows:
        r["earnings_advisory"] = flags.get(str(r["sym"]).upper())
        if warn and not r["earnings_advisory"]:
            r["earnings_warning"] = warn
    _d, default, weekly = paths()
    fresh = record(rows, path or default)
    if path is None:
        try:
            maintain(default, weekly)
        except Exception:
            pass
    return fresh
