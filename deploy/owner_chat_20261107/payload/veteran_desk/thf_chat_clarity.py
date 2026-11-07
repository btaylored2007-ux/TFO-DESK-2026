"""Owner-chat presentation and delivery guards.

Stored rows are not rewritten. The default view hides routine noise, keeps one
lead card for a reply/report pair, and labels independent answers. An audit
view still returns every input row. Synthetic tests cover the rules. This
module does not read private logs.
"""
import hashlib
import re

NOISE_KINDS = {"ack", "status", "hop", "unchanged", "rollup"}
STALE = {"cancelled", "canceled", "expired", "stale", "superseded"}
CRITICAL_KIND = {"halt", "approval", "auth_failure", "feed_halt"}
CRITICAL_RX = re.compile(r"\b(halt|feed down|approval required|auth failure|unauthori[sz]ed)\b", re.I)
CLAIM_RX = re.compile(r"\b(i've assigned|i have assigned|deployed the|i've asked|i have asked)\b", re.I)
TAIL_RX = re.compile(r"\bChecked/Blocks\b")


def _copy(rows):
    out = []
    for row in rows or []:
        out.append(dict(row) if isinstance(row, dict) else {"text": str(row)})
    return out


def _norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _migrated(index, row):
    seed = "|".join([
        str(index),
        str(row.get("from") or ""),
        str(row.get("to") or ""),
        str(row.get("kind") or ""),
        _norm(row.get("text"))[:240],
    ])
    return "mig-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]


def _prov(row):
    parts = [
        "provider=%s" % (row.get("provider") or "unknown"),
        "from=%s" % (row.get("from") or "unknown"),
        "to=%s" % (row.get("audience") or "unspecified"),
        "request=%s" % row.get("request_id"),
        "event=%s" % row.get("event_id"),
    ]
    if row.get("migrated_id"):
        parts.append("id=migrated")
    if row.get("independent"):
        parts.append("independent")
    if row.get("unresolved"):
        parts.append("unresolved")
    if row.get("stale"):
        parts.append("stale")
    if row.get("claim_status") == "unverified":
        parts.append("claim-unverified")
    return " · ".join(parts)


def _noise(row):
    if row.get("critical"):
        return False
    kind = str(row.get("kind") or "")
    if kind in NOISE_KINDS:
        return True
    text = str(row.get("text") or "")
    if TAIL_RX.search(text) and len(_norm(text)) < 80:
        return True
    return False


def prepare(rows):
    """Return enriched copies. Input objects are not changed."""
    enriched = []
    for index, row in enumerate(_copy(rows)):
        item = dict(row)
        item["append_index"] = index
        item["event_id"] = row.get("id") or _migrated(index, row)
        item["migrated_id"] = not bool(row.get("id"))
        item["received_time"] = row.get("received_time") or row.get("ts") or row.get("ts_wita") or index
        item["event_time"] = row.get("event_time") or row.get("ts") or row.get("ts_wita") or item["received_time"]
        item["provider"] = row.get("provider") or row.get("ai") or row.get("from") or "unknown"
        item["request_id"] = row.get("request_id") or row.get("root") or item["event_id"]
        item["audience"] = row.get("to") or "unspecified"
        item["consensus"] = False
        item["action"] = False
        text = str(item.get("text") or "")
        item["critical"] = bool(row.get("critical")) or str(row.get("kind") or "") in CRITICAL_KIND or bool(CRITICAL_RX.search(text))
        item["stale"] = str(row.get("status") or "") in STALE
        item["current"] = not item["stale"]
        if CLAIM_RX.search(text) and not row.get("receipt_id"):
            item["claim_status"] = "unverified"
        elif row.get("receipt_id"):
            item["claim_status"] = "receipt"
        else:
            item["claim_status"] = "none"
        item["prov"] = _prov(item)
        enriched.append(item)
    return enriched


def present(rows):
    """Split a copy of rows into the default owner view and the full audit view."""
    enriched = prepare(rows)
    seen = {}
    owner = []
    audit = []
    for item in enriched:
        audit.append(item)
        key = (item["request_id"], item["provider"], item.get("kind"), _norm(item.get("text")))
        if item["critical"]:
            owner.append(item)
            continue
        if key in seen and not item["stale"]:
            seen[key].setdefault("duplicate_ids", []).append(item["event_id"])
            item["folded"] = True
            item["clarity_hide"] = True
            continue
        seen[key] = item
        if item["stale"]:
            item["clarity_hide"] = False
            item["prov"] = "stale · not a new action · " + item["prov"]
            owner.append(item)
            continue
        if _noise(item):
            item["clarity_hide"] = True
            continue
        owner.append(item)
    grouped = {}
    for item in list(owner):
        grouped.setdefault((item["request_id"], item["provider"]), []).append(item)
    folded = []
    for group in grouped.values():
        kinds = {item.get("kind") for item in group}
        if "reply" in kinds and "report" in kinds:
            replies = [item for item in group if item.get("kind") == "reply"]
            lead = next((item for item in replies if not item.get("critical")), replies[0])
            lead["lead"] = True
            for item in group:
                if item is not lead and item.get("kind") == "report" and not item.get("critical"):
                    item["folded"] = True
                    item["clarity_hide"] = True
                    item["lead_event"] = lead["event_id"]
                    folded.append(item)
    owner = [item for item in owner if not item.get("clarity_hide")]
    by_request = {}
    for item in owner:
        by_request.setdefault(item["request_id"], []).append(item)
    for items in by_request.values():
        providers = {item["provider"] for item in items if item.get("from") != "brett" and item.get("kind") != "request"}
        snapshots = {item.get("snapshot_id") for item in items if item.get("snapshot_id")}
        scopes = {item.get("scope") for item in items if item.get("scope")}
        if len(providers) > 1:
            for item in items:
                if item.get("from") == "brett":
                    continue
                item["independent"] = True
                item["consensus"] = False
                item["prov"] = "independent answer · " + _prov(item)
        if len(snapshots) > 1 or len(scopes) > 1:
            for item in items:
                item["unresolved"] = True
                item["consensus"] = False
                if "unresolved" not in item["prov"]:
                    item["prov"] += " · unresolved snapshot"
    owner.sort(key=lambda item: (str(item.get("event_time")), item["append_index"]))
    return {"owner": owner, "audit": audit, "folded": folded, "rewritten": False}


def stamp(rows):
    """Copies tagged for the existing noise filter. Stored rows stay unchanged."""
    view = present(rows)
    hidden = {id(item) for item in view["audit"] if item.get("clarity_hide")}
    # present() copies are the rows we return; map by append index
    hide_at = {item["append_index"] for item in view["audit"] if item.get("clarity_hide")}
    stamped = []
    for item in view["audit"]:
        row = dict(item)
        row["clarity_hide"] = item["append_index"] in hide_at or item.get("clarity_hide")
        row["clarity_critical"] = bool(item.get("critical"))
        if item.get("lead"):
            row["clarity_lead"] = True
        stamped.append(row)
    # Keep audit order (append order) so callers that iterate still see history.
    # Default UIs use kind()==noise to hide clarity_hide rows.
    del hidden
    return stamped


def owner_rows(rows):
    return present(rows)["owner"]


def wording_note(original, later):
    """Flag a later text that drops tokens from the owner instruction it claims to quote."""
    original_tokens = [tok for tok in re.findall(r"[A-Za-z0-9']+", str(original or "")) if len(tok) > 2]
    later_text = str(later or "")
    missing = [tok for tok in original_tokens if tok.lower() not in later_text.lower()]
    claims_quote = "quote" in later_text.lower() or "instruction" in later_text.lower()
    return {
        "preserved": not missing,
        "missing_tokens": missing,
        "ambiguous": bool(missing and claims_quote),
        "policy_changed": False,
    }


def tenant_allows(actor, record):
    """Operator records are not readable by another tenant. No network and no store."""
    actor = actor or {}
    record = record or {}
    tenant = actor.get("tenant") or "owner"
    if record.get("visibility") == "operator" and tenant not in ("owner", "operator"):
        return False
    record_tenant = record.get("tenant")
    if record_tenant and tenant not in ("owner", "operator") and record_tenant != tenant:
        return False
    if record.get("kind") in ("key", "secret", "internal_mail") and tenant not in ("owner", "operator"):
        return False
    return True


def relay_allowed(hop):
    return int(hop or 1) <= 2


def task_allowed(message):
    """Bot and advisor rows must not open a new task."""
    message = message or {}
    sender = str(message.get("from") or message.get("role") or "")
    if sender and sender not in ("brett", "owner"):
        return False
    if message.get("kind") in NOISE_KINDS or message.get("kind") in ("reply", "report", "relay"):
        return False
    return True
