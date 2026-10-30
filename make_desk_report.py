#!/usr/bin/env python3
"""Desk report generator — one self-contained HTML page with every real position.

Pulls LIVE state from every book on the floor at generation time: the registered
Growth and Value books, the armed carry executor, the Kraken paper engine, and the
forward-test floors — positions, sizing, P&L, plus data-grounded company notes
from the value screen and the research dossiers.

Everything shown is PAPER. The page says so prominently.

Usage:  python3 make_desk_report.py     -> reports/desk_report.html
"""
import json
import os
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "reports", "desk_report.html")


def jf(path, fb=None):
    try:
        with open(os.path.join(BASE, "reports", path)) as f:
            return json.load(f)
    except Exception:
        return fb if fb is not None else {}


def engine_state():
    try:
        with urllib.request.urlopen("http://127.0.0.1:18799/state", timeout=8) as r:
            return json.load(r)
    except Exception:
        return {}


def money(x, cur="$"):
    if x is None:
        return "—"
    return "%s%s%,.2f" % ("-" if x < 0 else "", cur, abs(x)) \
        .replace("%s" % cur + "%", cur)


def m(x, cur="$"):
    if x is None:
        return "—"
    s = "{:,.2f}".format(abs(x))
    return ("-" if x < 0 else "") + cur + s


def cls(x):
    if x is None:
        return ""
    return "neg" if x < 0 else ("pos" if x > 0 else "")


# Data-grounded one-liners for the Value Book names (metrics from the live screen;
# qualitative facts kept to widely-established basics).
BLURBS = {
    "005930.KS": "Korea's memory & electronics giant. Screen's #1 composite — forward P/E ~3.4 prices a memory-cycle recovery at a fraction of US peer multiples.",
    "PDD": "Pinduoduo/Temu e-commerce. P/E ~9.5 with top-decile value rank AND strong growth rank — the screen's classic GARP hit. Carries China regulatory/geopolitical risk.",
    "BNP.PA": "Eurozone's largest bank. ~10x earnings, value rank 0.93 — the European-bank discount in one line.",
    "ITUB": "Brazil's biggest private-sector bank. ~10x earnings; an EM rates play as Brazil's cutting cycle matures.",
    "TTE": "French supermajor at ~10.6x trailing / 8.8x forward — cash-generative, but note the book already holds oil risk via USO in the Growth Book.",
    "SHEL": "Shell at under 10x earnings, FCF-heavy. Same oil-price overlap caveat as TotalEnergies.",
    "BARC.L": "Barclays at ~8x forward earnings — UK banks remain among the cheapest developed-market financials.",
    "MU": "Micron: trailing P/E 20 but forward ~5.7 — the screen is buying a priced-in memory-cycle earnings inflection. Quality rank 0.97. Cyclical, volatile.",
    "ADBE": "Adobe at ~9.4x forward — a fallen growth name where AI-disruption fear has compressed the multiple into value territory. The screen's bet is the fear is overpriced.",
    "TCEHY": "Tencent: China's platform giant, 17x trailing / 12x forward with high quality rank. Second China name — combined PDD+Tencent is ~20% of the book.",
    "SLV": "iShares Silver Trust — physical silver, 0.50% fee (the whole drag). Trailing year ~+57%; risks: high-beta-gold behaviour (~62% ann. vol), solar makers thrifting silver, crowded positioning.",
    "USO": "US Oil Fund — front-month WTI futures, rolled monthly. NOT spot oil: decade record +22% vs spot's +74% (roll drag). Trailing strength partly a war premium now deflating; contango regime would bleed several %/yr.",
}


def build():
    growth = jf("growth_book.json")
    value = jf("value_book.json")
    screen = jf("value_screen.json")
    carry_b = jf("carry_paper_book.json", {"positions": {}})
    carry_m = jf("carry_monitor.json")
    eng = engine_state()
    warroom_total = 1119

    screen_by_t = {r["ticker"]: r for r in screen.get("results", [])}
    now = time.strftime("%Y-%m-%d %H:%M")

    # ---------- rows ----------
    pos_rows = []
    # Value book: 10 names, equal-weight $5k
    # dict.get returns None -- NOT the default -- when the key exists with a null
    # value, which is exactly what value_book.py now writes when it refuses to
    # value a holding for want of an FX rate. `v_eq / n_v` then raises TypeError
    # during precisely the outage the refusal exists to announce.
    v_eq = value.get("equity")
    if v_eq is None:
        v_eq = 50000.0
        _v_eq_note = ("value book equity UNAVAILABLE (%s); sizes below are the "
                      "registered start, not a valuation"
                      % (value.get("equity_status") or "no reason recorded"))
        print("WARNING: " + _v_eq_note)
    n_v = max(1, len(value.get("holdings", {})))
    for p in value.get("last_picks", []):
        t = p["ticker"]
        s = screen_by_t.get(t, {})
        pos_rows.append({
            "book": "Value", "ticker": t, "name": p["name"],
            "country": p.get("country") or "—",
            "size": v_eq / n_v,
            "note": "comp %.3f · PE %s / fwd %s" % (
                p["composite"],
                round(s["trailing_pe"], 1) if s.get("trailing_pe") else "—",
                round(s["forward_pe"], 1) if s.get("forward_pe") else "—"),
            "blurb": BLURBS.get(t, ""),
        })
    # Growth book
    g_eq = growth.get("equity", 50000.0)
    for t in growth.get("holdings", {}):
        pos_rows.append({
            "book": "Growth", "ticker": t,
            "name": {"SLV": "iShares Silver Trust", "USO": "US Oil Fund"}.get(t, t),
            "country": "US-listed", "size": g_eq / max(1, len(growth.get("holdings", {}))),
            "note": "dual momentum top-2 · monthly", "blurb": BLURBS.get(t, ""),
        })
    # Carry
    for base_a, p in (carry_b.get("positions") or {}).items():
        pos_rows.append({
            "book": "Carry", "ticker": base_a, "name": "long spot / short perp",
            "country": "Kraken data", "size": p.get("notional_aud"),
            "note": "carry %.2f%% at entry" % p.get("entry_carry_pct", 0),
            "blurb": "Delta-neutral funding harvest — the one mechanism that clears its cost wall ~8x.",
        })

    books = [
        ("Growth Book", g_eq, growth.get("drawdown_pct"), "SLV + USO",
         "Dual momentum top-2 of 15 asset classes · monthly · registered 2026-08-06 · eval 2028-08-06"),
        ("Value Book", value.get("equity", 50000.0), value.get("drawdown_pct"),
         "%d global names" % len(value.get("last_picks", [])),
         "Global value/quality top-10 ex-traps · quarterly · benchmark VT · registered 2026-08-06"),
        ("Carry Executor", None, None,
         "FLAT — armed" if not carry_b.get("positions") else
         ", ".join(carry_b["positions"]),
         "Unvalidated PAPER carry; basket gate ≥32%%/yr, asset gate ≥16%%; now %.2f%%. Long spot / short perp BTC+ETH, A$33k total cap."
         % (carry_m.get("preferred_mean_annualised_pct") or 0)),
        ("Kraken Engine", eng.get("equity"), None,
         "FLAT" if not eng.get("positions") else "%d positions" % len(eng["positions"]),
         "Autonomous paper engine (A$) · %s trades closed · daily %s"
         % (len(eng.get("closed_trades", []) or []),
            m(eng.get("daily_pnl"), "A$"))),
    ]

    # ---------- html ----------
    try:
        import strategy_tracker as _ST; _trk = '<h2>Strategy Tracker (PAPER, best to worst, verdict = info only)</h2>' + _ST.html(_ST.write())
    except Exception as _e: _trk = '<p>Strategy Tracker unavailable: %s</p>' % _e
    h = []
    h.append("""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Desk Report · %s</title><style>
:root{--bg:#070b12;--panel:#0c1320;--line:#1c2a42;--fg:#dbe7f7;--dim:#7a8fb0;
--cyan:#38d5ca;--amber:#f0b429;--red:#ff5c6c;--green:#35e08b;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font-family:var(--mono);font-size:13px;line-height:1.55;padding:26px 30px 60px}
h1{font-size:17px;letter-spacing:.14em;text-transform:uppercase;margin:0}
h2{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--cyan);
margin:30px 0 10px;font-weight:600}
.sub{color:var(--dim);font-size:11px;margin-top:3px}
.banner{border:1px solid var(--amber);border-left-width:4px;border-radius:3px;
background:rgba(240,180,41,.06);padding:9px 14px;margin:16px 0;color:var(--amber);
font-size:11px;letter-spacing:.05em}
table{width:100%%;border-collapse:collapse;font-size:12px;margin-top:6px}
th,td{text-align:left;padding:6px 9px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-size:9px;letter-spacing:.12em;text-transform:uppercase}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.neg{color:var(--red)}.pos{color:var(--green)}.dim{color:var(--dim)}
.blurb{color:var(--dim);font-size:11px;max-width:560px}
.kpi{display:inline-block;border:1px solid var(--line);background:var(--panel);
border-radius:4px;padding:10px 16px;margin:8px 10px 0 0;min-width:150px}
.kpi .l{color:var(--dim);font-size:9px;letter-spacing:.12em;text-transform:uppercase}
.kpi .v{font-size:19px;margin-top:2px;font-variant-numeric:tabular-nums}
.foot{color:var(--dim);font-size:10px;margin-top:34px;border-top:1px solid var(--line);
padding-top:12px;line-height:1.7}</style></head><body>""" % now)

    h.append('<h1>Desk Report</h1><div class="sub">generated %s · all figures simulated (paper) · no broker connected</div>' % now)
    h.append(_trk)   # Strategy Tracker (Brett 2026-10-28)
    h.append('<div class="banner">PAPER TRADING ONLY — every position and dollar below is simulated. '
             'Costs are modelled. Not investment advice. Pre-registered rules; edits invalidate the registrations.</div>')

    # KPIs
    h.append('<div>')
    for label, val, cur in [("Growth Book", g_eq, "$"),
                            ("Value Book", value.get("equity"), "$"),
                            ("Engine (USD)", eng.get("equity"), "$"),
                            ("Carry", None, "")]:
        v = m(val, cur) if val is not None else ("PAPER · %.2f%%/32%%" %
                                                 (carry_m.get("preferred_mean_annualised_pct") or 0)
                                                 if label == "Carry" else "—")
        h.append('<div class="kpi"><div class="l">%s</div><div class="v">%s</div></div>'
                 % (label, v))
    h.append('<div class="kpi"><div class="l">Hypotheses tested</div><div class="v">~%d</div></div>' % warroom_total)
    # Exposure dial (registered 2026-08-15, spec 21e05244c53d4e77): cap-form
    # firm-vol dial, display-only here — reads its own report file, never computes.
    dial = jf("exposure_dial.json", {})
    if dial.get("scale") is not None:
        vol = dial.get("firm_vol_ann_pct")
        vol_text = "unavailable" if vol is None else "%.1f%%" % vol
        h.append('<div class="kpi"><div class="l">Exposure dial</div><div class="v">'
                 '%.2f · %s <span class="dim" style="font-size:11px">vol %s/%.0f%% · admission gate, not executed resizing</span>'
                 '</div></div>'
                 % (dial["scale"], dial.get("state", "?"),
                    vol_text, dial.get("target_vol_pct", 10)))
    # Post-boot report card (boot_notifier.py): shown for 24h after a reboot so a
    # recovering machine explains itself instead of leaving a bare page.
    boot = jf("boot_report.json", {})
    if boot.get("status") and boot.get("boot_epoch"):
        age_h = (time.time() - boot["boot_epoch"]) / 3600.0
        if age_h < 24:
            col = {"green": "var(--green)", "amber": "var(--amber)",
                   "red": "var(--red)"}.get(boot.get("level"), "var(--dim)")
            h.append('<div class="kpi"><div class="l">Boot report</div>'
                     '<div class="v" style="color:%s;font-size:13px">%s'
                     '<span class="dim" style="font-size:11px"> · rebooted %.1fh ago</span>'
                     '</div></div>' % (col, boot["status"], age_h))
    h.append('</div>')

    # ---- FIRM-WIDE DRAWDOWN (firm_risk.py, amendment 2026-08-27) ----------
    # The first firm-level number this desk has ever had. Rendered as a banner
    # rather than a KPI tile because a firm drawdown is not a statistic you scan
    # past. STALE state is shown as STALE — never as OK.
    fr = jf("firm_risk_state.json", {})
    if fr.get("firm_equity_usd") is not None:
        age_h = (time.time() - float(fr.get("epoch") or 0)) / 3600.0
        lvl = fr.get("level", "OK")
        col, msg = ("var(--green)", "within limits")
        if age_h > 6:
            col, lvl, msg = ("var(--dim)", "STALE", "state %.1fh old — not evaluated" % age_h)
        elif lvl == "HALT_NEW_ENTRIES":
            col, msg = ("var(--red)", "NEW ENTRIES HALTED FIRM-WIDE — nothing is "
                                      "liquidated; exits run normally")
        elif lvl == "WARN":
            col, msg = ("var(--amber)", "drawdown warning — nothing is blocked")
        h.append('<div style="border-left:4px solid %s;padding:8px 12px;margin:10px 0;'
                 'background:rgba(255,255,255,.03)"><b style="color:%s">FIRM %s</b> · '
                 'equity %s · peak %s · drawdown %s%% '
                 '<span class="dim">(warn %s%% / halt-new-entries %s%% · never '
                 'force-liquidates) · %d of %d books · %s</span></div>'
                 % (col, col, lvl, m(fr["firm_equity_usd"]), m(fr.get("firm_peak_usd")),
                    fr.get("firm_drawdown_pct"), fr.get("warn_dd_pct"),
                    fr.get("halt_dd_pct"), fr.get("books_readable", 0),
                    fr.get("books_total", 0), msg))
        for e in (fr.get("unreadable_books") or [])[:4]:
            h.append('<div class="dim" style="font-size:11px;padding-left:16px">'
                     '!! unreadable book: %s</div>' % e)

    # Books
    h.append('<h2>Books</h2><table><tr><th>Book</th><th class="num">Equity</th>'
             '<th class="num">DD</th><th>Holding</th><th>Mandate</th></tr>')
    for name, eq_v, dd, holding, mandate in books:
        h.append('<tr><td>%s</td><td class="num">%s</td><td class="num">%s</td>'
                 '<td>%s</td><td class="blurb">%s</td></tr>'
                 % (name, m(eq_v, "A$" if "Engine" in name else "$") if eq_v else "—",
                    ("%s%%" % dd) if dd is not None else "—", holding, mandate))
    h.append('</table>')

    # Positions with blurbs
    h.append('<h2>Positions &amp; sizing</h2><table><tr><th>Book</th><th>Ticker</th>'
             '<th>Company / instrument</th><th>Market</th><th class="num">Size</th>'
             '<th>Screen note</th></tr>')
    for r in pos_rows:
        h.append('<tr><td class="dim">%s</td><td>%s</td><td>%s</td><td class="dim">%s</td>'
                 '<td class="num">%s</td><td class="dim">%s</td></tr>'
                 % (r["book"], r["ticker"], r["name"], r["country"],
                    m(r["size"], "A$" if r["book"] == "Carry" else "$"), r["note"]))
        if r.get("blurb"):
            h.append('<tr><td></td><td colspan="5" class="blurb">%s</td></tr>' % r["blurb"])
    h.append('</table>')

    # Portfolio-level notes
    h.append('<h2>Concentrations &amp; risks (from the dossiers)</h2><table>')
    for note in [
        "Oil appears twice: USO (Growth) + TTE/SHEL (Value). The Growth position's trailing strength was partly a war premium now deflating; USO additionally bleeds roll costs in contango.",
        "China is ~20%% of the Value Book (PDD + Tencent) — single-jurisdiction regulatory risk.",
        "Financials + energy dominate the Value Book — the classic shape of a value screen; it is a rate-cycle and commodity-cycle bet, not ten independent ideas.",
        "Growth Book vol ≈ 38%% annualized: roughly $1,200/day one-sigma. Double-digit monthly drawdowns are within one standard deviation — expected, not malfunction.",
        "Value premium prior: real long-run (~2-4%%/yr) but with decade droughts and heavy post-publication decay. The forward test is the experiment.",
    ]:
        h.append('<tr><td class="blurb">%s</td></tr>' % note)
    h.append('</table>')

    # Watching
    fa_days = None
    try:
        import sqlite3
        c = sqlite3.connect("file:/Users/test/Applications/KrakenQuantDesk/kraken_bot/"
                            "market_intelligence.sqlite?mode=ro", uri=True)
        fa_days = round(c.execute("SELECT (MAX(ts)-MIN(ts))/86400.0 FROM "
                                  "futures_analytics").fetchone()[0] or 0, 1)
        c.close()
    except Exception:
        pass
    try:   # CSP sleeve line (Brett 2026-10-28 05:27 WITA): sleeve P&L + open puts, PAPER report-only
        import csp_sleeve_fwd
        h.append('<h2>CSP sleeve</h2><p>%s</p>' % csp_sleeve_fwd.summary_line())
    except Exception as e:
        h.append('<h2>CSP sleeve</h2><p>unavailable (%s)</p>' % str(e)[:80])
    h.append('<h2>Watching</h2><table>')
    h.append('<tr><td>Basis carry · unvalidated PAPER</td><td class="num">%.2f%% / 32%% basket trigger</td>'
             '<td class="blurb">Executor opens automatically at threshold.</td></tr>'
             % (carry_m.get("preferred_mean_annualised_pct") or 0))
    h.append('<tr><td>Futures panel</td><td class="num">%s / 21 days</td>'
             '<td class="blurb">The one untested dataset — ready ~Sept.</td></tr>'
             % (fa_days if fa_days is not None else "?"))
    h.append('<tr><td>News/Reddit corpus</td><td class="num">accumulating</td>'
             '<td class="blurb">Event study viable at ~3 weeks of span.</td></tr>')
    h.append('</table>')

    h.append('<div class="foot">Live dashboards: command :18811 · replica :18810 · jarvis :18808 · engine :18799.<br>'
             'Registrations: GROWTH_BOOK_PREREGISTRATION.md · VALUE_BOOK_PREREGISTRATION.md — quarterly reports, first 2026-11-06.<br>'
             'Research verdict to date: ~1,119 hypotheses across 13 streams; no validated after-cost alpha; two mechanisms deployed '
             '(risk-managed beta, armed carry). Every number above is paper and every cost is modelled.</div>')
    h.append('</body></html>')

    with open(OUT, "w") as f:
        f.write("\n".join(h))
    return OUT


if __name__ == "__main__":
    print("report ->", build())
