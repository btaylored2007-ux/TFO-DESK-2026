"""Intraday bar health: last bar time per timeframe and short gaps.

PAPER data only. A failed fetch still writes the file with a warning. Nothing
here blocks a trade.
"""
import datetime as dt, json, os
UTC = dt.timezone.utc
STEP = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "60m": 3600}
# A hole of 1..6 expected bars is a gap. Longer holes are session/weekend breaks.


def _iso(ts):
    if isinstance(ts, str):
        return ts
    try:
        return dt.datetime.fromtimestamp(float(ts), UTC).isoformat()
    except Exception:
        return None


def gaps_for(stamps, step_s):
    """stamps: sorted epoch seconds. Returns [{from, to, missing_bars}]."""
    out = []
    xs = []
    for s in stamps or []:
        try:
            xs.append(float(s))
        except (TypeError, ValueError):
            continue
    xs.sort()
    for a, b in zip(xs, xs[1:]):
        if step_s <= 0 or b <= a:
            continue
        missing = int(round((b - a) / float(step_s))) - 1
        if 1 <= missing <= 6:
            out.append(dict(**{"from": _iso(a), "to": _iso(b), "missing_bars": missing}))
    return out


def build(frames, now=None):
    """frames: {timeframe: {symbol: [epoch or iso, ...]}}.

    Returns the health document. Empty frames are valid (fail open).
    """
    now = now or dt.datetime.now(UTC)
    tfs = {}
    for tf, syms in (frames or {}).items():
        step = STEP.get(str(tf), None)
        per = {}
        gap_count = 0
        last = None
        for sym, stamps in (syms or {}).items():
            xs = []
            for s in stamps or []:
                if isinstance(s, str):
                    try:
                        xs.append(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
                    except Exception:
                        continue
                else:
                    try:
                        xs.append(float(s))
                    except (TypeError, ValueError):
                        continue
            xs.sort()
            g = gaps_for(xs, step) if step else []
            gap_count += len(g)
            last_s = _iso(xs[-1]) if xs else None
            if last_s and (last is None or last_s > last):
                last = last_s
            per[sym] = dict(last_bar_utc=last_s, n=len(xs), gaps=g)
        tfs[str(tf)] = dict(last_bar_utc=last, symbols=per, gap_count=gap_count, step_s=step)
    return dict(utc=now.isoformat() if isinstance(now, dt.datetime) else str(now), paper_only=True, blocks_trade=False, timeframes=tfs)


def write(doc, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    json.dump(doc, open(tmp, "w"), indent=1)
    os.replace(tmp, path)
    return path


def default_path(root=None):
    root = root or os.environ.get("DESK_BASE") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", "intraday", "health.json")


_BUF = {}


def observe(sym, interval, bars, path=None, now=None):
    """Remember one symbol's bar timestamps and rewrite health.json. Never raises."""
    try:
        stamps = []
        for b in bars or []:
            stamps.append(b[0] if not isinstance(b, dict) else b.get("timestamp") or b.get("t"))
        _BUF.setdefault(str(interval), {})[str(sym)] = stamps
        dest = path or default_path()
        if os.environ.get("PYTEST_CURRENT_TEST") and path is None:
            return None
        return write(build(_BUF, now=now), dest)
    except Exception:
        return None


def publish(frames, path=None, now=None, warnings=None):
    doc = build(frames, now=now)
    if warnings:
        doc["warnings"] = list(warnings)
    return write(doc, path or default_path())
