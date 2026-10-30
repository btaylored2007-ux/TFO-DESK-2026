#!/usr/bin/env python3
"""fundamental_open_new.py — open ONE new position in the TFO Fundamental Desk.

PAPER ONLY. No broker, no order, no credential.

WHY A SEPARATE FILE FROM `fundamental_book.py --open`
-----------------------------------------------------
`--open` builds the whole book once from a sealed screen artefact and then
refuses to run again, because re-opening would overwrite frozen entry prices and
destroy the forward record. This file adds a SINGLE position from a later hunt,
against the cash the book actually has, without touching anything already open.

EVERY GUARD HERE EXISTS BECAUSE SOMETHING WENT WRONG ONCE
---------------------------------------------------------
1. **Sizing is in USD, converted, or it does not open.** The first build of this
   desk computed `units = notional_usd / local_price`, which treats a yen price
   as dollars — Denso opened at 21 units for a claimed $40,000 position really
   worth about $260, and every non-USD name was wrong the same way. That is
   §3(c) of this desk's own data-hazard catalogue. If no FX rate can be had, the
   position DOES NOT OPEN. Do not work around it.
2. **Never spend cash the book does not have**, and never resize an existing
   position to make room.
3. **The short gross cap counts what is ALREADY open.** The registered book runs
   shorts at 30% gross and most of that allowance is already used; new shorts
   have to fit in what is left, which may be nothing.
4. **A thesis with no disconfirming condition is not a thesis.** The `--thesis`
   text must carry a KILLS IT clause, because every position in this book was
   opened with a pre-registered condition that would break it, and the catalyst
   watcher has nothing to raise without one.
5. **Refuse a duplicate.** Adding to an open position is a resize, not an entry,
   and this file does not resize.

Usage:
  python3 fundamental_open_new.py --symbol 7203.T --side long --notional 20000 \\
     --sector consumer \\
     --thesis "TSE capital plan filed 2026-08-xx, buyback 3% of cap. KILLS IT: \\
               plan withdrawn, or ROE below 8% at the Q3 print (Nov 2026)."
  ... --dry-run
"""
import argparse

import desk_lock
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
LEDGER = os.path.join(BASE, "reports", "fundamental_book.json")
ENTRY_LOG = os.path.join(BASE, "reports", "fundamental_new_entries.jsonl")
ENTRY_HOLDS = os.path.join(BASE, "research_controls", "fundamental_entry_holds.json")

NAME_CAP_PCT = 0.08
SECTOR_CAP_PCT = 0.20
NON_USD_BLOC_CAP_PCT = 0.20
SHORT_GROSS_CAP_PCT = 0.30
REGISTERED_SECTORS = frozenset({
    "communication", "consumer", "crypto", "energy", "financials",
    "healthcare", "industrials", "materials", "real estate", "technology",
    "utilities",
})
RESEARCH_FEATURES = frozenset({
    "event_queue", "filing_diff", "insurer_v1", "bank_v1",
    "investment_entity_v1", "regulated_utility_v1",
    "holdco_dualclass_v1", "resources_v1",
})
QUEUE_SCHEMA = "tfo.discovery.session-queue.v1"
EXECUTION_MANIFEST_SCHEMA = "tfo.discovery.execution-manifest.v1"
QUEUE_ONLY_FIELDS = {
    "source_candidate_sha256", "state", "queued_utc", "last_check",
}


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json_sha256(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hex64(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-fA-F]{64}", value))


def _valid_stable_issuer_id(value):
    if not isinstance(value, str):
        return False
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{1,127}", value):
        return True
    # Completed general-research blocks also retain the sourced legal name
    # beside a LEI. Accept that exact notation only for a checksum-valid LEI;
    # the original string must still match the bound research candidate. The
    # checksum checks syntax, not the issuer's identity or current LEI status.
    match = re.fullmatch(
        r"([A-Z0-9]{18}[0-9]{2}) \(GLEIF LEI, legal name ([^()\r\n]{2,200})\)",
        value,
    )
    if not match or not match[2].strip():
        return False
    digits = "".join(char if char.isdigit() else str(ord(char) - 55)
                     for char in match[1])
    return int(digits) % 97 == 1


def _check_research_admission_hold(research_manifest_sha256, symbol):
    """Apply source-backed holds to an exact immutable research batch only."""
    try:
        with open(ENTRY_HOLDS, encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("research admission holds are unreadable: %s" % exc) from exc
    if (not isinstance(payload, dict)
            or payload.get("schema") != "tfo.fundamental.entry-holds.v1"
            or payload.get("paper_only") is not True
            or not isinstance(payload.get("holds"), list)):
        raise ValueError("research admission holds have an invalid schema")
    matched = []
    for hold in payload["holds"]:
        if (not isinstance(hold, dict)
                or not _hex64(hold.get("research_manifest_sha256"))
                or not isinstance(hold.get("symbol"), str) or not hold["symbol"].strip()
                or not isinstance(hold.get("reason"), str) or not hold["reason"].strip()
                or not isinstance(hold.get("evidence_urls"), list)
                or not hold["evidence_urls"]
                or not all(isinstance(url, str) and re.fullmatch(r"https?://[^\s]+", url)
                           for url in hold["evidence_urls"])):
            raise ValueError("research admission holds contain a malformed row")
        if (hold["research_manifest_sha256"].lower() == research_manifest_sha256.lower()
                and hold["symbol"] == symbol):
            matched.append(hold["reason"])
    if matched:
        raise ValueError("research admission hold for %s: %s" % (symbol, "; ".join(matched)))


def _queue_binding_payload(queue):
    return {
        "schema": queue.get("schema"),
        "paper_only": queue.get("paper_only"),
        "as_of": queue.get("as_of"),
        "batch_id": queue.get("batch_id"),
        "batch_sha256": queue.get("batch_sha256"),
        "research_manifest_sha256": queue.get("research_manifest_sha256"),
        "allocator_receipt_sha256": queue.get("allocator_receipt_sha256"),
        "source_execution_manifest_sha256": queue.get("source_execution_manifest_sha256"),
        "candidate_source_sha256s": [
            row.get("source_candidate_sha256")
            for row in queue.get("candidates") or []
            if isinstance(row, dict)
        ],
    }


def _load_bound_ready_candidate(*, queue_path, expected_queue_sha256,
                                expected_manifest_sha256, symbol, side,
                                notional, sector, catalyst_date,
                                stable_issuer_id, features, thesis):
    if not isinstance(queue_path, str) or not queue_path.strip():
        raise ValueError("queue receipt path is required")
    if not _hex64(expected_queue_sha256):
        raise ValueError("queue receipt SHA256 must be exactly 64 hex characters")
    path = os.path.abspath(queue_path)
    if _file_sha256(path).lower() != expected_queue_sha256.lower():
        raise ValueError("queue receipt file hash mismatch")
    try:
        with open(path, encoding="utf-8") as handle:
            queue = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("queue receipt is unreadable: %s" % exc) from exc
    # Dynamic import avoids the module-import cycle: session_queue imports this
    # opener to call the fresh market gate.  At execution time both modules are
    # fully initialized, so the queue can rerun its authoritative manifest and
    # feature-receipt recomputation here instead of trusting self-consistent
    # caller-supplied hashes.
    try:
        import fundamental_session_queue as session_queue
        session_queue.validate_queue_for_open(queue)
    except (ImportError, ValueError, OSError, json.JSONDecodeError) as exc:
        raise ValueError("queue authenticity validation failed: %s" % exc) from exc
    today = dt.date.today().isoformat()
    if not isinstance(queue, dict) or queue.get("schema") != QUEUE_SCHEMA:
        raise ValueError("queue receipt schema is not strict/current")
    if queue.get("status") != "complete" or queue.get("paper_only") is not True:
        raise ValueError("queue receipt is not complete PAPER-only evidence")
    if queue.get("as_of") != today:
        raise ValueError("queue receipt is stale")
    try:
        verified_date = dt.datetime.fromisoformat(
            str(queue.get("verified_utc") or "").replace("Z", "+00:00")
        ).astimezone().date().isoformat()
    except ValueError as exc:
        raise ValueError("queue receipt lacks a verification timestamp") from exc
    if verified_date != today:
        raise ValueError("queue verification is stale")
    for field in ("batch_sha256", "research_manifest_sha256",
                  "allocator_receipt_sha256", "source_execution_manifest_sha256",
                  "queue_receipt_sha256"):
        if not _hex64(queue.get(field)):
            raise ValueError("queue receipt lacks valid %s" % field)
    if queue["source_execution_manifest_sha256"].lower() != expected_manifest_sha256.lower():
        raise ValueError("manifest SHA256 does not match queue binding")
    manifest = queue.get("execution_manifest")
    if not isinstance(manifest, dict) or manifest.get("schema") != EXECUTION_MANIFEST_SCHEMA:
        raise ValueError("queue lacks a strict execution-manifest binding")
    if manifest.get("status") != "complete" or manifest.get("paper_only") is not True:
        raise ValueError("bound execution manifest is not complete PAPER-only evidence")
    if manifest.get("as_of") != today or _json_sha256(manifest) != queue["source_execution_manifest_sha256"]:
        raise ValueError("bound execution manifest is stale or modified")
    for field in ("batch_id", "batch_sha256", "research_manifest_sha256",
                  "allocator_receipt_sha256"):
        if queue.get(field) != manifest.get(field):
            raise ValueError("queue/manifest %s binding mismatch" % field)
    for field in ("research_manifest_file", "allocator_receipt_file"):
        meta = manifest.get(field)
        if not isinstance(meta, dict) or not _hex64(meta.get("sha256")):
            raise ValueError("bound execution manifest lacks %s" % field)
        bound_path = str(meta.get("path") or "")
        if not os.path.isabs(bound_path) or _file_sha256(bound_path) != meta["sha256"]:
            raise ValueError("bound execution manifest source file changed")
    candidates = queue.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("queue candidates is not a list")
    source_candidates = []
    for row in candidates:
        if not isinstance(row, dict) or not _hex64(row.get("source_candidate_sha256")):
            raise ValueError("queued candidate lacks immutable provenance")
        source = {key: value for key, value in row.items() if key not in QUEUE_ONLY_FIELDS}
        if _json_sha256(source) != row["source_candidate_sha256"]:
            raise ValueError("queued candidate was modified after admission")
        source_candidates.append(source)
    if source_candidates != manifest.get("candidates"):
        raise ValueError("queued candidate set differs from verified execution manifest")
    if queue["queue_receipt_sha256"] != _json_sha256(_queue_binding_payload(queue)):
        raise ValueError("queue receipt candidate-set binding mismatch")

    matches = [row for row in candidates if str(row.get("execution_symbol") or "") == symbol]
    if len(matches) != 1:
        raise ValueError("queue must contain exactly one row for the execution symbol")
    row = matches[0]
    if row.get("state") != "READY_FOR_DRY_RUN":
        raise ValueError("queue row is not READY_FOR_DRY_RUN")
    if side != "long" or row.get("economic_direction") != "LONG":
        raise ValueError("queue row is not the exact LONG expression")
    try:
        queued_notional = float(row.get("notional_usd"))
    except (TypeError, ValueError) as exc:
        raise ValueError("queue row notional is invalid") from exc
    if not math.isclose(float(notional), queued_notional, abs_tol=0.005):
        raise ValueError("queue row notional does not match opener")
    exact = {
        "sector": sector,
        "catalyst_date": catalyst_date,
        "stable_issuer_id": stable_issuer_id,
        "thesis": thesis,
    }
    for field, expected in exact.items():
        if row.get(field) != expected:
            raise ValueError("queue row %s does not match opener" % field)
    if row.get("symbol") != symbol or row.get("execution_symbol") != symbol:
        raise ValueError("queue row symbol does not exactly match opener")
    if row.get("research_features") != features:
        raise ValueError("queue row research_features do not exactly match opener")
    _check_research_admission_hold(queue["research_manifest_sha256"], symbol)
    check = row.get("last_check")
    if not isinstance(check, dict) or not isinstance(check.get("price"), dict) or not isinstance(
            check.get("liquidity_receipt"), dict):
        raise ValueError("READY queue row lacks fresh gate outputs")
    if check["liquidity_receipt"].get("market_state") != "REGULAR":
        raise ValueError("READY queue row lacks a REGULAR-session gate receipt")
    return {
        "features": features,
        "manifest_sha256": expected_manifest_sha256.lower(),
        "queue_receipt_sha256": expected_queue_sha256.lower(),
        "queue_receipt_path": path,
        "stable_issuer_id": stable_issuer_id,
        "batch_sha256": queue["batch_sha256"],
        "research_manifest_sha256": queue["research_manifest_sha256"],
        "allocator_receipt_sha256": queue["allocator_receipt_sha256"],
    }


def registered_capital_usd(ledger):
    """Return the ledger's registered capital, never a task-text fallback."""
    values = {}
    for key in ("seed_usd", "contributed_capital_usd"):
        if key not in ledger or isinstance(ledger[key], bool):
            raise ValueError("missing %s" % key)
        try:
            values[key] = float(ledger[key])
        except (TypeError, ValueError):
            raise ValueError("invalid %s" % key)
        if not math.isfinite(values[key]) or values[key] <= 0:
            raise ValueError("invalid %s" % key)
    if abs(values["seed_usd"] - values["contributed_capital_usd"]) > 0.01:
        raise ValueError("seed_usd and contributed_capital_usd disagree")
    return values["seed_usd"]


def quote_currency_bloc(ccy):
    """Map an actual quote currency to its major-currency risk bloc."""
    if not isinstance(ccy, str) or not ccy.strip():
        return None
    raw = ccy.strip()
    minor = {"GBp": "GBP", "GBX": "GBP", "ILA": "ILS", "ILX": "ILS",
             "ZAc": "ZAR", "ZAX": "ZAR"}
    if raw in minor:
        return minor[raw]
    code = raw.upper()
    if len(code) != 3 or not code.isalpha() or code in ("UNK", "XXX"):
        return None
    return code


def trusted_sector(value):
    """Return a registered canonical sector, never a prose/default bucket."""
    if not isinstance(value, str):
        return None
    sector = value.strip().lower()
    return sector if sector in REGISTERED_SECTORS else None


def sector_exposure_from_positions(positions):
    """Return gross by sector, refusing any open position without metadata."""
    if not isinstance(positions, dict):
        raise ValueError("positions is not an object")
    exposure, unknown = {}, []
    for symbol, position in positions.items():
        if not isinstance(position, dict):
            raise ValueError("position %s is not an object" % symbol)
        try:
            notional = float(position.get("notional_usd"))
        except (TypeError, ValueError):
            raise ValueError("position %s has invalid notional_usd" % symbol)
        if not math.isfinite(notional) or notional < 0:
            raise ValueError("position %s has invalid notional_usd" % symbol)
        if notional == 0:
            continue
        sector = trusted_sector(position.get("sector"))
        if sector is None:
            from tfo_sector_evidence import repaired_sectors
            sectors = repaired_sectors(symbol, REGISTERED_SECTORS)
            if not sectors:
                unknown.append(symbol)
                continue
            # Mixed ETF: conservative upper bound charged to every sector,
            # not invented holdings weights or a new exempt ETF bucket.
            for bucket in sectors:
                exposure[bucket] = exposure.get(bucket, 0.0) + notional
            continue
        exposure[sector] = exposure.get(sector, 0.0) + notional
    if unknown:
        raise ValueError("open position sector unknown: %s" %
                         ", ".join(sorted(unknown)))
    return exposure


def verified_quote_currency(symbol, provider_ccy):
    """Resolve provider currency against the desk's suffix table, fail closed."""
    try:
        import fx
        resolved = fx.quote_currency(symbol, info_ccy=provider_ccy)
        if not resolved:
            return None
        # A major/minor disagreement changes price conversion by 100x for
        # pence, agorot, and cents.  Refuse it instead of accepting the shared
        # major-currency bloc while sizing from an incompatible quote unit.
        if provider_ccy:
            if fx.major_and_scale(provider_ccy) != fx.major_and_scale(resolved):
                return None
        return resolved
    except Exception:                                      # noqa: BLE001
        return None


def live_entry_gate(symbol, side, notional, book_module):
    """Re-run the authoritative tradability gate in a live session.

    Research-stage liquidity results are discovery evidence, not an entry
    ticket.  Quotes, volume and venue state can all change between the hunt and
    the scheduled entry cycle, so this check deliberately fetches them again.
    Returns (price, receipt, refusal); it never raises or silently falls back to
    a stored/previous close. The fill price and gate intentionally use the same
    provider snapshot.
    """
    try:
        import fundamental_liquidity as lq
        import yfinance as yf
        info = yf.Ticker(symbol).info or {}
    except Exception as exc:                              # noqa: BLE001
        return None, None, ("live entry measurement failed (%s: %s)"
                            % (type(exc).__name__, str(exc)[:120]))
    if not info:
        return None, None, "live entry measurement returned an empty record"

    market_state = info.get("marketState")
    if market_state != "REGULAR":
        return None, None, ("venue is not in a REGULAR session (marketState=%s); "
                            "a scheduled run or previous close is not an "
                            "executable entry" % market_state)

    provider_ccy = info.get("currency")
    live_ccy = verified_quote_currency(symbol, provider_ccy)
    if live_ccy is None:
        return None, None, ("live quote currency is not trustworthy/consistent "
                            "(%s)" % provider_ccy)
    try:
        fx_usd = book_module.fx_to_usd(live_ccy)
    except Exception as exc:                              # noqa: BLE001
        return None, None, "live FX lookup failed (%s)" % exc
    if not fx_usd:
        return None, None, "no live FX conversion for %s" % live_ccy
    px = info.get("currentPrice") or info.get("regularMarketPrice")
    try:
        px = float(px)
    except (TypeError, ValueError):
        px = None
    if not px or not math.isfinite(px) or px <= 0:
        return None, None, ("no positive live current/regular-market price; "
                            "previousClose is never an entry fill")
    price = {"px": px, "ccy": live_ccy, "fx_usd": fx_usd,
             "px_usd": px * fx_usd}
    # PAPER accepts delayed bars, but never an undated metadata price.
    import time
    from paper_execution_price import eligible, receipt
    try:
        source_epoch = float(info['regularMarketTime'])
        age = time.time() - source_epoch
        if not math.isfinite(age) or age < 0 or age > 1200:
            raise ValueError('price outside the 20-minute PAPER window')
        stamp = dt.datetime.fromtimestamp(source_epoch, dt.timezone.utc).isoformat()
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return None, None, 'Timestamped PAPER entry price required: '+str(exc)
    price.update(quote_utc=stamp, fetched_at=_utc(), paper_price_class='CURRENT PAPER')
    if age > 60:
        try:
            quotes, failures = book_module.fetch_prices([symbol])
            observed = quotes.get(symbol)
            if not eligible(observed) or observed.get('ccy') != live_ccy:
                return None, None, 'No eligible delayed regular-session PAPER bar: '+str(failures)
            price = observed
            fx_usd = price['fx_usd']
        except Exception as exc:
            return None, None, 'Delayed PAPER bar validation failed: '+str(exc)

    try:
        result = lq.assess(info, notional, side, fx_usd=fx_usd)
    except Exception as exc:                              # noqa: BLE001
        return None, None, ("authoritative liquidity gate failed to run (%s: %s)"
                            % (type(exc).__name__, str(exc)[:120]))
    if not result.get("ok"):
        reasons = result.get("reasons") or ["unspecified liquidity refusal"]
        return None, None, "; ".join(str(x) for x in reasons)

    measured = result.get("measured") or {}
    if measured.get("spread_pct") is None:
        return None, None, ("spread is unmeasured at entry; the research-stage "
                            "waiver requires an in-session remeasurement before "
                            "sizing")

    return price, {
        **receipt(price),
        "market_state": market_state,
        "provider_market_time": info.get("regularMarketTime"),
        "quote_currency": live_ccy,
        "measured": measured,
    }, None


def research_provenance(features=None, manifest_sha256=None,
                        stable_issuer_id=None, queue_receipt_path=None,
                        queue_receipt_sha256=None, *, symbol=None, side=None,
                        notional=None, sector=None, catalyst_date=None,
                        thesis=None):
    """Validate optional research provenance as one indivisible receipt.

    General research carries an explicit empty feature list, while attributed
    research names only the features actually used. Either bound entry must
    verify the entire queue/manifest/allocator chain and exact candidate. Older
    unbound callers may omit provenance entirely.
    """
    features = sorted(set(features or []))
    supplied = bool(features or manifest_sha256 or stable_issuer_id
                    or queue_receipt_path or queue_receipt_sha256)
    if not supplied:
        return None
    unknown = sorted(set(features) - RESEARCH_FEATURES)
    if unknown:
        raise ValueError("unknown research feature: %s" % ", ".join(unknown))
    if not isinstance(manifest_sha256, str) or not re.fullmatch(
            r"[0-9a-fA-F]{64}", manifest_sha256.strip()):
        raise ValueError("manifest_sha256 must be exactly 64 hex characters")
    if not _valid_stable_issuer_id(
            stable_issuer_id.strip() if isinstance(stable_issuer_id, str) else None):
        raise ValueError("stable_issuer_id is missing or malformed")
    return _load_bound_ready_candidate(
        queue_path=queue_receipt_path,
        expected_queue_sha256=queue_receipt_sha256,
        expected_manifest_sha256=manifest_sha256.strip(),
        symbol=symbol,
        side=side,
        notional=notional,
        sector=sector,
        catalyst_date=str(catalyst_date),
        stable_issuer_id=stable_issuer_id.strip(),
        features=features,
        thesis=thesis,
    )


def _entry_risk_allowed(ledger):
    """Existing book + firm policy, fail closed at the execution boundary."""
    try:
        import book_kill
        allowed, reason = book_kill.entries_allowed(
            ledger, "TFO Fundamental Desk", verbose=False)
        if allowed is not True:
            return False, str(reason or "book/firm entry risk refused")
        return True, "ok"
    except Exception as exc:                              # noqa: BLE001
        return False, "book/firm entry risk unavailable (%s: %s)" % (
            type(exc).__name__, str(exc)[:180])


def open_new(symbol, side, notional, thesis, catalyst_date, dry_run=False,
             sector=None, research_features=None, manifest_sha256=None,
             stable_issuer_id=None, queue_receipt_path=None,
             queue_receipt_sha256=None, sizing_policy="discretionary"):
    """Every caller shares the mark/entry lock, including imported API calls."""
    import v1_venues  # 2026-09-28 funder directive: V1 entry venues US/.T/.AX from 29 Sep Sydney
    v1_venues.require_entry_allowed(symbol)
    try:
        size = float(notional)
    except (TypeError, ValueError, OverflowError):
        size = float("nan")
    if isinstance(notional, bool) or not math.isfinite(size) or size <= 0:
        print("!! REFUSED: notional must be finite and positive")
        return 2
    import tfo_discretionary_sizing as sizing
    if sizing_policy not in ("discretionary", "registered"):
        print("!! REFUSED: unknown sizing policy")
        return 2
    with sizing.writer_scope(sizing_policy), desk_lock.ledger_lock("fundamental"):
        return _open_new_locked(
            symbol, side, size, thesis, catalyst_date, dry_run=dry_run,
            sector=sector, research_features=research_features,
            manifest_sha256=manifest_sha256, stable_issuer_id=stable_issuer_id,
            queue_receipt_path=queue_receipt_path,
            queue_receipt_sha256=queue_receipt_sha256, sizing_policy=sizing_policy)


def _open_new_locked(symbol, side, notional, thesis, catalyst_date, dry_run=False,
             sector=None, research_features=None, manifest_sha256=None,
             stable_issuer_id=None, queue_receipt_path=None,
             queue_receipt_sha256=None, sizing_policy="discretionary"):
    if side not in ("long", "short"):
        print("!! side must be long or short")
        return 2
    if notional <= 0:
        print("!! notional must be positive")
        return 2
    try:
        provenance = research_provenance(
            research_features, manifest_sha256, stable_issuer_id,
            queue_receipt_path, queue_receipt_sha256,
            symbol=symbol, side=side, notional=notional, sector=sector,
            catalyst_date=catalyst_date, thesis=thesis)
    except ValueError as exc:
        print("!! REFUSED: invalid research provenance (%s)" % exc)
        return 2
    candidate_sector = trusted_sector(sector)
    if candidate_sector is None:
        print("!! REFUSED: --sector must be one registered canonical sector: %s"
              % ", ".join(sorted(REGISTERED_SECTORS)))
        return 2
    # A DATE IS NOW MANDATORY. 47 of 60 positions were found on 2026-08-31 with
    # no sourced forward date, which made them permanently immune to the
    # catalyst-expiry rule registered three days earlier. A rule that reaches 13
    # of 60 positions is not a rule. The date must be REAL and SOURCED — the
    # backfill script refuses to invent one and so does this.
    import datetime as _dt
    try:
        _cd = _dt.date.fromisoformat(str(catalyst_date)[:10])
    except Exception:                                      # noqa: BLE001
        print("!! REFUSED: --catalyst-date %r is not an ISO date." % catalyst_date)
        return 2
    if _cd <= _dt.date.today():
        print("!! REFUSED: --catalyst-date %s is not in the future. The catalyst "
              "is what the position is FOR; one already past cannot justify "
              "opening it." % _cd)
        return 2
    catalyst_date = _cd.isoformat()

    if "KILLS IT" not in (thesis or "").upper():
        print("!! REFUSED: the thesis carries no KILLS IT condition.")
        print("   Every position in this book was opened with a pre-registered")
        print("   disconfirming condition. Without one the catalyst watcher has")
        print("   nothing to raise and the position can never be honestly reviewed.")
        return 2

    try:
        with open(LEDGER) as f:
            led = json.load(f)
    except OSError as e:
        print("!! ledger unreadable (%s) — refusing to act blind" % e)
        return 1

    allowed, reason = _entry_risk_allowed(led)
    if not allowed:
        print("!! REFUSED: %s. Existing positions and exits are unaffected." % reason)
        return 2

    try:
        seed = registered_capital_usd(led)
        cash = float(led["cash"])
        if not math.isfinite(cash) or cash < 0:
            raise ValueError("invalid cash")
        sector_used = sector_exposure_from_positions(led.get("positions"))
    except (KeyError, TypeError, ValueError) as e:
        print("!! ledger capital contract invalid (%s) — refusing to use a "
              "task-text fallback" % e)
        return 1

    sector_cap = seed * SECTOR_CAP_PCT
    existing_sector = sector_used.get(candidate_sector, 0.0)
    if existing_sector + notional > sector_cap + 1e-6:
        print("!! REFUSED: %s sector gross would be $%s against a %.0f%% cap "
              "of $%s. Longs and shorts count together."
              % (candidate_sector, "{:,.0f}".format(existing_sector + notional),
                 SECTOR_CAP_PCT * 100, "{:,.0f}".format(sector_cap)))
        print("   Already open: $%s. Headroom: $%s."
              % ("{:,.0f}".format(existing_sector),
                 "{:,.0f}".format(max(0.0, sector_cap - existing_sector))))
        return 2

    if symbol in (led.get("positions") or {}):
        print("!! %s is already open. This file opens, it does not resize." % symbol)
        return 2

    sizing_evidence = {"policy": sizing_policy}
    if sizing_policy == "discretionary":
        try:
            import tfo_discretionary_sizing as sizing
            sizing_evidence = sizing.admit(symbol, notional)
        except (ValueError, OSError, RuntimeError) as exc:
            print("!! REFUSED: discretionary sizing: %s" % exc)
            return 2
    name_cap = seed * NAME_CAP_PCT
    if sizing_policy == "registered" and notional > name_cap + 1e-6:
        print("!! REFUSED: $%s exceeds the %.0f%% per-name cap of $%s"
              % ("{:,.0f}".format(notional), NAME_CAP_PCT * 100,
                 "{:,.0f}".format(name_cap)))
        return 2
    if notional > cash + 1e-6:
        print("!! REFUSED: $%s exceeds available cash of $%s. Nothing already open "
              "will be resized to make room." % ("{:,.0f}".format(notional),
                                                 "{:,.2f}".format(cash)))
        return 2

    if side == "short":
        open_short = sum(p.get("notional_usd", 0.0)
                         for p in (led.get("positions") or {}).values()
                         if p.get("side") == "short")
        cap = seed * SHORT_GROSS_CAP_PCT
        if open_short + notional > cap + 1e-6:
            print("!! REFUSED: short gross would be $%s against a %.0f%% cap of $%s."
                  % ("{:,.0f}".format(open_short + notional),
                     SHORT_GROSS_CAP_PCT * 100, "{:,.0f}".format(cap)))
            print("   Already open: $%s. Headroom: $%s."
                  % ("{:,.0f}".format(open_short),
                     "{:,.0f}".format(max(0.0, cap - open_short))))
            return 2

    try:
        import fundamental_book as fb
    except Exception as e:                      # noqa: BLE001
        print("!! cannot import the book engine (%s) — no FX conversion available, "
              "so refusing to size. See guard 1." % e)
        return 1

    p, entry_liquidity, entry_refusal = live_entry_gate(
        symbol, side, notional, fb)
    if entry_refusal:
        print("!! NOT OPENED — live entry gate refused %s: %s"
              % (symbol, entry_refusal))
        data_failure = entry_refusal.startswith((
            "live entry measurement", "live quote currency", "live FX lookup",
            "no live FX conversion", "no positive live"))
        return 1 if data_failure else 2

    quote_ccy = verified_quote_currency(symbol, p.get("ccy"))
    candidate_bloc = quote_currency_bloc(quote_ccy)
    if candidate_bloc is None:
        print("!! NOT OPENED — %s has no trustworthy actual quote currency; "
              "the currency-bloc cap cannot be checked." % symbol)
        return 1

    if candidate_bloc == "USD":   # tfo_usbook_cap_20261030 (Brett 19:35 WITA): main-book US beta cap (40% main-book equity), new adds only
        import us_book_cap
        if us_book_cap.is_us_equity(symbol):
            _uok, _uwhy, _ = us_book_cap.check(symbol, size, side, book="fundamental_open_new", accumulate=False)
            if not _uok:
                print("!! NOT OPENED — %s" % _uwhy)
                return 2
    if candidate_bloc != "USD":
        bloc_open = 0.0
        for held_symbol, held in (led.get("positions") or {}).items():
            try:
                held_notional = float(held.get("notional_usd"))
            except (AttributeError, TypeError, ValueError):
                print("!! REFUSED: %s has invalid notional_usd; currency-bloc "
                      "headroom cannot be verified." % held_symbol)
                return 1
            held_bloc = quote_currency_bloc(held.get("entry_ccy"))
            if held_notional > 0 and held_bloc is None:
                print("!! REFUSED: %s has no trustworthy entry_ccy; currency-bloc "
                      "headroom cannot be verified." % held_symbol)
                return 1
            if held_bloc == candidate_bloc:
                bloc_open += held_notional
        bloc_cap = seed * NON_USD_BLOC_CAP_PCT
        if bloc_open + notional > bloc_cap + 1e-6:
            print("!! REFUSED: %s currency-bloc gross would be $%s against a "
                  "%.0f%% cap of $%s."
                  % (candidate_bloc, "{:,.0f}".format(bloc_open + notional),
                     NON_USD_BLOC_CAP_PCT * 100, "{:,.0f}".format(bloc_cap)))
            print("   Already open: $%s. Headroom: $%s."
                  % ("{:,.0f}".format(bloc_open),
                     "{:,.0f}".format(max(0.0, bloc_cap - bloc_open))))
            return 2

    units = notional / p["px_usd"]
    # Quotes/research verification can take time. Re-read existing book+firm risk
    # immediately before the dry-run success or commit, still under the ledger
    # lock. A halt arriving during the quote fetch must prevent the new entry.
    allowed, reason = _entry_risk_allowed(led)
    if not allowed:
        print("!! REFUSED at execution boundary: %s. Nothing was opened." % reason)
        return 2
    if sizing_policy == "discretionary":
        try:
            sizing_evidence = sizing.admit(symbol, notional)
        except (ValueError, OSError, RuntimeError) as exc:
            print("!! REFUSED at execution boundary: %s" % exc)
            return 2
    print("=" * 74)
    print("NEW ENTRY%s — %s %s" % (" [DRY RUN]" if dry_run else "", side.upper(), symbol))
    print("=" * 74)
    print("  quote        %.4f %s   fx %.6f -> $%.4f USD"
          % (p["px"], p["ccy"], p["fx_usd"], p["px_usd"]))
    print("  notional     $%s   units %.4f" % ("{:,.2f}".format(notional), units))
    _m = entry_liquidity["measured"]
    print("  liquidity    REGULAR · spread %.3f%% · ADV $%s · position %.3f%% of ADV"
          % (_m["spread_pct"],
             "{:,.0f}".format(_m.get("adv_usd") or 0),
             (_m.get("position_pct_of_adv") or 0) * 100))
    print("  cash before  $%s   after $%s"
          % ("{:,.2f}".format(cash), "{:,.2f}".format(cash - notional)))
    print("  thesis       %s" % thesis[:200])
    if provenance:
        print("  research     %s · %s" %
              (",".join(provenance["features"]) or "legacy_general",
               provenance["manifest_sha256"][:12]))
    if dry_run:
        print("\n  DRY RUN — nothing was changed.")
        return 0

    position = {
        "side": side, "notional_usd": round(notional, 2),
        "entry_px": p["px"], "entry_ccy": quote_ccy,
        "entry_fx_usd": p["fx_usd"], "entry_px_usd": p["px_usd"],
        "units": units, "entry_utc": _utc(),
        "mark": p["px"], "mark_usd": p["px_usd"], "mark_fx_usd": p["fx_usd"],
        "mark_utc": p.get('quote_utc'), "mark_stale": False, "unrealised_usd": 0.0,
        "entry_price_evidence": {k: entry_liquidity.get(k) for k in ('price_class', 'price_source_utc', 'price_received_utc', 'price_model', 'live_execution')},
        "thesis": thesis, "catalyst_date": catalyst_date,
        "sector": candidate_sector,
        "entry_liquidity": entry_liquidity,
        "source": "later hunt (not the sealed 2026-08-27 screen)",
    }
    position["sizing_policy_evidence"] = sizing_evidence
    if provenance:
        position["research_provenance"] = dict(provenance)
    led["positions"][symbol] = position
    led["cash"] = round(cash - notional, 2)
    import desk_costs as _dc  # 2026-09-26 V1 costs HARD RULE: entry fee (same model as V2); equity identity nets costs_paid_usd
    _f = _dc.fill_fees(symbol, _dc.side_for("BUY", side), units, p["px_usd"], p["px"], quote_ccy)
    _fee = round(_f["fees_usd"], 2); position["entry_fees_usd"] = _fee; position["entry_fee_detail"] = _f["breakdown"]
    led["cash"] = round(led["cash"] - _fee, 2); led["costs_paid_usd"] = round(float(led.get("costs_paid_usd") or 0) + _fee, 4)

    rec = {"utc": _utc(), "symbol": symbol, "side": side,
           "notional_usd": round(notional, 2), "units": units,
           "entry_px": p["px"], "entry_ccy": quote_ccy, "fx_usd": p["fx_usd"],
           "entry_px_usd": p["px_usd"], "thesis": thesis,
           "catalyst_date": catalyst_date, "sector": candidate_sector,
           "entry_liquidity": entry_liquidity}
    rec["sizing_policy_evidence"] = sizing_evidence
    if provenance:
        rec["research_provenance"] = dict(provenance)
    led.setdefault("new_entries", []).insert(0, rec)

    tmp = LEDGER + ".tmp"
    with open(tmp, "w") as f:
        json.dump(led, f, indent=2, default=str)
    os.replace(tmp, LEDGER)
    try:
        with open(ENTRY_LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError as e:
        print("!! could not append the permanent entry log (%s)" % e)

    print("\n  OPENED. %d positions now. Cash $%s."
          % (len(led["positions"]), "{:,.2f}".format(led["cash"])))
    return 0


def main():
    # open_new owns the lock for both CLI and imported callers. Taking it here
    # again would deadlock because the advisory lock is intentionally nonreentrant.
    return _main_locked()


def _main_locked():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--side", required=True, choices=("long", "short"))
    ap.add_argument("--notional", required=True, type=float)
    ap.add_argument("--sizing-policy", choices=("discretionary", "registered"),
                    default="discretionary", help="registered retains the pre-existing experiment 8% cap")
    ap.add_argument("--sector", required=True, choices=sorted(REGISTERED_SECTORS),
                    help="canonical sector from the completed research block")
    ap.add_argument("--thesis", required=True,
                    help="dated reason AND a KILLS IT disconfirming condition")
    ap.add_argument("--catalyst-date", required=True,
                    help="ISO date the pre-registered KILLS IT actually resolves. "
                         "REQUIRED since 2026-08-31: a position without one is "
                         "IMMUNE to the catalyst-expiry rule and can never be "
                         "forced to a decision.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--research-feature", action="append", default=[],
                    choices=sorted(RESEARCH_FEATURES),
                    help="repeatable feature provenance for measured PAPER entries")
    ap.add_argument("--manifest-sha256",
                    help="strict execution-manifest hash; required with any provenance")
    ap.add_argument("--stable-issuer-id",
                    help="exact bound stable issuer ID; required with any provenance")
    ap.add_argument("--queue-receipt",
                    help="strict current session-queue JSON; required with any provenance")
    ap.add_argument("--queue-sha256",
                    help="expected SHA256 of --queue-receipt; required with any provenance")
    a = ap.parse_args()
    return open_new(a.symbol, a.side, a.notional, a.thesis,
                    a.catalyst_date, dry_run=a.dry_run, sector=a.sector,
                    research_features=a.research_feature,
                    manifest_sha256=a.manifest_sha256,
                    stable_issuer_id=a.stable_issuer_id,
                    queue_receipt_path=a.queue_receipt,
                    queue_receipt_sha256=a.queue_sha256, sizing_policy=a.sizing_policy)


if __name__ == "__main__":
    sys.exit(main())
