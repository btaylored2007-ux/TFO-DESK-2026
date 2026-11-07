"""Per-tenant learning brain.

Lessons are appended only from this tenant's paper outcomes, research, and
approved teachings. A model call is a separate, bounded slot and is not wired.
"""

import os
from datetime import datetime, timedelta, timezone

from customer_platform.errors import BrainError, IdempotencyConflict, RailsIntegrity
from customer_platform.files import (
    append_jsonl,
    file_lock,
    hash_tree,
    iter_jsonl,
    process_lock,
    read_json,
    sha256_bytes,
    write_json,
    write_json_exclusive,
)
from customer_platform.paperbook import new_id

RAILS = {
    "brain_cannot_change_rails": True,
    "brain_cannot_place_orders": True,
    "customer_platform": "2026.11.07",
    "ideas_cannot_change_rails": True,
    "live_execution": "disabled",
    "maximum_paper_notional": "equity",
    "new_entries_require_positive_equity": True,
    "shorts": "off",
    "version": 1,
}


def _canonical_rails():
    import json
    return json.dumps(RAILS, separators=(",", ":"), sort_keys=True).encode("utf-8")


class Brain:
    def __init__(self, tenant_dir, clock, secrets, log):
        self.dir = os.path.join(tenant_dir, "brain")
        self.clock = clock
        self.secrets = secrets
        self.log = log
        self.sources = [PaperOutcomeSource(), ResearchSource(), ApprovedTeachingSource()]
        self.lock_path = os.path.join(self.dir, "lock")

    def add_source(self, source):
        self.sources.append(source)

    def init(self, currency):
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        rails_path = os.path.join(self.dir, "rails.json")
        if not os.path.exists(rails_path):
            body = dict(RAILS)
            body["sha256"] = sha256_bytes(_canonical_rails())
            write_json_exclusive(rails_path, body)
        cursor_path = os.path.join(self.dir, "cursor.json")
        if not os.path.exists(cursor_path):
            write_json(cursor_path, {
                "interval_seconds": 86400,
                "last_attempt_utc": None,
                "last_error_code": None,
                "last_run_id": None,
                "last_success_utc": None,
                "last_trigger": None,
                "ledger_index": 0,
                "next_due_utc": _plus(self.clock(), 86400),
                "pending_event": False,
                "teaching_index": 0,
            })
        spend_path = os.path.join(self.dir, "spend.json")
        if not os.path.exists(spend_path):
            write_json(spend_path, {
                "blocked_model_requests": 0,
                "cap": "0.00",
                "currency": currency,
                "model_calls": 0,
                "spent": "0.00",
            })
        for name in ("lessons.jsonl", "evidence.jsonl", "runs.jsonl"):
            path = os.path.join(self.dir, name)
            if not os.path.exists(path):
                open(path, "ab").close()
                os.chmod(path, 0o600)
        seen = os.path.join(self.dir, "seen.json")
        if not os.path.exists(seen):
            write_json(seen, {"keys": []})
        idem = os.path.join(self.dir, "idempotency.json")
        if not os.path.exists(idem):
            write_json(idem, {})

    def assert_rails(self):
        path = os.path.join(self.dir, "rails.json")
        body = read_json(path, None)
        if not body or body.get("shorts") != "off" or body.get("live_execution") != "disabled":
            raise RailsIntegrity()
        digest = body.get("sha256")
        if digest != sha256_bytes(_canonical_rails()):
            raise RailsIntegrity()
        return body

    def mark_pending(self):
        def edit(cursor):
            cursor["pending_event"] = True
            return cursor
        self._update_cursor(edit)

    def set_interval(self, seconds):
        seconds = int(seconds)
        if seconds < 0 or seconds > 86400 * 30:
            raise BrainError("interval", "brain interval is out of range")

        def edit(cursor):
            cursor["interval_seconds"] = seconds
            cursor["next_due_utc"] = _plus(self.clock(), seconds)
            return cursor
        self._update_cursor(edit)

    def set_due(self, utc):
        def edit(cursor):
            cursor["next_due_utc"] = utc
            return cursor
        self._update_cursor(edit)

    def set_spend_cap(self, amount_text):
        spend = read_json(os.path.join(self.dir, "spend.json"))
        spend["cap"] = amount_text
        write_json(os.path.join(self.dir, "spend.json"), spend)

    def skip_line(self, ledger_index):
        """Move past one poison ledger line without creating a lesson."""
        self.assert_rails()
        cursor = read_json(os.path.join(self.dir, "cursor.json"))
        if ledger_index != cursor["ledger_index"]:
            raise BrainError("skip", "only the current poison line can be skipped")
        cursor["ledger_index"] = ledger_index + 1
        cursor["last_error_code"] = "skipped_malformed_ledger_line"
        cursor["pending_event"] = True
        write_json(os.path.join(self.dir, "cursor.json"), cursor)
        append_jsonl(os.path.join(self.dir, "runs.jsonl"), {
            "error_code": "skipped_malformed_ledger_line",
            "new_lessons": 0,
            "run_id": new_id("run_"),
            "status": "skipped",
            "trigger": "manual",
            "utc": self.clock(),
        })

    def run(self, *, trigger, idempotency_key, tenant_id, ledger, teachings, crash_at=None):
        with process_lock(self.lock_path):
            with file_lock(self.lock_path):
                return self._run(
                    trigger=trigger,
                    idempotency_key=idempotency_key,
                    tenant_id=tenant_id,
                    ledger=ledger,
                    teachings=teachings,
                    crash_at=crash_at,
                )

    def status(self, *, worker_enabled, provider_status):
        self.assert_rails()
        cursor = read_json(os.path.join(self.dir, "cursor.json"))
        spend = read_json(os.path.join(self.dir, "spend.json"))
        lessons = iter_jsonl(os.path.join(self.dir, "lessons.jsonl"))
        evidence = {row["evidence_id"] for row in iter_jsonl(os.path.join(self.dir, "evidence.jsonl"))}
        current = {}
        for row in lessons:
            current[row["lesson_id"]] = row
        with_evidence = 0
        candidates = 0
        validated = 0
        for row in current.values():
            ids = row.get("evidence_ids") or []
            if ids and all(item in evidence for item in ids):
                with_evidence += 1
            if row["status"] == "candidate":
                candidates += 1
            elif row["status"] == "validated":
                validated += 1
        updated = with_evidence > 0
        if cursor.get("last_error_code") and not updated:
            summary = "The last brain run recorded an error and did not claim an update."
        elif updated:
            summary = "Persisted lessons have evidence from this tenant. That is not a model threshold and not a live strategy."
        else:
            summary = "No persisted lesson with evidence yet. The starter pack is not the brain."
        return {
            "brain_updated": updated,
            "candidate_count": candidates,
            "component": "tenant_learning_brain",
            "durable": True,
            "last_attempt_utc": cursor.get("last_attempt_utc"),
            "last_error_code": cursor.get("last_error_code"),
            "last_success_utc": cursor.get("last_success_utc"),
            "last_trigger": cursor.get("last_trigger"),
            "lessons_with_evidence": with_evidence,
            "model_calls": spend["model_calls"],
            "model_running": False,
            "next_due_utc": cursor.get("next_due_utc"),
            "outbound_ai": "not_wired",
            "pending_event": bool(cursor.get("pending_event")),
            "persisted_lesson_count": len(current),
            "provider_state": provider_status,
            "spend_cap": spend["cap"],
            "spent": spend["spent"],
            "statement": summary,
            "validated_count": validated,
            "worker_enabled": bool(worker_enabled),
        }

    def lesson_rows(self):
        return iter_jsonl(os.path.join(self.dir, "lessons.jsonl"))

    def tree_hash(self):
        return hash_tree(self.dir)

    def _run(self, *, trigger, idempotency_key, tenant_id, ledger, teachings, crash_at):
        self._key(idempotency_key)
        self.assert_rails()
        request_hash = sha256_bytes(f"{tenant_id}|{trigger}|{idempotency_key}".encode("utf-8"))
        prior = self._idem_get(idempotency_key)
        if prior and prior.get("status") in {"updated", "idle"}:
            if prior["request_hash"] != request_hash:
                raise IdempotencyConflict()
            return prior
        model = self._consider_model()
        cursor = read_json(os.path.join(self.dir, "cursor.json"))
        staged_lessons = []
        staged_evidence = []
        seen = set(read_json(os.path.join(self.dir, "seen.json"))["keys"])
        deduped = 0
        error_code = None
        indexes = {"ledger_index": cursor["ledger_index"], "teaching_index": cursor["teaching_index"]}
        ctx = {
            "ledger": ledger,
            "ledger_index": cursor["ledger_index"],
            "teaching_index": cursor["teaching_index"],
            "teachings": teachings,
            "tenant_id": tenant_id,
        }
        try:
            for source in self.sources:
                batch = source.collect(ctx)
                if batch.get("error_code"):
                    error_code = batch["error_code"]
                for item in batch.get("items") or []:
                    if item["dedup_key"] in seen:
                        deduped += 1
                        continue
                    seen.add(item["dedup_key"])
                    staged_evidence.append(item["evidence"])
                    staged_lessons.append(item["lesson"])
                if "ledger_index" in batch:
                    indexes["ledger_index"] = batch["ledger_index"]
                if "teaching_index" in batch:
                    indexes["teaching_index"] = batch["teaching_index"]
                if error_code:
                    break
        except BrainError as exc:
            error_code = exc.code
        if crash_at == "before_commit":
            raise BrainError("crash", "injected crash before the brain commit")
        for evidence in staged_evidence:
            append_jsonl(os.path.join(self.dir, "evidence.jsonl"), evidence)
        for lesson in staged_lessons:
            append_jsonl(os.path.join(self.dir, "lessons.jsonl"), lesson)
        now = self.clock()
        cursor["ledger_index"] = indexes["ledger_index"]
        cursor["teaching_index"] = indexes["teaching_index"]
        cursor["last_attempt_utc"] = now
        cursor["last_trigger"] = trigger
        cursor["pending_event"] = False
        if error_code and not staged_lessons:
            cursor["last_error_code"] = error_code
            status = "error"
        else:
            cursor["last_error_code"] = error_code
            cursor["last_success_utc"] = now
            cursor["next_due_utc"] = _plus(now, int(cursor["interval_seconds"]))
            status = "updated" if staged_lessons else "idle"
        run = {
            "deduped": deduped,
            "error_code": error_code,
            "idempotency_key": idempotency_key,
            "model": model,
            "new_lessons": len(staged_lessons),
            "request_hash": request_hash,
            "run_id": new_id("run_"),
            "status": status,
            "trigger": trigger,
            "utc": now,
        }
        cursor["last_run_id"] = run["run_id"]
        write_json(os.path.join(self.dir, "cursor.json"), cursor)
        write_json(os.path.join(self.dir, "seen.json"), {"keys": sorted(seen)})
        append_jsonl(os.path.join(self.dir, "runs.jsonl"), run)
        self._idem_put(idempotency_key, run)
        self.log("brain run " + status + " lessons " + str(len(staged_lessons)))
        return run

    def record_provider_lesson(self, *, tenant_id, fill_id, skill_id, version, decision, feed):
        """A private record of one paper decision. This does not call a model or place an order."""
        self.assert_rails()
        evidence_id = "ev_" + sha256_bytes((tenant_id + fill_id).encode("utf-8"))[:20]
        lesson_id = "ls_" + sha256_bytes((tenant_id + "provider" + fill_id).encode("utf-8"))[:20]
        append_jsonl(os.path.join(self.dir, "evidence.jsonl"), {
            "evidence_id": evidence_id,
            "kind": "provider_decision",
            "ledger_id": fill_id,
            "tenant_id": tenant_id,
        })
        append_jsonl(os.path.join(self.dir, "lessons.jsonl"), {
            "evidence_ids": [evidence_id],
            "kind": "provider_decision",
            "lesson_id": lesson_id,
            "provenance": {
                "decision": decision,
                "feed": feed,
                "skill_id": skill_id,
                "source_kind": "provider_schema",
                "strategy_version": str(version),
            },
            "quarantine": False,
            "statement": (
                "Paper decision {decision} for {skill} version {version} on {feed}. "
                "Not a live fill and not a model approval."
            ).format(decision=decision, skill=skill_id, version=version, feed=feed),
            "status": "candidate",
            "trading_effect": "none",
            "utc": self.clock(),
        })
        return {"lesson_id": lesson_id, "trading_effect": "none"}

    def accept_lesson(self, lesson_id):
        """Record a validated paper hypothesis. This does not place an order or edit rails."""
        self.assert_rails()
        rows = self.lesson_rows()
        current = None
        for row in rows:
            if row["lesson_id"] == lesson_id:
                current = row
        if current is None:
            raise BrainError("lesson", "lesson was not found")
        if current["status"] == "validated":
            return current
        evidence = {row["evidence_id"] for row in iter_jsonl(os.path.join(self.dir, "evidence.jsonl"))}
        if not current.get("evidence_ids") or any(item not in evidence for item in current["evidence_ids"]):
            raise BrainError("evidence", "a lesson without evidence cannot be validated")
        if current.get("quarantine"):
            raise BrainError("quarantine", "quarantined input cannot be validated")
        accepted = dict(current)
        accepted["status"] = "validated"
        accepted["trading_effect"] = "none"
        accepted["activation"] = "not_activated"
        accepted["accepted_utc"] = self.clock()
        accepted["statement"] = current["statement"] + " Validated as a paper hypothesis only."
        append_jsonl(os.path.join(self.dir, "lessons.jsonl"), accepted)
        return accepted

    def _consider_model(self):
        spend = read_json(os.path.join(self.dir, "spend.json"))
        provider = self.secrets()
        reasons = []
        if provider != "secret_ref":
            reasons.append("provider_unconfigured")
        if str(spend.get("cap")) in {"0", "0.00", "0.0"}:
            reasons.append("spend_cap_zero")
        reasons.append("outbound_not_wired")
        spend["blocked_model_requests"] = int(spend.get("blocked_model_requests") or 0) + 1
        spend["model_calls"] = 0
        spend["spent"] = "0.00"
        write_json(os.path.join(self.dir, "spend.json"), spend)
        decision = {"called": False, "model_running": False, "reasons": reasons}
        self.log("model slot refused " + ",".join(reasons))
        return decision

    def _update_cursor(self, edit):
        path = os.path.join(self.dir, "cursor.json")
        cursor = read_json(path)
        write_json(path, edit(cursor))

    def _idem_get(self, key):
        return read_json(os.path.join(self.dir, "idempotency.json"), {}).get(key)

    def _idem_put(self, key, run):
        path = os.path.join(self.dir, "idempotency.json")
        body = read_json(path, {})
        body[key] = {
            "new_lessons": run["new_lessons"],
            "request_hash": run["request_hash"],
            "run_id": run["run_id"],
            "status": run["status"],
        }
        write_json(path, body)

    def _key(self, key):
        text = str(key or "")
        if len(text) < 8 or len(text) > 80 or any(ch.isspace() for ch in text):
            raise BrainError("idempotency", "idempotency key is not usable")


class PaperOutcomeSource:
    name = "paper_outcome"

    def collect(self, ctx):
        ledger = ctx["ledger"]
        index = ctx["ledger_index"]
        items = []
        cursor = index
        while cursor < len(ledger):
            line = ledger[cursor]
            if not isinstance(line, dict) or "kind" not in line or "id" not in line:
                return {"error_code": "malformed_ledger_line", "items": items, "ledger_index": cursor}
            if line["kind"] == "fill" and line.get("side") == "sell":
                if "realized_pnl" not in line or "symbol" not in line:
                    return {"error_code": "malformed_ledger_line", "items": items, "ledger_index": cursor}
                evidence_id = "ev_" + sha256_bytes((ctx["tenant_id"] + line["id"]).encode("utf-8"))[:20]
                lesson_id = "ls_" + sha256_bytes((ctx["tenant_id"] + "paper" + line["id"]).encode("utf-8"))[:20]
                items.append({
                    "dedup_key": "paper:" + line["id"],
                    "evidence": {
                        "evidence_id": evidence_id,
                        "kind": "paper_fill",
                        "ledger_id": line["id"],
                        "tenant_id": ctx["tenant_id"],
                    },
                    "lesson": {
                        "evidence_ids": [evidence_id],
                        "kind": "paper_outcome",
                        "lesson_id": lesson_id,
                        "provenance": {"source_ids": [line["id"]], "source_kind": "paper_ledger"},
                        "quarantine": False,
                        "statement": (
                            f"Paper round-trip evidence {line['id']} in {line['symbol']} realized "
                            f"{line['realized_pnl']} {line['currency']} in epoch {line['epoch_id']}. "
                            "This is a recorded outcome, not a prediction."
                        ),
                        "status": "candidate",
                        "trading_effect": "none",
                    },
                })
            cursor += 1
        return {"items": items, "ledger_index": cursor}


class ResearchSource:
    name = "research"

    def collect(self, ctx):
        rows = ctx["teachings"]
        index = ctx["teaching_index"]
        items = []
        cursor = index
        while cursor < len(rows):
            row = rows[cursor]
            if row.get("kind") == "submit":
                evidence_id = "ev_" + sha256_bytes((ctx["tenant_id"] + row["id"]).encode("utf-8"))[:20]
                lesson_id = "ls_" + sha256_bytes((ctx["tenant_id"] + "research" + row["id"]).encode("utf-8"))[:20]
                if row.get("quarantine"):
                    statement = "Quarantined research submission. Untrusted text was not copied into the brain statement."
                else:
                    statement = (
                        f"Research submission {row['idea_id']} version {row['version']} is stored as untrusted "
                        f"customer text ({row.get('chars', 0)} characters). It is not trading behavior."
                    )
                items.append({
                    "dedup_key": "research:" + row["id"],
                    "evidence": {
                        "evidence_id": evidence_id,
                        "history_id": row["id"],
                        "kind": "research",
                        "tenant_id": ctx["tenant_id"],
                    },
                    "lesson": {
                        "evidence_ids": [evidence_id],
                        "kind": "research_note",
                        "lesson_id": lesson_id,
                        "provenance": {"source_ids": [row["id"]], "source_kind": "customer_submission"},
                        "quarantine": bool(row.get("quarantine")),
                        "statement": statement,
                        "status": "candidate",
                        "trading_effect": "none",
                    },
                })
            cursor += 1
            if cursor > index and row.get("kind") == "submit":
                # research is ingested independently of later promote rows in this same pass
                pass
        return {"items": items, "teaching_index": index if False else cursor}


class ApprovedTeachingSource:
    name = "approved_teaching"

    def collect(self, ctx):
        # Teaching index is owned by ResearchSource when both run in one pass.
        # This source reads the same rows but does not move the shared index if
        # ResearchSource already reported the end. It still emits only promotions
        # at or after the cursor that was current at the start of the run.
        rows = ctx["teachings"]
        start = ctx["teaching_index"]
        items = []
        for row in rows[start:]:
            if row.get("kind") != "promote" or row.get("quarantine"):
                continue
            evidence_id = "ev_" + sha256_bytes((ctx["tenant_id"] + "promote" + row["id"]).encode("utf-8"))[:20]
            lesson_id = "ls_" + sha256_bytes((ctx["tenant_id"] + "approved" + row["id"]).encode("utf-8"))[:20]
            items.append({
                "dedup_key": "approved:" + row["id"],
                "evidence": {
                    "evidence_id": evidence_id,
                    "history_id": row["id"],
                    "kind": "approved_teaching",
                    "tenant_id": ctx["tenant_id"],
                },
                "lesson": {
                    "activation": "not_activated",
                    "evidence_ids": [evidence_id],
                    "kind": "approved_teaching",
                    "lesson_id": lesson_id,
                    "provenance": {"source_ids": [row["idea_id"]], "source_kind": "explicit_promotion"},
                    "quarantine": False,
                    "statement": (
                        f"Tenant approved paper hypothesis {row['idea_id']} version {row['version']}. "
                        "Activation is not enabled."
                    ),
                    "status": "validated",
                    "trading_effect": "none",
                },
            })
        return {"items": items}


def _plus(utc, seconds):
    parsed = datetime.strptime(utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=int(seconds))).strftime("%Y-%m-%dT%H:%M:%SZ")
