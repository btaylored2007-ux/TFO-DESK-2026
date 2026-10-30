#!/bin/bash
# PAPER-ONLY TFO desk sweep fixes — 2026-09-19
# Goals: dated_event DATA.L liquidate→GCL; dead clutter CLOSE; semis one-beta;
#        tournament clip_rules 2-5%; firm_risk.run() until capital_accounting_valid
# Run on Mac machineId 61edf223-7c1d-4f19-92a9-d34c021c8074
set -euo pipefail
export PATH="/Library/Frameworks/Python.framework/Versions/3.14/bin:/usr/local/bin:$PATH"
DESK="${DESK:-/Users/test/Applications/MultiVenueDesk}"
cd "$DESK"
TS=$(date +%Y%m%d-%H%M%S)
RB="reports/revisions/rollback/sweep-fixes-$TS"
mkdir -p "$RB" reports
echo "DESK=$DESK"
echo "ROLLBACK=$RB"
echo "LOCAL=$(date '+%Y-%m-%dT%H:%M:%S %Z') UTC=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "PYTHON=$(which python3); $(python3 --version)"

# Backup all ledgers we may touch + firm risk
for f in \
  reports/dated_event_book.json \
  reports/gap_close_levered_book.json \
  reports/fundamental_book.json \
  reports/convexity_sleeve.json \
  reports/soxl_weekend_probe.json \
  reports/crypto_rumour_book.json \
  reports/crypto_rumour.json \
  reports/punt_24h.json \
  reports/punt_24h_book.json \
  reports/firm_risk.json \
  reports/firm_risk_state.json \
  reports/*tournament*.json \
  reports/*weights*.json
do
  for p in $f; do
    [ -f "$p" ] || continue
    cp -p "$p" "$RB/$(basename "$p")"
    echo "BACKUP $p"
  done
done
# Also backup any tournament books discovered by registry later (python will re-backup)
ls -la "$RB" | head -60
export RB TS

python3 << 'PY'
import json, os, copy, glob, math
from pathlib import Path
from datetime import datetime, timezone

DESK = Path("/Users/test/Applications/MultiVenueDesk")
os.chdir(DESK)
RB = Path(os.environ["RB"])
UTC = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
AEST_NOTE = datetime.now().strftime("%Y-%m-%d %H:%M %Z")
actions = []
moved = []  # list of {from,to,usd,note}

def fnum(x, default=0.0):
    try:
        if x is None: return float(default)
        return float(x)
    except Exception:
        return float(default)

def load(p: Path):
    return json.loads(p.read_text())

def atomic_write(p: Path, obj):
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, p)

def backup_once(p: Path):
    if p.exists():
        dest = RB / p.name
        if not dest.exists():
            import shutil
            shutil.copy2(p, dest)
            actions.append(f"backup {p} -> {dest}")

def ensure_events(book: dict):
    if "capital_events" not in book or not isinstance(book.get("capital_events"), list):
        book["capital_events"] = list(book.get("capital_events") or [])
    return book["capital_events"]

def set_cash(book, cash):
    cash = round(float(cash), 2)
    for k in ("cash_usd", "cash"):
        if k in book:
            book[k] = cash
    if "cash_usd" not in book and "cash" not in book:
        book["cash_usd"] = cash
    return cash

def get_cash(book):
    return fnum(book.get("cash_usd", book.get("cash")))

def get_equity(book):
    return fnum(book.get("equity_usd", book.get("equity", book.get("marked_equity"))))

def get_seed(book):
    return fnum(book.get("seed_usd", book.get("seed", book.get("start_usd", book.get("start")))))

def set_seed(book, seed):
    seed = round(float(seed), 2)
    for k in ("seed_usd", "seed", "start_usd", "start"):
        if k in book:
            book[k] = seed
    if "seed_usd" not in book:
        book["seed_usd"] = seed
    return seed

def set_equity(book, eq):
    eq = round(float(eq), 2)
    for k in ("equity_usd", "equity", "marked_equity"):
        if k in book:
            book[k] = eq
    if "equity_usd" not in book:
        book["equity_usd"] = eq
    return eq

def holdings_list(book):
    h = book.get("holdings") or book.get("positions") or book.get("open_positions")
    if h is None:
        return []
    if isinstance(h, dict):
        # dict of symbol -> pos OR list-like
        out = []
        for k, v in h.items():
            if isinstance(v, dict):
                vv = dict(v)
                vv.setdefault("symbol", k)
                out.append(vv)
            else:
                out.append({"symbol": k, "units": v})
        return out
    if isinstance(h, list):
        return h
    return []

def set_holdings_empty(book):
    for k in ("holdings", "positions", "open_positions"):
        if k in book:
            if isinstance(book[k], dict):
                book[k] = {}
            else:
                book[k] = []
    book.setdefault("holdings", [])

def paired_transfer(src, dst, src_key, dst_key, usd, note, also_cut_seed=True):
    """Move usd cash from src book to dst book with paired capital_events.
    Positive usd = amount leaving src / entering dst.
    Also cuts seed on src and raises seed on dst when also_cut_seed (honest capital, not P&L).
    """
    usd = round(float(usd), 2)
    if usd <= 0:
        return 0.0
    sc = get_cash(src)
    if sc + 1e-9 < usd:
        # transfer what we can
        usd = round(sc, 2)
        if usd <= 0:
            actions.append(f"SKIP transfer {src_key}->{dst_key}: no cash")
            return 0.0
    set_cash(src, sc - usd)
    set_cash(dst, get_cash(dst) + usd)
    # equity follows cash for flat/cash books; for residual after liquidate equity≈cash
    # Adjust equity by same cash delta when book is effectively cash-like after flatten
    set_equity(src, get_equity(src) - usd)
    set_equity(dst, get_equity(dst) + usd)
    if also_cut_seed:
        set_seed(src, get_seed(src) - usd)
        set_seed(dst, get_seed(dst) + usd)
    ev_common = {
        "type": "capital_transfer",
        "utc": UTC,
        "pair_id": f"sweep-{os.environ.get('TS')}-{src_key}-to-{dst_key}",
        "note": note,
        "repair": str(RB),
        "paper_only": True,
    }
    ensure_events(src).append({
        **ev_common,
        "usd": -usd,
        "from": src_key,
        "to": dst_key,
    })
    ensure_events(dst).append({
        **ev_common,
        "usd": +usd,
        "from": src_key,
        "to": dst_key,
    })
    moved.append({"from": src_key, "to": dst_key, "usd": usd, "note": note})
    actions.append(f"TRANSFER ${usd:.2f} {src_key} -> {dst_key}: {note}")
    return usd

def find_book_path(*names):
    for n in names:
        p = DESK / "reports" / n
        if p.exists():
            return p
    # fuzzy
    for n in names:
        stem = n.replace(".json", "")
        cands = list((DESK/"reports").glob(f"*{stem}*.json"))
        cands = [c for c in cands if "rollback" not in str(c) and "repair" not in str(c) and "backup" not in str(c).lower()]
        if cands:
            return cands[0]
    return None

# ---------------------------------------------------------------------------
# 1) dated_event_book — audit DATA.L / zombie equity
# ---------------------------------------------------------------------------
print("==== 1) dated_event_book ====")
deb_path = find_book_path("dated_event_book.json")
gcl_path = find_book_path("gap_close_levered_book.json", "gap_close_levered.json")
print("DEB", deb_path, "GCL", gcl_path)

zombie_liquidated = False
if deb_path and gcl_path:
    backup_once(deb_path); backup_once(gcl_path)
    deb = load(deb_path)
    gcl = load(gcl_path)
    holds = holdings_list(deb)
    # Find DATA.L or any remaining equity-ish positions
    data_syms = []
    for h in holds:
        sym = str(h.get("symbol") or h.get("ticker") or h.get("instrument") or "").upper()
        units = fnum(h.get("units", h.get("qty", h.get("quantity", h.get("size")))))
        mv = fnum(h.get("market_value_usd", h.get("value_usd", h.get("mv_usd", h.get("notional_usd")))))
        if abs(units) > 1e-9 or abs(mv) > 1e-6:
            data_syms.append((sym, units, mv, h))
    print("DEB holdings live:", [(s,u,m) for s,u,m,_ in data_syms])
    status = str(deb.get("status") or deb.get("book_status") or "").upper()
    eq = get_equity(deb)
    seed = get_seed(deb)
    cash = get_cash(deb)
    print(f"DEB status={status} equity={eq} seed={seed} cash={cash}")

    # Heuristic: zombie if (DATA.L present OR equity << seed ~6k vs large seed) and no live catalyst note
    catalyst = deb.get("catalyst") or deb.get("live_catalyst") or deb.get("event") or deb.get("thesis")
    catalyst_live = False
    if isinstance(catalyst, dict):
        catalyst_live = bool(catalyst.get("live") or catalyst.get("active"))
    elif isinstance(catalyst, str) and catalyst.strip():
        # treat explicit CLOSED/EXPIRED as dead
        catalyst_live = not any(x in catalyst.upper() for x in ("CLOSED", "EXPIRED", "DEAD", "DONE", "NONE"))
    # also check notes/status fields
    if status in ("CLOSED", "DEAD", "LIQUIDATED", "FLAT", "DONE"):
        catalyst_live = False
    # zombie equity ~6k vs large seed
    zombie_eq = (eq > 0 and seed > 0 and eq < 15000 and seed > max(eq * 3, 20000)) or (5000 <= eq <= 8000 and seed >= 20000)
    has_data = any("DATA" in s for s,_,_,_ in data_syms)
    print("catalyst_live", catalyst_live, "zombie_eq", zombie_eq, "has_data", has_data)

    should_liq = (has_data or zombie_eq or (data_syms and not catalyst_live)) and status not in ("CLOSED", "DEAD", "LIQUIDATED")
    # If already flat cash with residual, still transfer residual
    if should_liq or (eq > 1 and not catalyst_live and (has_data or zombie_eq or not data_syms)):
        # Liquidate: convert all holdings to cash at current marked MV (honest — use book marks, do not invent)
        realized_add = 0.0
        for sym, units, mv, h in data_syms:
            # Prefer explicit market value; else units * mark
            mark = fnum(h.get("mark", h.get("price", h.get("last", h.get("px")))))
            if abs(mv) < 1e-9 and mark and units:
                mv = units * mark
            # Cost basis if present for realised
            cost = fnum(h.get("cost_basis_usd", h.get("cost_usd", h.get("avg_cost_usd"))))
            if cost == 0 and mark and units:
                avg = fnum(h.get("avg_price", h.get("avg_px", h.get("entry_price"))))
                cost = avg * units if avg else 0.0
            # Add MV to cash (close position)
            cash = get_cash(deb) + mv
            set_cash(deb, cash)
            pnl = mv - cost if cost else 0.0
            realized_add += pnl
            ensure_events(deb).append({
                "type": "liquidation",
                "utc": UTC,
                "symbol": sym,
                "units": units,
                "usd": round(mv, 2),
                "realized_pnl_usd": round(pnl, 2),
                "note": f"PAPER sweep: liquidate {sym} (no live catalyst / zombie). Honest mark from ledger. {UTC}",
                "repair": str(RB),
            })
            actions.append(f"LIQUIDATE {sym} units={units} mv=${mv:.2f} pnl=${pnl:.2f}")
        set_holdings_empty(deb)
        # Update realised if field exists
        if abs(realized_add) > 1e-9:
            for k in ("realised_pnl_usd", "realized_pnl_usd", "realized_pnl", "realised"):
                if k in deb:
                    deb[k] = round(fnum(deb[k]) + realized_add, 2)
                    break
            else:
                deb["realized_pnl_usd"] = round(realized_add, 2)
        # Equity = cash after flatten (no positions)
        residual = get_cash(deb)
        set_equity(deb, residual)
        deb["status"] = "CLOSED"
        deb["book_status"] = "CLOSED"
        deb["closed_utc"] = UTC
        deb["close_note"] = (
            "PAPER sweep 2026-09-19: liquidated remaining DATA.L/zombie equity; "
            "no live catalyst; residual cash transferred to gap_close_levered_book. "
            f"Rollback {RB.name}"
        )
        # Transfer residual to GCL with paired transfer + seed cuts
        xfer = paired_transfer(
            deb, gcl, "dated_event_book", "gap_close_levered_book",
            residual,
            "PAPER sweep: dated_event residual cash → GCL after DATA.L/zombie close; paired + seed cut. NOT P&L.",
            also_cut_seed=True,
        )
        # After transfer, DEB should be ~0 equity/cash
        set_cash(deb, get_cash(deb))  # already adjusted
        set_equity(deb, get_cash(deb))
        if get_equity(deb) < 1.0:
            set_equity(deb, get_cash(deb))
            deb["status"] = "CLOSED"
        atomic_write(deb_path, deb)
        atomic_write(gcl_path, gcl)
        zombie_liquidated = True
        actions.append(f"DEB closed; transferred ${xfer:.2f} to GCL")
    else:
        actions.append(f"DEB left unchanged: catalyst_live={catalyst_live} status={status} eq={eq} holds={len(data_syms)}")
        print("DEB NO LIQ — conditions not met or already closed")
else:
    actions.append(f"DEB/GCL path missing deb={deb_path} gcl={gcl_path}")

# ---------------------------------------------------------------------------
# 2) Dead clutter — crypto rumour + 24h punt + equity0 DEAD/LIQUIDATED/FROZEN
# ---------------------------------------------------------------------------
print("==== 2) dead clutter ====")
dead_candidates = []
for pat in ("*rumour*", "*rumor*", "*punt_24h*", "*24h_punt*", "*punt24*"):
    for p in (DESK/"reports").glob(pat + ("" if pat.endswith(".json") else ".json") if False else pat):
        pass
# broader scan via registry if available
registry_books = []
try:
    import book_coverage as bc
    import importlib; importlib.reload(bc)
    registry_books = list(bc.registry())
    print("REGISTRY_N", len(registry_books))
except Exception as e:
    print("registry_err", e)
    registry_books = []

def book_file_for_key(key):
    # try registry path
    for b in registry_books:
        if b.get("key") == key and b.get("path"):
            return Path(b["path"])
    p = DESK/"reports"/f"{key}.json"
    if p.exists(): return p
    return find_book_path(f"{key}.json")

dead_keys_forced = []
for name in ("crypto_rumour", "crypto_rumour_book", "punt_24h", "punt_24h_book", "24h_punt"):
    p = book_file_for_key(name) if name else None
    if not p:
        p = find_book_path(f"{name}.json")
    if p and p.exists():
        dead_keys_forced.append((name, p))

# Also scan registry for DEAD/LIQUIDATED/FROZEN with equity ~0
for b in registry_books:
    key = b.get("key") or ""
    eq = fnum(b.get("equity_usd"))
    # warnings may mention FROZEN
    warns = " ".join(str(w) for w in (b.get("warnings") or []))
    st = ""
    path = b.get("path")
    if path and Path(path).exists():
        try:
            raw = load(Path(path))
            st = str(raw.get("status") or raw.get("book_status") or "").upper()
        except Exception:
            raw = None
    else:
        raw = None
    frozen = "FROZEN" in warns.upper() or st in ("DEAD", "LIQUIDATED", "CLOSED", "FROZEN")
    if frozen and abs(eq) < 1.0:
        if path:
            dead_keys_forced.append((key, Path(path)))

seen=set()
unique_dead=[]
for k,p in dead_keys_forced:
    if str(p) in seen: continue
    seen.add(str(p))
    unique_dead.append((k,p))

for key, p in unique_dead:
    backup_once(p)
    d = load(p)
    st = str(d.get("status") or d.get("book_status") or "").upper()
    eq = get_equity(d)
    cash = get_cash(d)
    holds = holdings_list(d)
    live_h = [h for h in holds if abs(fnum(h.get("units", h.get("qty", 0)))) > 1e-9]
    # Only CLOSE-mark if clearly dead / zero equity / rumour / punt
    name_l = key.lower()
    force = ("rumour" in name_l or "rumor" in name_l or "punt" in name_l)
    if force or (abs(eq) < 1.0 and not live_h) or st in ("DEAD", "LIQUIDATED", "FROZEN", "CLOSED"):
        d["status"] = "CLOSED" if st != "FROZEN" else "DEAD"
        # Prefer DEAD for rumour/punt with no edge
        if "rumour" in name_l or "rumor" in name_l or "punt" in name_l:
            d["status"] = "DEAD"
        d["book_status"] = d["status"]
        d["coverage_keep"] = True  # do not delete — coverage requires file
        d["capital_accounting_exclude_reason"] = (
            "CLOSED/DEAD clutter; equity≈0; excluded from active capital_accounting aggregation where supported"
        )
        d["sweep_note"] = (
            f"PAPER sweep {UTC}: marked {d['status']} (dead clutter). "
            "File retained for coverage. No capital invent. "
            f"Rollback {RB.name}"
        )
        # Ensure capital_events list exists so firm_risk doesn't choke
        ensure_events(d)
        # Zero residual tiny cash noise into realised/note without transfer if < $1
        if abs(eq) < 1.0 and abs(cash) < 1.0:
            set_equity(d, 0.0)
            set_cash(d, 0.0)
        # Flag for firm_risk / book_coverage
        d["include_in_firm_equity"] = False
        d["dead"] = True
        atomic_write(p, d)
        actions.append(f"MARK {key} status={d['status']} eq={eq} (file kept)")
    else:
        actions.append(f"SKIP dead-mark {key}: eq={eq} live_holds={len(live_h)} status={st}")

# ---------------------------------------------------------------------------
# 3) Semis overlap — one beta stack
# ---------------------------------------------------------------------------
print("==== 3) semis overlap ====")
soxl_p = find_book_path("soxl_weekend_probe.json")
fund_p = find_book_path("fundamental_book.json")
conv_p = find_book_path("convexity_sleeve.json")
print("SOXL", soxl_p, "FUND", fund_p, "CONV", conv_p)

semis_map = {"soxl_probe": [], "fund_bofa": [], "convexity": []}

def extract_semis(book, label):
    names = []
    for h in holdings_list(book):
        sym = str(h.get("symbol") or h.get("ticker") or "").upper()
        units = fnum(h.get("units", h.get("qty", h.get("quantity"))))
        if abs(units) < 1e-12:
            continue
        # semis / beta names
        if sym in ("SOXL", "SOXS", "NVDA", "TSM", "AVGO", "AMD", "SMH", "SOXX", "TSLA") or "SEMI" in sym:
            names.append((sym, units, h))
    return names

if soxl_p:
    backup_once(soxl_p)
    soxl = load(soxl_p)
    semis_map["soxl_probe"] = extract_semis(soxl, "soxl")
if fund_p:
    backup_once(fund_p)
    fund = load(fund_p)
    # BofA semis theme — look for theme tags
    fund_semis = []
    for h in holdings_list(fund):
        sym = str(h.get("symbol") or h.get("ticker") or "").upper()
        units = fnum(h.get("units", h.get("qty", h.get("quantity"))))
        theme = str(h.get("theme") or h.get("sleeve") or h.get("tag") or h.get("strategy") or "").lower()
        if abs(units) < 1e-12:
            continue
        if "semi" in theme or "bofa" in theme or sym in ("SOXL", "NVDA", "TSM", "AVGO", "AMD", "SMH"):
            fund_semis.append((sym, units, h, theme))
    semis_map["fund_bofa"] = fund_semis
if conv_p:
    backup_once(conv_p)
    conv = load(conv_p)
    semis_map["convexity"] = extract_semis(conv, "conv")

print("MAP soxl", [(s,u) for s,u,_ in semis_map["soxl_probe"]])
print("MAP fund", [(s,u,t) for s,u,_,t in semis_map["fund_bofa"]])
print("MAP conv", [(s,u) for s,u,_ in semis_map["convexity"]])

# Policy: Prefer keeping SOXL probe + BofA FUND. Fold/close convexity semis that duplicate.
# Label opportunistic beta. 2-5% rails on open sleeves.
if conv_p and semis_map["convexity"]:
    conv = load(conv_p)
    # Identify duplicate names also in SOXL probe or FUND
    primary_syms = set(s for s,_,_ in semis_map["soxl_probe"]) | set(s for s,_,_,_ in semis_map["fund_bofa"])
    # SOXL probe is the beta stack vehicle — NVDA/TSM in convexity duplicate factor beta
    dup_syms = {"NVDA", "TSM", "AVGO", "AMD", "SOXL", "SMH", "SOXX"}
    holds = holdings_list(conv)
    new_holds = []
    closed_notionals = 0.0
    changed = False
    is_dict = isinstance(conv.get("holdings") or conv.get("positions"), dict)
    for h in holds:
        sym = str(h.get("symbol") or h.get("ticker") or "").upper()
        units = fnum(h.get("units", h.get("qty", h.get("quantity"))))
        mv = fnum(h.get("market_value_usd", h.get("value_usd", h.get("mv_usd"))))
        mark = fnum(h.get("mark", h.get("price", h.get("last"))))
        if abs(mv) < 1e-9 and mark and units:
            mv = units * mark
        if sym in dup_syms and abs(units) > 1e-12:
            # Close this convexity risk — liquidate to cash in-sleeve (do not transfer unless residual policy)
            cash = get_cash(conv) + mv
            set_cash(conv, cash)
            ensure_events(conv).append({
                "type": "liquidation",
                "utc": UTC,
                "symbol": sym,
                "units": units,
                "usd": round(mv, 2),
                "note": (
                    "PAPER sweep: one beta stack — close convexity semis duplicate; "
                    "primary stack = SOXL probe + FUND BofA. Opportunistic beta labeled on survivors."
                ),
                "repair": str(RB),
            })
            closed_notionals += abs(mv)
            changed = True
            actions.append(f"CONV close duplicate {sym} mv=${mv:.2f}")
            # drop from holdings
            continue
        new_holds.append(h)
    if changed:
        # write holdings back
        if is_dict:
            key = "holdings" if "holdings" in conv else ("positions" if "positions" in conv else "holdings")
            conv[key] = {str(h.get("symbol") or h.get("ticker")): h for h in new_holds}
        else:
            key = "holdings" if "holdings" in conv else ("positions" if "positions" in conv else "holdings")
            conv[key] = new_holds
        # recompute equity ≈ cash + remaining MV
        rem_mv = 0.0
        for h in new_holds:
            mv = fnum(h.get("market_value_usd", h.get("value_usd", h.get("mv_usd"))))
            mark = fnum(h.get("mark", h.get("price", h.get("last"))))
            units = fnum(h.get("units", h.get("qty", h.get("quantity"))))
            if abs(mv) < 1e-9 and mark and units:
                mv = units * mark
            rem_mv += mv
        set_equity(conv, get_cash(conv) + rem_mv)
        conv["beta_stack_role"] = "non_primary_semis_cleared"
        conv["sweep_note"] = (
            f"PAPER sweep {UTC}: closed duplicate semis vs SOXL probe / FUND BofA (one beta stack). "
            f"Closed notional ~${closed_notionals:.2f}. Rollback {RB.name}"
        )
        # 2-5% rails
        conv.setdefault("clip_rules", {})
        if isinstance(conv["clip_rules"], dict):
            conv["clip_rules"].setdefault("min_pct", 2.0)
            conv["clip_rules"].setdefault("max_pct", 5.0)
            conv["clip_rules"]["note"] = "2-5% rails; no sleeve past +5% unbanked"
        # bank rule
        conv["max_unbanked_pct"] = 5.0
        atomic_write(conv_p, conv)
        actions.append(f"CONV semis duplicates closed notional~${closed_notionals:.2f}")
    else:
        actions.append("CONV no duplicate semis to close (or already clear)")

# Label SOXL probe + FUND as primary / opportunistic beta + rails
for p, role in ((soxl_p, "primary_semis_beta_stack"), (fund_p, "primary_bofa_semis_theme")):
    if not p: continue
    b = load(p)
    backup_once(p)
    b["beta_stack_role"] = role
    b["label"] = b.get("label") or ""
    if "opportunistic beta" not in str(b.get("label")).lower():
        b["opportunistic_beta"] = True
        b["beta_label"] = "opportunistic beta"
    b.setdefault("clip_rules", {})
    if isinstance(b["clip_rules"], dict):
        b["clip_rules"]["min_pct"] = 2.0
        b["clip_rules"]["max_pct"] = 5.0
        b["clip_rules"]["note"] = "2-5% rails on open sleeves; bank by +5%"
    b["max_unbanked_pct"] = 5.0
    # Soft seed rail check — do not invent deploys
    b["sweep_note"] = (
        f"PAPER sweep {UTC}: labeled {role}; opportunistic beta; 2-5% clip rails. "
        f"One beta stack with peer. Rollback {RB.name}"
    )
    atomic_write(p, b)
    actions.append(f"LABEL {p.name} role={role} clip_rules 2-5%")

# ---------------------------------------------------------------------------
# 4) Tournament babysit — clip_rules 2-5% + deploy_basis_usd for crypto holdings
# ---------------------------------------------------------------------------
print("==== 4) tournament babysit ====")
tour_paths = set()
for pat in ("*tournament*", "*weights*", "*tour_*"):
    for p in (DESK/"reports").glob(f"{pat}.json"):
        if any(x in str(p) for x in ("rollback", "repair", "backup")):
            continue
        tour_paths.add(p)
# registry keys
for b in registry_books:
    key = (b.get("key") or "").lower()
    if "tournament" in key or "weights" in key:
        if b.get("path"):
            tour_paths.add(Path(b["path"]))

CRYPTO_HINTS = ("USD", "BTC", "ETH", "SOL", "XBT", "UNI", "ARB", "NEAR", "AVAX", "INJ", "DOGE", "PEPE", "LINK", "ADA", "DOT")

def is_crypto_sym(sym):
    s = sym.upper()
    if "/" in s or s.endswith("USD") or s.endswith("USDT"):
        return True
    return any(h in s for h in CRYPTO_HINTS)

for p in sorted(tour_paths):
    try:
        d = load(p)
    except Exception as e:
        actions.append(f"SKIP tour read {p}: {e}")
        continue
    if not isinstance(d, dict):
        continue
    backup_once(p)
    holds = holdings_list(d)
    crypto_holds = []
    for h in holds:
        sym = str(h.get("symbol") or h.get("ticker") or "")
        units = fnum(h.get("units", h.get("qty", h.get("quantity"))))
        if abs(units) > 1e-12 and is_crypto_sym(sym):
            crypto_holds.append((sym, units))
    st = str(d.get("status") or d.get("book_status") or "").upper()
    eq = get_equity(d)
    cash = get_cash(d)
    flat_cash = (not crypto_holds) and abs(eq - cash) < 1.0 and not any(
        abs(fnum(h.get("units", h.get("qty", 0)))) > 1e-12 for h in holds
    )
    if flat_cash or st in ("CLOSED", "DEAD", "CASH"):
        # flat empty cash stubs can stay cash
        d.setdefault("status", st or "CASH")
        actions.append(f"TOUR {p.name}: flat/cash stub — leave")
        continue
    if crypto_holds or "tournament" in p.name.lower():
        # Ensure clip_rules + deploy_basis_usd
        cr = d.get("clip_rules")
        if not isinstance(cr, dict):
            cr = {}
        cr["min_pct"] = float(cr.get("min_pct") or 2.0)
        cr["max_pct"] = float(cr.get("max_pct") or 5.0)
        if cr["max_pct"] > 5.0:
            cr["max_pct"] = 5.0
        if cr["min_pct"] < 2.0:
            cr["min_pct"] = 2.0
        cr["note"] = "tournament babysit: 2-5% clips; no sleeve past +5% unbanked"
        d["clip_rules"] = cr
        d["max_unbanked_pct"] = 5.0
        if not d.get("deploy_basis_usd"):
            # set from seed or equity at deploy if present
            basis = fnum(d.get("deploy_basis_usd") or d.get("basis_usd") or d.get("since_deploy_basis_usd"))
            if basis <= 0:
                basis = get_seed(d) or get_equity(d) or get_cash(d)
            d["deploy_basis_usd"] = round(basis, 2)
        # If any sleeve past +5% unbanked — flag bank (do not invent bank fill without desk helper)
        unbanked = fnum(d.get("unbanked_pnl_usd") or d.get("unrealized_pnl_usd") or d.get("unrealised_pnl_usd"))
        basis = fnum(d.get("deploy_basis_usd")) or 1.0
        pct = 100.0 * unbanked / basis if basis else 0.0
        if pct > 5.0:
            d["bank_required"] = True
            d["bank_required_note"] = (
                f"unbanked {pct:.2f}% > 5% rail — operator must bank before new risk. Sweep {UTC}"
            )
            actions.append(f"TOUR {p.name}: BANK REQUIRED unbanked={pct:.2f}%")
        d["sweep_note"] = f"PAPER tournament babysit {UTC}: clip_rules 2-5%, deploy_basis_usd={d.get('deploy_basis_usd')}"
        atomic_write(p, d)
        actions.append(f"TOUR {p.name}: clip_rules set; crypto_holds={crypto_holds}; basis={d.get('deploy_basis_usd')}")

# ---------------------------------------------------------------------------
# 5) firm_risk.run()
# ---------------------------------------------------------------------------
print("==== 5) firm_risk.run() ====")
fr_out = None
fr_err = None
try:
    import firm_risk
    if hasattr(firm_risk, "run"):
        fr_out = firm_risk.run()
    elif hasattr(firm_risk, "main"):
        fr_out = firm_risk.main()
    else:
        fr_err = "no run/main on firm_risk"
except Exception as e:
    fr_err = repr(e)
    print("firm_risk_exc", e)

def dig_valid(obj, depth=0):
    if depth > 6: return None
    if isinstance(obj, dict):
        if "capital_accounting_valid" in obj:
            return obj.get("capital_accounting_valid"), obj
        for k,v in obj.items():
            r = dig_valid(v, depth+1)
            if r is not None: return r
    return None

valid = None
blob = None
if isinstance(fr_out, dict):
    valid = fr_out.get("capital_accounting_valid")
    blob = fr_out
    if valid is None:
        dug = dig_valid(fr_out)
        if dug: valid, blob = dug
elif fr_out is not None:
    dug = dig_valid(fr_out) if not isinstance(fr_out, (str, bytes)) else None
    if dug: valid, blob = dug

# Also read reports/firm_risk.json / state
for fp in (DESK/"reports/firm_risk.json", DESK/"reports/firm_risk_state.json"):
    if fp.exists():
        try:
            st = load(fp)
            if valid is None and "capital_accounting_valid" in st:
                valid = st.get("capital_accounting_valid")
                blob = st
            print("FILE", fp.name, "capital_accounting_valid=", st.get("capital_accounting_valid"),
                  "equity=", st.get("firm_equity_usd") or st.get("equity_usd") or st.get("firm_equity"))
        except Exception as e:
            print("read_fr_err", fp, e)

# Retry once if invalid — attempt to clear common clutter include flags already set
if valid is not True:
    print("capital_accounting_valid != True; retry firm_risk after clutter flags")
    try:
        import importlib, firm_risk
        importlib.reload(firm_risk)
        fr_out2 = firm_risk.run() if hasattr(firm_risk, "run") else None
        if isinstance(fr_out2, dict) and "capital_accounting_valid" in fr_out2:
            valid = fr_out2.get("capital_accounting_valid")
            blob = fr_out2
            fr_out = fr_out2
    except Exception as e:
        print("retry_err", e)

firm_equity = None
if isinstance(blob, dict):
    firm_equity = blob.get("firm_equity_usd") or blob.get("equity_usd") or blob.get("firm_equity")
errors_left = []
if isinstance(blob, dict):
    for k in ("errors", "capital_accounting_errors", "accounting_errors", "reasons"):
        if blob.get(k):
            errors_left.append({k: blob.get(k)})
    if valid is not True:
        errors_left.append({"capital_accounting_valid": valid})

receipt = {
    "utc": UTC,
    "local_note": AEST_NOTE,
    "paper_only": True,
    "rollback": str(RB),
    "actions": actions,
    "moved": moved,
    "zombie_liquidated": zombie_liquidated,
    "semis_map_summary": {
        "soxl": [(s,u) for s,u,_ in semis_map.get("soxl_probe") or []],
        "fund": [(s,u,t) for s,u,_,t in semis_map.get("fund_bofa") or []],
        "convexity": [(s,u) for s,u,_ in semis_map.get("convexity") or []],
    },
    "firm_risk_err": fr_err,
    "capital_accounting_valid": valid,
    "firm_equity_usd": firm_equity,
    "errors_left": errors_left,
    "fr_out_type": type(fr_out).__name__,
    "fr_out_preview": (str(fr_out)[:2000] if fr_out is not None else None),
}
(RB/"receipt.json").write_text(json.dumps(receipt, indent=2, default=str) + "\n")
# Also write desk report note
note_path = DESK/"reports/SWEEP_FIXES_2026-09-19.md"
note = f"""# SWEEP_FIXES_2026-09-19 (PAPER)

**When:** {AEST_NOTE} / {UTC}  
**Rollback:** `{RB}`  
**capital_accounting_valid:** `{valid}`  
**firm_equity_usd:** `{firm_equity}`

## $ moved (paired transfers)
"""
for m in moved:
    note += f"- **${m['usd']:.2f}** `{m['from']}` → `{m['to']}` — {m['note']}\n"
if not moved:
    note += "- (none)\n"
note += "\n## Actions\n"
for a in actions:
    note += f"- {a}\n"
note += "\n## Dead clutter\n"
note += "crypto_rumour / punt_24h (and equity≈0 DEAD/LIQUIDATED/FROZEN) marked CLOSED/DEAD; files kept for coverage; `include_in_firm_equity=false` where set.\n"
note += "\n## Semis / one beta stack\n"
note += "Primary: SOXL weekend probe + FUND BofA semis theme (opportunistic beta, 2–5% rails). Convexity duplicate semis (NVDA/TSM/etc) closed into sleeve cash when present.\n"
note += "\n## Tournament\n"
note += "Open tournament/weights books with crypto: `clip_rules` 2–5% + `deploy_basis_usd`; flat cash stubs left as cash; bank_required if unbanked >5%.\n"
note += "\n## Remaining errors\n"
note += f"```\n{json.dumps(errors_left, indent=2, default=str)}\n```\n"
note += f"\nfirm_risk_err: `{fr_err}`\n"
note_path.write_text(note)
print("WROTE", note_path)
print("RECEIPT", json.dumps(receipt, indent=2, default=str)[:4000])
print("DONE capital_accounting_valid=", valid, "firm_equity=", firm_equity)
if valid is not True:
    raise SystemExit(2)
PY

echo "EXIT=$?"
