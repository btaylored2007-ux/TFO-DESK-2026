"""Operator incident queue. External alerts stay off.

A proposal is a backlog note. It does not patch code or loosen a rail.
Closure may be blocked, escalated, or proposed. Fixed is recorded only
after verification passes.
"""

import os

from customer_platform.errors import Denied
from customer_platform.files import append_jsonl, read_json, write_json
from customer_platform.paperbook import new_id

ALLOWLIST = frozenset({"recheck_prices"})
OPEN = frozenset({"open", "assigned", "repairing"})


class Incidents:
    def __init__(self, root, clock):
        self.dir = os.path.join(root, "operator")
        self.path = os.path.join(self.dir, "incidents.json")
        self.audit_path = os.path.join(self.dir, "incident_audit.jsonl")
        self.backlog_path = os.path.join(self.dir, "improvement_backlog.json")
        self.clock = clock
        self.external_enabled = False

    def detect(self, code, tenant_id, *, severity="blocking", evidence=None):
        code = _code(code)
        evidence = _evidence(evidence)
        body = self._load()
        for row in body["incidents"]:
            if row["code"] == code and row["tenant_id"] == tenant_id and row["status"] in OPEN:
                row["recurrence"] = int(row.get("recurrence") or 1) + 1
                row["suppressed"] = int(row.get("suppressed") or 0) + 1
                self._save(body, "dedup", row["id"], "")
                return {"created": False, "id": row["id"], "recurrence": row["recurrence"]}
        prior = [row for row in body["incidents"] if row["code"] == code and row["tenant_id"] == tenant_id and row["status"] == "closed"]
        incident = {
            "code": code,
            "delivery": {"ack": False, "attempts": 0, "external_sent": False, "in_app": True},
            "evidence": evidence,
            "id": new_id("inc_"),
            "improvement": None,
            "learning": "",
            "owner": "",
            "prior_id": prior[-1]["id"] if prior else "",
            "recurrence": 1,
            "recurrence_after_fix": len(prior),
            "repair": "",
            "requirement": _requirement(code),
            "root_cause": "UNKNOWN" if not evidence else evidence[0],
            "severity": severity if severity in {"blocking", "warn"} else "warn",
            "status": "open",
            "suppressed": 0,
            "tenant_id": tenant_id,
            "utc": self.clock(),
            "verification": "",
        }
        external = self._external()
        incident["delivery"].update(external)
        body["incidents"].append(incident)
        self._save(body, "detect", incident["id"], "")
        return {"created": True, "external_sent": False, "id": incident["id"]}

    def list_for_operator(self):
        rows = []
        for row in self._load()["incidents"]:
            rows.append({
                "code": row["code"],
                "id": row["id"],
                "improvement_status": (row.get("improvement") or {}).get("status", ""),
                "owner": row.get("owner") or "",
                "recurrence": row.get("recurrence") or 1,
                "root_cause": row.get("root_cause") or "UNKNOWN",
                "severity": row["severity"],
                "status": row["status"],
                "tenant_id": row["tenant_id"],
                "verification": row.get("verification") or "",
                "learning": row.get("learning") or "",
                "external_sent": False,
            })
        return {
            "engineer_running": False,
            "external_notification": "off",
            "incidents": rows,
        }

    def assign(self, incident_id, operator_id):
        row = self._row(incident_id)
        row["owner"] = str(operator_id or "")[:40]
        row["status"] = "assigned"
        row["delivery"]["ack"] = True
        self._save(self._load_replaced(row), "assign", incident_id, operator_id)
        return {"id": incident_id, "status": "assigned"}

    def repair(self, incident_id, action, operator_id, probe):
        if action not in ALLOWLIST:
            self._audit("repair_refused", incident_id, operator_id)
            raise Denied("that repair is not on the allowlist and was not run")
        row = self._row(incident_id)
        feeds = probe() if probe else []
        ready = any(item.get("feed_id") == "prices" and item.get("state") == "ready" and item.get("fresh") and item.get("authorized") for item in feeds)
        row["repair"] = action
        row["status"] = "repairing"
        row["verification"] = "prices ready" if ready else "prices still unavailable"
        row["root_cause"] = row["verification"] if row["root_cause"] == "UNKNOWN" else row["root_cause"]
        self._save(self._load_replaced(row), "repair", incident_id, operator_id)
        return {"fixed": False, "id": incident_id, "verification": row["verification"]}

    def verify(self, incident_id, operator_id, probe):
        row = self._row(incident_id)
        feeds = probe() if probe else []
        ready = any(item.get("feed_id") == "prices" and item.get("state") == "ready" and item.get("fresh") and item.get("authorized") for item in feeds)
        row["verification"] = "prices ready" if ready else "prices still unavailable"
        row["status"] = "verified" if ready else "blocked"
        self._save(self._load_replaced(row), "verify", incident_id, operator_id)
        return {"fixed": ready, "id": incident_id, "status": row["status"]}

    def close(self, incident_id, operator_id, outcome, learning=""):
        if outcome not in {"closed", "blocked", "escalated", "proposed"}:
            raise Denied("closure outcome is not valid")
        row = self._row(incident_id)
        if outcome == "closed" and row.get("verification") != "prices ready":
            raise Denied("cannot record fixed without a passing verification")
        row["status"] = outcome
        row["learning"] = str(learning or "")[:240]
        self._save(self._load_replaced(row), "close", incident_id, operator_id)
        return {"fixed": outcome == "closed", "id": incident_id, "status": outcome}

    def propose(self, incident_id, operator_id, *, priority, test, text):
        row = self._row(incident_id)
        item = {
            "activates_code": False,
            "id": new_id("imp_"),
            "incident_id": incident_id,
            "owner": operator_id,
            "priority": priority if priority in {"low", "medium", "high"} else "medium",
            "requirement": row.get("requirement") or "",
            "status": "proposed",
            "test": str(test or "not written")[:120],
            "text": str(text or "")[:240],
        }
        row["improvement"] = item
        row["status"] = "proposed"
        backlog = read_json(self.backlog_path, {"items": []})
        backlog["items"].append(item)
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        write_json(self.backlog_path, backlog)
        self._save(self._load_replaced(row), "propose", incident_id, operator_id)
        return {"activates_code": False, "id": item["id"], "status": "proposed"}

    def _external(self):
        return {"ack": False, "attempts": 0, "external_sent": False, "reason": "external notification is off"}

    def _load(self):
        body = read_json(self.path, None)
        if body is None:
            body = {"incidents": []}
        return body

    def _row(self, incident_id):
        for row in self._load()["incidents"]:
            if row["id"] == incident_id:
                return row
        raise Denied("incident was not found")

    def _load_replaced(self, row):
        body = self._load()
        body["incidents"] = [row if item["id"] == row["id"] else item for item in body["incidents"]]
        return body

    def _save(self, body, action, incident_id, operator_id):
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        write_json(self.path, body)
        self._audit(action, incident_id, operator_id)

    def _audit(self, action, incident_id, operator_id):
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        append_jsonl(self.audit_path, {
            "action": action, "id": incident_id, "operator": operator_id, "utc": self.clock(),
        })


def _code(value):
    text = str(value or "")
    if text not in {"prices_unavailable", "school_content_missing", "engine_not_connected"}:
        return "prices_unavailable"
    return text


def _evidence(value):
    if not value:
        return []
    clean = []
    for item in value:
        text = str(item)
        if "sk-" in text or "key" in text.lower():
            continue
        clean.append(text[:120])
    return clean[:5]


def _requirement(code):
    return {
        "engine_not_connected": "P0-3",
        "prices_unavailable": "P0-3",
        "school_content_missing": "P1-3",
    }.get(code, "P0-3")
