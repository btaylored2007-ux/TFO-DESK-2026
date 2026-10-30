"""Strategy Tracker (Brett 2026-10-28 07:10/07:19 WITA) - PAPER, report only. One plain-English row per strategy.
Every strategy keeps RUNNING (Brett 07:12: "we still want to run all strategies"); the triage verdict is shown as INFO ONLY - nothing
here disables, pauses or resizes anything. Writes static/strategy_tracker.json (War Room card + /report)."""
import datetime as dt, glob, json, os, statistics
BASE = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.join(BASE, "static", "strategy_tracker.json")
try:   # 2026-10-28: live/daily AUDUSD with labelled fallback 0.7022 (was hardcoded)
    import fundamental_true_return as _FTR; FX_INFO = _FTR.audusd()
except Exception:
    FX_INFO = {"rate": 0.7022, "source": "FALLBACK constant 0.7022", "fallback": True}
FX = float(FX_INFO["rate"]); TP, SL = 0.03, 0.08; BE0 = SL / (TP + SL)   # 72.7% before costs
PASS_N = 50   # forward pass mark first checkpoint (registered 28 Oct 05:04 WITA)
HUNTS = [("Hunt US main", "state/hunt48_short/ledger.json", "usd", "CUT new entries"),   # 2026-10-29 TFO 11:12 WITA: Hunt US Claude retired, removed (ledger kept for history)
         ("Hunt US GPT", "state/hunt48_short_gpt/ledger.json", "usd", "CUT"), ("Hunt ASX", "state/hunt48_asx/ledger.json", "aud", "CUT"),
         ("Hunt Japan", "state/hunt48_jp/ledger.json", "local", "CUT"), ("Hunt Hong Kong", "state/hunt48_hk/ledger.json", "local", "CUT"),
         ("Hunt Singapore", "state/hunt48_sg/ledger.json", "local", "CUT"), ("Hunt Korea", "state/hunt48_kr/ledger.json", "local", "CUT"),
         ("Hunt Taiwan", "state/hunt48_tw/ledger.json", "local", "CUT"), ("Hunt London", "state/hunt48_lse/ledger.json", "local", "CUT")]
# Non-hunt rows: P&L from the 28 Oct 05:50 WITA inventory unless a live file is read below (src says which).
STATIC = [("OANDA AGGRO", "Running", "whole-account NAV, no fixed target", 1141, None, "FIX (info)", "inventory 05:50"),
          ("OANDA sleeve OXL1/OXL2/OXF1/OXP1/OXB1/OXT1", "Running", "per-design SL/TP", -321, 10, "CUT (info)", "inventory 05:50"),
          ("OXD1 daily limits", "Running", "SL/TP per setup", 0, 0, "CUT (info)", "inventory 05:50"),
          ("DEEP18 shadows FT1-5 / r2ref", "Running", "monthly rebalance", None, None, "CUT (info)", "inventory: -4.5..-5.1%"),
          ("DEEP19 ns_cf7 / ns_s01", "Running", "monthly rebalance", None, None, "CUT (info)", "inventory: ns_cf7 -16.5%, ns_s01 -1.8%"),
          ("Crypto20 FT1 trend (shadow)", "Running", "SMA trend", -69000, None, "CUT as alpha (info)", "inventory: -6.9% on A$1M shadow"),
          ("Crypto clips / D4 / BTC pounce", "Running", "per-trade swing", 769, 0, "CUT (info)", "inventory: +A$769 is cash interest"),
          ("Swing U4 (US)", "Running", "swing", 259, 0, "CUT (info)", "inventory: open P&L"),
          ("Honest10 RP10", "Running", "12-month risk parity", 0, 0, "CUT or start (info)", "inventory: day 0"),
          ("Semis/shark/announcements/PEAD, Edge50/smallcap/overnight2k/parallel", "Running", "labs", 0, 0, "CUT (info)", "inventory")]
def _j(p):
    try: return json.load(open(os.path.join(BASE, p)))
    except Exception: return None
def _wr(nets):
    """Win rate from net P&Ls; None if no closed sample (never invent)."""
    return (sum(1 for n in nets if n > 0) / len(nets)) if nets else None
def oanda_live_metrics():
    """Open / closed / win_rate from OANDA practice snapshot + net_r.json."""
    snap = _j("state/oanda/snapshot.json") or {}
    nr = _j("state/oanda/net_r.json") or {}
    open_n = snap.get("tfo_open")
    if open_n is None:
        open_n = snap.get("open_trade_count")
    trades = nr.get("trades") if isinstance(nr.get("trades"), list) else []
    nets = []
    for t in trades:
        if t.get("realized_pl_aud") is not None:
            try: nets.append(float(t["realized_pl_aud"]))
            except (TypeError, ValueError): pass
    n_closed = len(nets) if nets else (nr.get("n") if isinstance(nr.get("n"), int) else None)
    return dict(open=open_n, trades=n_closed, win_rate=_wr(nets),
                pnl_aud=round(float(nr["net_aud"]), 0) if nr.get("net_aud") is not None else None)
def crypto_sleeve_metrics():
    """Crypto clips / D4 / BTC pounce from live status files only (U4 has its own row)."""
    bp = _j("static/btc_pounce_status.json") or {}
    d4 = _j("static/swing_d4_status.json") or {}
    ht = _j("reports/tfo_ht_crypto.json") or {}
    open_n = (1 if bp.get("position") else 0) + len(d4.get("positions") or {}) + len(ht.get("positions") or {})
    trades = int(bp.get("n_trades") or 0) + int(d4.get("n_trades") or 0)
    nets = []
    for t in (d4.get("trades") or []):
        if t.get("status") in ("CLOSED", "STOPPED", "EXITED", "TIME_EXIT") and t.get("pnl_aud") is not None:
            try: nets.append(float(t["pnl_aud"]))
            except (TypeError, ValueError): pass
    return dict(open=open_n, trades=trades, win_rate=_wr(nets))
def trend_core_open():
    tc = _j("static/trend_core_status.json") or {}
    return sum(1 for h in (tc.get("holdings") or []) if (h.get("qty") or 0) or (h.get("value_aud") or 0))
def trend18_open():
    t18 = _j("state/trend18.json") or {}
    h = t18.get("holdings") or {}
    if isinstance(h, dict):
        return sum(1 for v in h.values() if v)
    return len(h) if isinstance(h, list) else None
def crypto_engine_metrics():
    ht = _j("reports/tfo_ht_crypto.json") or {}
    pos = ht.get("positions") or {}
    closed = ht.get("closed") or []
    open_n = len(pos) if isinstance(pos, dict) else None
    trades = len(closed) if isinstance(closed, list) else None
    nets = []
    for c in closed if isinstance(closed, list) else []:
        for k in ("pnl_usd", "realised_usd", "net_usd"):
            if c.get(k) is not None:
                try: nets.append(float(c[k]) / FX); break
                except (TypeError, ValueError): pass
    return dict(open=open_n, trades=trades, win_rate=_wr(nets))
def _net_aud(t, ccy):
    if ccy == "usd": return ((t.get("realised_usd") or 0) - (t.get("fees_usd") or 0) - (t.get("borrow_usd") or 0)) / FX
    for k in ("realised_aud", "pnl_aud", "net_aud"):
        if t.get(k) is not None: return float(t[k])
    return float(t.get("realised_usd") or 0) / FX
def _missed(L):
    ms = L.get("missed_shadow") if isinstance(L.get("missed_shadow"), list) else []
    return len(ms) + sum(1 for t in L.get("trades", []) if t.get("status") in ("MISSED", "SHADOW") or t.get("missed_shadow"))
def _v(x): return str((x or {}).get("verdict") if isinstance(x, dict) else (x or "")).upper()
def ai_class(t):
    """2026-10-28 Brett: GPT/Claude advisory. AI-FAIL = either FAIL; AI-PASS = >=1 PASS and no FAIL; else None (no verdict)."""
    g, c = _v(t.get("ai_gpt")), _v(t.get("ai_claude"))
    if "FAIL" in (g, c): return "FAIL"
    if "PASS" in (g, c): return "PASS"
    return None
def ai_split(closed, nets):
    out = {}
    for k in ("PASS", "FAIL"):
        ns = [n for t, n in zip(closed, nets) if ai_class(t) == k]
        out[k] = dict(n=len(ns), pnl_aud=round(sum(ns), 0), win_rate=(sum(1 for n in ns if n > 0) / len(ns)) if ns else None)
    return out
def ai_text(a):
    if not a: return None
    f = lambda d: f"n={d['n']} A${d['pnl_aud']:+,.0f}" + (f" win {d['win_rate']:.0%}" if d["win_rate"] is not None else "")
    return f"AI-PASS {f(a['PASS'])} | AI-FAIL {f(a['FAIL'])}"
def hunt_row(name, path, ccy, verdict):
    L = _j(path) or {}; T = L.get("trades", [])
    closed = [t for t in T if t.get("status") in ("CLOSED", "STOPPED") and (t.get("entry") or t.get("fill"))]
    opn = [t for t in T if t.get("status") == "FILLED"]
    nets = [_net_aud(t, ccy) for t in closed]; Rs = [n / float(t.get("risk_cap_aud") or t.get("risk_aud") or 2500) for n, t in zip(nets, closed)]
    costs = [float(t["cost_R"]) for t in T if isinstance(t.get("cost_R"), (int, float))]
    cR = statistics.mean(costs) if costs else 0.02; be = (1 + cR) / (TP / SL + 1)
    wins = sum(1 for n in nets if n > 0); wr = wins / len(nets) if nets else None
    mR = statistics.mean(Rs) if Rs else None
    return dict(strategy=name, status="Running", rule="Target +3% / Stop -8% (new trades); 48-72h exit; max 3 open", open=len(opn), trades=len(closed),
                pnl_aud=round(sum(nets), 0), win_rate=wr, be_win_rate=round(be, 3), expectancy_aud=round(statistics.mean(nets), 0) if nets else None,
                pass_mark=f"{len(closed)}/{PASS_N} closed to 1st check", kill=(f"mean R {mR:+.2f} (flag if <=0 at n={PASS_N}); win {wr:.0%} vs BE {be:.0%}" if nets else f"no closed trades; flag check at n={PASS_N}"),
                missed_shadow=_missed(L), ai_split=ai_split(closed, nets), verdict=verdict + " (info)", src=path)
RS_TAG = "rs_long_in_short_regime"
def rs_long_row():
    """2026-10-28: relative-strength longs in short-regime books, tracked separately (tag rs_long_in_short_regime) across all hunt ledgers."""
    T, by = [], {}
    for name, path, ccy, _ in HUNTS:
        for t in (_j(path) or {}).get("trades", []):
            if t.get("strategy") == RS_TAG: T.append((t, ccy)); by[name.replace("Hunt ", "")] = by.get(name.replace("Hunt ", ""), 0) + 1
    closed = [(t, c) for t, c in T if t.get("status") in ("CLOSED", "STOPPED") and (t.get("entry") or t.get("fill"))]
    nets = [_net_aud(t, c) for t, c in closed]; wr = sum(n > 0 for n in nets) / len(nets) if nets else None
    st = lambda k: sum(1 for t, _ in T if t.get("status") == k)
    return dict(strategy="RS longs in short regime (rs_long_in_short_regime)", status="Running", rule="long only if RS20 >= +5 vs index; +3%/-8%; A$2.5k risk; book caps/gates",
                open=st("FILLED"), armed=st("ARMED"), bench=st("BENCH"), trades=len(closed), pnl_aud=round(sum(nets), 0), win_rate=wr,
                expectancy_aud=round(statistics.mean(nets), 0) if nets else None, by_book=by, pass_mark=f"{len(closed)}/{PASS_N} closed to 1st check",
                verdict="NEW 28 Oct (info)", src="hunt48 ledgers, strategy tag")
def rows(now=None):
    R = [hunt_row(*h) for h in HUNTS]
    # 2026-10-28 Brett 14:58 WITA: ~10% NAV SPEC sleeve sub-row per hunt book (empty until Brett ticks)
    for name, path, ccy, _ in HUNTS:
        mkt = None
        for key in ("asx", "jp", "hk", "sg", "kr", "tw", "lse"):
            if f"hunt48_{key}" in path: mkt = key; break
        if not mkt: continue
        ss = _j(f"static/hunt48_{mkt}_spec_status.json") or {}
        if not ss: continue
        R.append(dict(strategy=f"  └ {name} SPEC sleeve", status="HALTED" if ss.get("spec_halt") else ("Empty" if (ss.get("n_pos") or 0) == 0 else "Running"),
                      rule="10% NAV carve, <=2%/name, -20% stop/name, -30% sleeve halt; funder tick required; no auto-arm",
                      open=ss.get("n_pos") or 0, trades=None, pnl_aud=round(float(ss.get("pnl_aud") or 0), 0),
                      verdict="NEW spec (info)", src=f"static/hunt48_{mkt}_spec_status.json"))
    R.append(rs_long_row())
    cb = _j("state/calendar/paper_bench_ledger.json") or {}
    R.append(dict(strategy="Calendar bench (macro/commodity)", status="Running", rule="trigger/stop/T1/T2", open=sum(1 for t in cb.get("trades", []) if t.get("status") == "FILLED"),
                  trades=sum(1 for t in cb.get("trades", []) if t.get("status") == "CLOSED"), pnl_aud=round(sum(float(t.get("realised_usd") or 0) for t in cb.get("trades", [])) / FX, 0),
                  missed_shadow=_missed(cb), verdict="CUT (info)", src="state/calendar/paper_bench_ledger.json"))
    t18 = _j("state/trend18.json") or {}
    if t18.get("start") and t18.get("equity"):
        R.append(dict(strategy="Trend18 / DEEP18 R2", status="Running", rule="monthly levered risk-parity trend", open=trend18_open(), trades=None,
                      pnl_aud=round((float(t18["equity"]) - float(t18["start"])) / FX, 0), pnl_usd=round(float(t18["equity"]) - float(t18["start"]), 0),
                      verdict="KEEP core (info)", src="state/trend18.json (live; US$ ledger -> A$ at desk FX %.4f)" % FX))
    tc = _j("static/trend_core_status.json") or {}
    if tc.get("nav_aud") is not None:
        pnl = tc.get("vs_seed_aud")
        if pnl is None and tc.get("seed_aud") is not None:
            pnl = float(tc["nav_aud"]) - float(tc["seed_aud"])
        R.append(dict(strategy="TREND-CORE A$1m", status="Running",
                      rule="monthly levered risk-parity trend (AUD, A$50k spare; path B alongside Trend18; not firm equity)",
                      open=trend_core_open(), trades=None,
                      pnl_aud=round(float(pnl), 0) if pnl is not None else None,
                      verdict="KEEP core (info)", src="static/trend_core_status.json (live)"))
    tg = _j("static/trend_core_global_status.json") or {}
    if tg:
        R.append(dict(strategy="  \u2514 TREND-CORE SPEC sleeve", status="HALTED" if tg.get("spec_halt") else ("Running" if tg.get("executed") else "Executes 28 Oct US open"),
                      rule="<=10% NAV higher-beta trend legs (ETH/SOL/ERX/EDC), 20% vol, monthly; -25% leg stop, -30% sleeve halt",
                      open=(sum(1 for t in tg.get("targets") or [] if t.get("group") == "spec" and float(t.get("target_aud") or 0) > 0) if tg.get("executed") else None), trades=None, pnl_aud=round(float(tg.get("spec_pnl_aud") or 0), 0), verdict="NEW spec (info)", src="static/trend_core_global_status.json (live)"))
    es = _j("static/essentials_status.json") or {}
    if es.get("nav_aud") is not None:
        R.append(dict(strategy="ESSENTIALS A$1m", status="Running", rule="quality energy/utilities/essential services, all markets; inv-vol, 8%/name 40%/mkt; quarterly rebal; -22% stop/trend break; A$50k spare; not firm equity",
                      open=sum(1 for h in es.get("holdings") or [] if h.get("status") == "FILLED"), trades=None,
                      pnl_aud=round(float(es.get("core_pnl_aud") or 0), 0), verdict="NEW core (info)", src="static/essentials_status.json (live)"))
        R.append(dict(strategy="  \u2514 ESSENTIALS SPEC sleeve", status="HALTED" if es.get("spec_halt") else "Running", rule="10% NAV, <=2%/name, -20% stop/name, -30% sleeve halt",
                      open=sum(1 for h in es.get("holdings") or [] if h.get("sleeve") == "spec" and h.get("status") == "FILLED"), trades=None,
                      pnl_aud=round(float(es.get("spec_pnl_aud") or 0), 0), verdict="NEW spec (info)", src="static/essentials_status.json (live)"))
    nq = _j("state/shadow_nasdaq2x.json") or {}
    if nq.get("notional_usd") and nq.get("value_at_last_mark"):
        R.append(dict(strategy="Shadow Nasdaq 2x (benchmark)", status="Running", rule="benchmark", pnl_aud=round((float(nq["value_at_last_mark"]) - float(nq["notional_usd"])) / FX, 0),
                      verdict="KEEP (info)", src="state/shadow_nasdaq2x.json (live)"))
    cs = _j("static/csp_sleeve.json") or {}
    R.append(dict(strategy="CSP put sleeve", status="Running", rule="30-DTE 0.25-delta puts; " + (cs.get("backtest") or "backtest: see REPORT"), open=len(cs.get("open") or []), pnl_aud=round(float(cs.get("sleeve_pnl_aud") or 0), 0),
                  verdict="CUT (info)", src="static/csp_sleeve.json (live)"))
    _ce = crypto_engine_metrics()
    R.append(dict(strategy="Crypto entry engine (Kraken breakout)", status="Under review", rule="breakout; +2%/+5% exits", pnl_aud=None,
                  open=_ce.get("open"), trades=_ce.get("trades"), win_rate=_ce.get("win_rate"),
                  pass_mark="review date 27 Nov 2026 (display only, nothing auto-cut)", verdict="FIX/CUT split (info)", src="state/entry_engine + reports/tfo_ht_crypto.json"))
    oa = oanda_live_metrics(); _csleeve = crypto_sleeve_metrics()
    for n, st, rule, p, tr, v, src in STATIC:
        row = dict(strategy=n, status=st, rule=rule, pnl_aud=p, trades=tr, verdict=v, src=src)
        if n == "OANDA AGGRO":
            # Open = live TFO AGGRO opens. Closed/win_rate stay dashed unless AGGRO-specific
            # closed sample exists (net_r.json is design-sleeve attribution; do not invent).
            # Keep inventory P&L (NAV-based); do not overwrite with design net_r.
            row.update(open=oa.get("open"), src="state/oanda/snapshot.json (live open)")
        elif n == "OANDA sleeve OXL1/OXL2/OXF1/OXP1/OXB1/OXT1":
            snap = _j("state/oanda/snapshot.json") or {}
            broker_open, tfo_open = snap.get("open_trade_count"), snap.get("tfo_open")
            design_open = None
            if broker_open is not None and tfo_open is not None:
                try: design_open = max(0, int(broker_open) - int(tfo_open))
                except (TypeError, ValueError): design_open = None
            row.update(open=design_open, trades=oa.get("trades") if oa.get("trades") is not None else tr,
                       win_rate=oa.get("win_rate"),
                       src="state/oanda/net_r.json (live closed) + snapshot")
            if oa.get("pnl_aud") is not None:
                row["pnl_aud"] = oa["pnl_aud"]
        elif n == "Crypto clips / D4 / BTC pounce":
            row.update(open=_csleeve.get("open"), trades=_csleeve.get("trades"), win_rate=_csleeve.get("win_rate"),
                       src="static/btc_pounce_status.json + swing_d4/u4")
        elif n == "Swing U4 (US)":
            u4 = _j("static/swing_u4_status.json") or {}
            row.update(open=len(u4.get("positions") or {}), trades=u4.get("n_trades") if u4.get("n_trades") is not None else tr,
                       src="static/swing_u4_status.json (live)")
        R.append(row)
    for f in sorted(glob.glob(os.path.join(BASE, "state", "frozen_tests", "*.json"))):
        d = _j(f) or {}
        R.append(dict(strategy=d.get("label", os.path.basename(f)), status="Test", rule=d.get("rule"), open=d.get("open"), trades=d.get("n_closed", 0), pnl_aud=d.get("pnl_aud", 0),
                      win_rate=d.get("win_rate"), be_win_rate=d.get("be_win_rate"), expectancy_aud=d.get("expectancy_aud"), pass_mark=d.get("pass_mark"), kill=d.get("kill"),
                      missed_shadow=d.get("missed", 0), verdict="pre-registered test", src=os.path.relpath(f, BASE)))
    for r in R:
        for k in ("open", "trades", "pnl_aud", "win_rate", "be_win_rate", "expectancy_aud", "pass_mark", "kill", "missed_shadow", "ai_split"): r.setdefault(k, None)
    R.sort(key=lambda r: (r["pnl_aud"] is None, -(r["pnl_aud"] or 0)))
    return R
def write(now=None):
    now = now or dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    d = dict(paper_only=True, updated_wita=now.strftime("%a %d %b %H:%M WITA"), note="Sorted best to worst by P&L after costs (A$). Verdict = triage info only; every strategy keeps running. Break-even win rate for +3%/-8% = 72.7% + costs.", rows=rows(now))
    tmp = OUT + ".tmp"; json.dump(d, open(tmp, "w"), indent=1, default=str); os.replace(tmp, OUT); return d
def html(d=None):
    d = d or _j("static/strategy_tracker.json") or write()
    f = lambda x, p=False: "—" if x is None else (f"{x:.0%}" if p else (f"A${x:+,.0f}" if isinstance(x, (int, float)) else str(x)))
    h = ['<table class="tracker"><tr><th>Strategy</th><th>Status</th><th>Target / stop</th><th>Open</th><th>Trades</th><th>P&L after costs</th><th>Win rate (BE)</th><th>Expectancy</th><th>Pass mark</th><th>Kill distance</th><th>Missed (shadow)</th><th>AI-PASS vs AI-FAIL (closed, advisory)</th><th>Triage verdict (info)</th></tr>']
    for r in d["rows"]:
        wr = f(r["win_rate"], True) + (f' (BE {r["be_win_rate"]:.0%})' if r.get("be_win_rate") else "")
        h.append("<tr>" + "".join(f"<td>{x}</td>" for x in (r["strategy"], r["status"], r["rule"] or "—", f(r["open"]), f(r["trades"]), f(r["pnl_aud"]), wr, f(r["expectancy_aud"]), f(r["pass_mark"]), f(r["kill"]), f(r["missed_shadow"]), ai_text(r.get("ai_split")) or "—", r["verdict"])) + "</tr>")
    return "\n".join(h) + "</table>"
if __name__ == "__main__": d = write(); print(len(d["rows"]), "rows ->", OUT)
