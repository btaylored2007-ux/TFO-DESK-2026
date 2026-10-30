#!/usr/bin/env python3
"""TREND-CORE — A$1m AUD paper slow-trend book (DEEP18/Trend18 R2 spirit). PAPER ONLY.

Authority: Brett 28 Oct 2026 ~09:18 WITA (pt1568u) — A$1m paper book, A$50k spare cash never
deployed, deploy remaining A$950k, visible on War Room. Same spirit as Trend18 (multi-market,
vol-sized, monthly rebalance, bond gate, MF idle basket, kill/review).

Accounting path (B): NEW sleeve alongside Trend18. Does NOT register in book_coverage / firm
equity / funder fee. Own A$1m capital (like Honest10 / hunt A$1m books). Trend18 keeps its
firm-integrated US$ sleeve and Nov-2 second-half calendar. No double-booking of the same
positions as two firm books.

Law (mirrors TREND18_SLEEVE_REGISTRATION.md / DEEP18 R2, AUD-denominated):
  - Signals: trend18.compute_signals (0.5×SMA200 + 0.5×12m excess; inv-vol; 20% vol; levered
    notional UPRO3/TQQQ3/TMF3/UGL2/BTC1; TMF duration gate; 97% notional cap).
  - Base = book NAV A$; scale default 1.0; spare cash A$50,000 never deployed into risk.
  - MF basket DBMF/KMLM/CTA equal thirds from idle above spare (never force-sells trend legs).
  - Costs: 12.5bp/side ETF (MF incl.), 25bp/side BTC, charged in A$ cash.
  - Kill: NAV/unit −30% from HWM → sigma 10% + review flag; clear on new 6-month high.
  - Rebalance: monthly on first US session (signal = prior month last close); mid-month refresh;
    entry allowed once at inception.
  - Marks in A$ via AUDUSD (from hunt48_short_status.json, else 0.70 fallback labelled).

Files:
  state/trend_core.json          ledger
  state/trend_core_history.csv   NAV history
  state/trend_core_fills.csv     fills
  state/trend_core_log.txt       log
  static/trend_core_status.json  War Room / Strategy Tracker feed

Commands:
  python3 trend_core.py status
  python3 trend_core.py mark
  python3 trend_core.py enter [--dry-run]   # one-shot inception (no-op if already entered)
  python3 trend_core.py write-status
"""
from __future__ import annotations

import argparse, csv, datetime as dt, json, os, sys, tempfile

try:
    import desk_doh as _doh
    _doh.install()
except Exception as _e:
    print("desk_doh install skipped:", _e, file=sys.stderr)

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "state")
STATIC = os.path.join(BASE, "static")
LEDGER = os.path.join(STATE, "trend_core.json")
HIST = os.path.join(STATE, "trend_core_history.csv")
FILLS = os.path.join(STATE, "trend_core_fills.csv")
LOGF = os.path.join(STATE, "trend_core_log.txt")
STATUS = os.path.join(STATIC, "trend_core_status.json")
REG = "TREND_CORE_REGISTRATION.md"

UTC = dt.timezone.utc
WITA = dt.timezone(dt.timedelta(hours=8), "WITA")
NAV0 = 1_000_000.0
SPARE_AUD = 50_000.0
COST = {"etf": 0.00125, "crypto": 0.0025}
MF = ["DBMF", "KMLM", "CTA"]
TREND = ["UPRO", "TQQQ", "TMF", "UGL", "XBT/USD"]
SIGMA0 = 0.2
KILL_DD = -0.30

sys.path.insert(0, BASE)
import trend18  # noqa: E402  — reuse signals, yahoo/kraken marks, calendar helpers


def now_utc():
    return dt.datetime.now(UTC)


def iso(t=None):
    return (t or now_utc()).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def wita(t=None):
    return (t or now_utc()).astimezone(WITA).strftime("%Y-%m-%d %H:%M WITA")


def log(msg):
    line = "%s %s\n" % (wita(), msg)
    with open(LOGF, "a") as f:
        f.write(line)
    print(line.rstrip())


def atomic_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=2, sort_keys=False)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def audusd():
    p = os.path.join(STATIC, "hunt48_short_status.json")
    try:
        d = json.load(open(p))
        v = float(d.get("audusd") or 0)
        if v > 0.3:
            return v, "static/hunt48_short_status.json"
    except Exception:
        pass
    return 0.70, "fallback_0.70"


def new_ledger():
    return {
        "book": "trend_core",
        "label": "TREND-CORE A$1m (DEEP18/Trend18 R2 spirit, PAPER)",
        "kind": "sleeve_separate",
        "mode": "PAPER",
        "paper_only": True,
        "ccy": "AUD",
        "in_firm_equity": False,
        "accounting_path": "B",
        "accounting_note": (
            "Separate A$1m paper book alongside firm Trend18. Not in book_coverage / firm_risk / "
            "funder fee. Trend18 keeps its US$ firm sleeve. No double-count of the same positions."
        ),
        "registration": REG,
        "funder_approval": "Brett 2026-10-28 ~09:18 WITA pt1568u: A$1m slow-trend paper, A$50k spare, deploy A$950k, War Room visible",
        "rule": (
            "DEEP18/Trend18 R2 spirit in A$: SMA200+12m excess trend; inv-vol to 20%; levered "
            "UPRO3/TQQQ3/TMF3/UGL2/BTC1; TMF duration gate; 97% notional cap; monthly + mid-month "
            "refresh; idle above A$50k spare in DBMF/KMLM/CTA thirds; costs 12.5bp ETF / 25bp BTC; "
            "kill at -30% NAV/unit -> 10% vol + review. PAPER ONLY. Not firm capital."
        ),
        "created_utc": iso(),
        "created_wita": wita(),
        "seed_aud": NAV0,
        "spare_aud": SPARE_AUD,
        "deploy_cap_aud": NAV0 - SPARE_AUD,
        "cash_aud": NAV0,
        "equity_aud": NAV0,
        "peak_aud": NAV0,
        "units": NAV0,
        "nav_per_unit": 1.0,
        "peak_nav_per_unit": 1.0,
        "drawdown_pct": 0.0,
        "scale_current": 0.0,
        "sigma_current": SIGMA0,
        "holdings": {k: 0.0 for k in TREND + MF},
        "entry_px_usd": {},
        "mark_px_usd": {},
        "mark_source": {},
        "mark_utc": None,
        "costs_paid_aud": 0.0,
        "realised_aud": 0.0,
        "fills": [],
        "rebalances": [],
        "last_signals": None,
        "kill": {"active": False, "pending_rebalance": False},
        "kill_ref_peak_nav": 1.0,
        "review_flag": None,
        "next_rebalance": None,
        "audusd": None,
        "audusd_source": None,
        "updated": iso(),
        "updated_wita": wita(),
    }


def load():
    if not os.path.exists(LEDGER):
        L = new_ledger()
        save(L)
        return L
    return json.load(open(LEDGER))


def save(L):
    L["updated"] = iso()
    L["updated_wita"] = wita()
    atomic_json(LEDGER, L)


def fetch_marks(extra_crypto=()):
    """USD marks for trend + MF + BTC. Prefer yahoo ETFs + Kraken BTC (desk_doh / kraken_dns).
    2026-10-28 21:40 WITA feed doctor: also mark other held Kraken crypto (ETH/USD, SOL/USD from the
    global rebalance) - they were never re-marked and sat frozen at the entry price."""
    out = {}
    src = {}
    try:
        y = trend18.yahoo_prices(["UPRO", "TQQQ", "TMF", "UGL"] + MF)
        for k, v in (y or {}).items():
            if v and v.get("px"):
                out[k] = float(v["px"])
                src[k] = v.get("kind") or "yahoo"
    except Exception as e:
        log("yahoo marks fail: %s" % e)
    try:
        b = trend18.kraken_btc()
        if b and b.get("mid"):
            out["XBT/USD"] = float(b["mid"])
            src["XBT/USD"] = b.get("source") or "kraken"
    except Exception as e:
        log("btc mark fail: %s" % e)
    for inst in extra_crypto or ():
        if inst in out:
            continue
        try:
            import tfo_crypto
            q = tfo_crypto.quote(inst)
            if q and q.get("mid") and float(q["mid"]) > 0:
                out[inst] = float(q["mid"])
                src[inst] = "kraken_mid (tfo_crypto.quote)"
        except Exception as e:
            log("%s mark fail: %s" % (inst, e))
    # fall back to Trend18 live marks for any missing held names
    try:
        s = trend18.summary()
        for h in s.get("holdings") or []:
            inst = h.get("inst")
            px = h.get("px")
            if inst and px and inst not in out:
                out[inst] = float(px)
                src[inst] = "trend18.summary"
    except Exception:
        pass
    return out, src


def next_rebalance_label(now=None):
    """Next monthly = first US session of next month after last month-end signal."""
    now = now or now_utc()
    d = now.date()
    # If before Nov 2026 first session, that's the next one for the Oct-30 signal.
    try:
        # first session of current month
        first = trend18.first_session_of_month(d)
        if d <= first:
            return first.isoformat(), "monthly (first US session)"
        # else first of next month
        nxt = (d.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
        first2 = trend18.first_session_of_month(nxt)
        return first2.isoformat(), "monthly (first US session)"
    except Exception:
        return "2026-11-02", "monthly (first US session, fallback)"


def plan_entry(sig, nav_aud, scale, marks, fx):
    """Return list of orders sized in A$ / shares. Spare never touched."""
    orders = []
    trend_need = 0.0
    for s in sig["sleeves"]:
        inst = s["inst"]
        w = float(s["notional_w"] or 0)
        tgt = w * nav_aud * scale
        kind = "crypto" if s["kind"] == "crypto" else "etf"
        px = marks.get(inst)
        if tgt <= 0 or not px:
            if tgt > 0 and not px:
                raise RuntimeError("no mark for %s (need for target A$%.0f)" % (inst, tgt))
            continue
        # buy at ask-ish for BTC
        fill_px = px
        if kind == "crypto":
            try:
                b = trend18.kraken_btc()
                fill_px = float(b.get("ask") or px)
            except Exception:
                fill_px = px
        shares = (tgt * fx) / fill_px
        notional_aud = shares * fill_px / fx
        cost = notional_aud * COST[kind]
        orders.append({
            "inst": inst, "leg": "trend", "kind": kind, "side": "BUY",
            "target_w": w, "target_aud": round(tgt, 2),
            "shares": round(shares, 6 if kind == "crypto" else 4),
            "px_usd": round(fill_px, 4), "notional_aud": round(notional_aud, 2),
            "cost_aud": round(cost, 2),
        })
        trend_need += notional_aud + cost

    sum_n = float(sig.get("sum_notional") or 0)
    mf_cap = max(0.0, scale * (nav_aud * (1.0 - sum_n) - SPARE_AUD))
    # budget = cash after trend - spare
    # After paying trend+costs, residual above spare funds MF (equal thirds), costs included.
    # Size MF gross so gross*(1+cost) fits in mf_cap and in residual.
    residual_for_mf = max(0.0, nav_aud - SPARE_AUD - trend_need)
    mf_gross = min(mf_cap, residual_for_mf / (1.0 + COST["etf"]))
    if mf_gross > 1.0 and all(m in marks for m in MF):
        each = mf_gross / 3.0
        for m in MF:
            px = marks[m]
            shares = (each * fx) / px
            notional_aud = shares * px / fx
            cost = notional_aud * COST["etf"]
            orders.append({
                "inst": m, "leg": "mf", "kind": "etf", "side": "BUY",
                "target_w": None, "target_aud": round(each, 2),
                "shares": round(shares, 4),
                "px_usd": round(px, 4), "notional_aud": round(notional_aud, 2),
                "cost_aud": round(cost, 2),
            })
    elif mf_gross > 1.0:
        log("MF skipped: missing price for one of %s" % MF)

    return orders, round(mf_cap, 2), round(trend_need, 2)


def apply_buys(L, orders, sig, scale, marks, src, fx, fx_src, reason="entry"):
    cash = float(L["cash_aud"])
    fills = []
    for o in orders:
        cost = o["cost_aud"]
        pay = o["notional_aud"] + cost
        if pay > cash + 1e-6:
            raise RuntimeError("insufficient cash for %s: need A$%.2f have A$%.2f" % (o["inst"], pay, cash))
        cash -= pay
        L["holdings"][o["inst"]] = float(L["holdings"].get(o["inst"], 0)) + o["shares"]
        L["entry_px_usd"][o["inst"]] = o["px_usd"]
        L["costs_paid_aud"] = round(float(L["costs_paid_aud"]) + cost, 2)
        fid = "TC-%s-%s" % (reason[:3].upper(), o["inst"].replace("/", ""))
        fill = {
            "id": fid, "utc": iso(), "wita": wita(), "reason": reason,
            "inst": o["inst"], "side": "BUY", "shares": o["shares"],
            "px_usd": o["px_usd"], "notional_aud": o["notional_aud"],
            "cost_aud": cost, "audusd": fx, "leg": o["leg"], "paper_only": True,
        }
        fills.append(fill)
        L["fills"].append(fill)
        _append_fill(fill)

    if cash < SPARE_AUD - 1.0:
        # should not happen by construction; refuse rather than breach spare
        raise RuntimeError("spare breach after fills: cash A$%.2f < spare A$%.2f" % (cash, SPARE_AUD))

    L["cash_aud"] = round(cash, 2)
    L["scale_current"] = scale
    L["sigma_current"] = float(L.get("sigma_current") or SIGMA0)
    L["last_signals"] = sig
    L["mark_px_usd"] = {k: marks[k] for k in marks}
    L["mark_source"] = src
    L["mark_utc"] = iso()
    L["audusd"] = fx
    L["audusd_source"] = fx_src
    nb, nbl = next_rebalance_label()
    L["next_rebalance"] = {"date": nb, "label": nbl}
    L["rebalances"].append({
        "utc": iso(), "wita": wita(), "reason": reason, "scale": scale,
        "sigma": L["sigma_current"], "signal_asof": sig.get("asof"),
        "base_aud": NAV0, "mf_cap_aud": None, "fills": [f["id"] for f in fills],
        "audusd": fx, "paper_only": True,
    })
    restrike(L)
    return fills


def restrike(L):
    fx = float(L.get("audusd") or audusd()[0])
    mv = 0.0
    for inst, qty in (L.get("holdings") or {}).items():
        q = float(qty or 0)
        if q == 0:
            continue
        px = float((L.get("mark_px_usd") or {}).get(inst) or 0)
        if not px:
            continue
        mv += q * px / fx
    cash = float(L.get("cash_aud") or 0)
    eq = round(cash + mv, 2)
    L["equity_aud"] = eq
    units = float(L.get("units") or NAV0)
    npu = eq / units if units else 1.0
    L["nav_per_unit"] = npu
    L["peak_nav_per_unit"] = max(float(L.get("peak_nav_per_unit") or 1.0), npu)
    L["peak_aud"] = max(float(L.get("peak_aud") or eq), eq)
    peak = float(L["peak_nav_per_unit"])
    L["drawdown_pct"] = round((npu / peak - 1.0) * 100.0, 2) if peak else 0.0
    # kill check
    ref = float(L.get("kill_ref_peak_nav") or peak)
    if npu <= ref * (1.0 + KILL_DD):
        if not (L.get("kill") or {}).get("active"):
            L["kill"] = {"active": True, "pending_rebalance": True, "triggered_utc": iso()}
            L["sigma_current"] = 0.10
            L["review_flag"] = {"reason": "TREND-CORE kill: NAV/unit <= -30% vs kill ref", "utc": iso()}
            log("KILL ACTIVE nav/unit=%.4f ref=%.4f" % (npu, ref))
    return L


def mark(L=None):
    L = L or load()
    held_crypto = [i for i, q in (L.get("holdings") or {}).items()
                   if "/" in i and i != "XBT/USD" and float(q or 0) > 0]
    marks, src = fetch_marks(held_crypto)
    fx, fx_src = audusd()
    L["audusd"] = fx
    L["audusd_source"] = fx_src
    if marks:
        L["mark_px_usd"].update(marks)
        L["mark_source"].update(src)
        L["mark_utc"] = iso()
    restrike(L)
    nb, nbl = next_rebalance_label()
    L["next_rebalance"] = {"date": nb, "label": nbl}
    save(L)
    _hist(L)
    write_status(L)
    # 2026-10-28 Brett 14:58 WITA: every mark writes the log (was silent -> empty launchd/desk logs)
    log("mark NAV A$%.2f cash A$%.2f next %s marks=%d" % (
        float(L.get("equity_aud") or 0), float(L.get("cash_aud") or 0), nb, len(marks or {})))
    return L


def _append_fill(fill):
    new = not os.path.exists(FILLS)
    with open(FILLS, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "id", "utc", "wita", "reason", "inst", "side", "shares", "px_usd",
            "notional_aud", "cost_aud", "audusd", "leg", "paper_only",
        ])
        if new:
            w.writeheader()
        w.writerow(fill)


def _hist(L):
    new = not os.path.exists(HIST)
    with open(HIST, "a", newline="") as f:
        cols = ["utc", "wita", "equity_aud", "cash_aud", "nav_per_unit", "drawdown_pct",
                "scale", "sigma", "kill_active", "audusd"] + [
            "%s_qty" % i.replace("/", "") for i in TREND + MF
        ]
        w = csv.DictWriter(f, fieldnames=cols)
        if new:
            w.writeheader()
        row = {
            "utc": L["updated"], "wita": L["updated_wita"],
            "equity_aud": L["equity_aud"], "cash_aud": L["cash_aud"],
            "nav_per_unit": L["nav_per_unit"], "drawdown_pct": L["drawdown_pct"],
            "scale": L["scale_current"], "sigma": L["sigma_current"],
            "kill_active": bool((L.get("kill") or {}).get("active")),
            "audusd": L.get("audusd"),
        }
        for i in TREND + MF:
            row["%s_qty" % i.replace("/", "")] = L["holdings"].get(i, 0)
        w.writerow(row)


def holdings_view(L):
    fx = float(L.get("audusd") or audusd()[0])
    rows = []
    deployed = 0.0
    # 2026-10-28 health overlord: include global/spec holdings (EWJ, ERX, ETH/USD...) not just TREND+MF,
    # so deployed/holdings on the status file match equity_aud (restrike already counts them).
    _order = TREND + MF + sorted(k for k in (L.get("holdings") or {}) if k not in TREND + MF)
    for inst in _order:
        q = float((L.get("holdings") or {}).get(inst) or 0)
        px = float((L.get("mark_px_usd") or {}).get(inst) or 0)
        ep = float((L.get("entry_px_usd") or {}).get(inst) or 0)
        if q == 0 and px == 0:
            continue
        val = (q * px / fx) if px else 0.0
        cost_basis = (q * ep / fx) if ep else None
        upl = (val - cost_basis) if cost_basis is not None else None
        w = (val / float(L["equity_aud"]) * 100.0) if L.get("equity_aud") else 0.0
        deployed += val
        rows.append({
            "inst": inst, "qty": q, "px_usd": px, "entry_px_usd": ep,
            "value_aud": round(val, 2), "upl_aud": round(upl, 2) if upl is not None else None,
            "weight_pct": round(w, 2),
            "mark_source": (L.get("mark_source") or {}).get(inst),
        })
    return rows, round(deployed, 2)


def write_status(L=None):
    L = L or load()
    holdings, deployed = holdings_view(L)
    fx = L.get("audusd") or audusd()[0]
    spare_held = min(float(L.get("cash_aud") or 0), SPARE_AUD)
    free_cash = max(0.0, float(L.get("cash_aud") or 0) - SPARE_AUD)
    prior = None
    if os.path.exists(HIST):
        try:
            with open(HIST) as f:
                lines = list(csv.DictReader(f))
            if len(lines) >= 2:
                prior = float(lines[-2]["equity_aud"])
        except Exception:
            prior = None
    vs_prior = round(float(L["equity_aud"]) - prior, 2) if prior is not None else None
    vs_seed = round(float(L["equity_aud"]) - float(L["seed_aud"]), 2)
    nb = L.get("next_rebalance") or {}
    sig = L.get("last_signals") or {}
    gate = (sig.get("duration_gate") or {})
    status = {
        "book": "trend_core",
        "label": L.get("label"),
        "paper_only": True,
        "ccy": "AUD",
        "in_firm_equity": False,
        "accounting_path": "B",
        "accounting_note": L.get("accounting_note"),
        "nav_aud": L.get("equity_aud"),
        "seed_aud": L.get("seed_aud"),
        "cash_aud": L.get("cash_aud"),
        "spare_aud": SPARE_AUD,
        "spare_held_aud": round(spare_held, 2),
        "free_cash_aud": round(free_cash, 2),
        "deployed_aud": deployed,
        "deploy_cap_aud": NAV0 - SPARE_AUD,
        "vs_prior_aud": vs_prior,
        "vs_seed_aud": vs_seed,
        "drawdown_pct": L.get("drawdown_pct"),
        "nav_per_unit": L.get("nav_per_unit"),
        "peak_nav_per_unit": L.get("peak_nav_per_unit"),
        "scale": L.get("scale_current"),
        "sigma": L.get("sigma_current"),
        "kill": L.get("kill"),
        "review_flag": L.get("review_flag"),
        "holdings": holdings,
        "signal_asof": sig.get("asof"),
        "duration_gate_open": gate.get("open"),
        "sum_notional": sig.get("sum_notional"),
        "next_rebalance": nb,
        "audusd": fx,
        "audusd_source": L.get("audusd_source"),
        "mark_utc": L.get("mark_utc"),
        "costs_paid_aud": L.get("costs_paid_aud"),
        "updated_utc": L.get("updated"),
        "updated_wita": L.get("updated_wita"),
        "registration": REG,
        "alongside": "Trend18 firm sleeve (state/trend18.json) — separate capital, no double-count",
    }
    try:  # 2026-10-28 shadow profit-taking summary (trend_core_shadow.py; SHADOW, outside book P&L)
        import trend_core_shadow as _tcs
        if os.path.exists(_tcs.SHADOW):
            status["shadow"] = _tcs.summary()
    except Exception:
        pass
    atomic_json(STATUS, status)
    return status


def enter(dry_run=False):
    L = load()
    if float(L.get("scale_current") or 0) > 0 or any(float(v or 0) > 0 for v in (L.get("holdings") or {}).values()):
        log("enter: already entered (scale=%s) — no-op" % L.get("scale_current"))
        write_status(L)
        return L, []

    log("computing signals via trend18.compute_signals()")
    sig = trend18.compute_signals()
    marks, src = fetch_marks()
    fx, fx_src = audusd()
    log("audusd=%.4f (%s) signal_asof=%s sum_notional=%.4f gate_open=%s" % (
        fx, fx_src, sig.get("asof"), float(sig.get("sum_notional") or 0),
        (sig.get("duration_gate") or {}).get("open")))

    orders, mf_cap, trend_need = plan_entry(sig, NAV0, 1.0, marks, fx)
    log("plan: %d orders trend_need~A$%.0f mf_cap=A$%.0f spare=A$%.0f" % (
        len(orders), trend_need, mf_cap, SPARE_AUD))
    for o in orders:
        log("  %s %s %s sh=%s px=%.4f notional=A$%.2f cost=A$%.2f" % (
            o["side"], o["inst"], o["leg"], o["shares"], o["px_usd"], o["notional_aud"], o["cost_aud"]))

    if dry_run:
        log("DRY-RUN — no fills")
        return L, orders

    # ensure cash is seed
    L["cash_aud"] = NAV0
    L["equity_aud"] = NAV0
    fills = apply_buys(L, orders, sig, 1.0, marks, src, fx, fx_src, reason="entry")
    # stash mf_cap on last rebalance record
    if L["rebalances"]:
        L["rebalances"][-1]["mf_cap_aud"] = mf_cap
        L["rebalances"][-1]["trend_need_aud"] = round(trend_need, 2)
    save(L)
    _hist(L)
    write_status(L)
    log("ENTRY DONE equity=A$%.2f cash=A$%.2f costs=A$%.2f fills=%d" % (
        L["equity_aud"], L["cash_aud"], L["costs_paid_aud"], len(fills)))
    return L, fills


def status_text():
    L = load()
    st = write_status(L)
    print("TREND-CORE · PAPER · path B (alongside Trend18, not in firm equity)")
    print("NAV A$%s  cash A$%s  spare A$%s  deployed A$%s  vs seed A$%s  DD %s%%" % (
        "{:,.0f}".format(st["nav_aud"] or 0),
        "{:,.0f}".format(st["cash_aud"] or 0),
        "{:,.0f}".format(st["spare_aud"]),
        "{:,.0f}".format(st["deployed_aud"] or 0),
        "{:,.0f}".format(st["vs_seed_aud"] or 0),
        st.get("drawdown_pct")))
    print("scale %s  sigma %s  kill %s  signal_asof %s  next %s" % (
        st.get("scale"), st.get("sigma"),
        "ACTIVE" if (st.get("kill") or {}).get("active") else "armed (-30%)",
        st.get("signal_asof"), (st.get("next_rebalance") or {}).get("date")))
    print("audusd %.4f (%s)  updated %s" % (st.get("audusd") or 0, st.get("audusd_source"), st.get("updated_wita")))
    for h in st.get("holdings") or []:
        print("  %-8s qty=%s  px=$%s  val=A$%s  w=%s%%  upl=A$%s" % (
            h["inst"], h["qty"], h["px_usd"],
            "{:,.0f}".format(h["value_aud"]), h["weight_pct"],
            "{:,.0f}".format(h["upl_aud"] or 0)))


def main():
    ap = argparse.ArgumentParser(description="TREND-CORE A$1m paper book")
    ap.add_argument("cmd", choices=["status", "mark", "enter", "write-status"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.cmd == "status":
        status_text()
    elif args.cmd == "mark":
        import desk_lock   # 2026-10-28: same ledger lock as trend_core_global.py (no overlap with its 21:36-23:05 WITA writes)
        with desk_lock.ledger_lock("trend_core", timeout=300):
            mark()
        status_text()
    elif args.cmd == "enter":
        enter(dry_run=args.dry_run)
        status_text()
    elif args.cmd == "write-status":
        write_status()
        print("wrote", STATUS)


if __name__ == "__main__":
    main()
