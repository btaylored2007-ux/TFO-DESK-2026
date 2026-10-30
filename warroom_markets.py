"""War Room ALL MARKETS v1 (2026-10-27) - READ-ONLY aggregator for /api/markets.

PAPER ONLY. Reads existing status JSONs and ledgers; never writes, never places
orders, never touches OANDA config/positions (OANDA is mirrored from its local
snapshot for display only; the account id is dropped). Missing data -> None
(the page shows a dash). Money in A$, times in WITA (Asia/Makassar).
"""
import datetime as dt, json, os, subprocess, threading, time
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__))
WITA = ZoneInfo("Asia/Makassar")
UTC = dt.timezone.utc

# (key, label, kind, status json, ledger dir, session tz, open, close, launchd label)
MARKETS = [
    ("us_main", "US - Ours", "us", "static/hunt48_short_status.json", "state/hunt48_short", "America/New_York", "09:30", "16:00", "com.tfo.hunt48.short"),
    ("us_claude", "US - Claude", "us", "static/hunt48_short_claude_status.json", "state/hunt48_short_claude", "America/New_York", "09:30", "16:00", "com.tfo.hunt48.short.claude"),
    ("us_gpt", "US - GPT", "us", "static/hunt48_short_gpt_status.json", "state/hunt48_short_gpt", "America/New_York", "09:30", "16:00", "com.tfo.hunt48.short.gpt"),
    ("asx", "ASX", "asia", "static/hunt48_asx_status.json", "state/hunt48_asx", "Australia/Sydney", "10:00", "16:00", "com.tfo.hunt48.asx"),
    ("jp", "Japan", "asia", "static/hunt48_jp_status.json", "state/hunt48_jp", "Asia/Tokyo", "09:00", "15:30", "com.tfo.hunt48.jp"),
    ("hk", "Hong Kong", "asia", "static/hunt48_hk_status.json", "state/hunt48_hk", "Asia/Hong_Kong", "09:30", "16:00", "com.tfo.hunt48.hk"),
    ("sg", "Singapore", "asia", "static/hunt48_sg_status.json", "state/hunt48_sg", "Asia/Singapore", "09:00", "17:00", "com.tfo.hunt48.sg"),
    ("kr", "Korea", "asia", "static/hunt48_kr_status.json", "state/hunt48_kr", "Asia/Seoul", "09:00", "15:30", "com.tfo.hunt48.kr"),
    ("tw", "Taiwan", "asia", "static/hunt48_tw_status.json", "state/hunt48_tw", "Asia/Taipei", "09:00", "13:30", "com.tfo.hunt48.tw"),
    # 2026-10-27 Brett pt1522u: London FTSE 350 48-72h book (PAPER, A$1m), kind "europe" (same card/accounting as the Asia books)
    ("lse", "London", "europe", "static/hunt48_lse_status.json", "state/hunt48_lse", "Europe/London", "08:00", "16:30", "com.tfo.hunt48.lse"),
]
BOOK_KINDS = ("asia", "europe")   # exchange hunt48 books using asia_book() accounting
# freshness thresholds (green <= a, amber <= b, else red), seconds
FRESH = {"us_px": (2700, 7200), "us": (900, 7200), "asia": (900, 7200), "europe": (900, 7200), "crypto": (900, 3600), "trend18": (5400, 21600), "fx": (5400, 21600)}
JOBS = ["com.tfo.hunt48.short", "com.tfo.hunt48.short.claude", "com.tfo.hunt48.short.gpt", "com.tfo.hunt48.asx",
        "com.tfo.hunt48.jp", "com.tfo.hunt48.hk", "com.tfo.hunt48.sg", "com.tfo.hunt48.kr", "com.tfo.hunt48.tw", "com.tfo.hunt48.lse",
        "com.tfo.entry-engine", "com.tfo.ht-crypto-rails", "com.tfo.ht-equity-rails", "com.tfo.oanda-check"]


def _j(rel, default=None):
    try:
        with open(os.path.join(BASE, rel)) as f:
            return json.load(f)
    except Exception:
        return default


def _num(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _r(x, d=2):
    return None if x is None else round(x, d)


def _ts(s):
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=UTC)
    except Exception:
        return None


def wita_str(t):
    return None if t is None else t.astimezone(WITA).strftime("%a %d %b %H:%M WITA")


def fresh(updated, kind, now):
    t = _ts(updated) if not isinstance(updated, dt.datetime) else updated
    if t is None:
        return {"level": "red", "age_min": None, "asof_wita": None, "note": "no timestamp"}
    age = (now - t).total_seconds()
    a, b = FRESH.get(kind, (900, 7200))
    lvl = "green" if age <= a else "amber" if age <= b else "red"
    return {"level": lvl, "age_min": int(age // 60), "asof_wita": wita_str(t)}


def session(now, tzname, o, c, days=(0, 1, 2, 3, 4)):
    """Regular-hours session state (exchange holidays are NOT modelled)."""
    tz = ZoneInfo(tzname)
    loc = now.astimezone(tz)
    oh, om = map(int, o.split(":")); ch, cm = map(int, c.split(":"))
    for d in range(0, 9):
        day = (loc + dt.timedelta(days=d)).date()
        if day.weekday() not in days:
            continue
        op = dt.datetime(day.year, day.month, day.day, oh, om, tzinfo=tz)
        cl = dt.datetime(day.year, day.month, day.day, ch, cm, tzinfo=tz)
        if d == 0 and op <= loc < cl:
            return {"open": True, "closes_wita": wita_str(cl), "next_open_wita": None}
        if op > loc:
            return {"open": False, "closes_wita": None, "next_open_wita": wita_str(op)}
    return {"open": None, "closes_wita": None, "next_open_wita": None}


def fx_session(now):
    """OANDA FX week: Sun 17:00 New York -> Fri 17:00 New York."""
    ny = now.astimezone(ZoneInfo("America/New_York"))
    wd, hm = ny.weekday(), ny.hour * 60 + ny.minute
    closed = wd == 5 or (wd == 4 and hm >= 1020) or (wd == 6 and hm < 1020)
    if not closed:
        fri = (ny + dt.timedelta(days=(4 - wd) % 7)).replace(hour=17, minute=0, second=0, microsecond=0)
        return {"open": True, "closes_wita": wita_str(fri), "next_open_wita": None}
    sun = (ny + dt.timedelta(days=(6 - wd) % 7)).replace(hour=17, minute=0, second=0, microsecond=0)
    return {"open": False, "closes_wita": None, "next_open_wita": wita_str(sun)}


def _is_today(t, now):
    return t is not None and t.astimezone(WITA).date() == now.astimezone(WITA).date()


def us_book(key, label, st, ledger, now):
    fx = _num((st or {}).get("audusd")) or _num((ledger or {}).get("audusd_armed"))
    pos, armed, risk = [], [], 0.0
    for t in (st or {}).get("trades") or []:
        s = t.get("status")
        if s == "FILLED" and (t.get("open_qty") or 0) > 0:
            sg = -1 if t.get("direction") == "SHORT" else 1
            e, sp, q = _num(t.get("entry")), _num(t.get("stop")), t.get("open_qty")
            r = max(0.0, q * (e - sp) * sg / fx) if None not in (e, sp, fx) else None
            if r is not None:
                risk += r
            pos.append({"sym": t.get("ticker"), "side": t.get("direction"), "entry": e, "stop": sp,
                        "target": _num(t.get("t2")), "t1": _num(t.get("t1")), "mark": _num(t.get("price")),
                        "mark_utc": t.get("price_utc"), "qty": q, "upl_aud": _num(t.get("pnl_aud")),
                        "risk_aud": _r(r), "note": "P&L after costs (incl. any partial exit)"})
        elif s == "ARMED":
            armed.append({"sym": t.get("ticker"), "side": t.get("direction"), "trigger": _num(t.get("trigger")),
                          "stop": _num(t.get("stop")), "dist_pct": _num(t.get("dist_to_trigger_pct")),
                          "risk_aud": _num(t.get("risk_aud"))})
    tot = today = None
    if ledger is not None and fx:
        tot = today = 0.0
        for t in ledger.get("trades") or []:
            if not t.get("exits") or (t.get("open_qty") or 0) > 0:
                continue
            net = ((t.get("realised_usd") or 0) - (t.get("fees_usd") or 0) - (t.get("borrow_usd") or 0)) / fx
            tot += net
            if _is_today(_ts(t["exits"][-1].get("utc")), now):
                today += net
    return {"positions": pos, "armed": armed, "risk_aud": _r(risk) if pos else 0.0,
            "realized_today_aud": _r(today), "realized_total_aud": _r(tot),
            "open_count": (st or {}).get("open", len(pos)) if st else None, "max_open": (st or {}).get("max_open"),
            # audit H4: only a day-change figure is Day P&L; the old lifetime number is pnl_since_entry_aud
            "pnl_today_aud": _num((st or {}).get("pnl_today_aud")) if (st or {}).get("pnl_today_basis") else None,
            "pnl_since_entry_aud": _num((st or {}).get("pnl_since_entry_aud")),
            "price_asof": (st or {}).get("price_asof_utc"), "data_error_count": (st or {}).get("data_error_count"),
            "data_errors": (st or {}).get("data_errors") or [], "updated": (st or {}).get("updated_utc")}


def us_fresh(b, sess, now):
    """audit H14: while NY is open a US book is only as fresh as its PRICE (oldest open-position mark); closed -> status time."""
    if sess.get("open") and b.get("price_asof"):
        f = fresh(b["price_asof"], "us_px", now); f["basis"] = "price"
    else:
        f = fresh(b.get("updated"), "us", now); f["basis"] = "status"
    if b.get("data_error_count"): f["data_errors"] = b["data_error_count"]
    if b.get("data_error_count") and f["level"] == "green": f["level"] = "amber"
    return f


def asia_book(key, label, st, ledger, now):
    pos, armed, risk = [], [], 0.0
    for t in (st or {}).get("armed") or []:
        if t.get("status") == "FILLED" and (t.get("qty_open") or 0) > 0:
            sg = 1 if t.get("direction") == "long" else -1
            fxa = 1.0 if key == "asx" else _num(t.get("fx_aud"))
            f, m, q, sp = _num(t.get("fill")), _num(t.get("mark")), t.get("qty_open"), _num(t.get("stop_live", t.get("stop")))
            gross = sg * (m - f) * q * fxa if None not in (f, m, fxa) else None
            cost = _num(t.get("est_cost_aud"))
            upl = gross - cost if gross is not None and cost is not None else None
            r = max(0.0, abs(f - sp) * q * fxa) if None not in (f, sp, fxa) else None
            if r is not None:
                risk += r
            pos.append({"sym": t.get("sym"), "side": (t.get("direction") or "").upper(), "entry": f, "stop": sp,
                        "target": _num(t.get("t2")), "t1": _num(t.get("t1")), "mark": m, "mark_utc": t.get("last_bar_utc"),
                        "qty": q, "upl_aud": _r(upl), "risk_aud": _r(r), "note": "P&L after est. round-trip costs"})
        elif t.get("status") == "ARMED":
            armed.append({"sym": t.get("sym"), "side": (t.get("direction") or "").upper(), "trigger": _num(t.get("trigger")),
                          "stop": _num(t.get("stop")), "dist_pct": None, "risk_aud": _num(t.get("risk_aud")),
                          "armed_wita": t.get("armed_wita")})
    tot = today = None
    if ledger is not None:
        tot = today = 0.0
        for t in ledger.get("trades") or []:
            if t.get("status") != "CLOSED":
                continue
            n = _num(t.get("net_aud")) or 0.0
            tot += n
            if _is_today(_ts(t.get("last_bar_utc")), now):
                today += n
    # 2026-10-28 Brett 14:58 WITA: War Room sub-row for ~10% NAV SPEC sleeve (funded from book cash)
    ss = _j(f"static/hunt48_{key}_spec_status.json") or {}
    sub = []
    if ss:
        sub.append({"label": "SPEC sleeve (<=2%/name, -20% stop, -30% halt)",
                    "value_aud": _r(ss.get("nav_aud")),
                    "cash_aud": _r(ss.get("cash_aud")),
                    "positions": ss.get("n_pos") or 0,
                    "pnl_aud": _r(ss.get("pnl_aud")),
                    "status": "HALTED" if ss.get("spec_halt") else ("Empty" if (ss.get("n_pos") or 0) == 0 else "Running"),
                    "updated": ss.get("updated_wita")})
    return {"positions": pos, "armed": armed, "risk_aud": _r(risk) if pos else 0.0,
            "realized_today_aud": _r(today), "realized_total_aud": _r(tot),
            "open_count": len(pos) if st else None, "max_open": (st or {}).get("max_open"),
            "pnl_today_aud": None, "bench": len((st or {}).get("bench") or []),
            "updated": (st or {}).get("updated_utc"), "hours_wita": (st or {}).get("hours_wita") or (st or {}).get("asx_hours_wita"),
            "sub_rows": sub}


def crypto(now, audusd):
    ee = _j("static/entry_engine.json") or {}
    af = _j("state/entry_engine/fired_latest.json") or {}
    bk = _j("reports/tfo_ht_crypto.json")
    pos, risk = [], 0.0
    if bk:
        for pair, p in (bk.get("positions") or {}).items():
            if not isinstance(p, dict):
                continue
            q, e, sp = _num(p.get("qty")), _num(p.get("entry_px")), _num(p.get("stop"))
            m = _num((bk.get("mark_px_usd") or {}).get(pair))
            upl = (m - e) * q / audusd if None not in (m, e, q, audusd) else None
            r = max(0.0, (e - sp) * q / audusd) if None not in (e, sp, q, audusd) else None
            if r is not None:
                risk += r
            pos.append({"sym": pair, "side": "LONG", "entry": e, "stop": sp, "target": None, "mark": m, "qty": q,
                        "upl_aud": _r(upl), "risk_aud": _r(r), "note": "gross of exit fees"})
    cur_usd = _num(bk.get("realised_usd")) if bk else None
    tot = cur_usd / audusd if audusd and cur_usd is not None else None
    bl = baseline("crypto_kraken")
    since = since_reset(_r(tot), cur_usd, bl, audusd)
    scan_line, scan_wita = scan_summary(ee)
    today = None
    if bk is not None:
        closed_today = [c for c in bk.get("closed") or [] if _is_today(_ts(c.get("closed_utc")), now)]
        today = 0.0 if not closed_today else None   # per-close P&L not stored as one field -> dash
    last = af.get("last_fire") if isinstance(af.get("last_fire"), dict) else {}
    return {"positions": pos, "armed": [], "risk_aud": _r(risk) if pos else 0.0,
            "realized_today_aud": _r(today), "realized_total_aud": _r(tot), "open_count": len(pos) if bk else None,
            "max_open": _num(af.get("max_per_day")), "pnl_today_aud": None, "updated": ee.get("utc"),
            "engine_status": ee.get("status"), "auto_fire": af.get("state"), "fired_today": af.get("fired_today"),
            "max_per_day": af.get("max_per_day"), "last_action": af.get("last_action"),
            "last_fire": {"asset": last.get("asset"), "syd": last.get("syd")} if last else None,
            "scan_line": scan_line, "last_scan_wita": scan_wita,
            "realized_since_reset_aud": since,
            "baseline": ({"reset_at": bl.get("reset_at"), "prior_aud": _num(bl.get("realized_at_reset_aud"))} if bl else None),
            "count_label": "fires today / max"}


BASELINES = "state/warroom_baselines.json"   # display-only P&L baselines (ledgers untouched)


def baseline(key, path=None):
    """Display baseline for a card, e.g. {"reset_at": ISO WITA, "realized_at_reset_aud": x, "realized_at_reset_usd": y}."""
    try:
        with open(path or os.path.join(BASE, BASELINES)) as f:
            b = json.load(f).get(key)
        return b if isinstance(b, dict) else None
    except Exception:
        return None


def since_reset(cur_aud, cur_usd, bl, audusd):
    """Realized since reset. Uses the USD delta when both sides have USD (so an AUD/USD move alone never
    shows as P&L); otherwise the A$ delta. No baseline -> the current total (nothing reset)."""
    if not bl:
        return cur_aud
    bu, ba = _num(bl.get("realized_at_reset_usd")), _num(bl.get("realized_at_reset_aud"))
    if bu is not None and cur_usd is not None and audusd:
        return _r((cur_usd - bu) / audusd)
    if ba is not None and cur_aud is not None:
        return _r(cur_aud - ba)
    return None


def scan_summary(ee):
    """'Last scan <time WITA> - <result>' from entry_engine.json, or 'no scan yet'."""
    t = _ts((ee or {}).get("utc"))
    if t is None:
        return "no scan yet", None
    res = None
    for blk in ((ee or {}).get("latest_md") or "").split("\n\n"):
        if blk.startswith("This scan"):
            body = [ln.strip() for ln in blk.split("\n")[1:] if ln.strip()]
            txt = " ".join(body)
            if txt.startswith("None."):
                txt = txt[5:].strip() or "no signal"
            res = txt or None
            break
    return "Last scan " + wita_str(t) + (" · " + res if res else ""), wita_str(t)


def trend18_block(now, audusd):
    try:
        import trend18
        s = trend18.summary()
    except Exception as exc:
        return {"error": str(exc)[:120], "positions": [], "armed": [], "updated": None}
    pos = []
    for h in s.get("holdings") or []:
        if not (_num(h.get("qty")) or 0):
            continue  # health overlord 2026-10-28: zero-qty legs (TMF/DBMF/KMLM/CTA) are not open positions
        upl = _num(h.get("upl_usd"))
        pos.append({"sym": h.get("inst"), "side": "LONG" if (_num(h.get("qty")) or 0) >= 0 else "SHORT",
                    "entry": _num(h.get("avg_cost")), "stop": None, "target": None, "mark": _num(h.get("px")),
                    "mark_utc": h.get("mark_utc"), "qty": _num(h.get("qty")),
                    "upl_aud": _r(upl / audusd) if upl is not None and audusd else None, "risk_aud": None,
                    "note": "trend sleeve: no stop/target (rebalance rules)"})
    pnl = _num(s.get("pnl_usd"))
    return {"positions": pos, "armed": [], "risk_aud": None, "realized_today_aud": None, "realized_total_aud": None,
            "open_count": len(pos), "max_open": None, "pnl_today_aud": None, "updated": s.get("updated"),
            "status": s.get("status"), "equity_aud": _r(_num(s.get("equity_usd")) / audusd) if _num(s.get("equity_usd")) and audusd else None,
            "pnl_total_aud": _r(pnl / audusd) if pnl is not None and audusd else None,
            "drawdown_pct": _num(s.get("drawdown_pct")), "next": s.get("next"), "count_label": "holdings"}



def essentials_block(now):
    """READ-ONLY A$1m ESSENTIALS paper book (not firm equity); SPEC sleeve shown as sub-row."""
    s = _j("static/essentials_status.json")
    if not s:
        return {"error": "essentials_status missing", "positions": [], "armed": [], "updated": None}
    pos, armed = [], []
    for h in s.get("holdings") or []:
        if h.get("status") == "STAGED":
            armed.append({"sym": h.get("inst"), "side": "LONG", "entry": _num(h.get("ref_close_local")), "stop": None, "target": None,
                          "note": "%s %s STAGED next open, A$%s target (live px at open)" % (h.get("sleeve"), h.get("mkt"), h.get("target_aud"))})
            continue
        pos.append({"sym": "%s [%s %s]" % (h.get("inst"), h.get("mkt"), h.get("sleeve")), "side": "LONG", "entry": _num(h.get("entry_px_local")),
                    "stop": None, "target": None, "mark": _num(h.get("mark_px_local")), "qty": _num(h.get("qty")),
                    "upl_aud": _num(h.get("upl_aud")), "risk_aud": None,
                    "note": "ESSENTIALS %s: local px, A$ P&L; stop A$%s%s" % (h.get("sleeve"), h.get("stop_aud"), " HIT" if h.get("stop_hit") else "")})
    return {"positions": pos, "armed": armed, "risk_aud": None, "realized_today_aud": None,
            "realized_total_aud": _r(_num(s.get("vs_seed_aud"))), "open_count": len(pos), "max_open": None,
            "pnl_today_aud": None, "updated": s.get("updated_utc"), "status": "PAPER",
            "equity_aud": _r(_num(s.get("nav_aud"))), "cash_aud": _r(_num(s.get("cash_aud"))), "spare_aud": _r(_num(s.get("spare_aud"))),
            "deployed_aud": _r(_num(s.get("deployed_aud"))), "staged_aud": _r(_num(s.get("staged_aud"))),
            "pnl_total_aud": _r(_num(s.get("vs_seed_aud"))), "core_pnl_aud": _r(_num(s.get("core_pnl_aud"))),
            "spec_pnl_aud": _r(_num(s.get("spec_pnl_aud"))), "spec_value_aud": _r(_num(s.get("spec_value_aud"))),
            "spec_halt": s.get("spec_halt"), "by_market_aud": s.get("by_market_aud"),
            "sub_rows": [{"label": "SPEC sleeve (<=2%/name, -20% stop, -30% halt)", "value_aud": _r(_num(s.get("spec_value_aud"))),
                          "pnl_aud": _r(_num(s.get("spec_pnl_aud"))), "halt": s.get("spec_halt")}],
            "count_label": "holdings", "in_firm_equity": False}

def trend_core_block(now):
    """READ-ONLY A$1m TREND-CORE paper book (path B, not firm equity)."""
    s = _j("static/trend_core_status.json")
    if not s:
        return {"error": "trend_core_status missing", "positions": [], "armed": [], "updated": None}
    pos = []
    for h in s.get("holdings") or []:
        if not (h.get("qty") or h.get("value_aud")):
            continue
        pos.append({"sym": h.get("inst"), "side": "LONG", "entry": _num(h.get("entry_px_usd")),
                    "stop": None, "target": None, "mark": _num(h.get("px_usd")),
                    "qty": _num(h.get("qty")), "upl_aud": _num(h.get("upl_aud")), "risk_aud": None,
                    "note": "TREND-CORE: A$ mark, no stop/target (rebalance rules)"})
    nb = s.get("next_rebalance") or {}
    return {"positions": pos, "armed": [], "risk_aud": None, "realized_today_aud": None,
            "realized_total_aud": _r(_num(s.get("vs_seed_aud"))), "open_count": len(pos), "max_open": None,
            "pnl_today_aud": None, "updated": s.get("updated_utc"),
            "status": "PAPER", "equity_aud": _r(_num(s.get("nav_aud"))),
            "cash_aud": _r(_num(s.get("cash_aud"))), "spare_aud": _r(_num(s.get("spare_aud"))),
            "deployed_aud": _r(_num(s.get("deployed_aud"))),
            "pnl_total_aud": _r(_num(s.get("vs_seed_aud"))),
            "drawdown_pct": _num(s.get("drawdown_pct")),
            "next": nb.get("date"), "count_label": "holdings",
            "in_firm_equity": False, "accounting_path": "B",
            "shadow_diff_aud": _r(_num((s.get("shadow") or {}).get("diff_aud"))),
            "shadow_equity_aud": _r(_num((s.get("shadow") or {}).get("shadow_equity_aud"))),
            "shadow_actions": (s.get("shadow") or {}).get("n_actions"),
            "shadow_note": "SHADOW profit-taking (log only, outside book P&L): " + str((s.get("shadow") or {}).get("rules") or "not yet run")}


def oanda_ruleset():
    c = _j("state/oanda/config.json") or {}
    return "AGGRO" if str(c.get("active_ruleset") or "DESIGN").upper() == "AGGRO" else "DESIGN"
def _oanda_capital(s):
    """28 Oct 2026 (Brett +A$100k paper): capital base = broker NAV + funder top-ups (state/oanda_aggro/funder_topups.jsonl)."""
    tu = 0.0
    try:
        for l in open(os.path.join(BASE, "state/oanda_aggro/funder_topups.jsonl")):
            if l.strip(): tu += float(json.loads(l).get("amount_aud") or 0)
    except Exception: pass
    nav = _num(s.get("nav"))
    return {"broker_nav_aud": _r(nav), "funder_topup_aud": _r(tu), "capital_base_aud": _r((nav or 0) + tu) if nav is not None else None}
def oanda_block(now):
    """READ-ONLY mirror of state/oanda/snapshot.json (display only; account id never exposed)."""
    s = _j("state/oanda/snapshot.json")
    if not s:
        return {"positions": [], "armed": [], "updated": None}
    pos = []
    for t in s.get("trades") or []:
        u = _num(t.get("units")) or 0
        pos.append({"sym": t.get("instrument"), "side": "LONG" if u > 0 else "SHORT", "entry": _num(t.get("price")),
                    "stop": _num(t.get("stop")), "target": None, "mark": None, "qty": u, "upl_aud": _num(t.get("upl_aud")),
                    "risk_aud": None, "owner": t.get("owner"), "note": "broker UPL (A$), owner " + str(t.get("owner") or "-")})
    return {"positions": pos, "armed": [], "risk_aud": None, "realized_today_aud": None,
            "realized_total_aud": _r(_num(s.get("tfo_realized_aud"))), "open_count": len(pos), "max_open": None,
            "pnl_today_aud": None, "updated": s.get("asof_utc"), "nav_aud": _r(_num(s.get("nav"))),
            "upl_aud": _r(_num(s.get("upl"))), "count_label": "open trades (all owners)", "read_only": True,
            **_oanda_capital(s)}


def oanda_lab_block(now):
    """OANDA STRATEGY LAB (added 2026-10-27, research/oanda_deep_20261027): READ-ONLY mirror of state/oanda_lab/lab.json written by
    research/oanda_deep_20261027/code/lab_job.py (launchd com.tfo.oanda-lab). Research + SHADOW forward tests only - never places OANDA orders."""
    L = _j("state/oanda_lab/lab.json")
    if not L:
        return {"available": False, "label": "OANDA strategy lab"}
    s = L.get("search") or {}
    h = L.get("hunt") or {}
    sc = h.get("scorecard") or {}
    surv = []
    for x in (L.get("survivors") or [])[:5]:
        surv.append({k: x.get(k) for k in ("name", "instruments", "tf", "family", "holdout_aud", "holdout_aud_05", "win_rate", "trades",
                                             "maxdd_aud", "fwd_trades", "fwd_open", "fwd_aud", "status")})
    picks = []
    for p in (h.get("picks") or [])[:3]:
        picks.append({k: p.get(k) for k in ("ins", "tf", "side", "entry", "stop", "targets", "risk_aud", "score", "fams", "status", "mtm_aud", "exit_why")})
    return {"available": True, "label": "OANDA strategy lab", "shadow_only": True, "places_orders": False,
            "updated": L.get("updated_utc"), "updated_wita": L.get("updated_wita"), "freshness": fresh(L.get("updated_utc"), "fx", now),
            "configs_tested": _num(s.get("configs_tested")), "universe": _num(s.get("universe")), "timeframes": s.get("timeframes"),
            "survivors_count": _num(s.get("survivors_count")) if s.get("survivors_count") is not None else len(surv),
            "candidates_frozen": _num(s.get("candidates_frozen")), "survivors": surv, "verdict": L.get("verdict"),
            "near_miss": (L.get("search") or {}).get("near_miss"),
            "hunt": {"date_wita": h.get("date_wita"), "regime": (h.get("regime") or {}).get("label"), "picks": picks,
                     "n_setups": _num(h.get("n_setups")), "rule": h.get("rule"), "open": (h.get("open") or [])[:8],
                     "kill": {k: (h.get("kill") or {}).get(k) for k in ("halted", "reason", "mtm_dd_R", "rule")},
                     "watch": [{k: w.get(k) for k in ("ins", "tf", "side", "fam")} for w in (h.get("watch") or [])[:5]],
                     "purpose": h.get("purpose"),
                     "scorecard": {k: sc.get(k) for k in ("n_closed", "n_open", "wins", "win_rate", "net_aud", "open_mtm_aud", "avg_R", "t_stat", "target_n")}},
            "checks": L.get("checks"), "report": L.get("report")}


def jobs():
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    rows = {}
    for ln in out.splitlines():
        p = ln.split("\t")
        if len(p) == 3 and p[2] in JOBS:
            rows[p[2]] = {"pid": None if p[0] == "-" else p[0], "last_exit": p[1]}
    bad = [k for k in JOBS if k in rows and rows[k]["last_exit"] not in ("0", "-15") and not rows[k]["pid"]]
    missing = [k for k in JOBS if k not in rows]
    return {"jobs": rows, "failing": bad, "missing": missing}


def rs_long_info(led):
    """2026-10-28: count of rs_long_in_short_regime trades in this book's ledger by status (tracked separately)."""
    T = (led or {}).get("trades", []) if isinstance(led, dict) else []
    c = {}
    for t in T:
        if t.get("strategy") == "rs_long_in_short_regime": c[t.get("status")] = c.get(t.get("status"), 0) + 1
    return c
def attach_tags(key, b):
    """2026-10-28 INFO ONLY: news-risk / quality / catalyst tags from state/trade_tags/tags.json onto armed + open rows (display only)."""
    T = _j("state/trade_tags/tags.json") or {}
    for row in (b.get("armed") or []) + (b.get("positions") or []):
        r = T.get(f"{key}:{row.get('sym')}")
        if r: row["tags"] = {"news_risk": r.get("news_risk") or [], "quality": (r.get("quality") or {}).get("verdict", "n/a"),
                             "quality_detail": r.get("quality"), "catalyst": r.get("catalyst")}
def funder_queue_block():
    """2026-10-28 Brett 10:37: FUNDER APPROVAL queue (state/funder_queue.json). Pending first, then the last 10 decided."""
    E = list(((_j("state/funder_queue.json") or {}).get("entries") or {}).values())
    pend = [e for e in E if e.get("state") == "awaiting_funder"]; done = sorted([e for e in E if e.get("state") != "awaiting_funder"], key=lambda e: e.get("applied_utc") or "")[-10:]
    return {"pending": pend, "recent": done, "how": "python3 funder_approve.py --approve ID / --reject ID (engine applies on next tick)"}
def rescan_info(key):
    """2026-10-28 intraday rescan (every 45 min while open): last/next rescan WITA per market from static/intraday_rescan_status.json."""
    m = "us" if key.startswith("us_") else key
    r = ((_j("static/intraday_rescan_status.json") or {}).get("markets") or {}).get(m)
    if not r: return None
    return {"last_wita": r.get("last_rescan_wita"), "next_wita": r.get("next_rescan_wita"), "rc": r.get("last_rc"),
            "newly_armed": r.get("newly_armed") or [], "every_min": r.get("every_min"), "note": r.get("note")}


def build(now=None):
    now = now or dt.datetime.now(UTC)
    main = _j("static/hunt48_short_status.json") or {}
    audusd = _num(main.get("audusd"))
    out = []
    for key, label, kind, sj, ld, tz, o, c, job in MARKETS:
        st = _j(sj)
        led = _j(os.path.join(ld, "ledger.json"))
        b = (us_book if kind == "us" else asia_book)(key, label, st, led, now)
        sess = session(now, tz, o, c)
        # Session open/closed uses regular exchange hours (exchange holidays not modelled).
        # Book in_hours is shown as a note when it disagrees (status can lag past the close).
        note = None
        if kind in BOOK_KINDS and st is not None and "in_hours" in st:
            ih = bool(st.get("in_hours"))
            if ih != bool(sess.get("open")):
                note = "book status still says " + ("in hours" if ih else "closed") + " (may lag)"
        b.update(key=key, label=label, kind=kind, session=sess, job=job,
                 freshness=us_fresh(b, sess, now) if kind == "us" else fresh(b.get("updated"), kind, now), source=sj,
                 bench=b.get("bench"),
                 hours_wita=b.get("hours_wita") or ((st or {}).get("hours_wita") or (st or {}).get("asx_hours_wita")),
                 session_note=note)
        b["rescan"] = rescan_info(key)
        attach_tags(key, b)
        b["rs_long"] = rs_long_info(led)
        out.append(b)
    cb = crypto(now, audusd)
    cb.update(key="crypto", label="Crypto (Kraken)", kind="crypto", session={"open": True, "closes_wita": None, "next_open_wita": None, "always": True},
              job="com.tfo.entry-engine", freshness=fresh(cb.get("updated"), "crypto", now), source="static/entry_engine.json")
    cb["rescan"] = rescan_info("crypto")
    out.append(cb)
    tb = trend18_block(now, audusd)
    tcb = trend_core_block(now)
    tcb.update(key="trend_core", label="TREND-CORE A$1m", kind="trend18", session=session(now, "America/New_York", "09:30", "16:00"),
               freshness=fresh(tcb.get("updated"), "trend18", now), source="static/trend_core_status.json",
               note="path B: separate A$1m paper, not in firm equity")
    tb.update(key="trend18", label="Trend18 sleeve", kind="trend18", session=session(now, "America/New_York", "09:30", "16:00"),
              freshness=fresh(tb.get("updated"), "trend18", now), source="trend18.summary()")
    out.append(tb)
    out.append(tcb)
    try:  # 2026-10-28 ESSENTIALS A$1m paper card
        eb = essentials_block(now)
        eb.update(key="essentials", label="ESSENTIALS A$1m", kind="trend18", session={"open": True, "closes_wita": None, "next_open_wita": None, "always": True, "note": "multi-market"},
                  freshness=fresh(eb.get("updated"), "trend18", now), source="static/essentials_status.json",
                  note="separate A$1m paper, not in firm equity; SPEC sub-row")
        out.append(eb)
    except Exception as _e_es:
        out.append({"key": "essentials", "label": "ESSENTIALS A$1m", "error": str(_e_es)[:160], "positions": [], "armed": []})
    ob = oanda_block(now)
    _rs = oanda_ruleset()   # 28 Oct 2026 (Brett 13:14 WITA): show the active OANDA paper ruleset on the card
    ob.update(active_ruleset=_rs, session_note=("Active ruleset: AGGRO (3% risk/trade, 25% open-risk cap, stop on every trade) - paper" if _rs == "AGGRO" else "Active ruleset: DESIGN (paper)"))
    ob.update(key="oanda", label="OANDA FX (read-only) · %s active" % _rs, kind="fx", session=fx_session(now), job="com.tfo.oanda-check",
              freshness=fresh(ob.get("updated"), "fx", now), source="state/oanda/snapshot.json")
    out.append(ob)
    def _day(m):
        if m["kind"] == "us":
            return m.get("pnl_today_aud")
        if m.get("realized_today_aud") is None and not m["positions"]:
            return m.get("realized_today_aud")  # None or 0
        upl = sum((p.get("upl_aud") or 0) for p in m["positions"])
        rt = m.get("realized_today_aud")
        if rt is None and not m["positions"]:
            return None
        return (rt or 0) + upl

    def _group(kinds):
        ms = [m for m in out if m["kind"] in kinds]
        risk = [m.get("risk_aud") for m in ms]
        day = [_day(m) for m in ms]
        return {
            "open_risk_aud": _r(sum(r for r in risk if r is not None)) if any(r is not None for r in risk) else None,
            "day_pnl_aud": _r(sum(d for d in day if d is not None)) if any(d is not None for d in day) else None,
            "open_positions": sum(len(m["positions"]) for m in ms),
            "armed": sum(len(m.get("armed") or []) for m in ms),
            "bench": sum(m.get("bench") or 0 for m in ms if m["kind"] in BOOK_KINDS),
            "markets_open": sum(1 for m in ms if (m.get("session") or {}).get("open") or (m.get("session") or {}).get("always")),
            "count": len(ms),
        }

    us_g = _group(("us",))
    asia_g = _group(("asia",))
    europe_g = _group(("europe",))
    crypto_g = _group(("crypto",))
    hunt = [m for m in out if m["kind"] in ("us", "asia", "europe", "crypto")]
    risk = [m.get("risk_aud") for m in hunt]
    day = [_day(m) for m in hunt]
    rw = _j("reports/rail_watchdog.json") or {}
    jb = jobs()
    firm = {"open_risk_aud": _r(sum(r for r in risk if r is not None)) if any(r is not None for r in risk) else None,
            "risk_complete": all(r is not None for r in risk),
            "day_pnl_aud": _r(sum(d for d in day if d is not None)) if any(d is not None for d in day) else None,
            "day_pnl_complete": all(d is not None for d in day),
            "open_positions": sum(len(m["positions"]) for m in hunt),
            "us": us_g, "asia": asia_g, "europe": europe_g, "crypto": crypto_g,
            "basis": "firm totals = US x3 + ASX/JP/HK/SG/KR/TW + London + Kraken crypto (Trend18, TREND-CORE A$1m & OANDA separate; TREND-CORE not in firm equity)",
            "watchdog": {"status": rw.get("status"), "alerts": _num(rw.get("alert_count")), "asof_wita": wita_str(_ts(rw.get("updated_utc")))},
            "jobs": jb, "audusd": audusd}
    h = "OK" if rw.get("status") == "OK" and jb is not None and not jb["failing"] and not jb["missing"] else ("ALERT" if rw.get("status") not in (None, "OK") else "CHECK")
    firm["health"] = h
    return {"schema": "warroom_markets v1", "paper_only": True, "read_only": True, "generated_wita": wita_str(now),
            "generated_utc": now.isoformat(timespec="seconds"), "firm": firm, "markets": out, "oanda_lab": oanda_lab_block(now),
            "funder_queue": funder_queue_block(),
            "notes": ["Session times are regular hours; exchange holidays are not shown.",
                      "Dash = data missing. Money A$ (US$ converted at status audusd)."]}


_LOCK = threading.Lock(); _CACHE = {"t": 0.0, "v": None}


def cached(ttl=30):
    with _LOCK:
        if _CACHE["v"] is None or time.time() - _CACHE["t"] > ttl:
            _CACHE["v"] = build(); _CACHE["t"] = time.time()
        return _CACHE["v"]


if __name__ == "__main__":
    print(json.dumps(build(), indent=1, default=str)[:6000])
