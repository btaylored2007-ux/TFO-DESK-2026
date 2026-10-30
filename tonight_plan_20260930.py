#!/usr/bin/env python3
"""TONIGHT PLAN — US session 30 Sep 2026 (Brett 19:10/19:11 AEST). PAPER. One night only; ends 06:05 AEST 1 Oct.
Regime from 10y yield (^TNX) change vs 22:25 AEST pre-print snapshot: <= -3bp SOFT -> TLT long only; >= +3bp HOT -> SH (1x inverse S&P) long
as the SPY-short proxy, managed on SPY levels; else MIXED -> cash. Never both. Regime entries 23:45-00:15 AEST.
MU: intraday only after 1082.66 break + retest hold, 23:45-04:00; flat by 05:40. FICO short skipped (V1 cannot short; borrow unverified).
Post-data fallbacks (22:48 AEST): AMZN (Claude validate5) + NVDA reclaim (ChatGPT validate5) when MIXED or leftover powder after TLT/SH/MU.
GLD skipped — mark already above ChatGPT entry 383.10-383.40 (no chase). Max two fallbacks. Flat by 05:40 AEST.
Idle-powder hunt (01:01 AEST ChatGPT/Claude hunt_idle_1001_0100): AAPL pullback long only (ChatGPT #1; Claude TSM/BA invalid on re-quote — TSM chase+semis-full, BA below stop). Entry by 03:30 AEST; flat 05:40. SPY twin-gated.
Size = LOSS / risk fraction, capped at desk/ADV cap and by unreserved GCL powder minus buffer. Entries via tfo_buy.py gates; exits via tfo_ticket.execute."""
import sys, os, json, time, subprocess, datetime as dt, urllib.request
from zoneinfo import ZoneInfo
ROOT = "/Users/test/Applications/MultiVenueDesk"; os.chdir(ROOT); sys.path.insert(0, ROOT)
import tfo_ticket as tt
SYD = ZoneInfo("Australia/Sydney"); UTC = dt.timezone.utc; PY = sys.executable
LOSS, CAP, BUF = 1e9, 300000.0, 5000.0  # Brett 19:19 "deploy max": size = desk cap per name, powder permitting
CAPS = {"TLT": 600000.0, "MU": 600000.0, "SH": 300000.0, "AMZN": 600000.0, "NVDA": 600000.0, "AAPL": 600000.0}  # $600k needs 10d ADV >= $2B
STATE = "reports/tonight_plan_20260930_state.json"; LOGF = "reports/tonight_plan_20260930.log"
def S(mo, d, h, m): return dt.datetime(2026, mo, d, h, m, tzinfo=SYD)
T_SNAP, T_OPEN, T_DEC, T_REG_END = S(9,30,22,25), S(9,30,23,30), S(9,30,23,45), S(10,1,0,15)
T_MU_END, T_MU_FLAT, T_FLAT, T_END = S(10,1,4,0), S(10,1,5,40), S(10,1,5,50), S(10,1,6,5)
T_IDLE_END = S(10,1,3,30)  # idle-powder entries no later than 13:30 ET
OPEN_UTC = dt.datetime(2026, 9, 30, 13, 30, tzinfo=UTC).timestamp()
def now(): return dt.datetime.now(SYD)
def log(msg):
    line = "%s %s" % (now().strftime("%Y-%m-%d %H:%M:%S"), msg); print(line, flush=True); open(LOGF, "a").write(line + "\n")
def ld():
    try: return json.load(open(STATE))
    except Exception: return {}
def sv(s): json.dump(s, open(STATE, "w"), indent=1, default=str)
def chart(sym, interval="1m", rng="1d"):
    u = "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=%s&range=%s&includePrePost=true" % (sym.replace("^", "%5E"), interval, rng)
    r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=15))["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    ts = r.get("timestamp") or []
    def col(k):
        a = q.get(k)
        return a if isinstance(a, list) else [None] * len(ts)
    o, h, l, c, v = col("open"), col("high"), col("low"), col("close"), col("volume")
    out = []
    for i, t in enumerate(ts):
        if i >= len(c) or c[i] is None: continue
        out.append(dict(
            t=t,
            o=o[i] if i < len(o) else None,
            h=h[i] if i < len(h) else c[i],
            l=l[i] if i < len(l) else c[i],
            c=c[i],
            v=(v[i] if i < len(v) and v[i] is not None else 0),
        ))
    return out
def last(sym):
    b = chart(sym); return b[-1]["c"] if b else None
def reg(bars): return [b for b in bars if b["t"] >= OPEN_UTC]
def vwap(bars):
    v = sum(b["v"] for b in bars); return sum((b["h"] + b["l"] + b["c"]) / 3 * b["v"] for b in bars) / v if v else None
def book(): return tt.load("fundamental_book")
def held(sym): return sym in (book().get("positions") or {})
def powder():
    g = tt.load(tt.GCL)
    return float(g["cash"]) - float(g.get("reserved_usd") or 0)
def buy(sym, risk_frac, theme, label, why):
    usd = min(LOSS / max(risk_frac, 1e-4), CAPS.get(sym, CAP), powder() - BUF)
    if usd < 20000: log("SKIP %s: size %.0f too small (powder %.0f)" % (sym, usd, powder())); return False
    r = subprocess.run([PY, "tfo_buy.py", sym, "%.0f" % usd, "--theme", theme, "--label", label, "--why", why, "--execute"], capture_output=True, text=True, timeout=180)
    ok = held(sym); log("BUY %s $%.0f risk %.2f%% -> %s | %s %s" % (sym, usd, 100 * risk_frac, "FILLED" if ok else "NOT FILLED", r.stdout.strip()[-600:], r.stderr.strip()[-300:]))
    return ok
def exit_(sym, act, rule):
    rows = [r for r in tt.scan(only=[sym]) if r["sym"] == sym and r["book"] == "fundamental_book"]
    if not rows or "_q" not in rows[0]: log("EXIT %s %s deferred: no quote" % (act, sym)); return None
    r = rows[0]; r["rail"] = act; r["exit_rule"] = rule; r["rail_level_usd"] = None
    line = tt.execute(r); log("EXIT %s %s %s -> %s" % (act, sym, rule, line)); return line
def rr_ok(entry, stop, t1, min_r=1.5):
    if entry is None or entry <= stop: return False
    return (t1 - entry) / (entry - stop) >= min_r
def step(s):
    t = now()
    if t >= T_SNAP and "tnx0" not in s:
        s["tnx0"], s["tlt0"], s["spy0"] = last("^TNX"), last("TLT"), last("SPY"); log("SNAPSHOT pre-print TNX %s TLT %s SPY %s" % (s["tnx0"], s["tlt0"], s["spy0"]))
    if t < T_DEC: return
    tnx = last("^TNX"); d = None if (tnx is None or s.get("tnx0") is None) else tnx - s["tnx0"]
    regime = "SOFT" if d is not None and d <= -0.03 else "HOT" if d is not None and d >= 0.03 else "MIXED"
    if s.get("regime_logged") != regime: log("REGIME %s (TNX %s vs %s delta %s)" % (regime, tnx, s.get("tnx0"), d)); s["regime_logged"] = regime
    # regime entries
    if T_DEC <= t < T_REG_END and not s.get("regime_trade"):
        if regime == "SOFT":
            b = reg(chart("TLT")); px = b[-1]["c"] if b else None; vw = vwap(b)
            if px and 78.55 <= px <= 78.85 and vw and px >= vw and min(x["l"] for x in b) > 78.10:
                if buy("TLT", (px - 78.10) / px, "rates", "edge", "Tonight plan: soft PCE/GDP, yields down; TLT holds 78.55-78.75 above VWAP; stop 78.10 T1 79.46 T2 80.23"):
                    s["regime_trade"] = "TLT"; s["tlt_stop"] = 78.10
        elif regime == "HOT":
            b15 = [x for x in reg(chart("SPY", "15m")) if x["t"] + 900 <= time.time()]; spy = last("SPY")
            if b15 and spy and b15[-1]["c"] < 762.35 and 758.5 <= spy < 762.35:
                if buy("SH", (766.98 - spy) / spy, "index_hedge", "beta", "Tonight plan: hot print; SPY 15m close under 762.35; SH as SPY-short proxy; SPY stop 766.98 T1 757.72 T2 749.60"):
                    s["regime_trade"] = "SH"; s["spy_entry"] = spy; s["spy_stop"] = 766.98
    if t >= T_REG_END and not s.get("regime_trade") and not s.get("regime_closed"):
        log("REGIME WINDOW CLOSED: no TLT/SPY entry (%s) -> cash/fallbacks" % regime); s["regime_closed"] = True
    # MU intraday
    if T_DEC <= t < T_MU_END and not s.get("mu_done"):
        b = reg(chart("MU"))
        if b:
            hi = max(x["h"] for x in b); px = b[-1]["c"]
            if hi > 1082.66:
                i = next(k for k, x in enumerate(b) if x["h"] > 1082.66); lo_after = min(x["l"] for x in b[i:])
                smh = chart("SMH", "1d", "5d"); semis_up = len(smh) >= 2 and last("SMH") > smh[-2]["c"]
                if 1083 <= px <= 1086.5 and lo_after >= 1075 and semis_up and len(b) - i >= 3:
                    s["mu_done"] = True
                    if buy("MU", (px - 1070) / px, "semis", "beta", "Tonight plan: MU break 1082.66 + retest hold; intraday only; stop 1070 T 1108.72; flat 05:40 AEST"):
                        s["mu_held"] = True
    # Fallbacks: MIXED from T_DEC, or leftover powder after primary fill / regime window closed. Max 2 (AMZN, NVDA). GLD skipped (past entry).
    n_fb = int(bool(s.get("amzn_held"))) + int(bool(s.get("nvda_held")))
    can_fb = T_DEC <= t < T_MU_END and n_fb < 2 and (
        regime == "MIXED"
        or bool(s.get("regime_trade"))
        or bool(s.get("regime_closed"))
    )
    if can_fb and not s.get("amzn_done") and not held("AMZN"):
        b = reg(chart("AMZN")); px = b[-1]["c"] if b else None; vw = vwap(b)
        # Claude validate5: entry 247.00-247.50, stop 245.40, T1 250.00; require VWAP hold + T1 >= 1.5R at fill
        if px and vw and 247.00 <= px <= 247.50 and px >= vw and rr_ok(px, 245.40, 250.00):
            s["amzn_done"] = True
            if buy("AMZN", (px - 245.40) / px, "megacap", "edge", "Tonight fallback: Claude validate5 AMZN Anthropic-stake; 247-247.50 above VWAP; stop 245.40 T1 250; flat 05:40 AEST"):
                s["amzn_held"] = True; s["amzn_stop"] = 245.40
    if can_fb and not s.get("nvda_done") and not held("NVDA"):
        # Skip NVDA if MU theme heat already on (correlated NVDA/MU/QQQ)
        if s.get("mu_held") or held("MU"):
            if not s.get("nvda_skip_mu"):
                log("SKIP NVDA fallback: MU theme heat already on"); s["nvda_skip_mu"] = True
        else:
            b = reg(chart("NVDA")); px = b[-1]["c"] if b else None; vw = vwap(b)
            # ChatGPT validate5 reclaim: entry 229-229.20 after reclaim hold above VWAP; stop 226.90 T1 232.80
            if px and vw and 229.00 <= px <= 229.20 and px >= vw and rr_ok(px, 226.90, 232.80):
                hi = max(x["h"] for x in b) if b else 0
                if hi >= 229.00:  # reclaim observed
                    s["nvda_done"] = True
                    if buy("NVDA", (px - 226.90) / px, "semis", "beta", "Tonight fallback: ChatGPT validate5 NVDA reclaim 229-229.20 above VWAP; stop 226.90 T1 232.80; flat 05:40 AEST"):
                        s["nvda_held"] = True; s["nvda_stop"] = 226.90
    # exits
    if s.get("regime_trade") == "TLT" and held("TLT"):
        px = last("TLT")
        if px is not None:
            if px <= s["tlt_stop"]: exit_("TLT", "FLAT", "TONIGHT_PLAN_STOP")
            elif px >= 80.23: exit_("TLT", "FLAT", "TONIGHT_PLAN_T2")
            elif px >= 79.46 and not s.get("tlt_half"):
                if exit_("TLT", "HALF", "TONIGHT_PLAN_T1"): s["tlt_half"] = True; s["tlt_stop"] = float(book()["positions"]["TLT"].get("rail_orig_entry_usd") or book()["positions"]["TLT"]["entry_px_usd"])
            elif t >= T_FLAT and not s.get("tlt_half"): exit_("TLT", "FLAT", "TONIGHT_PLAN_TIME_NO_T1")
    if s.get("regime_trade") == "SH" and held("SH"):
        spy = last("SPY")
        if spy is not None:
            if spy >= s["spy_stop"]: exit_("SH", "FLAT", "TONIGHT_PLAN_STOP_SPY")
            elif spy <= 749.60: exit_("SH", "FLAT", "TONIGHT_PLAN_T2_SPY")
            elif spy <= 757.72 and not s.get("sh_half"):
                if exit_("SH", "HALF", "TONIGHT_PLAN_T1_SPY"): s["sh_half"] = True; s["spy_stop"] = s["spy_entry"]
            elif t >= T_FLAT: exit_("SH", "FLAT", "TONIGHT_PLAN_TIME_FLAT")
    if s.get("mu_held") and held("MU"):
        px = last("MU")
        if px is not None:
            if px <= 1070: exit_("MU", "FLAT", "TONIGHT_PLAN_STOP")
            elif px >= 1108.72: exit_("MU", "FLAT", "TONIGHT_PLAN_T1")
            elif t >= T_MU_FLAT: exit_("MU", "FLAT", "TONIGHT_PLAN_TIME_PRE_EARNINGS")
    if s.get("amzn_held") and held("AMZN"):
        px = last("AMZN")
        if px is not None:
            if px <= s.get("amzn_stop", 245.40): exit_("AMZN", "FLAT", "TONIGHT_PLAN_FB_STOP")
            elif px >= 250.00: exit_("AMZN", "FLAT", "TONIGHT_PLAN_FB_T1")
            elif t >= T_MU_FLAT: exit_("AMZN", "FLAT", "TONIGHT_PLAN_FB_TIME")
    if s.get("nvda_held") and held("NVDA"):
        px = last("NVDA")
        if px is not None:
            if px <= s.get("nvda_stop", 226.90): exit_("NVDA", "FLAT", "TONIGHT_PLAN_FB_STOP")
            elif px >= 232.80: exit_("NVDA", "FLAT", "TONIGHT_PLAN_FB_T1")
            elif t >= T_MU_FLAT: exit_("NVDA", "FLAT", "TONIGHT_PLAN_FB_TIME")
    # Idle-powder hunt (hunt_idle_1001_0100): AAPL only. SPY twin-gated; TSM chase+semis-full; BA stop broken on re-quote.
    if T_DEC <= t < T_IDLE_END and not s.get("aapl_done") and not held("AAPL"):
        b = reg(chart("AAPL")); px = b[-1]["c"] if b else None; vw = vwap(b)
        # ChatGPT hunt: entry 337.10-337.30 after hold above 336.83 + 1m close >337.20; stop 336.70; T1 339.20; flat 05:40
        if px and vw and 337.10 <= px <= 337.30 and px >= vw and rr_ok(px, 336.70, 339.20):
            recent = b[-3:] if len(b) >= 3 else b
            held_above = min(x["l"] for x in recent) >= 336.83 if recent else False
            conf = recent[-1]["c"] >= 337.20 if recent else False
            if held_above and conf:
                s["aapl_done"] = True
                if buy("AAPL", (px - 336.70) / px, "megacap", "beta", "Idle powder hunt_idle_1001_0100: ChatGPT AAPL support retest 337.10-337.30 above VWAP; stop 336.70 T1 339.20; flat 05:40 AEST"):
                    s["aapl_held"] = True; s["aapl_stop"] = 336.70
    if s.get("aapl_held") and held("AAPL"):
        px = last("AAPL")
        if px is not None:
            if px <= s.get("aapl_stop", 336.70): exit_("AAPL", "FLAT", "TONIGHT_PLAN_IDLE_STOP")
            elif px >= 339.20: exit_("AAPL", "FLAT", "TONIGHT_PLAN_IDLE_T1")
            elif t >= T_MU_FLAT: exit_("AAPL", "FLAT", "TONIGHT_PLAN_IDLE_TIME")
def main():
    if "--probe" in sys.argv:
        for x in ("^TNX", "TLT", "SPY", "SH", "MU", "SMH", "AMZN", "GLD", "NVDA", "AAPL"): print(x, last(x))
        print("powder", powder(), "held TLT/SH/MU/AMZN/NVDA/AAPL", held("TLT"), held("SH"), held("MU"), held("AMZN"), held("NVDA"), held("AAPL")); return
    log("WATCHER START pid %d" % os.getpid()); s = ld()
    while now() < T_END:
        try: step(s); sv(s)
        except Exception as e: log("ERR %r" % e)
        time.sleep(30 if now() >= T_SNAP - dt.timedelta(minutes=1) else 120)
    log("WATCHER END state %s" % json.dumps(s, default=str))
if __name__ == "__main__": main()
