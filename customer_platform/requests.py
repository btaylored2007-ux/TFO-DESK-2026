"""Customer request record. This is not the owner desk chat and it does not call a model.

A request has one lead reply. Extra reviews stay behind that reply. The same
delivery key is a retry, not a reason to drop a different error. A late reply
after cancel is kept and is not the active answer.
"""

import os
import threading

from customer_platform.errors import Denied, ResetBlocked
from customer_platform.files import read_json, write_json
from customer_platform.paperbook import new_id


class Requests:
    def __init__(self, tenant_dir, clock):
        self.path = os.path.join(tenant_dir, "requests", "threads.json")
        self.clock = clock
        self.calls = 0
        self.reply_bound = 24
        self._owner = None
        self._lock = threading.Lock()

    def submit(self, *, user_id, text, idempotency_key):
        with self._lock:
            body = self._load()
            for row in body["requests"]:
                if row["idempotency_key"] == idempotency_key:
                    return _customer(row)
            if not str(text or "").strip():
                raise ResetBlocked("request", "the question is empty")
            row = {
                "answer_id": "",
                "cancelled": False,
                "customer_id": user_id,
                "deliveries": [],
                "events": [],
                "id": new_id("rq_"),
                "idempotency_key": idempotency_key,
                "provider": "not_connected",
                "provenance": "not_sent",
                "reviews": [],
                "status": "pending",
                "text": str(text)[:2000],
                "utc": self.clock(),
            }
            body["requests"].append(row)
            write_json(self.path, body)
            return _customer(row)

    def deliver(self, request_id, *, sender, body_text, kind, delivery_key, stale=False):
        if self._owner == threading.get_ident():
            raise ResetBlocked("loop", "a delivery cannot start another delivery")
        with self._lock:
            self._owner = threading.get_ident()
            try:
                return self._deliver(request_id, sender=sender, body_text=body_text, kind=kind, delivery_key=delivery_key, stale=stale)
            finally:
                self._owner = None

    def _deliver(self, request_id, *, sender, body_text, kind, delivery_key, stale=False):
            if kind not in {"lead", "review", "critical", "failure"}:
                raise ResetBlocked("delivery", "delivery kind is not valid")
            if not str(sender or "").strip():
                raise ResetBlocked("sender", "sender is required")
            stored = self._load()
            row = self._row(stored, request_id)
            for item in row["deliveries"]:
                if item["delivery_key"] == delivery_key:
                    item["retries"] = int(item.get("retries") or 0) + 1
                    row.setdefault("audit", []).append({
                        "delivery_key": delivery_key,
                        "kind": "retry",
                        "utc": self.clock(),
                    })
                    write_json(self.path, stored)
                    return _customer(row)
            assigned = int(row.get("next_sequence") or 1)
            entry = {
                "audience": row["customer_id"],
                "body": str(body_text or "")[:2000],
                "delivery_key": delivery_key,
                "id": new_id("dv_"),
                "kind": kind,
                "request_id": row["id"],
                "retries": 0,
                "sender": str(sender)[:80],
                "sequence": assigned,
                "utc": self.clock(),
            }
            row["next_sequence"] = assigned + 1
            row["deliveries"].append(entry)
            over_bound = len(row["deliveries"]) > self.reply_bound
            if kind == "critical":
                row["events"].append({"body": entry["body"], "id": entry["id"], "sender": entry["sender"], "utc": entry["utc"]})
            elif kind == "review" and not stale and not over_bound:
                if row["cancelled"] or row["status"] in {"cancelled", "failed", "complete"}:
                    entry["late"] = True
                row["reviews"].append({
                    "body": entry["body"],
                    "discussion": False,
                    "id": entry["id"],
                    "independent": True,
                    "sender": entry["sender"],
                })
            elif stale or over_bound or row["cancelled"] or row["status"] in {"cancelled", "failed", "complete"}:
                entry["late"] = True
                if stale:
                    entry["stale"] = True
                if over_bound:
                    entry["gated"] = "bound"
            elif kind == "failure":
                row["status"] = "failed"
                row["provider"] = entry["sender"]
            elif kind == "lead":
                row["status"] = "complete"
                row["answer_id"] = delivery_key
                row["provider"] = entry["sender"]
            write_json(self.path, stored)
            return _customer(row)

    def cancel(self, request_id):
        with self._lock:
            stored = self._load()
            row = self._row(stored, request_id)
            if row["status"] == "pending":
                row["status"] = "cancelled"
                row["cancelled"] = True
                write_json(self.path, stored)
            return _customer(row)

    def customer_view(self, request_id):
        return _customer(self._row(self._load(), request_id))

    def reviews(self, request_id):
        row = self._row(self._load(), request_id)
        return list(row["reviews"])

    def list_for(self, user_id):
        rows = []
        for row in self._load()["requests"]:
            if row["customer_id"] != user_id:
                continue
            shown = _customer(row)
            shown["history"] = [
                {
                    "id": item.get("id") or "",
                    "kind": item.get("kind") or "",
                    "late": bool(item.get("late")),
                    "sender": item.get("sender") or "",
                    "sequence": item.get("sequence") or 0,
                }
                for item in row["deliveries"]
            ]
            shown["provenance"] = row.get("provenance") or "not_sent"
            rows.append(shown)
        return rows

    def note_unconnected_retry(self, request_id):
        with self._lock:
            stored = self._load()
            row = self._row(stored, request_id)
            row.setdefault("audit", []).append({"kind": "retry_unconnected", "utc": self.clock()})
            row["provenance"] = row.get("provenance") or "not_sent"
            write_json(self.path, stored)
            return _customer(row)

    def consider_text(self, text):
        """A reply is text. It does not open a request or place an order."""
        return {"acted": False, "opened": 0, "reason": "reply text is not an action"}

    def _load(self):
        body = read_json(self.path, None)
        if body is None:
            body = {"requests": []}
            os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
            write_json(self.path, body)
        return body

    def _row(self, stored, request_id):
        for row in stored["requests"]:
            if row["id"] == request_id:
                return row
        raise Denied("request was not found")


def _customer(row):
    lead = ""
    if row.get("answer_id"):
        for item in row["deliveries"]:
            if item["delivery_key"] == row["answer_id"] and not item.get("late"):
                lead = item["body"]
    return {
        "acted": False,
        "answer": lead or "No answer yet.",
        "discussion": False,
        "events": list(row.get("events") or []),
        "id": row["id"],
        "owner": row["customer_id"],
        "provider": row.get("provider") or "not_connected",
        "reviews_available": len(row.get("reviews") or []),
        "provenance": row.get("provenance") or "not_sent",
        "status": row["status"],
        "text": row.get("text") or "",
        "synthesis": False,
    }
