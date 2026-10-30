"""mobile_api.py -- read-only aggregate for the phone page /m and the War Room 'Desk status' panel (PAPER ONLY).

Added 2026-10-09 (dashboard audit). Served at /api/m by command_server.py (module reloaded per request, so edits
here need no server restart). Every figure is read straight from its source file, never from a cached digest:

  main book    reports/firm_risk.json (gross equity, peak, drawdown) + funder_fee.summary() (fee to date, net equity,
               today's change since the 09:00 Sydney fee boundary)
  trend18      state/trend18.json
  parallel     state/parallel_desk/ledger.json + trades.jsonl (parallel_desk.score: the $1,000/day scoreboard)
               + state/behaviour/dd_halt.json (same latch research/behaviour/dd_halt.py status reports)
  oanda        state/oanda/snapshot.json (practice account, AUD, not desk equity)
  entry engine state/entry_engine/latest.json + alerts.jsonl
  positions    reports/fundamental_book.json + reports/ht_equity_rails_latest.json (stop = rail level),
               trend18 holdings, parallel open clips, OANDA TFO trades
  health       EODHD (state/parallel_desk/us_stream_status.json), Kraken (parallel rails heartbeat + entry engine scan),
               OANDA (snapshot age), IBKR (LIVE TCP connect to the Mac gateway 127.0.0.1:4002, launchd com.tfo.ibgateway;
               connect only, no API session, no clientId used), plus source-file ages.

Rules: reads files, opens one TCP socket to localhost:4002 and closes it. Never writes, never orders.
Dollar results are reported against the desk's own history only.
"""
import datetime as dt
import json
import os
import socket
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
UTC = dt.timezone.utc
try:
    from zoneinfo import ZoneInfo
    SYD = ZoneInfo("Australia/Sydney")
except Exception:  # pragma: no cover
    SYD = dt.timezone(dt.timedelta(hours=11))

IBKR_HOST, IBKR_PORT = "127.0.0.1", 4002
_CACHE = {"ts": 0.0, "data": None}
TTL_S = 10.0


def _p(*a):
    return os.path.join(BASE, *a)


def _load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {} if default is None else default


def _jsonl(path, n=50):
    out = []
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 300_000))
            for ln in f.read().decode("utf-8", "replace").splitlines()[-n:]:
                try:
                    out.append(json.loads(ln))
                except Exception:
                    pass
    except OSError:
        pass
    return out


def _t(v):
    if v in (None, ""):
        return None
    try:
        if isinstance(v, (int, float)):
            return dt.datetime.fromtimestamp(float(v), UTC)
        s = str(v).strip().replace("Z", "+00:00")
        t = dt.datetime.fromisoformat(s)
        return t if t.tzinfo else t.replace(tzinfo=UTC)
    except Exception:
        return None


def _f(x, d=None):
    try:
        v = float(x)
        return v if v == v else d
    except (TypeError, ValueError):
        return d


def _syd(t):
    return t.astimezone(SYD).strftime("%a %d %b %H:%M") if t else None


def _age(t, now):
    return None if t is None else round((now - t).total_seconds())


def _fresh(asof, now, max_age_s, label, source, note=""):
    t = _t(asof)
    age = _age(t, now)
    status = "UNKNOWN" if age is None else ("OK" if age <= max_age_s else "STALE")
    return {"label": label, "source": source, "asof_utc": t.isoformat() if t else None, "asof_syd": _syd(t),
            "age_s": age, "max_age_s": max_age_s, "status": status, "note": note}


def _mtime(path):
    try:
        return dt.datetime.fromtimestamp(os.path.getmtime(path), UTC)
    except OSError:
        return None


# ------------------------------------------------------------------------------------------------ blocks
def main_book(now):
    fr = _load(_p("reports", "firm_risk.json"))
    try:
        import funder_fee
        fee = funder_fee.summary()
    except Exception as exc:
        fee = {"status": "UNAVAILABLE", "error": str(exc)[:160]}
    gross = _f(fr.get("firm_equity_usd"))
    peak = _f(fr.get("firm_peak_usd"))
    cum_fee = _f(fee.get("cum_fee_paid_usd"))
    net = _f(fee.get("equity_net_usd"))
    if net is None and gross is not None and cum_fee is not None:
        net = round(gross - cum_fee, 2)
    last = fee.get("last_day") or {}
    net_0900 = _f(last.get("equity_net_after_usd"))
    today_chg = None if (net is None or net_0900 is None) else round(net - net_0900, 2)
    # health fix 2026-10-28 20:4x WITA: net - net_0900 counted today's deposits/transfers as profit
    # (+$3.5M after the paper top-ups). Use funder_fee's flow-free day P&L since 09:00 (gross less fees paid since).
    _day = fee.get("day") or {}
    if _f(_day.get("pnl_gross_usd")) is not None:
        today_chg = round(_f(_day.get("pnl_gross_usd")) - (_f(_day.get("fees_paid_usd")) or 0.0), 2)
    lvl = fr.get("level") or "?"
    badge = "HALTED" if fr.get("new_entries_halted") or lvl.upper().startswith("HALT") else ("KILL-WATCH" if lvl.upper() not in ("OK", "?") else "OK")
    return {
        "equity_gross_usd": gross, "equity_net_usd": net, "cum_fee_usd": cum_fee,
        "fee_today_usd": _f((fee.get("today") or {}).get("fee_paid_usd")),
        "next_fee_usd": _f((fee.get("today") or {}).get("next_fee_projected_usd")),
        "next_fee_syd": (fee.get("today") or {}).get("next_accrual_sydney"),
        "today_change_usd": today_chg, "today_since": "09:00 Sydney fee boundary (after today's fee)",
        "net_at_0900_usd": net_0900,
        "peak_usd": peak, "dd_usd": None if (gross is None or peak is None) else round(gross - peak, 2),
        "dd_pct": _f(fr.get("firm_drawdown_pct")), "peak_at_utc": fr.get("firm_peak_at_utc"),
        "level": lvl, "badge": badge, "asof_utc": fr.get("utc"), "asof_syd": _syd(_t(fr.get("utc"))),
        "fee_status": fee.get("status"),
    }


def trend18(now):
    d = _load(_p("state", "trend18.json"))
    eq, start = _f(d.get("equity")), _f(d.get("start"))
    kill = d.get("kill") or {}
    dd = _f(d.get("drawdown_pct"))
    badge = "HALTED" if kill.get("active") else ("KILL-WATCH" if (dd is not None and dd <= -20.0) or kill.get("pending_rebalance") else "OK")
    pos = []
    for sym, q in (d.get("holdings") or {}).items():
        e, m = _f((d.get("entry_px") or {}).get(sym)), _f((d.get("mark_px_usd") or {}).get(sym))
        q = _f(q, 0)
        pos.append({"book": "Trend18", "sym": sym.replace("XBT/USD", "BTC"), "pnl_usd": None if e is None or m is None else round(q * (m - e), 2),
                    "stop": None, "stop_dist_pct": None, "stop_note": "no stop rail; kill at NAV -30%"})
    return {"equity_usd": eq, "start_usd": start, "pnl_usd": None if eq is None or start is None else round(eq - start, 2),
            "dd_pct": dd, "kill_active": bool(kill.get("active")), "badge": badge, "asof_utc": d.get("updated"),
            "asof_syd": _syd(_t(d.get("updated"))), "positions": pos}


def parallel(now):
    led = _load(_p("state", "parallel_desk", "ledger.json"))
    hb = _load(_p("state", "parallel_desk", "heartbeat.json"))
    dd = _load(_p("state", "behaviour", "dd_halt.json"))
    sc = {}
    try:
        pdir = _p("research", "parallel_desk")
        if pdir not in sys.path:
            sys.path.insert(0, pdir)
        import parallel_desk as PD
        sc = PD.score(PD.read_jsonl(_p("state", "parallel_desk", "trades.jsonl")), PD.syd_date(now))
    except Exception as exc:
        sc = {"error": str(exc)[:160]}
    latched = None
    thr = _f(dd.get("threshold"), -15000.0)
    try:
        bdir = _p("research", "behaviour")
        if bdir not in sys.path:
            sys.path.insert(0, bdir)
        import behaviour_check as BC
        import behaviour_hooks as BH
        st = BH.load_dd_halt(BASE)
        latched = bool(BC.dd_halt_latched(st))
        thr = _f(BC.CONFIG.get("dd_halt_threshold_usd"), thr)
        try: thr = _f(BC.dd_halt_effective_threshold(st, thr), thr)   # 2026-10-30 dashfix: honour re-arm reference
        except Exception: pass
    except Exception:
        latched = (dd.get("status") == "tripped" and not dd.get("cleared_at"))
    booked = _f(led.get("booked_pnl_usd"))
    headroom = None if booked is None else round(booked - thr, 2)
    badge = "HALTED" if latched else ("KILL-WATCH" if headroom is not None and headroom < 5000 else "OK")
    pos = []
    ee = _load(_p("state", "entry_engine", "latest.json"))
    live = {m.get("id"): _f(m.get("live_px")) for m in (ee.get("monitor") or []) if m.get("id")}
    for cid, c in (led.get("open") or {}).items():
        e, q = _f(c.get("entry_px")), _f(c.get("qty_open"), 0)
        stop = _f((c.get("rails") or {}).get("stop"))
        px = live.get(cid)
        pos.append({"book": "Parallel", "sym": c.get("canon") or c.get("pair"), "pnl_usd": None if px is None or e is None else round(q * (px - e), 2),
                    "stop": stop, "stop_dist_pct": None if px is None or not stop else round((px - stop) / px * 100, 2),
                    "stop_note": "desk rail" + ("" if px is not None else "; live px n/a"), "px": px})
    return {"seed_usd": _f(led.get("seed_usd")), "cash_usd": _f(led.get("cash_usd")), "booked_since_seed_usd": booked,
            "open_clips": len(led.get("open") or {}), "halt_latched": latched, "halt_threshold_usd": thr, "halt_headroom_usd": headroom,
            "halt_tripped_at_utc": dd.get("tripped_at"), "badge": badge,
            "today": {"syd_day": sc.get("syd_day"), "booked_usd": _f(sc.get("booked")), "exits": sc.get("exits"),
                      "base_target_usd": 1000.0, "target_with_carry_usd": _f(sc.get("target")), "carry_usd": _f(sc.get("carry")),
                      "result": sc.get("result"), "cum_booked_usd": _f(sc.get("cum_booked")), "cum_target_usd": _f(sc.get("cum_target")),
                      "days": sc.get("days"), "error": sc.get("error")},
            "ledger_updated_utc": led.get("updated_utc"), "heartbeat_utc": hb.get("last_run_utc"), "heartbeat_status": hb.get("status"),
            "positions": pos}


def oanda(now):
    s = _load(_p("state", "oanda", "snapshot.json"))
    nav, start = _f(s.get("nav")), _f(s.get("start_nav_aud"))
    trades = s.get("trades") or []
    audusd = _f(_load(_p("state", "entry_engine", "latest.json")).get("audusd"))
    audjpy = None
    for t in trades:
        if t.get("instrument") == "AUD_JPY" and _f(t.get("units")):
            u, e, upl = _f(t["units"]), _f(t["price"]), _f(t.get("upl_aud"), 0)
            if 1 - upl / u:
                audjpy = e / (1 - upl / u)
    pos = []
    for t in trades:
        if not t.get("tfo"):
            continue
        inst, u, e, upl, stop = t.get("instrument", ""), _f(t.get("units"), 0), _f(t.get("price")), _f(t.get("upl_aud")), _f(t.get("stop"))
        px = None
        quote = inst.split("_")[-1]
        try:
            if inst == "AUD_JPY":
                px = audjpy
            elif quote == "JPY" and audjpy:
                px = e + upl * audjpy / u
            elif quote == "USD" and audusd:
                px = e + upl * audusd / u
            elif quote == "AUD":
                px = e + upl / u
        except Exception:
            px = None
        dist = None
        if px and stop:
            dist = round(((px - stop) / px if u > 0 else (stop - px) / px) * 100, 2)
        pos.append({"book": "OANDA", "sym": inst.replace("_", "/"), "pnl_aud": upl, "pnl_usd": None if upl is None or not audusd else round(upl * audusd, 2),
                    "stop": stop, "stop_dist_pct": dist, "stop_note": "broker stop" + ("" if px else "; px n/a"), "px": None if px is None else round(px, 5)})
    badge = "HALTED" if s.get("kill") else "OK"
    try:   # 25 Oct 2026: reconcile failing -> UNVERIFIED badge; stale marks not counted
        import oanda_reconcile_guard as _RG; rg = _RG.board(_p("state", "oanda"))
    except Exception: rg = {"verified": False, "book_label": "UNVERIFIED", "reconcile_status": "UNKNOWN", "entries_blocked": False}
    if not rg.get("verified"):
        badge = "UNVERIFIED" + (" · ENTRIES BLOCKED" if rg.get("entries_blocked") else "")
        pos = [dict(p_, pnl_aud=None, pnl_usd=None, stop_note=(p_.get("stop_note") or "") + "; UNVERIFIED (reconcile failing)") for p_ in pos]
        nav = None
    return {"verified": bool(rg.get("verified")), "reconcile": rg, "nav_aud": nav, "start_nav_aud": start, "pnl_aud": None if nav is None or start is None else round(nav - start, 2),
            "pnl_usd": None if nav is None or start is None or not audusd else round((nav - start) * audusd, 2), "audusd": audusd,
            "tfo_open": s.get("tfo_open"), "tfo_upl_aud": _f(s.get("tfo_upl_aud")), "badge": badge, "asof_utc": s.get("asof_utc"),
            "asof_syd": _syd(_t(s.get("asof_utc"))), "in_desk_equity": False, "positions": pos}


def main_positions():
    fb = _load(_p("reports", "fundamental_book.json"))
    rails = {r.get("sym"): r for r in (_load(_p("reports", "ht_equity_rails_latest.json")).get("rows") or [])}
    out = []
    for sym, p in (fb.get("positions") or {}).items():
        r = rails.get(sym) or {}
        px = _f(r.get("px_usd")) or _f(p.get("mark_usd"))
        stop = _f(r.get("rail_level_usd"))
        side = p.get("side", "long")
        dist = None
        if px and stop:
            dist = round(((px - stop) / px if side == "long" else (stop - px) / px) * 100, 2)
        out.append({"book": "Main V1", "sym": sym, "pnl_usd": _f(p.get("unrealised_usd")), "stop": stop, "stop_dist_pct": dist,
                    "stop_note": "rail level" + (" (half banked)" if r.get("half_banked") else ""), "status": r.get("status"), "px": px})
    return out


def entry_engine(now):
    d = _load(_p("state", "entry_engine", "latest.json"))
    pick = d.get("pick")
    pk = None
    if pick:
        if isinstance(pick, dict):
            pk = {"name": pick.get("name") or pick.get("symbol"), "score": pick.get("score"), "grade": pick.get("grade") or (pick.get("ticket") or {}).get("grade"),
                  "text": pick.get("text") or pick.get("why") or ""}
        else:
            pk = {"name": str(pick)}
    al = _jsonl(_p("state", "entry_engine", "alerts.jsonl"), 50)[-3:]
    alerts = []
    for a in reversed(al):
        tk = a.get("ticket") or {}
        alerts.append({"utc": a.get("utc"), "syd": _syd(_t(a.get("utc"))), "name": a.get("name"), "book": a.get("book"),
                       "event": a.get("event") or a.get("type"), "grade": tk.get("grade") or a.get("grade"), "text": a.get("text")})
    af = _load(_p("state", "entry_engine", "fired_latest.json"))  # 2026-10-09 auto_fire.py one-line status (PAPER)
    af_state = "OFF" if os.path.exists(_p("state", "entry_engine", "AUTO_FIRE_OFF")) else (af.get("state") or "ON (no run yet)")
    af_line = "Auto-fire: %s, %s today, last action %s" % (af_state, af.get("fired_today", 0), af.get("last_action") or "none yet")
    return {"scan_utc": d.get("utc"), "scan_syd": _syd(_t(d.get("utc"))), "status": d.get("status"), "pick": pk,
            "no_pick_reason": None if pk else "No name scored 55 or higher", "alerts": alerts, "alerts_total": len(_jsonl(_p("state", "entry_engine", "alerts.jsonl"), 100000)),
            "auto_fire": {"line": af_line, "state": af_state, "fired_today": af.get("fired_today", 0), "last_action": af.get("last_action"),
                          "last_fire": af.get("last_fire"), "asof_syd": af.get("syd")}}


def ibkr_probe():
    t0 = time.time()
    try:
        with socket.create_connection((IBKR_HOST, IBKR_PORT), timeout=1.5):
            pass
        return {"status": "UP", "ok": True, "latency_ms": round((time.time() - t0) * 1000, 1),
                "detail": "Mac gateway %s:%d accepting connections (live TCP probe; launchd com.tfo.ibgateway)" % (IBKR_HOST, IBKR_PORT)}
    except Exception as exc:
        return {"status": "DOWN", "ok": False, "latency_ms": None,
                "detail": "Mac gateway %s:%d not accepting: %s" % (IBKR_HOST, IBKR_PORT, str(exc)[:80])}


def health(now, mb, t18, pdk, oa, ee):
    feeds = []
    us = _load(_p("state", "parallel_desk", "us_stream_status.json"))
    row = _fresh(us.get("last_msg_utc") or us.get("utc"), now, 600, "EODHD", "us_streamer -> state/parallel_desk/us_stream_status.json",
                 "quote ws %s · trade ws %s · %s" % (us.get("quote_ws"), us.get("trade_ws"), us.get("phase") or ""))
    if row["status"] == "OK" and (us.get("quote_ws") != "up" or us.get("trade_ws") != "up"):
        row["status"] = "DOWN"
    feeds.append(row)
    rh = _load(_p("state", "parallel_desk", "rails_heartbeat.json"))
    row = _fresh(rh.get("last_run_utc"), now, int(_f(rh.get("stale_after_s"), 180)), "Kraken", "parallel rails (60 s) + entry engine scan",
                 "rails %s · scan %s min ago · rate-limited %s" % (rh.get("status"), (_age(_t(ee.get("scan_utc")), now) or 0) // 60,
                                                                     _load(_p("state", "entry_engine", "latest.json")).get("kraken_rate_limited")))
    sa = _age(_t(ee.get("scan_utc")), now)
    if row["status"] == "OK" and (rh.get("status") != "OK" or sa is None or sa > 900):
        row["status"] = "STALE"
    feeds.append(row)
    feeds.append(_fresh(oa.get("asof_utc"), now, 35 * 60, "OANDA", "oanda_sleeve (15 min) -> state/oanda/snapshot.json", "practice account · A$"))
    ib = ibkr_probe()
    feeds.append({"label": "IBKR", "source": "live TCP connect %s:%d (Mac)" % (IBKR_HOST, IBKR_PORT), "asof_utc": now.isoformat(),
                  "asof_syd": _syd(now), "age_s": 0, "max_age_s": None, "status": "OK" if ib["ok"] else "DOWN",
                  "note": ib["detail"] + ("" if ib["latency_ms"] is None else " · %s ms" % ib["latency_ms"])})
    files = [
        _fresh(mb.get("asof_utc"), now, 75 * 60, "Firm equity / drawdown", "reports/firm_risk.json (hourly desk tick)"),
        _fresh(t18.get("asof_utc"), now, 26 * 3600, "Trend18 mark", "state/trend18.json"),
        _fresh(pdk.get("heartbeat_utc"), now, 30 * 60, "Parallel desk run", "state/parallel_desk/heartbeat.json (15 min)"),
        _fresh(ee.get("scan_utc"), now, 15 * 60, "Entry engine scan", "state/entry_engine/latest.json (5 min)"),
    ]
    sc = _load(_p("state", "desk_driver", "scorecard.json"))
    scr = _fresh(_mtime(_p("state", "desk_driver", "scorecard.json")), now, 25 * 3600, "Desk-driver scorecard", "state/desk_driver/scorecard.json (daily 20:41 note)",
                 "written %s; its figures are a snapshot, the tiles above are live" % (sc.get("ts_aedt") or "?"))
    cardv = _f((sc.get("scorecard") or {}).get("parallel_desk_cum_booked_usd"))
    ledv = _f(pdk.get("booked_since_seed_usd"))
    if cardv is not None and ledv is not None and abs(cardv - ledv) > 1.0:
        scr["status"] = "STALE"
        scr["note"] = "written %s; its parallel-desk total $%s no longer matches the ledger $%s (live tiles use the ledger)" % (
            sc.get("ts_aedt") or "?", format(cardv, ",.2f"), format(ledv, ",.2f"))
    files.append(scr)
    ex = _fresh(_load(_p("state", "execution", "summary.json")).get("updated_utc"), now, 26 * 3600, "Execution summary", "state/execution/summary.json",
                "its IBKR 'gateway down on box' text is superseded by the live Mac probe above")
    files.append(ex)
    return {"feeds": feeds, "files": files, "ibkr": ib}


def build():
    now = dt.datetime.now(UTC)
    mb = main_book(now)
    t18 = trend18(now)
    pdk = parallel(now)
    oa = oanda(now)
    ee = entry_engine(now)
    hl = health(now, mb, t18, pdk, oa, ee)
    positions = main_positions() + t18.pop("positions") + pdk.pop("positions") + oa.pop("positions")
    return {"generated_utc": now.isoformat(), "generated_syd": now.astimezone(SYD).strftime("%a %d %b %Y %H:%M:%S %Z"),
            "paper_only": True, "read_only": True,
            "main": mb, "scoreboard": pdk.get("today"),
            "sleeves": [
                {"name": "Main V1", "value_usd": mb.get("equity_net_usd"), "value_label": "equity after fees", "badge": mb.get("badge"),
                 "detail": "firm book incl. Trend18 · DD %s%% from peak · level %s" % (mb.get("dd_pct"), mb.get("level"))},
                {"name": "Trend18", "value_usd": t18.get("equity_usd"), "value_label": "equity", "pnl_usd": t18.get("pnl_usd"), "badge": t18.get("badge"),
                 "detail": "P&L vs funding basis; DD %s%% (kill -30%%)" % t18.get("dd_pct")},
                {"name": "Parallel Desk", "value_usd": pdk.get("booked_since_seed_usd"), "value_label": "booked P&L since $300k seed", "badge": pdk.get("badge"),
                 "detail": "cash $%s · %s open · halt %s (headroom $%s)" % (format(pdk.get("cash_usd") or 0, ",.0f"), pdk.get("open_clips"),
                                                                        "LATCHED" if pdk.get("halt_latched") else "armed", format(pdk.get("halt_headroom_usd") or 0, ",.0f"))},
                {"name": "OANDA", "value_aud": oa.get("pnl_aud"), "value_usd": oa.get("pnl_usd"), "value_label": "P&L vs start NAV (practice, AUD)", "badge": oa.get("badge"),
                 "detail": "NAV A$%s · %s TFO open" % (format(oa.get("nav_aud") or 0, ",.2f"), oa.get("tfo_open"))},
            ],
            "trend18": t18, "parallel": pdk, "oanda": oa, "positions": positions, "entry_engine": ee, "health": hl}


def cached():
    now = time.time()
    if _CACHE["data"] is None or now - _CACHE["ts"] > TTL_S:
        _CACHE["data"] = build()
        _CACHE["ts"] = now
    return _CACHE["data"]


if __name__ == "__main__":
    print(json.dumps(build(), indent=1, default=str))
