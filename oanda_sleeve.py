#!/usr/bin/env python3
"""oanda_sleeve.py - TFO OANDA PRACTICE sleeve (paper money only). Authorised by Brett 4 Oct 2026 19:06 AEDT (via parent agent).
# GIVEBACK DNA (Brett HARD 2026-10-29 17:17 WITA, see RULE_LOG.md): GIVEBACK DNA (Brett HARD 2026-10-29 17:17 WITA; spec final 17:20; PAPER): every bot and engine. Never sit through a reverse that gives back open profit. Mechanical early-bank trigger on an open winner BEFORE its normal half-bank: (a) peak >= +0.5R then >= 50% of that peak given back; OR (b) a bar closes through the prior bar's low (shorts: high) on >= 2x average volume; OR (c) a logged adverse news item. Action: bank half or more at the live mark, residual stop to BE then normal trail. Noise (no trigger) is not a turn. Never forces an entry; never sells what is not held. Every early bank logs reason + rail counterfactual (what the normal rail would have done). Normal half-bank = net A$ >= 1R of ORIGINAL entry risk after costs (NO +1% leg); Dex trend seat 1.5R.
# TODO(giveback): wire triggers (a) peak>=0.5R & >=50% giveback, (b) close through prior bar on >=2x avg vol, (c) logged news into this engine's exit path;
#   bank >= half, residual BE, log reason + rail counterfactual. Until wired, apply by hand. No logic change in this edit.
Modes:  snapshot  - read-only account/positions/marks -> state/oanda/snapshot.json (dashboard panel, labelled PAPER, NOT desk equity)
        daily     - once per new OANDA daily close: compute design signals, exit flips, trail stops, queue/place entries
        check     - every 15 min while FX is open: snapshot, verify every TFO trade has a broker stop, place queued entries when spreads are normal
        dry       - like daily but never sends orders (prints the plan)
HARD RAILS (code-enforced): practice host + account id hard-coded; every order carries stopLossOnFill; risk per trade <= 0.5% NAV (scale gate up to 1%, hard cap 2%;
27 Oct 2026 Brett funder override 'funder_override_risk_pct' in config.json -> 1%, never above 1%);
book max 8 / OXF1 max 4 open TFO trades; margin used <= 50% NAV after the order; (5 Oct 2026) no new entry from the Friday 17:00 NY close until 60 min after
the Sunday 17:00 NY reopen, and entry spread <= min(3x median spread, 10bp); Brett's pre-existing trades (61365 SPX500, 61368 NAS100) and any trade without
tag 'TFO' are never modified or closed; NAS100_USD/SPX500_USD are excluded from the TFO universe (account is netting, no hedging);
all TFO orders carry clientExtensions tag 'TFO'; every order/close/stop change is logged to state/oanda/trades.jsonl with reason.
API (5 Oct 2026): reads retry 401/429/5xx/timeouts 3x over ~20s; orders/closes/stop changes are never auto-resent (entries are looked up
by client id after any error; closes/stops are read back before at most one resend). Retries -> state/oanda/api_retries.jsonl.
Stops (5 Oct 2026): tighten-only; a stop change counts (event=stop_update, confirmed) only after the broker shows it; dry runs log
stop_dry; every check reconciles broker vs ledger stops -> state/oanda/stop_reconcile.json.
12 Oct 2026 (Brett 21:41 AEDT, RULE_LOG.md): ~0.5% risk/trade, max 8 open TFO trades, per-currency NET open risk <= 1.5% NAV, portfolio
open risk <= 4% NAV, 18-pair entry universe; risk above 0.5% only via the frozen scale gate (state/oanda/SCALE_GATE_FROZEN.json, oanda_scale_gate.py);
an UNKNOWN (None) exit score never closes a trade (logged signal_unknown, position held, stop still trailed).
12 Oct 2026 (GPT blueprint, RULE_LOG.md; maths in oanda_risk.py, formulas research/oanda_formulas.md): LIVE - frozen entry R0 + net R per
trade, Wilder ATR20, instrument metadata (pipLocation/tradeUnitsPrecision/minimumTradeSize), sizing = min(0.5% risk ceiling, 3% per-position
vol cap), cost gate (cost/R0 <= 0.10, spread <= 0.05 x ATR20, financing credits ignored), daily NAV / financing / metadata logs, new frozen
OXF1 EMA20/60 trend sleeve (13 Oct 2026: own 2% / 4 trades / 1.5% per-ccy budget on top of the book; combined per-ccy net logged, warn > 2.5%). SHADOW - GPT's tighter currency caps (would-have-blocked), TSMOM+carry score, constant
0.5% track. Desk caps (1.5% net / 4% total / 8 trades) and the frozen scale gate are unchanged.
Kill switch: create state/oanda/KILL -> no new entries (stops/exits still managed). Sleeve halt: TFO realised+unrealised P&L < -10% of start NAV.
13 Oct 2026 audit fixes (research/oanda_audit_20261013.md): OXF1 fresh-cross entries (>=300 bars, bar freshness); entry skipped without a
conversion factor; sizing at OANDA accountLoss conversion; caps charge frozen (actual-fill) R; per-decision client ids; OPEN_ONLY entries;
stop retries never loosen; crossed stops/trails exit; failed exits/trails leave the close unprocessed; atomic state files; realised P&L incl. financing.
"""
import os, sys, json, time, fcntl, socket, http.client, datetime as dt, urllib.request, urllib.error
try:
    import desk_doh; desk_doh.install(ibkr=False, oanda=True, extra=False)  # 2026-10-27 audit: scoped DoH for OANDA practice
except Exception as _doh_e:
    print("desk_doh unavailable: %r" % _doh_e, file=sys.stderr)
import oanda_risk as RK          # 12 Oct 2026: pure risk/sizing/cost/shadow maths
import oanda_reconcile_guard as RG   # 25 Oct 2026 (Brett, TFO Risk audited): reconcile >2h failing -> block entries, book UNVERIFIED
HOST = "https://api-fxpractice.oanda.com"          # PRACTICE ONLY - never change to api-fxtrade
ACCOUNT = "101-011-39617916-001"
TAG = "TFO"
ROOT = os.path.dirname(os.path.abspath(__file__))
ST = os.path.join(ROOT, "state", "oanda"); os.makedirs(ST, exist_ok=True)
LOG = os.path.join(ST, "trades.jsonl"); CFG = os.path.join(ST, "config.json")
PROTECTED_TRADE_IDS = {"61365", "61368"}
EXCLUDED = {"NAS100_USD", "SPX500_USD"}
# 17 Oct 2026 (Brett 10:29 AEDT, weekly cut review): config.json "entry_disabled_designs" (OXL2 carry CUT) - no new entries for
# those designs. Their open trades keep broker stop, trail and signal exit (they stay in "designs" so exit data still loads).
ENTRY_DISABLED_DESIGNS = set()   # set in state/oanda/config.json (OXL2 since 17 Oct 2026)
# 28 Oct 2026 13:14 WITA (Brett, 'Change the oanda to riskier version'): config.json "active_ruleset": "AGGRO" makes oanda_aggro.py
# (3% risk/trade, 25% account open-risk cap (was 15%; Brett 28 Oct 2026), group caps, stopLossOnFill, -15% weekly halt, -40% kill) the ONLY OANDA paper entry ruleset.
# The design (TFO) sleeve opens no new trades; its open trades keep broker stop, trail and signal exits. Undo: set "DESIGN" / delete key.
def aggro_active(c=None): return str((c or {}).get("active_ruleset") or "DESIGN").upper() == "AGGRO"
def entry_disabled(c=None):
    return set(ENTRY_DISABLED_DESIGNS) | set((c or {}).get("entry_disabled_designs") or [])
DEFAULT_CFG = {"risk_pct": 0.005, "risk_cap_pct": 0.02, "max_trades": 8, "ccy_net_cap_pct": 0.015, "open_risk_cap_pct": 0.04, "margin_cap_frac": 0.50, "atr_mult": 2.0, "spread_mult_max": 3.0,
               "start_nav_aud": 50829.70, "halt_dd_frac": 0.10, "designs": ["OXL1", "OXL2", "OXF1"], "live": True,
               "spread_cap_bp": 10.0, "reopen_blackout_min": 60,   # 5 Oct 2026 fill-quality rails (RULE_LOG.md)
               # 12 Oct 2026 GPT blueprint (RULE_LOG.md): C vol cap, E cost gate, F trend trail
               "vol_cap_pct": 0.03, "max_cost_r": 0.10, "max_spread_atr": 0.05, "hold_days": 30, "slip_spread_frac": 0.5,
               "trend_trail_atr": 3.0, "trend_entry_london": "08:05",
               # 13 Oct 2026 04:31 AEDT (Brett, RULE_LOG.md): OXF1 trend sleeve gets its OWN budget on top of the book's 4% / 8 trades
               "trend_open_risk_cap_pct": 0.02, "trend_max_trades": 4, "trend_ccy_net_cap_pct": 0.015, "combined_ccy_warn_pct": 0.025,
               # 13 Oct 2026 Part C1: quote-health policy (new entries only; broker stops untouched)
               "quote_max_age_s": 120,
               # 16 Oct 2026 (Brett 20:49 AEDT): commodity bucket + whole-sleeve ceiling
               "cmdty_risk_pct": 0.01, "cmdty_open_risk_cap_pct": 0.04, "cmdty_max_trades": 6, "cmdty_ccy_net_cap_pct": 0.03,
               "cmdty_group_cap_pct": {"METALS": 0.015, "OIL": 0.01}, "total_open_risk_cap_pct": 0.08}
TREND = "OXF1"
FUNDER_OVERRIDE_MAX = 0.01   # 27 Oct 2026 (Brett): funder override of the scale gate may never exceed 1% (book entries)
BASE_DESIGN_RISK = 0.005     # frozen gate base: oanda_designs.RISK values are expressed relative to this
def funder_override(c):
    """27 Oct 2026: validated funder override of the frozen scale gate -> float in (0, 1%] or None. Needs a non-empty approval note."""
    try:
        v = c.get("funder_override_risk_pct")
        if v is None or not str(c.get("funder_override_note") or "").strip(): return None
        v = min(float(v), FUNDER_OVERRIDE_MAX, 0.02)
        return v if v > 0 else None
    except Exception: return None
def cfg():
    c = dict(DEFAULT_CFG)
    if os.path.exists(CFG): c.update(json.load(open(CFG)))
    c["risk_pct"] = min(float(c["risk_pct"]), 0.02)        # rail: never above 2%
    # 27 Oct 2026 18:57 WITA (Brett, chat 'Lift OANDA paper risk to 1% per trade now'; PAPER only, RULE_LOG.md): explicit FUNDER
    # OVERRIDE of the 12 Oct frozen scale gate. config.json "funder_override_risk_pct" (+ "funder_override_note") raises the clamp floor
    # to the override, never above 1% (FUNDER_OVERRIDE_MAX) and never above the 2% rail. Without the key the frozen gate rules alone.
    # The frozen gate file (SCALE_GATE_FROZEN.json, sha-pinned) is NOT rewritten. Undo: delete the key and set risk_pct 0.005.
    ov = funder_override(c)
    try:                                                     # rail (12 Oct 2026): risk above 0.5% only if the frozen scale gate allows it
        import oanda_scale_gate as _g; c["risk_pct"] = min(c["risk_pct"], max(_g.allowed_risk_pct(ST), ov or 0.0))
    except Exception: c["risk_pct"] = min(c["risk_pct"], ov if ov else 0.005)
    c["funder_override_risk_pct"] = ov
    c["max_trades"] = min(int(c["max_trades"]), 10)         # rail (12 Oct 2026: 4 -> 8; 27 Oct 2026 19:02 WITA Brett 'OANDA: 10 trades at 1% each, raise open-risk cap to 10%': 8 -> 10, PAPER)
    c["ccy_net_cap_pct"] = min(float(c.get("ccy_net_cap_pct", 0.015)), 0.015)    # rail (12 Oct 2026)
    c["open_risk_cap_pct"] = min(float(c.get("open_risk_cap_pct", 0.04)), 0.10)  # rail (12 Oct 2026 4%; 27 Oct 2026 19:02 WITA Brett: book 4% -> 10%, PAPER)
    c["margin_cap_frac"] = min(float(c["margin_cap_frac"]), 0.50)  # rail
    c["trend_open_risk_cap_pct"] = min(float(c.get("trend_open_risk_cap_pct", 0.02)), 0.02)  # rail (13 Oct 2026): OXF1 own budget <= 2%
    c["trend_max_trades"] = min(int(c.get("trend_max_trades", 4)), 4)                         # rail (13 Oct 2026)
    c["trend_ccy_net_cap_pct"] = min(float(c.get("trend_ccy_net_cap_pct", 0.015)), 0.015)    # rail (13 Oct 2026)
    c["combined_ccy_warn_pct"] = min(float(c.get("combined_ccy_warn_pct", 0.025)), 0.025)    # warn threshold never looser than 2.5%
    c["spread_cap_bp"] = min(float(c.get("spread_cap_bp", 10.0)), 10.0)          # rail (5 Oct 2026): hard spread cap never above 10bp
    c["reopen_blackout_min"] = max(int(c.get("reopen_blackout_min", 60)), 60)  # rail (5 Oct 2026): >= 60 min after the weekly FX reopen
    c["vol_cap_pct"] = min(float(c.get("vol_cap_pct", 0.03)), 0.03)          # rail (12 Oct 2026): per-position vol cap never above 3%
    c["max_cost_r"] = min(float(c.get("max_cost_r", 0.10)), 0.10)            # rail (12 Oct 2026)
    c["max_spread_atr"] = min(float(c.get("max_spread_atr", 0.05)), 0.05)    # rail (12 Oct 2026)
    c["cmdty_risk_pct"] = min(float(c.get("cmdty_risk_pct", 0.01)), 0.01)                     # rail (16 Oct 2026 21:03, Brett): <= 1%
    c["cmdty_open_risk_cap_pct"] = min(float(c.get("cmdty_open_risk_cap_pct", 0.04)), 0.04)   # rail
    c["cmdty_max_trades"] = min(int(c.get("cmdty_max_trades", 6)), 6)                          # rail
    c["total_max_trades"] = min(int(c.get("total_max_trades", 10)), 10)   # rail (audit H15, Brett 27 Oct 2026 19:35 WITA): max 10 open TFO trades across ALL buckets (book + trend + cmdty); per-bucket 10/4/6 still apply
    c["cmdty_ccy_net_cap_pct"] = min(float(c.get("cmdty_ccy_net_cap_pct", 0.03)), 0.03)       # rail
    c["total_open_risk_cap_pct"] = min(float(c.get("total_open_risk_cap_pct", 0.08)), 0.10)   # rail (16 Oct 2026): whole sleeve <= 8%; 27 Oct 2026 19:07 WITA Brett 'Raise the OANDA sleeve-wide cap to 10%': <= 10% (PAPER)
    return c
def token():
    t = os.environ.get("OANDA_PRACTICE_TOKEN")
    if not t:
        p = os.path.expanduser("~/.tfo/oanda_practice_token")
        t = open(p).read().strip()
    return t
# ---------------- HTTP + bounded retry (5 Oct 2026, RULE_LOG.md) ----------------
# The 09:05 AEDT 5 Oct daily run died on a transient OANDA-side 401 (same token, host and account worked in the 09:06 run and since).
# READS (GET) retry 401/429/5xx/timeouts/connection errors: 3 tries, waits 5s then 15s (~20s). WRITES (orders, closes, stop changes)
# are NEVER auto-retried here: callers verify broker state first (place_entry / close_trade / set_stop).
READ_BACKOFF_S = (5.0, 15.0)
_sleep = time.sleep; _urlopen = urllib.request.urlopen          # injectable for offline tests
TRANSIENT_NET = (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, http.client.HTTPException)
class OandaHTTPError(RuntimeError):
    def __init__(self, code, path, msg):
        super().__init__(f"HTTP {code} {path}: {msg}"); self.code = code; self.path = path; self.body = msg
def retryable_http(code): return code == 401 or code == 429 or code >= 500
def is_transient(ex): return isinstance(ex, TRANSIENT_NET) or (isinstance(ex, OandaHTTPError) and retryable_http(ex.code))
def _note_retry(method, path, k, ex, wait):
    rec = dict(utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), method=method, path=path, attempt=k + 1,
               error=str(ex)[:200], wait_s=wait, pid=os.getpid(), mode=(sys.argv[1] if len(sys.argv) > 1 else None))
    print("oanda api retry %d %s %s in %.0fs: %s" % (k + 1, method, path, wait, str(ex)[:160]), file=sys.stderr)
    try:
        with open(os.path.join(ST, "api_retries.jsonl"), "a") as f: f.write(json.dumps(rec) + "\n")
    except Exception: pass
def _api_once(method, path, body, params):
    assert HOST.startswith("https://api-fxpractice."), "practice host only"
    url = HOST + path
    if params: url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json",
                                                                         "Accept-Datetime-Format": "RFC3339"})   # token re-read every try
    try:
        with _urlopen(req, timeout=30) as r: return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try: msg = e.read().decode()[:800]
        except Exception: msg = ""
        finally:
            try: e.close()
            except Exception: pass
        raise OandaHTTPError(e.code, path, msg) from None
def api(method, path, body=None, params=None, retry=None):
    """retry=None -> GET retried (bounded), anything else sent exactly once."""
    if retry is None: retry = (method == "GET")
    waits = READ_BACKOFF_S if retry else ()
    for k in range(len(waits) + 1):
        try:
            return _api_once(method, path, body, params)
        except Exception as ex:
            if k < len(waits) and is_transient(ex):
                _note_retry(method, path, k, ex, waits[k]); _sleep(waits[k]); continue
            raise
def acct(path=""): return f"/v3/accounts/{ACCOUNT}{path}"
def log(rec):
    rec["utc"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"); rec["paper_practice"] = True
    with open(LOG, "a") as f: f.write(json.dumps(rec) + "\n")
def now_utc(): return dt.datetime.now(dt.timezone.utc)
# ---------------- entry-quality rails (5 Oct 2026, execution bot fill-quality review: NZD_CHF trade 61587 filled 5 min after the
# Sunday reopen at ~26bp spread; the 3x rule allowed ~37bp). NEW entries only; never applied to closes, stops or trails. ----------------
def _ny(t):
    """UTC datetime -> naive New York wall-clock time. zoneinfo first; fallback = US DST rule (2nd Sun Mar 02:00 .. 1st Sun Nov 02:00)."""
    t = t.astimezone(dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return t.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
    except Exception:
        y = t.year
        def nth_sun(m, n): d = dt.datetime(y, m, 1); d += dt.timedelta(days=(6 - d.weekday()) % 7); return d + dt.timedelta(weeks=n - 1)
        start = nth_sun(3, 2) + dt.timedelta(hours=7); end = nth_sun(11, 1) + dt.timedelta(hours=6)   # 02:00 local, in UTC
        off = -4 if start <= t.replace(tzinfo=None) < end else -5
        return (t + dt.timedelta(hours=off)).replace(tzinfo=None)
def reopen_blackout(now=None, minutes=60):
    """None if new entries are allowed, else a defer reason. Blocks from the Friday 17:00 New York close through the Sunday 17:00 NY
    weekly FX reopen and for `minutes` (>= 60) after it, i.e. no new entry before Sunday 18:00 NY. Uses NY wall-clock, so DST is handled."""
    ny = _ny(now or now_utc()); wd = ny.weekday(); mins = ny.hour * 60 + ny.minute   # Mon=0 .. Sun=6
    if (wd == 4 and mins >= 17 * 60) or wd == 5 or (wd == 6 and mins < 17 * 60):
        return "defer:fx_weekend_closed (NY %s)" % ny.strftime("%a %H:%M")
    if wd == 6 and mins < 17 * 60 + max(int(minutes), 60):
        return "defer:reopen_blackout %dmin after Sunday 17:00 NY reopen (< %d)" % (mins - 17 * 60, max(int(minutes), 60))
    return None
def spread_limit(floor_sp, c):
    """Max spread (fraction) for a new entry: the tighter of spread_mult_max x the instrument's median daily-close spread and the hard cap."""
    return min(c["spread_mult_max"] * max(floor_sp or 0.0, 1e-6), c["spread_cap_bp"] / 1e4)
# ---------------- market data ----------------
_inst = None
def instruments():
    global _inst
    if _inst is None: _inst = {i["name"]: i for i in api("GET", acct("/instruments"))["instruments"]}
    return _inst
def candles(ins, n=320):
    j = api("GET", f"/v3/instruments/{ins}/candles", params={"granularity": "D", "count": n, "price": "BA",
                                                                  "dailyAlignment": 17, "alignmentTimezone": "America/New_York"})  # 17:00 NY closes
    out = []
    for c in j["candles"]:
        if not c["complete"]: continue
        b, a = c["bid"], c["ask"]
        out.append(dict(t=c["time"], o=(float(b["o"]) + float(a["o"])) / 2, h=(float(b["h"]) + float(a["h"])) / 2, l=(float(b["l"]) + float(a["l"])) / 2,
                        c=(float(b["c"]) + float(a["c"])) / 2, sp=(float(a["c"]) - float(b["c"])) / ((float(a["c"]) + float(b["c"])) / 2)))
    return out
def _ny_parse(t):
    """OANDA RFC3339 (nanoseconds) -> aware UTC datetime."""
    t = t.rstrip("Z"); base, _, frac = t.partition(".")
    return dt.datetime.fromisoformat(base).replace(tzinfo=dt.timezone.utc) + dt.timedelta(microseconds=int((frac + "000000")[:6]) if frac else 0)
def quote_health(px, c):
    """Part C1: None if the snapshot is usable for a NEW entry, else defer reason. A heartbeat is not a price; stale/missing quotes block
    new entries only - existing broker-side stops stay active."""
    if px is None: return "defer:quote_unavailable"
    age = px.get("quote_age_s")
    if "quote_age_s" in px and age is None: return "defer:quote_time_missing"
    if age is not None and age > c.get("quote_max_age_s", 120): return "defer:quote_stale %.0fs > %ss" % (age, c.get("quote_max_age_s", 120))
    return None
def pricing(insts):
    j = api("GET", acct("/pricing"), params={"instruments": ",".join(insts), "includeHomeConversions": "true"})
    hc = {h.get("currency"): h for h in (j.get("homeConversions") or [])}   # 13 Oct 2026 audit #4: accountLoss per currency
    out = {}
    for p in j["prices"]:
        if not p.get("bids") or not p.get("asks"): continue
        b = float(p["bids"][0]["price"]); a = float(p["asks"][0]["price"])
        qt = p.get("time"); age = None
        try: age = (now_utc() - _ny_parse(qt)).total_seconds() if qt else None
        except Exception: age = None
        out[p["instrument"]] = dict(bid=b, ask=a, mid=(a + b) / 2, sp=(a - b) / ((a + b) / 2), tradeable=p.get("tradeable", False),
                                    q2h=p.get("quoteHomeConversionFactors", {}), loss_cq=(hc.get(p["instrument"].split("_")[1]) or {}).get("accountLoss"), quote_time=qt, quote_received=now_utc().isoformat(), quote_age_s=age)
    return out
atr = RK.wilder_atr   # 12 Oct 2026: Wilder ATR20 (recursive, SMA-seeded) replaces the 20-day SMA of true range
def median(x): x = sorted(x); return x[len(x) // 2] if x else None
import oanda_designs as designs  # signal plug-ins (same formulas as research/oanda_ml/code)
# ---------------- TFO Behavior pre-trade gate (added 4 Oct 2026; NEW entries only; fail open) ----------------
BEH_BASE = ROOT; BEH_ERR = None
try:
    sys.path.insert(0, os.path.join(ROOT, "research", "behaviour")); import oanda_hook as BEH
except Exception as _ex:
    BEH = None; BEH_ERR = repr(_ex)[:200]
def behaviour_refs(p, cs, a):
    """Inputs for the chasing rule, from the daily bars daily() already fetched. reference_level = the signal bar's close
    (prior completed daily close) for BOTH designs: OXL1 (gold SMA200) and OXL2 (carry) are state signals, not breakouts,
    so 'chasing' = how far the fill has run past the close the signal was computed on (measured in ATR20 when atr_pct given).
    SMA200 distance is recorded for OXL1 as info only (using it as the reference would block most trend entries)."""
    try:
        r = dict(ref_level=cs[-1]["c"], ref_basis="signal-bar daily close (prior completed close)",
                 day_ret_pct=round((cs[-1]["c"] / cs[-2]["c"] - 1) * 100, 4) if len(cs) >= 2 and cs[-2]["c"] else None,
                 atr_pct=round(a / cs[-1]["c"] * 100, 4) if a and cs[-1]["c"] else None)
        if p.get("design") == "OXL1" and len(cs) >= 200:
            sma = sum(x["c"] for x in cs[-200:]) / 200.0; r["sma200"] = round(sma, 5); r["sma200_dist_pct"] = round((cs[-1]["c"] / sma - 1) * 100, 3)
        return r
    except Exception:
        return {}
def behaviour_ok(e, px, c, nav, live):
    """TFO Behavior gate for a NEW entry (never called for closes, stop updates or the KILL/halt path). True = proceed.
    Fail open: any error (incl. the module failing to import) -> True, logged as event=behaviour_gate_error."""
    try:
        if BEH is None:
            log(dict(event="behaviour_gate_error", instrument=e.get("ins"), error="import: %s" % BEH_ERR, live=live)); return True
        price = (px["ask"] if e["dir"] > 0 else px["bid"]) if px else e.get("close")
        risk_pct = entry_risk_frac(e, c)   # same as place_entry
        ok = BEH.gate_order(e["ins"], e["dir"], price=price, reference_level=e.get("ref_level"), theme=e["design"],
                            stop_px=(price - e["dir"] * e["stop_dist"]) if price else None, risk_aud=round(risk_pct * nav, 2),
                            day_return_pct=e.get("day_ret_pct"), atr_pct=e.get("atr_pct"), dry=not live, base=BEH_BASE,
                            baseline_size_usd=round(designs.RISK.get(e["design"], c["risk_pct"]) * nav, 2))
        print("behaviour gate %s %s %s: %s (price %s ref %s atr%% %s day%% %s)" % (e["ins"], e["design"], "long" if e["dir"] > 0 else "short",
              "PASS" if ok else "BLOCK", price, e.get("ref_level"), e.get("atr_pct"), e.get("day_ret_pct")))
        return ok
    except Exception as ex:
        try: log(dict(event="behaviour_gate_error", instrument=e.get("ins"), error=repr(ex)[:300], live=live))
        except Exception: pass
        return True
# ---------------- broker stop-fill reconciliation (TFO trades only; read-only on the broker) ----------------
SEEN = os.path.join(ST, "closed_seen.json")
STOP_REASONS = {"STOP_LOSS_ORDER", "TRAILING_STOP_LOSS_ORDER", "GUARANTEED_STOP_LOSS_ORDER"}
def reconcile_stop_fills():
    """Log each TFO trade closed by a broker-side stop as event=stop_filled (realized_pl_aud, units, price, time=fill time,
    trade_id). Source: GET /trades?state=CLOSED (last 100) -> for unseen TFO trades, their closingTransactionIDs ->
    ORDER_FILL with reason STOP_LOSS_ORDER / TRAILING_STOP_LOSS_ORDER / GUARANTEED_STOP_LOSS_ORDER. Dedupe: trade ids in
    state/oanda/closed_seen.json + any stop_filled already in trades.jsonl. Brett's/untagged trades are ignored. Never raises."""
    try:
        seen = set()
        if os.path.exists(SEEN):
            with open(SEEN) as f: seen = set(str(x) for x in json.load(f).get("ids", []))
        if os.path.exists(LOG):
            with open(LOG) as f:
                for ln in f:
                    try:
                        r = json.loads(ln)
                        if r.get("event") == "stop_filled" and r.get("trade_id"): seen.add(str(r["trade_id"]))
                    except Exception: pass
        closed = api("GET", acct("/trades"), params={"state": "CLOSED", "count": 100}).get("trades", [])
        new = []
        for t in closed:
            tid = str(t.get("id"))
            if tid in seen or not is_tfo(t): continue
            fill = None
            for xid in t.get("closingTransactionIDs") or []:
                x = api("GET", acct(f"/transactions/{xid}")).get("transaction") or {}
                if x.get("type") == "ORDER_FILL" and x.get("reason") in STOP_REASONS: fill = x; break
            if fill:
                tc = next((y for y in (fill.get("tradesClosed") or []) if str(y.get("tradeID")) == tid), {})
                log(dict(event="stop_filled", trade_id=tid, instrument=t["instrument"], design=(t.get("clientExtensions") or {}).get("comment"),
                         units=float(t.get("initialUnits") or 0), price=float(fill.get("price") or t.get("averageClosePrice") or 0),
                         realized_pl_aud=float(tc.get("realizedPL", t.get("realizedPL")) or 0), time=fill.get("time") or t.get("closeTime"),
                         open_price=float(t.get("price") or 0), open_time=t.get("openTime"), transaction_id=fill.get("id"), stop_type=fill.get("reason"),
                         source="broker reconcile", live=True))
                new.append(tid)
            seen.add(tid)
        tmp = SEEN + ".tmp"
        with open(tmp, "w") as f: json.dump({"ids": sorted(seen), "updated_utc": now_utc().isoformat()}, f)
        os.replace(tmp, SEEN)
        if new: print("stop_filled logged for trades", new)
        return new
    except Exception as ex:
        try: log(dict(event="reconcile_error", what="stop_fills", error=repr(ex)[:300]))
        except Exception: pass
        return None
# ---------------- state ----------------
def open_trades():
    return api("GET", acct("/openTrades"))["trades"]
def is_tfo(t):
    ce = t.get("clientExtensions") or {}
    return t["id"] not in PROTECTED_TRADE_IDS and ce.get("tag") == TAG
def summary(): return api("GET", acct("/summary"))["account"]
AGGRO_TAG = "AGGRO"   # 17 Oct 2026: oanda_aggro.py tags every order "AGGRO"
def owner_label(t):
    """Snapshot owner. 17 Oct 2026 (weekly cut review): AGGRO trades are labelled AGGRO, never as an untouched outside position;
    only Brett's PROTECTED_TRADE_IDS are 'Brett (pre-existing, untouched)'."""
    if is_tfo(t): return "TFO"
    if t["id"] in PROTECTED_TRADE_IDS: return "Brett (pre-existing, untouched)"
    if (t.get("clientExtensions") or {}).get("tag") == AGGRO_TAG: return "AGGRO"
    return "other (untouched)"
TFO_START_UTC = "2026-10-04T00:00:00"   # sleeve authorised 4 Oct 2026; no TFO trade opened before this
def closed_tfo_trades(max_pages=40):
    """13 Oct 2026 (GPT review): EVERY closed TFO trade, paging GET /trades?state=CLOSED with beforeID until trades opened before
    TFO_START_UTC (or the end). Raises if a page cannot be read (callers fall back / fail loudly)."""
    out = []; before = None
    for _ in range(max_pages):
        prm = {"state": "CLOSED", "count": 500}
        if before: prm["beforeID"] = before
        page = api("GET", acct("/trades"), params=prm).get("trades", [])
        if not page: return out
        out += [t for t in page if is_tfo(t)]
        ids = [int(t["id"]) for t in page]; before = str(min(ids))
        if len(page) < 500 or min(str(t.get("openTime") or "") for t in page) < TFO_START_UTC: return out
    raise RuntimeError("closed_tfo_trades: more than %d pages" % max_pages)
def snapshot(extra=None):
    a = summary(); tr = open_trades()
    rows = []
    for t in tr:
        ce = t.get("clientExtensions") or {}
        rows.append(dict(id=t["id"], instrument=t["instrument"], units=float(t["currentUnits"]), price=float(t["price"]), open_utc=t["openTime"],
                         upl_aud=float(t.get("unrealizedPL", 0)), margin_aud=float(t.get("marginUsed", 0)), tfo=is_tfo(t),
                         owner=owner_label(t),
                         design=ce.get("comment"), stop=(t.get("stopLossOrder") or {}).get("price"), trail=(t.get("trailingStopLossOrder") or {}).get("distance")))
    c = cfg()
    tfo_upl = sum(r["upl_aud"] for r in rows if r["tfo"])
    tfo_real = 0.0; real_src = "ledger close_fill/stop_filled (ex financing; broker read failed)"
    try:   # 13 Oct 2026 audit #12: realised = broker realizedPL + financing of closed TFO trades + financing accrued on open TFO trades
        cl = closed_tfo_trades(); cids = {str(x["id"]) for x in cl}
        op = [t for t in tr if is_tfo(t) and str(t["id"]) not in cids]   # r3 F2: a trade that closed between the two reads counts ONCE (as closed)
        tfo_upl = sum(float(t.get("unrealizedPL", 0)) for t in op)
        tfo_fin = sum(float(x.get("financing") or 0) for x in cl) + sum(float(t.get("financing") or 0) for t in op)
        tfo_real = (sum(float(x.get("realizedPL") or 0) for x in cl) + sum(float(t.get("realizedPL") or 0) for t in op)   # incl. partial closes (r2 F3)
                    + tfo_fin); real_src = "broker realizedPL + financing"
    except Exception:
        tfo_fin = None
    if real_src.startswith("ledger") and os.path.exists(LOG):
        for ln in open(LOG):
            try:
                r = json.loads(ln)
                if r.get("event") in ("close_fill", "stop_filled"): tfo_real += float(r.get("realized_pl_aud") or 0)   # broker stop-outs count too
            except Exception: pass
    snap = dict(label="OANDA PRACTICE sleeve - PAPER (practice account, not desk equity)", account=ACCOUNT, host="practice",
                asof_utc=now_utc().isoformat(timespec="seconds"), currency=a["currency"], nav=float(a["NAV"]), balance=float(a["balance"]),
                margin_used=float(a["marginUsed"]), margin_available=float(a["marginAvailable"]), upl=float(a["unrealizedPL"]),
                open_trade_count=int(a["openTradeCount"]), tfo_open=sum(1 for r in rows if r["tfo"]), tfo_upl_aud=tfo_upl, tfo_realized_aud=tfo_real, tfo_financing_aud=tfo_fin, tfo_realized_source=real_src, tfo_realized_incl_financing=real_src.startswith("broker"), pnl_unverified=not real_src.startswith("broker"),
                start_nav_aud=c["start_nav_aud"], trades=rows, designs=c["designs"], risk_pct=c["risk_pct"], max_trades=c["max_trades"], trend_max_trades=c["trend_max_trades"],
                kill=os.path.exists(os.path.join(ST, "KILL")), in_desk_equity=False, test_status=designs.STATUS, **RG.board(ST))
    if extra: snap.update(extra)
    tmp = os.path.join(ST, "snapshot.json.tmp"); json.dump(snap, open(tmp, "w"), indent=1); os.replace(tmp, os.path.join(ST, "snapshot.json"))
    return snap, tr
# ---------------- portfolio + per-currency risk caps (12 Oct 2026) ----------------
def legs(ins):
    """(base, quote) of an instrument; XAU_USD -> ('XAU','USD'). Long = +base / -quote."""
    b, q = ins.split("_"); return b, q
def ledger_entry_risk():
    """trade_id -> dict(risk_aud, stop_dist) from live entry_order lines."""
    out = {}
    if not os.path.exists(LOG): return out
    with open(LOG) as f:
        for ln in f:
            try: r = json.loads(ln)
            except Exception: continue
            if r.get("event") == "entry_order" and r.get("live") and (r.get("response") or {}).get("trade_id"):
                out[str(r["response"]["trade_id"])] = dict(risk_aud=r.get("r0_aud_filled") or r.get("r0_aud") or r.get("risk_aud"), stop_dist=r.get("stop_dist"),
                                                           r0_aud=r.get("r0_aud_filled") or r.get("r0_aud"), nav_aud=r.get("nav_aud"), design=r.get("design"))
    return out
def open_risk_rows(trades, nav, c):
    """One row per open TFO trade: ins, dir, risk_frac = loss to the CURRENT broker stop as a fraction of NAV (entry risk scaled by
    current stop distance / entry stop distance; 0 once the stop is at or past entry). Unknown -> charged the full configured risk (conservative)."""
    led = ledger_entry_risk(); rows = []
    for t in trades:
        if not is_tfo(t): continue
        d = 1 if float(t["currentUnits"]) > 0 else -1; L = led.get(str(t["id"])) or {}
        # 13 Oct 2026 audit (GPT N01/N02, Brett): caps charge the R FROZEN at entry (actual-fill R when logged), scaled only by
        # remaining/initial units - a trailed stop no longer releases budget.
        rf = (float(L["risk_aud"]) / nav) if L.get("risk_aud") and nav else c["risk_pct"]
        try:
            iu = float(t.get("initialUnits") or 0); cu = abs(float(t["currentUnits"]))
            if iu: rf *= min(1.0, cu / abs(iu))
        except Exception: pass
        rows.append(dict(ins=t["instrument"], dir=d, risk_frac=rf, design=designs.design_of(t)))
    return rows
CMDTY = ("OXP1", "OXB1", "OXT1")   # 16 Oct 2026 (Brett 20:49 AEDT): commodity trend pullback / vol-filtered breakout, own bucket
GROUP = {"XAU": "METALS", "XAG": "METALS", "XPT": "METALS", "XCU": "METALS", "BCO": "OIL", "WTICO": "OIL"}
def bucket(design):
    """13 Oct 2026: 'trend' (OXF1, own 2% / 4 trades / 1.5% per-ccy budget) or 'book' (OXL1/OXL2 and anything else: 4% / 8 / 1.5%).
    16 Oct 2026: 'cmdty' (OXP1/OXB1): own 2.5% / 4 trades; per-group caps METALS 1.5%, OIL 0.75% (one oil bet), USD 2.5%."""
    return "trend" if design == TREND else "cmdty" if design in CMDTY else "book"
def bucket_limits(design, c):
    if bucket(design) == "trend": return c["trend_open_risk_cap_pct"], c["trend_ccy_net_cap_pct"], c["trend_max_trades"]
    if bucket(design) == "cmdty": return c["cmdty_open_risk_cap_pct"], c["cmdty_ccy_net_cap_pct"], c["cmdty_max_trades"]
    return c["open_risk_cap_pct"], c["ccy_net_cap_pct"], c["max_trades"]
def ccy_net(rows):
    net = {}
    for r in rows:
        b, q = legs(r["ins"]); net[b] = net.get(b, 0.0) + r["dir"] * r["risk_frac"]; net[q] = net.get(q, 0.0) - r["dir"] * r["risk_frac"]
    return net
def caps_check(rows, ins, d, risk_frac, c, design=None):
    """None if adding (ins, d, risk_frac) keeps its BUCKET's open risk and every currency's |net risk| inside that bucket's caps.
    13 Oct 2026: book (OXL1/OXL2, design None = book) -> 4% total / 1.5% per ccy over book rows only; OXF1 -> 2% / 1.5% over OXF1 rows only."""
    eps = 1e-9; bk = bucket(design); tcap, ccap, _ = bucket_limits(design, c); tag = "" if bk == "book" else bk + "_"
    try:   # 16 Oct 2026 (Brett 21:37): ACCOUNT open risk (TFO + AGGRO) <= 15% NAV; AGGRO risk from its last check
        ag = sum(r["risk_frac"] for r in json.load(open(os.path.join(ROOT, "state", "oanda_aggro", "open_risk.json"))).get("rows", []) if r.get("owner") == "AGGRO")
    except Exception: ag = 0.0
    if sum(r["risk_frac"] for r in rows) + risk_frac + ag > 0.25 + eps:
        return "skip:account_open_risk_cap (TFO+AGGRO) %.2f%% > 25%%" % ((sum(r["risk_frac"] for r in rows) + risk_frac + ag) * 100)
    allt = sum(r["risk_frac"] for r in rows) + risk_frac      # 16 Oct 2026: whole-sleeve open-risk ceiling across all buckets
    if allt > c["total_open_risk_cap_pct"] + eps:
        return "skip:total_open_risk_cap %.2f%% > %.2f%%" % (allt * 100, c["total_open_risk_cap_pct"] * 100)
    if bk == "cmdty":   # 16 Oct 2026: correlated-asset group caps count EVERY bucket's rows (e.g. OXL1 gold counts toward METALS)
        g = GROUP.get(legs(ins)[0])
        if g:
            gn = sum(r["dir"] * r["risk_frac"] for r in rows if GROUP.get(legs(r["ins"])[0]) == g) + d * risk_frac
            gl = c["cmdty_group_cap_pct"].get(g, 0.0075)
            if abs(gn) > gl + eps: return "skip:cmdty_group_cap %s net %+.2f%% > %.2f%%" % (g, gn * 100, gl * 100)
    rows = [r for r in rows if bucket(r.get("design")) == bk]
    tot = sum(r["risk_frac"] for r in rows) + risk_frac
    if tot > tcap + eps:
        return "skip:%sopen_risk_cap %.2f%% > %.2f%%" % (tag, tot * 100, tcap * 100)
    net = ccy_net(rows + [dict(ins=ins, dir=d, risk_frac=risk_frac)])
    for k in legs(ins):
        if k == "XAU": continue
        if abs(net[k]) > ccap + eps:
            return "skip:%sccy_cap %s net %+.2f%% > %.2f%%" % (tag, k, net[k] * 100, ccap * 100)
    return None
def combined_ccy_check(rows, c, context="", live=False, extra=None):
    """13 Oct 2026: LOG-ONLY combined (book + OXF1) per-currency net risk -> state/oanda/combined_ccy.jsonl (every check);
    combined_ccy_warning also goes to trades.jsonl. Warns when any |net| > combined_ccy_warn_pct (2.5%). Never blocks."""
    try:
        net = ccy_net(rows + list(extra or [])); lim = c["combined_ccy_warn_pct"]
        over = {k: round(v * 100, 3) for k, v in net.items() if k != "XAU" and abs(v) > lim + 1e-9}
        _append("combined_ccy.jsonl", dict(event="combined_ccy_net", utc=now_utc().isoformat(timespec="seconds"), context=context, live=live, total_open_risk_pct=round(sum(r["risk_frac"] for r in rows + list(extra or [])) * 100, 3),
                 net_pct={k: round(v * 100, 3) for k, v in sorted(net.items())}, warn_pct=lim * 100))
        if over:
            w = dict(event="combined_ccy_warning", context=context, live=live, over_pct=over, warn_pct=lim * 100); log(w)
            _append("combined_ccy.jsonl", dict(w, utc=now_utc().isoformat(timespec="seconds")))
            print("WARNING combined ccy net >", lim * 100, "%:", over)
        return over
    except Exception as ex:
        log(dict(event="combined_ccy_error", error=repr(ex)[:200])); return {}
def entry_risk_frac(e, c):
    if bucket(e.get("design")) == "cmdty":   # 16 Oct 2026 (Brett 20:49 AEDT): 0.75% per trade, own rail (not the frozen scale gate)
        return min(float(e.get("risk_pct") or c["cmdty_risk_pct"]), c["cmdty_risk_pct"], 0.01)
    r = float(e.get("risk_pct") or c["risk_pct"])
    if c.get("funder_override_risk_pct"):   # 27 Oct 2026 (Brett): design risks scale with the override (0.5% -> 1%, OXF1 XAU 0.25% -> 0.5%)
        r = r * c["risk_pct"] / BASE_DESIGN_RISK
    return min(r, c["risk_pct"], 0.02)
# ---------------- order helpers ----------------
def fmt(ins, x): return f"{x:.{instruments()[ins]['displayPrecision']}f}"
def meta(ins): return RK.inst_meta(instruments()[ins])
def margin_needed(ins, units, px):
    return abs(units) * px["mid"] * RK.c_quote_loss(px) * meta(ins)["marginRate"]
def place_entry(e, live):
    c = cfg()
    if RG.entries_blocked(ST):   # 25 Oct 2026: fill/stop reconcile failing > 2 h -> no new entries until one clean pass
        log(dict(event="entry_blocked_reconcile", instrument=e.get("ins"), design=e.get("design"), live=live)); return "defer:reconcile_blocked"
    if e.get("design") in entry_disabled(c): return "skip:design_entries_disabled"   # 17 Oct 2026: OXL2 cut (queued items too)
    if aggro_active(c):   # 28 Oct 2026 13:14 WITA (Brett): AGGRO is the active OANDA paper ruleset -> design (TFO) sleeve opens nothing new
        log(dict(event="ruleset_skip", instrument=e.get("ins"), design=e.get("design"), live=live, reason="active_ruleset AGGRO (Brett 28 Oct)"))
        return "skip:ruleset_aggro_active"
    a = summary(); nav = float(a["NAV"])
    all_tr = open_trades(); tr = [t for t in all_tr if is_tfo(t)]
    bk = bucket(e["design"]); mx = bucket_limits(e["design"], c)[2]   # 13 Oct 2026: per-bucket trade count (book 8, OXF1 4)
    if any(t["instrument"] == e["ins"] for t in tr):
        if any(x.get("instrument") == e["ins"] for x in _load_exits()): return "defer:exit_pending"   # GPT r7/r8 F3: reversal waits for the exit (before slot check)
        return "skip:already_open"
    pend_x = bool(_load_exits())   # GPT r9 F3: a slot/cap shortfall while ANY deferred exit is pending may clear once it fills -> defer, not drop
    if len(tr) >= c["total_max_trades"]: return ("defer:" if pend_x else "skip:") + "total_max_trades" + ("_exit_pending" if pend_x else "")   # audit H15: 10 open across all buckets
    if sum(1 for t in tr if bucket(designs.design_of(t)) == bk) >= mx: return ("defer:" if pend_x else "skip:") + "%smax_trades" % ("" if bk == "book" else bk + "_") + ("_exit_pending" if pend_x else "")   # one position per pair across the WHOLE desk (13 Oct: kept)
    if any(t["instrument"] == e["ins"] for t in all_tr if not is_tfo(t)):          # 13 Oct 2026 audit (GPT N05): never trade into another owner's position
        return "skip:instrument_held_by_other_owner"
    if e["ins"] in EXCLUDED: return "skip:excluded"
    rows0 = open_risk_rows(tr, nav, c)
    capr = caps_check(rows0, e["ins"], e["dir"], entry_risk_frac(e, c), c, e["design"])   # 12 Oct caps; 13 Oct per bucket
    if capr:
        log(dict(event="entry_cap_block", instrument=e["ins"], design=e["design"], reason=capr, live=live, exit_pending=pend_x))
        return ("defer:%s_exit_pending" % capr.split(":", 1)[-1]) if pend_x else capr   # actual-position caps still enforced (no order placed)
    combined_ccy_check(rows0, c, "place_entry " + e["ins"], live, [dict(ins=e["ins"], dir=e["dir"], risk_frac=entry_risk_frac(e, c))])
    t_dec = now_utc().isoformat()
    try: px = pricing([e["ins"]]).get(e["ins"])
    except Exception as ex:                       # Part C1: snapshot/connection failure blocks the NEW entry only
        log(dict(event="quote_health_block", instrument=e["ins"], design=e["design"], reason="snapshot_failed", error=str(ex)[:200], live=live)); return "defer:quote_snapshot_failed"
    qh = quote_health(px, c) if px else None
    if qh:
        log(dict(event="quote_health_block", instrument=e["ins"], design=e["design"], reason=qh, quote_time=px.get("quote_time"), live=live)); return qh
    if not behaviour_ok(e, px, c, nav, live):   # TFO Behavior gate: entries only; a block drops the queued entry
        log(dict(event="entry_behaviour_block", instrument=e["ins"], design=e["design"], live=live)); return "skip:behaviour"
    if not px or not px["tradeable"]: return "defer:not_tradeable"
    if e.get("trigger") is not None:   # 16 Oct 2026 (GPT ideas 1-2): stop-entry emulation; the queue item expires after ~20h (next session only)
        lvl = float(e["trigger"]); p0 = px["ask"] if e["dir"] > 0 else px["bid"]
        if (e["dir"] > 0 and p0 < lvl) or (e["dir"] < 0 and p0 > lvl): return "defer:trigger_not_hit %s %s %s" % (p0, "<" if e["dir"] > 0 else ">", lvl)
    lim = spread_limit(e["floor_sp"], c)   # 5 Oct 2026: min(3x floor, hard 10bp cap)
    if px["sp"] > lim: return f"defer:spread {px['sp']*1e4:.1f}bp > limit {lim*1e4:.1f}bp (min of {c['spread_mult_max']}x {e['floor_sp']*1e4:.1f}bp, cap {c['spread_cap_bp']:.0f}bp)"
    risk_aud = entry_risk_frac(e, c) * nav            # risk CEILING (scale gate classifies steps on risk_aud/nav_aud)
    mt = meta(e["ins"]); cq = RK.c_quote_loss(px)
    if not cq:                                                   # 13 Oct 2026 audit #5: no conversion -> no entry (never assume 1.0)
        log(dict(event="entry_conversion_block", instrument=e["ins"], design=e["design"], reason="skip:no_conversion", live=live)); return "skip:no_conversion"
    ref = px["ask"] if e["dir"] > 0 else px["bid"]
    # Part C4: stop rounded OUTWARD to the instrument's price quantum, then size from the ACTUAL distance D = |ref - stop|
    stop_prop = ref - e["dir"] * e["stop_dist"]; qp = 10.0 ** -mt["displayPrecision"]
    stop = RK.round_stop_outward(stop_prop, e["dir"], qp); D = abs(ref - stop)
    u_raw, binding, u_risk, u_vol = RK.size_units(nav, D, ref, cq, e.get("sigma_ann"), risk_pct=entry_risk_frac(e, c), vol_cap=c["vol_cap_pct"])
    units = RK.round_units(u_raw, mt)
    if units <= 0: return "skip:units0 (below minimumTradeSize %s)" % mt["minimumTradeSize"]
    need = margin_needed(e["ins"], units, px)
    if float(a["marginUsed"]) + need > c["margin_cap_frac"] * nav:
        units = RK.round_units(units * max(0.0, (c["margin_cap_frac"] * nav - float(a["marginUsed"])) / need), mt); binding = "margin_cap"
        if units <= 0: return "defer:margin_cap_exit_pending" if pend_x else "skip:margin_cap"   # GPT r10 F3
        need = margin_needed(e["ins"], units, px)
    r0 = RK.r0_aud(units, D, cq)                       # A: frozen at entry, never shrunk by the trail
    sp_px = px["ask"] - px["bid"]; fin = (mt["financing"] or {}).get("longRate" if e["dir"] > 0 else "shortRate")
    try: fin = float(fin)
    except (TypeError, ValueError):   # 13 Oct 2026 (GPT N08): unknown funding is not zero funding
        log(dict(event="entry_cost_block", instrument=e["ins"], design=e["design"], reason="skip:no_financing_data", live=live)); return "skip:no_financing_data"
    cr = RK.cost_ratio(units, sp_px, c["slip_spread_frac"] * sp_px, ref, cq, fin, c["hold_days"], r0)
    cg = RK.cost_gate(units, sp_px, c["slip_spread_frac"] * sp_px, ref, cq, fin, c["hold_days"], r0, e.get("atr"), c["max_cost_r"], c["max_spread_atr"])
    if cg:
        log(dict(event="entry_cost_block", instrument=e["ins"], design=e["design"], reason=cg, cost_r=cr, spread_px=sp_px, atr=e.get("atr"),
                 financing_rate=fin, live=live)); return cg
    import firm_pretrade
    _pct = (r0 / nav) if nav else 0.0
    _side = "LONG" if e["dir"] > 0 else "SHORT"
    _why = firm_pretrade.blocked(dict(desk="oanda", seat="lab", ticker=e["ins"], side=_side, venue="OANDA", grade="B", action="entry",
                                      risk_pct=_pct, risk_aud=r0, notional_aud=abs(units) * float(ref), seat_equity_aud=nav,
                                      stop=float(stop), entry=float(ref)))
    if _why:
        log(dict(event="firm_risk_block", instrument=e["ins"], design=e.get("design"), reason=_why, live=live))
        return "skip:firm_risk:" + _why
    cid = client_id_for(e)                                        # 13 Oct 2026 audit (GPT N03): one broker id per DECISION
    if live and e.get("decision_id"):
        try: prior, _ptr = order_by_client_id(cid)
        except Exception as ex:
            log(dict(event="entry_idempotency_unverified", instrument=e["ins"], client_id=cid, error=str(ex)[:200], live=live)); return "defer:idempotency_lookup_failed"
        if prior is not None:
            log(dict(event="duplicate_decision_order_skip", instrument=e["ins"], design=e["design"], client_id=cid, decision_id=e.get("decision_id"),
                     broker_state=prior.get("state"), live=live))
            return "skip:decision_already_sent (%s)" % prior.get("state")
    body = {"order": {"type": "MARKET", "instrument": e["ins"], "units": str(units if e["dir"] > 0 else -units), "timeInForce": "FOK",
                      "positionFill": "OPEN_ONLY", "stopLossOnFill": {"price": fmt(e["ins"], stop), "timeInForce": "GTC"},
                      "clientExtensions": {"id": cid, "tag": TAG, "comment": e["design"]},
                      "tradeClientExtensions": {"id": cid, "tag": TAG, "comment": e["design"]}}}
    rec = dict(event="entry_order", design=e["design"], instrument=e["ins"], direction=("long" if e["dir"] > 0 else "short"), units=units,
               ref_price=ref, stop=float(fmt(e["ins"], stop)), stop_dist=D, stop_proposed=stop_prop, stop_rounded=stop, stop_quantum=qp,
               requested_units=units, filled_units=None, decision_time=t_dec, quote_time=px.get("quote_time"), quote_received_time=px.get("quote_received"),
               strategy_version=designs.VERSION.get(e["design"]), decision_id=e.get("decision_id"), risk_aud=round(risk_aud, 2), nav_aud=nav,
               margin_needed_aud=round(need, 2), score=e["score"], reason=e["reason"], test_status=designs.STATUS.get(e["design"]), live=live, client_id=cid,
               r0_aud=round(r0, 2), r0_frac=round(r0 / nav, 6), c_quote=cq, sizing_binding=binding, units_risk=round(u_risk, 2), units_vol=round(u_vol, 2),
               sigma_ann=e.get("sigma_ann"), atr20_wilder=e.get("atr"), cost_r=round(cr, 4) if cr is not None else None, financing_rate=fin,
               instrument_meta={k: mt[k] for k in ("pipLocation", "tradeUnitsPrecision", "minimumTradeSize", "marginRate")})
    if binding == "vol_cap": rec["vol_cap_note"] = "3%% vol cap shrank size from %.0f to %.0f units" % (u_risk, units)
    if not live: rec["event"] = "entry_dry"; log(rec); return "dry"
    try:
        rec["submit_time"] = now_utc().isoformat()
        r = api("POST", acct("/orders"), body, retry=False)          # sent exactly once
        fill = r.get("orderFillTransaction"); cancel = r.get("orderCancelTransaction")
        rec["filled_units"] = 0.0                                                         # Part C1/C7: requested != filled
        if fill: fill_accounting(rec, fill, dict(rows0=rows0, nav=nav, c=c, e=e, cq=cq, stop=stop))   # 13 Oct 2026 audit (GPT N02)
        rec["response"] = {"fill_price": fill.get("price") if fill else None, "trade_id": ((fill or {}).get("tradeOpened") or {}).get("tradeID"),
                           "cancel_reason": (cancel or {}).get("reason")}
        log(rec); return "filled" if fill else "cancelled:" + str((cancel or {}).get("reason"))
    except Exception as ex:
        rec["error"] = str(ex)[:500]
        return resolve_failed_entry(rec, cid, ex, ctx=dict(rows0=rows0, nav=nav, c=c, e=e, cq=cq, stop=stop))
def client_id_for(e):
    """13 Oct 2026 audit (GPT N03): deterministic clientExtensions id per decision (decision_id = VERSION|ins|bar). Legacy queue items
    without a decision_id keep the old minute-based id."""
    import hashlib
    if e.get("decision_id"):
        return ("TFO-%s-%s-%s" % (e["design"], e["ins"], hashlib.sha1(e["decision_id"].encode()).hexdigest()[:12]))[:128]
    return f"TFO-{e['design']}-{now_utc():%Y%m%d%H%M}-{e['ins']}"[:128]
def order_by_client_id(cid):
    """Read-only broker lookup of a TFO order by its clientExtensions id. Returns (order dict or None, open trade dict or None).
    None/None = OANDA confirms it does not exist. Raises if the broker state cannot be read (then the caller must NOT resend)."""
    order = None
    try: order = api("GET", acct(f"/orders/@{cid}")).get("order")
    except OandaHTTPError as e:
        if e.code != 404: raise
    trade = next((t for t in open_trades() if ((t.get("clientExtensions") or {}).get("id") == cid)), None)
    return order, trade
def fill_accounting(rec, fill, ctx):
    """13 Oct 2026: actual-fill R (fill price, filled units, fill lossQuoteHome) + post-fill cap check. Used by the normal and recovery paths."""
    cq = ctx["cq"]; stop = ctx["stop"]
    rec["filled_units"] = abs(float(fill.get("units") or 0)); rec["fill_time"] = fill.get("time"); rec["broker_transaction_id"] = fill.get("id")
    if fill.get("price") and rec["filled_units"]:
        fcq = cq
        try: fcq = float(((fill.get("homeConversionFactors") or {}).get("lossQuoteHome") or {}).get("factor") or cq)
        except Exception: pass
        rec["r0_aud_filled"] = round(RK.r0_aud(rec["filled_units"], abs(float(fill["price"]) - stop), fcq), 2); rec["c_quote_fill_loss"] = fcq
        try:   # recheck caps on the ACTUAL fill R (log only - the broker stop already protects it)
            e = ctx["e"]; pf = caps_check(ctx["rows0"], e["ins"], e["dir"], rec["r0_aud_filled"] / ctx["nav"], ctx["c"], e["design"]) if ctx["nav"] else None
            if pf: rec["post_fill_cap_breach"] = pf
        except Exception: pass
def resolve_failed_entry(rec, cid, ex, ctx=None):
    """An entry POST raised (timeout/401/5xx/...). Never resend blindly: look the order up by client id first.
    filled -> record it; cancelled/rejected -> report; confirmed absent + transient error -> 'defer:' so the entry stays queued and the
    NEXT scheduled run re-prices it and re-runs every gate (max_trades / already_open / behaviour / spread / margin); unreadable -> 'error'."""
    try:
        order, trade = order_by_client_id(cid)
    except Exception as ex2:
        rec.update(event="entry_unverified", verify_error=str(ex2)[:300]); log(rec)
        return "error:unverified (order state unknown; not resent)"
    if order is None and trade is None:
        rec["verify"] = "order not found at broker"
        if is_transient(ex):
            rec["event"] = "entry_not_placed"; log(rec); return "defer:order_not_placed (%s)" % str(ex)[:80]
        log(rec); return "error"
    st = (order or {}).get("state"); tid = (order or {}).get("tradeOpenedID") or (trade or {}).get("id")
    if trade is not None or st == "FILLED":
        rec["response"] = {"fill_price": (trade or {}).get("price"), "trade_id": tid, "cancel_reason": None, "recovered": "order found after error " + str(st)}
        try:   # 13 Oct 2026 (GPT review r2 F2): recovered orders use the order's own filling transaction, same accounting as a normal fill
            fx = None
            if (order or {}).get("fillingTransactionID"):
                try: fx = api("GET", acct("/transactions/%s" % order["fillingTransactionID"])).get("transaction")
                except Exception as exf: fx = None; rec["filling_tx_error"] = str(exf)[:120]   # r3 F1: fall back to the trade's own data
            if fx and ctx: fill_accounting(rec, fx, ctx); rec["response"]["fill_price"] = fx.get("price")
            elif trade is not None and trade.get("price") and ctx:
                fill_accounting(rec, {"price": trade["price"], "units": trade.get("initialUnits") or trade.get("currentUnits")}, ctx)
            else: rec["r0_aud_filled_missing"] = "recovered fill without filling transaction; caps charge planned r0"
        except Exception as exn: rec["r0_aud_filled_error"] = repr(exn)[:120]
        log(rec); return "filled"
    rec["response"] = {"fill_price": None, "trade_id": None, "cancel_reason": (order or {}).get("cancelledReason") or st, "recovered": "order found after error"}
    log(rec); return "cancelled:" + str(rec["response"]["cancel_reason"])
def close_trade(t, reason, live):
    if not is_tfo(t): raise RuntimeError("refusing to close non-TFO trade " + t["id"])
    rec = dict(event="close_order", trade_id=t["id"], instrument=t["instrument"], units=t["currentUnits"], reason=reason, live=live,
               design=(t.get("clientExtensions") or {}).get("comment"))
    if not live: rec["event"] = "close_dry"; log(rec); return
    for k in range(2):      # at most one resend, and only after the broker confirms the trade is still open
        try:
            r = api("PUT", acct(f"/trades/{t['id']}/close"), {"units": "ALL"}, retry=False)
            f = r.get("orderFillTransaction")
            if not f:   # 13 Oct 2026 (GPT review): a cancelled close is NOT an exit
                rec.update(event="close_cancelled", cancel_reason=(r.get("orderCancelTransaction") or {}).get("reason")); rec.pop("error", None); break
            rec.update(event="close_fill", price=f.get("price"), realized_pl_aud=f.get("pl")); rec.pop("error", None); break
        except Exception as ex:
            rec["error"] = str(ex)[:500]
            try: bt = api("GET", acct(f"/trades/{t['id']}")).get("trade") or {}
            except Exception as ex2: rec["verify_error"] = str(ex2)[:300]; break
            if bt.get("state") == "CLOSED":
                # closed by a broker stop meanwhile -> reconcile_stop_fills() books it as stop_filled (do not double count here);
                # closed by our own close order (response lost) -> book it as close_fill.
                why = None
                try:
                    for xid in bt.get("closingTransactionIDs") or []:
                        x = api("GET", acct(f"/transactions/{xid}")).get("transaction") or {}
                        if x.get("type") == "ORDER_FILL": why = x.get("reason"); break
                except Exception as ex3: rec["verify_error"] = str(ex3)[:300]
                if why in STOP_REASONS or why is None:
                    rec.update(event="close_already_closed", closed_by=why, recovered="trade already closed (stop/unknown); P&L via stop reconcile")
                else:
                    rec.update(event="close_fill", price=bt.get("averageClosePrice"), realized_pl_aud=bt.get("realizedPL"), recovered="own close found after error")
                rec.pop("error", None); break
            if not (is_transient(ex) and bt.get("state") == "OPEN" and k == 0): break
            rec["retried"] = True
    log(rec)
    return rec.get("event") in ("close_fill", "close_already_closed")   # 13 Oct 2026: False = exit NOT done (daily stays unprocessed)
def _same_px(ins, a, b):
    """True if two prices are equal at the instrument's display precision."""
    try: return a is not None and b is not None and fmt(ins, float(a)) == fmt(ins, float(b))
    except Exception: return False
def _looser(d, new, cur):
    """True if `new` is a looser stop than `cur` for direction d (+1 long, -1 short)."""
    return (d > 0 and float(new) < float(cur)) or (d < 0 and float(new) > float(cur))
def set_stop(t, price, reason, live):
    """Move a TFO trade's broker stop. 5 Oct 2026: TIGHTEN-ONLY vs the stop on `t`. The change counts as done (event=stop_update,
    confirmed=True) ONLY after the broker shows the new stop: trade read back, or the PUT response's stopLossOrderTransaction if the
    read-back can't be done. Otherwise event=stop_update_failed or stop_update_unconfirmed (nothing claims a stop the broker lacks).
    Dry runs log event=stop_dry (never stop_update). Returns True only when the broker confirmed it."""
    if not is_tfo(t): raise RuntimeError("refusing to modify non-TFO trade " + t["id"])
    ins = t["instrument"]; d = 1 if float(t.get("currentUnits") or 0) > 0 else -1
    cur0 = (t.get("stopLossOrder") or {}).get("price")
    rec = dict(event="stop_update", trade_id=t["id"], instrument=ins, new_stop=price, prev_broker_stop=cur0, reason=reason, live=live)
    if cur0 is not None and _looser(d, price, cur0):
        rec.update(event="stop_update_refused", why="would loosen the broker stop (tighten-only rail)"); log(rec); return False
    if not live:
        rec["event"] = "stop_dry"; log(rec); return False
    want = fmt(ins, price); rec["new_stop"] = float(want); resp = None; err = None
    for k in range(2):  # at most one resend, only after reading the trade back (replacing a stop with the same price is idempotent)
        try:
            resp = api("PUT", acct(f"/trades/{t['id']}/orders"), {"stopLoss": {"price": want, "timeInForce": "GTC"}}, retry=False); err = None
        except Exception as ex:
            err = ex; rec["error"] = str(ex)[:500]
        try:
            bt = api("GET", acct(f"/trades/{t['id']}")).get("trade") or {}
        except Exception as ex2:
            rec["verify_error"] = str(ex2)[:300]; bt = None
        if bt is not None:
            cur = (bt.get("stopLossOrder") or {}).get("price"); rec["broker_stop"] = float(cur) if cur is not None else None
            if cur is not None and not _same_px(ins, cur, want) and _looser(d, want, cur):
                # 13 Oct 2026 audit (GPT N04): broker already holds a TIGHTER stop -> never resend a looser one
                rec.update(confirmed=True, confirm_source="broker already tighter; not resent", broker_stop_order_id=(bt.get("stopLossOrder") or {}).get("id"))
                rec.pop("error", None); want = fmt(ins, float(cur)); break
            if _same_px(ins, cur, want):
                rec.update(confirmed=True, confirm_source="broker read-back", broker_stop_order_id=(bt.get("stopLossOrder") or {}).get("id"))
                if err is not None: rec["recovered"] = "stop at target after error"
                rec.pop("error", None); break
            if err is None or not (is_transient(err) and bt.get("state") == "OPEN" and k == 0):
                break
            rec["retried"] = True; continue
        # read-back impossible: accept only the broker's own PUT response as confirmation; never resend blind
        tx = (resp or {}).get("stopLossOrderTransaction") or {}
        if err is None and _same_px(ins, tx.get("price"), want):
            rec.update(confirmed=True, confirm_source="PUT response stopLossOrderTransaction", broker_stop=float(tx["price"]), broker_stop_order_id=tx.get("id"))
        break
    if not rec.get("confirmed"):
        rec["confirmed"] = False; rec["event"] = "stop_update_failed" if err is not None else "stop_update_unconfirmed"
    else:
        t.setdefault("stopLossOrder", {})["price"] = want   # later steps in this run see the confirmed broker stop
    log(rec); return bool(rec.get("confirmed"))
# ---------------- broker <-> ledger stop reconciliation (5 Oct 2026) ----------------
def stop_state_path(): return os.path.join(ST, "stop_reconcile.json")   # resolved at call time (tests repoint ST)
def ledger_stops():
    """trade_id -> last stop the LEDGER says the broker holds: the live entry's stopLossOnFill, then live stop_update lines without an
    error (legacy lines) / with confirmed=True, then stop_reconcile corrections. Dry lines (live=false, stop_dry, *_dry) never count."""
    out = {}
    if not os.path.exists(LOG): return out
    with open(LOG) as f:
        for ln in f:
            try: r = json.loads(ln)
            except Exception: continue
            ev = r.get("event")
            if ev == "entry_order" and r.get("live") and (r.get("response") or {}).get("trade_id"):
                out[str(r["response"]["trade_id"])] = dict(stop=r.get("stop"), src="entry_order", utc=r.get("utc"))
            elif ev == "stop_update" and r.get("live") is True and not r.get("error") and r.get("confirmed", True) and r.get("trade_id"):
                out[str(r["trade_id"])] = dict(stop=r.get("broker_stop", r.get("new_stop")), src="stop_update", utc=r.get("utc"))
            elif ev == "stop_reconcile" and r.get("trade_id") and r.get("ledger_stop_now") is not None:
                out[str(r["trade_id"])] = dict(stop=r["ledger_stop_now"], src="stop_reconcile", utc=r.get("utc"))
    return out
def reconcile_stops(trades, live):
    """Compare every open TFO trade's broker stop/trailing stop with the ledger. Broker is authoritative EXCEPT when the ledger holds a
    broker-CONFIRMED stop that is tighter than what the broker now shows (broker lost/loosened it): then re-push it (tighten-only, confirmed).
    Broker tighter / ledger has nothing -> append stop_reconcile (ledger corrected to broker). Missing broker stop -> left to check()'s
    restore path. Brett's / untagged trades are never looked at. Writes state/oanda/stop_reconcile.json. Never raises."""
    rows = []
    try:
        led = ledger_stops()
        for t in trades:
            if not is_tfo(t): continue
            ins = t["instrument"]; d = 1 if float(t["currentUnits"]) > 0 else -1
            b = (t.get("stopLossOrder") or {}).get("price"); trail = (t.get("trailingStopLossOrder") or {}).get("distance")
            l = (led.get(str(t["id"])) or {}).get("stop")
            row = dict(trade_id=t["id"], instrument=ins, broker_stop=float(b) if b is not None else None, broker_trailing_distance=trail,
                       ledger_stop=l, ledger_src=(led.get(str(t["id"])) or {}).get("src"), status="match", action=None)
            if b is None: row.update(status="broker_stop_missing", action="check() restore path")
            elif l is None or not _same_px(ins, l, b):
                if l is not None and _looser(d, b, l):
                    row.update(status="broker_looser_than_confirmed_ledger", action="re-push ledger stop (tighten-only)")
                    row["pushed_confirmed"] = set_stop(t, float(l), "reconcile: broker stop looser than broker-confirmed ledger stop", live)
                else:
                    row.update(status="ledger_corrected_to_broker", action="ledger set to broker stop")
                    if live:
                        log(dict(event="stop_reconcile", trade_id=t["id"], instrument=ins, ledger_stop_was=l, broker_stop=float(b), ledger_stop_now=float(b),
                                 action="ledger corrected to broker (broker authoritative)", live=True))
            rows.append(row)
        if live:
            sp = stop_state_path(); tmp = sp + ".tmp"
            with open(tmp, "w") as f: json.dump(dict(asof_utc=now_utc().isoformat(timespec="seconds"), trades=rows), f, indent=1)
            os.replace(tmp, sp)
        bad = [r for r in rows if r["status"] != "match"]
        if bad: print("stop reconcile:", json.dumps(bad, default=str))
        return rows
    except Exception as ex:
        try: log(dict(event="reconcile_error", what="stops", error=repr(ex)[:300]))
        except Exception: pass
        return None
# ---------------- 12 Oct 2026 GPT blueprint: trails, OXF1 timing, shadow + logging ----------------
def next_london(hhmm, now=None):
    """Next occurrence (UTC) of hh:mm Europe/London wall-clock, strictly after now."""
    from zoneinfo import ZoneInfo
    L = ZoneInfo("Europe/London"); n = (now or now_utc()).astimezone(L); h, m = map(int, hhmm.split(":"))
    t = n.replace(hour=h, minute=m, second=0, microsecond=0)
    if t <= n: t = (n + dt.timedelta(days=1)).replace(hour=h, minute=m, second=0, microsecond=0)
    return t.astimezone(dt.timezone.utc)
def trail_stop(t, cs, c):
    """(new stop, reason) on the daily close; tighten-only is enforced by the caller and set_stop.
    OXF1: highest (lowest for shorts) completed close since entry -/+ trend_trail_atr x Wilder ATR20. Others: close -/+ atr_mult x ATR20."""
    a = atr(cs) if cs else None
    if not a: return None, None
    d = 1 if float(t["currentUnits"]) > 0 else -1
    if designs.design_of(t) in ("OXF1",) + CMDTY:   # 16 Oct 2026: OXP1/OXB1 use the same 3xATR best-close trail (as backtested)
        # 13 Oct 2026 (audit #9, blueprint B3): completed closes whose bar ENDS after entry (bar t is the OPEN; it ends 24h later),
        # seeded with the entry fill as the starting reference.
        ot = t.get("openTime") or ""
        try: ot_dt = _ny_parse(ot) if ot else None
        except Exception: ot_dt = None
        since = [float(t["price"])] + [x["c"] for x in cs if ot_dt is None or _ny_parse(x["t"]) + dt.timedelta(days=1) > ot_dt]
        ext = max(since) if d > 0 else min(since)
        k = designs.TRAIL_ATR.get(designs.design_of(t), c["trend_trail_atr"])   # 16 Oct 2026: OXT1 6xATR
        return ext - d * k * a, designs.design_of(t) + " trail %s close since entry %s %sxATR20" % ("highest" if d > 0 else "lowest", "-" if d > 0 else "+", c["trend_trail_atr"])
    return cs[-1]["c"] - d * c["atr_mult"] * a, f"chandelier trail {c['atr_mult']}xATR20 on daily close"
def _time_exit(t, cs, dz, d):
    try:
        ot = _ny_parse(t["openTime"]); bars = sum(1 for x in cs if _ny_parse(x["t"]) >= ot)
        sd = (ledger_entry_risk().get(str(t["id"])) or {}).get("stop_dist")
        if not sd or bars < designs.TIME_EXIT_BARS[dz]: return False
        return d * (cs[-1]["c"] - float(t["price"])) < 0.5 * float(sd)
    except Exception: return False
def trend_stopouts():
    """instrument -> latest stop_filled time of an OXF1 trade (for the no-re-entry-in-same-regime rule)."""
    out = {}
    if not os.path.exists(LOG): return out
    for ln in open(LOG):
        try: r = json.loads(ln)
        except Exception: continue
        if r.get("event") == "stop_filled" and r.get("design") == "OXF1": out[r["instrument"]] = max(out.get(r["instrument"], ""), str(r.get("time") or ""))
    return out
def _write_json(name, obj):
    p = os.path.join(ST, name); tmp = p + ".tmp"; json.dump(obj, open(tmp, "w"), indent=1, default=str); os.replace(tmp, p)
def _append(name, rec):
    with open(os.path.join(ST, name), "a") as f: f.write(json.dumps(rec, default=str) + "\n")
def _pos_row(ins, d, risk_frac, units, px_mid, cq, nav):
    return dict(ins=ins, dir=d, risk_frac=risk_frac, notional_frac=abs(units) * px_mid * cq / nav if nav else 0.0)
def shadow_gpt_caps(trades, queue, data, nav, c, last_close, live):
    """D in SHADOW: would GPT's caps (net 1%, gross 1.5%, total initial risk 2%, |e_c|<=E, gross notional<=3E, 8% EWMA vol) have blocked
    each queued entry that passed OUR live caps? Logs event=shadow_gpt_caps and keeps a running count. Never blocks; never raises."""
    try:
        led = ledger_entry_risk(); ex = []
        try: ccys, rows = RK.factor_returns(data); S = RK.ewma_cov(rows) if rows else None; S_raw = RK.ewma_cov(rows, raw=True) if rows else None
        except Exception: ccys, S, S_raw = None, None, None
        for t in trades:
            if not is_tfo(t): continue
            u = float(t["currentUnits"]); L = led.get(str(t["id"])) or {}
            rf = (float(L["risk_aud"]) / nav) if L.get("risk_aud") and nav else c["risk_pct"]      # INITIAL risk, not trailed
            cs = data.get(t["instrument"]); P = cs[-1]["c"] if cs else float(t["price"])
            ex.append(_pos_row(t["instrument"], 1 if u > 0 else -1, rf, u, P, aud_per(t["instrument"], data), nav))
        new = []
        for e in queue:
            rf = entry_risk_frac(e, c); cq = aud_per(e["ins"], data)
            new.append(_pos_row(e["ins"], e["dir"], rf, (rf * nav) / (e["stop_dist"] * cq) if e["stop_dist"] else 0.0, e["close"], cq, nav))
        acc = list(ex); res = []
        for e, row in zip(queue, new):
            br = RK.gpt_caps_check(acc + [row], ccys, S, require_vol=True)
            a = RK.batch_a_star(acc, [row], ccys, {"shrunk": S})["a_star"]
            res.append(dict(ins=e["ins"], design=e["design"], dir=e["dir"], gpt_would_block=bool(br), breaches=br, max_scale_a=round(a, 3)))
            if not br: acc.append(row)
        # Part C2 (SHADOW): closed-form a_star over vol roots (raw + shrunk EWMA), signed net/notional ccy caps, gross ccy, total stop, gross notional
        ast = RK.batch_a_star(ex, new, ccys, {"raw": S_raw, "shrunk": S}) if new else dict(a_star=1.0, limiting="no candidates", bounds={})
        batch_a = ast["a_star"]
        ast0 = RK.batch_a_star([], new, ccys, {"raw": S_raw, "shrunk": S}) if new else None   # diagnostic: same batch on an empty book
        fb = None
        if new:   # Part C4 (SHADOW): integer units, recheck every cap on the ROUNDED batch, halve a until it passes
            u0 = [(rw["risk_frac"] * nav) / (e["stop_dist"] * aud_per(e["ins"], data)) if e["stop_dist"] else 0.0 for e, rw in zip(queue, new)]
            cands = [dict(ins=e["ins"], u=u, row=rw) for e, u, rw in zip(queue, u0, new)]
            def units_of(cd, a): return RK.round_units_q(a * cd["u"], 1)
            def chk(adm):
                rows_ = [dict(cd["row"], risk_frac=cd["row"]["risk_frac"] * (u / cd["u"]), notional_frac=cd["row"]["notional_frac"] * (u / cd["u"])) for cd, u in adm]
                br = RK.gpt_caps_check(ex + rows_, ccys, S, require_vol=True) + _raw_vol_breach(ex + rows_, ccys, S_raw)
                return ";".join(br) if br else None
            r = RK.rounding_fallback(cands, batch_a, units_of, chk)
            fb = dict(a=round(r["a"], 6), halvings=r["halvings"], limiting=r["limiting"], admitted={cd["ins"]: u for cd, u in r["admitted"]}, dropped=r["dropped"])
        vol_now = RK.forecast_vol(RK.ccy_notional(ex), ccys, S) if S else None
        rec = dict(event="shadow_gpt_caps", last_close=last_close, live=live, candidates=len(queue), would_block=sum(r["gpt_would_block"] for r in res),
                   batch_scale_a=round(batch_a, 3), a_star=round(batch_a, 6), a_star_limiting=ast["limiting"],
                   a_star_bounds={k: (None if v is None else (round(v, 6) if v != float("inf") else "inf")) for k, v in ast["bounds"].items()},
                   a_star_empty_book=(dict(a_star=round(ast0["a_star"], 6), limiting=ast0["limiting"]) if ast0 else None), rounding_fallback=fb, versions={"baseline_partB": designs.VERSION["OXF1"], "partA_shadow": designs.VERSION["SHADOW_PARTA"]}, forecast_vol_open=round(vol_now, 4) if vol_now is not None else None, rows=res)
        log(rec)
        if live:
            p = os.path.join(ST, "shadow_gpt_caps.json"); tot = json.load(open(p)) if os.path.exists(p) else dict(runs=0, candidates=0, would_block=0)
            if tot.get("last_close") != last_close:
                tot.update(runs=tot["runs"] + 1, candidates=tot["candidates"] + len(queue), would_block=tot["would_block"] + rec["would_block"], last_close=last_close,
                           note="SHADOW ONLY: GPT caps (net 1%, gross 1.5%, total 2%, notional, 8% vol) vs live caps (net 1.5%, total 4%)")
                _write_json("shadow_gpt_caps.json", tot)
        return rec
    except Exception as exn:
        log(dict(event="shadow_error", what="gpt_caps", error=repr(exn)[:300])); return None
def _raw_vol_breach(rows, ccys, S_raw):
    """GPT r7 F6: raw-EWMA vol recheck; unavailable raw vol is a FAILED constraint (never None > cap)."""
    if not S_raw or not ccys: return ["vol_raw_unavailable"] if any(abs(v) > 1e-12 and k != "USD" for k, v in RK.ccy_notional(rows).items()) else []
    v = RK.forecast_vol(RK.ccy_notional(rows), ccys, S_raw, RK.VOL_DAYS)
    if v is None: return ["vol_raw_unavailable"]
    return ["vol_raw"] if v > RK.GPT_CAPS["vol"] + 1e-9 else []
def aud_per(ins, data):
    """AUD per 1 unit of the quote currency, from daily closes (approximation for shadow maths only)."""
    q = ins.split("_")[1]
    if q == "AUD": return 1.0
    def last(i): cs = data.get(i); return cs[-1]["c"] if cs else None
    if q == "USD": v = last("AUD_USD"); return 1.0 / v if v else 1.5
    for i, inv in ((f"AUD_{q}", True), (f"{q}_AUD", False)):
        v = last(i)
        if v: return 1.0 / v if inv else v
    v1, v2 = last("AUD_USD"), last(f"USD_{q}")
    return 1.0 / (v1 * v2) if v1 and v2 else 1.0
def shadow_scores(data, last_close, live):
    """G in SHADOW: TSMOM 20/60/120 (clip +-2) + 0.2 x carry/vol using OANDA financing; signal if |score| > 0.25. Logged only."""
    try:
        out = []
        for ins, cs in sorted(data.items()):
            f = (designs.LIVE_FIN.get(ins) or {}); lr, sr = f.get("longRate"), f.get("shortRate")
            carry = (float(lr) - float(sr)) / 2 if lr is not None and sr is not None else None
            sc = RK.tsmom_carry_score(cs, carry)
            if sc is not None: out.append(dict(ins=ins, score=round(sc, 3), carry_ann=carry, signal=(1 if sc > 0.25 else -1 if sc < -0.25 else 0)))
        rec = dict(event="shadow_score", last_close=last_close, live=live, threshold=0.25, strategy_version=designs.VERSION["SHADOW_PARTA"], scores=out)
        _append("shadow_scores.jsonl", dict(rec, utc=now_utc().isoformat(timespec="seconds"))) if live else None
        return rec
    except Exception as exn:
        log(dict(event="shadow_error", what="score", error=repr(exn)[:300])); return None
def log_nav(snap):
    """H: one NAV row per UTC date in state/oanda/nav_daily.jsonl."""
    try:
        p = os.path.join(ST, "nav_daily.jsonl"); day = now_utc().date().isoformat()
        if os.path.exists(p):
            for ln in open(p):
                if json.loads(ln).get("date") == day: return False
        _append("nav_daily.jsonl", dict(date=day, utc=now_utc().isoformat(timespec="seconds"), nav=snap["nav"], balance=snap["balance"], upl=snap["upl"],
                                        tfo_open=snap["tfo_open"], tfo_upl_aud=snap["tfo_upl_aud"], tfo_realized_aud=snap["tfo_realized_aud"]))
        return True
    except Exception as exn:
        log(dict(event="log_error", what="nav", error=repr(exn)[:200])); return None
def log_financing():
    """H: append new DAILY_FINANCING transactions (all positions; TFO flagged per trade) to state/oanda/financing.jsonl."""
    try:
        sp = os.path.join(ST, "financing_state.json"); st = json.load(open(sp)) if os.path.exists(sp) else {}
        last = st.get("last_id")
        if last is None: last = max(1, int(summary()["lastTransactionID"]) - 2000)
        j = api("GET", acct("/transactions/sinceid"), params={"id": last, "type": "DAILY_FINANCING"})
        n = 0; seen = set(); fp = os.path.join(ST, "financing.jsonl")
        if os.path.exists(fp):
            for ln in open(fp):
                try: seen.add(str(json.loads(ln).get("id")))
                except Exception: pass
        for x in j.get("transactions", []):
            if x.get("type") != "DAILY_FINANCING" or str(x.get("id")) in seen: continue   # Part C5: each broker txn id booked once
            seen.add(str(x.get("id")))
            _append("financing.jsonl", dict(id=x.get("id"), time=x.get("time"), financing_aud=float(x.get("financing") or 0),
                                            positions=x.get("positionFinancings"))); n += 1
        st["last_id"] = j.get("lastTransactionID") or last; st["updated_utc"] = now_utc().isoformat(timespec="seconds"); _write_json("financing_state.json", st)
        return n
    except Exception as exn:
        log(dict(event="log_error", what="financing", error=repr(exn)[:200])); return None
def log_instrument_meta():
    """H: the instrument metadata used today -> state/oanda/instrument_meta/YYYY-MM-DD.json."""
    try:
        d = os.path.join(ST, "instrument_meta"); os.makedirs(d, exist_ok=True)
        json.dump({k: RK.inst_meta(v) for k, v in instruments().items()}, open(os.path.join(d, now_utc().date().isoformat() + ".json"), "w"), indent=1)
        return True
    except Exception as exn:
        log(dict(event="log_error", what="instrument_meta", error=repr(exn)[:200])); return None
def net_r_report():
    """A + I: every closed TFO trade -> net R = (realizedPL + financing) / frozen R0, plus the constant-0.5% shadow P&L
    (net R x 0.5% x NAV at entry). Legacy trades without r0_aud use the logged risk_aud (flagged). -> state/oanda/net_r.json."""
    try:
        led = ledger_entry_risk(); out = []
        for t in closed_tfo_trades():
            L = led.get(str(t["id"])) or {}; r0 = L.get("r0_aud") or L.get("risk_aud")
            nr = RK.net_r(t.get("realizedPL"), t.get("financing"), r0)
            out.append(dict(trade_id=t["id"], instrument=t["instrument"], design=(t.get("clientExtensions") or {}).get("comment"), open=t.get("openTime"),
                            close=t.get("closeTime"), realized_pl_aud=float(t.get("realizedPL") or 0), financing_aud=float(t.get("financing") or 0),
                            r0_aud=r0, r0_source="frozen r0_aud" if L.get("r0_aud") else ("legacy risk_aud" if r0 else "missing"), net_r=nr,
                            shadow_pnl_05_aud=(nr * 0.005 * float(L["nav_aud"])) if nr is not None and L.get("nav_aud") else None))
        rs = [x["net_r"] for x in out if x["net_r"] is not None]
        _write_json("net_r.json", dict(asof_utc=now_utc().isoformat(timespec="seconds"), n=len(out), sum_net_r=round(sum(rs), 4),
                                       mean_net_r=round(sum(rs) / len(rs), 4) if rs else None, net_aud=round(sum(x["realized_pl_aud"] + x["financing_aud"] for x in out), 2),
                                       shadow_05_aud=round(sum(x["shadow_pnl_05_aud"] or 0 for x in out), 2),
                                       shadow_05_note="normalised closed-trade attribution (net R x 0.5% x entry NAV); not a sized/marked shadow NAV, excludes open trades",
                                       trades=out))
        return out
    except Exception as exn:
        log(dict(event="log_error", what="net_r", error=repr(exn)[:200])); return None
def nav_reconcile(snap):
    """Part C5/C8 (13 Oct 2026): one row per UTC day -> state/oanda/nav_recon.jsonl: broker NAV (authoritative), external flows
    (TRANSFER_FUNDS, booked once by txn id; flows are NOT returns), flow-adjusted NAV return, and the constant-0.005 shadow series
    (cumulative net R x 0.5% x entry NAV from net_r.json; operational tier changes never rewrite it). Log only."""
    try:
        p = os.path.join(ST, "nav_recon.jsonl"); day = now_utc().date().isoformat(); prev = None
        if os.path.exists(p):
            for ln in open(p):
                r = json.loads(ln)
                if r.get("date") == day: return False
                prev = r
        sp = os.path.join(ST, "transfer_state.json"); st = json.load(open(sp)) if os.path.exists(sp) else {}
        if prev and prev.get("watermark_txn") is not None and int(st.get("last_id") or 0) < int(prev["watermark_txn"]):
            # GPT r7 F2: the recon row is the commit record - recover a checkpoint that failed after the row was written
            st["last_id"] = str(prev["watermark_txn"]); st["seen"] = sorted(set(st.get("seen", [])) | set(prev.get("transfer_ids") or []))
        a = summary(); wm = int(a["lastTransactionID"])   # GPT N07 / r7 F1: summary FIRST; flows counted only up to its watermark
        last = st.get("last_id") or max(1, wm - 2000); seen = set(st.get("seen", [])); flow = 0.0
        j = api("GET", acct("/transactions/sinceid"), params={"id": last, "type": "TRANSFER_FUNDS"})
        new_seen = set(seen)
        for x in j.get("transactions", []):
            if x.get("type") == "TRANSFER_FUNDS" and str(x["id"]) not in seen and int(x["id"]) <= wm: flow += float(x.get("amount") or 0); new_seen.add(str(x["id"]))
        snap = dict(snap, nav=float(a["NAV"]), balance=float(a["balance"]), upl=float(a["unrealizedPL"]))
        nr = json.load(open(os.path.join(ST, "net_r.json"))) if os.path.exists(os.path.join(ST, "net_r.json")) else {}
        nav = float(snap["nav"]); r_nav = RK.nav_return(float(prev["nav"]), nav, flow) if prev and prev.get("nav") else None
        _append("nav_recon.jsonl", dict(date=day, utc=now_utc().isoformat(timespec="seconds"), nav=nav, balance=snap.get("balance"), upl=snap.get("upl"),
                 external_flow_aud=round(flow, 2), r_nav=r_nav, flow_convention="end-of-period", operational_nav=nav,
                 shadow_const_005_cum_aud=nr.get("shadow_05_aud"), net_r_sum=nr.get("sum_net_r"), net_trades=nr.get("n"),
                 tfo_upl_aud=snap.get("tfo_upl_aud"), tfo_realized_aud=snap.get("tfo_realized_aud"),
                 cash_check_aud=round(float(snap.get("balance") or 0) + float(snap.get("upl") or 0) - nav, 2), watermark_txn=wm, transfer_ids=sorted(new_seen - seen),
                 shadow_note="shadow_const_005 = closed-trade net R x 0.5% x entry NAV (normalised attribution, not an independently sized/marked shadow NAV)"))
        st.update(last_id=str(wm), seen=sorted(new_seen)[-500:]); _write_json("transfer_state.json", st)   # checkpoint AFTER the row (GPT N07)
        return True
    except Exception as exn:
        log(dict(event="log_error", what="nav_recon", error=repr(exn)[:200])); return None
def daily_logs(snap, last_close):
    log_nav(snap); log_financing(); log_instrument_meta(); net_r_report(); nav_reconcile(snap)
# ---------------- modes ----------------
def lpb_key(design, ins): return f"{designs.VERSION.get(design, design)}|{ins}"
def load_lpb():
    p = os.path.join(ST, "last_processed_bar.json")
    try: return json.load(open(p)) if os.path.exists(p) else {}
    except Exception: return {}
def lock():
    fh = open(os.path.join(ST, ".lock"), "w"); fcntl.flock(fh, fcntl.LOCK_EX); return fh
def halted(snap):
    c = cfg(); pnl = snap["tfo_upl_aud"] + snap["tfo_realized_aud"]
    if snap.get("pnl_unverified"): return True     # 13 Oct 2026 (GPT review r2 F4): no authoritative P&L -> no NEW entries (stops/exits still run)
    return pnl < -c["halt_dd_frac"] * c["start_nav_aud"]
EXITS = "pending_exits.json"
def _load_exits():
    try: return json.load(open(os.path.join(ST, EXITS))).get("exits", []) if os.path.exists(os.path.join(ST, EXITS)) else []
    except Exception as ex: log(dict(event="state_read_error", file=EXITS, error=repr(ex)[:200])); return []
def _save_exits(xs): _write_json(EXITS, dict(updated_utc=now_utc().isoformat(timespec="seconds"), exits=xs))
def exit_spread_ok(ins, floor_sp, c):
    """(ok, spread_frac). Signal exits use the same spread limit as entries: min(3x median daily spread, 10bp). Unknown quote -> not ok."""
    try:
        px = pricing([ins]).get(ins)
        if not px or not px.get("tradeable"): return False, None
        return px["sp"] <= spread_limit(floor_sp, c), px["sp"]
    except Exception: return False, None
def request_exit(t, reason, floor_sp, c, live):
    """13 Oct 2026 (audit #3): a SIGNAL exit is never sent into the rollover spread. Close now if the spread is normal; otherwise
    persist an exit intent (state/oanda/pending_exits.json) that check() retries every 15 min and forces at the deadline
    (next 08:05 Europe/London). Broker stops stay active throughout. Returns True (closed or deferred) / False (close failed)."""
    ok, sp = exit_spread_ok(t["instrument"], floor_sp, c)
    if ok: return close_trade(t, reason, live)
    if not live:
        log(dict(event="exit_deferred_dry", trade_id=t["id"], instrument=t["instrument"], reason=reason, spread_bp=round(sp * 1e4, 2) if sp else None, live=False)); return True
    old = _load_exits(); prior = next((x for x in old if str(x.get("trade_id")) == str(t["id"])), None)
    xs = [x for x in old if str(x.get("trade_id")) != str(t["id"])]
    dl = next_london("08:05").isoformat()
    if prior and prior.get("deadline_utc") and prior["deadline_utc"] < dl: dl = prior["deadline_utc"]   # GPT r7 F4: keep the EARLIEST deadline
    x = dict(trade_id=str(t["id"]), instrument=t["instrument"], reason=reason, floor_sp=floor_sp, created_utc=(prior or {}).get("created_utc") or now_utc().isoformat(),
             deadline_utc=dl); xs.append(x); _save_exits(xs)
    log(dict(event="exit_deferred", spread_bp=round(sp * 1e4, 2) if sp else None, live=live, **x)); return True
def run_exits(trades, c, live):
    """13 Oct 2026 (audit #3): process deferred signal exits: trade gone -> drop; spread normal or deadline passed -> close (kept if it fails)."""
    xs = _load_exits()
    if not xs or not live: return
    open_by = {str(t["id"]): t for t in trades if is_tfo(t)}; keep = []
    for x in xs:
        t = open_by.get(str(x["trade_id"]))
        if t is None: log(dict(event="exit_intent_dropped", trade_id=x["trade_id"], why="trade no longer open (stopped/closed)")); continue
        due = dt.datetime.fromisoformat(x["deadline_utc"]) <= now_utc(); ok, sp = exit_spread_ok(x["instrument"], x.get("floor_sp"), c)
        if ok or due:
            if close_trade(t, x["reason"] + (" [deferred exit: spread normal]" if ok else " [deferred exit: deadline 08:05 London]"), live): continue
        keep.append(x)
    _save_exits(keep)
QUEUE_RULES = "audit-fixes-20261013"   # queue items carry the rule set that decided them; a same-bar retry keeps only these
def stop_through_market(ins, d, stop):
    """13 Oct 2026 audit #7: True if a protective stop at `stop` would already be triggered (long: bid <= stop; short: ask >= stop).
    None if no usable quote (caller then tries to set the stop and lets the broker reject it)."""
    try:
        px = pricing([ins]).get(ins)
        if not px: return None
        return (px["bid"] <= stop) if d > 0 else (px["ask"] >= stop)
    except Exception: return None
BAR_MAX_AGE_H = 80   # a Friday bar ends Fri 17:00 NY and is still current at the Sunday reopen (+48h) / Monday morning run
def bar_freshness(last_close, now=None):
    """13 Oct 2026 audit (GPT N10): None if the latest completed daily bar (candle OPEN time last_close; it ends 24h later) is current,
    else a skip reason. New entries only."""
    try:   # 13 Oct 2026 (GPT review): compare with the EXPECTED latest completed NY session (Mon-Fri 17:00 NY ends), not a blanket age
        n = _ny(now or now_utc()); exp = n.date() if n.hour >= 17 else n.date() - dt.timedelta(days=1)
        while exp.weekday() >= 5: exp -= dt.timedelta(days=1)          # no session ends Sat/Sun
        got = (_ny(RK_parse_t(last_close)) + dt.timedelta(days=1)).date()  # candle OPEN 17:00 NY -> ends next day 17:00 NY
        return None if got >= exp else "stale_bar: latest bar ends %s NY, expected %s" % (got, exp)
    except Exception as ex: return "stale_bar: unparseable %r" % (last_close,)
def RK_parse_t(t): return _ny_parse(t)
def daily(live=True):
    c = cfg(); live = live and c.get("live", True)
    _rf = reconcile_stop_fills()
    if _rf is None and live: RG.record_pass(ST, False, "stop_fills reconcile failed", log)
    if live:   # 12 Oct 2026: refresh the frozen scale-gate status (read-only GETs); failure leaves risk at its last allowed level / 0.5%
        try:
            import oanda_scale_gate as _g; _g.run_evaluate(ST); c = cfg()
        except BaseException as ex: log(dict(event="scale_gate_error", error=repr(ex)[:200]))
    snap, tr = snapshot()
    data = {}
    held = {t["instrument"] for t in tr if is_tfo(t)}
    for ins in list(dict.fromkeys(designs.universe(c["designs"]) + [i for i in designs.exit_universe(c["designs"]) if i in held])):
        if ins in EXCLUDED: continue
        try: data[ins] = candles(ins)
        except Exception as ex: log(dict(event="data_error", instrument=ins, error=str(ex)[:200]))
    last_close = max(cs[-1]["t"] for cs in data.values() if cs)
    stf = os.path.join(ST, "last_daily.json")
    try: done = json.load(open(stf)).get("last_close") if os.path.exists(stf) else None
    except Exception as ex: done = None; log(dict(event="state_read_error", file="last_daily.json", error=repr(ex)[:200]))
    if live and done == last_close:
        print("daily: already processed close", last_close); return
    designs.LIVE_FIN = {k: v.get("financing") or {} for k, v in instruments().items()}
    plan = designs.signals(c["designs"], data)   # list of dict(design, ins, dir, score, reason)
    _off = entry_disabled(c)
    if _off:   # 17 Oct 2026: cut designs queue nothing new (exits below still run for their open trades)
        for p in plan:
            if p["design"] in _off: log(dict(event="design_disabled_skip", instrument=p["ins"], design=p["design"], live=live))
        plan = [p for p in plan if p["design"] not in _off]
    tfo = [t for t in tr if is_tfo(t)]
    _rs = reconcile_stops(tr, live)
    if live and _rf is not None: RG.record_pass(ST, _rs is not None, None if _rs is not None else "stops reconcile failed", log)
    mgmt_fail = []   # 13 Oct 2026 audit (GPT N06): failed exits/trails keep this close UNPROCESSED so the next daily run retries
    # 1) exits on signal flip (per the trade's own design); 2) trail on daily closes (tighten-only)
    for t in tfo:
        ins = t["instrument"]; d = 1 if float(t["currentUnits"]) > 0 else -1; dz = designs.design_of(t)
        try: p = designs.score_for(dz, data, ins, d) if dz in CMDTY else designs.score_for(dz, data, ins)
        except Exception as ex: p = None; log(dict(event="score_error", instrument=ins, error=repr(ex)[:200]))
        if p is None:   # 12 Oct 2026: unknown signal (missing candles / rates) never closes a position
            log(dict(event="signal_unknown", trade_id=t["id"], instrument=ins, action="hold (None score never closes)", live=live))
        elif p == 0 and dz == "OXF1":
            pass        # OXF1 exits only on the OPPOSITE crossover; EMAs exactly equal is not a cross
        elif dz in designs.TIME_EXIT_BARS and _time_exit(t, data.get(ins), dz, d):   # 16 Oct 2026 (GPT idea 2): 60 bars and < +0.5R -> exit
            cs_x = data.get(ins) or []
            if request_exit(t, f"time exit: >= {designs.TIME_EXIT_BARS[dz]} bars and < +0.5R ({dz})", median([x["sp"] for x in cs_x[-260:]]) if cs_x else None, c, live) is False and live: mgmt_fail.append(("exit", t["id"]))
            continue
        elif p == 0 or (p > 0) != (d > 0):
            cs_x = data.get(ins) or []
            r_ex = request_exit(t, f"signal flip/flat (score {p}, design {dz})", median([x["sp"] for x in cs_x[-260:]]) if cs_x else None, c, live)
            if r_ex is False and live: mgmt_fail.append(("exit", t["id"]))
            continue
        ns, why = trail_stop(t, data.get(ins), c)
        if ns is not None:
            cur = float((t.get("stopLossOrder") or {}).get("price") or (0 if d > 0 else 1e18))
            if (d > 0 and ns > cur) or (d < 0 and ns < cur):
                if stop_through_market(ins, d, ns):   # 13 Oct 2026 audit #7: a crossed trail is an exit, not an invalid stop
                    if close_trade(t, "trail %s already through market (%s)" % (ns, why), live) is False and live: mgmt_fail.append(("trail_exit", t["id"]))
                elif not set_stop(t, ns, why, live) and live: mgmt_fail.append(("trail", t["id"]))
    # 3) entries: strongest signals first, up to max_trades; queued so 'check' can retry when spreads normalise
    open_ins = {t["instrument"] for t in open_trades() if is_tfo(t)} if live else {t["instrument"] for t in tfo}
    exiting = {x["instrument"] for x in _load_exits()} if live else set()
    open_ins -= exiting   # GPT r7 F3: a pair with a deferred exit can still queue its reversal; place_entry defers until the exit fills
    queue = []; seen_ins = set(); stopped = trend_stopouts(); lpb = load_lpb(); decided = {}
    fresh_why = bar_freshness(last_close)   # 13 Oct 2026 audit (GPT N10): the latest bar must be the CURRENT completed session
    for p in sorted(plan, key=lambda x: (designs.PRIORITY.get(x["design"], 9), -abs(x["score"]))):
        if p["ins"] in open_ins or p["ins"] in EXCLUDED or p["ins"] in seen_ins: continue   # one position per pair (netting account)
        # Part C1 (13 Oct 2026): one decision per completed bar per instrument + strategy version; stale bars never trade
        bar = data[p["ins"]][-1]["t"]; key = lpb_key(p["design"], p["ins"])
        if fresh_why:
            log(dict(event="bar_freshness_skip", instrument=p["ins"], design=p["design"], bar=bar, reason=fresh_why, live=live)); continue
        if bar != last_close:
            log(dict(event="bar_stale_skip", instrument=p["ins"], design=p["design"], bar=bar, last_close=last_close, live=live)); continue
        if live and lpb.get(key) == bar:
            log(dict(event="duplicate_decision_skip", instrument=p["ins"], design=p["design"], bar=bar, key=key)); continue
        decided[key] = bar; p = dict(p, decision_id=f"{key}|{bar}", strategy_version=designs.VERSION.get(p["design"]), signal_bar_end=bar)
        if p["design"] == "OXF1" and p.get("cross_utc") and stopped.get(p["ins"], "") > p["cross_utc"]:
            continue    # OXF1: stopped out in this regime -> wait for the next crossover
        cs = data[p["ins"]]; a = atr(cs)
        if not a: continue
        seen_ins.add(p["ins"])
        e = dict(p, stop_dist=float(p.get("stop_atr") or c["atr_mult"]) * a, atr=a, sigma_ann=RK.sigma_ann(cs), floor_sp=median([x["sp"] for x in cs[-260:]]), close=cs[-1]["c"],
                 expires_utc=(now_utc() + dt.timedelta(hours=20)).isoformat(), **behaviour_refs(p, cs, a))
        if p["design"] == "OXF1": e["not_before_utc"] = next_london(c["trend_entry_london"]).isoformat()   # F: enter ~08:05 London
        e["queued_by"] = QUEUE_RULES
        queue.append(e)
    q2 = []; per = {}; nb = {}
    cur = open_trades() if live else tfo
    cur = [t for t in cur if t["instrument"] not in exiting]   # GPT r8 F3: a trade with a deferred exit frees its slot/caps for its reversal
    for t in cur:
        if is_tfo(t): k = designs.design_of(t); per[k] = per.get(k, 0) + 1; nb[bucket(k)] = nb.get(bucket(k), 0) + 1
    rows = open_risk_rows(cur, snap["nav"], c)
    for e in queue:   # 13 Oct 2026: slots, total and per-ccy caps per bucket (book 8 / 4% / 1.5%; OXF1 4 / 2% / 1.5%)
        k = e["design"]; bk = bucket(k)
        if per.get(k, 0) >= designs.MAXSLOTS.get(k, 4) or nb.get(bk, 0) >= bucket_limits(k, c)[2]: continue
        rf = entry_risk_frac(e, c); why = caps_check(rows, e["ins"], e["dir"], rf, c, k)
        if why: print("queue skip", e["ins"], k, why); log(dict(event="queue_cap_skip", instrument=e["ins"], design=k, reason=why, live=live)); continue
        per[k] = per.get(k, 0) + 1; nb[bk] = nb.get(bk, 0) + 1; q2.append(e); rows.append(dict(ins=e["ins"], dir=e["dir"], risk_frac=rf, design=k))
    queue = q2
    combined_ccy_check(rows, c, "daily queue (open + queued)", live)
    shadow_gpt_caps(cur, queue, data, snap["nav"], c, last_close, live)     # D: GPT caps in SHADOW (never blocks)
    shadow_scores(data, last_close, live)                                    # G: TSMOM+carry score in SHADOW
    if live: daily_logs(snap, last_close)                                    # H + A + I logs
    try:   # 13 Oct 2026 (GPT review): a same-bar retry must not drop entries the first run queued (LPB now skips re-deciding them)
        old = json.load(open(os.path.join(ST, "pending.json" if live else "pending_dry.json"))).get("queue", [])
        have = {e["ins"] for e in queue}
        for e in old:
            if (e.get("ins") not in have and e.get("signal_bar_end") == last_close            # SAME signal bar only (GPT review r2 F1)
                    and e.get("queued_by") == QUEUE_RULES                                       # never carry items decided by older rules
                    and dt.datetime.fromisoformat(e["expires_utc"]) > now_utc()): queue.append(e); have.add(e["ins"])
    except Exception: pass
    _write_json("pending.json" if live else "pending_dry.json", dict(created_utc=now_utc().isoformat(), last_close=last_close, queue=queue))   # audit #8: atomic
    if live:
        lpb.update(decided); _write_json("last_processed_bar.json", lpb)       # Part C1: decisions are fixed once queued (ids are per decision)
    print(json.dumps(dict(last_close=last_close, plan=plan, queue=queue), indent=1, default=str))
    run_queue(live, snap)
    if live:
        if mgmt_fail:
            log(dict(event="management_incomplete", last_close=last_close, failed=mgmt_fail, action="last_daily NOT advanced; next daily run retries"))
        else:
            _write_json("last_daily.json", dict(last_close=last_close, at=now_utc().isoformat()))   # audit #8: atomic, after run_queue
def run_queue(live, snap=None):
    pf = os.path.join(ST, "pending.json" if live else "pending_dry.json")
    if not os.path.exists(pf): return
    try: q = json.load(open(pf))
    except Exception as ex:
        log(dict(event="state_read_error", file=os.path.basename(pf), error=repr(ex)[:200])); return
    keep = []
    if snap is None: snap, _ = snapshot()
    blocked = os.path.exists(os.path.join(ST, "KILL")) or halted(snap)
    bo = reopen_blackout(minutes=cfg()["reopen_blackout_min"])   # 5 Oct 2026: no new entries within 60 min of the weekly FX reopen
    for e in q.get("queue", []):
        if dt.datetime.fromisoformat(e["expires_utc"]) < now_utc(): log(dict(event="entry_expired", instrument=e["ins"], design=e["design"])); continue
        if blocked: log(dict(event="entry_blocked", instrument=e["ins"], reason="KILL file, sleeve halt or unverified P&L")); keep.append(e); continue
        sb = e.get("signal_bar_end"); fw = bar_freshness(sb) if sb else None
        if fw:   # 13 Oct 2026 (GPT review r2 F1): a queued decision whose signal bar is no longer current is dropped, never submitted
            log(dict(event="entry_stale_bar_drop", instrument=e["ins"], design=e["design"], signal_bar_end=sb, reason=fw, live=live)); continue
        nb = e.get("not_before_utc")
        if nb and dt.datetime.fromisoformat(nb) > now_utc(): keep.append(e); continue   # OXF1 waits for ~08:05 London
        if bo: keep.append(e); print(e["ins"], bo); log(dict(event="entry_deferred", instrument=e["ins"], design=e["design"], reason=bo, live=live)); continue
        res = place_entry(e, live); print(e["ins"], res)
        if res.startswith("defer"): keep.append(e); log(dict(event="entry_deferred", instrument=e["ins"], design=e["design"], reason=res, live=live))
    q["queue"] = keep
    if live: _write_json(os.path.basename(pf), q)   # audit #8: atomic
def check(live=True):
    c = cfg(); live = live and c.get("live", True)
    _rf = reconcile_stop_fills()
    if _rf is None and live: RG.record_pass(ST, False, "stop_fills reconcile failed", log)
    snap, tr = snapshot()
    for t in tr:
        if is_tfo(t) and not t.get("stopLossOrder") and not t.get("trailingStopLossOrder"):
            d = 1 if float(t["currentUnits"]) > 0 else -1; a = None
            try: a = atr(candles(t["instrument"], 120))
            except Exception as ex: log(dict(event="data_error", instrument=t["instrument"], error=str(ex)[:200], context="stop restore"))
            if a: ns, src = float(t["price"]) - d * c["atr_mult"] * a, "entry -/+ atr_mult x ATR20"
            else:                                       # 13 Oct 2026 audit #7: no ATR -> the last broker-confirmed ledger stop
                ns = (ledger_stops().get(str(t["id"])) or {}).get("stop"); src = "ledger stop (ATR unavailable)"
            if ns is None:
                log(dict(event="stop_restore_failed", trade_id=t["id"], instrument=t["instrument"], reason="no ATR and no ledger stop", live=live)); continue
            ns = float(ns)
            if stop_through_market(t["instrument"], d, ns):
                close_trade(t, "missing broker stop and restore level %s already through market (%s)" % (ns, src), live)
            else:
                set_stop(t, ns, "missing broker stop restored (%s)" % src, live)
    _rs = reconcile_stops(tr, live)        # 5 Oct 2026: broker vs ledger stops every check
    if live and _rf is not None: RG.record_pass(ST, _rs is not None, None if _rs is not None else "stops reconcile failed", log)
    try: run_exits(open_trades(), c, live)   # 13 Oct 2026 (audit #3): deferred signal exits
    except Exception as ex: log(dict(event="exit_error", error=repr(ex)[:200]))
    run_queue(live, snap)
    snap, _ = snapshot()
    if live: log_nav(snap)            # 12 Oct 2026: one NAV row per UTC day (no-op if already written)
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "snapshot"
    fh = lock()
    try:
        if mode == "snapshot": print(json.dumps(snapshot()[0], indent=1)[:3000])
        elif mode == "daily": daily(True)
        elif mode == "dry": daily(False)
        elif mode == "check": check(True)
        if mode in ("daily", "dry", "check"):   # 16 Oct 2026 (Brett 21:35-21:37): AGGRO sleeve runs in the same jobs, same lock; its errors never stop this sleeve
            try:
                import oanda_aggro; oanda_aggro.run(mode)
            except Exception as ex: log(dict(event="aggro_hook_error", error=repr(ex)[:300]))
        else: print(__doc__)
    except Exception as ex:
        log(dict(event="error", mode=mode, error=str(ex)[:800]))
        if mode in ("daily", "check"):   # 25 Oct 2026: a run that dies (e.g. DNS) before a clean reconcile is a failed pass
            if not RG.RECORDED: RG.record_pass(ST, False, "run error: " + str(ex)[:200], log)
        raise
