#!/usr/bin/env python3
"""TFO bank-rail scanner — PAPER ONLY. SCALED half@+2 / flat@+5 / stop@-5."""
from __future__ import annotations
import json, shutil, urllib.request, urllib.parse, math, fcntl
import argparse, hashlib, os, tempfile, uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

ROOT = Path("/Users/test/Applications/MultiVenueDesk")
REPORTS = ROOT / "reports"
ROLLBACK = REPORTS / "revisions" / "rollback"
ROLLBACK.mkdir(parents=True, exist_ok=True)
SYD = ZoneInfo("Australia/Sydney")
UTC = ZoneInfo("UTC")

PAIR_TO_KRAKEN = {
    "BTC/USD": "XXBTZUSD", "XBT/USD": "XXBTZUSD",
    "ETH/USD": "XETHZUSD", "XRP/USD": "XXRPZUSD",
    "SOL/USD": "SOLUSD", "UNI/USD": "UNIUSD", "AAVE/USD": "AAVEUSD",
    "SUI/USD": "SUIUSD", "DOGE/USD": "XDGUSD", "ADA/USD": "ADAUSD",
    "NEAR/USD": "NEARUSD", "LINK/USD": "LINKUSD", "TAO/USD": "TAOUSD",
    "HYPE/USD": "HYPEUSD", "XMR/USD": "XXMRZUSD", "VVV/USD": "VVVUSD",
    "AVAX/USD": "AVAXUSD", "ONDO/USD": "ONDOUSD", "LTC/USD": "XLTCZUSD",
    "ENA/USD": "ENAUSD", "INJ/USD": "INJUSD", "ARB/USD": "ARBUSD",
}

ACTIVE = [
    "crypto_liq_beta_book.json",
    "crypto_uni_sui_rs.json",
    "crypto_clip_book.json",
    "crypto_mid_clip_book.json",
    "crypto_avax_ondo_ltc.json",
    "soxl_weekend_probe.json",
]
GCL_FILE = "gap_close_levered_book.json"
SELL_BPS = 5  # 5 bps per sell leg


def funder_action(name, action, cycle_id, reports=REPORTS, current=None):
    if not cycle_id or name != 'crypto_liq_beta_book.json' or action != 'HOLD':
        return action
    current = datetime.now(UTC).timestamp() if current is None else current
    gate = json.loads((reports / 'private_wealth_gate.json').read_text()).get('active') or {}
    expires = datetime.fromisoformat(gate.get('expires_utc', '').replace('Z', '+00:00')).timestamp()
    if gate.get('cycle_id') != cycle_id or not current < expires:
        raise RuntimeError('Funder reduction requires the active unexpired cycle')
    deadline = datetime(2026, 9, 25, 8, 16, 11, tzinfo=UTC).timestamp()
    if not datetime(2026, 9, 24, 8, 23, 48, tzinfo=UTC).timestamp() <= current < deadline:
        raise RuntimeError('Funder cash-raising authorization window is not active')
    return 'FUNDER_DIRECTED_CASH_RAISE'


def now_syd():
    return datetime.now(SYD)


def http_get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "TFO-BankRail/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fetch_kraken(pairs):
    out = {}
    for pair in sorted(set(p for p in pairs if p)):
        try:
            url = "https://api.kraken.com/0/public/Trades?" + urllib.parse.urlencode({"pair": pair, "count": 1})
            data = http_get_json(url, timeout=6)
            if data.get("error"):
                continue
            rows = [v for k, v in data.get("result", {}).items() if k != "last"]
            if len(rows) != 1 or not rows[0]:
                continue
            trade = max(rows[0], key=lambda row: float(row[2]))
            quote = {"px": float(trade[0]), "epoch": float(trade[2]), "source": url}
            bar_url = "https://api.kraken.com/0/public/OHLC?" + urllib.parse.urlencode({"pair": pair, "interval": 1})
            bars = http_get_json(bar_url, timeout=6)
            bar_rows = [v for k, v in bars.get("result", {}).items() if k != "last"]
            if bars.get("error") or len(bar_rows) != 1 or not bar_rows[0]:
                continue
            bar = bar_rows[0][-1]
            if not quote_valid({"px": float(bar[4]), "epoch": float(bar[0])}):
                continue
            quote.update(bar=bar, bar_source=bar_url)
            if quote_valid(quote):
                out[pair] = quote
        except Exception:
            continue
    return out


def quote_valid(quote, current=None):
    try:
        current = datetime.now(UTC).timestamp() if current is None else current
        return (math.isfinite(quote["px"]) and quote["px"] > 0
                and 0 <= current - quote["epoch"] <= 60)
    except (KeyError, TypeError, ValueError):
        return False


def fetch_soxl():
    # Yahoo chart last close/regular
    url = "https://query1.finance.yahoo.com/v8/finance/chart/SOXL?interval=1m&range=1d"
    try:
        data = http_get_json(url)
        meta = data["chart"]["result"][0]["meta"]
        px = meta.get("regularMarketPrice")
        timestamp = meta.get("regularMarketTime")
        age = datetime.now(UTC).timestamp() - float(timestamp or 0)
        session = meta.get("currentTradingPeriod", {}).get("regular", {})
        current = datetime.now(UTC).timestamp()
        if not (session.get("start", 0) <= current < session.get("end", 0)):
            return None, "market_closed: last close is display-only"
        if not (0 <= age <= 60) or not px or not math.isfinite(float(px)) or float(px) <= 0:
            return None, "stale_or_invalid_quote"
        return {"px": float(px), "epoch": float(timestamp), "source": url}, "regular_fresh"
    except Exception as e:
        return None, f"yahoo_err:{e}"


def resolve_px(pair, tickers, book):
    if pair == "SOXL":
        return None  # handled separately
    k = PAIR_TO_KRAKEN.get(pair)
    if k and quote_valid(tickers.get(k)):
        return tickers[k]["px"], k
    # try alt keys
    for cand in (k, pair, pair.replace("/", ""), pair.replace("/USD", "USD")):
        if cand and quote_valid(tickers.get(cand)):
            return tickers[cand]["px"], cand
    # Stored marks are display-only; never use them for a bank or stop.
    return None, k


def mark_book(book, tickers, soxl_px):
    holdings = book.get("holdings") or {}
    cash = float(book.get("cash") or 0.0)
    legs = []
    mark_px = dict(book.get("mark_px_usd") or {})
    incomplete = []
    pos = 0.0
    for pair, qty in holdings.items():
        qty = float(qty or 0)
        if not math.isfinite(qty) or qty < 0:
            raise ValueError("Invalid long units: " + pair)
        if qty == 0:
            continue
        if pair == "SOXL":
            px = soxl_px["px"] if quote_valid(soxl_px) else None
            k = "SOXL"
            if px is None:
                incomplete.append(pair)
                px = float((book.get("mark_px_usd") or {}).get(pair) or (book.get("entry_px") or {}).get(pair) or 0)
                if not px:
                    continue
        else:
            px, k = resolve_px(pair, tickers, book)
            if px is None or not math.isfinite(px) or px <= 0:
                incomplete.append(pair)
                continue
        mark_px[pair] = px
        notional = qty * px
        pos += notional
        entry = float((book.get("entry_px") or {}).get(pair) or 0)
        if not math.isfinite(entry) or entry <= 0 or not 0.4 <= px / entry <= 2.5:
            incomplete.append(pair + ":dirty_or_missing_entry")
        pnl_pct = ((px / entry) - 1.0) * 100.0 if entry else 0.0
        legs.append({
            "pair": pair, "qty": qty, "entry": entry, "mark": px,
            "notional": round(notional, 2), "pnl_pct": round(pnl_pct, 4),
            "kraken": k, "venue": "Yahoo" if pair == "SOXL" else "Kraken",
            "quote": soxl_px if pair == "SOXL" else tickers.get(k),
        })
    mtm = pos + cash
    return mtm, legs, mark_px, incomplete


def decide(pct):
    if pct <= -5.0:
        return "STOP", "hit -5% stop vs deploy_basis"
    if pct >= 5.0:
        return "FULL_BANK", "hit +5% full-flat mandatory vs deploy_basis"
    if pct >= 2.0:
        return "HALF_BANK", "hit >=+2% half-bank rail vs deploy_basis"
    return "HOLD", "within rails"


GCL_BOOK = "gap_close_levered_book"


def paired_transfer_sleeve_to_gcl(book, sleeve_book_id, usd, note, now, tag, gcl):
    """Move proceeds sleeve → GCL with paired capital_transfer + seed/start follow.

    Risk law: seed/start follow paired transfers on BOTH books. Sleeve seed/start
    cut by proceeds; GCL seed/start bumped by inbound proceeds (not cash-only —
    cash-only GCL credit leaves seed lag → firm INVALID). Never leave a flat/$0
    sleeve at pre-bank seed. Same utc, opposite usd.
    """
    usd = round(float(usd), 2)
    if usd <= 0:
        return float(gcl.get("cash") or 0), book
    utc = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    before = float(gcl.get("cash") or 0)
    gcl["cash"] = round(before + usd, 2)
    if not (gcl.get("holdings") or {}):
        gcl["equity"] = gcl["cash"]
    else:
        gcl["equity"] = round(float(gcl["equity"]) + usd, 2)

    sleeve_ev = {
        "type": "capital_transfer",
        "utc": utc,
        "usd": -usd,
        "from": sleeve_book_id,
        "to": GCL_BOOK,
        "note": note,
        "tag": tag,
        "paper_only": True,
    }
    gcl_ev = {
        "type": "capital_transfer",
        "utc": utc,
        "usd": usd,
        "from": sleeve_book_id,
        "to": GCL_BOOK,
        "note": note,
        "tag": tag,
        "paper_only": True,
        "cash_before": before,
        "cash_after": gcl["cash"],
    }
    book.setdefault("capital_events", []).append(sleeve_ev)
    gcl.setdefault("capital_events", []).append(gcl_ev)

    # Sleeve: seed/start follow outbound transfer (cut by proceeds)
    old_seed = float(book.get("seed_usd") or 0)
    old_start = float(book.get("start") or old_seed)
    book["seed_usd"] = round(old_seed - usd, 2)
    book["start"] = round(old_start - usd, 2)
    book["_pair_seed_cut"] = {
        "old_seed_usd": old_seed,
        "new_seed_usd": book["seed_usd"],
        "cut_usd": usd,
    }

    # GCL: seed/start follow inbound transfer (bump by proceeds) — not cash-only
    # (Risk 2026-09-20: cash-only GCL credit leaves seed lag → firm INVALID)
    gcl_old_seed = float(gcl.get("seed_usd") or 0)
    gcl_old_start = float(gcl.get("start") or gcl_old_seed)
    gcl["seed_usd"] = round(gcl_old_seed + usd, 2)
    gcl["start"] = round(gcl_old_start + usd, 2)

    gcl["updated"] = now.isoformat()
    return gcl["cash"], book



def execute_half_bank(book, legs, mtm, deploy_basis, pct, now):
    holdings = dict(book.get("holdings") or {})
    cash = float(book.get("cash") or 0.0)
    entry_px = dict(book.get("entry_px") or {})
    mark_px = dict(book.get("mark_px_usd") or {})
    new_holdings = {}
    sold = {}
    banked = 0.0
    for leg in legs:
        pair = leg["pair"]
        qty = float(holdings.get(pair, 0))
        if qty <= 0:
            continue
        sell_qty = qty * 0.5
        keep_qty = qty - sell_qty
        px = float(leg["mark"])
        proceeds = sell_qty * px
        cost = proceeds * (SELL_BPS / 10000.0)
        net = proceeds - cost
        banked += net
        principal = sell_qty * float(leg["entry"])
        sold[pair] = {"qty": sell_qty, "px": px, "usd": net,
                      "principal_usd": principal, "proceeds_usd": net,
                      "gross_pnl_usd": proceeds - principal, "pnl_usd": net - principal,
                      "fees_usd": cost, "slippage_estimate_usd": 0.0,
                      "cost_source": "registered PAPER 5bps sell-cost model; zero additional slippage assumed, not measured",
                      "quote": leg["quote"]}
        mark_px[pair] = px
        if keep_qty > 1e-12:
            new_holdings[pair] = keep_qty
    # Move sold proceeds to GCL (not leave as sleeve cash)
    residual_pos = sum(new_holdings[p] * mark_px[p] for p in new_holdings)
    # sleeve cash stays as working dust; banked goes to GCL
    residual_equity = residual_pos + cash
    old_basis = deploy_basis
    book["holdings"] = new_holdings
    book["cash"] = round(cash, 2)
    book["mark_px_usd"] = mark_px
    book["equity"] = round(residual_equity, 2)
    book["deploy_basis_usd"] = round(residual_equity, 2)
    ev = {
        "type": "HALF_BANK",
        "ts": now.isoformat(),
        "utc": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pct_at_bank": round(pct, 4),
        "mtm_at_bank": round(mtm, 2),
        "old_deploy_basis_usd": old_basis,
        "new_deploy_basis_usd": book["deploy_basis_usd"],
        "banked_to_gcl_usd": round(banked, 2),
        "sold": sold,
        "note": "SCALED LAW half-bank ~50% flatten @ live marks; proceeds → GCL via paired capital_transfer; rebase deploy_basis on residual; seed/start cut by proceeds in paired_transfer; PAPER",
    }
    book.setdefault("capital_events", []).append(ev)
    book["status"] = book.get("status") if "OPEN" in str(book.get("status") or "") else "OPEN_CLIP_2_5_ARMED"
    if "SOXL" in str(book.get("status") or ""):
        book["status"] = "OPEN_SOXL_2_5_ARMED"
    else:
        book["status"] = "OPEN_CLIP_2_5_ARMED"
    book["last_mark_action"] = "HALF_BANK"
    return book, ev, residual_equity, banked


def execute_full_flat(book, legs, mtm, deploy_basis, pct, now, reason_action):
    holdings = dict(book.get("holdings") or {})
    cash = float(book.get("cash") or 0.0)
    sold = {}
    banked = cash
    for leg in legs:
        pair = leg["pair"]
        qty = float(holdings.get(pair, 0))
        if qty <= 0:
            continue
        px = float(leg["mark"])
        proceeds = qty * px
        cost = proceeds * (SELL_BPS / 10000.0)
        net = proceeds - cost
        banked += net
        principal = qty * float(leg["entry"])
        sold[pair] = {"qty": qty, "px": px, "usd": net,
                      "principal_usd": principal, "proceeds_usd": net,
                      "gross_pnl_usd": proceeds - principal, "pnl_usd": net - principal,
                      "fees_usd": cost, "slippage_estimate_usd": 0.0,
                      "cost_source": "registered PAPER 5bps sell-cost model; zero additional slippage assumed, not measured",
                      "quote": leg["quote"]}
    book["holdings"] = {}
    book["cash"] = 0.0
    book["equity"] = 0.0
    ev = {
        "type": reason_action,
        "ts": now.isoformat(),
        "utc": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pct_at_exit": round(pct, 4),
        "mtm_at_exit": round(mtm, 2),
        "deploy_basis_usd": deploy_basis,
        "banked_to_gcl_usd": round(banked, 2),
        "sold": sold,
        "note": f"{reason_action} full flatten @ live marks; proceeds+cash → GCL via paired capital_transfer; seed/start cut by proceeds in paired_transfer; PAPER",
    }
    book.setdefault("capital_events", []).append(ev)
    book["closed"] = True
    book["banked_utc"] = ev["utc"]
    if reason_action == 'FUNDER_DIRECTED_CASH_RAISE':
        book['status'] = 'CLOSED_FUNDER_DIRECTED'
        ev['mandate_utc'] = '2026-09-24T08:23:48Z'
        ev['rationale'] = 'Funder-directed cash raising: remove overlapping BTC/ETH/XRP/SOL beta; not a strategy rail or approved replacement trade.'
    elif reason_action == "STOP":
        book["status"] = "CLOSED_STOPPED_NEG5"
    else:
        book["status"] = "CLOSED_BANKED_FLAT5"
    book["last_mark_action"] = reason_action
    return book, ev, 0.0, banked


def encoded(value):
    return (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_bytes(path, data):
    fd, temp = tempfile.mkstemp(prefix=".rail-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def recover_transaction():
    journal = REPORTS / ".tfo_bank_rail_transaction.json"
    if not journal.exists():
        return
    tx = json.loads(journal.read_bytes())
    if tx["status"] == "COMMITTED":
        return
    if tx["status"] != "PREPARED":
        raise RuntimeError("Unknown rail transaction state")
    for name, entry in tx["entries"].items():
        if name not in ACTIVE + [GCL_FILE]:
            raise RuntimeError("Unexpected rail transaction target")
        if digest(encoded(entry["after"])) != entry["after_hash"]:
            raise RuntimeError("Corrupt rail transaction")
        if digest((REPORTS / name).read_bytes()) not in (entry["before_hash"], entry["after_hash"]):
            raise RuntimeError("Ledger changed during rail recovery: " + name)
    # Replay an already authorised, durable transaction, never a new quote decision.
    for name, entry in tx["entries"].items():
        atomic_bytes(REPORTS / name, encoded(entry["after"]))
    tx["status"] = "COMMITTED"
    atomic_bytes(journal, encoded(tx))


def commit_books(staged, originals, quotes, ticket_id):
    for name, raw in originals.items():
        if (REPORTS / name).read_bytes() != raw:
            raise RuntimeError("Ledger changed during scan: " + name)
    if not all(quote_valid(q) for q in quotes):
        raise RuntimeError("Quote aged before commit; no financial changes made")
    delta = sum(float(book.get("equity", 0)) - float(json.loads(originals[name]).get("equity", 0))
                for name, book in staged.items())
    if not math.isfinite(delta) or abs(delta) > 50000:
        raise RuntimeError("Firm mark circuit breaker; no financial changes made")
    tx = {"status": "PREPARED", "paper_only": True, "ticket_id": ticket_id,
          "quotes": quotes, "entries": {
              name: {"before_hash": digest(originals[name]),
                     "after_hash": digest(encoded(book)), "after": book}
              for name, book in staged.items()}}
    atomic_bytes(REPORTS / ".tfo_bank_rail_transaction.json", encoded(tx))
    recover_transaction()


def main(execute=False, funder_cycle_id=None):
    if execute:
        recover_transaction()
    import firm_risk, book_coverage
    risk = firm_risk.run(verbose=False)
    unclaimed, _ = book_coverage.coverage_audit(verbose=False)
    if execute and (risk.get("capital_accounting_valid") is not True or unclaimed
                    or risk.get("coverage_audit_clean") is not True):
        raise RuntimeError("Local accounting/coverage gate rejected execution")
    now = now_syd()
    tag = now.strftime("%Y%m%d_%H%M%S_%f")
    ticket_id = str(uuid.uuid4())
    originals, staged, commit_quotes = {}, {}, []
    gcl_path = REPORTS / GCL_FILE
    originals[GCL_FILE] = gcl_path.read_bytes()
    gcl = json.loads(originals[GCL_FILE])
    # load books
    books = {}
    kraken_needed = set()
    for name in ACTIVE:
        path = REPORTS / name
        originals[name] = path.read_bytes()
        book = json.loads(originals[name])
        books[name] = (book, path)
        for pair in (book.get("holdings") or {}):
            if pair != "SOXL":
                k = PAIR_TO_KRAKEN.get(pair)
                if k:
                    kraken_needed.add(k)
    tickers = fetch_kraken(sorted(kraken_needed))
    # also index by pair alias from result keys
    soxl_px, soxl_state = fetch_soxl()
    now = now_syd()  # Decisions and cash events occur after quote collection.

    # rollback snapshot before any mutate
    rb = ROLLBACK / f"bank-rail-{tag}"
    rb.mkdir(parents=True, exist_ok=True)
    for name, (book, path) in books.items():
        shutil.copy2(path, rb / name)
    gcl_path = REPORTS / GCL_FILE
    shutil.copy2(gcl_path, rb / GCL_FILE)
    for fr in ("firm_risk.json", "firm_risk_state.json"):
        p = REPORTS / fr
        if p.exists():
            shutil.copy2(p, rb / fr)

    sleeves = []
    executed = []
    prices = {}
    if soxl_px is not None:
        prices["SOXL"] = soxl_px["px"]

    for name, (book, path) in books.items():
        status = book.get("status") or ""
        if str(status).startswith("CLOSED") or book.get("closed") is True:
            sleeves.append({
                "book": name.replace(".json", ""), "pct": 0.0, "action": "SKIP",
                "mtm_usd": round(float(book.get("equity") or 0), 2), "net_usd": 0.0,
                "deploy_basis_usd": book.get("deploy_basis_usd"),
                "reason": "closed book", "status": status,
            })
            continue
        deploy_basis = float(book.get("deploy_basis_usd") or 0)
        mtm, legs, mark_px, incomplete = mark_book(book, tickers, soxl_px)
        for leg in legs:
            prices[leg["pair"]] = leg["mark"]
        if deploy_basis <= 0:
            sleeves.append({
                "book": name.replace(".json", ""), "pct": 0.0, "action": "SKIP",
                "mtm_usd": round(mtm, 2), "net_usd": 0.0, "deploy_basis_usd": deploy_basis,
                "reason": "no deploy_basis", "status": status,
            })
            continue
        if incomplete:
            sleeves.append({
                "book": name.replace(".json", ""), "pct": 0.0, "action": "SKIP",
                "mtm_usd": round(mtm, 2), "net_usd": 0.0, "deploy_basis_usd": deploy_basis,
                "reason": f"incomplete marks: {incomplete}", "status": status,
            })
            continue
        pct = ((mtm / deploy_basis) - 1.0) * 100.0
        if not all(math.isfinite(v) for v in (mtm, deploy_basis, pct)):
            raise ValueError("Nonfinite ledger value: " + name)
        action, reason = decide(pct)
        action = funder_action(name, action, funder_cycle_id)
        if action == 'FUNDER_DIRECTED_CASH_RAISE':
            reason = 'Explicit funder cash-raising order; overlapping crypto beta; no replacement return guaranteed'
        if not execute and action != "HOLD":
            sleeves.append({"book": name[:-5], "pct": round(pct, 4),
                            "action": "WOULD_" + action, "mtm_usd": round(mtm, 2),
                            "net_usd": round(mtm-deploy_basis, 2), "reason": reason,
                            "deploy_basis_usd": deploy_basis, "status": status})
            continue
        banked_usd = 0.0
        ev = None
        if action in ("HALF_BANK", "FULL_BANK", "STOP", 'FUNDER_DIRECTED_CASH_RAISE'):
            if action == "HALF_BANK":
                book, ev, new_mtm, banked_usd = execute_half_bank(book, legs, mtm, deploy_basis, pct, now)
                mtm_post = new_mtm
            else:
                book, ev, new_mtm, banked_usd = execute_full_flat(book, legs, mtm, deploy_basis, pct, now, action)
                mtm_post = new_mtm
            sleeve_id = name.replace(".json", "")
            if banked_usd > 0:
                note = (
                    f"{action} paired out @ {pct:.4f}% vs deploy_basis; "
                    f"proceeds → GCL powder; seed/start cut by proceeds; PAPER paired"
                )
                gcl_cash, book = paired_transfer_sleeve_to_gcl(
                    book, sleeve_id, banked_usd, note, now, tag, gcl,
                )
                if ev is not None:
                    ev["seed_after_pair"] = book.get("seed_usd")
                    ev["start_after_pair"] = book.get("start")
                    ev["paired_capital_transfer_usd"] = round(banked_usd, 2)
            else:
                gcl_cash = float(json.load(open(gcl_path)).get("cash") or 0)
            book["last_mark_utc"] = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            book["last_mark_syd"] = now.isoformat()
            book["last_mark_venue"] = "Kraken timestamped public Trades; SOXL via Yahoo"
            book["last_mark_usd"] = round(mtm_post, 2)
            book["last_mark_pct_net_since_deploy"] = round(pct, 4)
            book["last_mark_routine"] = "tfo-bank-rail-scanner"
            book["updated"] = now.isoformat()
            ev.update(ticket_id=ticket_id + ":" + name[:-5], paper_only=True, quote_evidence=[leg["quote"] for leg in legs])
            staged[name] = book
            staged[GCL_FILE] = gcl
            commit_quotes.extend(leg["quote"] for leg in legs)
            exec_rec = {
                "action": action,
                "ticket_id": ev["ticket_id"],
                "book": name.replace(".json", ""),
                "pct": round(pct, 4),
                "net_usd": round(mtm - deploy_basis, 2),
                "banked_to_gcl_usd": round(banked_usd, 2),
                "new_deploy_basis_usd": book.get("deploy_basis_usd"),
                "residual_equity_usd": round(mtm_post, 2),
                "sold": (ev or {}).get("sold"),
                "rollback": str(rb),
            }
            executed.append(exec_rec)
            sleeves.append({
                "book": name.replace(".json", ""),
                "pct": round(pct, 4),
                "action": action,
                "mtm_usd": round(mtm, 2),
                "net_usd": round(mtm - deploy_basis, 2),
                "deploy_basis_usd": deploy_basis,
                "reason": reason,
                "status": book.get("status"),
                "banked_to_gcl_usd": round(banked_usd, 2),
                "new_deploy_basis_usd": book.get("deploy_basis_usd"),
            })
        else:
            # HOLD — update marks only (no basis mutate)
            book["mark_px_usd"] = mark_px
            book["equity"] = round(mtm, 2)
            book["last_mark_utc"] = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            book["last_mark_syd"] = now.isoformat()
            book["last_mark_venue"] = "Kraken timestamped public Trades; SOXL via Yahoo"
            book["last_mark_usd"] = round(mtm, 2)
            book["last_mark_action"] = "HOLD"
            book["last_mark_pct_net_since_deploy"] = round(pct, 4)
            book["last_mark_routine"] = "tfo-bank-rail-scanner"
            book["updated"] = now.isoformat()
            if execute:
                staged[name] = book
                commit_quotes.extend(leg["quote"] for leg in legs)
            sleeves.append({
                "book": name.replace(".json", ""),
                "pct": round(pct, 4),
                "action": "HOLD",
                "mtm_usd": round(mtm, 2),
                "net_usd": round(mtm - deploy_basis, 2),
                "deploy_basis_usd": deploy_basis,
                "reason": reason,
                "status": status,
            })

    if execute and staged:
        commit_books(staged, originals, commit_quotes, ticket_id)
        firm_risk.run(verbose=False)
    gcl_cash = float(gcl.get("cash") or 0)
    fr = {}
    try:
        fr_raw = json.load(open(REPORTS / "firm_risk.json"))
        fr = {
            "capital_accounting_valid": fr_raw.get("capital_accounting_valid", False),
            "dd_pct": fr_raw.get("firm_drawdown_pct"),
            "equity": fr_raw.get("firm_equity_live_usd") or fr_raw.get("firm_equity_usd"),
            "status": fr_raw.get("level"),
            "new_entries_halted": fr_raw.get("new_entries_halted"),
        }
    except Exception:
        fr = {"capital_accounting_valid": None, "dd_pct": None, "equity": None, "status": None}

    # nearest to +2% from above or highest pct
    nearest = sorted((s for s in sleeves if s["action"] not in ("SKIP",)),
                     key=lambda s: min(abs(s["pct"] - rail) for rail in (-5, 2, 5)))[:5]
    nearest = [{"book": s["book"], "pct": s["pct"], "mtm": s["mtm_usd"]} for s in nearest]

    overall = "BANKS_STOPS_EXECUTED" if executed else ("NO_EXECUTIONS" if execute else "DRY_RUN")
    report = {
        "asof": now.isoformat(),
        "timezone": "Australia/Sydney",
        "paper_only": True,
        "desk_scope": "LOCAL_ONLY",
        "execution_enabled": execute,
        "quote_evidence": tickers,
        "routine": "tfo-bank-rail-scanner",
        "bank_law": "SCALED rail: half-bank @ >=+2% vs deploy_basis_usd (rebase residual); full flat by +5%; stop @ <=-5%; never miss +2%",
        "overall": overall,
        "notify_brett": bool(executed),
        "banks_stops_executed": executed,
        "nearest": nearest,
        "sleeves": sleeves,
        "gcl_powder_cash": round(gcl_cash, 2),
        "firm_risk": fr,
        "prices": prices,
        "soxl_market_state": soxl_state,
        "price_source": "Kraken timestamped public Trades; SOXL via Yahoo regular session",
        "rollback_dir": str(rb),
    }
    out1 = REPORTS / f"tfo_bank_rail_scan_{tag}.json"
    out2 = REPORTS / "tfo_bank_rail_scan_latest.json"
    with open(out1, "w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    with open(out2, "w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    if executed:
        with open(REPORTS / "tfo_bank_rail_scan_notify.json", "w") as f:
            json.dump(report, f, indent=2)
            f.write("\n")
    print(json.dumps({
        "overall": overall,
        "notify_brett": bool(executed),
        "banks_stops_executed": executed,
        "nearest": nearest,
        "asof": report["asof"],
        "report": str(out2),
        "sleeves_summary": [{k: s[k] for k in ("book", "pct", "action", "mtm_usd", "net_usd")} for s in sleeves],
        "gcl_powder_cash": report["gcl_powder_cash"],
        "firm_risk": fr,
    }, indent=2))


if __name__ == "__main__":
    from tfo_equity_refresh import writer_locks
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Execute eligible PAPER rails; default is preview")
    parser.add_argument('--funder-cycle-id', help='One-time funder-directed liquid-beta reduction under an active cycle')
    args = parser.parse_args()
    with writer_locks(REPORTS):
        main(execute=args.execute, funder_cycle_id=args.funder_cycle_id)
