#!/usr/bin/env python3
"""tfo_compare.py — daily V1 vs V2 comparison. READ-ONLY on every V1 file. PAPER ONLY.

Common base (funder spec 2026-09-25): V2 was seeded with exactly V1 firm equity at V2's funding
timestamp. Both firms' % returns are measured from THAT number:
  V1 FIRM   = reports/firm_equity.csv (all V1 books), from the row matched at V2 funding.
  V2 FIRM   = reports/v2/tfo_v2_firm_equity.csv / tfo_v2_book.json.
  V1 HT     = Head-Trader-only view: tickets in tfo_ht_crypto.json, fundamental_book.json (ht_clip entries
              and HT exits on legacy books), fundamental_spec_book.json, convexity_sleeve.json, cross-checked
              against reports/tfo_head_trader_facts.jsonl. V1 HT capital is NOT a fixed allocation (clips are
              funded ticket-by-ticket from powder), so % is shown on (a) the common base and (b) average
              deployed capital. Legacy-book exits realise P&L mostly accrued BEFORE the HT mandate; they are
              reported separately from HT-originated clips.
  V1 FEES   = BOOKED figures only (2026-09-26): each fill's fee as written in the V1 ledgers (position entry_fees_usd,
              exit-fill / closed-record fees_usd). Fills before the desk_costs patch carry the TFO Risk backfill
              (fee_backfill.tag V1_FEE_BACKFILL_20260926); fills after 06:40 AEST 26 Sep carry their own booked fee.
              Each fill counted once; no estimates. The HT LOG 13th field (fees_usd) is a cross-check only.
Writes ONLY reports/v2/tfo_compare.json, reports/v2/tfo_compare_daily.csv and the V1-HT snapshot series
reports/v2/tfo_compare_v1ht_series.csv (needed because V1 HT has no MTM history of its own).
"""
import csv, datetime as dt, json, math, os, sys, tempfile
import desk_costs
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("DESK") or os.environ.get("BASE") or HERE
R = os.path.join(BASE, "reports"); V2 = os.path.join(R, "v2")
SYD, UTC = ZoneInfo("Australia/Sydney"), dt.timezone.utc
HT_MANDATE_START = "2026-09-24T14:00:00Z"          # 2026-09-25 00:00 Sydney
OUT_JSON = os.path.join(V2, "tfo_compare.json"); OUT_CSV = os.path.join(V2, "tfo_compare_daily.csv")
HT_SERIES = os.path.join(V2, "tfo_compare_v1ht_series.csv")
V1_TICKET_BOOKS = ["tfo_ht_crypto", "fundamental_book", "fundamental_spec_book", "convexity_sleeve"]
FEE_BACKFILL_TAG = "V1_FEE_BACKFILL_20260926"      # TFO Risk historical fee backfill, booked 2026-09-26T06:18:47Z
V1_FEE_PATCH_UTC = "2026-09-25T20:40:00Z"          # 06:40 AEST 26 Sep: new V1 fills carry desk_costs fees_usd from here
HT_LOG = os.path.join(R, "tfo_head_trader_log.txt")
# 2026-10-30 paidfeeds: V2 US data source switched Yahoo -> paid feeds (Massive quotes/bars, FMP+EODHD history/earnings,
# Yahoo fallback only). SET THIS AT DEPLOY to the UTC minute the new tfo_v2.py went live (see backups/v2_paidfeeds_staging/DEPLOY_NOTE.md).
V2_DATA_SOURCE_SWITCH_UTC = os.environ.get("TFO_V2_DATA_SOURCE_SWITCH_UTC") or "2026-10-30T14:56:05Z"
V2_DATA_SOURCE_CHANGE = "V2 US prices/history: Yahoo (yfinance) -> Massive first, then EODHD/FMP, Yahoo fallback only; crypto Kraken unchanged"
_SKIP = ("head_trader_tickets", "entry_ticket", "quote", "fee_backfill", "cost_backfills", "capital_events")   # ticket copies / metadata


def now_utc():
    o = os.environ.get("TFO_V2_NOW")
    return dt.datetime.fromisoformat(o.replace("Z", "+00:00")).astimezone(UTC) if o else dt.datetime.now(UTC)


def parse(s): return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(UTC)
def iso(t): return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
def sydday(t): return parse(t).astimezone(SYD).strftime("%Y-%m-%d") if isinstance(t, str) else t.astimezone(SYD).strftime("%Y-%m-%d")


def est_fee(t):
    """Estimate one unbooked fill fee for comparison/report tests; never raises."""
    try:
        when = parse(t["utc"])
        sym = t.get("sym") or t.get("symbol")
        qty = float(t.get("qty") or 0)
        px = float(t.get("px_usd") or t.get("fill_px_usd") or 0)
        if not sym or qty <= 0 or px <= 0:
            return 0.0
        return float(desk_costs.fill_fees(sym, desk_costs.side_for(t.get("action") or "BUY", t.get("side") or "long"), qty, px, t.get("px_native"), t.get("ccy"), when=when)["fees_usd"])
    except Exception:
        return 0.0


def jl(path, default=None):
    try: return json.load(open(path))
    except (FileNotFoundError, json.JSONDecodeError): return default


def rows(path):
    try: return list(csv.DictReader(open(path)))
    except FileNotFoundError: return []


def atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix="." + os.path.basename(path), suffix=".tmp")
    with os.fdopen(fd, "w") as f: f.write(text)
    os.replace(tmp, path)


def series_stats(daily_eq, base):
    """daily_eq: [(syd_date, equity)] last observation per Sydney day."""
    out = {"days": len(daily_eq)}
    if not daily_eq: return out
    eqs = [base] + [e for _, e in daily_eq]
    pnl = [b - a for a, b in zip(eqs[:-1], eqs[1:])]
    rets = [(b / a - 1) for a, b in zip(eqs[:-1], eqs[1:]) if a]
    peak, mdd = eqs[0], 0.0
    for e in eqs:
        peak = max(peak, e); mdd = min(mdd, e / peak - 1)
    sd = (sum((r - sum(rets) / len(rets)) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5 if len(rets) > 1 else 0.0
    out.update(daily_pnl=[{"date": d, "pnl_usd": round(p, 2)} for (d, _), p in zip(daily_eq, pnl)],
               daily_sharpe_ann=round((sum(rets) / len(rets)) / sd * math.sqrt(365), 2) if sd > 0 else None,
               max_dd_pct=round(100 * mdd, 3), sharpe_note="calendar-day returns x sqrt(365); meaningless under ~20 days")
    return out


def last_per_day(obs):
    d = {}
    for t, e in obs: d[sydday(t)] = e
    return sorted(d.items())


def trade_stats(nets, fees):
    w = [x for x in nets if x > 0]; l = [x for x in nets if x <= 0]
    return {"trades": len(nets), "hit_rate_pct": round(100 * len(w) / len(nets), 1) if nets else None,
            "avg_win_usd": round(sum(w) / len(w), 2) if w else None, "avg_loss_usd": round(sum(l) / len(l), 2) if l else None,
            "expectancy_usd": round(sum(nets) / len(nets), 2) if nets else None,
            "profit_factor": round(sum(w) / -sum(l), 3) if l and sum(l) < 0 else None, "fees_usd": round(fees, 2)}


# ----------------------------------------------------------------------------- V2
def v2_view(since):
    b = jl(os.path.join(V2, "tfo_v2_book.json"))
    if not b: return None, ["V2 book missing (not funded yet)"]
    fr = rows(os.path.join(V2, "tfo_v2_firm_equity.csv"))
    obs = [(r["utc"], float(r["equity_usd"])) for r in fr if r["utc"] >= since]
    obs.append((iso(now_utc()), float(b["equity"])))
    closed = [c for c in b.get("closed", []) if c.get("closed_utc", "") >= since]
    fees = float(b.get("fees_paid_usd") or 0)
    deployed_series = [float(r["deployed_usd"]) for r in fr if r["utc"] >= since] or [0.0]
    deployed_now = sum(p["qty"] * (p.get("mark_usd") or p["entry_px"]) for p in b["positions"].values())
    seed = float(b["seed_usd"]); eq = float(b["equity"])
    avg_dep = sum(deployed_series) / len(deployed_series)
    unreal = sum(p["qty"] * ((p.get("mark_usd") or p["entry_px"]) - p["entry_px"]) for p in b["positions"].values())
    v = {"equity_usd": eq, "seed_usd": seed, "net_pnl_usd": round(eq - seed, 2), "return_on_seed_pct": round(100 * (eq / seed - 1), 4),
         "realised_net_usd": round(float(b.get("realised_usd") or 0), 2), "unrealised_usd": round(unreal, 2),
         "avg_deployed_usd": round(avg_dep, 2), "deployed_now_usd": round(deployed_now, 2), "exposure_now_pct": round(100 * deployed_now / eq, 2) if eq else None,
         "return_on_avg_deployed_pct": round(100 * (eq - seed) / avg_dep, 3) if avg_dep > 0 else None,
         "open_positions": len(b["positions"]), "by_setup": {},
         **trade_stats([c["net_pnl_usd"] for c in closed], fees)}
    for c in closed:
        s = v["by_setup"].setdefault(c["setup"], [])
        s.append(c["net_pnl_usd"])
    v["by_setup"] = {k: trade_stats(x, sum(c["fees_usd"] for c in closed if c["setup"] == k)) for k, x in v["by_setup"].items()}
    return {"view": v, "obs": obs, "book": b}, []


# ----------------------------------------------------------------------------- V1 HT
def _fee_fields(o, path, out):
    """Every BOOKED fee field in a V1 ledger: position entry_fees_usd (keyed by its entry_ticket), exit-fill fees_usd
    (keyed by ticket_id) and closed-record fees_usd. Ticket copies (head_trader_tickets, entry_ticket) are skipped."""
    if isinstance(o, dict):
        for fk in ("entry_fees_usd", "fees_usd"):
            v = o.get(fk)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                if fk == "entry_fees_usd":
                    et = o.get("entry_ticket"); kind = "entry"; utc = o.get("entry_utc")
                    tid = et.get("ticket_id") if isinstance(et, dict) else None
                else:
                    kind = "exit" if "/exit_fills[" in path else "record"; utc = o.get("utc") or o.get("closed_utc")
                    tid = o.get("ticket_id")
                out.append({"path": path, "field": fk, "kind": kind, "usd": float(v), "ticket_id": tid, "utc": utc,
                            "backfill": (o.get("fee_backfill") or {}).get("tag") == FEE_BACKFILL_TAG})
        for k, v in o.items():
            if k not in _SKIP: _fee_fields(v, path + "/" + str(k), out)
    elif isinstance(o, list):
        for i, v in enumerate(o): _fee_fields(v, "%s[%d]" % (path, i), out)


def _log_fee_check():
    """Cross-check only: HT LOG lines with a 13th field (fees_usd) at/after the patch (06:40 AEST 26 Sep)."""
    n = usd = 0.0
    try:
        for ln in open(HT_LOG):
            f = ln.split()
            if len(f) >= 13 and f[0] == "LOG" and "%s %s" % (f[1], f[2]) >= "2026-09-26 06:40":
                try: usd += float(f[12]); n += 1
                except ValueError: pass
    except FileNotFoundError:
        return {"log_lines_with_fees": None, "log_fees_usd": None, "note": "tfo_head_trader_log.txt missing"}
    return {"log_lines_with_fees": int(n), "log_fees_usd": round(usd, 2)}


def v1_booked_fees():
    """V1 fees from BOOKED ledger figures only. Returns (summary, per-book fee index for ticket lookup, notes).
    Exactly once per fill: one record per (book, exit|entry, ticket_id) or per closed-record path. A ticket's own
    fees_usd only counts when no fill record in its book carries a fee for that ticket (never both)."""
    st = jl(os.path.join(R, "firm_risk_state.json")) or {}
    books = st.get("book_keys") or V1_TICKET_BOOKS
    recs, idx, notes, by_book = [], {}, [], {}
    for b in books:
        d = jl(os.path.join(R, b + ".json"))
        if d is None: continue
        out, seen = [], set(); _fee_fields(d, "", out)
        for r in out:
            key = (r["kind"], r["ticket_id"]) if r["ticket_id"] and r["kind"] != "record" else (r["path"], r["field"])
            if key in seen: continue
            seen.add(key); r["book"] = b; recs.append(r)
            if r["ticket_id"] and r["kind"] in ("entry", "exit"): idx[(b, r["kind"], r["ticket_id"])] = r
        for t in d.get("head_trader_tickets") or []:
            k = (b, "entry" if t.get("action") == "BUY" else "exit", t.get("ticket_id"))
            if k not in idx and isinstance(t.get("fees_usd"), (int, float)):
                r = {"path": "/head_trader_tickets", "field": "fees_usd", "kind": "ticket", "usd": float(t["fees_usd"]), "ticket_id": t.get("ticket_id"),
                     "utc": t.get("utc"), "backfill": False, "book": b}
                recs.append(r); idx[k] = r
        mine = [r for r in recs if r["book"] == b]
        bf = round(sum(r["usd"] for r in mine if r["backfill"]), 2)
        risk = round(sum(float(e.get("usd") or 0) for e in d.get("cost_backfills") or [] if e.get("tag") == FEE_BACKFILL_TAG), 2)
        by_book[b] = {"booked_usd": round(sum(r["usd"] for r in mine), 2), "fills": len(mine), "backfill_usd": bf,
                      "backfill_fills": sum(1 for r in mine if r["backfill"]), "risk_cost_backfills_usd": risk,
                      "book_fees_paid_usd": d.get("fees_paid_usd")}
        if abs(bf - risk) > 0.005:
            notes.append("%s: fee fields tagged %s sum to $%.2f but its cost_backfills summary says $%.2f (diff $%.2f); the compare uses the "
                         "per-fill booked fees, which is what the book's P&L and equity moved by" % (b, FEE_BACKFILL_TAG, bf, risk, bf - risk))
    bfr = [r for r in recs if r["backfill"]]
    post = [r for r in recs if not r["backfill"] and (r["utc"] or "") >= V1_FEE_PATCH_UTC]
    other = [r for r in recs if not r["backfill"] and (r["utc"] or "") < V1_FEE_PATCH_UTC]
    summ = {"fees_booked_usd": round(sum(r["usd"] for r in recs), 2), "fills_with_booked_fee": len(recs),
            "backfill_usd": round(sum(r["usd"] for r in bfr), 2), "backfill_fills": len(bfr), "backfill_tag": FEE_BACKFILL_TAG,
            "risk_cost_backfills_total_usd": round(sum(v["risk_cost_backfills_usd"] for v in by_book.values()), 2),
            "booked_since_patch_usd": round(sum(r["usd"] for r in post), 2), "booked_since_patch_fills": len(post),
            "other_booked_usd": round(sum(r["usd"] for r in other), 2), "other_booked_fills": len(other),
            "patch_utc": V1_FEE_PATCH_UTC, "by_book": by_book, "ht_log_cross_check": _log_fee_check(),
            "basis": "BOOKED: per-fill fees in the V1 ledgers (backfill-tagged before the desk_costs patch, booked natively after); no estimates"}
    return summ, idx, notes


def v1_ht_view(since, fee_idx=None):
    notes = []; tickets = []; fee_idx = fee_idx or {}
    for k in V1_TICKET_BOOKS:
        d = jl(os.path.join(R, k + ".json"))
        if d is None: notes.append("%s.json absent/unreadable — skipped" % k); continue
        for t in d.get("head_trader_tickets") or []:
            if t.get("utc", "") >= since: tickets.append((k, t))
    facts = []
    try:
        for ln in open(os.path.join(R, "tfo_head_trader_facts.jsonl")):
            try: facts.append(json.loads(ln))
            except json.JSONDecodeError: notes.append("unparseable facts line skipped")
    except FileNotFoundError:
        notes.append("tfo_head_trader_facts.jsonl missing")
    fact_ids = {f.get("ticket") for f in facts}
    missing = [t["ticket_id"] for _, t in tickets if t.get("ticket_id") not in fact_ids]
    if missing: notes.append("%d HT tickets have no facts line: %s" % (len(missing), ", ".join(missing[:5])))
    def booked(k, t):      # the fill's BOOKED fee, once: ledger fill record (backfill or native), else the ticket's own fees_usd
        r = fee_idx.get((k, "entry" if t.get("action") == "BUY" else "exit", t.get("ticket_id")))
        return (float(r["usd"]), bool(r["backfill"])) if r else ((float(t["fees_usd"]), False) if isinstance(t.get("fees_usd"), (int, float)) else (None, False))
    fee_of = {t.get("ticket_id"): booked(k, t) for k, t in tickets}
    nofee = [tid for tid, (f, _) in fee_of.items() if f is None]
    if nofee: notes.append("%d HT tickets have no booked fee in any V1 ledger (counted at $0): %s" % (len(nofee), ", ".join(nofee[:5])))
    clip_entries = {}
    realised_legacy = realised_clip = fees = 0.0; legacy_trades = []; by_day = {}
    for k, t in tickets:
        f = fee_of[t.get("ticket_id")][0] or 0.0; fees += f
        sym = t.get("sym") or t.get("pair")
        if t.get("action") == "BUY":
            clip_entries[(k, sym)] = t; realised_clip -= f; by_day[sydday(t["utc"])] = by_day.get(sydday(t["utc"]), 0) - f
            continue
        pnl = float(t.get("pnl_usd") or 0) - f
        is_clip = (k, sym) in clip_entries or k == "tfo_ht_crypto"
        if is_clip: realised_clip += pnl
        else: realised_legacy += pnl; legacy_trades.append(pnl)
        by_day[sydday(t["utc"])] = by_day.get(sydday(t["utc"]), 0) + pnl
    # open HT clips (MTM)
    unreal = deployed = 0.0; open_n = 0
    cb = jl(os.path.join(R, "tfo_ht_crypto.json")) or {}
    for pair, p in (cb.get("positions") or {}).items():
        mk = (cb.get("mark_px_usd") or {}).get(pair) or p["entry_px"]
        unreal += p["qty"] * (mk - p["entry_px"]); deployed += p["qty"] * p["entry_px"]; open_n += 1
    fbk = jl(os.path.join(R, "fundamental_book.json")) or {}
    for s, p in (fbk.get("positions") or {}).items():
        if p.get("ht_clip"):
            u = p.get("unrealised_usd")
            if u is None: u = float(p["units"]) * (float(p.get("mark_usd") or p["entry_px_usd"]) - float(p["entry_px_usd"]))
            unreal += float(u); deployed += float(p.get("notional_usd") or 0); open_n += 1
    # closed clip round trips (crypto book closed list + fully-closed fundamental clips)
    clip_trades = []
    for c in cb.get("closed") or []:
        p = c.get("position") or {}
        if (c.get("closed_utc") or "") < since: continue
        net = sum(float(x.get("pnl_usd") or 0) - float(x.get("fees_usd") or 0) for x in p.get("exit_fills") or []) - float(p.get("entry_fees_usd") or 0)
        clip_trades.append(net)
    for key, p in (fbk.get("closed") or {}).items():
        if p.get("ht_clip") and (p.get("rail_last_ticket_utc") or "") >= since:
            net = sum(float(x.get("pnl_usd") or 0) - float(x.get("fees_usd") or 0) for x in p.get("exit_fills") or [] if x.get("by") == "TFO Head Trader") - float(p.get("entry_fees_usd") or 0)
            clip_trades.append(net)
    total = realised_clip + realised_legacy + unreal
    return {"realised_clip_net_usd": round(realised_clip, 2), "realised_legacy_exits_net_usd": round(realised_legacy, 2),
            "unrealised_open_clips_usd": round(unreal, 2), "net_pnl_usd": round(total, 2), "open_clips": open_n,
            "deployed_now_usd": round(deployed, 2), "fees_usd": round(fees, 2), "tickets": len(tickets), "facts_lines": len(facts),
            "fees_backfill_usd": round(sum(f for f, b in fee_of.values() if f and b), 2),
            "fees_booked_native_usd": round(sum(f for f, b in fee_of.values() if f and not b), 2),
            "tickets_without_booked_fee": len(nofee), "fees_basis": "BOOKED per-fill fees (TFO Risk backfill for pre-patch fills); no estimates",
            "clip_round_trips": trade_stats(clip_trades, 0.0), "legacy_exit_tickets": trade_stats(legacy_trades, 0.0),
            "realised_by_syd_day": dict(sorted(by_day.items()))}, notes


def v1_reconcile(v1r, v1obs, base, fees_v1):
    """V1 equity in the compare vs firm_risk's own published figure (same source file, same timestamp)."""
    st = jl(os.path.join(R, "firm_risk_state.json")) or {}
    rec = {"firm_risk_utc": st.get("utc"), "firm_risk_equity_usd": st.get("firm_equity_usd"), "drawdown_valid": st.get("drawdown_valid"),
           "capital_accounting_valid": st.get("capital_accounting_valid"), "capital_accounting_errors": st.get("capital_accounting_errors"),
           "external_flow_usd": st.get("external_flow_usd"), "compare_v1_asof_utc": v1obs[-1][0] if v1obs else None,
           "compare_v1_equity_usd": v1obs[-1][1] if v1obs else None}
    row = next((r for r in reversed(v1r) if r["utc"] == st.get("utc")), None)
    if row and isinstance(st.get("firm_equity_usd"), (int, float)):
        rec["csv_equity_at_firm_risk_utc"] = float(row["firm_equity_usd"])
        rec["diff_usd"] = round(float(row["firm_equity_usd"]) - float(st["firm_equity_usd"]), 2)
        rec["compare_is_latest_firm_risk_row"] = rec["compare_v1_asof_utc"] == st.get("utc")
    else:
        rec["diff_usd"] = None; rec["note"] = "firm_risk_state utc not found in firm_equity.csv"
    pt = [e for b in ("gap_close_levered_book", "tfo_ht_crypto") for e in ((jl(os.path.join(R, b + ".json")) or {}).get("capital_events") or [])
          if e.get("tag") == FEE_BACKFILL_TAG]
    rec["backfill_paired_transfers"] = [{"from": e.get("from"), "to": e.get("to"), "usd": e.get("usd"), "utc": e.get("utc")} for e in pt]
    rec["backfill_paired_transfers_net_usd"] = round(sum(float(e.get("usd") or 0) for e in pt), 2)
    rec["basis"] = ("V1 net P&L since base = V1 firm equity - common base; firm_risk nets paired internal transfers to zero "
                    "(external_flow_usd), so the powder-funded crypto backfill is capital movement, not P&L. All booked fees are "
                    "inside firm equity, including the backfill booked after the base.")
    return rec


def main():
    t = now_utc(); notes = []
    v2, n2 = v2_view("1970")
    notes += n2
    if v2 is None:
        payload = {"asof_utc": iso(t), "status": "V2 not funded yet — nothing to compare", "notes": notes}
        atomic_write(OUT_JSON, json.dumps(payload, indent=1)); print(payload["status"]); return 0
    b = v2["book"]; m = b.get("v1_match") or {}
    base = float(m.get("v1_firm_equity_usd") or b["seed_usd"]); since = b.get("funded_utc") or m.get("matched_at_utc")
    if not m.get("v1_firm_equity_usd"): notes.append("V2 book carries no V1 match — common base falls back to V2 seed")
    # V1 firm
    v1r = rows(os.path.join(R, "firm_equity.csv"))
    v1obs = [(r["utc"], float(r["firm_equity_usd"])) for r in v1r if r["utc"] >= (m.get("v1_asof_utc") or since) and r.get("level") != "UNMEASURED"]
    capev = [r["utc"] for r in v1r if r["utc"] > (m.get("v1_asof_utc") or since) and r.get("capital_event") == "YES"]
    if capev: notes.append("V1 firm series shows %d capital_event=YES rows since the base (%s...). firm_risk reconciles internal transfers to zero; "
                           "check reports/firm_risk_state.json external_flow_usd before reading V1 firm %% as performance" % (len(capev), capev[0]))
    v1_now = v1obs[-1][1] if v1obs else None
    fees_v1, fee_idx, nf = v1_booked_fees(); notes += nf
    v1_stats = series_stats(last_per_day(v1obs), base)
    v1 = {"equity_usd": v1_now, "net_pnl_usd": round(v1_now - base, 2) if v1_now else None,
          "return_on_common_base_pct": round(100 * (v1_now / base - 1), 4) if v1_now else None,
          "asof_utc": v1obs[-1][0] if v1obs else None, "capital_event_rows_since_base": len(capev),
          "fees_usd": fees_v1["fees_booked_usd"], "booked_fees": fees_v1, "reconciliation": v1_reconcile(v1r, v1obs, base, fees_v1),
          **{k: v for k, v in v1_stats.items() if k != "daily_pnl"}, "daily_pnl": v1_stats.get("daily_pnl")}
    # V2
    v2v = v2["view"]; v2_stats = series_stats(last_per_day(v2["obs"]), base)
    v2v.update(return_on_common_base_pct=round(100 * (v2v["equity_usd"] / base - 1), 4), **{k: v for k, v in v2_stats.items()})
    # V1 HT (since V2 funding for the common window; also since the HT mandate for context)
    ht, nh = v1_ht_view(since, fee_idx); notes += nh
    ht_all, _ = v1_ht_view(HT_MANDATE_START, fee_idx)
    # V1 HT snapshot series (appended each run)
    new = not os.path.exists(HT_SERIES)
    os.makedirs(V2, exist_ok=True)
    with open(HT_SERIES, "a", newline="") as f:
        w = csv.writer(f)
        if new: w.writerow(["utc", "ht_net_pnl_since_v2_funding_usd", "ht_deployed_usd", "ht_unrealised_usd"])
        w.writerow([iso(t), ht["net_pnl_usd"], ht["deployed_now_usd"], ht["unrealised_open_clips_usd"]])
    hs = rows(HT_SERIES)
    dep = [float(r["ht_deployed_usd"]) for r in hs if r["utc"] >= since] or [ht["deployed_now_usd"]]
    avg_dep = sum(dep) / len(dep)
    ht.update(return_on_common_base_pct=round(100 * ht["net_pnl_usd"] / base, 4), avg_deployed_usd=round(avg_dep, 2),
              return_on_avg_deployed_pct=round(100 * ht["net_pnl_usd"] / avg_dep, 3) if avg_dep > 0 else None,
              exposure_now_pct_of_common_base=round(100 * ht["deployed_now_usd"] / base, 2))
    ht_daily = last_per_day([(r["utc"], float(r["ht_net_pnl_since_v2_funding_usd"])) for r in hs if r["utc"] >= since])
    ht_stats = series_stats([(d, base + p) for d, p in ht_daily], base)
    ht.update({k: v for k, v in ht_stats.items()})
    notes.append("V1 HT capital is not a fixed allocation: clips are funded ticket-by-ticket from powder. Compare % on the common base "
                 "AND % on average deployed capital; neither alone is a fair like-for-like.")
    notes.append("V1 HT MTM history exists only from this job's own snapshots (tfo_compare_v1ht_series.csv); before that only realised P&L by day is known.")
    payload = {"asof_utc": iso(t), "paper_only": True,
               "common_base": {"usd": base, "v1_asof_utc": m.get("v1_asof_utc"), "v2_funded_utc": since, "basis": "V1 firm equity at V2 funding"},
               "v1_firm": v1, "v2_firm": v2v, "v1_head_trader_since_v2_funding": ht, "v1_head_trader_since_mandate": ht_all,
               "notes": notes}
    if V2_DATA_SOURCE_SWITCH_UTC:
        payload["v2_data_source_markers"] = [{"utc": V2_DATA_SOURCE_SWITCH_UTC, "date_syd": sydday(V2_DATA_SOURCE_SWITCH_UTC),
                                              "change": V2_DATA_SOURCE_CHANGE}]
        notes.append("DATA SOURCE SWITCH %s (%s Sydney): %s. V2 results before and after this date come from different data feeds."
                     % (V2_DATA_SOURCE_SWITCH_UTC, sydday(V2_DATA_SOURCE_SWITCH_UTC), V2_DATA_SOURCE_CHANGE))
    atomic_write(OUT_JSON, json.dumps(payload, indent=1, default=str))
    # daily csv
    days = sorted(set([d for d, _ in last_per_day(v1obs)] + [d for d, _ in last_per_day(v2["obs"])] + [d for d, _ in ht_daily]))
    v1d, v2d, htd = dict(last_per_day(v1obs)), dict(last_per_day(v2["obs"])), dict(ht_daily)
    lines = ["date_syd,common_base_usd,v1_firm_equity_usd,v1_firm_day_pnl_usd,v1_firm_ret_pct,v2_equity_usd,v2_day_pnl_usd,v2_ret_pct,v1_ht_net_pnl_usd,v1_ht_day_pnl_usd,v1_ht_ret_pct_common_base,v2_data_source"]
    pv1 = pv2 = base; pht = 0.0
    for d in days:
        e1, e2, h = v1d.get(d), v2d.get(d), htd.get(d)
        f = lambda x: "" if x is None else "%.2f" % x
        lines.append(",".join([d, "%.2f" % base, f(e1), f(e1 - pv1 if e1 is not None else None), f(100 * (e1 / base - 1) if e1 else None),
                               f(e2), f(e2 - pv2 if e2 is not None else None), f(100 * (e2 / base - 1) if e2 else None),
                               f(h), f(h - pht if h is not None else None), f(100 * h / base if h is not None else None),
                               ("" if not V2_DATA_SOURCE_SWITCH_UTC else "paid-first" if d >= sydday(V2_DATA_SOURCE_SWITCH_UTC) else "yahoo")]))
        pv1 = e1 if e1 is not None else pv1; pv2 = e2 if e2 is not None else pv2; pht = h if h is not None else pht
    atomic_write(OUT_CSV, "\n".join(lines) + "\n")
    print(json.dumps({"common_base": base, "v1_firm_ret_pct": v1.get("return_on_common_base_pct"), "v2_ret_pct": v2v["return_on_common_base_pct"],
                      "v1_ht_net_pnl_usd": ht["net_pnl_usd"], "v1_ht_fees_booked_usd": ht["fees_usd"], "v1_fees_booked_usd": fees_v1["fees_booked_usd"],
                      "v1_fees_backfill_usd": fees_v1["backfill_usd"], "v1_fees_since_patch_usd": fees_v1["booked_since_patch_usd"],
                      "v1_vs_firm_risk_diff_usd": v1["reconciliation"].get("diff_usd"), "v2_net_pnl_usd": v2v["net_pnl_usd"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
