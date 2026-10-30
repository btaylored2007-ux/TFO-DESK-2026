#!/usr/bin/env python3
"""TFO Head Trader crypto clips on Kraken LIVE tape (Brett 2026-09-25: 24/7, any liquid coin). PAPER ONLY.
  tfo_crypto.py mark                      re-mark book equity from Kraken (health; never tickets)
  tfo_crypto.py rails [--execute|--dry-run]  -5% stop / +2% half, then trail 3xATR after +2% half, BE floor, 30d max hold (variant C); sells at bid
      2026-10-02: NEW clips tagged exit_variant=C3 (2xATR14 entry stop, +15% half, same trail/BE, 40d max hold; 1% firm risk to the stop;
      V0 shadow in crypto_exit_ab.py; SHARK_EXIT_RULE.md). Untagged clips (SOL, HBAR) keep the rail above. CRYPTO_EXIT_VARIANT=None reverts.
  tfo_crypto.py buy PAIR USD --theme T [--label beta|edge] [--why ..] [--stop-pct F] [--slip-pct F] [--presized] [--execute]   buys at ask, all gates
      2026-10-04 21:30 AEDT (TFO Risk audit, Head Trader adopted; risk_caps.py): clip = min(USD, $5k / (actual stop + slippage));
      if ATR14% > stop, clip x stop/ATR (skip below $5k min). Actual stop = min(rail stop (C3 2xATR or -5%), --stop-pct).
      --presized: caller already applied the ATR rule (overnight2k), so only the idempotent planned-loss cap is re-checked.
  tfo_crypto.py scan [N]                  rank liquid Kraken USD pairs by gate pass (research only)
  tfo_crypto.py sell PAIR --rule stale_flat [--why ..] [--execute]   HT discretionary FLAT of a stale clip at bid (2026-09-29)
Book: reports/tfo_ht_crypto.json (registered in book_coverage). Funding/proceeds via paired transfer with powder."""
import argparse, datetime as dt, json, math, os, sys, time, urllib.parse, urllib.request, uuid
BASE = os.path.dirname(os.path.abspath(__file__)); R = os.path.join(BASE, "reports"); sys.path.insert(0, BASE)
try:  # 2026-10-27 WITA Brett: ISP hijacks port-53 DNS for Kraken -> scoped DoH for Kraken public hosts only (kill: state/entry_engine/KRAKEN_DOH_OFF)
    import kraken_dns; kraken_dns.install()
except Exception as _e:  # pragma: no cover
    print("kraken_dns unavailable: %r" % _e, file=sys.stderr)
import desk_lock
import desk_costs  # 2026-09-25: shared retail cost model; Kraken taker fee per fill (PAPER)
import exit_check_evidence as ece  # custodian 2026-09-25: native exit-coverage evidence, decision logic unchanged
import tfo_trail_rail as trl  # V1 variant C exit rail (2026-09-25)
import quote_check  # 2026-09-29 HT: bid/ask consistency gate
import tfo_heat  # 2026-09-26 HT decision: heat at live stops (floored residuals 1% gap buffer)
import risk_caps  # 2026-10-04 21:30 AEDT TFO Risk audit (Head Trader adopted): planned-loss cap per name + high-ATR size-down
from tfo_ticket import load, save, LOG, FACTS, GCL, SYD, UTC
CRYPTO_EXIT_VARIANT = "C3"   # 2026-10-02 (Brett: "your call, maximise desk profits"): NEW clips use exit variant C3 (SHARK_EXIT_RULE.md); None reverts new clips to V0
CRYPTO_EXIT_VARIANT_START = "2026-10-02"
C3_MAX_RISK_PCT = 0.01       # SHARK_SIZING_RULE: risk 1% of firm equity per A+ clip to its stop (C3 stop = 2xATR at entry)
KEY = "tfo_ht_crypto"; STACK_CAP = 1000000.0; CLIP_CAP = 300000.0; HEAT_CAP_PCT = tfo_heat.HEAT_CAP_PCT
# 2026-10-02 Brett (PAPER): crypto stack cap $500k -> $1,000,000 (entry-cost basis); = desk_caps.CRYPTO_STACK_CAP_USD. Clip cap, C3, heat unchanged. Revert: 500000.0
UA = {"User-Agent": "TFO-HT/1.0"}
NOT_TRADES = {"USDT/USD","USDC/USD","DAI/USD","PYUSD/USD","USDG/USD","RLUSD/USD","EUR/USD","GBP/USD","AUD/USD","CAD/USD","CHF/USD","JPY/USD","USDS/USD","FDUSD/USD","TUSD/USD","EURC/USD","USDE/USD","USD1/USD"}

# 2026-10-17 (Brett via KDESK owner; TON entry paid ~$403 taker): entry-engine crypto clips use POST-ONLY maker limits for the entry
# and the planned +half exit; stops / trail / max-hold exits stay taker (stop-market semantics). PAPER: a resting order fills only
# when a later public Kraken trade prints THROUGH its limit (strictly below a buy / above a sell), at the limit, maker fee.
# No taker fallback: an unfilled entry expires and its powder is returned. RULE_LOG 2026-10-17.
MAKER_THEMES = {"entry_engine_crypto"}
# 2026-10-17 (Brett, weekly cut review): entry-engine clips exit +2% half / flat by +5% (replaces the C3 +15% half) with a
# 1.5% minimum stop (rail stop, sizing stop and heat). Revert: EE_THEMES = set().
EE_THEMES = {"entry_engine_crypto"}
# 2026-10-30 15:4x WITA ARCHIVE LOCK: Risk archived this book (reports/book_archives/tfo_ht_crypto.json, verified_zero_balance_archive_v1).
# Any new open (taker buy, post-only rest, resting fill) writes a capital_transfer / position, voids the archive fingerprint,
# re-registers the book and halts firm entries. So ALL opens are refused here; exits/rails/stops/half-bank are untouched.
# Route option (not wired): crypto risk can be taken in gap_close_levered_book (GCL) itself or another live book. Revert: False.
OPENS_DISABLED = True
OPENS_DISABLED_REASON = "book archived by Risk 2026-10-30; tfo_ht_crypto read-only for opens"
OPENS_BLOCK_CODE = "tfo_ht_crypto_archived_no_opens"

def opens_blocked(where, pair=None):
    """Single choke point for every new clip. Returns the block code (and prints it) or None. Never writes."""
    if not OPENS_DISABLED: return None
    print("BLOCKED %s %s: %s (%s)" % (where, pair or "", OPENS_BLOCK_CODE, OPENS_DISABLED_REASON)); print("LOG none")
    return OPENS_BLOCK_CODE

def exit_profile(p):
    return trl.EE if (p or {}).get("theme") in EE_THEMES else None

def planned_exit(dec):
    """Planned (post-only maker) exits: any HALF, and the entry-engine +5% FLAT. STOP / trail / BE / max-hold stay taker stops."""
    return dec.get("act") == "HALF" or (dec.get("act") == "FLAT" and dec.get("rule") == "FLAT_5PCT")

def rail_stop_dist(p):
    """Stop distance the rail actually uses for clip p (C3 2xATR, else 5%; entry-engine clips floored at 1.5%)."""
    sd = float(p.get("c3_stop_dist") or 0.05) if p.get("exit_variant") == trl.C3 else 0.05
    return trl.ee_stop_dist(sd) if exit_profile(p) else sd
MAKER_ENTRY_TTL_S = 900; MAKER_EXIT_TTL_S = 600

def trades_since(pair, since_epoch):
    """Public Kraken prints after since_epoch -> [(px, vol, t)] oldest first."""
    tr = get("Trades", pair=kpair(pair), since=str(int(since_epoch * 1e9)))
    rows = [v for k, v in tr.items() if k != "last"]
    return sorted([(float(r[0]), float(r[1]), float(r[2])) for r in (rows[0] if rows else []) if float(r[2]) > since_epoch], key=lambda x: x[2])

def _epoch(iso): return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()

def get(path, **params):
    url = "https://api.kraken.com/0/public/%s?%s" % (path, urllib.parse.urlencode(params))
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=10) as r:
        d = json.loads(r.read().decode())
    if d.get("error"): raise RuntimeError("%s %s" % (path, d["error"]))
    return d["result"]

def kpair(pair): return pair.replace("/", "")

def quote(pair):
    t = list(get("Ticker", pair=kpair(pair)).values())[0]
    tr = get("Trades", pair=kpair(pair), count=1); rows = [v for k, v in tr.items() if k != "last"][0]
    last_epoch = max(float(r[2]) for r in rows) if rows else 0
    bid, ask, last = float(t["b"][0]), float(t["a"][0]), float(t["c"][0])
    vwap24, vol24, opn = float(t["p"][1]), float(t["v"][1]), float(t["o"])
    age = time.time() - last_epoch
    q = {"pair": pair, "bid": bid, "ask": ask, "last": last, "mid": (bid + ask) / 2, "open_utc_day": opn,
         "vol24": vol24, "dollar_vol24": vol24 * vwap24, "trade_age_sec": round(age, 1),
         "spread_bp": (ask - bid) / ((ask + bid) / 2) * 1e4 if bid > 0 and ask > bid else None,
         "utc": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "Kraken public Ticker+Trades"}
    q["tape"] = "LIVE" if age < 60 else "STALE"
    ok = all(math.isfinite(x) and x > 0 for x in (bid, ask, last))
    if not ok: q["tape"] = "BAD"
    return q

# HT 2026-10-10 10:21 stale-tape fix (BUY gate only; rails/exits still need tape == "LIVE" < 60s).
# A coin passing the $1M day-volume floor may enter on a last trade <= 3 min old, or, if the last trade is older (<= 15 min,
# never more), on a fresh, uncrossed book with spread <= 20bp fetched in this same quote. Anything older: refuse.
ENTRY_DVOL_MIN = 1e6; ENTRY_TRADE_MAX_SEC = 180; ENTRY_BOOK_FALLBACK_MAX_SEC = 900; ENTRY_BOOK_MAX_SPREAD_BP = 20.0
def entry_tape(q, quote_age_sec=0.0, book_fresh_sec=15.0):
    """-> (ok, mode, px_ref, why). mode LIVE|RECENT|BOOK|STALE|BAD; px_ref = price to test the trigger with (last, or mid in BOOK mode)."""
    if q.get("tape") == "BAD": return False, "BAD", None, "bad quote"
    age = q.get("trade_age_sec"); last = q.get("last"); bid = q.get("bid"); ask = q.get("ask")
    if q.get("tape") == "LIVE": return True, "LIVE", last, "last trade %ss" % age
    if age is None: return False, "STALE", None, "no trade age"
    if (q.get("dollar_vol24") or 0) < ENTRY_DVOL_MIN: return False, "STALE", None, "last trade %.0fs, day $vol < $1M: needs a print < 60s" % age
    if age <= ENTRY_TRADE_MAX_SEC: return True, "RECENT", last, "last trade %.0fs (<= 3 min, day $vol >= $1M)" % age
    sp = q.get("spread_bp")
    if age > ENTRY_BOOK_FALLBACK_MAX_SEC: return False, "STALE", None, "last trade %.0fs > 15 min: truly old" % age
    if quote_age_sec is None or quote_age_sec > book_fresh_sec: return False, "STALE", None, "book not fresh (%ss)" % quote_age_sec
    if not (bid and ask and ask > bid) or sp is None or sp > ENTRY_BOOK_MAX_SPREAD_BP:
        return False, "STALE", None, "last trade %.0fs and book not tight (spread %s bp)" % (age, sp if sp is None else round(sp, 1))
    return True, "BOOK", (bid + ask) / 2, "last trade %.0fs; fresh book mid, spread %.1fbp <= 20bp" % (age, sp)

def avg10_vol(pair):
    rows = list(get("OHLC", pair=kpair(pair), interval=1440).values())[0]
    rows = [r for r in rows if isinstance(r, list)][-11:-1]
    return sum(float(r[6]) for r in rows) / len(rows) if rows else 0

def new_book():
    return {"book": KEY, "label": "HT Crypto Clips (Kraken LIVE)", "mode": "PAPER", "paper_only": True, "label_alpha_beta": "per clip",
            "retail_executable": True, "seed_usd": 0.0, "start": 0.0, "cash": 0.0, "equity": 0.0, "peak": 0.0,
            "holdings": {}, "entry_px": {}, "mark_px_usd": {}, "positions": {}, "closed": [], "head_trader_tickets": [],
            "capital_events": [], "created_utc": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "rails": "stop -5% orig, half +2% orig, then trail 3xATR after +2% half, BE floor, 30d max hold; sells at bid, buys at ask"}

def book():
    p = os.path.join(R, KEY + ".json")
    if not os.path.exists(p):
        if OPENS_DISABLED: return new_book()   # 2026-10-30 archive lock: in-memory only, never recreate the archived ledger on disk
        save(KEY, new_book())
    return load(KEY)

def restrike(b, marks):
    pos = 0.0
    for pair, p in b["positions"].items():
        px = marks.get(pair) or b["mark_px_usd"].get(pair) or p["entry_px"]
        b["mark_px_usd"][pair] = px; b["holdings"][pair] = p["qty"]; b["entry_px"][pair] = p["entry_px"]
        pos += p["qty"] * px
    for pair in list(b["holdings"]):
        if pair not in b["positions"]: b["holdings"].pop(pair, None); b["mark_px_usd"].pop(pair, None); b["entry_px"].pop(pair, None)
    b["equity"] = round(b["cash"] + pos, 2); b["peak"] = max(float(b.get("peak") or 0), b["equity"])
    b["updated"] = dt.datetime.now(SYD).isoformat()

def xfer(b, g, usd, to_powder, utc, tag, note):
    usd = round(usd, 2)
    ev = {"type": "capital_transfer", "utc": utc, "from": KEY if to_powder else GCL, "to": GCL if to_powder else KEY, "tag": tag, "note": note, "paper_only": True}
    s = 1 if to_powder else -1
    before = float(g["cash"]); g["cash"] = round(before + s * usd, 2)
    g.setdefault("capital_events", []).append({**ev, "usd": s * usd, "cash_before": before, "cash_after": g["cash"]})
    b["capital_events"].append({**ev, "usd": -s * usd})
    b["cash"] = round(b["cash"] - s * usd, 2); b["seed_usd"] = round(b["seed_usd"] - s * usd, 2); b["start"] = b["seed_usd"]
    g["seed_usd"] = round(float(g["seed_usd"]) + s * usd, 2); g["start"] = round(float(g["start"]) + s * usd, 2)
    g["equity"] = g["cash"]; g["updated"] = utc

def log(act, pair, qty, px, principal, proceeds, pnl, fact, tid, fees=0.0):
    syd = dt.datetime.now(SYD).strftime("%Y-%m-%d %H:%M")
    line = "LOG %s %s %s %.8g %.8g LIVE %s %.2f %.2f %.2f %.2f" % (syd, act, pair, qty, px, KEY, principal, proceeds, pnl, fees)
    open(LOG, "a").write(line + "\n"); open(FACTS, "a").write(json.dumps({"syd": syd, "fact": fact, "ticket": tid, "fees_usd": round(fees, 2)}) + "\n")
    return line

def cmd_mark():
    with desk_lock.ledger_lock(KEY):
        b = book(); marks = {}
        for pair in b["positions"]:
            try:
                q = quote(pair)
                if q["tape"] != "BAD": marks[pair] = q["mid"]
            except Exception as e: print("mark fail", pair, e)
        restrike(b, marks); save(KEY, b)
    print(json.dumps({"equity": b["equity"], "cash": b["cash"], "n": len(b["positions"]), "marks": marks}))

def rail_detail(p, px, now=None, atr_fetch=None):
    """V1 variant C (tfo_trail_rail): -5% orig stop / +2% half; residual trails max(3%, 3xATR14) off its HWM, BE floor,
    30 calendar-day max hold; no +5% cap. Returns (decision, updated trail state; persisted only by --execute)."""
    now = now or dt.datetime.now(UTC)
    st = dict(p.get("trail") or {}); banked = bool(p.get("rebase") or st.get("half_banked"))
    if banked and atr_fetch: trl.refresh_atr(st, atr_fetch, now.astimezone(SYD).date().isoformat())
    var = trl.C3 if p.get("exit_variant") == trl.C3 else None  # 2026-10-02: C3-tagged new clips; untagged = unchanged V0
    prof = exit_profile(p)                                     # 2026-10-17: entry-engine clips -> +2% half / +5% flat / stop >= 1.5%
    dec = trl.decide(p["orig_entry"], px, "long", banked, st.get("hwm_usd"), st.get("atr14_pct"), trl.held(p.get("entry_utc"), now, True), crypto=True,
                     variant=var, stop_dist=p.get("c3_stop_dist"), profile=prof)
    if banked:
        if not st.get("hwm_usd"): st["seeded"] = "current_px_at_switch"
        st.update({"half_banked": True, "hwm_usd": dec["wm"], "trail_pct": round(dec["trail_pct"], 6), "level_usd": round(dec["level"], 10),
                   "rule": trl.rule_text(var, prof), "updated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
    return dec, st

def rail(p, px):
    dec, _ = rail_detail(p, px)
    return dec["act"], dec["r0"]

HB = os.path.join(R, "ht_crypto_rails_heartbeat.json")  # audit 2026-09-26: liveness only (hunts check age, no kickstart)
_HB_DEFAULT = HB

def rails_heartbeat(execute, n_pos, qfails, statuses, n_tickets):
    """Atomic liveness file for launchd com.tfo.ht-crypto-rails (60s). Never affects rails decisions; failures are swallowed."""
    # no ledger-shaped keys (cash/positions/holdings/equity/nav...) so book_coverage never mistakes it for an unclaimed ledger
    try:
        now = dt.datetime.now(UTC)
        hb = {"at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "at_sydney": now.astimezone(SYD).strftime("%Y-%m-%d %H:%M:%S %Z"), "pid": os.getpid(),
              "runner": "tfo_crypto.py rails" + (" --execute" if execute else ""), "interval_sec": 60, "stale_after_sec": 180,
              "n_open_clips": n_pos, "quote_fails": qfails, "statuses": statuses, "tickets": n_tickets, "ok": not qfails, "paper_only": True}
        fd, tmp = __import__("tempfile").mkstemp(dir=R, prefix=".ht_crypto_rails_heartbeat", suffix=".tmp")
        with os.fdopen(fd, "w") as f: json.dump(hb, f, indent=1)
        # follow R when only R is redirected (tests), so a temp-desk run never touches the live heartbeat
        os.replace(tmp, HB if HB != _HB_DEFAULT else os.path.join(R, os.path.basename(HB)))
    except Exception as e:
        print("heartbeat write failed:", e)

def _wd_idle(execute):  # 2026-10-30 rail watchdog: idle/archived rails still report a cycle (no false quiet-runner)
    if execute and os.path.abspath(R) == os.path.abspath(os.path.join(BASE, "reports")):
        try:
            import rail_watchdog; rail_watchdog.update("crypto", [])
        except Exception as e: print("RAIL-WATCHDOG unavailable: %s" % e)

def cmd_rails(execute, dry=False):
    lines = []; qfails = []; statuses = {}; wd_rows = []; ab_rows = []
    if OPENS_DISABLED and not os.path.exists(os.path.join(R, KEY + ".json")):   # 2026-10-30: never recreate an archived ledger
        print("NO LEDGER %s: %s; rails idle (nothing to exit)" % (KEY, OPENS_BLOCK_CODE)); rails_heartbeat(execute, 0, [], {}, 0); _wd_idle(execute); print("LOG none"); return
    with desk_lock.ledger_lock(KEY), desk_lock.ledger_lock("tfo_gcl_powder"):
        b = book(); g = load(GCL); marks = {}; now = dt.datetime.now(UTC); utc = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        if OPENS_DISABLED and not b.get("positions") and not b.get("resting_entries"):   # 2026-10-30 dashfix: archived + flat -> heartbeat only, never rewrite the archived ledger
            print("ARCHIVED %s flat: rails idle, ledger not rewritten" % KEY); rails_heartbeat(execute, 0, [], {}, 0); _wd_idle(execute); print("LOG none"); return
        ece.start(b, "tfo_crypto.rails" + (" --execute" if execute else ""))
        resting_entries(b, g, now, execute, lines)
        for pair, p in list(b["positions"].items()):
            try: q = quote(pair)
            except Exception as e: print("QUOTE FAIL", pair, e); qfails.append(pair); wd_rows.append({"sym": pair, "book": KEY, "status": "NO_QUOTE", "why": "quote fail: %s" % str(e)[:120]}); continue
            if q["tape"] == "BAD": print("BAD QUOTE", pair); qfails.append(pair); wd_rows.append({"sym": pair, "book": KEY, "status": "NO_QUOTE", "why": "bad quote"}); continue
            marks[pair] = q["mid"]; px = q["bid"]
            ab_rows.append({"pair": pair, "px": px, "tape": q["tape"], "last_mark": b["mark_px_usd"].get(pair)})
            if px > 2.5 * (b["mark_px_usd"].get(pair) or px) or px < (b["mark_px_usd"].get(pair) or px) / 2.5: print("SANITY", pair); wd_rows.append({"sym": pair, "book": KEY, "status": "NO_QUOTE", "why": "price sanity (>2.5x last mark)"}); continue
            dec, st = rail_detail(p, px, now, (lambda pr=pair: trl.kraken_atr_pct(get, kpair(pr))))
            act, r0 = dec["act"], dec["r0"]
            liq = "taker"; re_ = p.get("resting_exit")
            if re_:                                                    # 2026-10-17 post-only planned exit resting at its limit
                try: prs = trades_since(pair, _epoch(re_["placed_utc"]))
                except Exception: prs = None
                hit = prs is not None and any(t <= _epoch(re_["expires_utc"]) and x > re_["limit_px"] for x, v, t in prs)
                stopish = act == "STOP" or (act == "FLAT" and not planned_exit(dec))   # 2026-10-17: FLAT_5PCT is a planned exit, not a stop
                if hit and not stopish:
                    ract = re_.get("act") or "HALF"
                    act, px, liq = ract, re_["limit_px"], "maker"; dec = dict(dec, act=ract, rule=re_.get("rule") or dec["rule"])
                elif stopish or (prs is not None and now.timestamp() > _epoch(re_["expires_utc"])):
                    print("RESTEXIT %-10s cancelled (%s)" % (pair, "expired" if not stopish else act))
                    if execute: p.pop("resting_exit", None)
                    if not stopish:                                    # 2026-10-17: an expired planned exit never falls through to a taker sale;
                        statuses[pair] = "RESTEXIT_EXPIRED"            # it re-posts post-only on the next rails run if it still applies
                        if execute and st and q["tape"] == "LIVE": p["trail"] = st
                        continue
                else:
                    print("RESTEXIT %-10s open limit %.8g" % (pair, re_["limit_px"])); statuses[pair] = "RESTING_EXIT"; continue
            elif planned_exit(dec) and p.get("maker_exits") and q["tape"] == "LIVE":
                print("RESTEXIT %-10s post-only %s sell @ %.8g (ask)" % (pair, act, q["ask"])); statuses[pair] = "RESTING_EXIT"
                if execute: p["resting_exit"] = {"placed_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "expires_utc": (now + dt.timedelta(seconds=MAKER_EXIT_TTL_S)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                 "limit_px": q["ask"], "rule": dec["rule"], "act": act, "post_only": True}
                continue
            if execute and st and q["tape"] == "LIVE": p["trail"] = st  # HWM only moves on LIVE, sane bids
            lt = p.get("last_ticket_utc")
            recent = lt and (now - dt.datetime.fromisoformat(lt.replace("Z", "+00:00"))).total_seconds() < 300
            if act and not planned_exit(dec): recent = False   # audit H12 (Brett 27 Oct 2026 19:35 WITA): protective stops (STOP/trail/BE/max-hold) skip the 5-min cooldown
            status = "HOLD" if not act else ("BLOCKED stale tape (last trade %.0fs)" % q["trade_age_sec"] if q["tape"] != "LIVE" else ("BLOCKED <5min" if recent else "READY"))
            if liq == "maker":                                         # the resting exit already filled on real prints: book it
                status = "READY"
                if execute: p.pop("resting_exit", None)
            print("%-8s %-10s %-5s %6.2f%% bid %.8g" % (status.split()[0], pair, act or "-", 100 * r0, px)); statuses[pair] = status
            wd_rows.append({"sym": pair, "book": KEY, "rail": act, "exit_rule": dec["rule"], "rail_level_usd": dec["level"], "px_usd": px, "ret_orig_pct": round(100 * r0, 2),
                            "status": status.split()[0], "why": status if status.startswith("BLOCKED") else None, "session": "regular", "executed": bool(execute and status == "READY")})
            if st: print("TRAIL    %-10s hwm %.8g level %.8g (%s) trail %.2f%% atr %s seeded %s" % (pair, st["hwm_usd"], st["level_usd"], dec["rule"] or "hold", 100 * st["trail_pct"], st.get("atr14_pct"), st.get("seeded")))
            ev = {"quote": {"session": "regular", "quote_utc": q["utc"], "bid": px, "tape": q["tape"], "trade_age_sec": q["trade_age_sec"]}, "ret_orig_pct": round(100 * r0, 4), "rail": act, "exit_rule": dec["rule"], "rail_level_usd": dec["level"]}
            if status == "HOLD" and exit_profile(p): ece.record(b, pair, p, "HOLD", "Inside rails: -%.2f%% stop / +2%% half / +5%% flat / " % (100 * rail_stop_dist(p)) + trl.EE_RULE_TEXT, **ev)
            elif status == "HOLD": ece.record(b, pair, p, "HOLD", ("Inside rails: -%.2f%% (2xATR) stop / +15%% half / " % (100 * float(p.get("c3_stop_dist") or 0.05)) + trl.C3_RULE_TEXT) if p.get("exit_variant") == trl.C3 else ("Inside rails: -5% stop / +2% half / " + trl.RULE_TEXT), **ev)
            elif status.startswith("BLOCKED"): ece.record(b, pair, p, "WAIT_MARKET", status, **ev)
            elif not execute: ece.record(b, pair, p, "SIGNAL_NOT_APPLIED", "Rail READY but runner not in --execute mode", **ev)
            if not (execute and status == "READY"): continue
            tid = "HT-" + uuid.uuid4().hex[:10]; qty0 = p["qty"]; sold = qty0 / 2 if act == "HALF" else qty0
            principal = sold * p["entry_px"]; proceeds = sold * px; pnl = proceeds - principal
            # desk_costs 2026-09-25: Kraken taker fee on the sale; cash and realised are net of it; net proceeds to powder
            fee = desk_costs.fill_fees(pair, "sell", sold, px, when=now, liquidity=liq); fees = round(fee["fees_usd"], 2)
            p["qty"] = qty0 - sold; b["cash"] = round(b["cash"] + proceeds - fees, 2); b["realised_usd"] = round(float(b.get("realised_usd") or 0) + pnl - fees, 2)
            b["fees_paid_usd"] = round(float(b.get("fees_paid_usd") or 0) + fees, 2)
            if act == "HALF": p["rebase"] = px; p["trail"] = {"half_banked": True, "hwm_usd": px, "seeded": "half_fill", "half_utc": utc, "rule": trl.rule_text(p.get("exit_variant"), exit_profile(p))}  # variant C (C3: +15% half)
            p["last_ticket_utc"] = utc
            tk = {"ticket_id": tid, "utc": utc, "action": act, "pair": pair, "qty": sold, "px_usd": px, "tape": "LIVE", "principal_usd": round(principal, 2),
                  "proceeds_usd": round(proceeds, 2), "pnl_usd": round(pnl, 2), "ret_orig_pct": round(100 * r0, 2), "quote": q, "paper_only": True,
                  "fees_usd": fees, "fee_detail": fee["breakdown"], "fee_schedule": fee["schedule"], "net_proceeds_usd": round(proceeds - fees, 2), "liquidity": liq}
            if dec["rule"]: tk["exit_rule"] = dec["rule"]; tk["rail_level_usd"] = dec["level"]  # variant C audit
            if p.get("exit_variant"): tk["exit_variant"] = p.get("exit_variant")  # 2026-10-02 crypto exit test
            p.setdefault("exit_fills", []).append(tk); b["head_trader_tickets"].append(tk)
            if p["qty"] > 1e-12: ece.record(b, pair, p, "EXIT", "Head Trader %s applied" % act, applied=True, ticket_ref=tid, **ev)
            if p["qty"] <= 1e-12:
                b["closed"].append({"pair": pair, "closed_utc": utc, "reason": "Head Trader " + act, "position": p}); del b["positions"][pair]
            xfer(b, g, proceeds - fees, True, utc, "HT_%s_%s" % (act, pair), "Head Trader %s %s; proceeds to powder; firm moves by P&L only. PAPER" % (act, pair))
            lines.append(log(act, pair, sold, px, principal, proceeds, pnl, "%s %s %.2f%% from orig fill; pnl %.2f; fees %.2f" % (act, pair, 100 * r0, pnl, fees), tid, fees))
        if dry: print("DRY RUN: nothing saved, no tickets"); return
        ece.finish(b); restrike(b, marks); save(KEY, b); save(GCL, g)
    rails_heartbeat(execute, len(b["positions"]), qfails, statuses, len(lines))
    if execute and os.path.abspath(R) == os.path.abspath(os.path.join(BASE, "reports")):  # live desk only; 2026-09-28 blocked-rail watchdog (report-only)
        try:
            import rail_watchdog; rail_watchdog.update("crypto", wd_rows)
        except Exception as e: print("RAIL-WATCHDOG unavailable: %s" % e)
        try:  # 2026-10-02 crypto exit A/B: V0 shadow for C3-tagged clips (own files only; never raises)
            import crypto_exit_ab; crypto_exit_ab.update(ab_rows, b, reports=R)
        except Exception as e: print("CRYPTO-EXIT-AB unavailable: %s" % e)
    print("\n".join(lines) if lines else "LOG none")

STALE_MIN_DAYS = 3.0; STALE_MAX_RET_PCT = 1.0

def stale_check(p, px, now):
    """-> None if the clip qualifies for rule stale_flat, else the KEEP reason. Not half-banked, held >= 3 calendar days,
    return from original fill <= +1% (flat or in a loss). 2026-09-29 13:03 AEST funder follow-up; Head Trader rule."""
    if p.get("rebase") or (p.get("trail") or {}).get("half_banked"): return "half-banked residual on trail (excluded)"
    et = p.get("entry_utc")
    if not et: return "no entry time"
    age = (now - dt.datetime.fromisoformat(et.replace("Z", "+00:00"))).total_seconds() / 86400.0
    if age < STALE_MIN_DAYS: return "held %.2f d < %.0f d" % (age, STALE_MIN_DAYS)
    r0 = px / p["orig_entry"] - 1
    if 100 * r0 > STALE_MAX_RET_PCT: return "ret %+.2f%% > +%.0f%% (making progress)" % (100 * r0, STALE_MAX_RET_PCT)
    return None

def cmd_sell(pair, rule, why, execute):
    """Discretionary FLAT of one clip at the live Kraken bid: same fee model, cash/realised, closed record, powder transfer,
    LOG/fact line as a rails FLAT. Refused on a non-LIVE/BAD/insane tape, inside the 5-min double guard, or if the clip
    does not meet the rule."""
    if rule not in ("stale_flat", "ht_flat"): raise SystemExit("unknown rule %r" % rule)
    with desk_lock.ledger_lock(KEY), desk_lock.ledger_lock("tfo_gcl_powder"):
        b = book(); g = load(GCL); now = dt.datetime.now(UTC); utc = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        p = b["positions"].get(pair)
        if not p: print("SKIP %s: not held in %s" % (pair, KEY)); return
        q = quote(pair)
        if q["tape"] != "LIVE": print("SKIP %s: tape %s (last trade %.0fs); not a live print" % (pair, q["tape"], q["trade_age_sec"])); return
        px = q["bid"]; last = b["mark_px_usd"].get(pair) or px
        if px > 2.5 * last or px < last / 2.5: print("SKIP %s: price sanity (>2.5x last mark)" % pair); return
        lt = p.get("last_ticket_utc")
        if lt and (now - dt.datetime.fromisoformat(lt.replace("Z", "+00:00"))).total_seconds() < 300: print("SKIP %s: double guard (<5 min)" % pair); return
        keep = stale_check(p, px, now) if rule == "stale_flat" else None  # ht_flat: HT discretionary (any clip incl. residual), all other guards kept
        if keep: print("SKIP %s: %s" % (pair, keep)); return
        r0 = px / p["orig_entry"] - 1; qty0 = p["qty"]
        principal = qty0 * p["entry_px"]; proceeds = qty0 * px; pnl = proceeds - principal
        fee = desk_costs.fill_fees(pair, "sell", qty0, px, when=now); fees = round(fee["fees_usd"], 2)
        reason = ("stale_flat: held >= %.0f d, ret %+.2f%% from fill, not half-banked%s" % (STALE_MIN_DAYS, 100 * r0, ("; " + why) if why else "")) if rule == "stale_flat" else ("ht_flat: Head Trader discretionary FLAT, ret %+.2f%% from fill%s" % (100 * r0, ("; " + why) if why else ""))
        if not execute:
            print("PREVIEW SELL %s %s qty %.8g @ bid %.8g (spread %.2fbp) pnl %.2f fees %.2f net %.2f | %s" % (KEY, pair, qty0, px, q["spread_bp"] or 0, pnl, fees, pnl - fees, reason)); return
        ece.start(b, "tfo_crypto.sell --execute")
        tid = "HT-" + uuid.uuid4().hex[:10]
        b["cash"] = round(b["cash"] + proceeds - fees, 2); b["realised_usd"] = round(float(b.get("realised_usd") or 0) + pnl - fees, 2)
        b["fees_paid_usd"] = round(float(b.get("fees_paid_usd") or 0) + fees, 2)
        p["qty"] = 0.0; p["last_ticket_utc"] = utc
        tk = {"ticket_id": tid, "utc": utc, "action": "FLAT", "pair": pair, "qty": qty0, "px_usd": px, "tape": "LIVE", "principal_usd": round(principal, 2),
              "proceeds_usd": round(proceeds, 2), "pnl_usd": round(pnl, 2), "ret_orig_pct": round(100 * r0, 2), "quote": q, "paper_only": True,
              "fees_usd": fees, "fee_detail": fee["breakdown"], "fee_schedule": fee["schedule"], "net_proceeds_usd": round(proceeds - fees, 2),
              "exit_rule": rule, "rotation_reasons": [reason], "rotation_by": ("TFO Head Trader decision 2026-09-29 13:03 AEST (funder: liquidate stale positions)" if rule == "stale_flat" else "TFO Head Trader decision (ht_flat)")}
        p.setdefault("exit_fills", []).append(tk); b["head_trader_tickets"].append(tk)
        b["closed"].append({"pair": pair, "closed_utc": utc, "reason": "Head Trader FLAT (%s)" % rule, "position": p}); del b["positions"][pair]
        xfer(b, g, proceeds - fees, True, utc, "HT_FLAT_%s" % pair, "Head Trader FLAT %s (%s); proceeds to powder; firm moves by P&L only. PAPER" % (pair, rule))
        line = log("FLAT", pair, qty0, px, principal, proceeds, pnl, "FLAT %s %.2f%% from orig fill; pnl %.2f; fees %.2f; %s" % (pair, 100 * r0, pnl, fees, reason), tid, fees)
        marks = {pair: q["mid"]}
        ece.finish(b); restrike(b, marks); save(KEY, b); save(GCL, g)
    print(line)

def clip_heat():
    """(heat at live stops per tfo_heat, crypto stack at entry). Stack cap basis unchanged (qty x entry)."""
    fbk = load("fundamental_book"); b = book()
    cr = sum(p["qty"] * p["entry_px"] for p in b["positions"].values()) + sum(float(r.get("usd") or 0) for r in (b.get("resting_entries") or {}).values())   # gate F3: resting = committed
    return tfo_heat.clip_heat(fbk["positions"], b)[0], cr

def gates(pair, usd, theme):
    from tfo_buy import firm_state, sizing_eq
    fails = []; b = book()
    if pair in NOT_TRADES: fails.append("stablecoin/FX is cash, not a clip")
    from tfo_ticket import CLOSED_NAMES as _CN  # 2026-09-30: crypto gate now honours Brett closed-book list
    if str(pair).upper().split("/")[0] in _CN: fails.append("closed name")
    rest = b.get("resting_entries") or {}   # gate F3 17 Oct: resting post-only entries count as held everywhere
    if pair in b["positions"] or pair in rest: fails.append("already held")
    if sum(1 for p in list(b["positions"].values()) + list(rest.values()) if p.get("theme") == theme) >= 2: fails.append("theme already has two clips")
    if usd > CLIP_CAP: fails.append("crypto clip over $300k")
    heat, cr = clip_heat()
    if cr + usd > STACK_CAP: fails.append("crypto stack cap $%.0fk" % (STACK_CAP / 1e3))
    eq, dd, opn = firm_state()
    try: seq = sizing_eq()   # 2026-10-29: C3 1% clip risk sizes off firm equity ex veteran_desk; heat/freezes stay firm-wide
    except Exception as _e: seq = 0.0; fails.append("sizing equity unavailable (%s)" % str(_e)[:80])
    c3 = {}
    if CRYPTO_EXIT_VARIANT == trl.C3:  # 2026-10-02: C3 stop = 2 x Kraken daily ATR14% at entry; heat and 1% risk use that stop
        try: a = trl.kraken_atr_pct(get, kpair(pair))
        except Exception as e: a = None; c3["c3_atr_error"] = str(e)[:120]
        sd = trl.c3_stop_dist(a)
        if sd is None: fails.append("no ATR14 for the C3 stop (fail closed)")
        else:
            if theme in EE_THEMES: sd = trl.ee_stop_dist(sd)    # 2026-10-17: entry-engine stop never tighter than 1.5%
            c3 = {"c3_atr14_entry": round(a, 6), "c3_stop_dist": round(sd, 6), "c3_max_clip_usd": round(C3_MAX_RISK_PCT * seq / sd, 2)}
            if usd * sd > C3_MAX_RISK_PCT * seq + 1e-6: fails.append("C3 risk at the 2xATR stop %.0f > 1%% sizing equity (max clip %.0f)" % (usd * sd, C3_MAX_RISK_PCT * seq / sd))
    new_pct = c3.get("c3_stop_dist") or 0.05
    if heat + new_pct * usd > HEAT_CAP_PCT * eq: fails.append("combined clip heat at stops > %.0f%% firm" % (100 * HEAT_CAP_PCT))
    if dd <= -10: fails.append("firm DD freeze")
    if opn and (eq - opn) / opn <= -0.03: fails.append("Sydney-day -3% freeze")
    for p in b["positions"].values():
        px = b["mark_px_usd"].get(next(k for k, v in b["positions"].items() if v is p)) or p["orig_entry"]
        _sd = rail_stop_dist(p)
        if px / p["orig_entry"] - 1 <= -(_sd - 0.005): fails.append("a crypto clip is within 50bp of its stop")
    for t in b["head_trader_tickets"]:
        if t.get("pair") == pair and (dt.datetime.now(UTC) - dt.datetime.fromisoformat(t["utc"].replace("Z", "+00:00"))).total_seconds() < 300: fails.append("round-trip inside 5 min")
    g = load(GCL)
    q = quote(pair); a10 = avg10_vol(pair)
    fee_est = desk_costs.fill_fees(pair, "buy", usd / q["ask"], q["ask"])["fees_usd"] if q["ask"] > 0 else 0.0
    if usd + fee_est > float(g["cash"]) - float(g.get("reserved_usd") or 0): fails.append("exceeds unreserved powder (clip + fees)")
    chg = q["last"] / q["open_utc_day"] - 1 if q["open_utc_day"] else 0
    info = {"fees_est_usd": round(fee_est, 2), "ask": q["ask"], "spread_bp": q["spread_bp"] and round(q["spread_bp"], 1), "dollar_vol24": round(q["dollar_vol24"]), "chg_vs_utc_open_pct": round(100 * chg, 2),
            "vol24_vs_10d": round(q["vol24"] / a10, 2) if a10 else None, "trade_age_sec": q["trade_age_sec"], "tape": q["tape"], **c3}
    _tok, _tmode, _, _twhy = entry_tape(q); info["entry_tape"] = _tmode
    if not _tok: fails.append("tape not live enough for entry: " + _twhy)
    _qok, _qr, _ = quote_check.check(q["bid"], q["ask"], q["last"])   # 2026-09-29 HT: same consistency rule as tfo_buy
    if not _qok: fails.append(_qr)
    elif q["spread_bp"] is None or q["spread_bp"] >= 20: fails.append("spread >= 20bp")
    if usd > 0.05 * q["dollar_vol24"]: fails.append("size > 5% of 24h dollar volume")
    if chg < -0.03: fails.append("down >3% on the day")
    if chg > 0.05 and not (a10 and q["vol24"] >= 2 * a10): fails.append("up >5% without 2x 10-day volume")
    return fails, q, info

def _cap_log_path():
    """Live desk -> state/risk_caps/cap_log.jsonl; a redirected R (tests) logs inside R so tests never touch the live log."""
    return risk_caps.LOG_PATH if os.path.abspath(R) == os.path.abspath(os.path.join(BASE, "reports")) else os.path.join(R, "risk_cap_log.jsonl")

def risk_size(pair, usd, info, stop_pct=None, slip_pct=None, presized=False, theme=None):
    """2026-10-04 21:30 AEDT TFO Risk audit (Head Trader adopted). Rule 1: clip <= $5k / (actual stop + slippage).
    Rule 2: ATR14% > stop -> clip x stop/ATR, skip below the $5k desk minimum. Actual stop = the tighter of the rail stop
    (C3: 2xATR14 at entry; V0: -5%) and a caller stop (--stop-pct, e.g. overnight2k -5% night stop). Never sizes up.
    Brett 2026-10-09 21:10 AEDT: theme entry_engine_crypto (auto_fire) -> min($150k, $7,500 / actual stop) (risk_caps.theme_params),
    so a wide 2xATR stop still sizes down; ATR rule and $5k minimum unchanged. Other themes keep the $5k cap."""
    c3 = CRYPTO_EXIT_VARIANT == trl.C3
    rail_sd = info.get("c3_stop_dist") if c3 else 0.05
    atr = info.get("c3_atr14_entry")
    if atr is None and not presized:
        try: atr = trl.kraken_atr_pct(get, kpair(pair))
        except Exception: atr = None
    sds = [float(x) for x in (rail_sd, stop_pct) if x]
    if theme in EE_THEMES and sds: sds = [max(min(sds), trl.EE_MIN_STOP)]   # 2026-10-17: sizing stop never tighter than 1.5%
    return risk_caps.size_entry(usd, min(sds) if sds else None, slip_pct=slip_pct, atr_pct=atr, apply_atr_rule=not presized,
                                min_clip=risk_caps.CRYPTO_MIN_CLIP_USD, **risk_caps.theme_params(theme))

def _post_entry(pair, usd, theme, label, why, q, info):
    """Post-only buy resting at the best bid (never crosses). Powder for clip + maker fee is reserved now, returned on expiry."""
    if opens_blocked("tfo_crypto.post_entry", pair): return OPENS_BLOCK_CODE   # 2026-10-30 archive lock
    with desk_lock.ledger_lock(KEY), desk_lock.ledger_lock("tfo_gcl_powder"):
        b = book(); g = load(GCL); now = dt.datetime.now(UTC); utc = now.strftime("%Y-%m-%dT%H:%M:%SZ"); tid = "HT-" + uuid.uuid4().hex[:10]
        rest = b.setdefault("resting_entries", {})
        if pair in rest or pair in b["positions"]: print("SKIP %s: already resting/held" % pair); print("LOG none"); return
        px = q["bid"]; qty = usd / px
        fee = desk_costs.fill_fees(pair, "buy", qty, px, liquidity="maker"); fees = round(fee["fees_usd"], 2)
        xfer(b, g, usd + fees, False, utc, "HT_POSTBUY_%s" % pair, "Head Trader post-only BUY %s resting; powder reserved (returned if unfilled). PAPER" % pair)
        rest[pair] = {"ticket_id": tid, "placed_utc": utc, "expires_utc": (now + dt.timedelta(seconds=MAKER_ENTRY_TTL_S)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "limit_px": px, "qty": qty, "usd": usd, "reserved_usd": round(usd + fees, 2), "theme": theme, "label": label, "why": why,
                      "quote": q, "c3_stop_dist": info.get("c3_stop_dist"), "c3_atr14_entry": info.get("c3_atr14_entry"),
                      "risk_cap": info.get("risk_cap"), "post_only": True, "paper_only": True}
        restrike(b, {})   # gate r4#1: book equity includes the reservation now, so firm equity never dips by it
        save(KEY, b); save(GCL, g)
    print("RESTING %s %s post-only BUY qty %.8g @ %.8g (bid) usd %.0f ttl %ds ticket %s" % (KEY, pair, qty, px, usd, MAKER_ENTRY_TTL_S, tid))

def _fill_entry(b, pair, r, utc):
    """Book a filled post-only entry exactly like cmd_buy, at the limit with the maker fee (reservation already in book cash)."""
    px, qty, usd = r["limit_px"], r["qty"], r["usd"]
    fee = desk_costs.fill_fees(pair, "buy", qty, px, liquidity="maker"); fees = round(fee["fees_usd"], 2)
    b["cash"] = round(b["cash"] - usd - fees, 2)
    b["realised_usd"] = round(float(b.get("realised_usd") or 0) - fees, 2); b["fees_paid_usd"] = round(float(b.get("fees_paid_usd") or 0) + fees, 2)
    tk = {"ticket_id": r["ticket_id"], "utc": utc, "action": "BUY", "pair": pair, "qty": qty, "px_usd": px, "tape": "LIVE", "principal_usd": usd, "theme": r["theme"],
          "label": r["label"], "why": r["why"], "quote": r["quote"], "paper_only": True, "fees_usd": fees, "fee_detail": fee["breakdown"], "fee_schedule": fee["schedule"],
          "liquidity": "maker", "post_only": True, "placed_utc": r["placed_utc"]}
    if r.get("risk_cap"): tk["risk_cap"] = r["risk_cap"]
    b["positions"][pair] = {"qty": qty, "entry_px": px, "orig_entry": px, "entry_utc": utc, "theme": r["theme"], "label": r["label"], "why": r["why"],
                            "last_ticket_utc": utc, "entry_ticket": tk, "entry_fees_usd": fees,
                            "maker_exits": r["theme"] in MAKER_THEMES}   # gate F2 17 Oct: --post-only governs the ENTRY only; exits stay as before
    if CRYPTO_EXIT_VARIANT == trl.C3 and r.get("c3_stop_dist"):
        b["positions"][pair].update({"exit_variant": trl.C3, "exit_variant_start": CRYPTO_EXIT_VARIANT_START, "c3_stop_dist": r["c3_stop_dist"], "c3_atr14_entry": r.get("c3_atr14_entry")})
        tk["exit_variant"] = trl.C3; tk["c3_stop_dist"] = r["c3_stop_dist"]
    b["head_trader_tickets"].append(tk)
    return log("BUY", pair, qty, px, usd, 0, 0, "BUY %s $%.0f post-only maker fill (%s) theme %s: %s; fees %.2f" % (pair, usd, r["label"], r["theme"], r["why"], fees), r["ticket_id"], fees)

def cmd_cancel_resting(pair, why, execute):
    """17 Oct 2026 (gate N1): caller cancels its own resting post-only entry (e.g. overnight2k night stop / flat-by). Returns the
    powder reservation exactly like a TTL expiry. Never touches positions, stops or exits."""
    with desk_lock.ledger_lock(KEY), desk_lock.ledger_lock("tfo_gcl_powder"):
        b = book(); g = load(GCL); r = (b.get("resting_entries") or {}).get(pair)
        if not r: print("NO RESTING %s" % pair); return False
        print("CANCEL RESTING %s %s limit %.8g usd %.0f (%s)%s" % (pair, r["ticket_id"], r["limit_px"], r["usd"], why, "" if execute else " [preview]"))
        if not execute: return False
        utc = dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        xfer(b, g, r["reserved_usd"], True, utc, "HT_POSTBUY_CANCEL_%s" % pair, "Head Trader post-only BUY %s caller_cancel (%s); reservation returned to powder. PAPER" % (pair, why[:120]))
        b.setdefault("resting_log", []).append({**{k: r[k] for k in ("ticket_id", "placed_utc", "limit_px", "qty", "usd")}, "pair": pair, "outcome": "caller_cancel", "why": why[:200], "utc": utc})
        del b["resting_entries"][pair]
        restrike(b, {})   # gate M2: re-strike book equity after the cash leaves, so firm equity never sees it twice
        save(KEY, b); save(GCL, g)
    return True

def resting_entries(b, g, now, execute, lines):
    """Fill / expire resting post-only entries. A buy fills when a later print trades strictly BELOW its limit; if a print at or
    below the entry's own stop comes first, the order is cancelled instead (never buy into a broken setup)."""
    utc = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    for pair, r in list((b.get("resting_entries") or {}).items()):
        try: prs = trades_since(pair, _epoch(r["placed_utc"]))
        except Exception as e: print("RESTING %s: trades fetch failed %s" % (pair, str(e)[:80])); prs = None
        exp = _epoch(r["expires_utc"]); stop = r["limit_px"] * (1 - float(r.get("c3_stop_dist") or 0.05))
        outcome = None
        for px, vol, t in (prs or []):
            if t > exp: break
            if px <= stop: outcome = "stop_cancel"; break
            if px < r["limit_px"]: outcome = "fill"; break
        if outcome is None and now.timestamp() > exp and prs is not None: outcome = "expired"
        if outcome is None and now.timestamp() > exp + MAKER_ENTRY_TTL_S: outcome = "expired"   # gate F7: feed down past 2x TTL -> expire on the clock
        if outcome == "fill" and OPENS_DISABLED: outcome = "archived_cancel"   # 2026-10-30 archive lock: never fill into a position; reservation returned
        print("RESTING %-10s %s limit %.8g -> %s" % (pair, r["ticket_id"], r["limit_px"], outcome or "open"))
        if not execute or outcome is None: continue
        if outcome == "fill": lines.append(_fill_entry(b, pair, r, utc))
        else: xfer(b, g, r["reserved_usd"], True, utc, "HT_POSTBUY_CANCEL_%s" % pair, "Head Trader post-only BUY %s %s; reservation returned to powder. PAPER" % (pair, outcome))
        b.setdefault("resting_log", []).append({**{k: r[k] for k in ("ticket_id", "placed_utc", "limit_px", "qty", "usd")}, "pair": pair, "outcome": outcome, "utc": utc})
        del b["resting_entries"][pair]

def cmd_buy(pair, usd, theme, label, why, execute, stop_pct=None, slip_pct=None, presized=False, post_only=False):
    if opens_blocked("tfo_crypto.buy", pair): return OPENS_BLOCK_CODE   # 2026-10-30 archive lock (before gates, preview or execute)
    fails, q, info = gates(pair, usd, theme)
    rk = risk_size(pair, usd, info, stop_pct, slip_pct, presized, theme=theme)
    if rk["bound"] and not rk["skip"]:
        usd = rk["usd"]; fails, q, info = gates(pair, usd, theme)   # re-gate at the risk-sized clip (smaller is never worse)
    if rk["skip"]: fails = list(fails) + [rk["skip_reason"]]
    info = {**info, "usd_requested": rk["usd_requested"], "risk_cap": rk}
    print(json.dumps({"pair": pair, "usd": usd, "theme": theme, **info, "gate_fails": fails}))
    print(risk_caps.describe(rk))
    if execute: risk_caps.log_bind(rk, "tfo_crypto.buy", pair, executed=not fails, path=_cap_log_path(), extra={"theme": theme, "why": (why or "")[:200]})
    if fails: print("LOG none"); return
    if not execute: print("PREVIEW PASS (not executed)"); return
    # 17 Oct 2026 (Brett 14:27 item 1): callers (watchlist, overnight2k) may ask for a post-only entry per order with --post-only
    if theme in MAKER_THEMES or post_only: return _post_entry(pair, usd, theme, label, why, q, info)
    with desk_lock.ledger_lock(KEY), desk_lock.ledger_lock("tfo_gcl_powder"):
        b = book(); g = load(GCL); utc = dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"); tid = "HT-" + uuid.uuid4().hex[:10]
        px = q["ask"]; qty = usd / px
        # desk_costs 2026-09-25: taker fee funded from powder on top of the clip; cash/realised net of it
        fee = desk_costs.fill_fees(pair, "buy", qty, px); fees = round(fee["fees_usd"], 2)
        xfer(b, g, usd + fees, False, utc, "HT_BUY_%s" % pair, "Head Trader BUY %s crypto clip funded from powder; paired; firm unchanged. PAPER" % pair)
        b["cash"] = round(b["cash"] - usd - fees, 2)
        b["realised_usd"] = round(float(b.get("realised_usd") or 0) - fees, 2); b["fees_paid_usd"] = round(float(b.get("fees_paid_usd") or 0) + fees, 2)
        tk = {"ticket_id": tid, "utc": utc, "action": "BUY", "pair": pair, "qty": qty, "px_usd": px, "tape": "LIVE", "principal_usd": usd, "theme": theme, "label": label, "why": why, "quote": q, "paper_only": True,
              "fees_usd": fees, "fee_detail": fee["breakdown"], "fee_schedule": fee["schedule"]}
        _rk = info.get("risk_cap") or {}
        if _rk: tk["risk_cap"] = {**{k: _rk.get(k) for k in ("rule", "usd_requested", "usd", "bound", "binds", "stop_dist", "slip_allowance", "atr14_pct", "atr_scale", "planned_loss_usd", "planned_loss_cap_usd")}, **{k: _rk[k] for k in ("planned_loss_at_stop_usd", "target_usd", "loss_basis") if k in _rk}}
        b["positions"][pair] = {"qty": qty, "entry_px": px, "orig_entry": px, "entry_utc": utc, "theme": theme, "label": label, "why": why, "last_ticket_utc": utc, "entry_ticket": tk, "entry_fees_usd": fees}
        if CRYPTO_EXIT_VARIANT == trl.C3 and info.get("c3_stop_dist"):  # 2026-10-02 crypto exit test (gates guarantee the stop exists)
            b["positions"][pair].update({"exit_variant": trl.C3, "exit_variant_start": CRYPTO_EXIT_VARIANT_START, "c3_stop_dist": info["c3_stop_dist"], "c3_atr14_entry": info["c3_atr14_entry"]})
            tk["exit_variant"] = trl.C3; tk["c3_stop_dist"] = info["c3_stop_dist"]
        b["head_trader_tickets"].append(tk); restrike(b, {pair: q["mid"]}); save(KEY, b); save(GCL, g)
    print(log("BUY", pair, qty, px, usd, 0, 0, "BUY %s $%.0f LIVE (%s) theme %s: %s; fees %.2f" % (pair, usd, label, theme, why, fees), tid, fees))

def cmd_scan(n):
    pairs = get("AssetPairs"); usd = [v["wsname"] for k, v in pairs.items() if v.get("quote") in ("ZUSD", "USD") and v.get("status", "online") == "online" and ".d" not in k]
    tick = get("Ticker"); out = []
    alt = {v["wsname"]: k for k, v in pairs.items()}
    for ws in usd:
        t = tick.get(alt.get(ws));
        if not t: continue
        bid, ask, last, opn = float(t["b"][0]), float(t["a"][0]), float(t["c"][0]), float(t["o"])
        if bid <= 0 or ask <= 0 or opn <= 0: continue
        dv = float(t["v"][1]) * float(t["p"][1]); sp = (ask - bid) / ((ask + bid) / 2) * 1e4
        out.append({"pair": ws.replace("XBT", "BTC") if False else ws, "dollar_vol24": round(dv), "spread_bp": round(sp, 1), "chg_pct": round(100 * (last / opn - 1), 2), "max_clip_5pct": round(0.05 * dv)})
    out = [o for o in out if o["pair"] not in NOT_TRADES and o["spread_bp"] < 20 and o["dollar_vol24"] > 2e6]
    out.sort(key=lambda o: -o["dollar_vol24"])
    for o in out[:n]: print(json.dumps(o))

def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="c", required=True)
    sp.add_parser("mark"); r = sp.add_parser("rails"); r.add_argument("--execute", action="store_true"); r.add_argument("--dry-run", action="store_true")
    bu = sp.add_parser("buy"); bu.add_argument("pair"); bu.add_argument("usd", type=float); bu.add_argument("--theme", required=True)
    bu.add_argument("--label", default="beta", choices=["beta", "edge"]); bu.add_argument("--why", default=""); bu.add_argument("--execute", action="store_true")
    bu.add_argument("--stop-pct", type=float, default=None, help="caller's actual stop as a FRACTION (0.05 = -5%%) if tighter than the rail stop")
    bu.add_argument("--slip-pct", type=float, default=None, help="measured book slippage as a FRACTION (allowance = max(0.5%%, this))")
    bu.add_argument("--post-only", dest="post_only", action="store_true", help="rest a post-only maker BUY at the bid (same machinery as MAKER_THEMES)")
    bu.add_argument("--presized", action="store_true", help="caller already applied the high-ATR size-down (planned-loss cap still re-checked)")
    cr_ = sp.add_parser("cancel-resting"); cr_.add_argument("pair"); cr_.add_argument("--why", default=""); cr_.add_argument("--execute", action="store_true")
    se = sp.add_parser("sell"); se.add_argument("pair"); se.add_argument("--rule", required=True, choices=["stale_flat", "ht_flat"]); se.add_argument("--why", default=""); se.add_argument("--execute", action="store_true")
    sc = sp.add_parser("scan"); sc.add_argument("n", nargs="?", type=int, default=40)
    a = ap.parse_args()
    if a.c == "mark": cmd_mark()
    elif a.c == "rails": cmd_rails(a.execute and not a.dry_run, a.dry_run)
    elif a.c == "cancel-resting": cmd_cancel_resting(a.pair, a.why, a.execute)
    elif a.c == "buy": cmd_buy(a.pair, a.usd, a.theme, a.label, a.why, a.execute, stop_pct=a.stop_pct, slip_pct=a.slip_pct, presized=a.presized, post_only=a.post_only)
    elif a.c == "sell": cmd_sell(a.pair, a.rule, a.why, a.execute)
    else: cmd_scan(a.n)

if __name__ == "__main__":
    main()
