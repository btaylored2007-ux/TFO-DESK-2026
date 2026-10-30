"""Theme exposure + combined stress (Brett 2026-10-28 12:17 WITA, STEP 1). PAPER ONLY, WARN-ONLY.
Never blocks, resizes, or places anything. Reads book ledgers/status files only (no network, no keys).
Firm-wide A$ exposure across books + staged/queued entries, grouped by theme (leverage-adjusted), combined
stress (NDX -20, SPX -15, BTC -30, gold -15, silver +15, equities -15, AUD -5%), stress budget = budget_pct x firm NAV
(state/theme_cap.json), cap per theme = theme_cap_frac x budget. Writes state/theme_exposure.json + static copy for War Room."""
import datetime as dt, json, os
BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "state", "theme_exposure.json"); STATIC = os.path.join(BASE, "static", "theme_exposure.json")
CFG_PATH = os.path.join(BASE, "state", "theme_cap.json")
WITA = dt.timezone(dt.timedelta(hours=8)); DESK_FX = 0.7022
THEMES = ["us_tech", "ai_infra", "crypto", "metals", "energy_essentials", "rates_bonds", "other"]
LABEL = {"us_tech": "US tech / Nasdaq beta (lev-adj)", "ai_infra": "  AI-infra / neocloud (look-through sub-theme)", "crypto": "BTC / crypto",
         "metals": "Metals", "energy_essentials": "Energy / essentials", "rates_bonds": "Rates / bonds", "other": "Other"}
DEFAULT_CFG = {
    "mode": "enforce", "_note": "ENFORCE (Brett 28 Oct 13:40 approve-all #5; was WARN-ONLY). Edit to change. budget_pct of firm NAV = firm stress-loss budget; cap per theme = theme_cap_frac x budget.",
    "budget_pct_of_firm_nav": 0.10, "theme_cap_frac_of_budget": 0.20, "amber_pct_of_cap": 0.75, "firm_nav_aud_override": None,
    "fx_audusd": None,
    "shocks": {"NDX": -0.20, "SPX": -0.15, "BTC": -0.30, "CRYPTO": -0.30, "GOLD": -0.15, "SILVER": 0.15, "EQ": -0.15,
               "COPPER": -0.15, "CMDTY": -0.15, "BOND": 0.0, "MF": 0.0, "FX": 0.0, "AUD_FX": -0.05},
    "_shock_notes": "EQ = all other equities incl. ASX/Asia/energy stocks; COPPER/CMDTY (oil, DBC) assumed equity-like -15%; BOND/MF/FX pairs 0 (no rate shock specified); AUD -5% raises A$ value of non-AUD assets x1/0.95",
    "ai_lookthrough": {"TQQQ": 0.30, "QQQ": 0.30, "NAS100_USD": 0.30, "UPRO": 0.15, "SPY": 0.15, "US500_USD": 0.15},
    "fx_to_aud": {"AUD": 1.0, "GBP": 2.03, "EUR": 1.74, "CHF": 1.90, "JPY": 0.0100, "HKD": 0.183, "SGD": 1.10, "KRW": 0.00106, "TWD": 0.0465, "CAD": 1.03, "NZD": 0.91},
}
# sym -> (theme, underlying, leverage)
MAP = {"TQQQ": ("us_tech", "NDX", 3), "QQQ": ("us_tech", "NDX", 1), "NAS100_USD": ("us_tech", "NDX", 1), "SOXL": ("us_tech", "NDX", 3),
       "UPRO": ("us_tech", "SPX", 3), "SPY": ("us_tech", "SPX", 1), "US500_USD": ("us_tech", "SPX", 1), "SPX500_USD": ("us_tech", "SPX", 1),
       "UGL": ("metals", "GOLD", 2), "XAU_USD": ("metals", "GOLD", 1), "GLD": ("metals", "GOLD", 1), "XAG_USD": ("metals", "SILVER", 1),
       "SLV": ("metals", "SILVER", 1), "AGQ": ("metals", "SILVER", 2), "XCU_USD": ("metals", "COPPER", 1),
       "TMF": ("rates_bonds", "BOND", 3), "TLT": ("rates_bonds", "BOND", 1),
       "XLE": ("energy_essentials", "EQ", 1), "ERX": ("energy_essentials", "EQ", 2), "USO": ("energy_essentials", "CMDTY", 1),
       "DBC": ("energy_essentials", "CMDTY", 1), "BCO_USD": ("energy_essentials", "CMDTY", 1), "WTICO_USD": ("energy_essentials", "CMDTY", 1),
       "DBMF": ("other", "MF", 1), "KMLM": ("other", "MF", 1), "CTA": ("other", "MF", 1), "EDC": ("other", "EQ", 3)}
AI_NAMES = {"NVDA", "AVGO", "AMD", "TSM", "MU", "SMCI", "CRWV", "NBIS", "ORCL", "VRT", "ANET", "IREN", "CIFR", "WULF", "APLD", "CORZ",
            "MRVL", "DELL", "ARM", "SMH", "SOXX", "SOXL", "CLS", "HUT", "BTDR", "ALAB", "COHR", "LITE", "2330.TW", "000660.KS", "6857.T"}
TECH_NAMES = AI_NAMES | {"AAPL", "MSFT", "GOOGL", "GOOG", "META", "AMZN", "TSLA", "NFLX", "PLTR", "ADBE", "CRM", "INTC", "QCOM"}
SUFFIX_CCY = {".AX": "AUD", ".T": "JPY", ".HK": "HKD", ".SI": "SGD", ".KS": "KRW", ".KQ": "KRW", ".TW": "TWD", ".L": "GBP", ".PA": "EUR", ".DE": "EUR", ".OL": "NOK"}

def _j(p, fb=None):
    try:
        with open(p if os.path.isabs(p) else os.path.join(BASE, p)) as f: return json.load(f)
    except Exception: return fb
def load_cfg():
    c = _j(CFG_PATH)
    if not isinstance(c, dict):
        c = json.loads(json.dumps(DEFAULT_CFG))
        try:
            with open(CFG_PATH, "w") as f: json.dump(c, f, indent=1)
        except OSError: pass
    out = json.loads(json.dumps(DEFAULT_CFG)); 
    for k, v in c.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict): out[k].update(v)
        else: out[k] = v
    return out
def ccy_of(sym):
    s = str(sym or "")
    for suf, c in SUFFIX_CCY.items():
        if s.endswith(suf): return c
    return "USD"
def classify(sym, hint=None):
    s = str(sym or "").upper()
    if s in MAP: return MAP[s]
    if "/" in s or s in ("BTC", "ETH", "SOL", "XBT") or (s.endswith("-USD") and s[:-4].isalpha()):
        return ("crypto", "BTC" if s.startswith(("XBT", "BTC")) else "CRYPTO", 1)
    if s in TECH_NAMES: return ("us_tech", "NDX", 1)
    h = str(hint or "").lower()
    if any(k in h for k in ("energy", "utilit", "essential", "oil", "gas", "uranium", "power")): return ("energy_essentials", "EQ", 1)
    if any(k in h for k in ("gold", "silver", "metal", "copper", "mining")): return ("metals", "EQ", 1)
    if "_" in s and len(s) == 7 and s[:3].isalpha(): return ("other", "FX", 1)
    return ("other", "EQ", 1)
def ai_frac(sym, cfg):
    s = str(sym or "").upper()
    if s in AI_NAMES: return 1.0
    return float(cfg["ai_lookthrough"].get(s, 0.0))
def pos(book, sym, value_aud, side="long", ccy=None, hint=None, staged=False, src=""):
    return dict(book=book, sym=sym, value_aud=float(value_aud or 0), sign=-1.0 if str(side).lower() == "short" else 1.0,
                ccy=ccy or ccy_of(sym), hint=hint, staged=staged, src=src)

def fx_audusd(cfg):
    if cfg.get("fx_audusd"): return float(cfg["fx_audusd"])
    tc = _j("static/trend_core_status.json") or {}
    try: return float(tc.get("audusd")) or DESK_FX
    except Exception: return DESK_FX

def collect(cfg, fx):
    P, nav, errs = [], {}, []
    def guard(name, fn):
        try: fn()
        except Exception as e: errs.append(f"{name}: {type(e).__name__}: {e}")
    def trend18():
        d = _j("state/trend18.json") or {}; m = d.get("mark_px_usd") or {}
        for s, q in (d.get("holdings") or {}).items():
            if q: P.append(pos("Trend18 (US$)", s, float(q) * float(m.get(s) or 0) / fx, ccy="USD", src="state/trend18.json"))
        if d.get("equity"): nav["Trend18"] = float(d["equity"]) / fx
    def trend_core():
        d = _j("static/trend_core_status.json") or {}; cur = {}
        for h in d.get("holdings") or []:
            v = float(h.get("value_aud") or 0); cur[h["inst"]] = v
            if v: P.append(pos("TREND-CORE", h["inst"], v, ccy="USD", src="static/trend_core_status.json"))
        g = _j("static/trend_core_global_status.json") or {}
        for f in g.get("crypto_fills") or []:
            if f.get("side") == "BUY" and f.get("inst") not in cur:
                P.append(pos("TREND-CORE spec", f["inst"], f.get("notional_aud"), ccy="USD", src="trend_core_global crypto_fills")); cur[f["inst"]] = float(f.get("notional_aud") or 0)
        if g and not g.get("executed"):
            for t in g.get("targets") or []:
                if "/" in t["inst"]: continue          # crypto legs already executed
                delta = float(t.get("target_aud") or 0) - cur.get(t["inst"], 0.0)
                if abs(delta) > 1: P.append(pos("TREND-CORE rebook (staged)", t["inst"], delta, ccy="USD", staged=True, src="trend_core_global targets - current"))
        if d.get("nav_aud"): nav["TREND-CORE"] = float(d["nav_aud"])
    def essentials():
        d = _j("static/essentials_status.json") or {}
        for h in d.get("holdings") or []:
            c = "AUD" if h.get("mkt") in ("ASX", "AX") or str(h.get("inst", "")).endswith(".AX") else (h.get("ccy") or ccy_of(h.get("inst")))
            if h.get("status") == "FILLED": P.append(pos("ESSENTIALS", h["inst"], h.get("value_aud"), ccy=c, hint="essential", src="static/essentials_status.json"))
            elif h.get("status") == "STAGED" and h.get("target_aud"): P.append(pos("ESSENTIALS (staged)", h["inst"], h["target_aud"], ccy=c, hint="essential", staged=True, src="essentials staged"))
        if d.get("nav_aud"): nav["ESSENTIALS"] = float(d["nav_aud"])
    def hunts():
        import glob
        seen = set()
        for f in sorted(glob.glob(os.path.join(BASE, "state", "hunt48_*", "ledger.json"))):
            L = _j(f) or {}; book = "Hunt " + os.path.basename(os.path.dirname(f)).replace("hunt48_", "")
            for t in L.get("trades") or []:
                st = t.get("status"); sym = t.get("ticker") or t.get("sym") or t.get("symbol"); side = t.get("direction") or "long"
                us = ccy_of(sym) == "USD"
                if st == "FILLED":
                    q = float(t.get("open_qty") or t.get("shares") or 0); px = float(t.get("last_px") or t.get("entry") or 0)
                    if us: v = q * px / fx
                    elif t.get("notional_aud") and t.get("entry"): v = float(t["notional_aud"]) * px / float(t["entry"]) * (q / float(t.get("shares") or q or 1))
                    else: v = float(t.get("notional_aud") or 0)
                    P.append(pos(book, sym, v, side, src=os.path.relpath(f, BASE)))
                elif st == "ARMED":
                    v = float(t.get("notional_aud") or 0) or (float(t.get("shares") or 0) * float(t.get("trigger") or 0) / (fx if us else 1))
                    P.append(pos(book + " (armed)", sym, v, side, staged=True, src=os.path.relpath(f, BASE))); seen.add((book, t.get("id")))
        q = (_j("state/funder_queue.json") or {}).get("entries") or {}
        for k, e in q.items():
            if str(e.get("state") or e.get("status")).lower() == "awaiting_funder":
                sym = e.get("ticker"); us = ccy_of(sym) == "USD"
                v = float(e.get("notional_aud") or 0) or float(e.get("shares") or 0) * float(e.get("entry") or 0) / (fx if us else 1) * (1 if us else float(e.get("fx_aud") or 1))
                if not us and not e.get("notional_aud") and not e.get("fx_aud"):   # 2026-10-28 approve-all #5 fix: local px (LSE pence) was read as A$
                    hl = _j("state/hunt48_%s/ledger.json" % e.get("book")) or {}
                    hv = [float(t.get("notional_aud") or 0) for t in hl.get("trades") or [] if (t.get("sym") or t.get("ticker")) == sym and t.get("notional_aud")]
                    if hv: v = hv[-1]
                    else:
                        c = ccy_of(sym); v = float(e.get("shares") or 0) * float(e.get("entry") or 0) * float(cfg["fx_to_aud"].get(c, 1.0)) * (0.01 if str(sym).endswith(".L") else 1)
                P.append(pos("Hunt " + str(e.get("book")) + " (awaiting funder)", sym, v, e.get("side") or "long", staged=True, src="state/funder_queue.json"))
    def oanda():
        d = _j("state/oanda/snapshot.json") or {}; F = cfg["fx_to_aud"]
        for t in d.get("trades") or []:
            ins = t.get("instrument", ""); u = float(t.get("units") or 0); base, _, quote = ins.partition("_")
            if base in F or base == "USD": v = abs(u) * (F.get(base) or 1 / fx)
            else: v = abs(u) * float(t.get("price") or 0) * (F.get(quote) or (1 / fx if quote == "USD" else 0))
            P.append(pos("OANDA", ins, v, "short" if u < 0 else "long", ccy=base if (base in F or base == "USD") else quote, src="state/oanda/snapshot.json"))
        if d.get("nav"): nav["OANDA"] = float(d["nav"])
    def v2():
        d = _j("reports/v2/tfo_v2_book.json") or {}
        for s, p in (d.get("positions") or {}).items():
            P.append(pos("TFO V2", s, float(p.get("qty") or 0) * float(p.get("mark_usd") or p.get("entry_px") or 0) / fx, p.get("side") or "long", hint=p.get("cluster"), src="reports/v2/tfo_v2_book.json"))
        st = _j("reports/v2/tfo_v2_staged.json") or {}
        for o in st.get("orders") or []:
            if str(o.get("status")).upper() in ("STAGED", "PENDING", "ARMED"):
                P.append(pos("TFO V2 (staged)", o.get("sym"), float(o.get("indicative_usd") or 0) / fx, hint=o.get("cluster"), staged=True, src="tfo_v2_staged.json"))
        if d.get("equity"): nav["TFO V2"] = float(d["equity"]) / fx
    def fundamental():
        for path, book in (("reports/fundamental_book.json", "Fundamental"), ("reports/fundamental_spec_book.json", "Fundamental spec")):
            d = _j(path) or {}
            for s, p in (d.get("positions") or {}).items():
                usd = float(p.get("units") or 0) * float(p.get("mark_usd") or p.get("entry_px_usd") or 0)
                P.append(pos(book, s, usd / fx, p.get("side") or "long", ccy=p.get("entry_ccy") or ccy_of(s), hint=p.get("sector") or p.get("ht_theme"), src=path))
    def crypto():
        import glob
        for f in glob.glob(os.path.join(BASE, "reports", "crypto_*book*.json")):
            d = _j(f) or {}; m = d.get("mark_px_usd") or {}
            for s, q in (d.get("holdings") or {}).items():
                if q: P.append(pos("Crypto " + os.path.basename(f)[:-5], s, float(q) * float(m.get(s) or 0) / fx, ccy="USD", src=os.path.relpath(f, BASE)))
    for n, fn in (("trend18", trend18), ("trend_core", trend_core), ("essentials", essentials), ("hunts", hunts), ("oanda", oanda), ("v2", v2), ("fundamental", fundamental), ("crypto", crypto)):
        guard(n, fn)
    fr = _j("reports/firm_risk_state.json") or {}
    if fr.get("firm_equity_usd"): nav["Firm (firm_risk: Trend18/fundamental/floors)"] = float(fr["firm_equity_usd"]) / fx; nav.pop("Trend18", None)
    return P, nav, errs

def stress_one(p, cfg):
    th, und, lev = classify(p["sym"], p.get("hint"))
    sh = cfg["shocks"]; r = lev * float(sh.get(und, sh["EQ"]))
    fxm = 1.0 if p["ccy"] == "AUD" else 1.0 / (1.0 + float(sh["AUD_FX"]))
    pnl = p["sign"] * p["value_aud"] * ((1 + r) * fxm - 1)
    return th, und, lev, -pnl
def build(cfg=None, now=None, positions=None, nav=None):
    cfg = cfg or load_cfg(); now = now or dt.datetime.now(WITA); fx = fx_audusd(cfg); errs = []
    if positions is None: positions, nav, errs = collect(cfg, fx)
    firm_nav = float(cfg.get("firm_nav_aud_override") or sum((nav or {}).values()))
    budget = firm_nav * float(cfg["budget_pct_of_firm_nav"]); cap = budget * float(cfg["theme_cap_frac_of_budget"])
    T = {k: dict(theme=k, label=LABEL[k], net_aud=0.0, gross_aud=0.0, lev_adj_aud=0.0, staged_lev_adj_aud=0.0, stress_loss_aud=0.0, staged_stress_loss_aud=0.0, n=0, top=[]) for k in THEMES}
    rows = []
    for p in positions:
        th, und, lev, loss = stress_one(p, cfg)
        e = p["sign"] * p["value_aud"] * lev
        rows.append(dict(p, theme=th, underlying=und, lev=lev, lev_adj_aud=round(e), stress_loss_aud=round(loss)))
        targets = [th] + (["ai_infra"] if ai_frac(p["sym"], cfg) > 0 else [])
        for k in targets:
            f = 1.0 if k == th else ai_frac(p["sym"], cfg)
            t = T[k]; t["n"] += 1; t["net_aud"] += f * p["sign"] * p["value_aud"]; t["gross_aud"] += f * abs(p["value_aud"]); t["lev_adj_aud"] += f * e; t["stress_loss_aud"] += f * loss
            if p["staged"]: t["staged_lev_adj_aud"] += f * e; t["staged_stress_loss_aud"] += f * loss
            t["top"].append((abs(f * e), f"{p['sym']} {p['book']}{' [staged]' if p['staged'] else ''} A${f*e:+,.0f}"))
    amber = float(cfg["amber_pct_of_cap"])
    for t in T.values():
        t["top"] = [s for _, s in sorted(t["top"], reverse=True)[:4]]
        t["current_stress_loss_aud"] = t["stress_loss_aud"] - t["staged_stress_loss_aud"]; t["proforma_stress_loss_aud"] = t["stress_loss_aud"]
        t["current_pct_of_cap"] = round(t["current_stress_loss_aud"] / cap, 3) if cap > 0 else None
        t["proforma_pct_of_cap"] = round(t["proforma_stress_loss_aud"] / cap, 3) if cap > 0 else None
        t["pct_of_cap"] = t["current_pct_of_cap"] if cap > 0 else None   # 2026-10-28 14:10 Brett fundamental auto-trade (HT call): flag/block on HOLDINGS only; staged = info
        t["staged_info_pct_of_cap"] = t["proforma_pct_of_cap"]
        pc = t["pct_of_cap"] or 0
        t["flag"] = "RED" if pc >= 1 else ("AMBER" if pc >= amber else "GREEN")
        for k in ("net_aud", "gross_aud", "lev_adj_aud", "staged_lev_adj_aud", "stress_loss_aud", "staged_stress_loss_aud", "current_stress_loss_aud", "proforma_stress_loss_aud"): t[k] = round(t[k])
    total = sum(T[k]["stress_loss_aud"] for k in THEMES if k != "ai_infra")
    total_cur = sum(T[k]["current_stress_loss_aud"] for k in THEMES if k != "ai_infra")
    enf = enforcing(cfg)
    return dict(paper_only=True, warn_only=not enf, mode="enforce" if enf else "warn", never_blocks_or_resizes=not enf, never_forces_sales=True,
                blocks_new_buys_in=[t["theme"] for t in T.values() if (t["pct_of_cap"] or 0) >= 1] if enf else [], updated_wita=now.strftime("%a %d %b %Y %H:%M WITA"),
                updated_utc=now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), fx_audusd=fx, firm_nav_aud=round(firm_nav), nav_parts_aud={k: round(v) for k, v in (nav or {}).items()},
                stress_budget_aud=round(budget), theme_cap_aud=round(cap), budget_pct_of_firm_nav=cfg["budget_pct_of_firm_nav"], theme_cap_frac_of_budget=cfg["theme_cap_frac_of_budget"],
                scenario=cfg["shocks"], total_stress_loss_aud=round(total), total_current_stress_loss_aud=round(total_cur), total_current_pct_of_budget=round(total_cur / budget, 3) if budget else None, total_pct_of_budget=round(total / budget, 3) if budget else None,
                themes=[T[k] for k in THEMES], positions=rows, errors=errs,
                note="Info only. Stress loss = combined scenario (incl. AUD -5% revaluing non-AUD assets); AI-infra is a look-through SUB-theme of the others (not added to total). Staged = armed/queued/rebook deltas.")
# ---- 2026-10-28 13:40 WITA Brett approve-all #5: ENFORCE (was warn-only). Blocks NEW BUYS only; never forces or blocks sales.
def enforcing(cfg=None):
    return str((cfg or load_cfg()).get("mode", "warn")).lower() == "enforce"
def check_buy(sym, value_aud, ccy=None, hint=None, d=None, cfg=None, book="new buy"):
    """(ok, why). A buy is blocked when cap mode is 'enforce' and the buy ADDS stress loss to a theme that is already at/over its
    cap on HOLDINGS (current; staged shown as info only since 2026-10-28 14:10) or that the buy would push over its cap. Zero-/negative-stress buys
    (e.g. managed futures, shock 0) always pass. Any error -> fail closed (blocked) only while enforcing."""
    cfg = cfg or load_cfg()
    if not enforcing(cfg): return True, "theme cap warn-only"
    try:
        d = d or build(cfg=cfg)
        p = dict(book=book, sym=sym, value_aud=abs(float(value_aud)), sign=1, ccy=ccy or ccy_of(sym), hint=hint, staged=True)
        th, und, lev, loss = stress_one(p, cfg); cap = float(d["theme_cap_aud"])
        T = {t["theme"]: t for t in d["themes"]}
        hits = [th] + (["ai_infra"] if ai_frac(sym, cfg) > 0 else [])
        for k in hits:
            add = loss * (1.0 if k == th else ai_frac(sym, cfg))
            if add <= 0: continue
            t = T[k]; worst = t["current_stress_loss_aud"]   # 2026-10-28 14:10: HOLDINGS only block; staged (proforma) is info only
            if worst >= cap: return False, "theme cap ENFORCE: %s at %.0f%% of cap A$%s (stress A$%s) -> new buy %s blocked" % (k, 100 * worst / cap, f"{cap:,.0f}", f"{worst:,.0f}", sym)
            if worst + add > cap: return False, "theme cap ENFORCE: %s %s adds stress A$%s -> %.0f%% of cap A$%s -> blocked" % (sym, k, f"{add:,.0f}", 100 * (worst + add) / cap, f"{cap:,.0f}")
        return True, "theme cap ok (%s, stress add A$%s)" % (th, f"{loss:,.0f}")
    except Exception as e:
        return False, "theme cap ENFORCE: check failed (%s) -> blocked (fail closed)" % str(e)[:80]
def atomic(path, d):
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(d, f, indent=1, default=str)
    os.replace(tmp, path)
def write(now=None):
    d = build(now=now); atomic(OUT, d); atomic(STATIC, {k: v for k, v in d.items() if k != "positions"}); return d
if __name__ == "__main__":
    d = write()
    print(d["updated_wita"], "firm NAV A$%s budget A$%s cap/theme A$%s" % (f"{d['firm_nav_aud']:,}", f"{d['stress_budget_aud']:,}", f"{d['theme_cap_aud']:,}"))
    for t in d["themes"]: print(f"{t['label']:<48} lev-adj A${t['lev_adj_aud']:>11,} stress now A${t['current_stress_loss_aud']:>9,} ({t['current_pct_of_cap']:.0%}) +staged A${t['proforma_stress_loss_aud']:>9,} ({t['proforma_pct_of_cap']:.0%}) {t['flag']}")
    if d["errors"]: print("ERRORS", d["errors"])
