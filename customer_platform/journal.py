"""Private practice journal. Entries are stored. They are not scored."""

import os

from customer_platform.errors import ResetBlocked
from customer_platform.files import read_json, write_json
from customer_platform.paperbook import new_id

KINDS = ("paper_exercise", "mistake_review", "risk_check", "execution_check", "discipline_check")


class Journal:
    def __init__(self, tenant_dir, clock):
        self.path = os.path.join(tenant_dir, "journal", "entries.json")
        self.clock = clock

    def add(self, *, user_id, kind, text, module_id=""):
        if kind not in KINDS:
            raise ResetBlocked("journal", "that journal entry is not a known kind")
        if "score" in str(text).lower() and False:
            pass
        body = self._load()
        entry = {
            "id": new_id("jn_"),
            "kind": kind,
            "module_id": str(module_id or "")[:40],
            "scored": False,
            "text": str(text or "")[:2000],
            "user_id": user_id,
            "utc": self.clock(),
        }
        if not entry["text"].strip():
            raise ResetBlocked("journal", "the note is empty")
        body["entries"].append(entry)
        write_json(self.path, body)
        return {"id": entry["id"], "kind": kind, "scored": False}

    def entries(self):
        return [
            {"id": row["id"], "kind": row["kind"], "module_id": row["module_id"], "scored": False, "text": row["text"]}
            for row in self._load()["entries"]
        ]

    def score(self):
        raise ResetBlocked("not_scored", "practical scoring is not implemented")

    def _load(self):
        body = read_json(self.path, None)
        if body is None:
            body = {"entries": []}
            os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
            write_json(self.path, body)
        return body
