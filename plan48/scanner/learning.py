"""Closed hunt trades for the weekly self-tune. Writes plan48/scanner/closed_trades.jsonl.

Shadow ranks 4-10 are marked on a time exit, with ticker and entry. Attribution is advisory and does not change a rail.
"""
import datetime as dt, json, os
UTC = dt.timezone.utc
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT = os.path.join(ROOT, "plan48", "scanner", "closed_trades.jsonl")

def closed_path():
    return os.environ.get("TFO_HUNT_CLOSED") or DEFAULT

def _read(path):
    rows = []
    try:
        for ln in open(path):
            ln = ln.strip()
            if ln:
                try: rows.append(json.loads(ln))
                except Exception: continue
    except Exception: return []
    return rows

def _pnl(trade):
    if trade.get("pnl_aud") is not None:
        try: return float(trade["pnl_aud"]), "pnl_aud"
        except (TypeError, ValueError): return None, None
    if trade.get("realised_usd") is not None:
        try: return float(trade["realised_usd"]), "realised_usd"
        except (TypeError, ValueError): return None, None
    return None, None

def _signals(trade):
    if trade.get("signals"): return list(trade["signals"])
    cands = trade.get("catalyst_candidates") or []
    names = [c.get("setup") for c in cands if isinstance(c, dict) and c.get("setup")]
    return names

def shadow_marks(ledger):
    """Ranks 4 through 10 only. Entry is null when the shadow row did not carry one."""
    out = []
    seen = set()
    pools = list((ledger or {}).get("trades") or []) + list((ledger or {}).get("shadow") or [])
    for t in pools:
        if not isinstance(t, dict) or t.get("shadow_rank") is None: continue
        try: rk = int(t["shadow_rank"])
        except (TypeError, ValueError): continue
        if rk < 4 or rk > 10 or rk in seen: continue
        entry = t.get("entry") if t.get("entry") is not None else t.get("shadow_entry")
        out.append(dict(shadow_rank=rk, ticker=t.get("ticker"), entry=entry, entry_missing=entry is None))
        seen.add(rk)
    return sorted(out, key=lambda r: r["shadow_rank"])

def _time_exit(trade, events):
    ev = [str(x) for x in (events or [])]
    if "time_exit" in ev: return True
    reason = str(trade.get("exit_reason") or "")
    return reason.lower().startswith("time exit")

def record_close(trade, ledger=None, now=None, events=None, path=None):
    """Append one closed hunt trade. Does not mutate the trade. Skips under pytest unless TFO_HUNT_CLOSED is set."""
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("TFO_HUNT_CLOSED") and not path:
        return dict(ok=True, skipped="pytest", blocks_trade=False)
    now = now or dt.datetime.now(UTC)
    path = path or closed_path()
    pnl, pnl_field = _pnl(trade)
    exits = trade.get("exits") or []
    exit_px = exits[-1].get("px") if exits and isinstance(exits[-1], dict) else trade.get("exit")
    key = "%s|%s|%s|%s" % (trade.get("id"), trade.get("book") or "", trade.get("exit_reason"), exit_px)
    prior = _read(path)
    if any(r.get("key") == key for r in prior): return dict(ok=True, duplicate=True, blocks_trade=False)
    row = dict(key=key, id=trade.get("id"), ticker=trade.get("ticker"), entry=trade.get("entry"), exit=exit_px,
               status=trade.get("status"), exit_reason=trade.get("exit_reason"), book=trade.get("book"),
               seat=trade.get("seat") or "hunt", direction=trade.get("direction"), closed_utc=now.isoformat() if not isinstance(now, str) else now,
               signals=_signals(trade), pnl=pnl, pnl_field=pnl_field, advisory=True, blocks_trade=False, paper_only=True)
    if pnl is None: row["pnl_missing"] = True
    if _time_exit(trade, events): row["shadow_ranks_4_10"] = shadow_marks(ledger or {})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f: f.write(json.dumps(row, default=str) + "\n")
    return dict(ok=True, wrote=path, blocks_trade=False)

def attribute(rows):
    """Which signals sat on winners and losers. A trade with no pnl is counted as unknown, not as a loss."""
    out = {}
    for r in rows or []:
        seat = r.get("seat") or "hunt"
        pnl = r.get("pnl")
        if pnl is None: bucket = "unknown"
        elif pnl > 0: bucket = "winners"
        else: bucket = "losers"
        signals = r.get("signals") or ["(no signal recorded)"]
        seat_d = out.setdefault(seat, {})
        for s in signals:
            cell = seat_d.setdefault(s, dict(winners=0, losers=0, unknown=0, net_pnl=None))
            cell[bucket] += 1
            if pnl is not None: cell["net_pnl"] = round((cell["net_pnl"] or 0) + pnl, 2)
    return out

def weekly(path=None):
    """Read closed_trades.jsonl. Proposals are filters only and are not applied."""
    path = path or closed_path()
    rows = _read(path)
    attr = attribute(rows)
    proposals = []
    for seat, signals in attr.items():
        for name, cell in signals.items():
            if name.startswith("("): continue
            n = cell["winners"] + cell["losers"]
            if n < 5 or cell["losers"] <= cell["winners"]: continue
            proposals.append(dict(seat=seat, kind="skip_signal", value=name, text="Skip %s on %s (advisory filter)" % (name, seat),
                                  winners=cell["winners"], losers=cell["losers"], status="pending_tfo_approval",
                                  auto_apply=False, blocks_trade=False))
    return dict(generated_utc=dt.datetime.now(UTC).isoformat(), paper_only=True, advisory=True, auto_apply=False, blocks_trade=False,
                source=path, closed_trades=len(rows), attribution=attr,
                shadow_marked=sum(1 for r in rows if r.get("shadow_ranks_4_10")), proposals=proposals,
                note="Reads plan48/scanner/closed_trades.jsonl. Filters only. Rails unchanged. Nothing is applied.")

def hunt_summary(path=None):
    """Read-only view for the seat learning weekly. Does not write."""
    path = path or closed_path()
    rows = _read(path)
    return dict(advisory=True, blocks_trade=False, auto_apply=False, paper_only=True, source=path, closed_trades=len(rows),
                attribution=attribute(rows), shadow_marked=sum(1 for r in rows if r.get("shadow_ranks_4_10")))
