#!/usr/bin/env python3
"""tfo_v2_dashboard.py — TFO V2 (separate paper fund) dashboard. PAPER ONLY. READ-ONLY.

http://127.0.0.1:18813/   (V1 War Room stays on 18812; 18820 is reserved — both refused here)
  /          single page, auto-refresh every 30 s from /api/all
  /api/all   every panel as JSON, computed server-side from the V2 ledgers
  /healthz   200 only when there are ZERO error-level alerts; else 503 with the alerts
             (data coverage 'degraded' = partial price downloads after retries is NEUTRAL: never an alert)

Stdlib only (http.server + json). Binds 127.0.0.1 only. It opens files read-only: it never writes a
ledger or state file and cannot ticket. Missing values are shown as "unavailable" and raise an alert —
no placeholders, no fake numbers.
"""
import csv, datetime as dt, json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("DESK") or os.environ.get("BASE") or HERE
R = os.path.join(BASE, "reports"); V2 = os.path.join(R, "v2")
PORT = int(os.environ.get("TFO_V2_DASH_PORT", "18813"))
if PORT in (18812, 18820): sys.exit("REFUSED: port %d belongs to V1 / is reserved" % PORT)
HOST = "127.0.0.1"
SYD, NY, UTC = ZoneInfo("Australia/Sydney"), ZoneInfo("America/New_York"), dt.timezone.utc
F = {"book": "tfo_v2_book.json", "status": "tfo_v2_status.json", "staged": "tfo_v2_staged.json", "compare": "tfo_compare.json",
     "firm": "tfo_v2_firm_equity.csv", "facts": "tfo_v2_facts.jsonl", "watch": "tfo_v2_dashboard_health.json"}
CONFIG = os.path.join(BASE, "tfo_v2_config.json")
# cadence (seconds) each source must meet; badge amber beyond 1x, red beyond 3x or missing
CADENCE = {"rails": 180, "enter": 180, "signals": 900, "mark": 1800, "compare": 1800, "firm_csv": 1800}
RUNNER_LABEL = {"rails": "v2-rails", "signals": "v2-signals", "enter": "v2-enter", "mark": "v2-mark-compare"}
UNAV = "unavailable"


def now_utc():
    o = os.environ.get("TFO_V2_NOW")
    return dt.datetime.fromisoformat(o.replace("Z", "+00:00")).astimezone(UTC) if o else dt.datetime.now(UTC)


def parse(s): return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(UTC)


def age(s, t):
    try: return round((t - parse(s)).total_seconds(), 1)
    except Exception: return None


NYSE_HOLIDAYS = {"2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
                 "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"}     # same list as tfo_v2.py
NYSE_EARLY_CLOSE = {"2026-11-27", "2026-12-24", "2027-11-26"}


def us_open(t):
    n = t.astimezone(NY); d = n.strftime("%Y-%m-%d"); hm = n.hour * 60 + n.minute
    cut = 12 * 60 + 50 if d in NYSE_EARLY_CLOSE else 15 * 60 + 50
    return n.weekday() < 5 and d not in NYSE_HOLIDAYS and 9 * 60 + 35 <= hm < cut


def read_json(name, alerts, required=True):
    p = os.path.join(V2, F[name]) if name in F else name
    try:
        with open(p) as f: return json.load(f)
    except FileNotFoundError:
        if required: alerts.append({"sev": "error", "code": "missing", "msg": "%s missing — %s" % (os.path.basename(p), UNAV)})
    except json.JSONDecodeError as e:
        alerts.append({"sev": "error", "code": "json_parse", "msg": "%s JSON parse error: %s" % (os.path.basename(p), e)})
    return None


def build():
    t = now_utc(); A = []
    book = read_json("book", A); status = read_json("status", A); staged = read_json("staged", A, required=False)
    compare = read_json("compare", A); cfg = read_json(CONFIG, A); watch = read_json("watch", A, required=False)
    firm_rows = []
    try:
        firm_rows = list(csv.DictReader(open(os.path.join(V2, F["firm"]))))
    except FileNotFoundError:
        A.append({"sev": "error", "code": "missing", "msg": "tfo_v2_firm_equity.csv missing — %s" % UNAV})
    except Exception as e:
        A.append({"sev": "error", "code": "csv_parse", "msg": "firm equity csv parse error: %s" % e})
    facts_ids = set()
    try:
        for i, ln in enumerate(open(os.path.join(V2, F["facts"]))):
            try: facts_ids.add(json.loads(ln).get("ticket"))
            except json.JSONDecodeError: A.append({"sev": "error", "code": "json_parse", "msg": "facts line %d unparseable" % (i + 1)})
    except FileNotFoundError:
        A.append({"sev": "warn", "code": "missing", "msg": "tfo_v2_facts.jsonl missing"})
    out = {"fund": "TFO V2 Systematic — separate paper fund", "paper": "PAPER ONLY — no broker, no real orders",
           "asof_utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "asof_syd": t.astimezone(SYD).strftime("%Y-%m-%d %H:%M:%S %Z")}
    # ---------------- firm
    firm = {k: UNAV for k in ("equity_usd", "seed_usd", "pnl_usd", "pnl_pct", "day_pnl_usd", "day_pnl_pct", "drawdown_pct", "peak_usd", "level", "cash_usd", "deployed_usd", "cash_pct", "deployed_pct")}
    positions, closed = [], []
    if book:
        try:
            mtm = sum(float(p["qty"]) * float(p["mark_usd"]) for p in book["positions"].values() if isinstance(p.get("mark_usd"), (int, float)))
            eq, cash, seed = float(book["equity"]), float(book["cash"]), float(book["seed_usd"])
            firm.update(equity_usd=round(eq, 2), seed_usd=round(seed, 2), pnl_usd=round(eq - seed, 2), pnl_pct=round(100 * (eq / seed - 1), 4),
                        cash_usd=round(cash, 2), deployed_usd=round(mtm, 2), cash_pct=round(100 * cash / eq, 2) if eq else UNAV,
                        deployed_pct=round(100 * mtm / eq, 2) if eq else UNAV, funded_utc=book.get("funded_utc"),
                        v1_match=book.get("v1_match"), fees_paid_usd=book.get("fees_paid_usd"), realised_net_usd=book.get("realised_usd"))
            if abs(eq - (cash + mtm)) > 1.0:
                A.append({"sev": "error", "code": "equity_identity", "msg": "equity %.2f != cash %.2f + MTM %.2f (gap %.2f)" % (eq, cash, mtm, eq - cash - mtm)})
            if cash < 0: A.append({"sev": "error", "code": "negative_cash", "msg": "cash is negative: %.2f" % cash})
            pos_keys, hold_keys = set(book["positions"]), set(book.get("holdings") or {})
            for s in pos_keys ^ hold_keys:
                A.append({"sev": "error", "code": "orphan_symbol", "msg": "%s present in %s only" % (s, "positions" if s in pos_keys else "holdings")})
            for s in set(book.get("mark_px_usd") or {}) - pos_keys:
                A.append({"sev": "error", "code": "orphan_symbol", "msg": "%s has a mark but no position" % s})
            for s, p in book["positions"].items():
                mk = p.get("mark_usd"); st = p.get("stop")
                row = {"symbol": s, "venue": p.get("venue", UNAV), "setup": p.get("setup", UNAV), "qty": p.get("qty"), "fill": p.get("entry_px"),
                       "mark": mk if mk is not None else UNAV, "mark_utc": p.get("mark_utc", UNAV), "mark_age_sec": age(p.get("mark_utc"), t),
                       "tape": p.get("mark_tape", UNAV), "half_banked": bool(p.get("banked")), "stop": st if st else UNAV,
                       "init_stop": p.get("init_stop"), "bank_target": p.get("bank_target_px"), "entry_utc": p.get("entry_utc")}
                if isinstance(mk, (int, float)):
                    ef = float(p.get("entry_fees_usd") or 0) * (float(p["qty"]) / float(p.get("qty0") or p["qty"]))
                    realised_part = sum(float(x.get("net_pnl_usd") or 0) for x in p.get("exit_fills") or [])
                    unreal = float(p["qty"]) * (mk - float(p["entry_px"]))
                    row["pnl_usd_net"] = round(unreal - ef + realised_part - (float(p.get("entry_fees_usd") or 0) - ef), 2)
                    row["pnl_pct_net"] = round(100 * row["pnl_usd_net"] / (float(p.get("qty0") or p["qty"]) * float(p["entry_px"])), 3)
                    row["dist_to_stop_pct"] = round(100 * (mk / st - 1), 3) if st else UNAV
                else:
                    row.update(pnl_usd_net=UNAV, pnl_pct_net=UNAV, dist_to_stop_pct=UNAV)
                    A.append({"sev": "error", "code": "no_mark", "msg": "%s has no mark" % s})
                if not st or st <= 0: A.append({"sev": "error", "code": "no_stop", "msg": "%s has no stop" % s})
                ma = row["mark_age_sec"]
                lim = 1200
                need_live = p.get("venue") == "KRAKEN" or us_open(t)
                row["current"] = bool(ma is not None and (ma <= lim or not need_live))
                row["mark_note"] = None if need_live else "last mark while US market shut"
                if ma is None: A.append({"sev": "error", "code": "stale_mark", "msg": "%s mark time unavailable" % s})
                elif (p.get("venue") == "KRAKEN" or us_open(t)) and ma > lim:
                    A.append({"sev": "error", "code": "stale_mark", "msg": "%s mark %.0f s old (> %d s cadence)" % (s, ma, lim)})
                positions.append(row)
            for tk in book.get("tickets") or []:
                if "fees_usd" not in tk: A.append({"sev": "error", "code": "fees_missing", "msg": "fill %s has no fees_usd" % tk.get("ticket_id")})
                if tk.get("tape") != "LIVE": A.append({"sev": "error", "code": "non_live_fill", "msg": "fill %s tape %s" % (tk.get("ticket_id"), tk.get("tape"))})
                if facts_ids and tk.get("ticket_id") not in facts_ids: A.append({"sev": "warn", "code": "facts_gap", "msg": "fill %s has no facts line" % tk.get("ticket_id")})
            for c in (book.get("closed") or [])[-200:]:
                closed.append({k: c.get(k) for k in ("sym", "setup", "entry_utc", "closed_utc", "reason", "entry_px", "qty0", "gross_pnl_usd", "fees_usd", "net_pnl_usd", "ret_net_pct")})
        except Exception as e:
            A.append({"sev": "error", "code": "book_shape", "msg": "book unreadable: %s: %s" % (type(e).__name__, e)})
    if firm_rows:
        last = firm_rows[-1]
        try:
            firm.update(day_pnl_usd=float(last["day_pnl_usd"]), day_pnl_pct=float(last["day_pnl_pct"]), drawdown_pct=float(last["drawdown_pct"]),
                        peak_usd=float(last["peak_usd"]), level=last["level"], firm_row_utc=last["utc"])
            if age(last["utc"], t) is None or age(last["utc"], t) > CADENCE["firm_csv"]:
                A.append({"sev": "error", "code": "stale_firm_row", "msg": "V2 firm equity row %s s old" % age(last["utc"], t)})
            if last["level"] not in ("OK",):
                A.append({"sev": "warn", "code": "firm_rail", "msg": "V2 firm rail level %s (dd %s%%, day %s%%)" % (last["level"], last["drawdown_pct"], last["day_pnl_pct"])})
        except Exception as e:
            A.append({"sev": "error", "code": "csv_parse", "msg": "firm row unreadable: %s" % e})
    out["firm"] = firm; out["positions"] = positions; out["closed"] = list(reversed(closed))
    out["equity_series"] = [{"utc": r["utc"], "equity": float(r["equity_usd"])} for r in firm_rows[-500:]] if firm_rows else []
    # ---------------- heartbeats
    hb = []
    for k, lab in RUNNER_LABEL.items():
        r = ((status or {}).get("runners") or {}).get(k)
        if not r:
            hb.append({"runner": lab, "last_run_utc": UNAV, "age_sec": None, "ok": None, "errors": []})
            A.append({"sev": "error", "code": "heartbeat_missing", "msg": "%s has never reported" % lab}); continue
        a = age(r.get("last_run_utc"), t)
        hb.append({"runner": lab, "last_run_utc": r.get("last_run_utc"), "age_sec": a, "ok": r.get("ok"), "errors": r.get("errors") or [],
                   "last_ok_utc": r.get("last_ok_utc"), "mode": r.get("mode"),
                   "status": r.get("status") or ("ok" if r.get("ok") else ("error" if r.get("ok") is False else UNAV))})
        if a is None or a > CADENCE[k]: A.append({"sev": "error", "code": "heartbeat_stale", "msg": "%s last ran %s s ago (cadence %d s)" % (lab, a, CADENCE[k])})
        if r.get("ok") is False: A.append({"sev": "error", "code": "runner_error", "msg": "%s: %s" % (lab, "; ".join(r.get("errors") or [])[:300])})
        if k in ("rails", "enter") and r.get("mode") == "preview": A.append({"sev": "warn", "code": "preview_mode", "msg": "%s ran in PREVIEW (no --execute)" % lab})
    out["heartbeats"] = hb
    # ---------------- data coverage (NEUTRAL by design: a degraded setup never adds an alert, never flips /healthz)
    cov = []
    for k, v in sorted(((status or {}).get("data_coverage") or {}).items()):
        if not isinstance(v, dict): continue
        sh = v.get("failed_share")
        cov.append({"setup": k, "status": v.get("status", UNAV), "universe": v.get("universe", UNAV),
                    "failed_first_pass": v.get("failed_first_pass", UNAV), "failed": v.get("failed", UNAV),
                    "failed_share_pct": round(100 * sh, 2) if isinstance(sh, (int, float)) else UNAV,
                    "recovered_by_retry": v.get("recovered_by_retry", UNAV), "retry_attempts": v.get("retry_attempts", UNAV),
                    "failed_sample": v.get("failed_sample") or [], "utc": v.get("utc", UNAV), "age_sec": age(v.get("utc"), t),
                    "source": v.get("source", UNAV), "fetches": v.get("fetches") or []})
    out["data_coverage"] = cov
    out["data_degraded"] = [c["setup"] for c in cov if c["status"] == "degraded"]
    out["coverage_threshold"] = (status or {}).get("coverage_threshold") or UNAV
    # ---------------- setups
    setups = []
    for k, v in ((cfg or {}).get("setups") or {}).items():
        cl = [c for c in closed if c.get("setup") == k]; nets = [float(c["net_pnl_usd"]) for c in cl if isinstance(c.get("net_pnl_usd"), (int, float))]
        bt = v.get("backtest") or {}
        setups.append({"setup": k, "name": v.get("name"), "enabled": bool(v.get("enabled")), "disabled_reason": v.get("disabled_reason"),
                       "review_note": v.get("review_note"), "bt_t_stat_net": bt.get("t_stat_net", UNAV),
                       "live_trades": len(nets), "live_win_rate_pct": round(100 * sum(1 for x in nets if x > 0) / len(nets), 1) if nets else UNAV,
                       "live_expectancy_net_usd": round(sum(nets) / len(nets), 2) if nets else UNAV,
                       "open": sum(1 for p in positions if p["setup"] == k),
                       "bt_trades": bt.get("trades", UNAV), "bt_expectancy_net_pct": bt.get("expectancy_net_pct", UNAV),
                       "bt_expectancy_net_usd": bt.get("expectancy_net_usd", UNAV), "bt_expectancy_gross_pct": bt.get("expectancy_gross_pct", UNAV),
                       "bt_profit_factor_net": bt.get("profit_factor_net", UNAV), "bt_sharpe": bt.get("sharpe", UNAV), "bt_max_dd_pct": bt.get("max_dd_pct", UNAV),
                       "bt_cagr_pct": bt.get("cagr_pct", UNAV), "bt_period": bt.get("period", UNAV), "bt_oos_expectancy_net_pct": bt.get("oos_expectancy_net_pct", UNAV)})
    if cfg is not None and not setups: A.append({"sev": "error", "code": "config", "msg": "tfo_v2_config.json has no setups"})
    out["setups"] = setups
    out["staged"] = [o for o in ((staged or {}).get("orders") or []) if o.get("status") == "STAGED"]
    out["exit_flags"] = (staged or {}).get("exit_flags") or {}
    # ---------------- watchlist (read-only view of current v2-signals candidates = STAGED orders in tfo_v2_staged.json)
    out["watchlist"] = [{"setup": o.get("setup", UNAV), "symbol": o.get("sym", UNAV),
                         "trigger": o["trigger"] if isinstance(o.get("trigger"), (int, float)) else (o["ref_close"] if isinstance(o.get("ref_close"), (int, float)) else UNAV),
                         "trigger_basis": "break of level" if isinstance(o.get("trigger"), (int, float)) else ("signal close (enters at live ask)" if isinstance(o.get("ref_close"), (int, float)) else UNAV),
                         "planned_size_usd": o["indicative_usd"] if isinstance(o.get("indicative_usd"), (int, float)) else UNAV,
                         "planned_units": o["indicative_units"] if isinstance(o.get("indicative_units"), (int, float)) else UNAV,
                         "planned_label": o.get("indicative_label") or "indicative, final size at live ask", "planned_note": o.get("indicative_note"),
                         "signal_utc": o.get("staged_utc", UNAV), "valid_until_utc": o.get("valid_until_utc", UNAV)} for o in out["staged"]]
    # ---------------- compare
    if compare:
        ca = age(compare.get("asof_utc"), t)
        out["compare"] = {"asof_utc": compare.get("asof_utc"), "age_sec": ca, "common_base": compare.get("common_base") or UNAV,
                          "v1_firm": {k: (compare.get("v1_firm") or {}).get(k, UNAV) for k in ("equity_usd", "net_pnl_usd", "return_on_common_base_pct", "max_dd_pct", "daily_sharpe_ann", "asof_utc", "fees_usd")},
                          "v1_fees": {k: ((compare.get("v1_firm") or {}).get("booked_fees") or {}).get(k, UNAV) for k in ("fees_booked_usd", "fills_with_booked_fee", "backfill_usd", "backfill_fills", "backfill_tag", "risk_cost_backfills_total_usd", "booked_since_patch_usd", "booked_since_patch_fills", "basis")},
                          "v1_recon": {k: ((compare.get("v1_firm") or {}).get("reconciliation") or {}).get(k, UNAV) for k in ("firm_risk_utc", "firm_risk_equity_usd", "diff_usd", "compare_is_latest_firm_risk_row", "backfill_paired_transfers_net_usd", "capital_accounting_valid")},
                          "v2_firm": {k: (compare.get("v2_firm") or {}).get(k, UNAV) for k in ("equity_usd", "net_pnl_usd", "return_on_common_base_pct", "return_on_avg_deployed_pct", "max_dd_pct", "daily_sharpe_ann", "trades", "hit_rate_pct", "expectancy_usd", "fees_usd")},
                          "v1_ht": {k: (compare.get("v1_head_trader_since_v2_funding") or {}).get(k, UNAV) for k in ("net_pnl_usd", "return_on_common_base_pct", "return_on_avg_deployed_pct", "realised_clip_net_usd", "realised_legacy_exits_net_usd", "unrealised_open_clips_usd", "fees_usd", "tickets")},
                          "notes": compare.get("notes") or [], "status": compare.get("status")}
        if ca is None or ca > CADENCE["compare"]: A.append({"sev": "error", "code": "stale_compare", "msg": "tfo_compare.json %s s old" % ca})
    else:
        out["compare"] = UNAV
    # ---------------- freshness badge
    src = {"v2-rails": next((h["age_sec"] for h in hb if h["runner"] == "v2-rails"), None), "v2-enter": next((h["age_sec"] for h in hb if h["runner"] == "v2-enter"), None),
           "v2-signals": next((h["age_sec"] for h in hb if h["runner"] == "v2-signals"), None), "v2-mark-compare": next((h["age_sec"] for h in hb if h["runner"] == "v2-mark-compare"), None),
           "firm_csv": age(firm_rows[-1]["utc"], t) if firm_rows else None, "compare": out["compare"]["age_sec"] if isinstance(out["compare"], dict) else None}
    cad = {"v2-rails": CADENCE["rails"], "v2-enter": CADENCE["enter"], "v2-signals": CADENCE["signals"], "v2-mark-compare": CADENCE["mark"], "firm_csv": CADENCE["firm_csv"], "compare": CADENCE["compare"]}
    stale = []; worst = "green"
    for k, a in src.items():
        if a is None or a > 3 * cad[k]: stale.append("%s (%s)" % (k, "missing" if a is None else "%ds" % a)); worst = "red"
        elif a > cad[k]:
            stale.append("%s (%ds)" % (k, a)); worst = "amber" if worst == "green" else worst
    out["freshness"] = {"badge": worst, "stale_sources": stale, "ages_sec": src, "cadence_sec": cad}
    # ---------------- per-section currency: a panel whose source is older than its cadence renders 'No current data'
    def sec(src_name, a, cadence, ok=True):
        return {"source": src_name, "age_sec": a, "cadence_sec": cadence, "current": bool(ok and a is not None and a <= cadence)}
    fa = src["firm_csv"]; book_ok = bool(book)
    sig_age = src["v2-signals"]
    out["sections"] = {
        "firm": sec("tfo_v2_firm_equity.csv (written by v2-mark-compare)", fa, CADENCE["firm_csv"], book_ok),
        "positions": sec("tfo_v2_book.json marks (v2-mark-compare / v2-rails)", fa, CADENCE["firm_csv"], book_ok),
        "closed": sec("tfo_v2_book.json", fa, CADENCE["firm_csv"], book_ok),
        "setups_live": sec("tfo_v2_book.json closed trades", fa, CADENCE["firm_csv"], book_ok),
        "staged": sec("tfo_v2_staged.json (v2-signals)", sig_age, CADENCE["signals"], staged is not None),
        "watchlist": sec("tfo_v2_staged.json (v2-signals)", sig_age, CADENCE["signals"], staged is not None),
        "heartbeats": {"source": "tfo_v2_status.json", "age_sec": 0, "cadence_sec": None, "current": status is not None},
        "coverage": {"source": "tfo_v2_status.json data_coverage (v2-signals / coverage probe)", "age_sec": 0, "cadence_sec": None, "current": status is not None},
        "compare": sec("tfo_compare.json (v2-mark-compare)", src["compare"], CADENCE["compare"], isinstance(out["compare"], dict)),
        "backtest": {"source": "tfo_v2_config.json (static research)", "age_sec": None, "cadence_sec": None, "current": cfg is not None},
    }
    out["watcher"] = watch or UNAV
    out["alerts"] = A
    out["clean"] = not any(a["sev"] == "error" for a in A)
    return out


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TFO V2 — Systematic (PAPER)</title>
<style>body{font:13px -apple-system,Helvetica,Arial,sans-serif;background:#0f1419;color:#d8dee4;margin:0;padding:14px}
h1{font-size:18px;margin:0}h2{font-size:14px;margin:18px 0 6px;color:#9fb3c8;border-bottom:1px solid #253040}
table{border-collapse:collapse;width:100%}td,th{padding:3px 6px;border-bottom:1px solid #1e2833;text-align:right;white-space:nowrap}
th{color:#8aa0b6;font-weight:600}td:first-child,th:first-child{text-align:left}.pos{color:#4cd07d}.neg{color:#ff6b6b}.muted{color:#71839a}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-weight:700;margin-left:8px}.green{background:#1d6b3a}.amber{background:#8a6d12}.red{background:#8a1f1f}
.paper{background:#3a2d6b;padding:2px 8px;border-radius:4px;margin-left:8px}.grid{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}
.card{background:#16202a;padding:8px;border-radius:6px}.card b{display:block;font-size:16px;margin-top:2px}
.chip{display:inline-block;padding:1px 8px;border-radius:10px;background:#253040;color:#c8d3de;font-size:12px;margin-left:8px;cursor:pointer;user-select:none}
#alist{display:none;margin:6px 0 4px;padding:6px 10px;background:#141c25;border:1px solid #253040;border-radius:6px;font-size:12px;max-width:900px}
#alist.open{display:block}#alist div{padding:1px 0}.sev{color:#8aa0b6;display:inline-block;width:44px}
.warn{background:#3a3320;padding:0 3px;border-radius:3px}.neutral{background:#5c6370;color:#eef1f4}.ua{color:#ff9f43;font-style:italic}.nocur{color:#71839a;font-style:italic;padding:4px 0}.grid>.nocur{grid-column:1/-1}@media(max-width:700px){table{display:block;max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}}</style></head><body>
<div id="hdr"></div><div id="alist"></div><div id="firm" class="grid"></div>
<h2>Open positions</h2><div id="pos"></div><h2>Staged orders / exit flags</h2><div id="staged"></div>
<h2>Watchlist (v2-signals candidates, read-only)</h2><div id="watch"></div>
<h2>Runner heartbeats</h2><div id="hb"></div>
<h2>Data coverage (price downloads after retries; DEGRADED is informational, not an error)</h2><div id="cov"></div><h2>Per-setup stats (live vs backtest, NET of desk_costs)</h2><div id="setups"></div>
<h2>V1 vs V2 (common base)</h2><div id="cmp"></div><h2>Closed trades (with fees)</h2><div id="closed"></div>
<h2>Backtest summary (V2-law variant, NET of desk_costs + half-spread; from tfo_v2_config.json / BACKTEST_REPORT.md)</h2><div id="bt"></div>
<script>
const UA='unavailable';
function f(v,d=2){if(typeof d!=='number'||!(d>=0&&d<=6))d=2;if(v===null||v===undefined||v===UA)return '<span class="ua">unavailable</span>';if(typeof v==='number')return v.toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});return String(v)}
function pn(v,d=2){if(typeof d!=='number'||!(d>=0&&d<=6))d=2;if(typeof v!=='number')return f(v,d);return '<span class="'+(v>=0?'pos':'neg')+'">'+f(v,d)+'</span>'}
function tbl(rows,cols){if(!rows||!rows.length)return '<div class="muted">none</div>';let h='<table><tr>'+cols.map(c=>'<th>'+c[1]+'</th>').join('')+'</tr>';
for(const r of rows){h+='<tr>'+cols.map(c=>'<td>'+(c[2]?c[2](r[c[0]],r):f(r[c[0]]))+'</td>').join('')+'</tr>'}return h+'</table>'}
function card(l,v){return '<div class="card">'+l+'<b>'+v+'</b></div>'}
const NOCUR='<span class="nocur">No current data</span>';
function gate(d,name,fn){const s=(d.sections||{})[name];if(!s||!s.current){return '<div class="nocur">No current data'+(s&&s.source?' — '+s.source+(s.age_sec!=null?' is '+Math.round(s.age_sec)+' s old':' unavailable')+(s.cadence_sec?' (cadence '+s.cadence_sec+' s)':''):'')+'</div>'}return fn()}
function toggleHealth(){document.getElementById('alist').classList.toggle('open')}
const IDS=['firm','pos','staged','watch','hb','cov','setups','cmp','closed','bt'];
function covtxt(c){return c.setup+' '+c.failed+'/'+c.universe+' failed ('+f(c.failed_share_pct,1)+'%)'}
function stchip(v){return v==='degraded'?'<span class="badge neutral" title="partial price downloads after retries: informational, not an error">DEGRADED (data)</span>':(v==='error'?'<span class=neg>ERROR</span>':(v==='ok'?'ok':f(v)))}
async function load(){let d;try{d=await (await fetch('/api/all',{cache:'no-store'})).json()}catch(e){
document.getElementById('hdr').innerHTML='<h1>TFO V2<span class="chip" onclick="toggleHealth()">Health: dashboard API unreachable</span></h1>';
document.getElementById('alist').innerHTML='<div><span class="sev">ERROR</span>api_unreachable: '+String(e).replace(/</g,'&lt;')+'</div>';
for(const i of IDS)document.getElementById(i).innerHTML='<div class="nocur">No current data</div>';return}
const fr=d.freshness;document.getElementById('hdr').innerHTML='<h1>'+d.fund+'<span class="paper">'+d.paper+'</span><span class="badge '+fr.badge+'">DATA '+fr.badge.toUpperCase()+'</span><span class="chip" id="hchip" onclick="toggleHealth()" title="click to show / hide the checks">Health: '+(d.alerts.length?d.alerts.length+' check'+(d.alerts.length>1?'s':'')+' pending':'OK')+'</span>'+((d.data_degraded||[]).length?'<span class="badge neutral" title="informational: partial price downloads after retries; does not affect Health">DATA COVERAGE DEGRADED: '+(d.data_coverage||[]).filter(c=>c.status==='degraded').map(covtxt).join('; ')+'</span>':'')+'</h1><div class="muted">as of '+d.asof_syd+' ('+d.asof_utc+')'+(fr.stale_sources.length?' — stale: '+fr.stale_sources.join(', '):' — all sources inside cadence')+'</div>';
document.getElementById('alist').innerHTML=d.alerts.map(a=>'<div><span class="sev">'+a.sev.toUpperCase()+'</span>'+a.code+': '+String(a.msg).replace(/</g,'&lt;')+'</div>').join('')||'<div class="muted">All clean-data checks pass.</div>';
const m=d.firm;document.getElementById('firm').innerHTML=gate(d,'firm',()=>card('Equity',f(m.equity_usd))+card('Seed',f(m.seed_usd))+card('Total P&L',pn(m.pnl_usd)+' ('+pn(m.pnl_pct,3)+'%)')+card('Day P&L',pn(m.day_pnl_usd)+' ('+pn(m.day_pnl_pct,3)+'%)')+card('Drawdown vs peak',pn(m.drawdown_pct,3)+'% (peak '+f(m.peak_usd)+')')+card('Rail state',f(m.level))+card('Cash',f(m.cash_usd)+' ('+f(m.cash_pct)+'%)')+card('Deployed (MTM)',f(m.deployed_usd)+' ('+f(m.deployed_pct)+'%)')+card('Fees paid',f(m.fees_paid_usd))+card('Realised (net of ALL fees paid, incl. entry fees on open positions)',pn(m.realised_net_usd))+card('Funded',f(m.funded_utc))+card('V1 match',m.v1_match?f(m.v1_match.v1_firm_equity_usd)+' @ '+m.v1_match.v1_asof_utc:UA));
document.getElementById('pos').innerHTML=gate(d,'positions',()=>tbl(d.positions,[['symbol','Symbol'],['venue','Venue'],['setup','Setup'],['qty','Qty',v=>f(v,6)],['fill','Fill',v=>f(v,6)],['mark','Mark',(v,r)=>r.current===false?NOCUR:f(v,6)+(r.mark_note?' <span class=muted>('+r.mark_note+')</span>':'')],['mark_utc','Mark time',(v,r)=>r.current===false?NOCUR:f(v)],['tape','Tape',(v,r)=>r.current===false?NOCUR:f(v)],['pnl_usd_net','P&L $ net',(v,r)=>r.current===false?NOCUR:pn(v)],['pnl_pct_net','P&L % net',(v,r)=>r.current===false?NOCUR:pn(v,3)],['half_banked','Half banked',v=>v?'YES':'no'],['stop','Stop/trail',v=>f(v,6)],['dist_to_stop_pct','Dist to stop %',(v,r)=>r.current===false?NOCUR:f(v,2)]]));
document.getElementById('staged').innerHTML=gate(d,'staged',()=>tbl(d.staged,[['setup','Setup'],['sym','Symbol'],['score','Score',v=>f(v,3)],['valid_until_utc','Valid until'],['why','Why']])+'<div class="muted">exit flags: '+(Object.keys(d.exit_flags).map(k=>k+' ('+d.exit_flags[k].reason+')').join('; ')||'none')+'</div>');
document.getElementById('watch').innerHTML=gate(d,'watchlist',()=>(d.watchlist||[]).length?tbl(d.watchlist,[['setup','Setup'],['symbol','Symbol'],['trigger','Trigger level',(v,r)=>f(v,6)+(r.trigger_basis&&r.trigger_basis!==UA?' <span class=muted>('+r.trigger_basis+')</span>':'')],['planned_size_usd','Planned size $',(v,r)=>v===UA?'<span class="ua">unavailable</span> <span class=muted>('+(r.planned_note||'sized by v2-enter at live ask')+')</span>':f(v)+(r.planned_units!==UA?' / '+f(r.planned_units,6)+' units':'')+' <span class=muted>('+r.planned_label+')</span>'],['signal_utc','Signal time'],['valid_until_utc','Valid until']]):'<div class="muted">No candidates from v2-signals right now.</div>');
document.getElementById('hb').innerHTML=gate(d,'heartbeats',()=>tbl(d.heartbeats,[['runner','Runner'],['last_run_utc','Last run'],['age_sec','Age s',v=>f(v,0)],['ok','OK',v=>v===true?'yes':(v===false?'<span class=neg>NO</span>':f(v))],['status','Status',stchip],['mode','Mode',v=>v||'n/a'],['errors','Errors',v=>(v||[]).join('; ')]]));
document.getElementById('cov').innerHTML=gate(d,'coverage',()=>(d.data_coverage||[]).length?tbl(d.data_coverage,[['setup','Setup'],['status','Status',stchip],['universe','Universe',v=>f(v,0)],['failed_first_pass','Failed 1st pass',v=>f(v,0)],['failed','Failed after retries',v=>f(v,0)],['failed_share_pct','Failed %',v=>f(v,2)],['recovered_by_retry','Recovered by retry',v=>f(v,0)],['retry_attempts','Retry passes',v=>f(v,0)],['failed_sample','Sample failed',v=>(v||[]).join(', ')||'none'],['utc','As of'],['source','Source']])+'<div class="muted">threshold: degraded when failed share &gt; '+(d.coverage_threshold&&d.coverage_threshold.degraded_if_failed_share_gt!=null?100*d.coverage_threshold.degraded_if_failed_share_gt+'%':'5%')+' of the universe or &ge; '+(d.coverage_threshold&&d.coverage_threshold.or_failed_count_gte||25)+' tickers; '+((d.coverage_threshold||{}).retry_scheme||'')+'</div>':'<div class="muted">No coverage recorded yet (next setup fetch or `tfo_v2.py coverage`).</div>');
const LV=!!((d.sections||{}).setups_live||{}).current;document.getElementById('setups').innerHTML=tbl(d.setups,[['setup','Setup'],['name','Name'],['enabled','State',(v,r)=>(v?'<span class=pos>ENABLED</span>':'<span class=neg>DISABLED</span>')+(r.review_note?' <span class=warn>REVIEW</span>':'')],['open','Open',v=>LV?f(v,0):NOCUR],['live_trades','Live trades',v=>LV?f(v,0):NOCUR],['live_win_rate_pct','Live win %',v=>LV?f(v,1):NOCUR],['live_expectancy_net_usd','Live exp $ net',v=>LV?pn(v):NOCUR],['bt_expectancy_net_pct','BT exp % net',v=>pn(v,3)],['bt_expectancy_net_usd','BT exp $ net',v=>pn(v)],['disabled_reason','Why disabled / review',(v,r)=>'<div style="white-space:normal;max-width:520px;text-align:left"><span class=muted>'+(v||'')+'</span>'+(r.review_note?'<span class=warn>'+r.review_note+'</span>':'')+'</div>']]);
document.getElementById('bt').innerHTML=gate(d,'backtest',()=>tbl(d.setups,[['setup','Setup'],['name','Name'],['enabled','State',v=>v?'<span class=pos>ENABLED</span>':'<span class=neg>DISABLED</span>'],['bt_period','Period'],['bt_trades','Trades',v=>f(v,0)],['bt_expectancy_net_pct','Exp % net',v=>pn(v,3)],['bt_expectancy_gross_pct','Exp % gross',v=>pn(v,3)],['bt_expectancy_net_usd','Exp $ net',v=>pn(v)],['bt_profit_factor_net','PF net',v=>f(v,2)],['bt_t_stat_net','t (net)',v=>f(v,2)],['bt_cagr_pct','CAGR %',v=>f(v,2)],['bt_sharpe','Sharpe',v=>f(v,2)],['bt_max_dd_pct','Max DD %',v=>pn(v,2)],['bt_oos_expectancy_net_pct','OOS exp % net',v=>pn(v,3)]]));
const c=d.compare;if(!((d.sections||{}).compare||{}).current){document.getElementById('cmp').innerHTML=gate(d,'compare',()=>'')}else if(c===UA||!c){document.getElementById('cmp').innerHTML='<span class="ua">unavailable</span>'}else{const cb=c.common_base;
document.getElementById('cmp').innerHTML='<div class="muted">common base '+f(cb.usd)+' = V1 firm equity at '+f(cb.v1_asof_utc)+'; V2 funded '+f(cb.v2_funded_utc)+'; compare as of '+f(c.asof_utc)+'</div>'+
tbl([{k:'V1 firm (all books)',...c.v1_firm},{k:'V2 firm',...c.v2_firm},{k:'V1 Head-Trader clips only',...c.v1_ht}],[['k','View'],['equity_usd','Equity'],['net_pnl_usd','Net P&L $',pn],['return_on_common_base_pct','% on common base',v=>pn(v,3)],['return_on_avg_deployed_pct','% on avg deployed',v=>pn(v,3)],['max_dd_pct','Max DD %',v=>f(v,2)],['daily_sharpe_ann','Daily Sharpe',v=>f(v,2)],['fees_usd','Fees (booked)',v=>f(v,2)]])+
(c.v1_fees&&c.v1_fees.fees_booked_usd!==UA?'<div class="muted">V1 fees BOOKED (no estimates): '+f(c.v1_fees.fees_booked_usd)+' over '+f(c.v1_fees.fills_with_booked_fee,0)+' fills = TFO Risk backfill '+f(c.v1_fees.backfill_usd)+' ('+f(c.v1_fees.backfill_fills,0)+' fills, '+c.v1_fees.backfill_tag+'; Risk summary '+f(c.v1_fees.risk_cost_backfills_total_usd)+') + booked since 06:40 AEST 26 Sep '+f(c.v1_fees.booked_since_patch_usd)+' ('+f(c.v1_fees.booked_since_patch_fills,0)+' fills). V1 HT row fees are the booked fees of HT fills since V2 funding.</div>':'')+
(c.v1_recon&&c.v1_recon.firm_risk_equity_usd!==UA?'<div class="muted">V1 equity vs firm_risk: '+f(c.v1_recon.firm_risk_equity_usd)+' @ '+f(c.v1_recon.firm_risk_utc)+', diff '+f(c.v1_recon.diff_usd)+(c.v1_recon.compare_is_latest_firm_risk_row===true?' (compare row = firm_risk row)':'')+'; paired powder transfer for the crypto backfill nets '+f(c.v1_recon.backfill_paired_transfers_net_usd)+' (capital movement, not P&L)</div>':'')+
'<div class="muted">'+(c.notes||[]).map(n=>'• '+n).join('<br>')+'</div>'}
document.getElementById('closed').innerHTML=gate(d,'closed',()=>tbl(d.closed,[['closed_utc','Closed'],['sym','Symbol'],['setup','Setup'],['reason','Exit'],['entry_px','Fill',v=>f(v,6)],['gross_pnl_usd','Gross $',pn],['fees_usd','Fees $'],['net_pnl_usd','Net $',pn],['ret_net_pct','Net %',v=>pn(v,3)]]));}
load();setInterval(load,30000);
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path == "/":
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if path == "/api/all":
                return self._send(200, json.dumps(build(), default=str), "application/json")
            if path == "/healthz":
                d = build(); errs = [a for a in d["alerts"] if a["sev"] == "error"]
                return self._send(200 if not errs else 503, json.dumps({"ok": not errs, "asof_utc": d["asof_utc"], "errors": errs,
                                  "freshness": d["freshness"]["badge"]}), "application/json")
            return self._send(404, json.dumps({"error": "not found"}), "application/json")
        except Exception as e:
            return self._send(500, json.dumps({"error": "%s: %s" % (type(e).__name__, e)}), "application/json")

    def do_POST(self): self._send(405, "read-only", "text/plain")
    do_PUT = do_DELETE = do_PATCH = do_POST

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), fmt % args))


def main():
    if "--dump" in sys.argv:
        print(json.dumps(build(), indent=1, default=str)); return
    srv = ThreadingHTTPServer((HOST, PORT), H)
    print("TFO V2 dashboard on http://%s:%d/ (read-only, PAPER)" % (HOST, PORT), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
