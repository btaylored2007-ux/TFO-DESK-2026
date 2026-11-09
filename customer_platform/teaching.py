"""Owner-scoped research and strategy versions. Submitted text is data, not instructions."""

import os
import re

from customer_platform.errors import ResetBlocked
from customer_platform.files import append_jsonl, iter_jsonl, read_json, write_json
from customer_platform.paperbook import new_id
from customer_platform.redact import redact

_INJECTION = re.compile(
    r"(ignore previous instructions|you are now|system prompt|enable shorts|"
    r"disable the stop|disable stops|api[_ ]?key|reveal the secret|"
    r"\bexec\s*\(|\beval\s*\(|__import__|subprocess)",
    re.I,
)
_ALLOWED = {"name", "note", "side", "symbol"}
PROMOTE_PHRASE = "PROMOTE PAPER HYPOTHESIS"


class TeachingStore:
    def __init__(self, knowledge_dir, ledger_ids, clock):
        self.dir = knowledge_dir
        self.path = os.path.join(knowledge_dir, "ideas.json")
        self.history_path = os.path.join(knowledge_dir, "history.jsonl")
        self.ledger_ids = ledger_ids
        self.clock = clock

    def load(self):
        body = read_json(self.path, None)
        if body is None:
            body = {"ideas": []}
            write_json(self.path, body)
        return body

    def submit(self, *, user_id, kind, body, idempotency_key):
        store = self.load()
        for idea in store["ideas"]:
            if idea.get("idempotency_key") == idempotency_key:
                return idea
        if kind not in {"natural_language", "structured"}:
            raise ResetBlocked("idea", "idea kind is not supported")
        quarantine, stored_body = self._screen(kind, body)
        idea = {
            "idempotency_key": idempotency_key,
            "idea_id": new_id("id_"),
            "owner_user_id": user_id,
            "versions": [
                {
                    "body": stored_body,
                    "kind": kind,
                    "provenance": {
                        "source": "customer_submission",
                        "submitted_by": user_id,
                        "untrusted": True,
                        "utc": self.clock(),
                    },
                    "quarantine": quarantine,
                    "status": "research",
                    "version": 1,
                }
            ],
        }
        store["ideas"].append(idea)
        write_json(self.path, store)
        self._history({
            "chars": _chars(stored_body),
            "idea_id": idea["idea_id"],
            "id": new_id("th_"),
            "kind": "submit",
            "quarantine": quarantine,
            "utc": self.clock(),
            "version": 1,
        })
        return idea

    def record_paper_test(self, idea_id, ledger_ids):
        store = self.load()
        idea = self._idea(store, idea_id)
        version = idea["versions"][-1]
        if version["quarantine"]:
            raise ResetBlocked("quarantine", "quarantined text cannot be paper-tested into a promotion")
        if version["status"] not in {"research", "paper_tested"}:
            raise ResetBlocked("status", "only research can be marked paper-tested")
        known = self.ledger_ids()
        clean = []
        for ledger_id in ledger_ids:
            if ledger_id not in known:
                raise ResetBlocked("evidence", "paper-test evidence is not in this tenant ledger")
            clean.append(ledger_id)
        if not clean:
            raise ResetBlocked("evidence", "paper-test evidence is required")
        version["status"] = "paper_tested"
        version["paper_test"] = {"ledger_ids": clean, "utc": self.clock()}
        write_json(self.path, store)
        self._history({
            "idea_id": idea_id,
            "id": new_id("th_"),
            "kind": "paper_test",
            "ledger_ids": clean,
            "quarantine": False,
            "utc": self.clock(),
            "version": version["version"],
        })
        return idea

    def promote(self, idea_id, confirmation):
        if confirmation != PROMOTE_PHRASE:
            raise ResetBlocked("confirmation_required", "promotion requires an explicit confirmation")
        store = self.load()
        idea = self._idea(store, idea_id)
        version = idea["versions"][-1]
        if version["quarantine"] or version["status"] != "paper_tested":
            raise ResetBlocked("not_ready", "an idea stays research until a paper test and an explicit promotion")
        version["status"] = "promoted"
        version["promotion"] = {
            "activation": "not_activated",
            "affects_firm_risk": False,
            "can_change_rails": False,
            "utc": self.clock(),
        }
        write_json(self.path, store)
        self._history({
            "idea_id": idea_id,
            "id": new_id("th_"),
            "kind": "promote",
            "quarantine": False,
            "utc": self.clock(),
            "version": version["version"],
        })
        return idea

    def rollback(self, idea_id):
        store = self.load()
        idea = self._idea(store, idea_id)
        current = idea["versions"][-1]
        idea["versions"].append({
            "body": current["body"],
            "kind": current["kind"],
            "provenance": {"source": "rollback", "untrusted": True, "utc": self.clock()},
            "quarantine": current["quarantine"],
            "rolls_back": current["version"],
            "status": "rolled_back",
            "version": current["version"] + 1,
        })
        write_json(self.path, store)
        self._history({
            "idea_id": idea_id,
            "id": new_id("th_"),
            "kind": "rollback",
            "quarantine": current["quarantine"],
            "utc": self.clock(),
            "version": current["version"] + 1,
        })
        return idea

    def history(self):
        return iter_jsonl(self.history_path)

    def _screen(self, kind, body):
        if kind == "natural_language":
            text = str(body if body is not None else "")
            if not text.strip() or len(text) > 4000:
                raise ResetBlocked("idea", "idea text is empty or too long")
            return bool(_INJECTION.search(text)), text
        if not isinstance(body, dict):
            raise ResetBlocked("idea", "structured idea must be an object")
        extra = set(body) - _ALLOWED
        if extra:
            raise ResetBlocked("idea", "structured idea has fields this desk does not accept")
        if body.get("side") != "long_only":
            raise ResetBlocked("idea", "structured ideas are long_only; shorts stay off")
        symbol = str(body.get("symbol") or "")
        if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", symbol):
            raise ResetBlocked("idea", "symbol is not usable")
        note = str(body.get("note") or "")
        if len(note) > 1000:
            raise ResetBlocked("idea", "note is too long")
        name = str(body.get("name") or "")[:80]
        stored = {"name": name, "note": note, "side": "long_only", "symbol": symbol}
        return bool(_INJECTION.search(note) or _INJECTION.search(name)), stored

    def _idea(self, store, idea_id):
        for idea in store["ideas"]:
            if idea["idea_id"] == idea_id:
                return idea
        raise ResetBlocked("idea", "idea was not found in this tenant")

    def _history(self, row):
        row["chars"] = row.get("chars", 0)
        append_jsonl(self.history_path, row)


def _chars(body):
    if isinstance(body, dict):
        return len(str(body.get("note") or ""))
    return len(str(body))


def public_idea(idea):
    """Copy safe for an API response. The raw body stays in the tenant file."""
    versions = []
    for version in idea["versions"]:
        versions.append({
            "kind": version["kind"],
            "quarantine": version["quarantine"],
            "status": version["status"],
            "untrusted": True,
            "version": version["version"],
        })
    return {"idea_id": idea["idea_id"], "note": scan_excerpt((idea["versions"][-1].get("body") if idea.get("versions") else "") or ""), "versions": versions, "raw_body_in_response": False}


def scan_excerpt(body):
    return redact(body if isinstance(body, str) else "")
