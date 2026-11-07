"""Private skills a customer teaches their own paper traders.

Uploaded text is data. It is not executed and it is not fetched.
An AI provider that is not connected does not invent rules.
"""

import os
import re

from customer_platform.errors import ResetBlocked
from customer_platform.files import append_jsonl, read_json, write_json
from customer_platform.paperbook import new_id

STATUSES = (
    "draft",
    "needs_clarification",
    "paper_testing",
    "ready_for_review",
    "active",
    "paused",
)
FIELDS = ("entry", "exit", "risk", "market")
MAX_BYTES = 64 * 1024
_RAIL = re.compile(r"(disable stops|shorts on|no stop|ignore risk|live order)", re.I)
_WEAK = re.compile(
    r"\b(ignore|skip|override|bypass|disable|forget|optional)\b.{0,48}\b(stop|risk|limit)\b"
    r"|\b(stop|risk)\b.{0,48}\b(sure|optional|ignore|skip|override)\b",
    re.I,
)
_SUPPORTED_RISK = re.compile(r"\bstop\b.+\b(below|under|at)\b|\bone position\b|\bsize\b.+\b(limit|cap)\b", re.I)
_URL = re.compile(r"https?://|www\.", re.I)


class Skills:
    def __init__(self, tenant_dir, clock, ai_connected=False):
        self.dir = os.path.join(tenant_dir, "skills")
        self.path = os.path.join(self.dir, "skills.json")
        self.history_path = os.path.join(self.dir, "history.jsonl")
        self.uploads = os.path.join(self.dir, "uploads")
        self.clock = clock
        self.ai_connected = bool(ai_connected)
        self.fetches = 0

    def save_description(self, *, user_id, trader_ids, text, rules=None):
        parsed = _read_rules(text, rules)
        return self._save(user_id, trader_ids, parsed, source_text=str(text or "")[:4000], upload_id="")

    def save_upload(self, *, user_id, trader_ids, filename, data):
        raw = _validate_upload(filename, data)
        text = raw.decode("utf-8")
        if _URL.search(text):
            self.fetches += 0
        parsed = _read_rules(text, None)
        upload_id = new_id("up_")
        os.makedirs(self.uploads, mode=0o700, exist_ok=True)
        path = os.path.join(self.uploads, upload_id)
        with open(path, "wb") as handle:
            handle.write(raw)
        os.chmod(path, 0o600)
        return self._save(user_id, trader_ids, parsed, source_text=text[:4000], upload_id=upload_id)

    def executable_rules(self):
        """Active versions whose own test accepted the written risk rule. Not a model reading."""
        rows = []
        for skill in self._load()["skills"]:
            if skill.get("status") != "active" or not skill.get("versions"):
                continue
            version = skill["versions"][-1]["version"]
            tests = [item for item in skill.get("tests") or [] if item.get("risk_ok")]
            if not tests:
                continue
            entry = skill["rules"].get("entry") or ""
            symbol = _symbol_in(entry)
            percent = _stop_percent(skill["rules"].get("risk") or "")
            if not symbol or percent is None:
                continue
            rows.append({
                "percent": percent,
                "skill_id": skill["id"],
                "symbol": symbol,
                "version": version,
            })
        return rows

    def list_skills(self):
        return [_public(skill) for skill in self._load()["skills"]]

    def for_trader(self, trader_id):
        return [_public(skill) for skill in self._load()["skills"] if trader_id in skill["trader_ids"]]

    def history(self):
        from customer_platform.files import iter_jsonl
        return iter_jsonl(self.history_path)

    def run_paper_test(self, skill_id):
        """Synthetic check of this version only. An account fill is not evidence."""
        body = self._load()
        skill = self._skill(body, skill_id)
        version = skill["versions"][-1]["version"]
        text = " ".join([skill.get("source_text") or ""] + list(skill["rules"].values()))
        risk_ok, review = _review_teaching(text, skill["rules"])
        record = {
            "human_attested": False,
            "id": new_id("st_"),
            "passed": False,
            "profit_is_a_pass": False,
            "risk_ok": risk_ok,
            "synthetic": True,
            "validated_skill": False,
            "version": version,
        }
        skill.setdefault("tests", []).append(record)
        if risk_ok:
            self._version(skill, status="paper_testing", evidence_ids=[record["id"]])
        write_json(self.path, body)
        shown = _public(skill)
        shown["last_test"] = {
            "id": record["id"],
            "passed": False,
            "review": review,
            "risk_ok": risk_ok,
            "synthetic": True,
            "validated_skill": False,
            "version": version,
        }
        return shown

    def attest(self, skill_id):
        body = self._load()
        skill = self._skill(body, skill_id)
        skill["human_attested"] = True
        write_json(self.path, body)
        shown = _public(skill)
        shown["attestation"] = "human attestation, not a passed test"
        return shown

    def set_status(self, skill_id, status, *, evidence_ids=None, known_evidence=None):
        if status not in STATUSES:
            raise ResetBlocked("status", "status is not valid")
        body = self._load()
        skill = self._skill(body, skill_id)
        current = skill["status"]
        evidence_ids = list(evidence_ids or [])
        own_tests = {item["id"] for item in skill.get("tests") or [] if item.get("version") == skill["versions"][-1]["version"] and item.get("risk_ok")}
        if status == "paper_testing":
            if not evidence_ids or any(item not in own_tests for item in evidence_ids):
                raise ResetBlocked("evidence", "an account fill is not a test of this skill version")
        elif evidence_ids and known_evidence is not None and any(item not in set(known_evidence) and item not in own_tests for item in evidence_ids):
            raise ResetBlocked("evidence", "evidence is not in this account")
        if status == "ready_for_review" and current != "paper_testing":
            raise ResetBlocked("status", "review comes after paper testing")
        if status == "active":
            if current != "ready_for_review":
                raise ResetBlocked("status", "an untested skill cannot become active")
            if not skill.get("evidence_ids"):
                raise ResetBlocked("evidence", "an untested skill cannot become active")
        if status == "paused" and current != "active":
            raise ResetBlocked("status", "only an active skill can be paused")
        self._version(skill, status=status, evidence_ids=evidence_ids or skill.get("evidence_ids") or [])
        write_json(self.path, body)
        return _public(skill)

    def rollback(self, skill_id, version):
        body = self._load()
        skill = self._skill(body, skill_id)
        prior = next((item for item in skill["versions"] if item["version"] == int(version)), None)
        if prior is None:
            raise ResetBlocked("version", "that version was not found")
        self._version(skill, status="draft", rules=prior["rules"], clarifications=prior["clarifications"])
        write_json(self.path, body)
        return _public(skill)

    def _save(self, user_id, trader_ids, parsed, source_text, upload_id):
        body = self._load()
        skill = {
            "ai": "unavailable" if not self.ai_connected else "connected",
            "clarifications": parsed["clarifications"],
            "evidence_ids": [],
            "id": new_id("sk_"),
            "rules": parsed["rules"],
            "rules_source": parsed["rules_source"],
            "source_text": source_text,
            "status": parsed["status"],
            "trader_ids": _traders(trader_ids),
            "upload_id": upload_id,
            "user_id": user_id,
            "versions": [],
        }
        self._version(skill, status=skill["status"], rules=skill["rules"], clarifications=skill["clarifications"])
        body["skills"].append(skill)
        write_json(self.path, body)
        return _public(skill)

    def _version(self, skill, *, status, rules=None, clarifications=None, evidence_ids=None):
        version = len(skill["versions"]) + 1
        skill["status"] = status
        if rules is not None:
            skill["rules"] = rules
        if clarifications is not None:
            skill["clarifications"] = clarifications
        if evidence_ids is not None:
            skill["evidence_ids"] = evidence_ids
        row = {
            "clarifications": list(skill["clarifications"]),
            "rules": dict(skill["rules"]),
            "status": status,
            "utc": self.clock(),
            "version": version,
        }
        skill["versions"].append(row)
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        append_jsonl(self.history_path, {
            "skill_id": skill["id"], "status": status, "utc": row["utc"], "version": version,
        })

    def _load(self):
        body = read_json(self.path, None)
        if body is None:
            body = {"skills": []}
            os.makedirs(self.dir, mode=0o700, exist_ok=True)
            write_json(self.path, body)
        return body

    def _skill(self, body, skill_id):
        for skill in body["skills"]:
            if skill["id"] == skill_id:
                return skill
        raise ResetBlocked("skill", "skill was not found on this account")


def _symbol_in(entry):
    match = re.search(r"\bbuy\s+([A-Za-z]{1,8})\b", entry or "")
    if not match:
        return ""
    return match.group(1).upper()


def _stop_percent(risk):
    match = re.search(r"\bstop\s+(\d+(?:\.\d+)?)\s+percent\s+below\b", risk or "", re.I)
    if not match:
        return None
    return match.group(1)


def _review_teaching(text, rules):
    """Supported risk wording only. Anything else stays in review. This is not a claim that all sentences are safe."""
    if _RAIL.search(text) or _WEAK.search(text):
        return False, "needs_review"
    risk = (rules or {}).get("risk") or ""
    if not (_SUPPORTED_RISK.search(risk) or _SUPPORTED_RISK.search(text)):
        return False, "needs_review"
    if not all((rules or {}).get(key) for key in ("entry", "exit", "risk")):
        return False, "needs_review"
    return True, "supported"


def _read_rules(text, rules):
    found = {}
    clarifications = []
    if isinstance(rules, dict):
        for key in FIELDS:
            value = str(rules.get(key) or "").strip()
            if value:
                if _RAIL.search(value) or _URL.search(value):
                    clarifications.append(key + " was not accepted")
                else:
                    found[key] = value[:500]
    for line in str(text or "").splitlines():
        if _URL.search(line):
            clarifications.append("A web address was ignored and not opened.")
            continue
        if _RAIL.search(line):
            clarifications.append("That line cannot change stops, shorts, or risk rails.")
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key in FIELDS and value.strip() and key not in found:
            found[key] = value.strip()[:500]
    missing = [key for key in FIELDS if key not in found]
    if not found and clarifications:
        status = "needs_clarification"
    elif missing:
        status = "needs_clarification"
    else:
        status = "draft"
    return {
        "clarifications": clarifications,
        "rules": found,
        "rules_source": "your words" if found else "not interpreted",
        "status": status,
    }


def _validate_upload(filename, data):
    name = str(filename or "")
    if "/" in name or "\\" in name or not name.lower().endswith((".txt", ".md")):
        raise ResetBlocked("upload", "only a plain text file is accepted")
    raw = bytes(data or b"")
    if not raw or len(raw) > MAX_BYTES:
        raise ResetBlocked("upload", "the file is empty or too large")
    if raw.startswith(b"#!") or b"\x00" in raw:
        raise ResetBlocked("upload", "that file is not accepted")
    try:
        raw.decode("utf-8")
    except UnicodeError as exc:
        raise ResetBlocked("upload", "that file is not accepted") from exc
    return raw


def _traders(value):
    rows = []
    for item in value or ["desk"]:
        text = str(item or "").strip()[:40]
        if text and text not in rows:
            rows.append(text)
    return rows or ["desk"]


def _public(skill):
    return {
        "ai": skill["ai"],
        "clarifications": list(skill["clarifications"]),
        "evidence_ids": list(skill.get("evidence_ids") or []),
        "human_attested": bool(skill.get("human_attested")),
        "id": skill["id"],
        "promise": False,
        "rules": dict(skill["rules"]),
        "rules_source": skill["rules_source"],
        "status": skill["status"],
        "trader_ids": list(skill["trader_ids"]),
        "versions": [{"status": item["status"], "version": item["version"]} for item in skill["versions"]],
    }
