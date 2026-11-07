#!/usr/bin/env python3
"""Append-only, hash-chained PAPER audit trail.

    audit.log(event_type, desk, payload)

Each record is one JSON line in state/audit/audit_YYYYMM.jsonl (UTC month).
The line carries prev_hash (the previous record's hash, or 64 zeroes for the
genesis record) and hash, the sha256 of the canonical JSON of every other
field. A later month's first record links to the previous month's tip, so
deleting a file breaks the chain.

log() never raises. A contended lock is abandoned after a short wait so an
audit miss cannot stall a stop or an exit. Secrets (api keys, tokens,
passwords, bearer values) are redacted before the line is hashed. Under
pytest the live directory is not touched unless AUDIT_DIR is set.

The ledger files are never written by this module. It only appends the audit
log, and the verifier only reads it.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import sys
import time

GENESIS = "0" * 64
SCHEMA = 1
_LOCK_WAIT_S = 0.15
_MAX_STR = 2000
_MAX_PAYLOAD = 12000
_SECRET_KEY = re.compile(
    r"(?i)(?:^|[_\-\s])(?:api[_\-]?key|apikey|secret|token|password|passwd|"
    r"authorization|bearer|private[_\-]?key|access[_\-]?key|refresh[_\-]?token|"
    r"credential|signing[_\-]?key|webhook)(?:$|[_\-\s])"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{6,}")
_SK = re.compile(r"\b(?:sk|rk)-[A-Za-z0-9]{6,}")
_KV = re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd)\s*[=:]\s*\S+")
_XOX = re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{6,}")

_STOP = {"stop", "stopped", "stop_gap_through", "stop_filled", "stop_update"}
_FILL = {"filled", "rebooked_after_open", "fill"}
_ARM = {"armed", "gate_promote"}
_RISK = {
    "blocked", "resized", "rebook_rejected", "us_book_cap_block", "causal_reject",
    "gate_cancel", "risk_trim", "us_book_cap_check", "arm_failed", "arm_lost",
    "shadow", "missed", "exit_deferred",
}
_ORDER = {
    "cancelled", "expired", "rebook_unfilled_beyond_limit", "close_order",
    "close_cancelled", "entry_order", "cover_requested",
}
_EXIT = {
    "target", "giveback", "rail_stop", "rail_time_stop", "target1_scale_out",
    "rail_2r_scale_out", "dex_2session_halfcut", "t1", "flat_3r", "thesis_exit",
    "time_stop", "time_exit", "eod_flat", "closed", "close_fill", "discretionary_cover",
}


def _root():
    override = os.environ.get("AUDIT_DIR")
    if override:
        return override
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "state", "audit")


def _now(ts=None):
    if ts is None:
        return dt.datetime.now(dt.timezone.utc)
    if isinstance(ts, dt.datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=dt.timezone.utc)
        return ts.astimezone(dt.timezone.utc)
    return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def _month_path(root, when):
    return os.path.join(root, "audit_%04d%02d.jsonl" % (when.year, when.month))


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(body):
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def _hash_of(rec):
    return _digest({k: v for k, v in rec.items() if k != "hash"})


def _scrub_text(text):
    if not isinstance(text, str):
        return text
    if len(text) > _MAX_STR:
        text = text[:_MAX_STR] + "…[truncated]"
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _SK.sub("[REDACTED]", text)
    text = _XOX.sub("[REDACTED]", text)
    text = _KV.sub(lambda m: m.group(1) + "=[REDACTED]", text)
    return text


def _redact(obj, depth=0):
    if depth > 6:
        return "[REDACTED-DEPTH]"
    if isinstance(obj, dict):
        out = {}
        for i, (k, v) in enumerate(obj.items()):
            if i >= 40:
                out["_truncated_keys"] = len(obj) - 40
                break
            key = str(k)
            if _SECRET_KEY.search(key):
                out[key] = "[REDACTED]"
            else:
                out[key] = _redact(v, depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        items = [_redact(v, depth + 1) for v in list(obj)[:40]]
        if len(obj) > 40:
            items.append("[…%d more]" % (len(obj) - 40))
        return items
    if isinstance(obj, str):
        return _scrub_text(obj)
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    return _scrub_text(str(obj))


def _jsonable(obj):
    return json.loads(_canonical(obj))


def _lock_acquire(fh):
    deadline = time.monotonic() + _LOCK_WAIT_S
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.002)


def _last_record(path):
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size <= 0:
        return None
    with open(path, "rb") as fh:
        fh.seek(max(0, size - 65536))
        chunk = fh.read()
    if not chunk:
        return None
    parts = chunk.split(b"\n")
    line = parts[-2] if chunk.endswith(b"\n") and len(parts) >= 2 else parts[-1]
    if not line.strip():
        return None
    try:
        return json.loads(line.decode("utf-8"))
    except Exception:
        return None


def _previous_tip(root, when):
    """Hash that the first record of `when`'s month must point at."""
    name = "audit_%04d%02d.jsonl" % (when.year, when.month)
    try:
        names = sorted(n for n in os.listdir(root) if n.startswith("audit_") and n.endswith(".jsonl") and n < name)
    except OSError:
        return GENESIS
    for prev in reversed(names):
        rec = _last_record(os.path.join(root, prev))
        if rec and rec.get("hash"):
            return rec["hash"]
    return GENESIS


def _tip_hash(root, when):
    path = _month_path(root, when)
    rec = _last_record(path)
    if rec and rec.get("hash"):
        return rec["hash"], path
    return _previous_tip(root, when), path


def log(event_type, desk, payload=None, *, ts=None):
    """Append one audit record. Returns the new hash, or None if nothing was written.

    Never raises. Callers on a stop or exit path can call this after the
    economic booking; a full disk, a bad payload, or a busy lock drops the
    audit line and leaves the exit in place.
    """
    try:
        return _log(event_type, desk, payload, ts)
    except Exception:
        return None


def _log(event_type, desk, payload, ts):
    root = _root()
    if not root:
        return None
    when = _now(ts)
    os.makedirs(root, exist_ok=True)
    if payload is None:
        payload = {}
    elif not isinstance(payload, dict):
        payload = {"value": payload}
    payload = _jsonable(_redact(payload))
    raw = _canonical(payload)
    if len(raw) > _MAX_PAYLOAD:
        payload = {"_truncated": True, "preview": raw[:_MAX_PAYLOAD]}
    lock_path = os.path.join(root, ".lock")
    with open(lock_path, "a+") as lock:
        if not _lock_acquire(lock):
            return None
        try:
            prev, path = _tip_hash(root, when)
            body = {
                "v": SCHEMA,
                "ts": when.isoformat(),
                "event_type": str(event_type or "event")[:64],
                "desk": str(desk or "tfo")[:32],
                "payload": payload,
                "prev_hash": prev,
            }
            body["hash"] = _digest(body)
            line = (_canonical(body) + "\n").encode("utf-8")
            fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
            try:
                os.write(fd, line)
                os.fsync(fd)
            finally:
                os.close(fd)
            return body["hash"]
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def capital(desk, payload):
    """Capital event (prize, funding, transfer, withdrawal). Never raises."""
    return log("capital_event", desk, payload)


def deploy(source, tag="", files=None, note="", entry="", desk="tfo"):
    """Deploy / config / rule change. A RULE file or note also records rule_change."""
    files = [str(f) for f in (files or [])]
    payload = {
        "source": source,
        "tag": tag,
        "files": files,
        "note": str(note or "")[:2000],
        "entry": str(entry or "")[:4000],
        "paper_only": True,
    }
    log("deploy", desk, payload)
    blob = " ".join(files + [str(tag or ""), str(note or "")]).upper()
    if "RULE" in blob:
        log("rule_change", desk, payload)
    if "CONFIG" in blob or any("config" in f.lower() for f in files):
        log("config_change", desk, payload)
    return True


def classify(kind):
    """Map a desk's own event name onto an audit event_type, or None to skip."""
    k = str(kind or "").strip().lower()
    if not k or k.endswith("_dry"):
        return None
    if "refused" in k or k.endswith("_block") or k.endswith("_blocked"):
        return "risk_decision"
    if k in _ARM:
        return "armed_setup"
    if k in _FILL:
        return "fill"
    if k in _STOP or k.startswith("stop"):
        return "stop"
    if k in _RISK:
        return "risk_decision"
    if k in _ORDER:
        return "paper_order"
    if (k in _EXIT or "giveback" in k or "full_flat" in k or "backstop" in k
            or "scale_out" in k or "quarter_out" in k or "halfcut" in k or k.endswith("_exit")
            or k.startswith("closed")):
        return "exit"
    return None


def book_events(desk, events):
    """Record veteran/THF tick events. `events` is the tick's (id, kind, detail) list.

    An arm is both the working paper order and the armed setup. Never raises.
    """
    try:
        for item in events or ():
            if not item or len(item) < 2:
                continue
            ident, kind = item[0], item[1]
            detail = item[2] if len(item) > 2 else None
            et = classify(kind)
            if not et:
                continue
            payload = {"id": ident, "kind": kind, "detail": detail, "paper_only": True}
            if et == "armed_setup":
                log("paper_order", desk, payload)
            log(et, desk, payload)
    except Exception:
        return None


def from_kind(desk, kind, fields=None):
    """One named desk event (hunts, and anything else that already has a kind)."""
    try:
        et = classify(kind)
        if not et:
            return None
        src = fields or {}
        payload = {"kind": kind, "paper_only": True}
        for key in ("ticker", "tickers", "book", "why", "note", "action", "stage", "ok", "side", "entry", "rc", "phase"):
            if key in src and src[key] is not None:
                payload[key] = src[key]
        if et == "armed_setup":
            log("paper_order", desk, payload)
        return log(et, desk, payload)
    except Exception:
        return None


def from_oanda(rec):
    """Slim an OANDA sleeve log record into the chain. Skips dry-run lines."""
    try:
        if not isinstance(rec, dict):
            return None
        if rec.get("live") is False and str(rec.get("event") or "").endswith("_dry"):
            return None
        et = classify(rec.get("event"))
        if not et:
            return None
        payload = {"paper_only": True}
        for key in ("event", "instrument", "design", "units", "trade_id", "reason", "stop", "live", "price", "direction"):
            if key in rec and rec[key] is not None:
                payload[key] = rec[key]
        if et == "armed_setup":
            log("paper_order", "oanda", payload)
        return log(et, "oanda", payload)
    except Exception:
        return None


def _iter_files(root):
    try:
        names = sorted(n for n in os.listdir(root) if n.startswith("audit_") and n.endswith(".jsonl"))
    except OSError:
        return []
    return [os.path.join(root, n) for n in names]


def _read_status(root):
    path = os.path.join(root, "verify_status.json")
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return {}


def verify(root=None, *, prior_tip=None):
    """Walk every monthly file. Returns a dict with ok, records, tip, breaks.

    A break is a malformed line, a hash that does not match the record body,
    a prev_hash that does not match the previous record, or a previously
    witnessed tip that is no longer present (truncation or replacement).
    """
    root = root or _root()
    breaks = []
    records = 0
    hashes = []
    prev = GENESIS
    tip = None
    tip_ts = None
    if not root or not os.path.isdir(root):
        return {"ok": True, "records": 0, "tip": None, "tip_ts": None, "breaks": [], "files": []}
    files = _iter_files(root)
    if prior_tip is None:
        prior_tip = (_read_status(root) or {}).get("last_good_tip")
    for path in files:
        try:
            fh = open(path, "r", encoding="utf-8")
        except OSError as exc:
            breaks.append({"file": os.path.basename(path), "line": 0, "error": "unreadable: %s" % exc})
            continue
        with fh:
            for n, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                records += 1
                try:
                    rec = json.loads(line)
                except Exception:
                    breaks.append({"file": os.path.basename(path), "line": n, "error": "malformed json"})
                    if len(breaks) >= 20:
                        break
                    continue
                got = rec.get("hash")
                expect = _hash_of(rec)
                if got != expect:
                    breaks.append({
                        "file": os.path.basename(path), "line": n, "error": "hash mismatch",
                        "expected": expect, "stored": got,
                    })
                if rec.get("prev_hash") != prev:
                    breaks.append({
                        "file": os.path.basename(path), "line": n, "error": "prev_hash break",
                        "expected": prev, "stored": rec.get("prev_hash"),
                    })
                if isinstance(got, str):
                    hashes.append(got)
                    prev = got
                    tip = got
                    tip_ts = rec.get("ts")
                if len(breaks) >= 20:
                    break
        if len(breaks) >= 20:
            break
    if prior_tip and prior_tip not in hashes:
        breaks.append({
            "file": "verify_status.json", "line": 0,
            "error": "prior tip is no longer in the chain (truncation or replacement)",
            "expected": prior_tip, "stored": tip,
        })
    return {
        "ok": not breaks,
        "records": records,
        "tip": tip,
        "tip_ts": tip_ts,
        "breaks": breaks[:20],
        "files": [os.path.basename(p) for p in files],
        "hashes": hashes,
    }


def write_status(result, root=None):
    root = root or _root()
    if not root:
        return None
    os.makedirs(root, exist_ok=True)
    prior = _read_status(root)
    last_good = result.get("tip") if result.get("ok") else prior.get("last_good_tip")
    doc = {
        "ok": bool(result.get("ok")),
        "records": result.get("records"),
        "tip": result.get("tip"),
        "tip_ts": result.get("tip_ts"),
        "last_good_tip": last_good,
        "breaks": result.get("breaks") or [],
        "files": result.get("files") or [],
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "paper_only": True,
    }
    path = os.path.join(root, "verify_status.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")
    os.replace(tmp, path)
    return doc


def card():
    """Read-only dashboard payload. Does not walk the whole chain (the nightly job does)."""
    root = _root()
    if not root:
        return {"paper_only": True, "ok": None, "status": "DISABLED", "records": 0, "breaks": []}
    status = _read_status(root) if os.path.isdir(root) else {}
    when = dt.datetime.now(dt.timezone.utc)
    path = _month_path(root, when) if root else ""
    tail = _last_record(path) if root and os.path.exists(path) else None
    tail_ok = None
    if tail:
        tail_ok = tail.get("hash") == _hash_of(tail) and bool(tail.get("prev_hash"))
    tip = (tail or {}).get("hash")
    verified_tip = status.get("tip")
    if not status:
        state = "UNVERIFIED"
        ok = None
    elif status.get("ok") and tip and verified_tip and tip != verified_tip:
        state = "TIP_MOVED"
        ok = True
    elif status.get("ok"):
        state = "INTACT" if (status.get("records") or tail) else "EMPTY"
        ok = True
    else:
        state = "BREAK"
        ok = False
    if tail_ok is False:
        state = "BREAK"
        ok = False
    return {
        "paper_only": True,
        "ok": ok,
        "status": state,
        "records": status.get("records"),
        "tip": tip or status.get("tip"),
        "tip_ts": (tail or {}).get("ts") or status.get("tip_ts"),
        "checked_at": status.get("checked_at"),
        "breaks": (status.get("breaks") or [])[:5] + ([] if tail_ok is not False else [{
            "error": "current tip hash does not match its body",
        }]),
        "since_verify": bool(tip and verified_tip and tip != verified_tip),
        "tail_ok": tail_ok,
        "month": "%04d%02d" % (when.year, when.month),
        "path": os.path.relpath(path, os.path.dirname(os.path.abspath(__file__))) if path else None,
    }


def _print_result(result):
    if result.get("ok"):
        print("OK records=%s tip=%s files=%s" % (result.get("records"), result.get("tip"), ",".join(result.get("files") or [])))
        return 0
    print("BREAK records=%s tip=%s" % (result.get("records"), result.get("tip")))
    for b in result.get("breaks") or []:
        extra = ""
        if b.get("expected") or b.get("stored"):
            extra = " expected=%s stored=%s" % (b.get("expected"), b.get("stored"))
        print("- %s:%s %s%s" % (b.get("file"), b.get("line"), b.get("error"), extra))
    return 1


def _emit_from_env():
    """Shell hook. Always succeeds so a deploy or exit script is not failed by the audit."""
    try:
        event = os.environ.get("AUDIT_EVENT") or ""
        desk = os.environ.get("AUDIT_DESK") or "tfo"
        raw = os.environ.get("AUDIT_JSON")
        if raw:
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"raw": raw[:2000]}
            if not isinstance(payload, dict):
                payload = {"value": payload}
            log(event or "event", desk, payload)
            return
        if event in ("", "deploy") or os.environ.get("AUDIT_SUMMARY") or os.environ.get("AUDIT_TAG") or os.environ.get("AUDIT_SOURCE"):
            files = os.environ.get("AUDIT_FILES", "")
            deploy(
                source=os.environ.get("AUDIT_SOURCE") or "deploy_log",
                tag=os.environ.get("AUDIT_TAG") or os.environ.get("AUDIT_MD") or "",
                files=files.split() if files else [],
                note=os.environ.get("AUDIT_NOTE") or os.environ.get("AUDIT_SUMMARY") or "",
                entry=os.environ.get("AUDIT_ENTRY") or "",
                desk=desk,
            )
            return
        log(event, desk, {})
    except Exception:
        return


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "verify"
    if cmd in ("-h", "--help", "help"):
        print("usage: python3 -m audit verify [--dir PATH] [--write-status] | emit | card")
        return 0
    if cmd == "emit":
        _emit_from_env()
        return 0
    if cmd == "card":
        print(json.dumps(card(), indent=1))
        return 0
    if cmd != "verify":
        print("unknown command %r" % cmd, file=sys.stderr)
        return 2
    root = None
    write = False
    args = argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--dir" and i + 1 < len(args):
            root = args[i + 1]
            i += 2
            continue
        if args[i] == "--write-status":
            write = True
            i += 1
            continue
        print("unknown flag %r" % args[i], file=sys.stderr)
        return 2
    result = verify(root)
    if write:
        write_status({k: v for k, v in result.items() if k != "hashes"}, root or _root())
    return _print_result(result)


if __name__ == "__main__":
    sys.exit(main())
