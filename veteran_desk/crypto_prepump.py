"""Pre-pump compression is a hypothesis. It is not a prediction and not a new desk law.

A wake reports measurements, ranked near-misses, and the exact fields that are missing.
It does not invent a watch so a reply looks complete. A watch is emitted only when the
caller supplies an explicit hypothesis cutoff and the bars support it without future data.

Existing chase and stop-distance checks are the ones already in desk.risk_gate_v1.
hunt48_shorts stays off. Kraken fees are the existing cost table. This module does not arm
and does not write the book. Matched AI reviews are not claimed until files exist under
state/thf/crypto_prepump_reviews/20261107.
"""
import datetime as dt
import os

from veteran_desk import cost_tables as CT

HYPOTHESIS_ID = "prepump_own_history_v0"
REVIEWS = "state/thf/crypto_prepump_reviews/20261107"
# desk.risk_gate_v1: ret5 > .12 or ret20 > .25 or close > sma20 + 3*atr, and stop inside 0.5-4 ATR.
EXISTING_CHASE_RET5 = 0.12
EXISTING_CHASE_RET20 = 0.25
EXISTING_CHASE_ATR = 3.0
EXISTING_STOP_LO = 0.5
EXISTING_STOP_HI = 4.0
SHORTS_OFF = True


def prompt_line():
    return (
        "\nCRYPTO PRE-PUMP (hypothesis, PAPER, not a proven edge): look for range compression and "
        "volume dry-up versus that coin's own prior bars, then a breakout trigger with the stop at "
        "the range low. Do not invent a watch. If the evidence is missing or the move already "
        "happened, list the near-miss and the missing field. The existing no-chase rule and the "
        "0.5-4 ATR stop band still apply; do not widen a stop to force a fill. hunt48_shorts is off. "
        "Kraken costs stay maker 0.25% / taker 0.40%. No matched-model review is on file yet.\n"
    )


def reviews_supplied(root=None):
    path = root or REVIEWS
    found = []
    if os.path.isdir(path):
        found = [name for name in os.listdir(path) if not name.startswith(".")]
    return dict(path=path, supplied=bool(found), files=sorted(found), claimed=False,
                note="Parent will supply raw model reviews. None are claimed until those files exist.")


def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _stamp(value):
    if not value:
        return None
    try:
        stamp = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return stamp


def chase_long(row):
    """Same long-chase test as desk.risk_gate_v1. None means the inputs are missing."""
    ret5, ret20 = _num(row.get("ret5")), _num(row.get("ret20"))
    close, sma, atr = _num(row.get("close")), _num(row.get("sma20")), _num(row.get("atr14"))
    if None in (ret5, ret20, close, sma, atr) or atr <= 0:
        return None
    return ret5 > EXISTING_CHASE_RET5 or ret20 > EXISTING_CHASE_RET20 or close > sma + EXISTING_CHASE_ATR * atr


def _ordered(bars):
    rows = []
    for bar in bars or []:
        if not isinstance(bar, dict):
            return None, ["bar_not_an_object"]
        high, low, close = _num(bar.get("high")), _num(bar.get("low")), _num(bar.get("close"))
        volume = _num(bar.get("volume"))
        opened = _num(bar.get("open"))
        stamp = _stamp(bar.get("utc") or bar.get("asof"))
        if None in (high, low, close) or high < low or stamp is None:
            return None, ["bar_missing_utc_or_prices"]
        if opened is None:
            opened = close
        rows.append(dict(bar, high=high, low=low, close=close, open=opened, volume=volume, utc=stamp))
    rows.sort(key=lambda item: item["utc"])
    for prev, nxt in zip(rows, rows[1:]):
        if nxt["utc"] <= prev["utc"]:
            return None, ["duplicate_or_unsorted_utc"]
    return rows, []


def _window(bars, end, length):
    start = end - length + 1
    if start < 0:
        return None
    chunk = bars[start:end + 1]
    if len(chunk) < length:
        return None
    return chunk


def measure(bars, index, range_window, lookback):
    """Stats at index using only bars[:index+1]."""
    missing = []
    if index < 0 or index >= len(bars):
        return None, ["index_out_of_range"]
    if any(bar.get("volume") is None for bar in bars[:index + 1]):
        missing.append("volume")
    current = _window(bars, index, range_window)
    if current is None:
        missing.append("range_window")
        return None, missing
    prior = []
    volumes = []
    for end in range(index - lookback, index):
        chunk = _window(bars, end, range_window)
        if chunk is None:
            continue
        prior.append(max(bar["high"] for bar in chunk) - min(bar["low"] for bar in chunk))
        if all(bar["volume"] is not None for bar in chunk):
            volumes.append(sum(bar["volume"] for bar in chunk) / len(chunk))
    if len(prior) < max(5, lookback // 2):
        missing.append("prior_ranges")
    if missing:
        return None, missing
    span = max(bar["high"] for bar in current) - min(bar["low"] for bar in current)
    range_low = min(bar["low"] for bar in current)
    range_high = max(bar["high"] for bar in current)
    trs = []
    for absolute in range(max(0, index - range_window + 1), index + 1):
        bar = bars[absolute]
        prev = bars[absolute - 1]["close"] if absolute else bar["close"]
        trs.append(max(bar["high"] - bar["low"], abs(bar["high"] - prev), abs(bar["low"] - prev)))
    atr = sum(trs) / len(trs) if trs else None
    if not atr or atr <= 0:
        return None, ["atr"]
    range_pct = sum(1 for item in prior if item <= span) / len(prior)
    vol_now = sum(bar["volume"] for bar in current) / len(current)
    vol_pct = None if not volumes else sum(1 for item in volumes if item <= vol_now) / len(volumes)
    close = bars[index]["close"]
    ret5 = None if index < 5 or bars[index - 5]["close"] <= 0 else close / bars[index - 5]["close"] - 1
    ret20 = None if index < 20 or bars[index - 20]["close"] <= 0 else close / bars[index - 20]["close"] - 1
    sma_slice = bars[max(0, index - 19):index + 1]
    sma = sum(bar["close"] for bar in sma_slice) / len(sma_slice)
    return dict(index=index, asof=bars[index]["utc"].isoformat(), close=close, atr14=atr,
                range_high=range_high, range_low=range_low, range_pct=round(range_pct, 4),
                volume_pct=None if vol_pct is None else round(vol_pct, 4),
                ret5=ret5, ret20=ret20, sma20=sma), []


def _stop_band(trigger, stop, atr):
    dist = trigger - stop
    if dist <= 0 or atr <= 0:
        return "stop_not_below_trigger"
    if dist < EXISTING_STOP_LO * atr or dist > EXISTING_STOP_HI * atr:
        return "stop_outside_existing_band"
    return None


def _watch_from_measure(ticker, stats, hypothesis, fixture=False):
    """A watch only when this explicit cutoff fits. The stop is not resized."""
    if not isinstance(hypothesis, dict) or hypothesis.get("proven_edge") is True:
        return None, "hypothesis_cutoff_not_supplied"
    range_max, vol_max = _num(hypothesis.get("range_percentile_max")), _num(hypothesis.get("volume_percentile_max"))
    if range_max is None or vol_max is None:
        return None, "hypothesis_cutoff_not_supplied"
    if stats.get("volume_pct") is None:
        return None, "volume"
    chase = chase_long(stats)
    if chase is None:
        return None, "chase_inputs"
    if chase:
        return None, "existing_chase_rule"
    band = _stop_band(stats["range_high"], stats["range_low"], stats["atr14"])
    if band:
        return None, band
    if stats["range_pct"] > range_max or stats["volume_pct"] > vol_max:
        return None, "outside_hypothesis_cutoff"
    return dict(ticker=ticker, side="LONG", paper_only=True, confirm=False, armable=True,
                proven_edge=False, hypothesis_id=hypothesis.get("id") or HYPOTHESIS_ID,
                fixture=bool(fixture), asof=stats["asof"], trigger=stats["range_high"],
                entry=stats["range_high"], stop=stats["range_low"], invalidation=stats["range_low"],
                target=stats["range_high"] + 2 * (stats["range_high"] - stats["range_low"]),
                evidence=dict(range_pct=stats["range_pct"], volume_pct=stats["volume_pct"],
                              atr14=stats["atr14"], ret5=stats["ret5"], ret20=stats["ret20"],
                              maker=CT.CM_KRAKEN["entry"], taker=CT.CM_KRAKEN["exit"]),
                tests=["own_history_percentile", "existing_chase_rule", "existing_stop_band"]), None


def scan(rows, hypothesis=None):
    """No watch unless the row qualifies. Near-misses name the missing or failed condition."""
    watches, near, rejected = [], [], []
    for row in rows or []:
        if not isinstance(row, dict):
            near.append(dict(reason="row_not_an_object", missing=["object"]))
            continue
        ticker = row.get("ticker") or row.get("sym")
        if str(row.get("side") or "LONG").upper() == "SHORT" or row.get("shorts"):
            rejected.append(dict(ticker=ticker, reason="hunt48_shorts_off"))
            continue
        bars, problems = _ordered(row.get("bars"))
        if problems:
            near.append(dict(ticker=ticker, reason=problems[0], missing=problems, asof=row.get("asof")))
            continue
        window = int(hypothesis.get("range_window") or 10) if isinstance(hypothesis, dict) else 10
        lookback = int(hypothesis.get("lookback") or 20) if isinstance(hypothesis, dict) else 20
        stats, missing = measure(bars, len(bars) - 1, window, lookback)
        if stats is None:
            near.append(dict(ticker=ticker, reason="insufficient_history", missing=missing,
                             asof=bars[-1]["utc"].isoformat() if bars else None))
            continue
        watch, why = _watch_from_measure(ticker, stats, hypothesis, fixture=row.get("fixture"))
        item = dict(ticker=ticker, asof=stats["asof"], trigger=stats["range_high"], stop=stats["range_low"],
                    range_pct=stats["range_pct"], volume_pct=stats["volume_pct"], reason=why or "watch")
        if watch:
            watches.append(watch)
        else:
            near.append(item)
    near.sort(key=lambda item: ((item.get("range_pct") if item.get("range_pct") is not None else 9),
                                len(item.get("missing") or [])))
    return dict(hypothesis_id=HYPOTHESIS_ID, proven_edge=False, reviews=reviews_supplied(),
                watches=watches, near_misses=near, rejected=rejected, shorts_off=SHORTS_OFF)


def apply_reply(reply, rows, hypothesis=None):
    """Attach the report. Do not turn a near-miss into an order."""
    out = dict(reply or {})
    if str(out.get("side") or "").upper() == "SHORT":
        out["side"] = None
        out.setdefault("crypto_rejected", []).append("hunt48_shorts_off")
    out["crypto_prepump"] = scan(rows, hypothesis)
    out["crypto_contract"] = seat_contract(out)
    out["confirm"] = False if out.get("action") == "stand_aside" else out.get("confirm")
    return out


def seat_contract(reply):
    report = reply.get("crypto_prepump") if isinstance(reply, dict) else None
    if not isinstance(report, dict):
        return dict(ok=False, reason="no_prepump_report")
    for watch in report.get("watches") or []:
        if watch.get("proven_edge") or watch.get("confirm") or str(watch.get("side") or "") == "SHORT":
            return dict(ok=False, reason="watch_breaks_rails")
        for key in ("ticker", "asof", "trigger", "stop", "invalidation", "evidence"):
            if watch.get(key) in (None, "", []):
                return dict(ok=False, reason="watch_missing_%s" % key)
    return dict(ok=True, watches=len(report.get("watches") or []),
                near_misses=len(report.get("near_misses") or []),
                rejected=len(report.get("rejected") or []))


def _trade(bars, index, stats, slippage_bps):
    """Enter on the next bar only. A gap through the trigger is not taken."""
    if index + 1 >= len(bars):
        return None
    nxt = bars[index + 1]
    trigger, stop = stats["range_high"], stats["range_low"]
    if nxt["open"] > trigger or nxt["high"] < trigger:
        return dict(taken=False, reason="no_fill_or_gap")
    slip = slippage_bps / 10000.0
    entry = trigger * (1 + slip)
    target = entry + 2 * (entry - stop)
    fee = CT.CM_KRAKEN["exit"]
    for bar in bars[index + 1:]:
        if bar["low"] <= stop:
            exit_px = stop * (1 - slip)
            gross = exit_px - entry
            net = gross - entry * fee - exit_px * fee
            return dict(taken=True, false_positive=True, net=net, entry_index=index + 1, signal_index=index)
        if bar["high"] >= target:
            exit_px = target * (1 - slip)
            gross = exit_px - entry
            net = gross - entry * fee - exit_px * fee
            return dict(taken=True, false_positive=False, net=net, entry_index=index + 1, signal_index=index)
    return dict(taken=False, reason="neither_stop_nor_target")


def walk_forward(bars, grid, range_window=10, lookback=20, holdout_frac=0.3, slippage_bps=0.0):
    """Choose a cutoff on the earlier bars only, then score that cutoff once on the later bars."""
    rows, problems = _ordered(bars)
    if problems:
        return dict(ok=False, missing=problems, proven_edge=False)
    split = int(len(rows) * (1 - holdout_frac))
    if split < range_window + lookback + 2 or len(rows) - split < 2:
        return dict(ok=False, missing=["series_too_short"], proven_edge=False)

    def run(start, end, cutoff):
        trades = []
        for index in range(start, end):
            stats, _missing = measure(rows, index, range_window, lookback)
            if not stats or chase_long(stats) is not False or _stop_band(stats["range_high"], stats["range_low"], stats["atr14"]):
                continue
            if stats["range_pct"] > cutoff[0] or stats.get("volume_pct") is None or stats["volume_pct"] > cutoff[1]:
                continue
            if index + 1 >= end and end != len(rows):
                continue
            done = _trade(rows, index, stats, slippage_bps)
            if done and done.get("taken") and done["entry_index"] < end:
                trades.append(done)
        nets = [item["net"] for item in trades]
        return dict(n=len(trades), false_positives=sum(1 for item in trades if item["false_positive"]),
                    expectancy_net=(sum(nets) / len(nets) if nets else None),
                    lookahead_ok=all(item["signal_index"] < item["entry_index"] for item in trades))

    train_end = split
    best, best_score = None, None
    for cutoff in grid or []:
        if len(cutoff) != 2:
            continue
        scored = run(range_window + lookback, train_end, cutoff)
        score = scored["expectancy_net"]
        if score is None:
            continue
        if best_score is None or score > best_score:
            best, best_score = cutoff, score
    holdout = None
    if best is not None:
        holdout = run(train_end, len(rows), best)
    return dict(ok=True, proven_edge=False, survivorship_not_assessed=True,
                universe="caller_supplied_series_only", entry_rule="next_bar_at_trigger_else_skip_gap",
                same_bar_stop_and_target="stop_first", holdout_used_for_selection=False,
                fees=dict(maker=CT.CM_KRAKEN["entry"], taker=CT.CM_KRAKEN["exit"], charged="taker_both_legs"),
                slippage_bps=slippage_bps, selected_cutoff=best, selected_on="train" if best else None,
                train_expectancy=best_score, holdout=holdout, reviews=reviews_supplied(),
                search_grid_not_desk_law=list(grid or []))
