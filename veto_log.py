#!/usr/bin/env python3
"""veto_log.py -- crypto veto ledger (TFO Risk audit 4 Oct 2026: crypto_vetoes.py CONDITIONAL PASS; Head Trader adopted
4 Oct 2026 21:30 AEDT). PAPER ONLY; never trades, never touches a book.

Condition from TFO Risk: log every vetoed would-be entry with its hypothetical result so the cost of the veto can be
measured, and review the veto after 30 vetoes.

  state/vetoes/vetoed_entries.jsonl   one line per vetoed entry (symbol, timestamp, veto reason, ref price, context),
                                      outcome {24h, 72h} filled later from live Kraken hourly bars (Coinbase fallback)
  state/vetoes/veto_counter.json      counted vetoes vs REVIEW_AFTER (30), review_due flag, average hypothetical returns

Counting: a veto counts toward the review once per (symbol, UTC day) -- the veto reads daily bars, so an hourly
re-scan of the same coin on the same day is logged (repeat_of) but not counted again. Fail-closed rows (veto data
unavailable / veto not run) are logged but never counted: they are not evidence about the veto rule.

CLI:  python3 veto_log.py fill      fill due 24h/72h outcomes (launchd com.tfo.veto-outcomes, hourly)
      python3 veto_log.py status    print the counter
"""
try:  # 2026-10-27 WITA Brett: ISP hijacks port-53 DNS for Kraken -> scoped DoH for Kraken public hosts only (kill: state/entry_engine/KRAKEN_DOH_OFF)
    import os as _kd_os, sys as _kd_sys
    _kd_sys.path.append(_kd_os.path.normpath(_kd_os.path.join(_kd_os.path.dirname(_kd_os.path.abspath(__file__)), '.')))
    import kraken_dns as _kraken_dns; _kraken_dns.install()
except Exception as _kd_e:  # pragma: no cover
    print("kraken_dns unavailable: %r" % _kd_e, file=__import__("sys").stderr)
import argparse, datetime as dt, fcntl, json, os, sys, time, urllib.request, uuid
# TFO DoH hook 2026-10-28 (Brett item B4: desk_doh process-local DoH for IBKR + Binance/Coinbase/OKX/Alpaca/Polymarket; no system DNS; kill switch TFO_DESK_DOH=0)
try:
    import desk_doh as _doh; _doh.install(extra=True)
except Exception as _e:
    print("desk_doh install skipped:", _e, file=__import__("sys").stderr)
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__))
VDIR = os.path.join(BASE, "state", "vetoes")
LEDGER = os.path.join(VDIR, "vetoed_entries.jsonl")
COUNTER = os.path.join(VDIR, "veto_counter.json")
LOCK = os.path.join(VDIR, ".veto_log.lock")
REVIEW_AFTER = 30
HORIZONS_H = (24, 72)
ROUND_TRIP_COST = 0.0080          # 0.40% Kraken taker each side (desk_costs schedule, Brett real fees 13 Oct 2026; was 0.26%), for the net hypothetical
HYPO_STOP = 0.05                  # the -5% clip stop used to report a stop-adjusted hypothetical
RULE_ADOPTED = "2026-10-04 21:30 AEDT, TFO Risk audit, Head Trader adopted"
SYD = ZoneInfo("Australia/Sydney"); UTC = dt.timezone.utc
UA = {"User-Agent": "TFO-veto-log/1.0 (paper)"}


def _paths(dry):
    if dry:
        return LEDGER.replace(".jsonl", "_dryrun.jsonl"), COUNTER.replace(".json", "_dryrun.json")
    return LEDGER, COUNTER


def _iso(t): return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
def _pts(s): return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


class _Lock:
    def __enter__(self):
        os.makedirs(VDIR, exist_ok=True)
        self.f = open(LOCK, "w"); fcntl.flock(self.f, fcntl.LOCK_EX); return self
    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN); self.f.close()


def read_all(path=None):
    p = path or LEDGER
    if not os.path.exists(p): return []
    out = []
    for ln in open(p):
        ln = ln.strip()
        if ln:
            try: out.append(json.loads(ln))
            except ValueError: pass
    return out


def _write_all(rows, path):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        for r in rows: f.write(json.dumps(r, default=str) + "\n")
    os.replace(tmp, path)


def classify(veto, reason):
    r = str(reason or "")
    if veto is True and not r.startswith("veto data unavailable"): return "veto"
    if veto is None or r == "not run": return "not_run"
    return "fail_closed"


def log_veto(symbol, reason, source, veto=True, ref_px=None, ref_px_source=None, kraken_pair=None, kraken_key=None,
             coinbase_product=None, context=None, ts=None, dry=False):
    """Append one vetoed would-be entry. Returns the record. Never raises (a logging failure must not block a pass)."""
    try:
        ledger, counter = _paths(dry)
        ts = ts or dt.datetime.now(UTC)
        kind = classify(veto, reason)
        with _Lock():
            rows = read_all(ledger)
            day = ts.astimezone(UTC).date().isoformat()
            prior = next((r for r in rows if r.get("counts_toward_review") and r.get("symbol") == symbol and r.get("utc", "")[:10] == day), None)
            rec = {"id": "VETO-" + uuid.uuid4().hex[:10], "utc": _iso(ts), "syd": ts.astimezone(SYD).strftime("%Y-%m-%d %H:%M:%S %Z"),
                   "source": source, "symbol": symbol, "veto_reason": reason, "veto_kind": kind,
                   "counts_toward_review": kind == "veto" and prior is None, "repeat_of": prior["id"] if prior else None,
                   "ref_px": ref_px, "ref_px_source": ref_px_source, "kraken_pair": kraken_pair, "kraken_key": kraken_key,
                   "coinbase_product": coinbase_product, "context": context or {}, "paper_only": True, "rule_adopted": RULE_ADOPTED,
                   "outcome": {"%dh" % h: None for h in HORIZONS_H}, "outcome_status": "pending" if kind == "veto" else "not_applicable"}
            with open(ledger, "a") as f: f.write(json.dumps(rec, default=str) + "\n")
            rows.append(rec)
            c = _write_counter(rows, counter)
        if c.get("review_due") and rec["counts_toward_review"]:
            print("VETO REVIEW DUE: %d counted vetoes >= %d (state/vetoes/veto_counter.json)" % (c["counted_vetoes"], REVIEW_AFTER))
        return rec
    except Exception as e:
        print("VETO LOG failed: %s" % e)
        return None


def _write_counter(rows, path):
    counted = [r for r in rows if r.get("counts_toward_review")]
    c = {"rule": "crypto_vetoes.py CONDITIONAL PASS (TFO Risk 4 Oct 2026): log every veto, review after %d" % REVIEW_AFTER,
         "rule_adopted": RULE_ADOPTED, "review_after": REVIEW_AFTER, "counted_vetoes": len(counted), "total_logged": len(rows),
         "repeats_not_counted": sum(1 for r in rows if r.get("repeat_of")),
         "fail_closed_not_counted": sum(1 for r in rows if r.get("veto_kind") in ("fail_closed", "not_run")),
         "review_due": len(counted) >= REVIEW_AFTER, "remaining_to_review": max(0, REVIEW_AFTER - len(counted)),
         "first_utc": counted[0]["utc"] if counted else None, "last_utc": counted[-1]["utc"] if counted else None,
         "by_source": {}, "updated_utc": _iso(dt.datetime.now(UTC))}
    for r in counted: c["by_source"][r.get("source")] = c["by_source"].get(r.get("source"), 0) + 1
    for h in HORIZONS_H:
        k = "%dh" % h
        filled = [r["outcome"][k] for r in counted if (r.get("outcome") or {}).get(k) and r["outcome"][k].get("ret") is not None]
        c["outcomes_%s" % k] = {"n": len(filled),
                                 "avg_ret": round(sum(x["ret"] for x in filled) / len(filled), 6) if filled else None,
                                 "avg_ret_stop_adj_net": round(sum(x["ret_stop_adj_net"] for x in filled) / len(filled), 6) if filled else None,
                                 "winners": sum(1 for x in filled if x["ret_stop_adj_net"] > 0),
                                 "note": "positive avg = the veto cost money (blocked entries would have made money); negative = the veto saved money"}
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(c, f, indent=1)
    os.replace(tmp, path)
    return c


# ----------------------------------------------------------------------------- hypothetical outcomes
def _get(url, retries=3):
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r: return json.loads(r.read())
        except Exception as e:
            last = e; time.sleep(1 + i)
    raise last


def kraken_hourly(key, since_ts):
    d = _get("https://api.kraken.com/0/public/OHLC?pair=%s&interval=60&since=%d" % (key, int(since_ts)))
    if d.get("error"): raise RuntimeError(str(d["error"]))
    rows = [v for k, v in d["result"].items() if k != "last"][0]
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows]


def coinbase_hourly(product, since_ts, end_ts):
    rows = _get("https://api.exchange.coinbase.com/products/%s/candles?granularity=3600&start=%d&end=%d" % (product, int(since_ts), int(end_ts)))
    return sorted((int(r[0]), float(r[3]), float(r[2]), float(r[1]), float(r[4])) for r in rows)


def outcome_from_bars(bars, t0, ref_px, hours, stop=HYPO_STOP, cost=ROUND_TRIP_COST):
    """bars: [(open_ts, o, h, l, c)] hourly. Hypothetical long from ref_px at t0, held `hours` (bars completed in (t0, t0+H]).
    -> dict or None if the window is not fully covered yet."""
    end = t0 + hours * 3600
    win = [b for b in bars if b[0] + 3600 > t0 and b[0] + 3600 <= end]
    if not win or win[-1][0] + 3600 < end - 3600: return None   # need a bar closing within the last hour of the window
    ref = ref_px
    if not ref:
        first = [b for b in bars if b[0] <= t0 < b[0] + 3600]
        ref = first[0][4] if first else win[0][1]
    px = win[-1][4]; hi = max(b[2] for b in win); lo = min(b[3] for b in win)
    stop_px = ref * (1 - stop); stopped = None
    for b in win:
        if b[3] <= stop_px: stopped = b[0]; break
    ret = px / ref - 1
    adj = -stop if stopped is not None else ret
    return {"ret": round(ret, 6), "px": px, "ref_px": ref, "max_up": round(hi / ref - 1, 6), "max_down": round(lo / ref - 1, 6),
            "stop_hit": stopped is not None, "stop_hit_utc": _iso(dt.datetime.fromtimestamp(stopped, UTC)) if stopped is not None else None,
            "ret_stop_adj_net": round(adj - cost, 6), "hypo_pnl_per_10k_usd": round(10000 * (adj - cost), 2), "bars": len(win),
            "window_end_utc": _iso(dt.datetime.fromtimestamp(end, UTC))}


def fill_outcomes(now=None, fetch_kraken=kraken_hourly, fetch_coinbase=coinbase_hourly, dry=False):
    """Fill due 24h/72h outcomes in place (atomic rewrite under the lock). Returns number of horizons filled."""
    ledger, counter = _paths(dry)
    now = now or dt.datetime.now(UTC); n = 0
    with _Lock():
        rows = read_all(ledger)
        for r in rows:
            if r.get("outcome_status") != "pending": continue
            t0 = _pts(r["utc"]).timestamp(); due = [h for h in HORIZONS_H if r["outcome"].get("%dh" % h) is None and now.timestamp() >= t0 + h * 3600]
            if not due: continue
            try:
                if r.get("kraken_key"): bars, src = fetch_kraken(r["kraken_key"], t0 - 3600), "kraken"
                elif r.get("coinbase_product"): bars, src = fetch_coinbase(r["coinbase_product"], t0 - 3600, t0 + max(HORIZONS_H) * 3600 + 3600), "coinbase"
                else: r["outcome_error"] = "no Kraken or Coinbase market recorded"; r["outcome_status"] = "unavailable"; continue
            except Exception as e:
                r["outcome_error"] = str(e)[:160]; continue
            for h in due:
                o = outcome_from_bars(bars, t0, r.get("ref_px"), h)
                if o: o["source"] = src; o["filled_utc"] = _iso(now); r["outcome"]["%dh" % h] = o; n += 1
            if all(r["outcome"].get("%dh" % h) for h in HORIZONS_H): r["outcome_status"] = "complete"
        _write_all(rows, ledger)
        c = _write_counter(rows, counter)
    return n, c


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["fill", "status"]); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    os.makedirs(VDIR, exist_ok=True)
    if a.cmd == "fill":
        n, c = fill_outcomes(dry=a.dry_run)
        print("filled %d outcome(s); counted vetoes %d / %d%s" % (n, c["counted_vetoes"], REVIEW_AFTER, " REVIEW DUE" if c["review_due"] else ""))
    else:
        ledger, counter = _paths(a.dry_run)
        with _Lock(): c = _write_counter(read_all(ledger), counter)
        print(json.dumps(c, indent=1))


if __name__ == "__main__":
    main()
