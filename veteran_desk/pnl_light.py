"""tfo_pnllight_20261101 (Brett, PAPER ONLY): one green/red/grey profit light per seat/desk.
green = profit, red = loss, grey = flat / not traded. Source = the seat's OWN ledger P&L, net of costs
(book.seat_view: cash already net of every entry/exit cost). Read-only: never writes a ledger."""
import json, os
EPS = 0.5   # A$: |P&L| below this counts as flat
COL = dict(green="#2ea043", red="#f85149", grey="#6e7681")
EMOJI = dict(green="🟢", red="🔴", grey="⚪")
def light(pnl, traded=True):
    """pnl in A$ (None = no figure). Not traded and ~0 -> grey."""
    try: p = float(pnl)
    except (TypeError, ValueError): return "grey"
    if p != p or abs(p) < EPS: return "grey"
    if not traded and abs(p) < EPS: return "grey"
    return "green" if p > 0 else "red"
def _traded(B):
    pos = B.get("positions") or {}
    vals = pos.values() if isinstance(pos, dict) else pos
    return bool(B.get("closed")) or any(isinstance(p, dict) and p.get("status") in ("open", "closed", "filled") for p in vals)
def seat_light(B):
    """B = one seat dict from a book ledger. Returns light + the figures it is based on."""
    B = B or {}
    try:
        from veteran_desk import book as BK; v = BK.seat_view(B)
    except Exception:
        v = dict(equity_aud=B.get("equity_aud"), today_pnl_aud=B.get("today_pnl_aud"), pnl_since_inception_aud=B.get("pnl_since_inception_aud"), costs_aud=None)
    tr = _traded(B); since = v.get("pnl_since_inception_aud"); lt = light(since, tr)
    if not tr and lt == "grey": why = "not traded yet"
    else: why = {"green": "in profit since start (net of costs)", "red": "in loss since start (net of costs)", "grey": "flat since start"}[lt]
    return dict(pnl_light=lt, pnl_light_why=why, pnl_since_aud=since, pnl_today_aud=v.get("today_pnl_aud"), equity_aud=v.get("equity_aud"), costs_aud=v.get("costs_aud"), traded=tr, net_of_costs=True)
def _a(x):
    try: x = float(x)
    except (TypeError, ValueError): return "n/a"
    return ("-" if x < 0 else "+") + "A$" + format(abs(round(x)), ",")
RED_PUSH = ("RED LIGHT PUSH: your seat is in loss. Hunt hard for A/A+ setups this wake, within every rule. "
            "Never loosen a gate, never chase, never size up or revenge-trade to win it back; every stop and cap stays. Standing aside beats a forced trade.")
def prompt_line(seat_name, book_path=None):
    """First line of a seat's wake prompt: its own light + P&L. '' if the ledger cannot be read (never blocks a wake)."""
    try:
        live = book_path is None
        if book_path is None:
            from veteran_desk import book as BK; book_path = BK.BOOK
        B = ((json.load(open(book_path)).get("seats") or {}).get(seat_name))
        if B is None: return ""
        L = seat_light(B); lt = L["pnl_light"]; td = f"today {_a(L['pnl_today_aud'])}"
        if live:   # seatagents_20261103 16:40 (dayclock_20261103, Brett 16:01 "Figures are not right"): 'today' = the dashboard's 06:00 WITA clock; the book's midnight day is labelled
            try:
                from veteran_desk import day_clock as DC; _d, _bd, _c = DC.day_of(seat_name)
                if _d is not None and _c == "06:00": td = f"today {_a(_d)} since 06:00 WITA (the dashboard/leaderboard clock; book day since 00:00 WITA {_a(_bd)} = risk rails)"
            except Exception: pass
        s = (f"YOUR PROFIT LIGHT: {EMOJI[lt]} {lt.upper()} ({L['pnl_light_why']}). Own ledger, net of costs: since start {_a(L['pnl_since_aud'])}, "
             f"{td}, equity {_a(L['equity_aud']).lstrip('+')}.\n")
        return s + (RED_PUSH + "\n" if lt == "red" else "")
    except Exception:
        return ""
